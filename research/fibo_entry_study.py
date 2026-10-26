#!/usr/bin/env python3
"""اختبار استراتيجية «FIBO ENTRY» (BOS + فيبوناتشي 0.79) على شموع 15 دقيقة (قراءة فقط؛ H-FIBO مسجّلة في CLAUDE.md قبل كتابة الكود؛ بطلب المستخدم 26/10/2026).

المصدر: فيديو josmy403 / Structure Art (الذهب 15 دقيقة)، قُرئ من لقطاته. الخطوات: كسر آخر قمة (BOS)، فيبوناتشي من القاع إلى القمة، انتظار الرجوع العميق إلى 0.79، شراء محدد عند 0.79، الوقف تحت القاع، الهدف القمة (عائد/مخاطرة نحو 3).
النسخة الرقمية (بلا نظرة للمستقبل): شموع 15 دقيقة من شموع 5 داخل الجلسة العادية؛ قمة/قاع محوري = أعلى/أدنى بدقة من K=2 شمعة على كل جانب ولا يُعرف قبل شمعتين لاحقتين.
إعداد صاعد: قاع L، قمة سابقة H0 قبله، BOS = أول إغلاق فوق H0 بعد L (دون إغلاق تحت L قبله)، ثم أول قمة محورية H بعد BOS؛ الساق H−L بين 0.4% و3% من L.
عند تأكيد H: أمر شراء محدد عند E = H − 0.79×الساق (إن كان إغلاق شمعة التأكيد فوق E)، يُلغى إذا تجاوز السعر H أو أغلق تحت L قبل التنفيذ أو بعد الشمعة التي تبدأ 15:15؛ التنفيذ إذا لامس أدنى الشمعة E.
الوقف L − 5%×الساق، الهدف H، وإلا الإغلاق؛ الوقف قبل الهدف في الشمعة نفسها؛ كلفة 0.002% لكل جهة؛ الهبوط مرآة؛ أول تنفيذ في اليوم لكل سلسلة.
«خط الاتجاه/السيولة» (الخطوة 2 في الفيديو) غير منفَّذ عمداً (ذاتي). تحت السير العشوائي متوسط R صفر قبل الكلفة (مارتنجيل)، فلا يلزم مستوى وهمي.
الفرضية: متوسط R بعد الكلفة > 0 بفاصل 95% بإعادة معاينة الأيام، بعد 300 صفقة، وفي كل من مجموعتي (صناديق مع SPY، شركات).
تشغيل: python research/fibo_entry_study.py   المخرج: research/fibo_entry_study_results.md
"""
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import levels_study as ls  # noqa: E402
import replication_study as rp  # noqa: E402

OUT = os.path.join(HERE, "fibo_entry_study_results.md")
K = 2
LEG_MIN, LEG_MAX = 0.004, 0.03
FIB = 0.79
STOP_BUF = 0.05
ENTRY_LAST = 23            # آخر شمعة 15 دقيقة يجوز أن تنفَّذ فيها (تبدأ 15:15)
COST = 0.00002
MIN_TRADES = 300
ZONE_TOP, ZONE_BOT = 0.618, 0.706     # «المنطقة الذهبية» (H-FIBOZ)
MODES = {"fib79": "H-FIBO", "zone": "H-FIBOZ"}


# ============================ دوال نقية ============================
def agg15(o, h, l, c):
    """من 78 شمعة 5 دقائق إلى 26 شمعة 15 دقيقة."""
    n = len(c) // 3
    o3, h3, l3, c3 = (np.asarray(x[: 3 * n], float).reshape(n, 3) for x in (o, h, l, c))
    return o3[:, 0], h3.max(1), l3.min(1), c3[:, 2]


def pivot_highs(h, k=K):
    return [i for i in range(k, len(h) - k) if all(h[i] > h[j] for j in range(i - k, i + k + 1) if j != i)]


def pivot_lows(l, k=K):
    return [i for i in range(k, len(l) - k) if all(l[i] < l[j] for j in range(i - k, i + k + 1) if j != i)]


def simulate(h, l, c, fill_t, entry, stop, target):
    """(R بعد الكلفة، نوع الخروج). الوقف قبل الهدف في الشمعة نفسها؛ شمعة التنفيذ لا يُحسب هدفها."""
    risk = entry - stop
    cost = 2 * COST * entry / risk
    if l[fill_t] <= stop:
        return -1.0 - cost, "sl"
    for t in range(fill_t + 1, len(c)):
        if l[t] <= stop:
            return -1.0 - cost, "sl"
        if h[t] >= target:
            return (target - entry) / risk - cost, "tp"
    return (float(c[-1]) - entry) / risk - cost, "eod"


def zone_entry(h, l, a, hi, H, leg):
    """H-FIBOZ: أعلى تقاطع بين أول فجوة FVG صاعدة داخل الساق (بين L عند a وH عند hi) والمنطقة الذهبية 0.618–0.706، أو None."""
    z_top, z_bot = H - ZONE_TOP * leg, H - ZONE_BOT * leg
    for j in range(a + 2, hi + 1):
        g_bot, g_top = h[j - 2], l[j]
        if g_top > g_bot and g_top >= z_bot and g_bot <= z_top:
            return float(min(g_top, z_top))
    return None


def bull_trade(o, h, l, c, mode="fib79"):
    """أول تنفيذ في إعداد صاعد: (فهرس شمعة التنفيذ، R، نوع الخروج) أو None. mode: fib79 (H-FIBO) أو zone (H-FIBOZ)."""
    n = len(c)
    ph, pl = pivot_highs(h), pivot_lows(l)
    best = None
    for a in pl:
        prior = [p for p in ph if p < a]
        if not prior:
            continue
        H0, L = h[prior[-1]], l[a]
        b = None
        for t in range(a + 1, n):
            if c[t] < L:
                break
            if c[t] > H0:
                b = t
                break
        if b is None:
            continue
        after = [p for p in ph if p > b]
        if not after:
            continue
        hi = after[0]
        H = h[hi]
        leg = H - L
        if not (LEG_MIN <= leg / abs(L) <= LEG_MAX):          # abs: جهة البيع تعمل بأسعار مَنفيّة
            continue
        t0 = hi + K
        if t0 >= n:
            continue
        S = L - STOP_BUF * leg
        if mode == "fib79":
            E = H - FIB * leg
        else:
            E = zone_entry(h, l, a, hi, H, leg)
            if E is None:
                continue
        if c[t0] <= E:
            continue
        for t in range(t0 + 1, min(ENTRY_LAST + 1, n)):
            if l[t] <= E:
                if o[t] <= S:
                    break
                entry = min(float(o[t]), E)
                r, kind = simulate(h, l, c, t, entry, S, H)
                if best is None or t < best[0]:
                    best = (t, r, kind)
                break
            if h[t] > H or c[t] < L:
                break
    return best


def day_trade(o, h, l, c, mode="fib79"):
    """أول تنفيذ (صاعد أو هابط بالمرآة) في اليوم: (R، نوع الخروج، اتجاه) أو None. المدخلات شموع 15 دقيقة."""
    up = bull_trade(o, h, l, c, mode)
    dn = bull_trade(-o, -l, -h, -c, mode)
    cands = [(t, r, k, d) for (res, d) in ((up, 1), (dn, -1)) if res for (t, r, k) in [res]]
    if not cands:
        return None
    t, r, kind, d = min(cands)
    return r, kind, d


def run_series(d, mode="fib79"):
    """DataFrame(day, R, kind, dir) لسلسلة واحدة (شموع 5 دقائق بتنسيق levels_study.load)."""
    rows = []
    for day, g in d.groupby(d["t"].dt.date):
        g, half = ls.trim_half_day(g)
        if half or len(g) < 78:
            continue
        o, h, l, c = agg15(*(g[k].to_numpy(float) for k in ("o", "h", "l", "c")))
        res = day_trade(o, h, l, c, mode)
        if res:
            rows.append({"day": pd.Timestamp(day), "R": res[0], "kind": res[1], "dir": res[2]})
    return pd.DataFrame(rows, columns=["day", "R", "kind", "dir"])


def group_stats(frames, days):
    """متوسط R وفاصله ونسبة بلوغ الهدف لقائمة إطارات: dict أو None."""
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    ci = rp.day_ci(df.rename(columns={"R": "val"}), days)
    if ci is None:
        return None
    n, mean, lv = ci
    return {"n": n, "mean": mean, "ci": lv, "tp": float((df["kind"] == "tp").mean()), "sl": float((df["kind"] == "sl").mean())}


def verdict(by_group, pooled, name="H-FIBO"):
    if pooled is None or any(v is None for v in by_group.values()):
        return f"{name}: لا حكم (بيانات ناقصة)"
    if pooled["n"] < MIN_TRADES:
        return f"{name}: لا حكم بعد ({pooled['n']} من {MIN_TRADES} صفقة)"
    lo = [v["ci"][95.0][0] for v in by_group.values()]
    hi = pooled["ci"][95.0][1]
    if all(x > 0 for x in lo) and pooled["ci"][95.0][0] > 0:
        return f"{name}: تحققت"
    return f"{name}: عكسية" if hi < 0 else f"{name}: لم تتحقق"


def fmt(v):
    if v is None:
        return "- | - | - | - | -"
    return f"{v['n']} | {100 * v['tp']:.0f}% | {v['mean']:+.3f}R | {v['ci'][95.0][0]:+.3f}…{v['ci'][95.0][1]:+.3f} | {v['ci'][99.6][0]:+.3f}…{v['ci'][99.6][1]:+.3f}"


def report(stats_by_mode, meta):
    """stats_by_mode: {mode: {مجموعة: إحصاءة}}."""
    L = ["# FIBO ENTRY و«المنطقة الذهبية + FVG» على 15 دقيقة (نتائج آلية)", "", f"- {meta}",
         "- قراءة فقط. R بعد كلفة 0.002% لكل جهة. تحت السير العشوائي: متوسط R ≈ 0 قبل الكلفة. القواعد نفسها في النسختين والفرق شرط الدخول فقط.",
         "- **الفرضيتان المسجّلتان مسبقاً:** متوسط R بعد الكلفة > 0 بفاصل 95% في كل مجموعة وفي المجموع بعد 300 صفقة. خطوة «خط الاتجاه» غير منفذة (ذاتية).", ""]
    titles = {"fib79": "H-FIBO: دخول عند 0.79 (عائد/مخاطرة نحو 3)", "zone": "H-FIBOZ: المنطقة الذهبية 0.618–0.706 + FVG (عائد/مخاطرة نحو 1.4)"}
    for mode, stats in stats_by_mode.items():
        L += [f"## {titles.get(mode, mode)}", "", "| المجموعة | صفقات | بلوغ الهدف | متوسط R | فاصل 95% | فاصل 99.6% |", "|---|---|---|---|---|---|"]
        for g in ("صناديق (مع SPY)", "شركات", "الكل"):
            L.append(f"| {g} | {fmt(stats.get(g))} |")
        by_group = {g: stats.get(g) for g in ("صناديق (مع SPY)", "شركات")}
        L += ["", "⚖️ **" + verdict(by_group, stats.get("الكل"), MODES.get(mode, mode)) + "**", ""]
    return L


def analyze(series, modes=tuple(MODES)):
    """{mode: {مجموعة: إحصاءة}} لكل نسخة."""
    groups = {"صناديق (مع SPY)": [s for s in series if s in rp.ETFS or s == "SPY"], "شركات": [s for s in series if s not in rp.ETFS and s != "SPY"]}
    days_by = {g: sorted({pd.Timestamp(x) for s in syms for x in series[s]["t"].dt.date.unique()}) for g, syms in groups.items()}
    out = {}
    for mode in modes:
        stats, all_frames = {}, []
        for gname, syms in groups.items():
            frames = [f for f in (run_series(series[s], mode).assign(sym=s) for s in syms) if len(f)]
            stats[gname] = group_stats(frames, days_by[gname])
            all_frames += frames
        stats["الكل"] = group_stats(all_frames, sorted(set(days_by["صناديق (مع SPY)"]) | set(days_by["شركات"])))
        out[mode] = stats
    return out


def main(argv):
    series = rp.load_series(argv[0] if argv else rp.BARS_DIR)
    if not series:
        print("لا بيانات سلاسل (شغّل bars_history.py أولاً).")
        return 0
    stats = analyze(series)
    meta = f"{len(series)} سلسلة"
    L = report(stats, meta)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
