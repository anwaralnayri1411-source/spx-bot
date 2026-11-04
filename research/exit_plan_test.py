#!/usr/bin/env python3
"""H-EXITPLAN: أي طريقة خروج أفضل حول إعلانات الأرباح؟ (مسجّل مسبقاً في CLAUDE.md 4/11/2026 بطلب المستخدم). اختبار فقط.

بأسعار عقود Alpaca الحقيقية (شموع 5د) على إعلانات H-EARNPRE (100 شركة، 2024-03 → 2026-10):
الدخول 10:30 قبل آخر جلسة قبل الإعلان بستة أيام تداول، CALL بدلتا ≈ 0.45 (100–600$).
  أ) الحالية: عقد ينتهي قبل الإعلان، +50% أو −40% أو إغلاق آخر جلسة قبل الإعلان.
  ب) مرحلتان: عقدان قبل الإعلان، الأول عند +50% ثم وقف الثاني عند التعادل وهدفه +100%.
  ج) مال السوق: عقدان عبر الإعلان، الأول عند +100% والثاني حتى إغلاق D0؛ بلا تضاعف: كلاهما يُغلق قبل الإعلان.
  د) عبر الإعلان كاملاً: حتى إغلاق D0، و−40% قبل الإعلان فقط.
تشغيل (الخادم، ALPACA): SPXBOT_DATA=/opt/spxbot/data python research/exit_plan_test.py [--limit N]
المخرج: research/exit_plan_results.md و SPXBOT_DATA/earnings_pre/exit_plan_rows.csv
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
from stocks_contract_replay import COMM, cap_end, fetch_bars, first_bar_after, occ, strike_for_delta  # noqa: E402
from stock_contract_test import half_spread, step_for  # noqa: E402

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
EVENTS = os.path.join(DATA, "earnings_pre", "events.csv")
ROWS = os.path.join(DATA, "earnings_pre", "exit_plan_rows.csv")
OUT = os.path.join(HERE, "exit_plan_results.md")
START, LAST = date(2024, 3, 1), date(2026, 10, 15)
ENTRY_T = dtime(10, 30)
HOLD_TD = 6
TARGET_D, COST_MIN, COST_MAX = 0.45, 100.0, 600.0
TP1, TP2, SL = 0.50, 1.00, -0.40
MIN_AGE_MIN = 30
CUTOFF = dtime(15, 55)
METHODS = {"a": "أ) الحالية: كل العقد عند +50% أو قبل الإعلان", "b": "ب) مرحلتان: نصف عند +50%، والباقي بوقف التعادل حتى قبل الإعلان",
           "c": "ج) مال السوق: نصف عند +100%، والباقي عبر الإعلان", "d": "د) عبر الإعلان كاملاً"}
N_BOOT = 3000


# ====================== المسار والطرق (دوال صافية) ======================
def path(bars, t0):
    """[(وقت نهاية الشمعة، قيمة العقد نسبةً للدخول)] من أول شمعة بعد t0، بنصف الفرق والعمولة. None بلا دخول."""
    ts = first_bar_after(bars, t0)
    if ts is None:
        return None
    o = float(bars.loc[ts, "o"])
    entry = o * (1 + half_spread(o)) + COMM
    out = []
    for t, r in bars[bars.index >= ts].iterrows():
        c = float(r["c"])
        out.append((t + timedelta(minutes=5), (c * (1 - half_spread(c)) - COMM) / entry - 1, (t - ts).total_seconds() / 60))
    return entry, out


def _until(p, cut):
    return [x for x in p if x[0] <= cut]


def method_a(p, cut):
    seg = _until(p, cut)
    for t, v, age in seg:
        if v >= TP1:
            return TP1
        if age >= MIN_AGE_MIN and v <= SL:
            return SL
    return seg[-1][1] if seg else None


def method_b(p, cut):
    seg = _until(p, cut)
    u1 = None
    for t, v, age in seg:
        if u1 is None:
            if v >= TP1:
                u1 = TP1
            elif age >= MIN_AGE_MIN and v <= SL:
                return SL
        else:
            if v >= TP2:
                return (u1 + TP2) / 2
            if v <= 0:
                return (u1 + 0.0) / 2
    if not seg:
        return None
    last = seg[-1][1]
    return (last + last) / 2 if u1 is None else (u1 + last) / 2


def method_c(p, cut1, cut2):
    seg1 = _until(p, cut1)
    for i, (t, v, age) in enumerate(seg1):
        if v >= TP2:
            seg2 = _until(p, cut2)
            return (TP2 + seg2[-1][1]) / 2
        if age >= MIN_AGE_MIN and v <= SL:
            return SL
    return seg1[-1][1] if seg1 else None


def method_d(p, cut1, cut2):
    for t, v, age in _until(p, cut1):
        if age >= MIN_AGE_MIN and v <= SL:
            return SL
    seg2 = _until(p, cut2)
    return seg2[-1][1] if seg2 else None


# ====================== التقويم والعقود ======================
def fridays(lo, hi):
    d, out = lo, []
    while d <= hi:
        if d.weekday() == 4:
            out.append(d)
        d += timedelta(days=1)
    return out


def plan_event(tdays, d0):
    """(يوم الدخول، آخر جلسة قبل الإعلان، انتهاء قبل الإعلان، انتهاء عبر الإعلان) أو None."""
    if d0 not in tdays:
        return None
    i = tdays.index(d0)
    if i < HOLD_TD + 1:
        return None
    ex, en = tdays[i - 1], tdays[i - 1 - HOLD_TD]
    pre = [f for f in fridays(en + timedelta(days=2), ex)]
    post = fridays(d0, d0 + timedelta(days=10))
    return en, ex, (pre[-1] if pre else None), (post[0] if post else None)


def candidates(S, T, iv):
    st = step_for(S)
    import options_bot as ob
    ks = set()
    for d in (0.35, 0.40, 0.45, 0.50, 0.55):
        k = strike_for_delta(S, T, iv, "CALL", d, ob.abs_delta)
        for x in (math.floor(k / st) * st, math.ceil(k / st) * st):
            if x > 0:
                ks.add(round(x, 3))
    return sorted(ks)


def pick(bars, sym, exp, ks, S, T, iv, t0):
    import options_bot as ob
    best = None
    for k in ks:
        b = bars.get(occ(sym, exp, "CALL", k))
        if b is None or len(b) < 5:
            continue
        ts = first_bar_after(b, t0)
        if ts is None:
            continue
        o = float(b.loc[ts, "o"])
        cost = (o * (1 + half_spread(o)) + COMM) * 100
        if not COST_MIN <= cost <= COST_MAX:
            continue
        d = ob.abs_delta(S, k, T, iv, "CALL")
        if best is None or abs(d - TARGET_D) < abs(best[1] - TARGET_D):
            best = (k, d, cost)
    return best


def run(limit=None):
    import yfinance as yf
    ev = pd.read_csv(EVENTS)
    ev = ev[(ev["d0"] >= START.isoformat()) & (ev["d0"] <= LAST.isoformat())]
    syms = sorted(ev["sym"].unique())[:limit] if limit else sorted(ev["sym"].unique())
    rows = []
    for sym in syms:
        try:
            px = yf.Ticker(sym).history(start="2023-12-01", auto_adjust=False)["Close"]
            px.index = px.index.tz_localize(None).date
        except Exception as e:  # noqa: BLE001
            print("تخطي", sym, type(e).__name__)
            continue
        tdays = list(px.index)
        for r in ev[ev["sym"] == sym].itertuples():
            d0 = date.fromisoformat(r.d0)
            pl = plan_event(tdays, d0)
            if not pl or not pl[2] or not pl[3]:
                continue
            en, ex, e_pre, e_post = pl
            k_en = tdays.index(en)
            S = float(px.iloc[k_en - 1])
            iv = float(np.log(px.iloc[max(0, k_en - 21):k_en]).diff().dropna().std() * math.sqrt(252) * 1.15)
            t0 = datetime.combine(en, ENTRY_T, tzinfo=NY)
            plan = {}
            syms_q = []
            for tag, exp in (("pre", e_pre), ("post", e_post)):
                T = max((exp - en).days, 1) / 365
                ks = candidates(S, T, iv)
                plan[tag] = (exp, T, ks)
                syms_q += [occ(sym, exp, "CALL", k) for k in ks]
            end = cap_end(max(e_post, d0))
            try:
                bars = fetch_bars(syms_q, t0.astimezone(UTC).isoformat(), end.isoformat())
            except Exception as e:  # noqa: BLE001
                print("فشل الجلب:", sym, d0, getattr(e, "code", type(e).__name__))
                continue
            cut1 = datetime.combine(ex, CUTOFF, tzinfo=NY)
            cut2 = datetime.combine(d0, CUTOFF, tzinfo=NY)
            out = {"sym": sym, "day": r.d0, "beat": r.beat, "r0": r.r0}
            for tag in ("pre", "post"):
                exp, T, ks = plan[tag]
                pk = pick(bars, sym, exp, ks, S, T, iv, t0)
                if not pk:
                    continue
                pr = path(bars[occ(sym, exp, "CALL", pk[0])], t0)
                if not pr:
                    continue
                _, p = pr
                if tag == "pre":
                    out.update(a=method_a(p, cut1), b=method_b(p, cut1), cost_pre=pk[2], delta_pre=round(pk[1], 3))
                else:
                    out.update(c=method_c(p, cut1, cut2), d=method_d(p, cut1, cut2), cost_post=pk[2])
            if any(k in out for k in ("a", "c")):
                rows.append(out)
        print(sym, len(rows), flush=True)
    df = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(ROWS), exist_ok=True)
    df.to_csv(ROWS, index=False)
    return df


# ====================== التقرير ======================
def boot_diff(df, m, base="a", n=N_BOOT, seed=0):
    x = df.dropna(subset=[m, base])
    if len(x) < 10:
        return np.nan, (np.nan, np.nan), len(x)
    days = x["day"].values
    u = np.unique(days)
    dif = (x[m] - x[base]).values
    by = {d: dif[days == d] for d in u}
    rng = np.random.default_rng(seed)
    ms = [np.mean(np.concatenate([by[d] for d in rng.choice(u, len(u))])) for _ in range(n)]
    return float(dif.mean()), tuple(np.percentile(ms, [2.5, 97.5])), len(x)


def report(df):
    L = ["# H-EXITPLAN: طرق الخروج حول إعلانات الأرباح بأسعار العقود الحقيقية (نتائج آلية)", "",
         f"- {df['sym'].nunique()} شركة، {len(df)} إعلاناً ({df['day'].min()} → {df['day'].max()}). CALL بدلتا ≈ 0.45، دخول قبل الإعلان بستة أيام تداول.",
         "- العائد على رأس المال كله (عقد أو عقدين)، بعد نصف الفرق والعمولة.", "",
         "| الطريقة | صفقات | متوسط العائد | رابحة | أسوأ 5% | لكل 300$ |", "|---|---|---|---|---|---|"]
    for m, name in METHODS.items():
        v = df[m].dropna() if m in df else pd.Series(dtype=float)
        if len(v):
            L.append(f"| {name} | {len(v)} | {v.mean() * 100:+.1f}% | {(v > 0).mean() * 100:.0f}% | {v.quantile(0.05) * 100:+.0f}% | {v.mean() * 300:+.0f}$ |")
    days = sorted(df["day"].unique())
    mid = days[len(days) // 2]
    L += ["", "## الفرق عن الطريقة الحالية (أ)", "", "| الطريقة | إعلانات مشتركة | الفرق | فاصل 95% | النصفان | الحكم |", "|---|---|---|---|---|---|"]
    best = ("a", 0.0)
    for m in ("b", "c", "d"):
        if m not in df:
            continue
        d, (lo, hi), n = boot_diff(df, m)
        h = [(x[m] - x["a"]).mean() for x in (df[df["day"] < mid].dropna(subset=[m, "a"]), df[df["day"] >= mid].dropna(subset=[m, "a"]))]
        ok = lo > 0 and min(h) > 0
        if ok and d > best[1]:
            best = (m, d)
        L.append(f"| {METHODS[m]} | {n} | {d * 100:+.1f} نقطة | [{lo * 100:+.1f}، {hi * 100:+.1f}] | {h[0] * 100:+.1f} / {h[1] * 100:+.1f} | "
                 f"{'أفضل ✅' if ok else 'لم تثبت'} |")
    L += ["", f"⚖️ **الأفضل: {METHODS[best[0]]}**" + ("" if best[0] != "a" else " (لا طريقة تفوقت على الحالية بثقة)"), "",
          "## وصفي: حسب نتيجة الأرباح", "", "| الطريقة | فوق التوقع | تحت التوقع |", "|---|---|---|"]
    for m, name in METHODS.items():
        if m in df:
            b_, w_ = df[df["beat"] > 0][m].dropna(), df[df["beat"] < 0][m].dropna()
            L.append(f"| {name} | {b_.mean() * 100:+.1f}% ({len(b_)}) | {w_.mean() * 100:+.1f}% ({len(w_)}) |")
    L += ["", "تحفظ: جمعات فقط؛ دلتا تقريبية من التذبذب المحقق؛ الأسعار من آخر صفقة في الشمعة؛ الهدف يُحتسب عند بلوغه بإغلاق الشمعة."]
    return "\n".join(L) + "\n"


def main(argv):
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    df = run(limit)
    md = report(df)
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main(sys.argv[1:])
