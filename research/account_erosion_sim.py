#!/usr/bin/env python3
"""محاكاة «تآكل الحساب» (مونت كارلو، قراءة فقط، رياضة بحتة لا بيانات): ماذا يحدث لحساب يشتري خيارات بكلفة السوق وإصابة معيّنة؟ بطلب المستخدم 24/10/2026.

كل صفقة تخاطر بنسبة f من الرصيد الحالي، وتربح W (عائد العقد عند الإصابة) باحتمال p، أو تخسر L (عند الخطأ) باحتمال 1−p. العوائد من option_buyer_cost (VIX × 1.0، خروج بعد 60 دقيقة):
  ATM 1DTE: W=+8%، L=−21% (تعادل 72%)   ATM 0DTE: W=+11%، L=−37% (تعادل 78%)   الأرقام نظرية الأسعار؛ الحقيقية تُحدَّث من long_price_calibration.
نعرض لكل (سيناريو، حجم، إصابة): وسيط الرصيد النهائي، أسوأ 5%، احتمال أن ينتهي أقل من نصف الرأسمال، واحتمال أن يمرّ بهبوط ≥50% في أي وقت، واحتمال الربح.
ثابت المسار: صفقة واحدة يومياً، 250 صفقة (سنة)، 20000 مسار، بذرة ثابتة. العوائد ثابتة (بلا توزيع داخل الفئة) فهي تقريبية للتآكل، لا تنبؤ.
القراءة: عند إصابة تحت نسبة التعادل يتآكل الحساب حتماً ويزداد التآكل بزيادة الحجم؛ فوقها ينمو لكن بتذبذب كبير. لا تُرفع الإصابة بأمنية: نستعمل الإصابة التي تثبتها الظلال فقط.
تشغيل: python research/account_erosion_sim.py   المخرج: research/account_erosion_sim_results.md
"""
import json
import os
import sys
from datetime import datetime

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "account_erosion_sim_results.md")
REAL = os.path.join(HERE, "long_price_calibration_wl.json")      # W/L الحقيقيان من long_price_calibration إن وُجدا
SCENARIOS = {"ATM 1DTE (60د)": (0.08, -0.21), "ATM 0DTE (60د)": (0.11, -0.37)}
SIZES = (0.05, 0.10, 0.25)
HITS = (0.50, 0.55, 0.60, 0.70, 0.80)
TRADES = 250
PATHS = 20000
RUIN = 0.5


def breakeven(w, l):
    return -l / (w - l)


def simulate(w, l, p, f, trades=TRADES, paths=PATHS, seed=7):
    """نتائج المسارات: dict(final: مصفوفة الرصيد النهائي، dd50: هل مرّ بهبوط ≥ RUIN من القمة، start=1)."""
    rng = np.random.default_rng(seed)
    win = rng.random((paths, trades)) < p
    mult = 1.0 + f * np.where(win, w, l)
    eq = np.cumprod(mult, axis=1)
    peak = np.maximum.accumulate(np.concatenate([np.ones((paths, 1)), eq], axis=1), axis=1)[:, 1:]
    dd = (eq / peak).min(axis=1)
    return {"final": eq[:, -1], "dd": dd}


def summarize(r):
    f = r["final"]
    return {"median": float(np.median(f)), "p5": float(np.percentile(f, 5)), "below_half": float((f < RUIN).mean()),
            "dd_half": float((r["dd"] <= RUIN).mean()), "profit": float((f > 1.0).mean())}


def expected_growth(w, l, p, f):
    """متوسط التغير اللوغاريتمي لكل صفقة (النمو الهندسي): القاعدة الحاسمة لنمو الحساب على المدى الطويل."""
    return p * np.log(1 + f * w) + (1 - p) * np.log(1 + f * l)


def scenarios(path=None):
    """السيناريوهات: الحقيقية أولاً (إن وُجد ملف المعايرة) ثم النظرية للمقارنة."""
    path = path or REAL
    out = {}
    try:
        for k, v in json.load(open(path, encoding="utf-8")).items():
            out[f"{k} (60د، أسعار حقيقية)"] = (v["W"], v["L"])
    except Exception:  # noqa: BLE001
        pass
    out.update({f"{k} (نظري)" if out else k: v for k, v in SCENARIOS.items()})
    return out


def report():
    L = ["# محاكاة تآكل الحساب لمشتري الخيارات (حساب رياضي، لا بيانات)", "",
         f"- {PATHS} مسار × {TRADES} صفقة (صفقة يومياً لسنة). كل صفقة تخاطر بنسبة f من الرصيد؛ الرصيد الابتدائي 1.00. العوائد W وL ثابتة من option_buyer_cost (نظرية الأسعار).",
         "- الوسيط = ما يحدث لحساب «نموذجي»؛ احتمال هبوط ≥50% = احتمال أن يمر الحساب بنصف قيمته من قمته في أي لحظة. لا تنبؤ: توضيح للتآكل.", ""]
    for name, (w, l) in scenarios().items():
        be = breakeven(w, l)
        L += [f"## {name}: W={100 * w:+.0f}% وL={100 * l:+.0f}% — نسبة التعادل {100 * be:.0f}%", "",
              "| الإصابة | الحجم f | نمو لوغاريتمي/صفقة | وسيط الرصيد | أسوأ 5% | احتمال < 0.5 | احتمال هبوط ≥50% | احتمال الربح |", "|---|---|---|---|---|---|---|---|"]
        for p in HITS:
            for f in SIZES:
                s = summarize(simulate(w, l, p, f))
                g = expected_growth(w, l, p, f)
                L.append(f"| {100 * p:.0f}%{' (تعادل≈)' if abs(p - be) < 0.04 else ''} | {100 * f:.0f}% | {100 * g:+.2f}% | {s['median']:.2f} | {s['p5']:.2f} | {100 * s['below_half']:.0f}% | {100 * s['dd_half']:.0f}% | {100 * s['profit']:.0f}% |")
        L.append("")
    L += ["- القراءة: تحت نسبة التعادل يكون النمو اللوغاريتمي سالباً مهما صغُر الحجم: التآكل حتمي والحجم يسرّعه فقط. فوقها لا يكفي الربح الوسطي: الحجم الكبير يرفع احتمال هبوط ≥50% حتى مع ميزة حقيقية.",
          "- لذلك لا نتداول بمال حقيقي قبل أن تُثبت الظلال إصابة **فوق نسبة التعادل بفاصل ثقة** على أيام جديدة، وبحجم صغير."]
    return L


def main(argv):
    L = report()
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
