#!/usr/bin/env python3
"""اختبار «روتين ما قبل الافتتاح» (قراءة فقط؛ H-PREOPEN مسجّلة في CLAUDE.md قبل كتابة الكود؛ بطلب المستخدم 26/10/2026).

المصدر: فيديو abd.qima. (1) قبل الافتتاح: قمم وقيعان فريم الساعة = مناطق سيولة (أوامر الوقف فوق القمم وتحت القيعان). (2) أول 15–30 دقيقة: السعر يكنس إحداها.
(3) على فريم الدقيقة: انعكاس فجوة FVG (IFVG) ثم تغير حالة الحركة (CISD) ثم الدخول؛ الهدف السيولة المقابلة.
التعريف الرقمي على شموع 5 دقائق (بياناتنا؛ التأكيد على الدقيقة مقرّب ويُعلن):
  المستويات = قمم/قيعان محورية (k=1) على شموع الساعة لآخر جلستين كاملتين + أعلى وأدنى أمس. سيولة البيع تحت افتتاح اليوم، وسيولة الشراء فوقه.
  الكنس: في أول 30 دقيقة (الشموع 0–5) يكسر أدنى شمعة أقرب مستوى بيع (شراء) أو أعلاها أقرب مستوى شراء (بيع)؛ الأسبق يحدد الاتجاه، والتعادل في الشمعة نفسها = لا صفقة.
  التأكيد خلال 12 شمعة بعد الكنس: إغلاق فوق CISD (افتتاح أول شمعة هابطة في السلسلة الهابطة المنتهية بقاع اليوم حتى تلك اللحظة)، وفوق أعلى آخر فجوة FVG هابطة قبل القاع إن وُجدت (IFVG).
  الدخول بإغلاق شمعة التأكيد، الوقف أدنى سعر منذ الافتتاح، يُهمل إن تجاوز الخطر 1%، الهدف أقرب مستوى سيولة فوق الدخول (وإلا لا صفقة)، ثم الإغلاق؛ الوقف قبل الهدف في الشمعة نفسها؛ كلفة 0.002% لكل جهة؛ البيع مرآة.
الوهمي: كل المستويات مُزاحة ±0.35%. الفرضية: متوسط R بعد الكلفة > 0 بفاصل 95% في المجموعتين وفي المجموع بعد 300 صفقة؛ ثانوياً الحقيقي − الوهمي.
تشغيل: python research/preopen_routine_study.py   المخرج: research/preopen_routine_study_results.md
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

OUT = os.path.join(HERE, "preopen_routine_study_results.md")
SWEEP_LAST = 5             # آخر شمعة 5 دقائق في نافذة الكنس (أول 30 دقيقة)
CONFIRM_WINDOW = 12
ENTRY_LAST = 69            # لا دخول بعد ~15:15
RISK_CAP = 0.01
COST = 0.00002
SHIFT = 0.0035
MIN_TRADES = 300
HISTORY_DAYS = 2


# ============================ دوال نقية ============================
def hourly(o, h, l, c):
    """78 شمعة 5 دقائق → 7 شموع ساعة (الأخيرة 15:30–16:00)."""
    idx = [(i, min(i + 12, len(c))) for i in range(0, len(c), 12)]
    return (np.array([o[a] for a, b in idx]), np.array([h[a:b].max() for a, b in idx]),
            np.array([l[a:b].min() for a, b in idx]), np.array([c[b - 1] for a, b in idx]))


def liquidity_levels(prev_days):
    """مستويات السيولة من آخر جلستين: قمم/قيعان محورية k=1 على شموع الساعة + أعلى وأدنى أمس. prev_days: [(o,h,l,c)] من الأقدم للأحدث."""
    if not prev_days:
        return []
    H, L = [], []
    for o, h, l, c in prev_days:
        _, hh, ll, _ = hourly(o, h, l, c)
        H += list(hh)
        L += list(ll)
    lv = [H[i] for i in range(1, len(H) - 1) if H[i] > H[i - 1] and H[i] > H[i + 1]]
    lv += [L[i] for i in range(1, len(L) - 1) if L[i] < L[i - 1] and L[i] < L[i + 1]]
    o, h, l, c = prev_days[-1]
    lv += [float(np.max(h)), float(np.min(l))]
    return sorted(set(float(x) for x in lv))


def sweep_time(l, level):
    """أول شمعة في نافذة الكنس يكسر أدناها level، أو None."""
    return next((t for t in range(0, SWEEP_LAST + 1) if l[t] < level), None)


def cisd_level(o, c, m):
    """افتتاح أول شمعة هابطة في السلسلة الهابطة المنتهية عند m (أو قبلها مباشرة إن كانت m صاعدة)، أو None."""
    j = m if c[m] < o[m] else m - 1
    if j < 0 or c[j] >= o[j]:
        return None
    while j - 1 >= 0 and c[j - 1] < o[j - 1]:
        j -= 1
    return float(o[j])


def last_bear_fvg_top(h, l, m):
    """أعلى آخر فجوة FVG هابطة (h[j] < l[j−2]) بين الشمعة 2 وm، أو None."""
    tops = [float(l[j - 2]) for j in range(2, m + 1) if h[j] < l[j - 2]]
    return tops[-1] if tops else None


def simulate(h, l, c, t0, entry, stop, target):
    risk = entry - stop
    cost = 2 * COST * abs(entry) / risk
    for t in range(t0 + 1, len(c)):
        if l[t] <= stop:
            return -1.0 - cost, "sl"
        if h[t] >= target:
            return (target - entry) / risk - cost, "tp"
    return (float(c[-1]) - entry) / risk - cost, "eod"


def bull_from_sweep(o, h, l, c, s, levels):
    """بعد كنس تحت مستوى عند الشمعة s: التأكيد والدخول والنتيجة (R، النوع) أو None."""
    for t in range(s, min(s + CONFIRM_WINDOW + 1, ENTRY_LAST + 1, len(c))):
        m = int(np.argmin(l[: t + 1]))
        lvl = cisd_level(o, c, m)
        if lvl is None or c[t] <= lvl:
            continue
        top = last_bear_fvg_top(h, l, m)
        if top is not None and c[t] <= top:
            continue
        entry, stop = float(c[t]), float(np.min(l[: t + 1]))
        risk = entry - stop
        if risk <= 0 or risk / abs(entry) > RISK_CAP:
            return None
        above = [x for x in levels if x > entry]
        if not above:
            return None
        return simulate(h, l, c, t, entry, stop, min(above))
    return None


def day_trade(o, h, l, c, levels):
    """أسبق كنس (تحت أقرب مستوى بيع → شراء، فوق أقرب مستوى شراء → بيع) ثم التأكيد: (R، النوع، اتجاه) أو None."""
    o0 = o[0]
    below = [x for x in levels if x < o0]
    above = [x for x in levels if x > o0]
    tb = sweep_time(l, max(below)) if below else None
    ts = sweep_time(-h, -min(above)) if above else None
    if tb is None and ts is None:
        return None
    if tb is not None and ts is not None and tb == ts:
        return None
    if ts is None or (tb is not None and tb < ts):
        r = bull_from_sweep(o, h, l, c, tb, levels)
        return None if r is None else (r[0], r[1], 1)
    r = bull_from_sweep(-o, -l, -h, -c, ts, sorted(-x for x in levels))
    return None if r is None else (r[0], r[1], -1)


def run_series(d):
    """DataFrame(day, kind, R, exit, dir): حقيقي ووهمي (المستويات مُزاحة ±0.35%)."""
    rows, hist = [], []
    for day, g in d.groupby(d["t"].dt.date):
        g, half = ls.trim_half_day(g)
        if half or len(g) < 78:
            continue
        o, h, l, c = (g[k].to_numpy(float) for k in ("o", "h", "l", "c"))
        if len(hist) >= HISTORY_DAYS:
            lv = liquidity_levels(hist[-HISTORY_DAYS:])
            for kind, mults in (("real", [1.0]), ("placebo", [1 + SHIFT, 1 - SHIFT])):
                for mlt in mults:
                    res = day_trade(o, h, l, c, [x * mlt for x in lv])
                    if res:
                        rows.append({"day": pd.Timestamp(day), "kind": kind, "R": res[0], "exit": res[1], "dir": res[2]})
        hist.append((o, h, l, c))
    return pd.DataFrame(rows, columns=["day", "kind", "R", "exit", "dir"])


def group_stats(frames, days):
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    real, plac = df[df["kind"] == "real"], df[df["kind"] == "placebo"]
    ci = rp.day_ci(real.rename(columns={"R": "val"}), days)
    if ci is None:
        return None
    n, mean, lv = ci
    return {"n": n, "mean": mean, "ci": lv, "tp": float((real["exit"] == "tp").mean()), "diff": rp.diff_ci(real[["day", "R"]], plac[["day", "R"]], days)}


def verdict(by_group, pooled):
    if pooled is None or any(v is None for v in by_group.values()):
        return "H-PREOPEN: لا حكم (بيانات ناقصة)"
    if pooled["n"] < MIN_TRADES:
        return f"H-PREOPEN: لا حكم بعد ({pooled['n']} من {MIN_TRADES} صفقة)"
    if pooled["ci"][95.0][0] > 0 and all(v["ci"][95.0][0] > 0 for v in by_group.values()):
        return "H-PREOPEN: تحققت"
    return "H-PREOPEN: عكسية" if pooled["ci"][95.0][1] < 0 else "H-PREOPEN: لم تتحقق"


def fmt(v):
    if v is None:
        return "- | - | - | - | -"
    d = v["diff"]
    diff = "-" if d is None else f"{d[1]:+.3f}R ({d[2][95.0][0]:+.3f}…{d[2][95.0][1]:+.3f})"
    return f"{v['n']} | {100 * v['tp']:.0f}% | {v['mean']:+.3f}R | {v['ci'][95.0][0]:+.3f}…{v['ci'][95.0][1]:+.3f} | {diff}"


def report(stats, meta):
    L = ["# روتين ما قبل الافتتاح: كنس سيولة الساعة في أول 30 دقيقة + CISD/IFVG (نتائج آلية)", "", f"- {meta}",
         "- قراءة فقط. على شموع 5 دقائق (الفيديو يؤكد على الدقيقة: تقريب معلن). R بعد كلفة 0.002% لكل جهة. الوهمي: كل المستويات مُزاحة ±0.35%.",
         "- **الفرضية المسجّلة مسبقاً H-PREOPEN:** متوسط R بعد الكلفة > 0 بفاصل 95% في المجموعتين وفي المجموع بعد 300 صفقة.", "",
         "| المجموعة | صفقات | بلوغ الهدف | متوسط R | فاصل 95% | الحقيقي − الوهمي (فاصل 95%) |", "|---|---|---|---|---|---|"]
    for g in ("صناديق (مع SPY)", "شركات", "الكل"):
        L.append(f"| {g} | {fmt(stats.get(g))} |")
    by_group = {g: stats.get(g) for g in ("صناديق (مع SPY)", "شركات")}
    L += ["", "⚖️ **" + verdict(by_group, stats.get("الكل")) + "**"]
    return L


def analyze(series):
    groups = {"صناديق (مع SPY)": [s for s in series if s in rp.ETFS or s == "SPY"], "شركات": [s for s in series if s not in rp.ETFS and s != "SPY"]}
    stats, all_frames, all_days = {}, [], set()
    for gname, syms in groups.items():
        days = sorted({pd.Timestamp(x) for s in syms for x in series[s]["t"].dt.date.unique()})
        frames = [f for f in (run_series(series[s]) for s in syms) if len(f)]
        stats[gname] = group_stats(frames, days)
        all_frames += frames
        all_days |= set(days)
    stats["الكل"] = group_stats(all_frames, sorted(all_days))
    return stats


def main(argv):
    series = rp.load_series(argv[0] if argv else rp.BARS_DIR)
    if not series:
        print("لا بيانات سلاسل (شغّل bars_history.py أولاً).")
        return 0
    L = report(analyze(series), f"{len(series)} سلسلة")
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
