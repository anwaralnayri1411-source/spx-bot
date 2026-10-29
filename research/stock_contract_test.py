#!/usr/bin/env python3
"""H-STKCONTRACT: أي عقد هو الأفضل لبوت الشركات؟ اختبار بأسعار حقيقية (لا يغيّر أي بوت). بطلب المستخدم 30/10/2026.

الدافع: مدير الظلال يُظهر عقود الشركات −20% في المتوسط (فاصلها كله تحت الصفر)، واستعادة 31 توصية أعطت العقد الأقرب للسعر +19.9% مقابل +6.3%.
البروتوكول (مسجّل هنا وفي CLAUDE.md قبل التشغيل):
  العينة: شركات قائمة البوت، كل 15 يوم تداول من 2024-03-01 حتى قبل 60 يوماً من التشغيل، الدخول 10:30 نيويورك، **بالاتجاهين معاً** (كول وبوت)
    حتى يقيس الاختبار العقد نفسه لا الاتجاه (اتجاه البوت لم يتفوق على العشوائي).
  العقود (السعر للعقد ضمن 80–350$ كحدود البوت ما لم يُذكر غير ذلك):
    bot = تقليد عقد البوت الحالي: أقرب انتهاء جمعة بعد 3 أيام تداول على الأقل (≤ 14 يوماً)، دلتا ≈ 0.25.
    b1  = الانتهاء نفسه، أقرب دلتا إلى 0.45 (لا أقل من 0.30).      b2 = انتهاء جمعة بعد 14–21 يوماً، دلتا → 0.45 (≥ 0.30).
    b4  = انتهاء شهري (الجمعة الثالثة) بعد 28–50 يوماً، دلتا → 0.45 (≥ 0.30).
    وصفي بلا حد سعر: a2 = انتهاء 14–21 يوماً دلتا 0.50، i4 = شهري دلتا 0.70.
  التسعير: شموع صفقات Alpaca الحقيقية 5د؛ الدخول بافتتاح أول شمعة بعد 10:30 + نصف الفرق، والخروج بالإغلاق − نصف الفرق − عمولة؛
    نصف الفرق = أكبر من 1.5% و0.025$ ÷ السعر (فرق تسعيرة 5 سنتات على الأقل، فيُحمَّل العقد الرخيص كلفته الحقيقية).
    الدلتا بتذبذب تقديري = تذبذب السهم المحقق 20 يوماً × 1.15.
  الخروج: r1 = قاعدة البوت (+50% / −40% بعد 30 دقيقة)؛ r2 = +50% ووقف على السهم 1.5% ضد الاتجاه بعد 30 دقيقة.
    وكلاهما بسقف 10 أيام تداول أو قبل الانتهاء بـ7 أيام للعقود الأطول (يُغلق بالإغلاق).
  **الفرضية الأساسية:** متوسط نتيجة الصفقة (r1) لـ b2 أعلى من bot، بفرق مزدوج (الدخول نفسه) فاصله 95% فوق الصفر بإعادة معاينة أيام الدخول.
  ثانوي (وصفي): كل العقود مقابل bot، وr2 مقابل r1 (الأولوية 2: قاعدة الخروج)، ومتوسط كلفة العقد ونسبة ما يقع ضمن حدود السعر.
تشغيل (الخادم، ALPACA): SPXBOT_DATA=/opt/spxbot/data python research/stock_contract_test.py   المخرج: research/stock_contract_test_results.md و..._trades.csv
"""
import math
import os
import sys
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from stocks_contract_replay import occ, strike_for_delta, third_friday, first_bar_after, fetch_bars, cap_end, COMM, TP, STOP, MIN_AGE_MIN, STOCK_STOP  # noqa: E402

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
OUT_MD = os.path.join(HERE, "stock_contract_test_results.md")
OUT_CSV = os.path.join(HERE, "stock_contract_test_trades.csv")
START, EVERY, LAG_DAYS = date(2024, 3, 1), 15, 60
ENTRY = dtime(10, 30)
PMIN, PMAX = 80.0, 350.0
MAX_HOLD_TD, TIME_STOP_DAYS = 10, 7
VARIANTS = {"bot": ("E1", 0.25, True), "b1": ("E1", 0.45, True), "b2": ("E2", 0.45, True), "b4": ("E4", 0.45, True),
            "a2": ("E2", 0.50, False), "i4": ("E4", 0.70, False)}
NAMES = {"bot": "تقليد عقد البوت (≤14 يوماً، دلتا 0.25)", "b1": "≤14 يوماً، دلتا → 0.45", "b2": "14–21 يوماً، دلتا → 0.45",
         "b4": "شهري 28–50 يوماً، دلتا → 0.45", "a2": "14–21 يوماً، دلتا 0.50 (بلا حد سعر)", "i4": "شهري، دلتا 0.70 (بلا حد سعر)"}
DELTA_GRID = (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.70)


# ============================ دوال نقية ============================
def half_spread(price):
    return max(0.015, 0.025 / price) if price > 0 else 1.0


def fridays_between(day, lo, hi):
    out, d = [], day + timedelta(days=lo)
    while (d - day).days <= hi:
        if d.weekday() == 4:
            out.append(d)
        d += timedelta(days=1)
    return out


def expiries(day, trading_days):
    """{E1, E2, E4}: E1 = أول جمعة بعد 3 أيام تداول على الأقل وخلال 14 يوماً؛ E2 = جمعة بعد 14–21 يوماً؛ E4 = شهري 28–50."""
    out = {}
    td = [d for d in trading_days if d > day]
    min_d = td[2] if len(td) >= 3 else day + timedelta(days=4)
    e1 = [f for f in fridays_between(day, 1, 14) if f >= min_d]
    if e1:
        out["E1"] = e1[0]
    e2 = fridays_between(day, 14, 21)
    if e2:
        out["E2"] = e2[0]
    monthly = [third_friday(day.year + (day.month - 1 + k) // 12, (day.month - 1 + k) % 12 + 1) for k in range(4)]
    e4 = [m for m in monthly if 28 <= (m - day).days <= 50]
    if e4:
        out["E4"] = e4[0]
    return out


def step_for(S):
    return 0.5 if S < 25 else 1.0 if S < 100 else 2.5 if S < 250 else 5.0


def strikes(S, T, iv, side, abs_delta):
    """سترايكات الشبكة المحيطة بدلتا 0.20–0.70 (خطوة حسب سعر السهم)."""
    st = step_for(S)
    ks = set()
    for d in DELTA_GRID:
        k = strike_for_delta(S, T, iv, side, d, abs_delta)
        for x in (math.floor(k / st) * st, math.ceil(k / st) * st):
            if x > 0:
                ks.add(round(x, 3))
    return sorted(ks)


def choose(cands, target, budget):
    """cands: [(strike, delta, entry_price)]. budget: ضمن 80–350$ ودلتا ≥ 0.30 (إلا هدف 0.25 لتقليد البوت). الأقرب دلتا للهدف."""
    ok = []
    for k, d, p in cands:
        if budget and not (PMIN <= p * 100 <= PMAX):
            continue
        if budget and target >= 0.45 and d < 0.30:
            continue
        ok.append((abs(d - target), k, d, p))
    return min(ok)[1:] if ok else None


def simulate(bars, stock, ts, side, S0, exp, rule, trading_days):
    """من شمعة الدخول ts: (نتيجة، سبب). نصف الفرق حسب السعر، وسقف 10 أيام تداول أو الانتهاء − 7 أيام للعقود الأطول."""
    o = float(bars.loc[ts, "o"])
    entry = o * (1 + half_spread(o)) + COMM
    td = [d for d in trading_days if d >= ts.date()]
    cap_day = td[min(MAX_HOLD_TD, len(td) - 1)] if td else ts.date()
    if (exp - ts.date()).days > 21:
        cap_day = min(cap_day, exp - timedelta(days=TIME_STOP_DAYS))
    last = None
    for t, r in bars[bars.index >= ts].iterrows():
        end = t + timedelta(minutes=5)
        c = float(r["c"])
        val = (c * (1 - half_spread(c)) - COMM) / entry - 1
        last = (val, "end")
        age = (end - ts).total_seconds() / 60
        if val >= TP:
            return TP, "tp"
        if rule == "r1" and age >= MIN_AGE_MIN and val <= -STOP:
            return -STOP, "stop"
        if rule == "r2" and stock is not None and age >= MIN_AGE_MIN:
            px = stock.asof(end)
            if px == px and px > 0:
                adverse = -(px / S0 - 1) if side == "CALL" else (px / S0 - 1)
                if adverse >= STOCK_STOP:
                    return val, "stock_stop"
        if end.date() >= cap_day and end.hour * 60 + end.minute >= 15 * 60 + 55:
            return val, "cap"
        if end.date() >= exp and end.hour * 60 + end.minute >= 15 * 60 + 55:
            return val, "expiry"
    return last


# ============================ التشغيل ============================
def read_stock(sym):
    p = os.path.join(DATA, "bars_5m", f"{sym}.csv.gz")
    d = pd.read_csv(p)
    d.index = pd.DatetimeIndex(pd.to_datetime(d["t"], utc=True)).tz_convert(NY)
    d = d[(d.index.time >= dtime(9, 30)) & (d.index.time < dtime(16, 0))]
    return d["c"].astype(float)


def run(symbols, ob, fetch=None, read=None, today=None):
    fetch = fetch or fetch_bars
    read = read or read_stock
    today = today or date.today()
    rows = []
    for sym in symbols:
        try:
            px = read(sym)
        except Exception as e:  # noqa: BLE001
            print("تخطي", sym, type(e).__name__)
            continue
        daily = px.groupby(px.index.date).last()
        tdays = sorted(daily.index)
        days = [d for d in tdays if START <= d <= today - timedelta(days=LAG_DAYS)][::EVERY]
        for day in days:
            t0 = datetime.combine(day, ENTRY, tzinfo=NY)
            S = px[px.index <= t0]
            if S.empty or S.index[-1].date() != day:
                continue
            S0 = float(S.iloc[-1])
            hist = daily[daily.index < day].iloc[-21:]
            if len(hist) < 21:
                continue
            iv = float(np.log(hist).diff().dropna().std() * math.sqrt(252) * 1.15)
            exps = expiries(day, tdays)
            plan, syms = {}, []
            for side in ("CALL", "PUT"):
                for ek, exp in exps.items():
                    T = max((exp - day).days, 1) / 365
                    ks = strikes(S0, T, iv, side, ob.abs_delta)
                    plan[(side, ek)] = (exp, T, ks)
                    syms += [occ(sym, exp, side, k) for k in ks]
            if not plan:
                continue
            end = cap_end(max(e for e, _, _ in plan.values()))
            end = min(end, t0.astimezone(UTC) + timedelta(days=16))
            try:
                bars = fetch(syms, t0.astimezone(UTC).isoformat(), end.isoformat())
            except Exception as e:  # noqa: BLE001
                print("فشل الجلب:", sym, day, getattr(e, "code", type(e).__name__))
                continue
            for side in ("CALL", "PUT"):
                for v, (ek, target, budget) in VARIANTS.items():
                    if (side, ek) not in plan:
                        continue
                    exp, T, ks = plan[(side, ek)]
                    cands = []
                    for k in ks:
                        b = bars.get(occ(sym, exp, side, k))
                        if b is None or len(b) < 3:
                            continue
                        ts = first_bar_after(b, t0)
                        if ts is None:
                            continue
                        o = float(b.loc[ts, "o"])
                        cands.append((k, ob.abs_delta(S0, k, T, iv, side), o * (1 + half_spread(o)) + COMM))
                    pick = choose(cands, target, budget)
                    if not pick:
                        continue
                    k, d, price = pick
                    b = bars[occ(sym, exp, side, k)]
                    ts = first_bar_after(b, t0)
                    for rule in ("r1", "r2"):
                        res = simulate(b, px, ts, side, S0, exp, rule, tdays)
                        if res:
                            rows.append({"sym": sym, "day": day.isoformat(), "side": side, "variant": v, "rule": rule, "exp": exp.isoformat(),
                                         "strike": k, "delta": round(d, 3), "cost": round(price * 100, 1), "pnl": round(res[0], 4), "why": res[1]})
        print("انتهى", sym, len(rows))
    return pd.DataFrame(rows)


def boot_diff(d, days, n=3000, seed=41):
    """فاصل 95% لمتوسط فرق مزدوج بإعادة معاينة أيام الدخول."""
    by = pd.Series(d.values, index=days).groupby(level=0)
    S, N = by.sum().values, by.size().values
    if len(S) < 10:
        return None
    idx = np.random.default_rng(seed).integers(0, len(S), (n, len(S)))
    m = S[idx].sum(1) / N[idx].sum(1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def report(df, meta):
    L = ["# H-STKCONTRACT: أي عقد هو الأفضل لبوت الشركات؟ (أسعار Alpaca الحقيقية، نتائج آلية)", "", f"- {meta}",
         "- الدخول بالاتجاهين معاً (كول وبوت) فالنتيجة تقيس العقد لا الاتجاه. نصف الفرق ≥ 1.5% أو 2.5 سنت. سقف الاحتفاظ 10 أيام تداول.", ""]
    if df.empty:
        return L + ["لا نتائج."]
    L += ["| العقد | الخروج | صفقات | متوسط | وسيط | رابحة | متوسط الكلفة | دلتا وسيطة |", "|---|---|---|---|---|---|---|---|"]
    for v in VARIANTS:
        for r in ("r1", "r2"):
            g = df[(df.variant == v) & (df.rule == r)]
            if len(g):
                L.append(f"| {NAMES[v]} | {r} | {len(g)} | {100 * g.pnl.mean():+.1f}% | {100 * g.pnl.median():+.1f}% | {100 * (g.pnl > 0).mean():.0f}% | "
                         f"{g.cost.mean():,.0f}$ | {g.delta.median():.2f} |")
    piv = df.pivot_table(index=["sym", "day", "side"], columns=["variant", "rule"], values="pnl")
    L += ["", "## مقابل تقليد عقد البوت (فرق مزدوج على الدخول نفسه، r1)", "", "| العقد | دخولات مشتركة | الفرق | فاصل 95% | أفضل في |", "|---|---|---|---|---|"]
    verdict = None
    for v in VARIANTS:
        if v == "bot" or (v, "r1") not in piv.columns or ("bot", "r1") not in piv.columns:
            continue
        d = (piv[(v, "r1")] - piv[("bot", "r1")]).dropna()
        if d.empty:
            continue
        ci = boot_diff(d, [i[1] for i in d.index])
        L.append(f"| {NAMES[v]} | {len(d)} | {100 * d.mean():+.1f} نقطة | {'-' if not ci else f'{100 * ci[0]:+.1f}…{100 * ci[1]:+.1f}'} | {100 * (d > 0).mean():.0f}% |")
        if v == "b2":
            verdict = "لا حكم (عينة صغيرة)" if not ci else "تحققت" if ci[0] > 0 else "عكسية" if ci[1] < 0 else "لم تتحقق"
    L += ["", f"⚖️ H-STKCONTRACT (b2 أفضل من عقد البوت): {verdict or 'لا حكم'}", "", "## قاعدة الخروج: r2 (وقف على السهم) − r1 (وقف على العقد)، لكل عقد", ""]
    for v in VARIANTS:
        if (v, "r1") in piv.columns and (v, "r2") in piv.columns:
            d = (piv[(v, "r2")] - piv[(v, "r1")]).dropna()
            if len(d):
                ci = boot_diff(d, [i[1] for i in d.index])
                L.append(f"- {NAMES[v]}: {100 * d.mean():+.1f} نقطة" + (f" (فاصل {100 * ci[0]:+.1f}…{100 * ci[1]:+.1f})" if ci else ""))
    return L


def main():
    import options_bot as ob
    import stocks_bot as sb
    if not (os.getenv("ALPACA_KEY") and os.getenv("ALPACA_SECRET")):
        print("يحتاج ALPACA_KEY وALPACA_SECRET (يعمل على الخادم).")
        return 0
    syms = [s for s in sb.WATCHLIST if os.path.exists(os.path.join(DATA, "bars_5m", f"{s}.csv.gz"))]
    df = run(syms, ob)
    df.to_csv(OUT_CSV, index=False, lineterminator="\n")
    L = report(df, f"{df.sym.nunique() if len(df) else 0} شركة، {df.groupby(['sym', 'day']).ngroups if len(df) else 0} دخولاً، {START} → قبل {LAG_DAYS} يوماً من التشغيل")
    with open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
