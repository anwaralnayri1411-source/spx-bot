#!/usr/bin/env python3
"""H-EDGE7H: هل تتفوق إشارات البوت القوية على العشوائي على 7 سنوات؟ اختبار فقط (لا يغيّر أي بوت). بطلب المستخدم 27/10/2026.

الفرضية (مسجّلة هنا وفي CLAUDE.md قبل التشغيل):
  نقاط اتجاه البوت (options_bot.direction_points نفسها) من مكوّنات الشموع فقط، على SPY 5د من Alpaca منذ 2019
  (المتوسطات اليومية، VWAP، ema9/21، r30، الفجوة، نطاق أول 30د، أعلى/أدنى أمس؛ بلا VIX والجدران والغاما ونقطة الألم وES والقياديات والكول/البوت).
  الفحص كما في البوت: :05 و:35 من 10:05 إلى 14:35 (آخر دخول في ظل edge7)، بالشموع المكتملة فقط؛ منع تكرار الاتجاه 90 دقيقة في اليوم.
  الإشارة القوية: فرق النقاط ≥ 7 (أقصى ما تبلغه النقاط الجزئية 7، فهي إشارة «كل المكوّنات متفقة»).
  النتيجة: حركة SPY في اتجاه الإشارة بعد 60 دقيقة (مدة ظل edge7)؛ ثانوياً 30 دقيقة.
  (1) التفوق على العشوائي: متوسط حركة 60د أعلى من 95% من 10,000 اختيار عشوائي للاتجاه في الأوقات نفسها.
  (2) الثبات: المتوسط موجب في النصفين (الأيام الأولى والأخيرة).
  (3) الجدوى: المتوسط > 0.03% (تقدير حاجز التعادل لعقد SPY على السعر لليوم التالي: خسارة المشتري العشوائي الفعلية نحو 3% للصفقة،
      ونسبة حركة العقد إلى حركة SPY نحو 100–120 ضعفاً).
  وصفي: العتبات ≥ 6 (كل المكوّنات متفقة بلا فجوة) و≥ 5 و≥ 3، ونسبة الإصابة، والنتيجة لكل سنة.
تشغيل: SPXBOT_DATA=/opt/spxbot/data python research/edge7_history_test.py   المخرج: research/edge7_history_test_results.md
"""
import os
import sys
from datetime import datetime, time as dtime, timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import options_bot as ob  # noqa: E402
from daytype_test import load  # noqa: E402
from live_tick_test import points_at  # noqa: E402

OUT = os.path.join(HERE, "edge7_history_test_results.md")
CHECKS = [dtime(h, m) for h in range(10, 15) for m in (5, 35) if (h, m) <= (14, 35)]
REPEAT = timedelta(minutes=ob.REPEAT_MINUTES)
THRESHOLDS = (7.0, 6.0, 5.0, 3.0)
HURDLE = 0.0003


def run(bars):
    """[{day, t, edge, side, f30, f60}] لكل إشارة (فرق ≥ 3) بمنع التكرار لكل عتبة على حدة لاحقاً."""
    days = sorted(set(bars.index.date))
    closes = bars.groupby(bars.index.date)["Close"].last()
    by_day = {d: g for d, g in bars.groupby(bars.index.date)}
    out = []
    for i, day in enumerate(days):
        if i < 50:
            continue                                     # إحماء: المتوسط اليومي 50
        g = by_day[day]
        if len(g) < 70:
            continue
        hist = pd.concat([by_day[d] for d in days[i - 4:i + 1]])
        prev = closes[closes.index < day]
        prev = pd.Series(prev.values)
        for ct in CHECKS:
            n = datetime.combine(day, ct, tzinfo=g.index.tz)
            ix = hist[hist.index < n]
            today = g[g.index < n]
            if len(today) < 6:
                continue
            bull, bear, S = points_at(ix, today, n, prev)
            e = abs(bull - bear)
            if e < THRESHOLDS[-1]:
                continue
            row = {"day": day, "t": n, "edge": e, "side": 1 if bull > bear else -1, "P": float(today["Close"].iloc[-1])}
            for h in (30, 60):
                fut = g[g.index < n + timedelta(minutes=h)]["Close"]
                row[f"f{h}"] = row["side"] * (float(fut.iloc[-1]) / row["P"] - 1)
            out.append(row)
    return out


def with_repeat(events, thr):
    out, last = [], {}
    for e in events:
        if e["edge"] < thr:
            continue
        if last.get("day") != e["day"]:
            last = {"day": e["day"]}
        if e["side"] in last and e["t"] - last[e["side"]] < REPEAT:
            continue
        last[e["side"]] = e["t"]
        out.append(e)
    return out


def vs_random(ev, key, sims=10000, seed=23):
    v = np.array([e[key] for e in ev])
    raw = v * np.array([e["side"] for e in ev])
    rand = (raw[None, :] * np.random.default_rng(seed).choice([-1, 1], (sims, len(raw)))).mean(1)
    return float(v.mean()), float((rand >= v.mean()).mean()), float(np.percentile(rand, 97.5)), float((v > 0).mean())


def report(events, meta):
    L = ["# H-EDGE7H: إشارات البوت القوية مقابل العشوائي على سنوات (اختبار، SPY 5د من Alpaca)", "", f"- {meta}",
         "- نقاط البوت الجزئية (بلا VIX والجدران والغاما ونقطة الألم وES والقياديات). حركة SPY ليست ربح العقد؛ حاجز التعادل التقديري لعقد على السعر لليوم التالي 0.03%.", "",
         "| العتبة | إشارات | أيام | حركة 60د | عشوائي أفضل منها | حد العشوائي 97.5% | إصابة | حركة 30د | النصف الأول | النصف الثاني |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    res = {}
    for thr in THRESHOLDS:
        ev = with_repeat(events, thr)
        if len(ev) < 10:
            L.append(f"| ≥ {thr:g} | {len(ev)} | - | - | - | - | - | - | - | - |")
            continue
        m60, p, hi, hit = vs_random(ev, "f60")
        m30 = float(np.mean([e["f30"] for e in ev]))
        days = sorted({e["day"] for e in ev})
        mid = days[len(days) // 2]
        h1 = float(np.mean([e["f60"] for e in ev if e["day"] < mid]))
        h2 = float(np.mean([e["f60"] for e in ev if e["day"] >= mid]))
        res[thr] = (m60, p, h1, h2, ev)
        L.append(f"| ≥ {thr:g} | {len(ev)} | {len(days)} | {100 * m60:+.3f}% | {100 * p:.1f}% | {100 * hi:+.3f}% | {100 * hit:.0f}% | {100 * m30:+.3f}% | {100 * h1:+.3f}% | {100 * h2:+.3f}% |")
    if 7.0 in res:
        m60, p, h1, h2, ev = res[7.0]
        L += ["", f"⚖️ H-EDGE7H (1) التفوق على العشوائي: {'نعم' if p < 0.05 else 'لا'} (أفضل من {100 * (1 - p):.1f}% من الاختيارات العشوائية)",
              f"⚖️ H-EDGE7H (2) الثبات في النصفين: {'نعم' if h1 > 0 and h2 > 0 else 'لا'} ({100 * h1:+.3f}% ثم {100 * h2:+.3f}%)",
              f"⚖️ H-EDGE7H (3) الجدوى فوق حاجز 0.03%: {'نعم' if m60 > HURDLE else 'لا'} ({100 * m60:+.3f}%)", "",
              "## الإشارات القوية لكل سنة (حركة 60د)", "", "| السنة | إشارات | متوسط | إصابة |", "|---|---|---|---|"]
        for y in sorted({e["day"].year for e in ev}):
            v = [e["f60"] for e in ev if e["day"].year == y]
            L.append(f"| {y} | {len(v)} | {100 * np.mean(v):+.3f}% | {100 * np.mean([x > 0 for x in v]):.0f}% |")
    return L


def main(argv):
    bars = load()
    events = run(bars)
    days = sorted(set(bars.index.date))
    L = report(events, f"{len(days) - 50} يوم تداول ({days[50]} → {days[-1]})، فحص :05/:35 من 10:05 إلى 14:35")
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
