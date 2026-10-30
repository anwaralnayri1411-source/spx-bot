#!/usr/bin/env python3
"""H-OFLOW: هل يتنبأ الأوردر فلو (دلتا الشراء/البيع العدواني) بحركة SPY؟ ومع مستويات Volume Profile؟ اختبار فقط. بطلب المستخدم 30/10/2026.

البيانات: SPXBOT_DATA/orderflow_SPY_1m.csv.gz من orderflow_data.py (قاعدة التيك على كل صفقات Alpaca SIP، عينة نحو 40 يوم تداول).
الاختبارات (مسجّلة قبل التشغيل؛ العينة صغيرة: الحكم = p يجتاز Benjamini-Hochberg بمعدل 5% على الاختبارات الثلاثة الأساسية
  (OF1 30د، OF2، OF3) **وموجب في النصفين**، ثم حاجز التكلفة):
  OF1 الاختلال: كل 5 دقائق من 10:00 إلى 15:30، اختلال آخر 5 دقائق = (شراء − بيع) ÷ (شراء + بيع)؛ إن كان |الاختلال| ≥ عتبة = المئين 90 لكل
      اختلالات العينة (يُحسب من البيانات لا من النتائج، لأن قيم قاعدة التيك تختلف بين الرموز) فالجهة = إشارته.
      عائد 15 و30 دقيقة في الجهة (الأساسي 30). حاجز 0.03%.
  OF2 الانحراف: كل 30 دقيقة، إن خالفت جهةُ حركة السعر في آخر 30 دقيقة جهةَ الدلتا المجمعة فيها → الجهة = جهة الدلتا. عائد 30 دقيقة. حاجز 0.03%.
  OF3 الدلتا التراكمية منذ الافتتاح عند 11:00 → الجهة؛ عائد حتى الإغلاق. حاجز 0.10%.
  OFC الدمج مع المستوى (وصفي لصغر العينة): أول ملامسة لـPOC الأمس (من ملف الحجم بالدقيقة) بعد 10:00 قادماً من جهة الافتتاح؛
      «مع الضغط» = اختلال 5 دقائق عند الملامسة في جهة الارتداد (> 0)، مقابل «بلا ضغط» (≤ 0). عائد 30 دقيقة في جهة الارتداد للمجموعتين.
  المقياس: الزيادة على عشوائي بالمزيج نفسه، ومعاينة بالأيام.
تشغيل: SPXBOT_DATA=/opt/spxbot/data python research/orderflow_test.py   المخرج: research/orderflow_test_results.md
"""
import os
import sys
from datetime import datetime, time as dtime, timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
OUT = os.path.join(HERE, "orderflow_test_results.md")
IMB = 0.15
BIN = 0.00065
HURDLE = {"OF1": 0.0003, "OF2": 0.0003, "OF3": 0.0010}


def load(path=None):
    d = pd.read_csv(path or os.path.join(DATA, "orderflow_SPY_1m.csv.gz"), parse_dates=["t"]).drop_duplicates("t").set_index("t").sort_index()
    return d[(d.index.time >= dtime(9, 30)) & (d.index.time < dtime(16, 0))]


def px_at(g, t):
    s = g[g.index < t]["close"]
    return None if s.empty else float(s.iloc[-1])


def ret(g, t, minutes=None):
    p0 = px_at(g, t)
    if p0 is None:
        return None
    if minutes is None:
        return float(g["close"].iloc[-1]) / p0 - 1
    p1 = px_at(g, t + timedelta(minutes=minutes))
    last = g.index[-1]
    if p1 is None or t + timedelta(minutes=minutes) > last + timedelta(minutes=1):
        return None
    return p1 / p0 - 1


def imbalance(g, t, minutes=5):
    w = g[(g.index >= t - timedelta(minutes=minutes)) & (g.index < t)]
    tot = w["buy"].sum() + w["sell"].sum()
    return None if tot <= 0 else float((w["buy"].sum() - w["sell"].sum()) / tot)


def poc_of(g):
    w = float(g["close"].iloc[0]) * BIN
    vol = (g["buy"] + g["sell"]).groupby((g["close"] / w).round().astype(int)).sum()
    return float(vol.idxmax()) * w


def threshold(d):
    """المئين 90 لـ|اختلال 5 دقائق| على كل العينة (10:00–15:30)."""
    v = []
    for day, g in d.groupby(d.index.date):
        r = g.resample("5min")[["buy", "sell"]].sum()
        r = r[(r.index.time >= dtime(9, 55)) & (r.index.time < dtime(15, 30))]
        tot = r["buy"] + r["sell"]
        v += list(((r["buy"] - r["sell"]) / tot.replace(0, np.nan)).abs().dropna())
    return max(0.02, float(np.percentile(v, 90))) if v else IMB


def analyze(d, imb=None):
    imb = imb if imb is not None else threshold(d)
    rows = []
    days = sorted(set(d.index.date))
    by = {x: g for x, g in d.groupby(d.index.date)}
    for i, day in enumerate(days):
        g = by[day]
        if len(g) < 300:
            continue
        base = datetime.combine(day, dtime(0, 0))
        # OF1
        t = base.replace(hour=10)
        while t <= base.replace(hour=15, minute=30):
            im = imbalance(g, t)
            if im is not None and abs(im) >= imb:
                for h in (15, 30):
                    r = ret(g, t, h)
                    if r is not None:
                        rows.append({"day": day, "test": f"OF1_{h}", "side": 1 if im > 0 else -1, "raw": r})
            t += timedelta(minutes=5)
        # OF2
        t = base.replace(hour=10)
        while t <= base.replace(hour=15, minute=0):
            p_now, p_then = px_at(g, t), px_at(g, t - timedelta(minutes=30))
            w = g[(g.index >= t - timedelta(minutes=30)) & (g.index < t)]
            dl = float(w["buy"].sum() - w["sell"].sum())
            if p_now and p_then and dl != 0 and np.sign(p_now - p_then) == -np.sign(dl):
                r = ret(g, t, 30)
                if r is not None:
                    rows.append({"day": day, "test": "OF2", "side": int(np.sign(dl)), "raw": r})
            t += timedelta(minutes=30)
        # OF3
        t = base.replace(hour=11)
        w = g[g.index < t]
        cd = float(w["buy"].sum() - w["sell"].sum())
        r = ret(g, t)
        if cd != 0 and r is not None:
            rows.append({"day": day, "test": "OF3", "side": int(np.sign(cd)), "raw": r})
        # OFC
        if i > 0 and days[i - 1] in by and len(by[days[i - 1]]) >= 300:
            poc = poc_of(by[days[i - 1]])
            o = float(g["close"].iloc[0])
            if abs(o / poc - 1) > 0.001:
                after = g[(g.index.time >= dtime(10, 0)) & (g.index.time < dtime(15, 30))]
                touch = after[((after["close"] - poc) * (o - poc)) <= 0]
                if len(touch):
                    tt = touch.index[0] + timedelta(minutes=1)
                    side = 1 if o > poc else -1
                    im = imbalance(g, tt)
                    r = ret(g, tt, 30)
                    if im is not None and r is not None:
                        rows.append({"day": day, "test": "OFC_with" if side * im > 0 else "OFC_without", "side": side, "raw": r})
    return pd.DataFrame(rows)


def stats(sub, n=3000, seed=73):
    if sub["day"].nunique() < 8:
        return None
    x = sub["side"] * (sub["raw"] - sub["raw"].mean())
    S = x.groupby(sub["day"]).sum().values
    N = x.groupby(sub["day"]).size().values
    idx = np.random.default_rng(seed).integers(0, len(S), (n, len(S)))
    m = S[idx].sum(1) / N[idx].sum(1)
    return {"n": len(sub), "days": sub["day"].nunique(), "mean": float(S.sum() / N.sum()), "p": float((m <= 0).mean()),
            "hit": float(((sub["side"] * sub["raw"]) > 0).mean())}


NAMES = {"OF1_15": "OF1 الاختلال 5د → 15د", "OF1_30": "OF1 الاختلال 5د → 30د", "OF2": "OF2 الانحراف (السعر عكس الدلتا) → 30د",
         "OF3": "OF3 الدلتا التراكمية عند 11:00 → الإغلاق", "OFC_with": "OFC ملامسة POC الأمس مع ضغط في جهة الارتداد → 30د",
         "OFC_without": "OFC ملامسة POC الأمس بلا ضغط → 30د"}


def half_txt(x):
    return "-" if not x else f"{100 * x['mean']:+.3f}%"


def report(df, meta):
    L = ["# H-OFLOW: الأوردر فلو على SPY (قاعدة التيك على كل الصفقات، عينة أيام أخيرة)", "", f"- {meta}",
         "- الزيادة = الفرق عن عشوائي بالمزيج نفسه. العينة صغيرة: الحكم يحتاج p < 0.05 على كل الأيام وموجباً في النصفين، ثم حاجز التكلفة.", "",
         "| الاختبار | أحداث | أيام | الزيادة | p | في الجهة الصحيحة | النصف الأول | النصف الثاني |", "|---|---|---|---|---|---|---|---|"]
    if df.empty:
        return L + ["لا بيانات."]
    days = sorted(df["day"].unique())
    mid = days[len(days) // 2]
    verdicts, prim = [], []
    for k in NAMES:
        sub = df[df["test"] == k]
        s = stats(sub) if len(sub) else None
        if not s:
            L.append(f"| {NAMES[k]} | {len(sub)} | - | - | - | - | - | - |")
            continue
        a, b = stats(sub[sub["day"] < mid]), stats(sub[sub["day"] >= mid])
        L.append(f"| {NAMES[k]} | {s['n']} | {s['days']} | {100 * s['mean']:+.3f}% | {s['p']:.3f} | {100 * s['hit']:.0f}% | "
                 f"{half_txt(a)} | {half_txt(b)} |")
        base = k.split("_")[0] if k.startswith("OF1") else k
        if k in ("OF1_30", "OF2", "OF3"):
            prim.append((k, s, a, b, base))
    if prim:
        from tournament import bh
        passed = bh(np.array([x[1]["p"] for x in prim]), 0.05)
        for (k, s, a, b, base), okp in zip(prim, passed):
            ok = bool(okp) and a and b and a["mean"] > 0 and b["mean"] > 0
            cost = s["mean"] > HURDLE[base]
            verdicts.append(f"⚖️ {NAMES[k]}: " + (("تحققت" + ("" if cost else " لكن دون حاجز التكلفة")) if ok else "لم تتحقق") + f" (الزيادة {100 * s['mean']:+.3f}%، p {s['p']:.3f})")
    return L + [""] + verdicts


def main():
    d = load()
    thr = threshold(d)
    df = analyze(d, thr)
    days = sorted(set(d.index.date))
    L = report(df, f"{len(days)} يوم تداول ({days[0]} → {days[-1]})، عتبة الاختلال (المئين 90) = {thr:.3f}")
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
