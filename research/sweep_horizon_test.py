#!/usr/bin/env python3
"""H-SWEEPX: عكس سحب السيولة على شركات 15د، هل تكبر ميزته مع مدة أطول؟ اختبار فقط (لا يغيّر أي بوت). بطلب المستخدم 30/10/2026.

الأصل: في البطولة (H-TOURNEY) كانت «عكس سحب السيولة» على 15د في الشركات الإشارة الوحيدة التي تفوقت على العشوائي بشكل مؤكد
(+0.013% للإشارة بعد 30 دقيقة، 73,835 إشارة)، لكنها أصغر من التكلفة. هنا: الإشارة نفسها حرفياً (tournament.signals، العمود sweep~)
على الشركات نفسها، ومدد أطول. الفرضية (مسجّلة هنا وفي CLAUDE.md قبل التشغيل):
  المدد: 30 دقيقة (للمقارنة)، ساعتان، حتى إغلاق اليوم، وحتى إغلاق اليوم التالي. الأساسيتان: **إغلاق اليوم** و**اليوم التالي**.
  المقياس كما في البطولة: جهة الإشارة × (العائد − متوسط عائد الرمز في النصف نفسه)، كل إشارة بوزن متساوٍ ومعاينة بالأيام.
  (1) في النصف الثاني (الحكم): زيادة موجبة بـ p < 0.05 في مدة أساسية واحدة على الأقل، وموجبة في النصف الأول للمدة نفسها.
  (2) الجدوى: مقارنة بحواجز تقديرية مسجّلة مسبقاً لحركة السهم: **أسهم** 0.03% (فرق وعمولة)؛ **عقد 14–21 يوماً دلتا 0.45**:
      0.25% حتى الإغلاق و0.45% لليوم التالي (فرق الشراء/البيع نحو 3.5% من سعر العقد وتآكل يومي نحو 3%، ونسبة حركة العقد إلى السهم نحو 15).
تشغيل: SPXBOT_DATA=/opt/spxbot/data python research/sweep_horizon_test.py   المخرج: research/sweep_horizon_test_results.md
"""
import os
import sys
from datetime import datetime, time as dtime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import tournament as T  # noqa: E402

OUT = os.path.join(HERE, "sweep_horizon_test_results.md")
HORIZONS = (("30m", "30 دقيقة"), ("2h", "ساعتان"), ("close", "حتى إغلاق اليوم"), ("1d", "حتى إغلاق اليوم التالي"))
PRIMARY = ("close", "1d")
HURDLE_STOCK = 0.0003
HURDLE_OPT = {"close": 0.0025, "1d": 0.0045}


def forwards(d):
    """عوائد المدد من إغلاق كل شمعة 15د: 30د (شمعتان)، ساعتان (8)، إغلاق اليوم، إغلاق اليوم التالي."""
    c = d["Close"]
    day = pd.Series(d.index.date, index=d.index)
    out = pd.DataFrame(index=d.index)
    for name, n in (("30m", 2), ("2h", 8)):
        f = c.shift(-n) / c - 1
        f[day.shift(-n) != day] = np.nan
        out[name] = f
    last = c.groupby(day).transform("last")
    out["close"] = last / c - 1
    daily = c.groupby(day).last()
    nxt = day.map(daily.shift(-1))
    out["1d"] = nxt / c - 1
    out[d.index.time >= dtime(15, 45)] = np.nan          # لا دخول في آخر شمعة
    return out


def collect(data):
    """صفوف الإشارات (sym, day, half, sign, عوائد مُزالة لكل مدة)."""
    rows = []
    days_all = sorted({x for d in data.values() for x in set(d.index.date)})
    mid = days_all[len(days_all) // 2]
    for sym, d5 in data.items():
        d = T.resample(d5, 15)
        sig = T.signals(d, 15)["sweep~"]
        fw = forwards(d)
        half = (pd.Series(d.index.date, index=d.index) >= mid).astype(int)
        for h, _ in HORIZONS:
            for hf in (0, 1):
                m = fw[h][half == hf].mean()
                fw.loc[half == hf, h] = fw.loc[half == hf, h] - m
        k = sig != 0
        part = fw[k].copy()
        part["sign"] = sig[k]
        part["day"] = [x for x in d.index[k].date]
        part["half"] = half[k]
        part["sym"] = sym
        rows.append(part)
    return pd.concat(rows) if rows else pd.DataFrame(), mid


def boot(v, days, n=3000, seed=47):
    df = pd.DataFrame({"v": v, "d": days}).dropna()
    if df["d"].nunique() < 20:
        return None, None
    g = df.groupby("d")["v"]
    S, N = g.sum().values, g.size().values
    idx = np.random.default_rng(seed).integers(0, len(S), (n, len(S)))
    mm = S[idx].sum(1) / N[idx].sum(1)
    return float(S.sum() / N.sum()), float((mm <= 0).mean())


def report(ev, mid, meta):
    L = ["# H-SWEEPX: عكس سحب السيولة (15د، الشركات) بمدد أطول (اختبار، Alpaca 5د منذ 2019)", "", f"- {meta}",
         f"- الاكتشاف قبل {mid}، والحكم بعده. الزيادة = الفرق عن عشوائي بالمزيج نفسه (بعد إزالة متوسط الرمز).", "",
         "| المدة | الاكتشاف: الزيادة | p | الحكم: الزيادة | p | إشارات (الحكم) | فوق حاجز الأسهم 0.03%؟ | فوق حاجز العقد؟ |", "|---|---|---|---|---|---|---|---|"]
    passed = []
    for h, name in HORIZONS:
        a = ev[ev["half"] == 0]
        b = ev[ev["half"] == 1]
        ma, pa = boot((a["sign"] * a[h]).values, a["day"].values)
        mb, pb = boot((b["sign"] * b[h]).values, b["day"].values)
        nb = int((b[h].notna()).sum())
        if mb is None:
            continue
        opt = HURDLE_OPT.get(h)
        L.append(f"| {name} | {100 * ma:+.3f}% | {pa:.3f} | {100 * mb:+.3f}% | {pb:.3f} | {nb} | {'نعم' if mb > HURDLE_STOCK else 'لا'} | "
                 f"{'-' if opt is None else ('نعم' if mb > opt else f'لا ({100 * opt:.2f}%)')} |")
        if h in PRIMARY and mb > 0 and pb < 0.05 and ma > 0:
            passed.append((h, name, mb))
    L += ["", f"⚖️ H-SWEEPX (1): {'تحققت في: ' + '، '.join(n for _, n, _ in passed) if passed else 'لم تتحقق في المدتين الأساسيتين'}"]
    if passed:
        best = max(passed, key=lambda x: x[2])
        L.append(f"⚖️ H-SWEEPX (2) الجدوى: أسهم {'نعم' if best[2] > HURDLE_STOCK else 'لا'}؛ عقد 14–21 يوماً {'نعم' if best[2] > HURDLE_OPT[best[0]] else 'لا'} "
                 f"({best[1]}: {100 * best[2]:+.3f}%)")
    return L


def main():
    import stocks_bot as sb
    data = {s: d for s, d in T.load_all().items() if s in sb.WATCHLIST}
    ev, mid = collect(data)
    L = report(ev, mid, f"{len(data)} شركة، {len(ev)} إشارة")
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(HERE))
    sys.exit(main())
