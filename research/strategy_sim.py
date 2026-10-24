#!/usr/bin/env python3
"""محاكي الاستراتيجيات الموحّد (قراءة فقط، بطلب المستخدم 24/10/2026: «ندمج ونحاكي»): إشارات قابلة للتبديل × عقد ATM بالكلفة الحقيقية المعايَرة × تقييم صادق.

الفكرة: كل استراتيجية = دالة إشارة تأخذ مزايا لحظة الدخول (لا تنظر للمستقبل) وتعيد +1 شراء كول و−1 شراء بوت و0 لا تدخل. المحاكي يسعّر العقد ويقيّم، وهي نفسها لكل إشارة فيكون الحكم عادلاً:
  - التسعير: بلاك-شولز بزمن التداول على SPY (ATM، خروج بعد 60 دقيقة، دخول 10:30/11:30/12:30/13:30)، مع **معامل تذبذب مُعايَر** بحيث يطابق متوسط الشراء العشوائي النظري متوسطه الحقيقي المقيس
    (long_price_calibration: 0DTE −3.6%، 1DTE −3.0% على 660 يوماً). فنحصل على كلفة واقعية بلا الحاجة لأسعار عقود تاريخية لكل إشارة.
  - الضابط: إشارة عشوائية (يجب أن تعطي متوسطاً قرب كلفة الشراء العشوائية) وإشارة «شراء كول دائماً».
  - النصف الأول من التاريخ (حتى 2022-11-23) استكشاف، والثاني **حكم**؛ فاصل 95% بإعادة معاينة الأيام؛ ثم محاكاة تآكل بحجم 5% من الرصيد بإصابة وW وL المحققة.
لا مزيج مفضّل هنا: إضافة إشارة جديدة أو مزيج تتم بتسجيل فرضيتها قبل التشغيل في CLAUDE.md، وإلا فهي استكشاف يخدع (كل مزيج جديد مقارنة جديدة).
تشغيل: python research/strategy_sim.py [مسار الشموع]   المخرج: research/strategy_sim_results.md
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import account_erosion_sim as ae  # noqa: E402
import levels_study as ls  # noqa: E402
import option_buyer_cost as obc  # noqa: E402

OUT = os.path.join(HERE, "strategy_sim_results.md")
WL = os.path.join(HERE, "long_price_calibration_wl.json")
SPLIT = "2022-11-23"
HALF_SPREAD = 0.015
TARGET_DEFAULT = {0: -0.036, 1: -0.030}            # متوسط الشراء العشوائي الحقيقي (long_price_calibration 24/10/2026)
SIZE = 0.05
MIN_TRADES = 100


# ============================ المزايا والإشارات ============================
def features(d):
    """DataFrame لكل (يوم، دخول): gap وr_first (9:30→10:00) وr_prev60 (آخر ساعة قبل الدخول) وبقية ما يحتاجه obc.samples. لا مزايا من بعد لحظة الدخول."""
    rows, prev_close = [], None
    for day, g in d.groupby(d["t"].dt.date):
        g, half = ls.trim_half_day(g)
        if len(g) < 30:
            continue
        o, c = g["o"].to_numpy(float), g["c"].to_numpy(float)
        if not half and len(g) >= 70 and prev_close:
            for i in obc.ENTRIES:
                if i + 12 < len(c):
                    base = c[i - 12] if i >= 12 else o[0]
                    rows.append({"day": pd.Timestamp(day), "i": i, "gap": float(o[0] / prev_close - 1), "r_first": float(c[5] / o[0] - 1),
                                 "r_prev60": float(c[i] / base - 1)})
        prev_close = float(c[-1])
    return pd.DataFrame(rows)


def sig_always_call(f):
    return np.ones(len(f))


def make_random(seed=1):
    def sig(f):
        return np.random.default_rng(seed).choice([-1.0, 1.0], len(f))
    return sig


def sig_gap_follow(f, thr=0.004):
    """يتبع اتجاه الفجوة الكبيرة (≥ thr)، وإلا لا دخول."""
    return np.where(f["gap"].abs() >= thr, np.sign(f["gap"]), 0.0)


def sig_first30_follow(f):
    return np.sign(f["r_first"]).to_numpy()


def sig_momentum_1h(f):
    return np.sign(f["r_prev60"]).to_numpy()


def sig_reversal_1h(f):
    return -np.sign(f["r_prev60"]).to_numpy()


def sig_agree_mf(f):
    """دخول فقط حين يتفق اتجاه آخر ساعة مع اتجاه أول 30 دقيقة (مرشح الدمج المسجّل H-AGREE)."""
    m, a = np.sign(f["r_prev60"]).to_numpy(), np.sign(f["r_first"]).to_numpy()
    return np.where(m == a, m, 0.0)


SIGNALS = {"عشوائي (ضابط)": make_random(), "كول دائماً (ضابط)": sig_always_call, "تتبّع الفجوة ≥0.4%": sig_gap_follow,
           "تتبّع أول 30 دقيقة": sig_first30_follow, "زخم آخر ساعة": sig_momentum_1h, "انعكاس آخر ساعة": sig_reversal_1h,
           "اتفاق الزخم وأول 30": sig_agree_mf}


# ============================ التسعير والتقييم ============================
def option_returns(S, vix, dte, mult):
    """(عائد الكول، عائد البوت، صعد؟، أيام) لعينات S بتذبذب VIX × mult، ATM، خروج 60 د."""
    res, up, days = obc.returns(S, vix, mult, 0.0, HALF_SPREAD, dte, 12)
    if len(days) != len(S):
        raise ValueError("عينات غير صالحة: رشّح S بتذبذب منتهٍ قبل التسعير")
    return res["call"], res["put"], up, days


def calibrate_mult(S, vix, dte, target, lo=0.3, hi=1.3):
    """معامل التذبذب الذي يطابق متوسط الشراء العشوائي النظري target (دالة متزايدة بالتذبذب المحقق/الضمني: نقسم بالتنصيف). يرجع (معامل، متوسط محقق)."""
    def random_mean(m):
        c, p, _, _ = option_returns(S, vix, dte, m)
        return float(((c + p) / 2).mean())
    a, b = random_mean(lo), random_mean(hi)
    if not (a <= target <= b or b <= target <= a):
        m = lo if abs(a - target) < abs(b - target) else hi
        return m, random_mean(m)
    for _ in range(30):
        mid = (lo + hi) / 2
        if (random_mean(mid) - target) * (a - target) > 0:
            lo = mid
        else:
            hi = mid
    m = (lo + hi) / 2
    return m, random_mean(m)


def summarize_trades(day, ret, right):
    ret, right = np.asarray(ret, float), np.asarray(right, bool)
    if len(ret) == 0:
        return None
    df = pd.DataFrame({"day": day, "r": ret}).groupby("day")["r"].agg(["sum", "count"])
    ci = None
    if len(df) >= 10:
        S_, N_ = df["sum"].to_numpy(), df["count"].to_numpy()
        idx = np.random.default_rng(11).integers(0, len(S_), (1500, len(S_)))
        m = S_[idx].sum(1) / N_[idx].sum(1)
        ci = (float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5)))
    W = float(ret[right].mean()) if right.any() else float("nan")
    L = float(ret[~right].mean()) if (~right).any() else float("nan")
    be = ae.breakeven(W, L) if np.isfinite(W) and np.isfinite(L) and W != L else None
    return {"n": len(ret), "days": int(df.shape[0]), "mean": float(ret.mean()), "ci": ci, "hit": float(right.mean()), "W": W, "L": L, "be": be}


def erosion(s):
    if s is None or not np.isfinite(s["W"]) or not np.isfinite(s["L"]):
        return None
    r = ae.simulate(s["W"], s["L"], s["hit"], SIZE, paths=4000)
    return ae.summarize(r)


def run_signal(F, sig, call, put, up, days):
    """يقيّم إشارة على كل العينات (F بالترتيب نفسه): dict(first, second, all) من summarize_trades."""
    s = np.asarray(sig(F), float)
    take = s != 0
    ret = np.where(s > 0, call, put)
    right = np.where(s > 0, up, ~up)
    day = pd.to_datetime(days)
    out = {}
    for label, m in (("all", take), ("first", take & (day < SPLIT)), ("second", take & (day >= SPLIT))):
        out[label] = summarize_trades(day[m], ret[m], right[m])
    return out


def load_targets(path=WL):
    try:
        j = json.load(open(path, encoding="utf-8"))
        return {0: float(j["ATM 0DTE"]["random"]), 1: float(j["ATM 1DTE"]["random"])}
    except Exception:  # noqa: BLE001
        return dict(TARGET_DEFAULT)


# ============================ التقرير ============================
def fmt(s):
    if s is None or s["n"] < 1:
        return "- | - | - | - | - | -"
    ci = "-" if s["ci"] is None else f"{100 * s['ci'][0]:+.1f}…{100 * s['ci'][1]:+.1f}"
    be = "-" if s["be"] is None else f"{100 * s['be']:.0f}%"
    return f"{s['n']} | {100 * s['hit']:.0f}% / {be} | {100 * s['mean']:+.1f}% | {ci} | {100 * s['W']:+.0f}% | {100 * s['L']:+.0f}%"


def verdict(s):
    """حكم وصفي على النصف الثاني: هل متوسط العائد بعد الكلفة أكبر من الصفر بفاصل يستثني الصفر؟"""
    if s is None or s["n"] < MIN_TRADES:
        return "لا حكم بعد (عينة قليلة)"
    if s["ci"] is None:
        return "لا حكم بعد"
    if s["ci"][0] > 0:
        return "رابحة بعد الكلفة"
    if s["ci"][1] < 0:
        return "خاسرة بعد الكلفة"
    return "غير مميّزة عن الصفر"


def report(results, mults, meta):
    L = ["# محاكي الاستراتيجيات الموحّد (نتائج آلية)", "", f"- {meta}",
         "- قراءة فقط. ATM بتسعير بلاك-شولز بمعامل تذبذب مُعايَر ليطابق كلفة الشراء العشوائي الحقيقية؛ دخول 10:30–13:30؛ خروج بعد 60 دقيقة؛ نصف فرق 1.5% للدخول والخروج.",
         "- «إصابة/تعادل»: نسبة صحة الاتجاه / النسبة اللازمة للتعادل. الحكم من النصف الثاني (بعد 2022-11-23)؛ الأول استكشاف. كل إشارة جديدة مقارنة جديدة: لا تُعتمد قبل تسجيلها مسبقاً واختبارها على أيام جديدة.", ""]
    for dte in (0, 1):
        L += [f"## ATM {dte}DTE — معامل التذبذب المُعايَر {mults[dte]:.2f} (كلفة عشوائية مستهدفة {100 * load_targets()[dte]:+.1f}%)", "",
              "| الاستراتيجية | النصف | صفقات | إصابة / تعادل | متوسط/صفقة | فاصل 95% (%) | W | L |", "|---|---|---|---|---|---|---|---|"]
        for name, r in results[dte].items():
            for half, label in (("first", "الأول"), ("second", "**الثاني**")):
                L.append(f"| {name} | {label} | {fmt(r[half])} |")
        L += [""]
        L += ["| الاستراتيجية | حكم النصف الثاني | احتمال هبوط ≥50% (حجم 5%، 250 صفقة) | وسيط الرصيد |", "|---|---|---|---|"]
        for name, r in results[dte].items():
            e = erosion(r["second"])
            tail = "- | -" if e is None else f"{100 * e['dd_half']:.0f}% | {e['median']:.2f}"
            L.append(f"| {name} | {verdict(r['second'])} | {tail} |")
        L.append("")
    return L


def main(argv):
    path = argv[0] if argv else ls.DATA
    if not os.path.exists(path):
        print("لا ملف بيانات:", path)
        return 0
    d = ls.load(path)
    vix = obc.load_vix()
    F = features(d)
    S = obc.samples(d)
    S = S.merge(F, on=["day", "i"], how="inner")
    S = S[np.isfinite(S["day"].map(vix).to_numpy(float))].reset_index(drop=True)
    targets = load_targets()
    results, mults = {}, {}
    for dte in (0, 1):
        mults[dte], _ = calibrate_mult(S, vix, dte, targets[dte])
        call, put, up, days = option_returns(S, vix, dte, mults[dte])
        results[dte] = {name: run_signal(S, sig, call, put, up, days) for name, sig in SIGNALS.items()}
    meta = f"{S['day'].nunique()} يوماً، {len(S)} عينة دخول"
    L = report(results, mults, meta)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
