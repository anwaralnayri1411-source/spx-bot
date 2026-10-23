#!/usr/bin/env python3
"""دراسة أيام اجتماعات الفيدرالي (FOMC) على SPY (قراءة فقط، استكشافية): هل «تجنب الحدث» أو «التداول بعده» أفضل؟ بطلب المستخدم 24/10/2026.

الإعلان 14:00 نيويورك. من شموع SPY 5 دقائق (`research/spy_history.py`) لأيام الاجتماعات مقابل الأيام العادية:
  (أ) التذبذب: متوسط الحركة المطلقة 14:00→16:00 وتذبذب الشموع 13:00–14:00 (الهدوء قبل الحدث) ونسبتهما للأيام العادية.
  (ب) هل خسرت الخيارات التسعير؟ نسبة الحركة المطلقة لليوم كله (افتتاح→إغلاق) إلى الحركة اليومية الضمنية VIX/√252: في أيام FOMC مقابل غيرها.
  (ج) الاستمرار: إشارة حركة 14:00→14:30 × حركة 14:30→16:00 (استمرار أم انعكاس)، وإشارة أول 5 دقائق × بقية النصف ساعة، وإشارة الحركة × عائد الغد.
أيام الاجتماعات من الذاكرة (لا مصدر خارجي متاح الآن) فنتحقق منها بالبيانات: كل تاريخ يُقبل فقط إن كان يوم تداول كاملاً وأظهرت شموع 14:00–14:30 مدى أعلى من 1.5× وسيط مداها في الأيام العادية.
استكشافية: لا فرضية مسجّلة رسمية (ما يصلح للقرار يُسجَّل فرضيته ويُختبر على أيام جديدة). عدد الأحداث نحو 60 فقط: الفواصل واسعة.
تشغيل: python research/fomc_study.py [مسار الشموع]   المخرج: research/fomc_study_results.md
"""
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import levels_study as ls  # noqa: E402

OUT = os.path.join(HERE, "fomc_study_results.md")
# اجتماعات مجدولة (يوم الإعلان). من الذاكرة، تُتحقق بالبيانات. بلا اجتماعات الطوارئ (مارس 2020).
FOMC = ("2019-01-30 2019-03-20 2019-05-01 2019-06-19 2019-07-31 2019-09-18 2019-10-30 2019-12-11 "
        "2020-01-29 2020-04-29 2020-06-10 2020-07-29 2020-09-16 2020-11-05 2020-12-16 "
        "2021-01-27 2021-03-17 2021-04-28 2021-06-16 2021-07-28 2021-09-22 2021-11-03 2021-12-15 "
        "2022-01-26 2022-03-16 2022-05-04 2022-06-15 2022-07-27 2022-09-21 2022-11-02 2022-12-14 "
        "2023-02-01 2023-03-22 2023-05-03 2023-06-14 2023-07-26 2023-09-20 2023-11-01 2023-12-13 "
        "2024-01-31 2024-03-20 2024-05-01 2024-06-12 2024-07-31 2024-09-18 2024-11-07 2024-12-18 "
        "2025-01-29 2025-03-19 2025-05-07 2025-06-18 2025-07-30 2025-09-17 2025-10-29 2025-12-10 "
        "2026-01-28 2026-03-18 2026-04-29 2026-06-17 2026-07-29 2026-09-16 2026-10-28").split()
I_1300, I_1400, I_1405, I_1430 = 42, 54, 55, 60     # فهارس بدايات الشموع (9:30 = 0)
RANGE_RATIO = 1.5


def day_stats(c, h, l, o):
    """إحصاءات يوم من شموعه (78): مدى 14:00–14:30 ومدى 13:00–14:00 وحركة 14:00→16:00 وعوائد النوافذ."""
    if len(c) < 78:
        return None
    ref = c[I_1400 - 1]
    return {"rng_rel": float(h[I_1400:I_1430].max() - l[I_1400:I_1430].min()) / ref,
            "calm": float(h[I_1300:I_1400].max() - l[I_1300:I_1400].min()) / ref,
            "move_pm": float(c[-1] / ref - 1), "r_5": float(c[I_1405 - 1] / ref - 1), "r_30": float(c[I_1430 - 1] / ref - 1),
            "r_rest30": float(c[I_1430 - 1] / c[I_1405 - 1] - 1), "post": float(c[-1] / c[I_1430 - 1] - 1),
            "day_move": float(c[-1] / o[0] - 1), "close": float(c[-1])}


def build(d, vix):
    """DataFrame لكل يوم كامل: إحصاءات + isfomc + VIX أمس + عائد الغد. d: شموع محمّلة (ls.load)."""
    rows = []
    for day, g in d.groupby(d["t"].dt.date):
        g, half = ls.trim_half_day(g)
        if half or len(g) < 78:
            continue
        s = day_stats(*(g[k].to_numpy(float) for k in ("c", "h", "l", "o")))
        if s:
            s["day"] = pd.Timestamp(day)
            rows.append(s)
    X = pd.DataFrame(rows).sort_values("day").reset_index(drop=True)
    X["next"] = X["close"].shift(-1) / X["close"] - 1
    X["vix"] = X["day"].map(vix)
    X["isfomc"] = X["day"].isin(pd.to_datetime(FOMC))
    return X


def validated(X):
    """يقبل من FOMC الأيام الموجودة بمدى 14:00–14:30 ≥ RANGE_RATIO × وسيط الأيام العادية؛ يرجع (X مع عمود ok، عدد المقبول)."""
    med = X.loc[~X["isfomc"], "rng_rel"].median()
    X = X.copy()
    X["ok"] = X["isfomc"] & (X["rng_rel"] >= RANGE_RATIO * med)
    return X, int(X["ok"].sum())


def boot(v, n=3000, seed=9):
    v = np.asarray(v, dtype=float)
    if len(v) < 10:
        return None
    rng = np.random.default_rng(seed)
    m = [v[rng.integers(0, len(v), len(v))].mean() for _ in range(n)]
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def line(name, v):
    v = np.asarray(v, dtype=float)
    v = v[np.isfinite(v)]
    ci = boot(v)
    return f"| {name} | {len(v)} | {100 * (v > 0).mean():.0f}% | {100 * v.mean():+.3f}% | " + ("-" if ci is None else f"{100 * ci[0]:+.2f}…{100 * ci[1]:+.2f}") + " |"


def report(X, n_listed, n_ok, meta):
    ev, normal = X[X["ok"]], X[~X["isfomc"]]
    L = ["# أيام اجتماعات الفيدرالي على SPY (نتائج آلية، استكشافية)", "", f"- {meta}",
         f"- التواريخ المذكورة {n_listed}؛ المقبولة (يوم كامل ومدى 14:00–14:30 ≥ {RANGE_RATIO}× العادي): **{n_ok}**. الباقي استُبعد (غير موجود أو بلا حركة مميزة: إما تاريخ خاطئ في قائمة الذاكرة أو حدث بلا مفاجأة).",
         "- قراءة فقط ولا تغيّر أي توصية. الأحداث قليلة (نحو 60): الفواصل واسعة، والنتائج استكشافية.", ""]
    if n_ok < 20:
        return L + ["أحداث مقبولة قليلة جداً؛ لا استنتاج."]
    L += ["## (أ) التذبذب حول الإعلان", "", "| المقياس | أيام FOMC | أيام عادية | النسبة |", "|---|---|---|---|"]
    for name, col in (("مدى 14:00–14:30 (نسبة للسعر)", "rng_rel"), ("مدى 13:00–14:00 (الهدوء قبل الحدث)", "calm")):
        a, b = ev[col].mean(), normal[col].mean()
        L.append(f"| {name} | {100 * a:.3f}% | {100 * b:.3f}% | {a / b:.2f}× |")
    a, b = ev["move_pm"].abs().mean(), normal["move_pm"].abs().mean()
    L.append(f"| حركة 14:00→16:00 المطلقة | {100 * a:.3f}% | {100 * b:.3f}% | {a / b:.2f}× |")
    ev_i = ev.dropna(subset=["vix"])
    nr_i = normal.dropna(subset=["vix"])
    ra = (ev_i["day_move"].abs() / (ev_i["vix"] / 100 / np.sqrt(252))).mean()
    rb = (nr_i["day_move"].abs() / (nr_i["vix"] / 100 / np.sqrt(252))).mean()
    L += ["", "## (ب) هل سُعّر الحدث؟ الحركة اليومية المطلقة ÷ الحركة الضمنية (VIX÷√252)", "",
          f"- أيام FOMC: **{ra:.2f}** | الأيام العادية: **{rb:.2f}** (أقل من 1 = السوق يسعّر أكثر مما يتحرك، وهذا ما يخسره المشتري عادة).", ""]
    L += ["## (ج) الاستمرار أم الانعكاس بعد الإعلان (إشارة × عائد؛ فوق 0 = استمرار)", "",
          "| الإشارة والعائد | أحداث | استمرار | متوسط | فاصل 95% (%) |", "|---|---|---|---|---|",
          line("حركة 14:00→14:30 × حركة 14:30→16:00", np.sign(ev["r_30"]) * ev["post"]),
          line("أول 5 دقائق × بقية النصف ساعة", np.sign(ev["r_5"]) * ev["r_rest30"]),
          line("حركة 14:00→14:30 × عائد الغد", np.sign(ev["r_30"]) * ev["next"]),
          line("(مرجع) أيام عادية: 14:00→14:30 × 14:30→16:00", np.sign(normal["r_30"]) * normal["post"]), ""]
    L.append("⚠️ ستون حدثاً لا تكفي لقرار؛ ما يظهر «ميزة» هنا يُسجَّل كفرضية ويُختبر على اجتماعات جديدة (الاجتماع القادم 28/10/2026).")
    return L


def main(argv):
    path = argv[0] if argv else ls.DATA
    if not os.path.exists(path):
        print("لا ملف بيانات:", path)
        return 0
    import option_buyer_cost as obc
    d = ls.load(path)
    vix = obc.load_vix()
    X = build(d, vix)
    X, n_ok = validated(X)
    meta = f"البيانات: {len(X)} يوماً كاملاً من {X['day'].min():%Y-%m-%d} إلى {X['day'].max():%Y-%m-%d}"
    L = report(X, len(FOMC), n_ok, meta)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
