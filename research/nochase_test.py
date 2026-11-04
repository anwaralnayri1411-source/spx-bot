#!/usr/bin/env python3
"""H-NOCHASE: هل يمنع شرط «الامتداد» بوت SPX من مطاردة السعر ويحسّن النتيجة بالدولار؟ (مسجّل مسبقاً في CLAUDE.md 4/11/2026). اختبار فقط.

يعيد قرار البوت نفسه (`direction_points` وقيوده) كل 5 دقائق من 09:45 ببيانات متأخرة 6 دقائق، ويختار العقد بقواعده،
ويسعّره بشموع SPXW الحقيقية من Alpaca. الأذرع لكل إشارة:
  A الحالي: دخول عند الإشارة.
  B منع المطاردة (الرئيسي): EXT ≥ 0.5 ← انتظار شمعة تُغلق ضمن 0.15 EM من VWAP والبوت على الاتجاه نفسه، وإلا لا صفقة.
  C تأكيد 5د: إغلاق شمعة فوق أعلى أول 30د (CALL) أو تحت أدناها (PUT).
  D منظور أوسع: عائد آخر 60 دقيقة في اتجاه الإشارة.
تشغيل (الخادم، ALPACA): SPXBOT_DATA=/opt/spxbot/data python research/nochase_test.py
المخرج: research/nochase_results.md و SPXBOT_DATA/nochase_rows.csv
"""
import math
import os
import sys
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import options_bot as ob  # noqa: E402
import backtest_spx_v2 as bt  # noqa: E402

NY = ob.NY
DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
SPY_FILE = os.path.join(DATA, "spy_5m.csv.gz")
ROWS = os.path.join(DATA, "nochase_rows.csv")
OUT = os.path.join(HERE, "nochase_results.md")
DELAY = 6
FIRST_M, LAST_M = 9 * 60 + 45, 15 * 60
FINAL_M = 15 * 60 + 30
EXT_MAX, VWAP_BAND = 0.5, 0.15
HS, COMM = 0.015, 0.0065
N_BOOT = 3000


# ====================== دوال صافية ======================
def em_pct(vix_prev):
    return vix_prev / 100 / math.sqrt(252)


def ext_of(S, open_px, side, em):
    """امتداد السعر في اتجاه الإشارة بوحدات الحركة اليومية المتوقعة."""
    return (S / open_px - 1) * (1 if side == "CALL" else -1) / em


def near_vwap(close, vwap, side, em, band=VWAP_BAND):
    """الإغلاق قريب من VWAP (ضمن band من EM في جهة الإشارة) أو عبره."""
    d = (close / vwap - 1) * (1 if side == "CALL" else -1)
    return d <= band * em


def orb_break(close, orb, side):
    return orb is not None and ((side == "CALL" and close > orb[0]) or (side == "PUT" and close < orb[1]))


def ret60_ok(r60, side):
    return r60 is not None and (r60 > 0 if side == "CALL" else r60 < 0)


def follow_real(bars, t0, entry_m=None):
    """(قيمة الدخول، النتيجة النسبية، نوع الخروج) بقواعد البوت على شموع حقيقية، أو None."""
    x = bars[bars.index >= t0]
    if x.empty or (x.index[0] - t0).total_seconds() > 600:
        return None
    o = float(x["o"].iloc[0])
    if o <= 0:
        return None
    entry = o * (1 + HS) + COMM
    for t, r in x.iterrows():
        end = t + timedelta(minutes=5)
        v = float(r["c"]) * (1 - HS) - COMM
        ret = v / entry - 1
        age = (end - t0).total_seconds() / 60
        if ret >= ob.TAKE_PROFIT:
            return entry, ret, "tp"
        if age >= ob.MIN_AGE_MIN and ret <= -ob.STOP_LOSS:
            return entry, ret, "stop"
        if end.hour * 60 + end.minute >= FINAL_M:
            return entry, ret, "final"
    return entry, ret, "final"


def boot_day(df, col, n=N_BOOT, seed=0):
    x = df.dropna(subset=[col])
    if len(x) < 5:
        return np.nan, (np.nan, np.nan)
    days = x["day"].values
    u = np.unique(days)
    by = {d: x[col].values[days == d] for d in u}
    rng = np.random.default_rng(seed)
    ms = [np.mean(np.concatenate([by[d] for d in rng.choice(u, len(u))])) for _ in range(n)]
    return float(x[col].mean()), tuple(np.percentile(ms, [2.5, 97.5]))


def bh(pvals, q=0.05):
    """رفض BH: قائمة bool."""
    p = np.asarray(pvals, dtype=float)
    order = np.argsort(p)
    k = 0
    for i, idx in enumerate(order, 1):
        if p[idx] <= q * i / len(p):
            k = i
    rej = np.zeros(len(p), bool)
    rej[order[:k]] = True
    return list(rej)


def boot_p(df, col, n=N_BOOT, seed=1):
    """قيمة p ذات طرفين لمتوسط col = 0 بإعادة معاينة الأيام."""
    x = df.dropna(subset=[col])
    if len(x) < 5:
        return 1.0
    days = x["day"].values
    u = np.unique(days)
    by = {d: x[col].values[days == d] for d in u}
    rng = np.random.default_rng(seed)
    ms = np.array([np.mean(np.concatenate([by[d] for d in rng.choice(u, len(u))])) for _ in range(n)])
    return float(min(1.0, 2 * min((ms <= 0).mean(), (ms >= 0).mean())))


# ====================== البيانات ======================
def load():
    data = bt.load()
    try:
        f = pd.read_csv(SPY_FILE)
        f.index = pd.to_datetime(f["t"], utc=True).tz_convert(NY)
        f = f.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})[["Open", "High", "Low", "Close", "Volume"]]
        f = f[f.index >= data["spx"].index.min() - pd.Timedelta(days=3)]
        spy = pd.concat([f, data["spy"][["Open", "High", "Low", "Close", "Volume"]]]) if len(data["spy"]) else f
        data["spy"] = spy[~spy.index.duplicated(keep="last")].sort_index()
    except Exception as e:  # noqa: BLE001
        print("ملف SPY:", e)
    return data


def spy_state(data, d, cutoff):
    """(إغلاق SPY، VWAP) من الشموع المنتهية قبل cutoff، أو None."""
    spy = bt._upto(data["spy"], d, cutoff)
    if spy is None or len(spy) == 0 or spy["Volume"].sum() <= 0:
        return None
    m = spy.index.hour * 60 + spy.index.minute
    spy = spy[m >= bt.OPEN_M]
    if spy.empty:
        return None
    tp = (spy["High"] + spy["Low"] + spy["Close"]) / 3
    return float(spy["Close"].iloc[-1]), float((tp * spy["Volume"]).sum() / spy["Volume"].sum())


def real_bars(cache, d, side, K):
    from stocks_contract_replay import fetch_bars, occ
    sym = occ("SPXW", d, side, K)
    if sym not in cache:
        a = datetime.combine(d, datetime.min.time(), NY).replace(hour=9, minute=30).astimezone(bt.ob.NY).isoformat()
        b = datetime.combine(d, datetime.min.time(), NY).replace(hour=16)
        try:
            got = fetch_bars([sym], pd.Timestamp(a).tz_convert("UTC").isoformat(), pd.Timestamp(b).tz_convert("UTC").isoformat())
        except Exception as e:  # noqa: BLE001
            print("فشل", sym, getattr(e, "code", type(e).__name__))
            got = {}
        cache[sym] = got.get(sym)
    return cache[sym]


def run():
    data = load()
    spx = data["spx"]
    dates = sorted(set(spx.index.date))
    rows, cache = [], {}
    for di, d in enumerate(dates):
        g, mm = bt.day_bars(spx, d)
        if len(g) < 70 or di == 0:
            continue
        prev_d = dates[di - 1]
        vprev = data["vixd"][data["vixd"].index < pd.Timestamp(d)]
        if not len(vprev):
            continue
        em = em_pct(float(vprev.iloc[-1]))
        open_px = float(g["Open"].values[0])
        iv0 = float(vprev.iloc[-1]) / 100
        scans = {}
        for run_m in range(FIRST_M, LAST_M + 1, 5):
            cutoff = run_m - DELAY
            c = bt.build_c(data, d, prev_d, cutoff, run_m)
            st = spy_state(data, d, cutoff)
            if c is None or st is None or bt.events_block(d, run_m):
                scans[run_m] = None
                continue
            bull, bear, _ = ob.direction_points(c)
            side = ("CALL" if bull > bear else "PUT") if abs(bull - bear) >= ob.MIN_EDGE else None
            today = bt._upto(spx, d, cutoff)
            r60 = float(today["Close"].iloc[-1] / today["Close"].iloc[-13] - 1) if len(today) >= 13 else None
            scans[run_m] = {"side": side, "S": c["S"], "orb": c.get("orb"), "spy": st, "r60": r60}
        last_side, last_t, stops = None, None, 0
        for run_m, s in scans.items():
            if not s or not s["side"]:
                continue
            side = s["side"]
            if stops >= ob.MAX_DAILY_STOPS or (last_side == side and last_t is not None and run_m - last_t < ob.REPEAT_MINUTES):
                continue
            last_side, last_t = side, run_m
            ext = ext_of(s["S"], open_px, side, em)
            out = {"day": d.isoformat(), "time": f"{run_m // 60:02d}:{run_m % 60:02d}", "side": side, "ext": round(ext, 3)}

            def trade(t_m, S):
                pk = bt.pick_contract(side, S, t_m - DELAY, iv0, open_px, "base")
                if pk is None:
                    return None
                b = real_bars(cache, d, side, pk[0])
                if b is None or len(b) < 3:
                    return None
                r = follow_real(b, datetime.combine(d, datetime.min.time(), NY).replace(hour=t_m // 60, minute=t_m % 60))
                return None if r is None else (r[0], r[1], r[2], pk[0])

            a = trade(run_m, s["S"])
            if a is None:
                continue
            out.update(a_usd=a[0] * a[1] * 100, a_kind=a[2], a_K=a[3])
            stops += a[2] == "stop"

            def wait(cond):
                for t2, s2 in scans.items():
                    if t2 < run_m or not s2:
                        continue
                    if cond(t2, s2):
                        r = trade(t2, s2["S"])
                        return (r[0] * r[1] * 100, f"{t2 // 60:02d}:{t2 % 60:02d}") if r else (0.0, "")
                return 0.0, ""

            if ext < EXT_MAX:
                out.update(b_usd=out["a_usd"], b_time=out["time"])
            else:
                u, tm = wait(lambda t2, s2: t2 > run_m and s2["side"] == side and near_vwap(s2["spy"][0], s2["spy"][1], side, em))
                out.update(b_usd=u, b_time=tm)
            u, tm = wait(lambda t2, s2: orb_break(s2["S"], s2["orb"], side))
            out.update(c_usd=u, c_time=tm)
            u, tm = wait(lambda t2, s2: ret60_ok(s2["r60"], side))
            out.update(d_usd=u, d_time=tm)
            rows.append(out)
        print(d, len(rows), flush=True)
    df = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(ROWS), exist_ok=True)
    df.to_csv(ROWS, index=False)
    return df


# ====================== التقرير ======================
def verdict(df):
    if not len(df):
        return "غير مدعوم", {}
    x = df.assign(diff_b=df["b_usd"] - df["a_usd"])
    m, ci = boot_day(x, "diff_b")
    days = sorted(x["day"].unique())
    mid = days[len(days) // 2]
    halves = (x[x["day"] < mid]["diff_b"].mean(), x[x["day"] >= mid]["diff_b"].mean())
    ok = m > 0 and ci[0] > 0 and min(halves) > 0
    return ("مدعوم" if ok else "غير مدعوم"), {"diff": m, "ci": ci, "halves": halves}


def report(df):
    v, d = verdict(df)
    L = ["# H-NOCHASE: منع المطاردة في بوت SPX (نتائج آلية)", "",
         f"- {df['day'].nunique() if len(df) else 0} يوماً، {len(df)} إشارة للبوت بعقد له شموع حقيقية ({df['day'].min() if len(df) else ''} → {df['day'].max() if len(df) else ''}).",
         f"- إشارات بامتداد ≥ {EXT_MAX} من الحركة المتوقعة: {int((df['ext'] >= EXT_MAX).sum()) if len(df) else 0}.", "",
         "| الذراع | الصفقات | الصافي $ | متوسط $ للإشارة | فاصل 95% (أيام) |", "|---|---|---|---|---|"]
    names = {"a": "A الحالي (دخول عند الإشارة)", "b": "B منع المطاردة", "c": "C تأكيد 5د خارج نطاق أول 30د", "d": "D اتجاه آخر 60 دقيقة"}
    for k, nm in names.items():
        col = f"{k}_usd"
        if col not in df:
            continue
        traded = int((df[col] != 0).sum()) if k != "a" else len(df)
        m, ci = boot_day(df, col)
        L.append(f"| {nm} | {traded} | {df[col].sum():+,.0f}$ | {m:+.1f}$ | [{ci[0]:+.1f}، {ci[1]:+.1f}] |")
    L += ["", "## الفرق عن الحالي (بالدولار للإشارة)", "", "| الذراع | الفرق | فاصل 95% | p | النصفان |", "|---|---|---|---|---|"]
    ps, rowsec = [], []
    for k in ("b", "c", "d"):
        x = df.assign(dd=df[f"{k}_usd"] - df["a_usd"])
        m, ci = boot_day(x, "dd")
        p = boot_p(x, "dd")
        days = sorted(x["day"].unique())
        mid = days[len(days) // 2]
        h = (x[x["day"] < mid]["dd"].mean(), x[x["day"] >= mid]["dd"].mean())
        if k != "b":
            ps.append(p)
            rowsec.append(k)
        L.append(f"| {names[k]} | {m:+.1f}$ | [{ci[0]:+.1f}، {ci[1]:+.1f}] | {p:.3f} | {h[0]:+.1f} / {h[1]:+.1f} |")
    rej = bh(ps) if ps else []
    L += ["", f"⚖️ **الحكم الرئيسي (B): {v}**",
          "الثانويان بعد BH: " + "، ".join(f"{names[k]}: {'دال ✅' if r else 'غير دال'}" for k, r in zip(rowsec, rej)), "",
          "## وصفي: نتيجة الذراع الحالي حسب الامتداد", "", "| الامتداد | إشارات | متوسط $ | رابحة |", "|---|---|---|---|"]
    for lo, hi, nm in ((-9, 0.25, "< 0.25"), (0.25, 0.5, "0.25–0.5"), (0.5, 99, "≥ 0.5")):
        g = df[(df["ext"] >= lo) & (df["ext"] < hi)]
        if len(g):
            L.append(f"| {nm} | {len(g)} | {g['a_usd'].mean():+.1f}$ | {(g['a_usd'] > 0).mean() * 100:.0f}% |")
    L += ["", "تحفظ: قرار البوت المعاد بلا الجدران والأخبار وحجم الكول/البوت؛ أسعار آخر صفقة في الشمعة؛ عينة أيام قليلة."]
    return "\n".join(L) + "\n"


def main(argv):
    df = pd.read_csv(ROWS) if "--report" in argv else run()
    md = report(df)
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main(sys.argv[1:])
