#!/usr/bin/env python3
"""H-FADE30: العكس بعد حركة SPY ≥ 0.25% في 30 دقيقة (مسجّل في CLAUDE.md 21/11/2026). اختبار فقط.
تشغيل: python research/fade30_study.py <مسار spy_5m.csv.gz أو مجلده>   المخرج: research/fade30_results.md
"""
import os
import sys
from datetime import time as dtime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brksigns_study as bk  # noqa: E402

OUT = os.path.join(HERE, "fade30_results.md")
TH, LOOK, GAP = 0.0025, 6, 12
HZ = [15, 30, 60]
FIRST, LAST = dtime(10, 0), dtime(15, 0)
SPLIT = pd.Timestamp("2022-11-23").date()
EXT = 0.0005


def events(d5):
    ev, base = [], []
    for dt, g in d5.groupby(d5.index.date):
        if len(g) < 70:
            continue
        c, h, l = (g[k].to_numpy(float) for k in ("Close", "High", "Low"))
        tm = g.index.time
        n = len(c)

        def fwd(j, m):
            k = j + m // 5
            return None if k >= n else (c[k] / c[j] - 1) * 1e4
        last = -999
        for j in range(LOOK, n):
            ct = (g.index[j] + pd.Timedelta(minutes=5)).time()      # وقت إغلاق الشمعة
            if not (FIRST <= ct <= LAST):
                continue
            base.append({"day": dt, **{f"r{m}": fwd(j, m) for m in HZ}})
            r30 = c[j] / c[j - LOOK] - 1
            if abs(r30) < TH or j - last < GAP:
                continue
            last = j
            d = -1 if r30 > 0 else 1
            hi, lo = h[:j + 1].max(), l[:j + 1].min()
            ext = (c[j] >= hi * (1 - EXT)) if r30 > 0 else (c[j] <= lo * (1 + EXT))
            ev.append({"day": dt, "dir": d, "ext": bool(ext), **{f"r{m}": (None if fwd(j, m) is None else d * fwd(j, m)) for m in HZ}})
    return pd.DataFrame(ev), pd.DataFrame(base)


def boot(x, days, seed=0, nb=2000):
    """متوسط وفاصل 95% بإعادة معاينة الأيام."""
    u = np.array(sorted(set(days)))
    idx = {d: i for i, d in enumerate(u)}
    k = np.array([idx[d] for d in days])
    s, cnt = np.bincount(k, x, len(u)), np.bincount(k, None, len(u))
    W = np.random.default_rng(seed).multinomial(len(u), np.full(len(u), 1 / len(u)), size=nb).astype(float)
    bs = (W @ s) / (W @ cnt)
    return float(x.mean()), np.percentile(bs, [2.5, 97.5])


def main(argv):
    p = argv[0] if argv else "/opt/spxbot/data/spy_5m.csv.gz"
    if os.path.isdir(p):
        p = os.path.join(p, "spy_5m.csv.gz")
    ev, base = events(bk.read_bars(p))
    for df in (ev, base):
        df["half"] = np.where(df["day"] < SPLIT, 1, 2)
    for m in HZ:
        mu = base.groupby("half")[f"r{m}"].mean()
        ev[f"a{m}"] = ev[f"r{m}"] - ev["dir"] * ev["half"].map(mu)
    L = ["# H-FADE30: العكس بعد حركة SPY ≥ 0.25% في 30 دقيقة (نتائج آلية)", "",
         f"- {len(ev)} إشارة على {ev['day'].nunique()} يوماً (2019 → {ev['day'].max()}). العائد في جهة العكس بنقاط الأساس ناقص المرجع العشوائي.", "",
         "| النصف | إشارات | 15د | 30د | 60د | فاصل 95% (60د) | رابحة 60د |", "|---|---|---|---|---|---|---|"]
    res = {}
    for hf in (1, 2):
        s = ev[(ev["half"] == hf) & ev["a60"].notna()]
        m60, ci = boot(s["a60"].to_numpy(), s["day"].tolist())
        res[hf] = (m60, ci)
        L.append(f"| {hf} | {len(s)} | {s['a15'].mean():+.1f} | {s['a30'].mean():+.1f} | {m60:+.1f} | [{ci[0]:+.1f}، {ci[1]:+.1f}] | "
                 f"{(s['r60'] > 0).mean() * 100:.0f}% |")
    ok = res[1][0] > 0 and res[2][0] > 0 and res[2][1][0] > 0
    L += ["", f"⚖️ **R1: {'مدعومة — تدخل ظلاً بعقود حقيقية قبل البوت' if ok else 'غير مدعومة'}**.", "",
          "## وصفي: عند قمة/قاع اليوم مقابل وسط المدى (النصف الثاني)", "", "| الموضع | إشارات | 60د (نقطة أساس) | ≈ نقاط SPX | رابحة |", "|---|---|---|---|---|"]
    s2 = ev[(ev["half"] == 2) & ev["a60"].notna()]
    for flag, nm in ((True, "عند قمة/قاع اليوم"), (False, "وسط المدى")):
        g = s2[s2["ext"] == flag]
        if len(g):
            L.append(f"| {nm} | {len(g)} | {g['a60'].mean():+.1f} | {g['a60'].mean() * 0.0001 * 6800:+.1f} | {(g['r60'] > 0).mean() * 100:.0f}% |")
    L += ["", "تحفظ: على الأصل بلا كلفة؛ عقود 0DTE تخسر من الزمن وفرق السعر، فالميزة يجب أن تكون أكبر بكثير من بضع نقاط أساس."]
    md = "\n".join(L) + "\n"
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main(sys.argv[1:])
