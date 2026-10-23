#!/usr/bin/env python3
"""دراسة فجوة الافتتاح وزخم أول 30 دقيقة على SPY (قراءة فقط، لا تغيّر أي توصية). بطلب المستخدم 24/10/2026: «نختبرها كلها».

البيانات: شموع SPY 5 دقائق لسنوات (`research/spy_history.py`)، الجلسة العادية، بلا أيام الإغلاق المبكر ولا أيام توزيع أرباح SPY (الجمعة الثالثة من مارس/يونيو/سبتمبر/ديسمبر: الفجوة فيها هبوط توزيع لا حركة سعر).
  الفجوة = افتتاح اليوم ÷ إغلاق أمس − 1. الدخول بعد أول شمعة (إغلاق 9:35) حتى لا نفترض تنفيذ الافتتاح.
**الفرضيات المسجّلة مسبقاً (24/10/2026، CLAUDE.md، قبل أي تشغيل)** — تُقسَّم الأيام عند 2022-11-23: النصف الأول استكشاف والثاني **حكم رسمي**، بفاصل 95% بإعادة معاينة الأيام:
  H-GAP: فجوة |≥0.4%| تتبعها حركة 9:35→10:30 في اتجاه الفجوة (إشارة × عائد > 0 بفاصل يستثني الصفر)، بعد 150 حدثاً على الأقل في النصف الثاني.
  H-LAST30: إشارة عائد أول 30 دقيقة (9:30→10:00) × عائد آخر 30 دقيقة (15:30→16:00) > 0 بفاصل يستثني الصفر، بعد 300 يوم على الأقل في النصف الثاني.
استكشافي بلا حكم: عتبات فجوة أخرى (0.2%/0.8%)، الخروج حتى الإغلاق، وانحدار آخر 30 دقيقة على أول 30 كاملاً. (عدد المقارنات كبير: لا يُعتمد أي رقم منفرد جيد.)
العوائد قبل أي كلفة تنفيذ: الميزة النظرية للمؤشر نفسه، والخيارات تضيف انتشاراً وثيتا (انظر option_buyer_cost).
تشغيل: python research/intraday_signals_study.py [مسار الملف]   المخرج: research/intraday_signals_study_results.md
"""
import os
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import levels_study as ls  # noqa: E402

OUT = os.path.join(HERE, "intraday_signals_study_results.md")
SPLIT = "2022-11-23"
GAP_MIN = 0.004
MIN_GAP_EVENTS = 150
MIN_LAST30_DAYS = 300
I_1000, I_1030, I_1530 = 5, 11, 71        # فهارس إغلاق الشموع: 10:00 و10:30 و15:30 (9:30 = 0)


# ============================ دوال نقية ============================
def third_friday(d):
    first = date(d.year, d.month, 1)
    return (first.weekday() <= 4) and d.day == 1 + (4 - first.weekday()) % 7 + 14


def is_ex_div_day(d):
    return d.month in (3, 6, 9, 12) and d.weekday() == 4 and third_friday(d)


def build_days(d):
    """DataFrame لكل يوم كامل: gap وr_first (9:30→10:00) وr_1030 (9:35→10:30) وr_close (9:35→الإغلاق) وr_last30. أمس = آخر يوم كامل أو إغلاق مبكر."""
    rows, prev_close = [], None
    for day, g in d.groupby(d["t"].dt.date):
        g, half = ls.trim_half_day(g)
        if len(g) < 30:
            continue
        o, c = g["o"].to_numpy(float), g["c"].to_numpy(float)
        full = len(g) >= 78 and not half
        if full and prev_close and not is_ex_div_day(day):
            rows.append({"day": pd.Timestamp(day), "gap": float(o[0] / prev_close - 1), "r_first": float(c[I_1000] / o[0] - 1),
                         "r_1030": float(c[I_1030] / c[0] - 1), "r_close": float(c[-1] / c[0] - 1),
                         "r_last30": float(c[-1] / c[I_1530] - 1)})
        prev_close = float(c[-1])
    return pd.DataFrame(rows)


def boot(v, n=4000, seed=17):
    v = np.asarray(v, dtype=float)
    v = v[np.isfinite(v)]
    if len(v) < 20:
        return None
    idx = np.random.default_rng(seed).integers(0, len(v), (n, len(v)))
    m = v[idx].mean(1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def edge(sign, ret):
    """إشارة × عائد لأيام الإشارة غير الصفرية: dict(n, hit, mean, ci)."""
    s, r = np.sign(np.asarray(sign, dtype=float)), np.asarray(ret, dtype=float)
    m = (s != 0) & np.isfinite(r)
    v = s[m] * r[m]
    if len(v) == 0:
        return None
    return {"n": len(v), "hit": float((v > 0).mean()), "mean": float(v.mean()), "ci": boot(v)}


def fmt(s):
    if s is None:
        return "- | - | - | -"
    ci = "-" if s["ci"] is None else f"{100 * s['ci'][0]:+.3f}…{100 * s['ci'][1]:+.3f}"
    return f"{s['n']} | {100 * s['hit']:.0f}% | {100 * s['mean']:+.3f}% | {ci}"


def judge(name, s, min_n, unit="حدثاً"):
    """حكم الفرضية على إحصاءة النصف الثاني."""
    if s is None or s["n"] < min_n:
        return f"{name}: لا حكم بعد ({0 if s is None else s['n']} من {min_n} {unit})"
    if s["ci"] is None:
        return f"{name}: لا حكم بعد (عينة صغيرة)"
    lo, hi = s["ci"]
    if lo > 0:
        return f"{name}: تحققت (متوسط {100 * s['mean']:+.3f}%، فاصل {100 * lo:+.3f}…{100 * hi:+.3f})"
    if hi < 0:
        return f"{name}: عكسية (الإشارة تخسر: متوسط {100 * s['mean']:+.3f}%)"
    return f"{name}: لم تتحقق (متوسط {100 * s['mean']:+.3f}% وفاصله {100 * lo:+.3f}…{100 * hi:+.3f} يشمل الصفر)"


def sections(X, label):
    """جداول جزء من الأيام X (النصف الأول أو الثاني أو الكل)."""
    L = [f"### {label} ({len(X)} يوماً)", "", "| الإشارة × العائد | أحداث | استمرار | متوسط | فاصل 95% (%) |", "|---|---|---|---|---|"]
    for thr in (0.002, GAP_MIN, 0.008):
        sub = X[X["gap"].abs() >= thr]
        tag = "**الأساسي** " if thr == GAP_MIN else "(استكشافي) "
        L.append(f"| {tag}فجوة ≥{100 * thr:.1f}% × حركة 9:35→10:30 | {fmt(edge(sub['gap'], sub['r_1030']))} |")
    sub = X[X["gap"].abs() >= GAP_MIN]
    L.append(f"| (استكشافي) فجوة ≥{100 * GAP_MIN:.1f}% × حركة 9:35→الإغلاق | {fmt(edge(sub['gap'], sub['r_close']))} |")
    L.append(f"| **الأساسي** أول 30 دقيقة × آخر 30 دقيقة | {fmt(edge(X['r_first'], X['r_last30']))} |")
    L.append(f"| (استكشافي) أول 30 دقيقة × حركة 10:00→الإغلاق مطروحاً منها آخر 30 | {fmt(edge(X['r_first'], X['r_close'] - X['r_first'] - X['r_last30']))} |")
    return L + [""]


def report(X, meta):
    L = ["# فجوة الافتتاح وزخم أول 30 دقيقة على SPY (نتائج آلية)", "", f"- {meta}",
         "- قراءة فقط ولا تغيّر أي توصية. العوائد على المؤشر قبل الكلفة. فاصل 95% بإعادة معاينة الأيام. الاستمرار = نسبة الأيام التي ربحت فيها الإشارة.",
         f"- **الفرضيات المسجّلة مسبقاً** (النصف الثاني بعد {SPLIT} = الحكم): H-GAP: فجوة ≥{100 * GAP_MIN:.1f}% × حركة 9:35→10:30 > 0 بعد {MIN_GAP_EVENTS} حدثاً؛ H-LAST30: أول 30 × آخر 30 > 0 بعد {MIN_LAST30_DAYS} يوماً.", ""]
    if len(X) == 0:
        return L + ["لا أيام."]
    first, second = X[X["day"] < SPLIT], X[X["day"] >= SPLIT]
    L += sections(first, "النصف الأول (استكشاف)") + sections(second, "النصف الثاني (الحكم الرسمي)")
    g2 = second[second["gap"].abs() >= GAP_MIN]
    L += ["⚖️ **" + judge("H-GAP", edge(g2["gap"], g2["r_1030"]), MIN_GAP_EVENTS) + "**", "",
          "⚖️ **" + judge("H-LAST30", edge(second["r_first"], second["r_last30"]), MIN_LAST30_DAYS, "يوماً") + "**"]
    return L


def main(argv):
    path = argv[0] if argv else ls.DATA
    if not os.path.exists(path):
        print("لا ملف بيانات:", path)
        return 0
    d = ls.load(path)
    X = build_days(d)
    meta = f"البيانات: {len(X)} يوماً كاملاً" + (f" من {X['day'].min():%Y-%m-%d} إلى {X['day'].max():%Y-%m-%d}" if len(X) else "")
    L = report(X, meta)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
