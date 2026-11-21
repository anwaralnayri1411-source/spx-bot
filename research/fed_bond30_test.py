#!/usr/bin/env python3
"""H-FEDBOND30: دخول SPY عند 14:05 يوم الفيدرالي باتجاه حركة SHY في أول شمعة بعد القرار، والخروج 14:30. مسجّل في CLAUDE.md 21/11/2026.
تشغيل: SPXBOT_DATA=/opt/spxbot/data python research/fed_bond30_test.py   المخرج: research/fed_bond30_results.md
"""
import os
import sys
from datetime import date

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fomc_study as fs  # noqa: E402
import fomc_surprise_study as fss  # noqa: E402

OUT = os.path.join(HERE, "fed_bond30_results.md")
I1355, I1405 = 53, 54
EXITS = {"14:15": 56, "14:20": 57, "14:30": 58}
TH_BOND, TH_SPY = 2.0, 0.10
NPERM = 10000


def trades():
    spy, shy = fss.load("SPY"), fss.load("SHY")
    bond, ctl = [], []
    for ds in fs.FOMC:
        d = date.fromisoformat(ds)
        if d not in spy or d not in shy:
            continue
        s, b = spy[d]["Close"].to_numpy(float), shy[d]["Close"].to_numpy(float)
        sb = (b[I1405] / b[I1355] - 1) * 1e4
        ss = (s[I1405] / s[I1355] - 1) * 100
        r = {k: (s[j] / s[I1405] - 1) * 100 for k, j in EXITS.items()}
        if abs(sb) >= TH_BOND:
            bond.append({"day": ds, "sig": sb, **{k: np.sign(sb) * v for k, v in r.items()}})
        if abs(ss) >= TH_SPY:
            ctl.append({"day": ds, "sig": ss, **{k: np.sign(ss) * v for k, v in r.items()}})
    return bond, ctl


def flip_p(x, seed=3):
    rng = np.random.default_rng(seed)
    x = np.asarray(x)
    null = (rng.choice([-1, 1], size=(NPERM, len(x))) * np.abs(x)).mean(1)
    return float((null >= x.mean()).mean())


def diff_p(a, c, seed=4):
    """B2 على الأيام المشتركة: فرق عائد الإشارتين، بقلب الإشارة لكل يوم."""
    common = sorted(set(a) & set(c))
    if not common:
        return np.nan, 0, np.nan
    dd = np.array([a[k] - c[k] for k in common])
    return float(dd.mean()), len(common), flip_p(dd, seed)


def main():
    bond, ctl = trades()
    L = ["# H-FEDBOND30: دخول 14:05 باتجاه السندات يوم الفيدرالي (نتائج آلية)", "",
         f"- إشارة السندات (|SHY 13:55→14:05| ≥ {TH_BOND:g} نقطة أساس): {len(bond)} اجتماعاً. الضابط (|SPY| ≥ {TH_SPY:g}%): {len(ctl)}.",
         "- العائد = حركة SPY بعد الدخول في جهة الإشارة، بالنسبة المئوية، بلا كلفة.", "",
         "| الخروج | السندات: متوسط | رابحة | الضابط (SPY): متوسط | رابحة |", "|---|---|---|---|---|"]
    for k in EXITS:
        a, c = [t[k] for t in bond], [t[k] for t in ctl]
        L.append(f"| {k} | {np.mean(a):+.3f}% | {np.mean(np.array(a) > 0) * 100:.0f}% | {np.mean(c):+.3f}% | {np.mean(np.array(c) > 0) * 100:.0f}% |")
    a = [t["14:30"] for t in bond]
    p1 = flip_p(a)
    m2, n2, p2 = diff_p({t["day"]: t["14:30"] for t in bond}, {t["day"]: t["14:30"] for t in ctl})
    ok = p1 < 0.05 and p2 < 0.05 and np.mean(a) > 0 and m2 > 0
    L += ["", f"- **B1** (متوسط اتجاه السندات > 0، خروج 14:30): {np.mean(a):+.3f}%، p = {p1:.3f}.",
          f"- **B2** (السندات − الضابط على {n2} يوماً مشتركاً): {m2:+.3f}%، p = {p2:.3f}.",
          f"- ⚖️ **الحكم: {'مدعومة — تُسجَّل وتُختبر على اجتماعات 9/12/2026 وما بعدها قبل أي بناء' if ok else 'غير مدعومة'}**.", "",
          "| اليوم | السندات (نقطة أساس) | 14:15 | 14:20 | 14:30 |", "|---|---|---|---|---|"]
    for t in bond:
        L.append(f"| {t['day']} | {t['sig']:+.1f} | {t['14:15']:+.2f}% | {t['14:20']:+.2f}% | {t['14:30']:+.2f}% |")
    L += ["", "تحفظ: 61 اجتماعاً فقط؛ على الأصل بلا كلفة، وفرق سعر عقود 0DTE لحظة القرار واسع."]
    md = "\n".join(L) + "\n"
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main()
