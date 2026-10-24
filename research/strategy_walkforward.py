#!/usr/bin/env python3
"""مهارة الإشارات بمقارنة مزدوجة + اختيار متدرج (walk-forward) على SPY (قراءة فقط، بطلب المستخدم 24/10/2026: «نختبر ونطوّر على التاريخ قبل أي تجربة حية»).

(1) المهارة المزدوجة: لكل عينة دخول الأساس العشوائي = متوسط عائد الكول والبوت (ATM، الكلفة الحقيقية المعايَرة) = ما كان سيحققه رامي عملة بلا تباين. مهارة الإشارة = عائدها − الأساس على العينات نفسها.
    المقارنة مزدوجة فتزول معظم الضجة التي تُغطي الفروق الصغيرة؛ فاصل 95% بإعادة معاينة الأيام. موجبة وفاصلها يستثني الصفر = تتفوق على العشوائي فعلاً.
(2) الاختيار المتدرج: لكل سنة اختبار Y (بعد سنتين من التاريخ على الأقل) نختار من الإشارات المرشحة أفضلها مهارةً على السنوات السابقة لـY فقط (بشرط ≥200 صفقة ومهارة موجبة)، ثم نقيس مهارتها في Y.
    هذا محاكاة نزيهة لعملية «اخترنا الفائز من الماضي»: إن نفعت فاختياراتها تربح في Y؛ وإن كانت وهماً تعود نتيجتها إلى الصفر. المقارنة بمتوسط كل المرشحين في Y (مرجع «اختيار عشوائي»).
قواعد تحمي الحكم: هذه البيانات رُئيت كلها، فالنتائج **استكشاف**؛ الحكم الحقيقي على الظلال الحية منذ 26/10/2026 فقط (لا يُدمج أي مزيج قبل تسجيل فرضيته في CLAUDE.md).
تشغيل: python research/strategy_walkforward.py [مسار الشموع]   المخرج: research/strategy_walkforward_results.md
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
import option_buyer_cost as obc  # noqa: E402
import strategy_sim as sim  # noqa: E402

OUT = os.path.join(HERE, "strategy_walkforward_results.md")
CANDIDATES = ("تتبّع الفجوة ≥0.4%", "تتبّع أول 30 دقيقة", "زخم آخر ساعة", "انعكاس آخر ساعة", "اتفاق الزخم وأول 30")
MIN_TRAIN_TRADES = 200
MIN_TRAIN_YEARS = 2


# ============================ دوال نقية ============================
def skill_frame(F, sig, call, put, up, days):
    """DataFrame(day, skill) لصفقات الإشارة: عائدها − متوسط الكول والبوت على العينة نفسها."""
    s = np.asarray(sig(F), float)
    take = s != 0
    ret = np.where(s > 0, call, put)
    base = (call + put) / 2
    return pd.DataFrame({"day": pd.to_datetime(days)[take], "skill": (ret - base)[take]})


def boot_ci(df, n=1500, seed=19):
    g = df.groupby("day")["skill"].agg(["sum", "count"])
    if len(g) < 10:
        return None
    S, N = g["sum"].to_numpy(), g["count"].to_numpy()
    idx = np.random.default_rng(seed).integers(0, len(S), (n, len(S)))
    m = S[idx].sum(1) / N[idx].sum(1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def skill_stat(df):
    if df is None or len(df) == 0:
        return None
    return {"n": len(df), "mean": float(df["skill"].mean()), "ci": boot_ci(df)}


def walk_forward(frames, min_trades=MIN_TRAIN_TRADES, min_years=MIN_TRAIN_YEARS):
    """frames: {اسم: DataFrame(day, skill)}. يرجع (صفوف السنوات، مهارة الاختيارات المجمّعة DataFrame(day, skill)، مهارة متوسط المرشحين المجمّعة).
    كل صف: dict(year, chosen، train_skill، test_skill، test_n)."""
    years = sorted({d.year for f in frames.values() for d in f["day"]})
    rows, chosen_parts, avg_parts = [], [], []
    for y in years:
        if y - years[0] < min_years:
            continue
        best, best_skill = None, 0.0
        for name, f in frames.items():
            tr = f[f["day"].dt.year < y]
            if len(tr) >= min_trades and tr["skill"].mean() > best_skill:
                best, best_skill = name, float(tr["skill"].mean())
        avg = pd.concat([f[f["day"].dt.year == y] for f in frames.values()])
        if len(avg):
            avg_parts.append(avg)
        if best is None:
            rows.append({"year": y, "chosen": "لا مرشح موجب", "train_skill": None, "test_skill": None, "test_n": 0})
            continue
        te = frames[best][frames[best]["day"].dt.year == y]
        chosen_parts.append(te)
        rows.append({"year": y, "chosen": best, "train_skill": best_skill, "test_skill": float(te["skill"].mean()) if len(te) else None, "test_n": len(te)})
    cat = lambda parts: pd.concat(parts) if parts else pd.DataFrame({"day": pd.to_datetime([]), "skill": []})
    return rows, cat(chosen_parts), cat(avg_parts)


def fmt_stat(s):
    if s is None:
        return "- | - | -"
    ci = "-" if s["ci"] is None else f"{100 * s['ci'][0]:+.2f}…{100 * s['ci'][1]:+.2f}"
    return f"{s['n']} | {100 * s['mean']:+.2f}% | {ci}"


def verdict(s):
    if s is None or s["n"] < MIN_TRAIN_TRADES or s["ci"] is None:
        return "لا حكم (عينة قليلة)"
    if s["ci"][0] > 0:
        return "تتفوق على العشوائي (في هذه البيانات المرئية)"
    if s["ci"][1] < 0:
        return "أسوأ من العشوائي"
    return "لا تتفوق على العشوائي"


def report(per_dte, meta):
    L = ["# مهارة الإشارات واختيار الأفضل من الماضي (نتائج آلية، استكشافية)", "", f"- {meta}",
         "- المهارة = عائد الإشارة − متوسط الكول والبوت على العينة نفسها (ATM، كلفة حقيقية معايَرة، خروج بعد 60 دقيقة). موجبة بفاصل يستثني الصفر = تتفوق على رامي العملة.",
         "- ⚠️ كل هذه البيانات رُئيت سابقاً: الأرقام تطوير وتنقيح لا حكم. الحكم الرسمي على الظلال الحية منذ 26/10/2026.", ""]
    for dte, (frames, rows, chosen, avg) in per_dte.items():
        L += [f"## ATM {dte}DTE", "", "### المهارة المزدوجة لكل إشارة (كل التاريخ)", "", "| الإشارة | صفقات | متوسط المهارة | فاصل 95% (%) | الحكم |", "|---|---|---|---|---|"]
        for name, f in frames.items():
            s = skill_stat(f)
            L.append(f"| {name} | {fmt_stat(s)} | {verdict(s)} |")
        L += ["", "### اختيار الأفضل من الماضي سنة بسنة (متدرج)", "", "| السنة | المختار | مهارته في التدريب | مهارته في السنة | صفقات |", "|---|---|---|---|---|"]
        for r in rows:
            tr = "-" if r["train_skill"] is None else f"{100 * r['train_skill']:+.2f}%"
            te = "-" if r["test_skill"] is None else f"{100 * r['test_skill']:+.2f}%"
            L.append(f"| {r['year']} | {r['chosen']} | {tr} | {te} | {r['test_n']} |")
        sc, sa = skill_stat(chosen), skill_stat(avg)
        L += ["", "| ما نقيسه | صفقات | متوسط المهارة | فاصل 95% (%) | الحكم |", "|---|---|---|---|---|",
              f"| **الاختيار المتدرج** (أفضل الماضي) | {fmt_stat(sc)} | {verdict(sc)} |",
              f"| مرجع: متوسط كل المرشحين | {fmt_stat(sa)} | {verdict(sa)} |", ""]
    return L


def main(argv):
    path = argv[0] if argv else ls.DATA
    if not os.path.exists(path):
        print("لا ملف بيانات:", path)
        return 0
    d = ls.load(path)
    vix = obc.load_vix()
    F = sim.features(d)
    S = obc.samples(d).merge(F, on=["day", "i"], how="inner")
    S = S[np.isfinite(S["day"].map(vix).to_numpy(float))].reset_index(drop=True)
    targets = sim.load_targets()
    per = {}
    for dte in (0, 1):
        mult, _ = sim.calibrate_mult(S, vix, dte, targets[dte])
        call, put, up, days = sim.option_returns(S, vix, dte, mult)
        frames = {n: skill_frame(S, sim.SIGNALS[n], call, put, up, days) for n in CANDIDATES}
        rows, chosen, avg = walk_forward(frames)
        per[dte] = (frames, rows, chosen, avg)
    meta = f"{S['day'].nunique()} يوماً، {len(S)} عينة دخول"
    L = report(per, meta)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
