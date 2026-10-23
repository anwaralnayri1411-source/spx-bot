"""ظل «التقاء Order Block مع FVG» (تسجيل وقياس فقط، لا يرسل ولا يغيّر أي توصية). أصله: شرح فيديو (23/10).
التعريف: منطقة OB (نفس تعريف shadow_levels) تتقاطع مع فجوة FVG من الاتجاه نفسه؛ المنطقة = التقاطع.
الدخول: أول عودة للمنطقة بعد تكوّنهما معاً دون كسرها بإغلاق. القياس كما في shadow_levels: أقصى حركة لصالح الارتداد/ضده خلال 12 شمعة 5د.
المقارنة: نفس المنطقة مُزاحة 0.25% (وهمي) في الاتجاه المعاكس لها، ثم الحكم بالأيام لا بالحالات."""
import os
import sys

import pandas as pd

import shadow_levels as sl

OUT = "shadow_confluence_log.csv"
HEAD = ["time_ny", "kind", "side", "zone_lo", "zone_hi", "fav", "adv"]
SHIFT = 0.0025


def zones(g):
    """(side, ob_i, fvg_i, lo, hi) لكل التقاء؛ g بعد reset_index."""
    n, out = len(g), []
    obs, fvs = [], []
    for i in range(1, n - 8 - sl.FWD):
        c, o = g.close[i], g.open[i]
        if c < o and (g.high[i + 1:i + 7].max() - c) / c >= sl.DISP:
            obs.append(("bull", i, g.low[i], g.high[i]))
        if c > o and (c - g.low[i + 1:i + 7].min()) / c >= sl.DISP:
            obs.append(("bear", i, g.low[i], g.high[i]))
    for i in range(2, n - sl.FWD - 1):
        for side in ("bull", "bear"):
            lo, hi = (g.high[i - 2], g.low[i]) if side == "bull" else (g.high[i], g.low[i - 2])
            if hi > lo and (hi - lo) / g.close[i] >= 0.0006:
                fvs.append((side, i, lo, hi))
    for s, oi, ol, oh in obs:
        for s2, fi, fl, fh in fvs:
            lo, hi = max(ol, fl), min(oh, fh)
            if s == s2 and lo < hi:
                out.append((s, oi, fi, lo, hi))
    return out


def first_touch(g, side, start, lo, hi):
    """أول عودة للمنطقة بعد start دون كسر؛ يرجع (j, لصالح, ضد) أو None."""
    for j in range(start, len(g) - sl.FWD):
        t = g.low[j] <= hi if side == "bull" else g.high[j] >= lo
        if t:
            ok = g.close[j] > lo if side == "bull" else g.close[j] < hi
            r = sl._react(g, j, g.close[j], side == "bull") if ok else None
            return (j, r[0], r[1]) if r else None
        if (g.close[j] < lo) if side == "bull" else (g.close[j] > hi):
            return None
    return None


def day_rows(g):
    g = g.reset_index()
    rows, seen = [], set()
    for side, oi, fi, lo, hi in zones(g):
        start = max(oi + 7, fi + 1)           # OB لا يُعتمد قبل اكتمال اندفاعه
        for kind, k in (("real", 0.0), ("placebo", -SHIFT if side == "bull" else SHIFT)):
            zl, zh = lo * (1 + k), hi * (1 + k)
            r = first_touch(g, side, start, zl, zh)
            if r and (kind, side, r[0]) not in seen:
                seen.add((kind, side, r[0]))
                rows.append((str(g.iloc[r[0], 0])[:16], kind, side, round(zl, 2), round(zh, 2), round(r[1], 5), round(r[2], 5)))
    return rows


def report(rows):
    by = {}
    for r in rows:
        by.setdefault(r[1], []).append(r)
    for kind, rs in by.items():
        days = len({r[0][:10] for r in rs})
        print(f"{kind:8s}", sl.summarize(rs, 5, 6), f"أيام={days}")


def daily(period="10d"):
    import yfinance as yf
    h = yf.Ticker("^SPX").history(period=period, interval="5m").reset_index()
    h.columns = [str(c).lower() for c in h.columns]
    d = sl._load(h.rename(columns={h.columns[0]: "time"}))
    days = "shadow_confluence_days.txt"
    done = set(open(days).read().split()) if os.path.exists(days) else set()
    for day, g in d.groupby("day"):
        k = day.isoformat()
        if k in done or k < "2026-10-23" or len(g) < 70:
            continue
        sl.append(OUT, HEAD, day_rows(g))
        with open(days, "a") as f:
            f.write(k + "\n")
        print("تمت معالجة", k)


if __name__ == "__main__":
    if sys.argv[1:] == ["--daily"]:
        daily()
    elif sys.argv[1:] == ["--report"] and os.path.exists(OUT):
        report([tuple(r[:5]) + (float(r[5]), float(r[6])) for r in pd.read_csv(OUT, dtype=str).values.tolist()])
    else:      # اختبار رجعي على الكاش (لا يكتب ملفات)
        d, R = sl._load("research/spx_5m_cache.csv"), []
        for _, g in d.groupby("day"):
            R += day_rows(g)
        print("التقاء OB+FVG، كل الكاش:")
        report(R)
