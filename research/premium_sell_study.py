#!/usr/bin/env python3
"""بيع الأقساط بمخاطرة محددة على SPY بأسعار عقود حقيقية (قراءة فقط؛ H-SELL وH-SELLTOOL مسجّلتان في CLAUDE.md قبل كتابة الكود؛ بطلب المستخدم 26/10/2026).

الأساس من أرقامنا: الخيارات مسعَّرة أغلى من حركتها الفعلية (long_price_calibration)، فالمشتري العشوائي يخسر والبائع يكسب. نختبر: هل البيع نفسه رابح بعد الكلفة؟ وهل وضع البيع خلف مستويات السيولة (أدوات المستخدم) أفضل من مسافة ثابتة؟
الصفقة: كل يوم عند 10:30 نيويورك، سبريد ائتماني بعرض 3$ على عقود اليوم التالي — بوت (بيع قصير/شراء أبعد بـ3$) وكول مقابل؛ الاثنان معاً = كوندور.
  base: البيع على مسافة 0.8 × الحركة اليومية الضمنية (السعر × VIX أمس ÷ 100 ÷ √252).
  tool: البيع خلف أقرب مستوى سيولة (قمم/قيعان الساعة لآخر جلستين + أعلى/أدنى أمس) يبعد بين 0.4 و2 × الحركة الضمنية؛ بلا مستوى لا صفقة لتلك الجهة.
الأسعار: افتتاح أول شمعة صفقات خلال 10 دقائق من 10:30؛ البيع × 0.97 والشراء × 1.03 وعمولة 0.0065$ لكل رجل؛ يُهمل الائتمان < 0.05$.
الاحتفاظ حتى الانتهاء بالقيمة الجوهرية عند إغلاق SPY يوم الانتهاء. R = الربح ÷ أقصى خسارة؛ للكوندور أقصى الخسارة = 3 − مجموع الائتمانين.
تشغيل (الخادم؛ يحتاج مفاتيح Alpaca وملف SPY 5 دقائق): python research/premium_sell_study.py   المخرج: research/premium_sell_study_results.md
"""
import math
import os
import sys
import time as _time
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import levels_study as ls  # noqa: E402
import live_quotes as lq  # noqa: E402
import preopen_routine_study as pre  # noqa: E402
import replication_study as rp  # noqa: E402

OUT = os.path.join(HERE, "premium_sell_study_results.md")
START = "2024-03-01"
ENTRY_BAR = 11             # إغلاق شمعة 10:25 → الدخول 10:30
WIDTH = 3.0
EM_BASE = 0.8
TOOL_MIN, TOOL_MAX = 0.4, 2.0
HALF_SPREAD = 0.03
COMM = 0.0065
MIN_CREDIT = 0.05
MAX_GAP_MIN = 10
MIN_DAYS_SELL = 300
MIN_PAIRS = 150
DAYS_PER_CALL = 5
NEAR_EM = 0.3              # H-SELLPUT: بوت قصير أقرب إلى السعر
EXEC = {"سوقي (3%)": 0.03, "محدد (1.5%) — الأساسي": 0.015, "الوسط (0%) — حد أعلى": 0.0}
PRIMARY_EXEC = "محدد (1.5%) — الأساسي"


# ============================ دوال نقية ============================
def expected_move(S, vix):
    return S * vix / 100.0 / math.sqrt(252)


def base_strikes(S, em):
    return float(math.floor(S - EM_BASE * em)), float(math.ceil(S + EM_BASE * em))


def tool_strikes(S, em, levels):
    """(بوت قصير، كول قصير) خلف أقرب مستوى في النطاق [0.4، 2] × الحركة الضمنية، أو None لكل جهة."""
    below = [x for x in levels if S - TOOL_MAX * em <= x <= S - TOOL_MIN * em]
    above = [x for x in levels if S + TOOL_MIN * em <= x <= S + TOOL_MAX * em]
    put = float(math.floor(max(below))) if below else None
    call = float(math.ceil(min(above))) if above else None
    return put, call


def legs(side, short, exp):
    """(رمز القصير، رمز الطويل) لسبريد ائتماني بعرض 3$."""
    if side == "put":
        return lq.occ("SPY", exp, "PUT", short), lq.occ("SPY", exp, "PUT", short - WIDTH)
    return lq.occ("SPY", exp, "CALL", short), lq.occ("SPY", exp, "CALL", short + WIDTH)


def first_open(bars, t0, gap=MAX_GAP_MIN):
    if bars is None or len(bars) == 0:
        return None
    sub = bars[(bars.index >= t0) & (bars.index <= t0 + pd.Timedelta(minutes=gap))]
    return float(sub["o"].iloc[0]) if len(sub) else None


def credit_of(p_short, p_long, h=HALF_SPREAD):
    """الائتمان الصافي للسهم بعد نصف الفرق h والعمولة، أو None إن نقص سعر أو قلّ الائتمان."""
    if p_short is None or p_long is None:
        return None
    c = p_short * (1 - h) - p_long * (1 + h) - 2 * COMM
    return c if c >= MIN_CREDIT else None


def spread_value(side, short, X):
    """قيمة السبريد الجوهرية عند سعر التسوية X (بين 0 و3)."""
    if side == "put":
        v = max(0.0, short - X) - max(0.0, short - WIDTH - X)
    else:
        v = max(0.0, X - short) - max(0.0, X - short - WIDTH)
    return min(max(v, 0.0), WIDTH)


def side_r(credit, value):
    return (credit - value) / (WIDTH - credit)


def condor_r(cp, cc, vp, vc):
    total = cp + cc
    if total >= WIDTH:
        return None
    return (total - vp - vc) / (WIDTH - total)


def drawdown(rs):
    """أعمق هبوط تراكمي بوحدات R من قمة سابقة."""
    if len(rs) == 0:
        return 0.0
    eq = np.cumsum(rs)
    peak = np.maximum.accumulate(np.r_[0.0, eq])[1:]
    return float((eq - peak).min())


# ============================ الخطة والتقييم ============================
def build_plans(d, vix, holidays=()):
    """خطة لكل يوم: {day, t0, S, em, exp, close_exp, strikes: {(kind, side): short}}. d: شموع SPY 5 دقائق؛ vix: إغلاق أمس بفهرس اليوم."""
    days = []
    for day, g in d.groupby(d["t"].dt.date):
        g, half = ls.trim_half_day(g)
        days.append((day, g, half))
    plans, hist = [], []
    for k, (day, g, half) in enumerate(days):
        full = not half and len(g) >= 78
        if full and str(day) >= START and k + 1 < len(days) and len(hist) >= pre.HISTORY_DAYS:
            nday, ng, nhalf = days[k + 1]
            v = vix.get(pd.Timestamp(day))
            if v is not None and np.isfinite(v) and not nhalf:
                c = g["c"].to_numpy(float)
                S = float(c[ENTRY_BAR])
                em = expected_move(S, float(v))
                lv = pre.liquidity_levels(hist[-pre.HISTORY_DAYS:])
                bp, bc = base_strikes(S, em)
                tp, tc = tool_strikes(S, em, lv)
                strikes = {("base", "put"): bp, ("base", "call"): bc, ("near", "put"): float(math.floor(S - NEAR_EM * em))}
                if tp is not None:
                    strikes[("tool", "put")] = tp
                if tc is not None:
                    strikes[("tool", "call")] = tc
                plans.append({"day": pd.Timestamp(day), "t0": list(g["t"])[ENTRY_BAR] + pd.Timedelta(minutes=5), "S": S, "em": em,
                              "exp": str(nday), "close_exp": float(ng["c"].to_numpy(float)[-1]), "strikes": strikes})
        if full:
            hist.append(tuple(g[x].to_numpy(float) for x in ("o", "h", "l", "c")))
    return plans


def plan_symbols(p):
    out = []
    for (kind, side), short in p["strikes"].items():
        out += list(legs(side, short, p["exp"]))
    return list(dict.fromkeys(out))


def evaluate(plans, bars):
    """صفوف لكل (يوم، نوع، جهة): الائتمان والقيمة وR. bars: {رمز: DataFrame بفهرس نيويورك وعمود o}."""
    rows = []
    for p in plans:
        for (kind, side), short in p["strikes"].items():
            s_sym, l_sym = legs(side, short, p["exp"])
            ps_, pl_ = first_open(bars.get(s_sym), p["t0"]), first_open(bars.get(l_sym), p["t0"])
            cr = credit_of(ps_, pl_)
            val = spread_value(side, short, p["close_exp"])
            rows.append({"day": p["day"], "kind": kind, "side": side, "short": short, "p_short": ps_, "p_long": pl_, "settle": p["close_exp"],
                         "credit": cr, "value": val if cr is not None else None, "R": side_r(cr, val) if cr is not None else None})
    return pd.DataFrame(rows, columns=["day", "kind", "side", "short", "p_short", "p_long", "settle", "credit", "value", "R"])


def near_put(df, h):
    """H-SELLPUT بنموذج تنفيذ نصف فرقه h على الصفقات نفسها: DataFrame(day, val, credit)."""
    sub = df[(df["kind"] == "near") & (df["side"] == "put")]
    out = []
    for r in sub.itertuples():
        if pd.isna(r.p_short) or pd.isna(r.p_long):
            continue
        cr = credit_of(float(r.p_short), float(r.p_long), h)
        if cr is None or cr >= WIDTH:
            continue
        out.append({"day": r.day, "val": side_r(cr, spread_value("put", r.short, r.settle)), "credit": cr})
    return pd.DataFrame(out, columns=["day", "val", "credit"])


def sellput_verdict(primary):
    days = sorted(set(primary["day"]))
    s = rp.day_ci(primary, days) if len(primary) >= 30 else None
    if s is None or s[0] < MIN_DAYS_SELL:
        return f"H-SELLPUT: لا حكم بعد ({len(primary)} من {MIN_DAYS_SELL} يوماً)"
    lo, hi = s[2][95.0]
    return "H-SELLPUT: " + ("تحققت" if lo > 0 else "عكسية" if hi < 0 else "لم تتحقق") + f" (متوسط {s[1]:+.3f}R، فاصل {lo:+.3f}…{hi:+.3f})"


def condors(df, kind):
    """R الكوندور لكل يوم توفرت فيه الجهتان: DataFrame(day, R)."""
    sub = df[(df["kind"] == kind) & df["R"].notna()]
    out = []
    for day, g in sub.groupby("day"):
        if set(g["side"]) == {"put", "call"}:
            p, c = g[g["side"] == "put"].iloc[0], g[g["side"] == "call"].iloc[0]
            r = condor_r(p["credit"], c["credit"], p["value"], c["value"])
            if r is not None:
                out.append({"day": day, "val": r})
    return pd.DataFrame(out, columns=["day", "val"])


def paired(df):
    """فرق R (أداة − أساس) لنفس اليوم والجهة: DataFrame(day, val)."""
    ok = df[df["R"].notna()]
    piv = ok.pivot_table(index=["day", "side"], columns="kind", values="R", aggfunc="first")
    if "tool" not in piv or "base" not in piv:
        return pd.DataFrame(columns=["day", "val"])
    piv = piv.dropna(subset=["tool", "base"])
    return pd.DataFrame({"day": [d for d, _ in piv.index], "val": (piv["tool"] - piv["base"]).to_numpy()})


def risk_profile(rs):
    rs = np.asarray(rs, float)
    if len(rs) == 0:
        return None
    return {"win": float((rs > 0).mean()), "maxloss": float((rs <= -0.99).mean()), "worst": sorted(rs)[:5], "dd": drawdown(rs)}


def verdicts(cb, pr_):
    days = sorted(set(cb["day"]))
    s = rp.day_ci(cb, days) if len(cb) >= 30 else None
    if s is None or s[0] < MIN_DAYS_SELL:
        v1 = f"H-SELL: لا حكم بعد ({len(cb)} من {MIN_DAYS_SELL} يوماً)"
    else:
        lo, hi = s[2][95.0]
        v1 = "H-SELL: " + ("تحققت" if lo > 0 else "عكسية" if hi < 0 else "لم تتحقق") + f" (متوسط {s[1]:+.3f}R، فاصل {lo:+.3f}…{hi:+.3f})"
    pdays = sorted(set(pr_["day"]))
    t = rp.day_ci(pr_, pdays) if len(pr_) >= 30 else None
    if t is None or t[0] < MIN_PAIRS:
        v2 = f"H-SELLTOOL: لا حكم بعد ({len(pr_)} من {MIN_PAIRS} مشاهدة مزدوجة)"
    else:
        lo, hi = t[2][95.0]
        v2 = "H-SELLTOOL: " + ("تحققت" if lo > 0 else "عكسية" if hi < 0 else "لم تتحقق") + f" (الفرق {t[1]:+.3f}R، فاصل {lo:+.3f}…{hi:+.3f})"
    return v1, v2


def report(df, meta):
    L = ["# بيع الأقساط بمخاطرة محددة على SPY بأسعار حقيقية (نتائج آلية)", "", f"- {meta}",
         "- قراءة فقط. سبريد ائتماني بعرض 3$ لليوم التالي عند 10:30؛ احتفاظ حتى الانتهاء؛ نصف فرق 3% لكل رجل وعمولة. R = الربح ÷ أقصى خسارة.",
         "- **الفرضيتان المسجّلتان مسبقاً:** H-SELL (الكوندور الأساسي رابح بعد الكلفة) وH-SELLTOOL (البيع خلف مستويات السيولة أفضل من المسافة الثابتة).", ""]
    if df.empty:
        return L + ["لا بيانات."]
    df = df.copy()
    miss = df["R"].isna().mean()
    L += [f"- صفقات بلا سعر عند الدخول (مستبعدة): {100 * miss:.0f}%", "",
          "| الصنف | الجهة | صفقات | متوسط الائتمان | رابحة | متوسط R | فاصل 95% | أيام الخسارة القصوى |", "|---|---|---|---|---|---|---|---|"]
    for kind in ("base", "tool", "near"):
        for side in ("put", "call"):
            sub = df[(df["kind"] == kind) & (df["side"] == side) & df["R"].notna()]
            if len(sub) == 0:
                continue
            s = rp.day_ci(sub.rename(columns={"R": "val"})[["day", "val"]], sorted(set(sub["day"]))) if len(sub) >= 30 else None
            ci = "-" if s is None else f"{s[2][95.0][0]:+.3f}…{s[2][95.0][1]:+.3f}"
            L.append(f"| {kind} | {side} | {len(sub)} | {sub['credit'].mean():.2f}$ | {100 * (sub['R'] > 0).mean():.0f}% | {sub['R'].mean():+.3f}R | {ci} | {100 * (sub['R'] <= -0.99).mean():.1f}% |")
    cb = condors(df, "base")
    L += ["", "## الكوندور الأساسي (بوت + كول)", ""]
    rpf = risk_profile(cb["val"].to_numpy())
    if rpf:
        L += [f"- أيام: {len(cb)}، متوسط R {cb['val'].mean():+.3f}، رابحة {100 * rpf['win']:.0f}%، أيام الخسارة القصوى {100 * rpf['maxloss']:.1f}%",
              f"- أسوأ 5 أيام: {', '.join(f'{x:+.2f}R' for x in rpf['worst'])}؛ أعمق هبوط تراكمي {rpf['dd']:+.1f}R"]
    v1, v2 = verdicts(cb, paired(df))
    L += ["", "⚖️ **" + v1 + "**", "", "⚖️ **" + v2 + "**", "",
          "## H-SELLPUT: بوت قريب (0.3 × الحركة الضمنية) بثلاثة نماذج تنفيذ على الصفقات نفسها", "",
          "| التنفيذ | صفقات | متوسط الائتمان | رابحة | متوسط R | فاصل 95% | أيام الخسارة القصوى | أعمق هبوط تراكمي |", "|---|---|---|---|---|---|---|---|"]
    primary = None
    for name, h in EXEC.items():
        np_ = near_put(df, h)
        if name == PRIMARY_EXEC:
            primary = np_
        if len(np_) == 0:
            L.append(f"| {name} | 0 | - | - | - | - | - | - |")
            continue
        s = rp.day_ci(np_, sorted(set(np_["day"]))) if len(np_) >= 30 else None
        ci = "-" if s is None else f"{s[2][95.0][0]:+.3f}…{s[2][95.0][1]:+.3f}"
        rk = risk_profile(np_["val"].to_numpy())
        L.append(f"| {name} | {len(np_)} | {np_['credit'].mean():.2f}$ | {100 * rk['win']:.0f}% | {np_['val'].mean():+.3f}R | {ci} | {100 * rk['maxloss']:.1f}% | {rk['dd']:+.1f}R |")
    L += ["", "⚖️ **" + sellput_verdict(primary if primary is not None else pd.DataFrame(columns=["day", "val"])) + "**"]
    return L


# ============================ التشغيل ============================
def main(argv):
    if not os.path.exists(ls.DATA) or not os.getenv("ALPACA_KEY"):
        print("يلزم ملف SPY 5 دقائق ومفاتيح Alpaca (الخادم).")
        return 0
    import option_buyer_cost as obc
    import options_bot as ob
    import stocks_contract_replay as scr
    d = ls.load(ls.DATA)
    plans = build_plans(d, obc.load_vix(), ob.HOLIDAYS)
    bars = {}
    for k in range(0, len(plans), DAYS_PER_CALL):
        chunk = plans[k:k + DAYS_PER_CALL]
        syms = [s for p in chunk for s in plan_symbols(p)]
        start = (chunk[0]["day"].tz_localize("America/New_York") + pd.Timedelta(hours=9)).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
        end = (chunk[-1]["day"].tz_localize("America/New_York") + pd.Timedelta(hours=17)).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            bars.update(scr.fetch_bars(syms, start, end))
        except Exception as e:  # noqa: BLE001
            print("تعذر جلب دفعة:", type(e).__name__)
        _time.sleep(0.2)
    df = evaluate(plans, bars)
    meta = f"{len(plans)} يوماً من {START}"
    L = report(df, meta)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
