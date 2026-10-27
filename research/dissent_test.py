#!/usr/bin/env python3
"""H-DISSENTH: حين يخالف صوتٌ الأغلبية، هل يقرأ اتجاه المؤشر في أول 15–30 دقيقة؟ اختبار فقط (لا يغيّر أي بوت). بطلب المستخدم 27/10/2026.

فكرة المستخدم: المخالف ينظر من زاوية أخرى؛ إن قال «هبوط» يهبط المؤشر في أول 15 دقيقة، فنجني ونخرج قبل الارتداد.
الفرضية (مسجّلة هنا وفي CLAUDE.md قبل التشغيل):
  مواعيد المجلس 10:30 و11:30 و12:30 و13:30 نيويورك، على SPY 5د من Alpaca منذ 2019. ثلاثة أصوات يمكن إعادة بنائها تاريخياً:
    mom   = جهة حركة الساعة السابقة (كما في candidates_shadow)،
    f30   = جهة أول 30 دقيقة (9:30→10:00)،
    bot   = جهة نقاط البوت الجزئية (direction_points بمكوّنات الشموع) في آخر فحص :05/:35 قبل الموعد إن كان الفرق ≥ 5 (وإلا بلا رأي).
  (ظل الزخم الحي لا تاريخ له فلا يدخل.) الخلاف = صوتان على الأقل لهما رأي وليسا في جهة واحدة؛ الأقلية = الصوت الوحيد المخالف لصوتين (2 ضد 1)؛ 1 ضد 1 تعادل بلا أقلية.
  النتيجة: حركة SPY في جهة الأقلية بعد 15 دقيقة (الأساسية، كما حدّدها المستخدم) و30 دقيقة.
  (1) التفوق على العشوائي: متوسط حركة 15د أعلى من 95% من 10,000 اختيار عشوائي للجهة في الأوقات نفسها.
  (2) الثبات: موجب في نصفي الأيام.
  (3) الجدوى: > 0.02% (تقدير تكلفة عقد على السعر لليوم التالي لمدة 15 دقيقة: فرق الشراء/البيع نحو 2% ونسبة حركة العقد إلى SPY نحو 100–120).
  وصفي: حسب الصوت المخالف، و30 دقيقة، ونسبة صواب الأقلية، والأغلبية في المواعيد نفسها، وكل صوت منفرداً في كل المواعيد.
تشغيل: SPXBOT_DATA=/opt/spxbot/data python research/dissent_test.py   المخرج: research/dissent_test_results.md
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
from daytype_test import load  # noqa: E402
from live_tick_test import points_at  # noqa: E402

OUT = os.path.join(HERE, "dissent_test_results.md")
SLOTS = (dtime(10, 30), dtime(11, 30), dtime(12, 30), dtime(13, 30))
BOT_EDGE = 5.0
HURDLE = 0.0002
NAMES = {"mom": "زخم آخر ساعة", "f30": "أول 30 دقيقة", "bot": "نقاط البوت"}


def sgn(x):
    return 1 if x > 0 else (-1 if x < 0 else 0)


def price_at(g, t):
    """إغلاق آخر شمعة 5د انتهت عند t أو قبله (بداية الشمعة + 5 ≤ t)."""
    s = g[g.index <= t - timedelta(minutes=5)]["Close"]
    return float(s.iloc[-1]) if len(s) else None


def slot_rows(bars):
    """صف لكل موعد: أصوات الثلاثة وحركة SPY بعد 15 و30 دقيقة."""
    days = sorted(set(bars.index.date))
    closes = bars.groupby(bars.index.date)["Close"].last()
    by_day = {d: g for d, g in bars.groupby(bars.index.date)}
    out = []
    for i, day in enumerate(days):
        if i < 50:
            continue
        g = by_day[day]
        if len(g) < 70:
            continue
        tz = g.index.tz
        hist = pd.concat([by_day[d] for d in days[i - 4:i + 1]])
        prev = pd.Series(closes[closes.index < day].values)
        o0 = float(g["Open"].iloc[0])
        p10 = price_at(g, datetime.combine(day, dtime(10, 0), tzinfo=tz))
        bot_side, last = None, None
        for st in SLOTS:
            n = datetime.combine(day, st, tzinfo=tz)
            ck = n - timedelta(minutes=25)                            # آخر فحص :05/:35 قبل الموعد
            if last != ck:
                today = g[g.index < ck]
                if len(today) >= 6:
                    bull, bear, _ = points_at(hist[hist.index < ck], today, ck, prev)
                    bot_side = (1 if bull > bear else -1) if abs(bull - bear) >= BOT_EDGE else None
                last = ck
            p0, p_1h = price_at(g, n), price_at(g, n - timedelta(minutes=60))
            if None in (p0, p_1h, p10):
                continue
            row = {"day": day, "slot": st.strftime("%H:%M"), "mom": sgn(p0 / p_1h - 1) or None, "f30": sgn(p10 / o0 - 1) or None, "bot": bot_side}
            for h in (15, 30):
                p1 = price_at(g, n + timedelta(minutes=h))
                row[f"m{h}"] = None if p1 is None else p1 / p0 - 1
            out.append(row)
    return out


def classify(rows):
    """يضيف state (إجماع/خلاف/تعادل/قليل) والأقلية وجهتها والأغلبية."""
    for r in rows:
        votes = {k: r[k] for k in ("mom", "f30", "bot") if r[k]}
        up, dn = [k for k, v in votes.items() if v > 0], [k for k, v in votes.items() if v < 0]
        r["minority"] = r["side"] = None
        if len(votes) < 2:
            r["state"] = "قليل"
        elif not up or not dn:
            r["state"] = "إجماع"
        elif len(up) == len(dn):
            r["state"] = "تعادل"
        else:
            r["state"] = "خلاف"
            mino, r["side"] = (up, 1) if len(up) < len(dn) else (dn, -1)
            r["minority"] = mino[0]
    return rows


def vs_random(moves, sims=10000, seed=29):
    v = np.array(moves)
    rand = (np.abs(v)[None, :] * np.random.default_rng(seed).choice([-1, 1], (sims, len(v)))).mean(1)
    return float(v.mean()), float((rand >= v.mean()).mean()), float((v > 0).mean())


def report(rows, meta):
    dis = [r for r in rows if r["state"] == "خلاف" and r["m15"] is not None]
    L = ["# H-DISSENTH: المخالف للأغلبية وحركة المؤشر في أول 15–30 دقيقة (اختبار، SPY 5د من Alpaca)", "", f"- {meta}",
         f"- مواعيد: {len(rows)}؛ إجماع {sum(r['state'] == 'إجماع' for r in rows)}، خلاف {sum(r['state'] == 'خلاف' for r in rows)}، "
         f"تعادل {sum(r['state'] == 'تعادل' for r in rows)}، أصوات قليلة {sum(r['state'] == 'قليل' for r in rows)}.",
         "- الأصوات: زخم آخر ساعة، أول 30 دقيقة، نقاط البوت الجزئية (فرق ≥ 5). حركة SPY ليست ربح العقد؛ حاجز التكلفة التقديري لـ15 دقيقة 0.02%.", ""]
    if len(dis) < 20:
        return L + [f"⚖️ H-DISSENTH: لا حكم (مواعيد خلاف {len(dis)})"]
    m15 = [r["side"] * r["m15"] for r in dis]
    m30 = [r["side"] * r["m30"] for r in dis if r["m30"] is not None]
    mean15, p, hit = vs_random(m15)
    days = sorted({r["day"] for r in dis})
    mid = days[len(days) // 2]
    h1 = float(np.mean([r["side"] * r["m15"] for r in dis if r["day"] < mid]))
    h2 = float(np.mean([r["side"] * r["m15"] for r in dis if r["day"] >= mid]))
    L += ["## الأقلية (المخالف) في مواعيد الخلاف", "", "| المدة | مواعيد | حركة في جهة المخالف | المخالف محق | عشوائي أفضل منها |", "|---|---|---|---|---|",
          f"| 15 دقيقة | {len(m15)} | {100 * mean15:+.3f}% | {100 * hit:.0f}% | {100 * p:.1f}% |"]
    if m30:
        a, b, c = vs_random(m30)
        L.append(f"| 30 دقيقة | {len(m30)} | {100 * a:+.3f}% | {100 * c:.0f}% | {100 * b:.1f}% |")
    L += ["", f"⚖️ H-DISSENTH (1) التفوق على العشوائي عند 15د: {'نعم' if p < 0.05 else 'لا'} (أفضل من {100 * (1 - p):.1f}% من الاختيارات العشوائية)",
          f"⚖️ H-DISSENTH (2) الثبات في النصفين: {'نعم' if h1 > 0 and h2 > 0 else 'لا'} ({100 * h1:+.3f}% ثم {100 * h2:+.3f}%)",
          f"⚖️ H-DISSENTH (3) الجدوى فوق 0.02%: {'نعم' if mean15 > HURDLE else 'لا'} ({100 * mean15:+.3f}%)", "",
          "## حسب الصوت المخالف (15 دقيقة)", "", "| المخالف | مواعيد | حركة في جهته | محق | 30 دقيقة |", "|---|---|---|---|---|"]
    for k in ("mom", "f30", "bot"):
        g = [r for r in dis if r["minority"] == k]
        if g:
            a = [r["side"] * r["m15"] for r in g]
            b = [r["side"] * r["m30"] for r in g if r["m30"] is not None]
            L.append(f"| {NAMES[k]} | {len(g)} | {100 * np.mean(a):+.3f}% | {100 * np.mean([x > 0 for x in a]):.0f}% | {100 * np.mean(b):+.3f}% |")
    L += ["", "## كل صوت منفرداً في كل المواعيد (وصفي، 15 دقيقة)", "", "| الصوت | آراء | حركة في جهته | محق |", "|---|---|---|---|"]
    for k in ("mom", "f30", "bot"):
        a = [r[k] * r["m15"] for r in rows if r[k] and r["m15"] is not None]
        if a:
            L.append(f"| {NAMES[k]} | {len(a)} | {100 * np.mean(a):+.3f}% | {100 * np.mean([x > 0 for x in a]):.0f}% |")
    cons = [r for r in rows if r["state"] == "إجماع" and r["m15"] is not None]
    if cons:
        a = [(r["mom"] or r["f30"] or r["bot"]) * r["m15"] for r in cons]
        L.append(f"| الإجماع (للمقارنة) | {len(a)} | {100 * np.mean(a):+.3f}% | {100 * np.mean([x > 0 for x in a]):.0f}% |")
    return L


def main(argv):
    bars = load()
    rows = classify(slot_rows(bars))
    days = sorted({r["day"] for r in rows})
    L = report(rows, f"{len(days)} يوم تداول ({days[0]} → {days[-1]})")
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
