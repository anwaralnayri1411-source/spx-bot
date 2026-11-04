#!/usr/bin/env python3
"""H-CONFIRM (تاريخياً): هل تستمر حركة أول ساعة أكثر حين يؤكدها الحجم واتساع القطاعات؟
(مسجّل مسبقاً في CLAUDE.md 4/11/2026). اختبار فقط، لا يغيّر أي بوت. يستعمل دوال confirm_shadow الصافية نفسها.

البيانات: SPXBOT_DATA/spy_5m.csv.gz وbars_5m/<قطاع>.csv.gz (SIP، ساعات السوق)، 2019-01 → 2026-10-23.
الفرضية C1: متوسط (10:30 → الإغلاق بإشارة الحركة) لأيام «الحجم والاتساع معاً» − أيام «بلا تأكيد» > 0
بفاصل 95% (إعادة معاينة الأيام) يستثني الصفر، وموجب في النصفين الزمنيين.
تشغيل (الخادم): SPXBOT_DATA=/opt/spxbot/data python research/confirm_study.py   المخرج: research/confirm_study_results.md
"""
import os
import sys
from datetime import time as dtime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import confirm_shadow as cs  # noqa: E402

DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
OUT = os.path.join(HERE, "confirm_study_results.md")
END = pd.Timestamp("2026-10-24")
N_BOOT = 5000


def read_bars(path):
    from tournament import read_bars as rb
    d = rb(path)
    return d[d.index < END]


def daily_points(d5):
    """لكل يوم: إغلاق أمس، إغلاق شمعة 10:25، إغلاق اليوم."""
    at = d5[d5.index.time == dtime(10, 25)]["Close"]
    at.index = at.index.date
    g = d5.groupby(d5.index.date)["Close"].last()
    t = pd.DataFrame({"close": g, "p1030": at}).dropna()
    t["prev"] = g.shift(1).reindex(t.index)
    t["next"] = g.shift(-1).reindex(t.index)
    return t.dropna(subset=["prev"])


def build():
    spy = read_bars(os.path.join(DATA, "spy_5m.csv.gz"))
    fh = cs.first_hour_volume(spy)
    sp = daily_points(spy)
    sec = {}
    for s in cs.SECTORS:
        p = os.path.join(DATA, "bars_5m", f"{s}.csv.gz")
        if os.path.exists(p):
            sec[s] = daily_points(read_bars(p))
    rows = []
    for day, r in sp.iterrows():
        move = r["p1030"] / r["prev"] - 1
        side = "CALL" if move > 0 else "PUT"
        moves = {s: (t.at[day, "p1030"] / t.at[day, "prev"] - 1) if day in t.index else None for s, t in sec.items()}
        b, nb = cs.breadth(moves, side)
        c = cs.classify(move, cs.vol_ratio(fh, day), b, nb)
        oc, on = cs.outcome(side, r["p1030"], r["close"], r["next"] if not np.isnan(r["next"]) else None)
        rows.append({"day": str(day), **c, "out_close": oc, "out_next": on, "n_sec_avail": len(sec)})
    return pd.DataFrame(rows), sorted(sec)


def boot_diff(a, b, col, n=N_BOOT, seed=0):
    u = np.unique(np.r_[a["day"].values, b["day"].values])
    ba = dict(zip(a["day"], a[col]))
    bb = dict(zip(b["day"], b[col]))
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        s = rng.choice(u, len(u))
        xa = [ba[d] for d in s if d in ba]
        xb = [bb[d] for d in s if d in bb]
        if xa and xb:
            out.append(np.mean(xa) - np.mean(xb))
    return float(a[col].mean() - b[col].mean()), tuple(np.percentile(out, [2.5, 97.5]))


def report(df, secs):
    m = df[df["move_day"] & (df["n_sectors"] >= len(cs.SECTORS) - 1)]
    L = ["# H-CONFIRM: تأكيد حركة أول ساعة بالحجم واتساع القطاعات (نتائج آلية)", "",
         f"- SPY وقطاعات {len(secs)} ({' '.join(secs)})، شموع 5د SIP، {df['day'].min()} → {df['day'].max()}؛ "
         f"{len(df)} يوماً، منها {len(m)} يوم حركة (|SPY حتى 10:30| ≥ {cs.MOVE_MIN * 100:.1f}%) بقطاعات مكتملة.",
         f"- الحجم: أول ساعة ≥ {cs.VOL_X}× متوسط 20 جلسة. الاتساع: ≥ {cs.BREADTH_MIN} من 11 قطاعاً باتجاه الحركة. "
         "النتيجة بإشارة الحركة (موجب = استمرت).", "",
         "| المجموعة | أيام | حتى الإغلاق | استمرت | حتى إغلاق الغد |", "|---|---|---|---|---|"]
    for g, name in cs.GROUP_AR.items():
        x = m[m["group"] == g]
        if len(x):
            L.append(f"| {name} | {len(x)} | {x['out_close'].mean() * 100:+.3f}% | {(x['out_close'] > 0).mean() * 100:.0f}% | "
                     f"{x['out_next'].mean() * 100:+.3f}% |")
    L.append(f"| كل أيام الحركة | {len(m)} | {m['out_close'].mean() * 100:+.3f}% | {(m['out_close'] > 0).mean() * 100:.0f}% | "
             f"{m['out_next'].mean() * 100:+.3f}% |")
    a, b = m[m["group"] == "both"], m[m["group"] == "none"]
    d, (lo, hi) = boot_diff(a, b, "out_close")
    days = sorted(m["day"])
    mid = days[len(days) // 2]
    halves = [x[x["group"] == "both"]["out_close"].mean() - x[x["group"] == "none"]["out_close"].mean()
              for x in (m[m["day"] < mid], m[m["day"] >= mid])]
    dn, (lon, hin) = boot_diff(a.dropna(subset=["out_next"]), b.dropna(subset=["out_next"]), "out_next")
    ok = lo > 0 and min(halves) > 0
    L += ["", "## الفرضية C1", "",
          f"- **الفرق حتى الإغلاق (الاثنان معاً − بلا تأكيد):** {d * 100:+.3f} نقطة، فاصل 95% [{lo * 100:+.3f}، {hi * 100:+.3f}]",
          f"- النصف الأول {halves[0] * 100:+.3f} | النصف الثاني {halves[1] * 100:+.3f}",
          f"- وصفي، حتى إغلاق الغد: {dn * 100:+.3f} نقطة [{lon * 100:+.3f}، {hin * 100:+.3f}]", "",
          f"⚖️ **C1: {'تحققت' if ok else 'لم تتحقق'}**", "",
          "## وصفي: حسب حجم حركة أول ساعة", "", "| الحركة | المجموعة | أيام | حتى الإغلاق |", "|---|---|---|---|"]
    for lab, cond in (("0.2–0.5%", m["move"].abs() < 0.005), ("0.5–1%", (m["move"].abs() >= 0.005) & (m["move"].abs() < 0.01)),
                      (">1%", m["move"].abs() >= 0.01)):
        for g in ("both", "none"):
            x = m[cond & (m["group"] == g)]
            if len(x):
                L.append(f"| {lab} | {cs.GROUP_AR[g]} | {len(x)} | {x['out_close'].mean() * 100:+.3f}% |")
    L += ["", "تحفظ: SPY بدل ES؛ الحجم يرتفع آلياً في أيام الحركة الكبيرة؛ حركة المؤشر ليست ربح العقد (عقد SPX لليوم نفسه يحتاج حركة أكبر بكثير من الفرق هنا)."]
    return "\n".join(L) + "\n", ok


def main():
    df, secs = build()
    md, _ = report(df, secs)
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main()
