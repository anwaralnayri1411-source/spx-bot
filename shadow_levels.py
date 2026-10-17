"""اختبار ظلّي (تسجيل وقياس فقط، لا يرسل ولا يغيّر أي توصية):
 1) شبكة مستويات على طريقة MGT (مرساة التوازن + خطوة 0.25%) ومقارنتها بشبكة وهمية مُزاحة نصف خطوة.
 2) مناطق Order Block (شمعة معاكسة قبل اندفاع ≥0.25% خلال 30 دقيقة) وقياس أول عودة لها.
القياس: بعد اللمس نقيس خلال ساعة (12 شمعة 5د) أقصى حركة لصالح الارتداد وأقصى حركة ضده.
تنبيه: بيانات مؤخرة، عينة صغيرة، والمرساة 7734.4 صالحة لأيام 14-16 أكتوبر فقط."""
import csv
import os

import pandas as pd

ANCHOR, STEP_PCT, FWD, DISP = 7734.4, 0.0025, 12, 0.0025
OUT_GRID, OUT_OB = "shadow_grid_log.csv", "shadow_ob_log.csv"


def _load(path_or_df):
    d = path_or_df if isinstance(path_or_df, pd.DataFrame) else pd.read_csv(path_or_df)
    if "time" in d.columns:
        d = d.set_index(pd.to_datetime(d["time"], utc=True).dt.tz_convert("America/New_York"))
    d = d.rename(columns=str.lower)[["open", "high", "low", "close"]]
    d = d[(d.index.time >= pd.Timestamp("09:30").time()) & (d.index.time <= pd.Timestamp("16:00").time())]
    d["day"] = d.index.date
    return d.sort_index()


def _react(g, j, ent, up):
    fw = g.iloc[j + 1:j + 1 + FWD]
    if len(fw) < FWD:
        return None
    hi, lo = (fw.high.max() - ent) / ent, (ent - fw.low.min()) / ent
    return (hi, lo) if up else (lo, hi)      # (لصالح الارتداد، ضده)


def grid_day(g, anchor=ANCHOR, offset=0.0):
    step = anchor * STEP_PCT
    rows, last = [], {}
    g = g.reset_index()
    for j in range(1, len(g) - FWD):
        lo, hi = g.low[j], g.high[j]
        k0, k1 = int((lo - anchor) // step) - 1, int((hi - anchor) // step) + 2
        for k in range(k0, k1 + 1):
            lvl = anchor + (k + offset) * step
            if not (lo <= lvl <= hi) or last.get(k, -99) > j - 6:
                continue
            last[k] = j
            up = g.close[j - 1] > lvl          # جاء من فوق → المتوقع ارتداد للأعلى (دعم)
            r = _react(g, j, lvl, up)
            if r:
                rows.append((str(g.iloc[j, 0])[:16], round(lvl, 2), "support" if up else "resist", round(r[0], 5), round(r[1], 5)))
    return rows


def ob_day(g):
    g = g.reset_index()
    rows, n = [], len(g)
    for i in range(1, n - 8 - FWD):
        for side in ("bull", "bear"):
            red, grn = g.close[i] < g.open[i], g.close[i] > g.open[i]
            if side == "bull":
                if not red or (g.high[i + 1:i + 7].max() - g.close[i]) / g.close[i] < DISP: continue
            else:
                if not grn or (g.close[i] - g.low[i + 1:i + 7].min()) / g.close[i] < DISP: continue
            zlo, zhi = g.low[i], g.high[i]
            for j in range(i + 7, n - FWD):
                t = g.low[j] <= zhi if side == "bull" else g.high[j] >= zlo
                if t and (g.close[j] > zlo if side == "bull" else g.close[j] < zhi):
                    r = _react(g, j, g.close[j], side == "bull")
                    if r: rows.append((str(g.iloc[j, 0])[:16], side, round(zlo, 2), round(zhi, 2), round(r[0], 5), round(r[1], 5)))
                    break
                if (g.close[j] < zlo) if side == "bull" else (g.close[j] > zhi): break
    return rows


def fvg_day(g, minsz=0.0006):
    """Fair Value Gap: فجوة بين شمعة 1 وشمعة 3 (high1 < low3 صاعدة / low1 > high3 هابطة) بحجم ≥0.06%؛ أول عودة للفجوة دون كسرها."""
    g = g.reset_index()
    rows, n = [], len(g)
    for i in range(2, n - FWD - 1):
        for side in ("bull", "bear"):
            lo, hi = (g.high[i - 2], g.low[i]) if side == "bull" else (g.high[i], g.low[i - 2])
            if hi <= lo or (hi - lo) / g.close[i] < minsz: continue
            for j in range(i + 1, n - FWD):
                t = g.low[j] <= hi if side == "bull" else g.high[j] >= lo
                brk = (g.close[j] < lo) if side == "bull" else (g.close[j] > hi)
                if t:
                    if not brk:
                        r = _react(g, j, g.close[j], side == "bull")
                        if r: rows.append((str(g.iloc[j, 0])[:16], side, round(lo, 2), round(hi, 2), round(r[0], 5), round(r[1], 5)))
                    break
                if brk: break
    return rows


def summarize(rows, fav_i, adv_i):
    if not rows: return "لا حالات"
    f = sum(r[fav_i] for r in rows) / len(rows); a = sum(r[adv_i] for r in rows) / len(rows)
    w = sum(r[fav_i] > r[adv_i] for r in rows) / len(rows)
    return f"n={len(rows)} لصالح={f:.4%} ضد={a:.4%} نسبة(لصالح>ضد)={w:.0%}"


def append(path, header, rows):
    fresh = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if fresh: w.writerow(header)
        w.writerows(rows)


def current_anchor():
    """آخر توازن مسجّل من تقارير MGT، وإلا المرساة الافتراضية."""
    try:
        rows = list(csv.DictReader(open("mgt_reports_log.csv", encoding="utf-8")))
        for r in reversed(rows):
            if r.get("equil"): return float(r["equil"])
    except Exception:
        pass
    return ANCHOR


def daily(period="10d"):
    """تشغيل يومي بعد الإغلاق: يعالج الأيام غير المعالجة (يبدأ من 14 أكتوبر 2026) ويسجّلها."""
    import yfinance as yf
    h = yf.Ticker("^SPX").history(period=period, interval="5m").reset_index()
    h.columns = [str(c).lower() for c in h.columns]
    h = h.rename(columns={h.columns[0]: "time"})
    d = _load(h)
    done = set(open("shadow_days.txt").read().split()) if os.path.exists("shadow_days.txt") else set()
    anchor = current_anchor()
    for day, g in d.groupby("day"):
        k = day.isoformat()
        if k in done or k < "2026-10-14" or len(g) < 70:      # يوم ناقص أو قبل بداية المرساة
            continue
        append(OUT_GRID, ["time_ny", "level", "type", "fav", "adv"], grid_day(g, anchor))
        append("shadow_grid_placebo_log.csv", ["time_ny", "level", "type", "fav", "adv"], grid_day(g, anchor, 0.5))
        append(OUT_OB, ["time_ny", "side", "zone_lo", "zone_hi", "fav", "adv"], ob_day(g))
        append("shadow_fvg_log.csv", ["time_ny", "side", "zone_lo", "zone_hi", "fav", "adv"], fvg_day(g))
        with open("shadow_days.txt", "a") as f: f.write(k + "\n")
        print("تمت معالجة", k, "مرساة", anchor)


if __name__ == "__main__" and __import__("sys").argv[1:] == ["--daily"]:
    daily()
elif __name__ == "__main__":      # اختبار رجعي على الكاش (لا يكتب ملفات)
    d = _load("research/spx_5m_cache.csv")
    G, P, O = [], [], []
    recent = {pd.Timestamp(x).date() for x in ("2026-10-14", "2026-10-15", "2026-10-16")}
    Gr, Pr = [], []
    for day, g in d.groupby("day"):
        a, b = grid_day(g), grid_day(g, offset=0.5)
        G += a; P += b; O += ob_day(g)
        if day in recent: Gr += a; Pr += b
    print("شبكة (كل 60 يوماً)   :", summarize(G, 3, 4))
    print("شبكة وهمية (+نصف خطوة):", summarize(P, 3, 4))
    print("شبكة (14-16 أكتوبر)  :", summarize(Gr, 3, 4))
    print("وهمية (14-16 أكتوبر) :", summarize(Pr, 3, 4))
    print("Order Block          :", summarize(O, 4, 5))
