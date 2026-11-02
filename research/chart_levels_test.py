#!/usr/bin/env python3
"""H-CHART: كسر وارتداد مستويات فريم الساعة بدخول على 5 دقائق لأسهم بوت الشركات (مسجّل مسبقاً في CLAUDE.md 3/11/2026).
اختبار فقط، لا يغيّر أي بوت.

المستويات: قمم/قيعان الساعة (±2 شمعة) لآخر 10 أيام + أعلى/أدنى أمس، مدمجة ≤0.15%. اتجاه الساعة: EMA20 وميلها.
الإشارات 10:00–15:00 (أول إشارة من كل نوع لكل سهم يومياً):
  كسر: إغلاق 5د خلف المستوى + حجم ≥1.5× متوسط 20 شمعة + اتجاه الساعة موافق.
  ارتداد: لمس المستوى ثم إغلاق 5د بالعكس (شمعة بلون الجهة) + اتجاه الساعة ليس معاكساً.
النتيجة: من افتتاح الشمعة التالية؛ المُدارة = خروج عند أول إغلاق ساعة خلف المستوى بـ0.5% ضد الصفقة، وإلا إغلاق +5.
الضابطان: مستويات مزيفة (إزاحة 0.4–1.5%)، ونقاط البوت الحالية (tech_points) بدخول 10:30.
تشغيل (الخادم): SPXBOT_DATA=/opt/spxbot/data python research/chart_levels_test.py   المخرج: research/chart_levels_results.md
"""
import glob
import os
import sys
from datetime import date, time as dtime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
OUT = os.path.join(HERE, "chart_levels_results.md")
OUT_CSV = os.path.join(HERE, "chart_levels_trades.csv")
START, END = date(2024, 3, 1), date(2026, 10, 23)
ETFS = {"SPY", "QQQ", "DIA", "IWM"}
MERGE, TOUCH, BREAK_STOP, VOL_X = 0.0015, 0.001, 0.005, 1.5
FAKE_LO, FAKE_HI = 0.004, 0.015
HOLD = 5
BARRIER = 0.01
N_BOOT = 4000
KINDS = {"break": "كسر", "bounce": "ارتداد"}


def read_bars(path):
    from tournament import read_bars as rb
    return rb(path)


# ====================== التحضير ======================
def prep(d5):
    d5 = d5.copy()
    d5["day"] = d5.index.date
    d5["m"] = d5.index.hour * 60 + d5.index.minute - 570          # دقائق منذ 9:30
    d5["vavg"] = d5["Volume"].rolling(20, min_periods=10).mean().shift(1)
    g = d5.groupby("day")
    t = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(),
                      "Close": g["Close"].last(), "Volume": g["Volume"].sum()})
    slot = d5["m"] // 60
    hg = d5.groupby([d5["day"], slot])
    h = pd.DataFrame({"high": hg["High"].max(), "low": hg["Low"].min(), "close": hg["Close"].last()}).reset_index()
    h.columns = ["day", "slot", "high", "low", "close"]
    h["ema"] = h["close"].ewm(span=20, adjust=False).mean()
    h["trend"] = np.where((h["close"] > h["ema"]) & (h["ema"] > h["ema"].shift(3)), 1,
                          np.where((h["close"] < h["ema"]) & (h["ema"] < h["ema"].shift(3)), -1, 0))
    first_h = h.groupby("day").apply(lambda x: x.index[0])
    return d5, t, h, first_h


def levels_for(t, h, i):
    days = t.index
    w = h[(h["day"] >= days[max(0, i - 10)]) & (h["day"] < days[i])].reset_index(drop=True)
    lv = [float(t["High"].iloc[i - 1]), float(t["Low"].iloc[i - 1])]
    for k in range(2, len(w) - 2):
        if w.at[k, "high"] == w["high"].iloc[k - 2:k + 3].max():
            lv.append(float(w.at[k, "high"]))
        if w.at[k, "low"] == w["low"].iloc[k - 2:k + 3].min():
            lv.append(float(w.at[k, "low"]))
    lv.sort()
    out = []
    for x in lv:
        if out and x / out[-1] - 1 <= MERGE:
            out[-1] = (out[-1] + x) / 2
        else:
            out.append(x)
    return out


def fake_levels(lv, rng):
    return [x * (1 + rng.choice([-1, 1]) * rng.uniform(FAKE_LO, FAKE_HI)) for x in lv]


# ====================== الإشارات ======================
def signals_day(bars, lv, trend_at):
    """bars: شموع 5د لليوم (10:00–15:00). يرجع [(النوع، الجهة، المستوى، موضع الشمعة)] أول إشارة لكل (نوع، جهة)."""
    if not lv or len(bars) < 2:
        return []
    L = np.array(lv)
    o, hi, lo, c = (bars[k].values for k in ("Open", "High", "Low", "Close"))
    pc = np.r_[np.nan, c[:-1]]
    volx = (bars["Volume"] / bars["vavg"]).values
    out, seen = [], set()
    for j in range(1, len(bars)):
        tr = trend_at(bars["m"].iloc[j])
        cands = []
        up_b = (pc[j] <= L) & (c[j] > L)
        dn_b = (pc[j] >= L) & (c[j] < L)
        if volx[j] >= VOL_X and tr == 1 and up_b.any():
            cands.append(("break", "CALL", float(L[up_b].max())))
        if volx[j] >= VOL_X and tr == -1 and dn_b.any():
            cands.append(("break", "PUT", float(L[dn_b].min())))
        sup = (lo[j] <= L * (1 + TOUCH)) & (c[j] > L) & (pc[j] > L)
        res = (hi[j] >= L * (1 - TOUCH)) & (c[j] < L) & (pc[j] < L)
        if c[j] > o[j] and tr != -1 and sup.any():
            cands.append(("bounce", "CALL", float(L[sup].max())))
        if c[j] < o[j] and tr != 1 and res.any():
            cands.append(("bounce", "PUT", float(L[res].min())))
        for kind, side, lvl in cands:
            if (kind, side) not in seen:
                seen.add((kind, side))
                out.append((kind, side, lvl, j))
    return out


def outcome(t, h, i, entry, side, lvl, m_entry, first_h):
    """(r1، r3، r5، المُدارة) بإشارة الجهة."""
    s = 1 if side == "CALL" else -1
    if i + HOLD >= len(t):
        return None
    cl = t["Close"].values
    r = {f"r{k}": s * (cl[i + k] / entry - 1) for k in (1, 3, 5)}
    g0 = first_h[t.index[i]]
    start = g0 + (m_entry + 59) // 60                       # أول شمعة ساعة تبدأ بعد الدخول
    end = first_h[t.index[i + HOLD + 1]] if i + HOLD + 1 < len(t) else len(h)
    hw = h.iloc[start:end]
    managed = r["r5"]
    for x in hw.itertuples():
        if (s > 0 and x.close <= lvl * (1 - BREAK_STOP)) or (s < 0 and x.close >= lvl * (1 + BREAK_STOP)):
            managed = s * (x.close / entry - 1)
            break
    r["managed"] = managed
    return r


def run_symbol(sym, d5, rng, spy=None):
    d5, t, h, first_h = prep(d5)
    days = list(t.index)
    rows = []
    for i in range(11, len(days) - HOLD - 1):
        d = days[i]
        if d < START or d > END:
            continue
        g0 = first_h[d]
        prev_last = g0 - 1                       # آخر شمعة ساعة في اليوم السابق

        def trend_at(m, g0=g0, prev_last=prev_last):
            k = (m + 5) // 60
            return int(h["trend"].iloc[g0 + k - 1]) if k >= 1 else int(h["trend"].iloc[prev_last])

        day5 = d5[(d5["day"] == d)]
        bars = day5[(day5["m"] >= 30) & (day5["m"] <= 330)].reset_index(drop=True)
        real = levels_for(t, h, i)
        for lvset, src in ((real, "real"), (fake_levels(real, rng), "fake")):
            for kind, side, lvl, j in signals_day(bars, lvset, trend_at):
                if j + 1 >= len(bars):
                    continue
                entry, m_e = float(bars["Open"].iloc[j + 1]), int(bars["m"].iloc[j + 1])
                o = outcome(t, h, i, entry, side, lvl, m_e, first_h)
                if o:
                    rows.append({"sym": sym, "day": str(d), "src": src, "kind": kind, "side": side, "lvl": lvl,
                                 "time": f"{9 + (30 + m_e) // 60}:{(30 + m_e) % 60:02d}", **o})
        if spy is not None:                    # الضابط 2: نقاط البوت الحالية (الشمعة اليومية حتى أمس)
            b = bot_signal(sym, t, i, spy)
            if b:
                side = b
                e10 = day5[day5["m"] == 60]
                if len(e10):
                    o = outcome(t, h, i, float(e10["Open"].iloc[0]), side, 0.0, 60, first_h)
                    if o:
                        o["managed"] = o["r5"]
                        rows.append({"sym": sym, "day": str(d), "src": "bot", "kind": "bot", "side": side, "lvl": 0.0,
                                     "time": "10:30", **o})
    return rows


def bot_signal(sym, t, i, spy):
    import stocks_bot as sb
    df = t.iloc[max(0, i - 130):i]
    if len(df) < 55:
        return None
    sd = spy[spy.index < pd.Timestamp(t.index[i])]
    if len(sd) < 21:
        return None
    spy5 = float(sd.iloc[-1] / sd.iloc[-6] - 1)
    spy_up = float(sd.iloc[-1]) > float(sd.rolling(20).mean().iloc[-1])
    try:
        bull, bear, _, _ = sb.tech_points(df, spy5, None, float(df["Volume"].iloc[-1]))
    except Exception:  # noqa: BLE001
        return None
    bull, bear = (bull + 0.5, bear) if spy_up else (bull, bear + 0.5)
    e = bull - bear
    return ("CALL" if e > 0 else "PUT") if abs(e) >= sb.MIN_EDGE else None


# ====================== الإحصاء ======================
def boot(v, days, n=N_BOOT, seed=0):
    v, days = np.asarray(v, float), np.asarray(days)
    u = np.unique(days)
    by = {d: v[days == d] for d in u}
    rng = np.random.default_rng(seed)
    ms = np.array([np.mean(np.concatenate([by[d] for d in rng.choice(u, len(u))])) for _ in range(n)])
    return float(np.mean(v)), tuple(np.percentile(ms, [2.5, 97.5])), float((ms <= 0).mean())


def boot_diff(a, b, n=N_BOOT, seed=1):
    """فرق المتوسطين (a − b) بإعادة معاينة الأيام المشتركة."""
    u = np.unique(np.r_[a["day"].values, b["day"].values])
    ba, bb = {d: g["managed"].values for d, g in a.groupby("day")}, {d: g["managed"].values for d, g in b.groupby("day")}
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        s = rng.choice(u, len(u))
        xa = np.concatenate([ba.get(d, np.array([])) for d in s])
        xb = np.concatenate([bb.get(d, np.array([])) for d in s])
        if len(xa) and len(xb):
            out.append(xa.mean() - xb.mean())
    return float(a["managed"].mean() - b["managed"].mean()), tuple(np.percentile(out, [2.5, 97.5])), float((np.array(out) <= 0).mean())


def bh(ps, q=0.05):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    k = 0
    for r, i in enumerate(order, 1):
        if ps[i] <= q * r / len(ps):
            k = r
    ok = [False] * len(ps)
    for r, i in enumerate(order, 1):
        ok[i] = r <= k
    return ok


def report(df):
    L = ["# H-CHART: كسر وارتداد مستويات فريم الساعة لأسهم بوت الشركات (نتائج آلية)", "",
         f"- {df['sym'].nunique()} شركة، {START} → {END}، شموع 5د الحقيقية. النتيجة بإشارة الجهة (حركة السهم، لا العقد).",
         "- المُدارة = خروج عند إغلاق ساعة خلف المستوى بـ0.5% ضد الصفقة، وإلا إغلاق اليوم +5. حاجز كلفة العقد ~1%.", "",
         "| المجموعة | صفقات | +1 يوم | +3 أيام | +5 أيام | **المُدارة** | فاصل 95% (المُدارة) | رابحة |", "|---|---|---|---|---|---|---|---|"]
    res = {}
    for src, sname in (("real", "مستويات حقيقية"), ("fake", "مستويات مزيفة (ضابط)")):
        for kind in ("break", "bounce"):
            g = df[(df["src"] == src) & (df["kind"] == kind)]
            if not len(g):
                continue
            m, (lo, hi), p = boot(g["managed"], g["day"])
            res[(src, kind)] = (g, m, lo, hi, p)
            L.append(f"| {KINDS[kind]} — {sname} | {len(g)} | {g['r1'].mean() * 100:+.2f}% | {g['r3'].mean() * 100:+.2f}% | "
                     f"{g['r5'].mean() * 100:+.2f}% | **{m * 100:+.2f}%** | [{lo * 100:+.2f}، {hi * 100:+.2f}] | {(g['managed'] > 0).mean() * 100:.0f}% |")
    b = df[df["src"] == "bot"]
    if len(b):
        m, (lo, hi), _ = boot(b["managed"], b["day"])
        L.append(f"| نقاط البوت الحالية (ضابط 2، بلا وقف) | {len(b)} | {b['r1'].mean() * 100:+.2f}% | {b['r3'].mean() * 100:+.2f}% | "
                 f"{b['r5'].mean() * 100:+.2f}% | **{m * 100:+.2f}%** | [{lo * 100:+.2f}، {hi * 100:+.2f}] | {(b['managed'] > 0).mean() * 100:.0f}% |")
    L += ["", "## الفرضيات", ""]
    days = sorted(df["day"].unique())
    mid = days[len(days) // 2]
    ps, lines, extra = [], [], []
    for kind, hname in (("break", "C1 الكسر"), ("bounce", "C2 الارتداد")):
        if ("real", kind) not in res or ("fake", kind) not in res:
            ps.append(1.0)
            extra.append(False)
            lines.append(f"- **{hname}:** لا بيانات كافية.")
            continue
        g, m, lo, hi, p = res[("real", kind)]
        f = res[("fake", kind)][0]
        dm, (dlo, dhi), dp = boot_diff(g, f)
        h1, h2 = g[g["day"] < mid]["managed"].mean(), g[g["day"] >= mid]["managed"].mean()
        ps.append(max(p, dp))
        extra.append(lo > 0 and dlo > 0 and h1 > 0 and h2 > 0)
        lines.append(f"- **{hname}:** المتوسط {m * 100:+.2f}% [{lo * 100:+.2f}، {hi * 100:+.2f}] | الفرق عن المزيفة {dm * 100:+.2f} نقطة "
                     f"[{dlo * 100:+.2f}، {dhi * 100:+.2f}] | النصفان {h1 * 100:+.2f}% / {h2 * 100:+.2f}%")
    ok = bh(ps)
    L += lines + [""]
    for (kind, hname), o, e in zip((("break", "C1 الكسر"), ("bounce", "C2 الارتداد")), ok, extra):
        L.append(f"⚖️ **{hname}: {'تحققت' if (o and e) else 'لم تتحقق'}**")
    L += ["", "## وصفي: حسب الجهة والوقت (مستويات حقيقية، المُدارة)", "", "| المجموعة | صفقات | المُدارة | رابحة |", "|---|---|---|---|"]
    r = df[df["src"] == "real"]
    for kind in ("break", "bounce"):
        for side in ("CALL", "PUT"):
            g = r[(r["kind"] == kind) & (r["side"] == side)]
            if len(g):
                L.append(f"| {KINDS[kind]} {side} | {len(g)} | {g['managed'].mean() * 100:+.2f}% | {(g['managed'] > 0).mean() * 100:.0f}% |")
        for lab, cond in (("قبل 11:00", r["time"] < "11:00"), ("بعد 11:00", r["time"] >= "11:00")):
            g = r[(r["kind"] == kind) & cond]
            if len(g):
                L.append(f"| {KINDS[kind]} {lab} | {len(g)} | {g['managed'].mean() * 100:+.2f}% | {(g['managed'] > 0).mean() * 100:.0f}% |")
    L += ["", f"تحفظ: حركة السهم ليست ربح العقد (الحاجز ~{BARRIER * 100:.0f}%)؛ لا أخبار ولا غاما تاريخية؛ المستويات تعريف واحد من كثير."]
    return "\n".join(L) + "\n"


def main():
    import yfinance as yf
    rng = np.random.default_rng(11)
    spy = yf.Ticker("SPY").history(start="2023-06-01", end="2026-10-31", auto_adjust=True)["Close"]
    spy.index = spy.index.tz_localize(None).normalize()
    paths = {os.path.basename(p).split(".")[0]: p for p in glob.glob(os.path.join(DATA, "bars_5m", "*.csv.gz"))}
    rows = []
    for s in sorted(p for p in paths if p not in ETFS):
        try:
            d5 = read_bars(paths[s])
            d5 = d5[d5.index >= pd.Timestamp("2023-06-01")]
            r = run_symbol(s, d5, rng, spy)
        except Exception as ex:  # noqa: BLE001
            print("تخطي", s, type(ex).__name__, ex)
            continue
        rows += r
        print(s, len(r), flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(OUT_CSV, index=False)
    md = report(df)
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main()
