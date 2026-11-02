#!/usr/bin/env python3
"""دراسة مستويات كاماريلا اليومية على SPX (قراءة فقط؛ H-CAMA مسجّلة في CLAUDE.md قبل كتابة الكود؛ بطلب المستخدم 2/11/2026).

الفكرة: منصة «المراقب الذكي» (enginora smart) تبني قراراتها على مستويات كاماريلا اليومية. نختبر نسخة تقريبية من قواعد الدخول الشائعة
على شموع SPX 5 دقائق في `research/spx_5m_cache.csv` (قواعدهم الفعلية وفلاترهم غير منشورة، فهذه ليست استنساخاً لمنصتهم).
المستويات من الجلسة العادية السابقة (القمة H، القاع L، الإغلاق C): المدى R = H − L، PP = C،
  H3 = C + 1.1R/4،  H4 = C + 1.1R/2،  H5 = (H/L)×C،  L3 = C − 1.1R/4،  L4 = C − 1.1R/2،  L5 = C − (H5 − C).
قاعدتان، كل منهما تُحسب وحدها وبأول إشارة في اليوم فقط (قرار عند إغلاق شمعة 5د، دخول بإغلاقها، نافذة 9:30–15:00، بلا نظرة للمستقبل):
  fade (انعكاس): شمعة لمست H3 (أعلى ≥ H3) وأغلقت تحته وفوق PP → بيع؛ الوقف H4، الهدف PP. المرآة عند L3 (شراء، الوقف L4، الهدف PP).
  break (اختراق): أول إغلاق فوق H4 (والسابق ≤ H4) → شراء؛ الوقف H3، الهدف H5. المرآة تحت L4 (بيع، الوقف L3، الهدف L5).
الوقف قبل الهدف في الشمعة نفسها، وإلا الإغلاق عند 15:55. النتيجة بمضاعف المخاطرة R (على المؤشر نفسه، ليس على عقد خيارات) بعد كلفة 0.002% لكل جهة.
المرجع: لكل إشارة نفس الهندسة (المسافتان) باتجاه عشوائي = متوسط R للاتجاهين (تحت السير العشوائي يقارب −الكلفة).
الفرضية H-CAMA: متوسط R بعد الكلفة > 0 وحدّه الأدنى بفاصل 95% (إعادة معاينة الأيام) > 0 وفوق المرجع، بعد 100 صفقة على الأقل لكل قاعدة.
تنبيه: الأسعار نظرية على المؤشر وليست عقود خيارات؛ العينة (~74 يوماً) أصغر من أن تحسم؛ ولا يثبت هذا الاختبار ادعاء «86%» لأي جهة.
تشغيل: python research/camarilla_study.py   المخرج: research/camarilla_study_results.md
"""
import os
import sys
from datetime import date
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(HERE, "spx_5m_cache.csv")
OUT = os.path.join(HERE, "camarilla_study_results.md")
NY = ZoneInfo("America/New_York")
COST = 0.00002          # 0.002% لكل جهة
ENTRY_LAST = 66         # آخر شمعة يجوز الدخول عند إغلاقها (تبدأ 15:00)
MIN_BARS = 30           # يوم أقل من هذا (نصف يوم/ناقص) يُتجاهل
MIN_TRADES = 100
BOOT = 2000
STRATS = {"fade": "انعكاس عند H3/L3", "break": "اختراق H4/L4"}


# ============================ دوال نقية ============================
def levels(h, l, c):
    """مستويات كاماريلا من قمة/قاع/إغلاق الجلسة السابقة."""
    r = h - l
    h5 = (h / l) * c
    return {"PP": c, "H3": c + 1.1 * r / 4, "H4": c + 1.1 * r / 2, "H5": h5,
            "L3": c - 1.1 * r / 4, "L4": c - 1.1 * r / 2, "L5": c - (h5 - c)}


def load_days(path=CACHE):
    """{تاريخ: {t,o,h,l,c}} من المخبأ (توقيت نيويورك، الجلسة العادية)."""
    df = pd.read_csv(path)
    t = pd.to_datetime(df["time"], utc=True).dt.tz_convert(NY)
    df = df.assign(t=t, d=t.dt.date)
    days = {}
    for d, g in df.groupby("d"):
        g = g.sort_values("t")
        if len(g) < MIN_BARS:
            continue
        days[d] = {"t": list(g["t"]), "o": g["Open"].to_numpy(float), "h": g["High"].to_numpy(float),
                   "l": g["Low"].to_numpy(float), "c": g["Close"].to_numpy(float)}
    return days


def prev_levels(days, d):
    """مستويات اليوم d من أقرب جلسة سابقة في المخبأ، أو None."""
    prior = [x for x in days if x < d]
    if not prior:
        return None
    p = days[max(prior)]
    return levels(float(p["h"].max()), float(p["l"].min()), float(p["c"][-1]))


def find_signal(day, lv, strat):
    """أول إشارة في اليوم: (فهرس الشمعة، الجهة +1/-1، الدخول، الوقف، الهدف) أو None."""
    h, l, c = day["h"], day["l"], day["c"]
    n = min(len(c), ENTRY_LAST + 1)
    for i in range(n):
        if strat == "fade":
            if h[i] >= lv["H3"] and lv["PP"] < c[i] < lv["H3"]:
                return i, -1, float(c[i]), lv["H4"], lv["PP"]
            if l[i] <= lv["L3"] and lv["L3"] < c[i] < lv["PP"]:
                return i, 1, float(c[i]), lv["L4"], lv["PP"]
        else:
            prev = c[i - 1] if i else lv["PP"]
            if c[i] > lv["H4"] and prev <= lv["H4"] and c[i] < lv["H5"]:
                return i, 1, float(c[i]), lv["H3"], lv["H5"]
            if c[i] < lv["L4"] and prev >= lv["L4"] and c[i] > lv["L5"]:
                return i, -1, float(c[i]), lv["L3"], lv["L5"]
    return None


def simulate(h, l, c, i, side, entry, stop, target):
    """(R بعد الكلفة، نوع الخروج sl/tp/eod). الوقف قبل الهدف في الشمعة نفسها؛ شمعة الدخول لا تُحسب."""
    risk = (entry - stop) if side == 1 else (stop - entry)
    if risk <= 0:
        return None
    reward = abs(target - entry)
    cost = 2 * COST * entry / risk
    for t in range(i + 1, len(c)):
        if side == 1:
            if l[t] <= stop:
                return -1.0 - cost, "sl"
            if h[t] >= target:
                return reward / risk - cost, "tp"
        else:
            if h[t] >= stop:
                return -1.0 - cost, "sl"
            if l[t] <= target:
                return reward / risk - cost, "tp"
    return side * (float(c[-1]) - entry) / risk - cost, "eod"


def trade_for(day, lv, strat):
    """صفقة اليوم لقاعدة واحدة أو None: dict فيه R والمرجع (متوسط الاتجاهين بالهندسة نفسها)."""
    sig = find_signal(day, lv, strat)
    if sig is None:
        return None
    i, side, entry, stop, target = sig
    res = simulate(day["h"], day["l"], day["c"], i, side, entry, stop, target)
    if res is None:
        return None
    risk, reward = abs(entry - stop), abs(target - entry)
    # المرجع: نفس المسافتين، الاتجاه المعاكس (وقفه حيث هدفنا وهدفه حيث وقفنا)
    opp_stop = entry + side * reward
    opp_target = entry - side * risk
    base = simulate(day["h"], day["l"], day["c"], i, -side, entry, opp_stop, opp_target)
    r, kind = res
    rb = (r + base[0]) / 2 if base else r
    return {"idx": i, "time": day["t"][i], "side": side, "entry": entry, "stop": stop, "target": target,
            "r": r, "kind": kind, "base_r": rb}


def bootstrap_ci(x, n=BOOT, seed=0):
    x = np.asarray(x, float)
    if len(x) == 0:
        return None, None
    rng = np.random.default_rng(seed)
    m = rng.choice(x, size=(n, len(x)), replace=True).mean(axis=1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def summarize(trades):
    """ملخص قائمة صفقات (dict): العدد، نسبة الربح، متوسط R، فاصل 95%، المرجع، الحكم."""
    n = len(trades)
    if n == 0:
        return {"n": 0, "verdict": "لا صفقات"}
    r = [t["r"] for t in trades]
    base = [t["base_r"] for t in trades]
    lo, hi = bootstrap_ci(r)
    out = {"n": n, "win": float(np.mean([x > 0 for x in r])), "mean": float(np.mean(r)), "lo": lo, "hi": hi,
           "base": float(np.mean(base)), "tp": sum(t["kind"] == "tp" for t in trades),
           "sl": sum(t["kind"] == "sl" for t in trades), "eod": sum(t["kind"] == "eod" for t in trades)}
    if n < MIN_TRADES:
        out["verdict"] = f"عينة صغيرة ({n} < {MIN_TRADES}): لا حكم"
    elif lo > 0 and out["mean"] > out["base"]:
        out["verdict"] = "تحقق الشرط (يحتاج ظلاً أمامياً قبل أي اعتماد)"
    else:
        out["verdict"] = "لم يتحقق الشرط"
    return out


def run(days):
    """{قاعدة: [صفقات]} لكل يوم له مستويات من جلسة سابقة."""
    res = {s: [] for s in STRATS}
    for d in sorted(days):
        lv = prev_levels(days, d)
        if lv is None:
            continue
        for s in STRATS:
            t = trade_for(days[d], lv, s)
            if t:
                t["date"] = d
                res[s].append(t)
    return res


def report(res, n_days, today=None):
    L = ["# مستويات كاماريلا على SPX (نتائج آلية، قراءة فقط)", "",
         f"- الفترة: {n_days} يوماً من `spx_5m_cache.csv`. النتيجة بمضاعف المخاطرة R **على المؤشر** (ليست عقود خيارات)، بعد كلفة 0.002% لكل جهة.",
         "- المرجع: نفس مسافتي الوقف والهدف بالاتجاه المعاكس عند الشمعة نفسها. الفاصل 95% بإعادة معاينة الصفقات.",
         "- القواعد تقريبية ومن اختيارنا؛ قواعد المنصات التجارية غير منشورة، فلا يقيس هذا أي ادعاء لهم. الفرضية H-CAMA: متوسط R > 0 وحدّه الأدنى > 0 وفوق المرجع بعد 100 صفقة.",
         "", "| القاعدة | صفقات | ربح% | متوسط R | فاصل 95% | المرجع | هدف/وقف/إغلاق | الحكم |", "|---|---|---|---|---|---|---|---|"]
    for s, name in STRATS.items():
        m = summarize(res[s])
        if m["n"] == 0:
            L.append(f"| {name} | 0 | - | - | - | - | - | {m['verdict']} |")
            continue
        L.append(f"| {name} | {m['n']} | {m['win']:.0%} | {m['mean']:+.2f} | [{m['lo']:+.2f}, {m['hi']:+.2f}] | "
                 f"{m['base']:+.2f} | {m['tp']}/{m['sl']}/{m['eod']} | {m['verdict']} |")
    L += ["", "⚠️ أي تحسّن هنا يبقى «اكتشافاً بعد النظر في البيانات»: يلزم ظل أمامي (`camarilla_shadow`) قبل أي اعتماد.", ""]
    return L


def main():
    days = load_days()
    res = run(days)
    lines = report(res, len(days))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
