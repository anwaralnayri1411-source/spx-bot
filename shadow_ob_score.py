"""ظل «نقاط قوة Order Block» (تسجيل وقياس فقط، لا يرسل ولا يغيّر أي توصية). أصله: فيديو (23/10) يقول إن المنطقة الأقوى تجمع أربعة عوامل.
العوامل (نقطة لكل منها): fvg = اندفاع المنطقة ترك فجوة FVG (الشمعة الأولى بعد شمعة OB)؛ bos = الاندفاع كسر قمة/قاع آخر ساعة؛
fresh = أول لمس للمنطقة (الثاني لا)؛ liq = قيعان/قمم متساوية (≥2 خلال 0.05%) تكوّنت بين الاندفاع والمنطقة وكُسرت في الطريق إليها.
القياس كما في shadow_levels (أقصى حركة لصالح/ضد خلال 12 شمعة 5د) ومقارنة بمنطقة وهمية مُزاحة 0.25%؛ والسؤال: هل ترتفع النتيجة مع عدد النقاط؟"""
import os
import sys

import shadow_levels as sl

OUT = "shadow_ob_score_log.csv"
HEAD = ["time_ny", "kind", "side", "zone_lo", "zone_hi", "fvg", "bos", "fresh", "liq", "score", "fav", "adv"]
SHIFT, LB, EQ = 0.0025, 12, 0.0005


def _liq(g, side, i, j, zl, zh):
    """قيعان (للشراء) أو قمم (للبيع) متساوية بين الاندفاع واللمس، فوق/تحت المنطقة."""
    pts = []
    for k in range(i + 2, j - 1):
        if side == "bull" and g.low[k] < g.low[k - 1] and g.low[k] < g.low[k + 1] and g.low[k] > zh:
            pts.append(g.low[k])
        if side == "bear" and g.high[k] > g.high[k - 1] and g.high[k] > g.high[k + 1] and g.high[k] < zl:
            pts.append(g.high[k])
    return any(abs(a - b) / a <= EQ for n, a in enumerate(pts) for b in pts[n + 1:])


def day_rows(g):
    g = g.reset_index()
    n, rows = len(g), []
    for i in range(LB, n - 8 - sl.FWD):
        c, o = g.close[i], g.open[i]
        for side in ("bull", "bear"):
            if side == "bull" and not (c < o and (g.high[i + 1:i + 7].max() - c) / c >= sl.DISP): continue
            if side == "bear" and not (c > o and (c - g.low[i + 1:i + 7].min()) / c >= sl.DISP): continue
            fvg = False
            for fi in range(i + 3, i + 5):
                lo, hi = (g.high[fi - 2], g.low[fi]) if side == "bull" else (g.high[fi], g.low[fi - 2])
                fvg = fvg or (hi > lo and (hi - lo) / g.close[fi] >= 0.0006)
            bos = (g.high[i + 1:i + 7].max() > g.high[i - LB:i].max()) if side == "bull" else (g.low[i + 1:i + 7].min() < g.low[i - LB:i].min())
            for kind, k in (("real", 0.0), ("placebo", -SHIFT if side == "bull" else SHIFT)):
                zl, zh = g.low[i] * (1 + k), g.high[i] * (1 + k)
                cnt, j = 0, i + 7
                while j < n - sl.FWD and cnt < 2:
                    t = g.low[j] <= zh if side == "bull" else g.high[j] >= zl
                    if (g.close[j] < zl) if side == "bull" else (g.close[j] > zh):
                        break
                    if t:
                        r = sl._react(g, j, g.close[j], side == "bull")
                        if r:
                            liq = _liq(g, side, i, j, zl, zh)
                            fresh = cnt == 0
                            rows.append((str(g.iloc[j, 0])[:16], kind, side, round(zl, 2), round(zh, 2), int(fvg), int(bos), int(fresh), int(liq),
                                         int(fvg) + int(bos) + int(fresh) + int(liq), round(r[0], 5), round(r[1], 5)))
                        cnt += 1
                        j += 6
                    j += 1
    return rows


def report(rows):
    """rows: tuples بترتيب HEAD (الأرقام أعداداً)."""
    for kind in ("real", "placebo"):
        rs = [r for r in rows if r[1] == kind]
        print(kind, sl.summarize(rs, 10, 11), "أيام=", len({r[0][:10] for r in rs}))
        for s in range(5):
            x = [r for r in rs if r[9] == s]
            if x: print(f"   نقاط={s}", sl.summarize(x, 10, 11))
    rs = [r for r in rows if r[1] == "real"]
    for name, ix in (("fvg", 5), ("bos", 6), ("fresh", 7), ("liq", 8)):
        for v in (1, 0):
            x = [r for r in rs if r[ix] == v]
            if x: print(f"   {name}={v}", sl.summarize(x, 10, 11))


def daily(period="10d"):
    import yfinance as yf
    h = yf.Ticker("^SPX").history(period=period, interval="5m").reset_index()
    h.columns = [str(c).lower() for c in h.columns]
    d = sl._load(h.rename(columns={h.columns[0]: "time"}))
    days = "shadow_ob_score_days.txt"
    done = set(open(days).read().split()) if os.path.exists(days) else set()
    for day, g in d.groupby("day"):
        k = day.isoformat()
        if k in done or k < "2026-10-23" or len(g) < 70:
            continue
        sl.append(OUT, HEAD, day_rows(g))
        with open(days, "a") as f:
            f.write(k + "\n")
        print("تمت معالجة", k)


def _read():
    import csv
    return [tuple(r[:3] + [float(r[3]), float(r[4])] + [int(x) for x in r[5:10]] + [float(r[10]), float(r[11])]) for r in list(csv.reader(open(OUT, encoding="utf-8")))[1:]]


if __name__ == "__main__":
    if sys.argv[1:] == ["--daily"]:
        daily()
    elif sys.argv[1:] == ["--report"] and os.path.exists(OUT):
        report(_read())
    else:      # اختبار رجعي على الكاش (لا يكتب ملفات)
        d, R = sl._load("research/spx_5m_cache.csv"), []
        for _, g in d.groupby("day"):
            R += day_rows(g)
        report(R)
