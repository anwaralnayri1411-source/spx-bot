#!/usr/bin/env python3
"""«ثمن أن تكون مشتري خيارات» على SPY (قراءة فقط، وصفية بلا فرضية): كم يخسر المشتري بلا ميزة، وما نسبة إصابة الاتجاه اللازمة للتعادل؟

لمسار SPY الحقيقي (شموع 5 دقائق لسنوات، `research/spy_history.py`) نشتري عقداً ATM أو OTM (0.5% / 1%) ينتهي اليوم (0DTE) أو غداً (1DTE) عند 10:30 و11:30 و12:30 و13:30،
ونعيد تسعيره بعد 60 دقيقة أو عند الإغلاق بالتذبذب نفسه. التسعير بلاك-شولز بـ**زمن التداول** (T = دقائق الجلسة ÷ (252×390))، لا زمن التقويم: زمن التقويم يقلّل T لعقود اليوم ويُظهر الخيارات رخيصة فيخدع. التذبذب = VIX إغلاق أمس × معامل (1.0 الأساس).
الدخول بسعر الطلب (الوسط + نصف الفرق h) والخروج بسعر البيع (الوسط − h): h=1.5% للـATM و3% للـOTM (قيس 1.5% على SPY في Alpaca).
لكل عينة نحسب عائد الكول والبوت. «صحيح» = اتجاه SPY حتى الخروج يوافق جهة العقد.
  - متوسط عشوائي = متوسط الكول والبوت = كلفة الشراء بلا ميزة (فرق التذبذب الضمني عن المحقق + التآكل + فرق السعر).
  - نسبة التعادل p* = −L / (W − L) حيث W متوسط العائد حين تصيب الاتجاه وL حين تخطئ (L سالب). فوق p* ربح، وتحتها خسارة.
تحفظات: التذبذب الضمني الفعلي لعقود 0DTE يختلف عن VIX (30 يوماً)؛ نعرض حساسية ×0.8 و×1.2. ثابت التذبذب عند الخروج (لا تغيّر ضمني)، فتُهمل آثار الانحراف والسكيو.
هذه الأرقام نظرية الأسعار: تعطي الحجم والاتجاه لا الدولار الدقيق (راجع real_price_check للأسعار الحقيقية على 68 يوماً).
تشغيل: python research/option_buyer_cost.py [مسار الشموع]   المخرج: research/option_buyer_cost_results.md
"""
import math
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import levels_study as ls  # noqa: E402  (تحميل الشموع وتقليم أيام الإغلاق المبكر)

OUT = os.path.join(HERE, "option_buyer_cost_results.md")
ENTRIES = (11, 23, 35, 47)                      # إغلاق شمعة 10:30 و11:30 و12:30 و13:30
HOLDS = {"60 دقيقة": 12, "حتى الإغلاق": None}
STRIKES = {"ATM": (0.0, 0.015), "OTM 0.5%": (0.005, 0.03), "OTM 1%": (0.01, 0.03)}   # (إزاحة، نصف الفرق h)
DTES = {"0DTE": 0, "1DTE": 1}
IV_MULTS = (0.8, 1.0, 1.2)
MIN_PRICE = 0.02
SESSION_MIN = 390
TRADING_MIN_YEAR = 252 * SESSION_MIN      # زمن التداول: السنة 252 يوماً × 390 دقيقة


def ncdf(x):
    return 0.5 * (1.0 + np.vectorize(math.erf)(np.asarray(x, dtype=float) / math.sqrt(2.0)))


def bs(S, K, T, sig, is_call):
    """سعر بلاك-شولز (بلا فوائد وتوزيعات) متّجه."""
    S, K, T, sig = (np.asarray(x, dtype=float) for x in (S, K, T, sig))
    T = np.maximum(T, 1e-9)
    d1 = (np.log(S / K) + 0.5 * sig * sig * T) / (sig * np.sqrt(T))
    d2 = d1 - sig * np.sqrt(T)
    call = S * ncdf(d1) - K * ncdf(d2)
    return call if is_call else call - S + K


def breakeven(w, lw):
    """نسبة الإصابة اللازمة للتعادل من متوسط عائد الإصابة w ومتوسط عائد الخطأ lw (سالب). None إن كان الربح مستحيلاً أو مضموناً."""
    if w <= lw:
        return None
    return float(-lw / (w - lw))


def samples(d, entries=ENTRIES):
    """من DataFrame الشموع (تُقلَّم الأيام المبكرة): قوائم (يوم، فهرس الدخول، سعر الدخول، سعر بعد 60د، سعر الإغلاق، دقائق حتى 16:00)."""
    out = []
    for day, g in d.groupby(d["t"].dt.date):
        g, half = ls.trim_half_day(g)
        if half or len(g) < 70:
            continue
        c = g["c"].to_numpy(float)
        for i in entries:
            if i + 12 < len(c):
                out.append((pd.Timestamp(day), i, c[i], c[i + 12], c[-1], (16 * 60) - (9 * 60 + 35 + 5 * i), (len(c) - 1 - i) * 5))
    return pd.DataFrame(out, columns=["day", "i", "s0", "s60", "sclose", "mins_to_close", "mins_to_last"])


def returns(S, vix, mult, strike_off, h, dte, hold):
    """عائد الكول والبوت لكل عينة. S: DataFrame العينات. vix: Series بفهرس اليوم (إغلاق أمس بالنسبة المئوية)."""
    sig = (S["day"].map(vix).to_numpy(float) / 100.0) * mult
    s0 = S["s0"].to_numpy(float)
    T0 = (S["mins_to_close"].to_numpy(float) + dte * SESSION_MIN) / TRADING_MIN_YEAR
    mins_held = 60.0 if hold == 12 else S["mins_to_last"].to_numpy(float)
    T1 = np.maximum(T0 - mins_held / TRADING_MIN_YEAR, 1e-7)
    s1 = S["s60"].to_numpy(float) if hold == 12 else S["sclose"].to_numpy(float)
    res = {}
    for name, is_call in (("call", True), ("put", False)):
        k = np.round(s0 * (1 + strike_off if is_call else 1 - strike_off))
        p0 = np.maximum(bs(s0, k, T0, sig, is_call), MIN_PRICE)
        p1 = np.maximum(bs(s1, k, T1, sig, is_call), 0.0)
        res[name] = (p1 * (1 - h)) / (p0 * (1 + h)) - 1.0
    ok = np.isfinite(res["call"]) & np.isfinite(res["put"]) & np.isfinite(sig)
    up = (s1 > s0)
    return {k: v[ok] for k, v in res.items()}, up[ok], S["day"].to_numpy()[ok]


def summarize(res, up, days):
    """متوسط عشوائي وW وL ونسبة التعادل ونسبة الإصابة الفعلية للاتجاه 50/50، وفاصل 95% لمتوسط العشوائي بإعادة معاينة الأيام."""
    rnd = (res["call"] + res["put"]) / 2
    right = np.where(up, res["call"], res["put"])
    wrong = np.where(up, res["put"], res["call"])
    W, Lw = float(right.mean()), float(wrong.mean())
    df = pd.DataFrame({"day": days, "r": rnd}).groupby("day")["r"].agg(["sum", "count"])
    S, N = df["sum"].to_numpy(), df["count"].to_numpy()
    idx = np.random.default_rng(5).integers(0, len(S), (1000, len(S)))
    m = S[idx].sum(1) / N[idx].sum(1)
    return {"n": len(rnd), "random": float(rnd.mean()), "ci": (float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))),
            "W": W, "L": Lw, "be": breakeven(W, Lw), "up_share": float(up.mean())}


def calibration_lines(theory, path=None):
    """معايرة النظري بالحقيقي: أسعار Alpaca الحقيقية في research/real_price_check_trades.csv (دخول بسعر الطلب، خروج بسعر البيع، خروج بعد 60 دقيقة).
    theory: {اسم: متوسط عشوائي نظري عند VIX×1.0}. يرجع أسطراً (فارغة إن لم يوجد الملف)."""
    path = path or os.path.join(HERE, "real_price_check_trades.csv")
    if not os.path.exists(path):
        return []
    t = pd.read_csv(path)
    t = t[t["exit"] == "t60"]
    out = []
    for kind, label, key in (("atm1", "ATM لليوم التالي (1DTE)", "ATM 1DTE"), ("otm0", "عقد البوت الرخيص (0DTE)", "OTM 1% 0DTE")):
        g = t[t["kind"] == kind]
        if len(g) >= 50 and key in theory:
            out.append(f"| {label} | {len(g)} صفقة / {g['day'].nunique()} يوماً | {100 * g['real'].mean():+.1f}% | {100 * theory[key]:+.1f}% |")
    if not out:
        return []
    return ["## معايرة: الحقيقي مقابل النظري (خروج بعد 60 دقيقة)", "",
            "| العقد | العينة الحقيقية | متوسط حقيقي (الاتجاهان معاً) | نظري هنا (VIX × 1.0) |", "|---|---|---|---|"] + out + [
            "", "- القراءة: النظري يبالغ في الخسارة لأن VIX يحمل تذبذب الليل وتذبذب 0DTE الفعلي أقل منه؛ فنسب التعادل أعلاه **حدّ أعلى** والحقيقية أقل (مثال: ATM 1DTE بخسارة عشوائية −3% بدل −6% يعني تعادلاً قرب 60% لا 72%).", ""]


def by_entry_lines(S, vix, mult=1.0):
    """استكشافي (H-ENTRYTIME، بلا حكم): كلفة المشتري العشوائي ونسبة التعادل بحسب وقت الدخول. خروج بعد 60 دقيقة، تذبذب VIX × mult."""
    L = ["## الكلفة بحسب وقت الدخول (H-ENTRYTIME، خروج بعد 60 دقيقة، استكشافي بلا حكم)", "",
         "| العقد | المدة | الدخول | عينات | عشوائي: متوسط العائد | فاصل 95% | نسبة التعادل |", "|---|---|---|---|---|---|---|"]
    for sname in ("ATM", "OTM 1%"):
        off, h = STRIKES[sname]
        for dname, dte in DTES.items():
            for i in sorted(S["i"].unique()):
                sub = S[S["i"] == i]
                res, up, days = returns(sub, vix, mult, off, h, dte, 12)
                if len(days) < 100:
                    continue
                s = summarize(res, up, days)
                clock = f"{(9 * 60 + 35 + 5 * int(i)) // 60}:{(9 * 60 + 35 + 5 * int(i)) % 60:02d}"
                be = "-" if s["be"] is None else f"{100 * s['be']:.1f}%"
                L.append(f"| {sname} | {dname} | {clock} | {s['n']} | {100 * s['random']:+.1f}% | {100 * s['ci'][0]:+.1f}…{100 * s['ci'][1]:+.1f} | {be} |")
    return L + [""]


def report(S, vix, meta):
    L = ["# ثمن أن تكون مشتري خيارات على SPY (نتائج آلية، نظرية الأسعار)", "",
         f"- {meta}. عينات الدخول: {len(S)} (4 أوقات يومياً). التسعير بلاك-شولز بتذبذب VIX إغلاق أمس، الدخول بالطلب والخروج بالبيع.",
         "- **العمود الأهم: نسبة التعادل** = نسبة إصابة الاتجاه اللازمة لتعادل الشراء. بلا ميزة (إصابة 50%) يكون المتوسط العشوائي هو ما تخسره في كل صفقة.",
         "- ⚠️ تذبذب VIX لا يساوي التذبذب الضمني لـ0DTE، وثبات التذبذب عند الخروج تبسيط؛ تُعرض حساسية ×0.8 و×1.2. لا حكم دولاري، بل الحجم والاتجاه.", ""]
    theory = {}
    for mult in IV_MULTS:
        L += [f"## تذبذب = VIX × {mult}", "", "| العقد | المدة | الاحتفاظ | عشوائي: متوسط العائد | فاصل 95% | متوسط عند الإصابة | عند الخطأ | نسبة التعادل |", "|---|---|---|---|---|---|---|---|"]
        for sname, (off, h) in STRIKES.items():
            for dname, dte in DTES.items():
                for hname, hold in HOLDS.items():
                    res, up, days = returns(S, vix, mult, off, h, dte, 12 if hold else None)
                    if len(days) < 100:
                        continue
                    s = summarize(res, up, days)
                    if mult == 1.0 and hname == "60 دقيقة":
                        theory[f"{sname} {dname}"] = s["random"]
                    be = "-" if s["be"] is None else f"{100 * s['be']:.1f}%"
                    L.append(f"| {sname} | {dname} | {hname} | {100 * s['random']:+.1f}% | {100 * s['ci'][0]:+.1f}…{100 * s['ci'][1]:+.1f} | {100 * s['W']:+.0f}% | {100 * s['L']:+.0f}% | {be} |")
        L.append("")
    return L + by_entry_lines(S, vix) + calibration_lines(theory)


def load_vix():
    import yfinance as yf
    v = yf.download("^VIX", period="15y", interval="1d", auto_adjust=False, progress=False)
    c = v["Close"].iloc[:, 0] if isinstance(v.columns, pd.MultiIndex) else v["Close"]
    c = c.dropna()
    c.index = pd.to_datetime(c.index).tz_localize(None)
    return c.shift(1)                      # إغلاق أمس لتجنب النظرة اللاحقة


def main(argv):
    path = argv[0] if argv else ls.DATA
    if not os.path.exists(path):
        print("لا ملف بيانات:", path)
        return 0
    d = ls.load(path)
    S = samples(d)
    vix = load_vix()
    S = S[S["day"].isin(vix.dropna().index)]
    meta = f"البيانات: {S['day'].nunique()} يوماً من {S['day'].min():%Y-%m-%d} إلى {S['day'].max():%Y-%m-%d}"
    L = report(S, vix, meta)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
