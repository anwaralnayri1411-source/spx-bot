#!/usr/bin/env python3
"""H-PEAD: استمرار ما بعد الأرباح (اختبار فقط، مسجّل مسبقاً في CLAUDE.md 30/10/2026). بطلب المستخدم بعد حالة INTC 65 بوت.

لكل شركة في bars_5m ولكل إعلان أرباح (ياهو، بوقته): يوم الحدث = اليوم نفسه إن كان الإعلان قبل 9:30، وإلا يوم التداول التالي.
الفجوة = افتتاح يوم الحدث ÷ إغلاق آخر يوم قبله − 1؛ حدث إذا 4% ≤ |الفجوة| ≤ 50%. الدخول سعر 10:00 (إغلاق شمعة 9:55) في جهة الفجوة.
النتيجة: من 10:00 حتى إغلاق يوم الحدث (h0) و+1 و+3 (الأساسية) و+5 أيام تداول.
الزيادة = الجهة × (العائد − متوسط عائد السهم نفسه من 10:00 للأفق نفسه في كل الأيام).
الحكم (+3): فاصل 95% للزيادة > 0 (معاينة بأيام الحدث)، وموجب في النصفين، ومتوسط حركة الجهة الخام > 1.0%.
تشغيل (الخادم): SPXBOT_DATA=/opt/spxbot/data python research/pead_test.py   المخرج: research/pead_test_results.md
فحص ذاتي بلا شبكة: python research/pead_test.py --selftest
"""
import os
import sys
from datetime import datetime, time as dtime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
OUT = os.path.join(HERE, "pead_test_results.md")
EARN_CACHE = os.path.join(DATA, "earnings_dates.csv")
ETFS = {"SPY", "QQQ", "DIA", "IWM"}
GAP_MIN, GAP_MAX = 0.04, 0.50
HORIZONS = (0, 1, 3, 5)
PRIMARY = 3
HURDLE = 0.010


def daily_table(d5):
    """من شموع 5د (ساعات السوق): لكل يوم الافتتاح وسعر 10:00 والإغلاق."""
    g = d5.groupby(d5.index.date)
    t = pd.DataFrame({"open": g["Open"].first(), "close": g["Close"].last()})
    p10 = d5[d5.index.time == dtime(9, 55)]["Close"]
    t["p10"] = pd.Series(p10.values, index=p10.index.date)
    t = t.dropna()
    t.index = pd.Index(t.index, name="day")
    return t


def baseline(t):
    """متوسط عائد السهم من 10:00 حتى إغلاق اليوم +h في كل الأيام."""
    c = t["close"].values
    p = t["p10"].values
    out = {}
    for h in HORIZONS:
        r = c[h:] / p[:len(p) - h] - 1 if h else c / p - 1
        out[h] = float(np.mean(r)) if len(r) else 0.0
    return out


def event_day(ts, days):
    """يوم الحدث في قائمة أيام التداول المرتبة (أو None)."""
    d = ts.date()
    i = np.searchsorted(days, d)
    if ts.time() >= dtime(9, 30):
        if i < len(days) and days[i] == d:
            i += 1
    return i if 0 < i < len(days) else None


def events_for(sym, t, earn):
    days = np.array(t.index)
    base = baseline(t)
    rows = []
    for ts, surprise in earn:
        i = event_day(ts, days)
        if i is None or i + max(HORIZONS) >= len(days):
            continue
        prev_close = t["close"].iloc[i - 1]
        op, p10 = t["open"].iloc[i], t["p10"].iloc[i]
        gap = op / prev_close - 1
        if not GAP_MIN <= abs(gap) <= GAP_MAX:
            continue
        side = 1 if gap > 0 else -1
        r = {"sym": sym, "day": days[i], "gap": gap, "side": side, "surprise": surprise,
             "confirmed": bool(side * (p10 - op) > 0)}
        for h in HORIZONS:
            ret = t["close"].iloc[i + h] / p10 - 1
            r[f"raw{h}"] = side * ret
            r[f"ex{h}"] = side * (ret - base[h])
        rows.append(r)
    return rows


def boot_ci(df, col, n=5000, seed=29):
    """فاصل 95% لمتوسط col بإعادة معاينة أيام الحدث (الأحداث في اليوم نفسه معاً)."""
    if len(df) < 2:
        return float("nan"), float("nan")
    g = df.groupby("day")[col].agg(["sum", "count"])
    s, c = g["sum"].values, g["count"].values
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(g), size=(n, len(g)))
    m = s[idx].sum(1) / c[idx].sum(1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def pct(x):
    return f"{x * 100:+.2f}%"


def row(name, df, h):
    if len(df) == 0:
        return f"| {name} | 0 | - | - | - | - |"
    lo, hi = boot_ci(df, f"ex{h}")
    win = (df[f"raw{h}"] > 0).mean()
    return f"| {name} | {len(df)} | {pct(df[f'raw{h}'].mean())} | {pct(df[f'ex{h}'].mean())} | [{pct(lo)}، {pct(hi)}] | {win:.0%} |"


def report(ev, meta, calib):
    L = ["# H-PEAD: استمرار ما بعد الأرباح (اختبار، Alpaca 5د منذ 2019)", "", f"- {meta}",
         f"- الحدث: |فجوة الأرباح| ≥ {GAP_MIN:.0%}؛ الدخول 10:00 في جهة الفجوة؛ «الخام» = حركة السهم في الجهة؛ «الزيادة» = على متوسط السهم نفسه.", ""]
    if len(ev) == 0:
        return L + ["لا أحداث."]
    mid = sorted(ev["day"])[len(ev) // 2]
    h1, h2 = ev[ev["day"] < mid], ev[ev["day"] >= mid]
    L += ["## كل الآفاق (كل الأحداث)", "", "| الأفق | العدد | الخام | الزيادة | فاصل 95% للزيادة | إصابة الجهة |", "|---|---|---|---|---|---|"]
    names = {0: "إغلاق يوم الحدث", 1: "+1 يوم", 3: "+3 أيام (الأساسية)", 5: "+5 أيام"}
    for h in HORIZONS:
        L.append(row(names[h], ev, h))
    h = PRIMARY
    L += ["", f"## وصفي عند +{h} أيام", "", "| المجموعة | العدد | الخام | الزيادة | فاصل 95% | إصابة |", "|---|---|---|---|---|---|"]
    groups = [(f"النصف الأول (قبل {mid})", h1), (f"النصف الثاني (من {mid})", h2),
              ("فجوة 4–8%", ev[ev["gap"].abs() < 0.08]), ("فجوة ≥ 8%", ev[ev["gap"].abs() >= 0.08]),
              ("فجوة صعود", ev[ev["side"] > 0]), ("فجوة هبوط", ev[ev["side"] < 0]),
              ("مؤكَّدة عند 10:00", ev[ev["confirmed"]]), ("غير مؤكَّدة", ev[~ev["confirmed"]])]
    s = ev.dropna(subset=["surprise"])
    if len(s):
        agree = np.sign(s["surprise"]) == s["side"]
        groups += [("المفاجأة مع الفجوة", s[agree]), ("المفاجأة ضد الفجوة", s[~agree])]
    for name, df in groups:
        L.append(row(name, df, h))
    lo, hi = boot_ci(ev, f"ex{h}")
    c1 = lo > 0
    c2 = h1[f"ex{h}"].mean() > 0 and h2[f"ex{h}"].mean() > 0
    c3 = ev[f"raw{h}"].mean() > HURDLE
    L += ["", "## الحكم (مسجّل مسبقاً)", "",
          f"1. فاصل 95% للزيادة عند +{h} فوق الصفر: {'✅' if c1 else '❌'} ([{pct(lo)}، {pct(hi)}])",
          f"2. موجبة في النصفين: {'✅' if c2 else '❌'} ({pct(h1[f'ex{h}'].mean())} / {pct(h2[f'ex{h}'].mean())})",
          f"3. الحركة الخام > {HURDLE:.1%}: {'✅' if c3 else '❌'} ({pct(ev[f'raw{h}'].mean())})",
          "", f"**النتيجة: {'تحققت — المرحلة التالية اختبارها بأسعار العقود الحقيقية' if (c1 and c2 and c3) else 'لم تتحقق'}**",
          "", f"- فحص المعايرة (جهات عشوائية، 200 مرة): متوسط الزيادة {pct(calib[0])}، ونسبة الفواصل فوق الصفر {calib[1]:.0%} (المتوقع نحو 2.5%).",
          "", "## أكبر 15 حدثاً (حسب الفجوة)", "", "| السهم | اليوم | الفجوة | خام +1 | خام +3 | خام +5 |", "|---|---|---|---|---|---|"]
    for _, r in ev.reindex(ev["gap"].abs().sort_values(ascending=False).index).head(15).iterrows():
        L.append(f"| {r['sym']} | {r['day']} | {pct(r['gap'])} | {pct(r['raw1'])} | {pct(r['raw3'])} | {pct(r['raw5'])} |")
    return L


def calibrate(ev, n=200, seed=3):
    """جهات عشوائية بدل جهة الفجوة: يجب أن تعطي زيادة قرب الصفر."""
    rng = np.random.default_rng(seed)
    h = PRIMARY
    raw_sign = ev[f"ex{h}"] * ev["side"]          # العائد بلا جهة (بعد إزالة المتوسط)
    means, pos = [], 0
    for k in range(n):
        x = ev[["day"]].copy()
        x["v"] = raw_sign * rng.choice([-1, 1], size=len(ev))
        means.append(x["v"].mean())
        lo, _ = boot_ci(x, "v", n=500, seed=k)
        pos += lo > 0
    return float(np.mean(means)), pos / n


def load_earnings(syms):
    if os.path.exists(EARN_CACHE):
        e = pd.read_csv(EARN_CACHE)
    else:
        import yfinance as yf
        parts = []
        for s in syms:
            try:
                d = yf.Ticker(s).get_earnings_dates(limit=40)
            except Exception as ex:  # noqa: BLE001
                print("لا مواعيد:", s, type(ex).__name__)
                continue
            if d is None or not len(d):
                continue
            d = d.reset_index()
            parts.append(pd.DataFrame({"sym": s, "ts": d.iloc[:, 0].astype(str), "surprise": d.get("Surprise(%)")}))
        e = pd.concat(parts) if parts else pd.DataFrame(columns=["sym", "ts", "surprise"])
        e.to_csv(EARN_CACHE, index=False)
    out = {}
    for s, g in e.groupby("sym"):
        ts = pd.to_datetime(g["ts"], utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
        out[s] = list(zip(ts, g["surprise"]))
    return out


def run(data):
    from tournament import read_bars
    import glob
    paths = {os.path.basename(p).split(".")[0]: p for p in glob.glob(os.path.join(data, "bars_5m", "*.csv.gz"))}
    syms = sorted(s for s in paths if s not in ETFS)
    earn = load_earnings(syms)
    rows, used = [], []
    for s in syms:
        if s not in earn:
            continue
        try:
            t = daily_table(read_bars(paths[s]))
        except Exception as ex:  # noqa: BLE001
            print("تخطي", s, type(ex).__name__)
            continue
        r = events_for(s, t, earn[s])
        rows += r
        used.append(f"{s} {len(r)}")
    ev = pd.DataFrame(rows)
    meta = f"{len(used)} شركة، {len(ev)} حدثاً ({'، '.join(used)})"
    return ev, meta


def selftest():
    """بيانات عشوائية: بلا ميزة يجب ألا يتحقق الحكم؛ مع استمرار مزروع يجب أن يتحقق."""
    rng = np.random.default_rng(5)
    days = pd.bdate_range("2019-01-01", "2026-10-01")
    res = {}
    for planted in (0.0, 0.02):
        rows = []
        for k in range(30):
            c = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, len(days))))
            t = pd.DataFrame({"open": c * np.exp(rng.normal(0, 0.005, len(days))), "p10": c, "close": c}, index=pd.Index(days.date, name="day"))
            earn = []
            for j in range(40, len(days) - 10, 63):
                g = rng.choice([-1, 1]) * rng.uniform(0.04, 0.12)
                t.iloc[j:, :] *= (1 + g)
                t.iloc[j, t.columns.get_loc("open")] = t["close"].iloc[j - 1] * (1 + g)
                t.iloc[j, t.columns.get_loc("p10")] = t["open"].iloc[j]
                if planted:
                    t.iloc[j + 2:, :] *= (1 + np.sign(g) * planted)
                earn.append((pd.Timestamp(days[j - 1]) + pd.Timedelta(hours=16), np.sign(g)))
            rows += events_for(f"S{k}", t, earn)
        ev = pd.DataFrame(rows)
        lo, _ = boot_ci(ev, f"ex{PRIMARY}")
        res[planted] = (len(ev), ev[f"ex{PRIMARY}"].mean(), lo)
        print(f"planted {planted}: n={len(ev)} mean={ev[f'ex{PRIMARY}'].mean():+.4f} lo={lo:+.4f}")
    assert res[0.0][2] <= 0 or res[0.0][1] < HURDLE, "إيجابية كاذبة على بيانات عشوائية"
    assert res[0.02][2] > 0, "لم يكتشف الاستمرار المزروع"
    print("selftest ok")


def main(argv):
    if "--selftest" in argv:
        selftest()
        return 0
    ev, meta = run(DATA)
    calib = calibrate(ev) if len(ev) else (0.0, 0.0)
    L = report(ev, meta, calib)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
