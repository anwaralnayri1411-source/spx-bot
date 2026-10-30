#!/usr/bin/env python3
"""H-POC: نقطة التحكم (POC) ومنطقة القيمة لليوم السابق — اختبار فقط (لا يغيّر أي بوت). بطلب المستخدم 30/10/2026.

POC ومنطقة القيمة (70% من الحجم) لكل يوم بدالة بطاقة قراءة السوق نفسها (market_read.volume_profile)، بخانة سعر = 0.065% من السعر
(≈ 0.5$ لـSPY كما في البطاقة)، من شموع 5د بحجمها الحقيقي (Alpaca SIP). الأساسي SPY؛ QQQ وIWM وDIA للتكرار (وصفي).
ثلاثة أسئلة مسجّلة قبل التشغيل، والحكم على **النصف الثاني** من الأيام (الأول للاكتشاف):
  (1) المغناطيس: في الأيام التي يفتتح فيها السعر على بُعد > 0.1% من POC الأمس، هل يلمس POC الأمس (9:35–15:55) أكثر من «المستوى المرآة»
      على المسافة نفسها في الجهة الأخرى من الافتتاح؟ الفرق في نسبة الملامسة > 0 بفاصل 95% (معاينة الأيام).
  (2) قاعدة الـ80%: افتتاح خارج منطقة قيمة الأمس، ثم إغلاق شمعتي 30 دقيقة متتاليتين داخلها → الدخول بإغلاق الثانية نحو الطرف المقابل،
      والخروج عند لمسه أو عند الإغلاق. المقياس: عائد SPY في الجهة؛ الفرضية > 0 بفاصل 95%، ونسبة النجاح (لمس الطرف المقابل) تُعرض مقابل ادعاء 80%.
      الجدوى: > 0.10% (تقدير تكلفة عقد SPY على السعر لليوم التالي حتى الإغلاق: فرق نحو 3% وتآكل ساعات ÷ رافعة نحو 120).
  (3) الارتداد: أول ملامسة لـPOC الأمس بعد 10:00 قادماً من جهة الافتتاح → جهة الارتداد = العودة نحو جهة الافتتاح؛ عائد 30 و60 دقيقة في الجهة،
      الفرضية: 30 دقيقة > 0 أعلى من 95% من 10,000 اختيار عشوائي للجهة. الجدوى: > 0.03%.
تشغيل: SPXBOT_DATA=/opt/spxbot/data python research/poc_test.py   المخرج: research/poc_test_results.md
"""
import os
import sys
from datetime import datetime, time as dtime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from market_read import volume_profile  # noqa: E402
from tournament import load_all  # noqa: E402

OUT = os.path.join(HERE, "poc_test_results.md")
BIN = 0.00065
MIN_DIST = 0.001
HURDLE_80, HURDLE_BOUNCE = 0.0010, 0.0003
SYMS = ("SPY", "QQQ", "IWM", "DIA")


def day_profiles(d):
    """{يوم: (poc, vah, val)} من شموع اليوم كاملة."""
    out = {}
    for day, g in d.groupby(d.index.date):
        if len(g) < 60:
            continue
        p = volume_profile(g, float(g["Close"].iloc[0]) * BIN)
        if p:
            out[day] = p
    return out


def analyze_symbol(d):
    """صفوف لكل يوم: magnet (لمس POC، لمس المرآة)، rule80 (عائد، نجاح)، bounce (عائد 30 و60)."""
    prof = day_profiles(d)
    days = sorted(prof)
    rows = []
    for i in range(1, len(days)):
        day, prev = days[i], days[i - 1]
        g = d[d.index.date == day]
        if len(g) < 70:
            continue
        poc, vah, val = prof[prev]
        o = float(g["Open"].iloc[0])
        r = {"day": day}
        # (1) المغناطيس
        body = g[(g.index.time >= dtime(9, 35)) & (g.index.time < dtime(15, 55))]
        if abs(o / poc - 1) > MIN_DIST:
            mirror = 2 * o - poc
            r["touch_poc"] = int(((body["Low"] <= poc) & (body["High"] >= poc)).any())
            r["touch_mirror"] = int(((body["Low"] <= mirror) & (body["High"] >= mirror)).any())
        # (2) قاعدة الـ80%
        if o > vah or o < val:
            r30 = g.resample("30min", label="left", closed="left", offset="0min")["Close"].last().dropna()
            inside = (r30 <= vah) & (r30 >= val)
            for k in range(1, len(r30)):
                if inside.iloc[k] and inside.iloc[k - 1]:
                    t_in = r30.index[k] + pd.Timedelta(minutes=30)
                    if t_in.time() >= dtime(15, 30):
                        break
                    side = -1 if o > vah else 1                       # من فوق المنطقة → نحو VAL (هبوط)
                    target = val if side < 0 else vah
                    p0 = float(r30.iloc[k])
                    after = g[g.index >= t_in]
                    hit = after[(after["Low"] <= target) if side < 0 else (after["High"] >= target)]
                    if len(hit):
                        exit_px, ok = target, 1
                    else:
                        exit_px, ok = float(after["Close"].iloc[-1]) if len(after) else p0, 0
                    r["r80"] = side * (exit_px / p0 - 1)
                    r["ok80"] = ok
                    break
        # (3) الارتداد
        after10 = g[g.index.time >= dtime(10, 0)]
        if abs(o / poc - 1) > MIN_DIST and len(after10):
            touch = after10[(after10["Low"] <= poc) & (after10["High"] >= poc)]
            if len(touch):
                t = touch.index[0]
                side = 1 if o > poc else -1                           # قادماً من فوق → ارتداد للأعلى
                fut = g[g.index > t]["Close"]
                for h in (30, 60):
                    f = fut[fut.index <= t + pd.Timedelta(minutes=h)]
                    if len(f) and f.index[-1] >= t + pd.Timedelta(minutes=h - 5):
                        r[f"b{h}"] = side * (float(f.iloc[-1]) / poc - 1)
                        r[f"b{h}_raw"] = float(f.iloc[-1]) / poc - 1
        rows.append(r)
    return pd.DataFrame(rows)


def boot_mean(v, days, n=3000, seed=59):
    df = pd.DataFrame({"v": v, "d": days}).dropna()
    if len(df) < 20:
        return None
    S = df.groupby("d")["v"].sum().values
    N = df.groupby("d")["v"].size().values
    idx = np.random.default_rng(seed).integers(0, len(S), (n, len(S)))
    m = S[idx].sum(1) / N[idx].sum(1)
    return float(S.sum() / N.sum()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def vs_random(signed, raw, sims=10000, seed=61):
    v = np.array(signed)
    rand = (np.abs(np.array(raw))[None, :] * np.random.default_rng(seed).choice([-1, 1], (sims, len(v)))).mean(1)
    return float(v.mean()), float((rand >= v.mean()).mean()), float((v > 0).mean())


def halves(df):
    days = sorted(df["day"])
    mid = days[len(days) // 2]
    return df[df["day"] < mid], df[df["day"] >= mid], mid


def section(df, name, primary):
    A, B, mid = halves(df)
    L, verdicts = [], []
    for part_name, P in (("الاكتشاف", A), ("الحكم", B)):
        m = P.dropna(subset=["touch_poc"]) if "touch_poc" in P else P.iloc[0:0]
        if len(m):
            diff = (m["touch_poc"] - m["touch_mirror"]).values
            s = boot_mean(diff, m["day"].values)
            L.append(f"| {name} | المغناطيس | {part_name} | {len(m)} يوماً | POC {100 * m['touch_poc'].mean():.0f}% مقابل المرآة {100 * m['touch_mirror'].mean():.0f}% | "
                     f"{'-' if not s else f'{100 * s[0]:+.1f} نقطة [{100 * s[1]:+.1f}…{100 * s[2]:+.1f}]'} |")
            if primary and part_name == "الحكم" and s:
                verdicts.append(f"⚖️ H-POC (1) المغناطيس: {'تحققت' if s[1] > 0 else 'لم تتحقق'} (فرق الملامسة {100 * s[0]:+.1f} نقطة)")
        q = P.dropna(subset=["r80"]) if "r80" in P else P.iloc[0:0]
        if len(q):
            s = boot_mean(q["r80"].values, q["day"].values)
            L.append(f"| {name} | قاعدة 80% | {part_name} | {len(q)} يوماً | نجاح {100 * q['ok80'].mean():.0f}% (الادعاء 80%) | "
                     f"{'-' if not s else f'{100 * s[0]:+.3f}% [{100 * s[1]:+.3f}…{100 * s[2]:+.3f}]'} |")
            if primary and part_name == "الحكم" and s:
                verdicts.append(f"⚖️ H-POC (2) قاعدة 80%: {'تحققت' if s[1] > 0 else 'لم تتحقق'} (نجاح {100 * q['ok80'].mean():.0f}%، عائد {100 * s[0]:+.3f}%؛ "
                                f"فوق حاجز 0.10%؟ {'نعم' if s[0] > HURDLE_80 else 'لا'})")
        b = P.dropna(subset=["b30"]) if "b30" in P else P.iloc[0:0]
        if len(b) >= 20:
            m30, p, hit = vs_random(b["b30"].values, b["b30_raw"].values)
            b60 = b["b60"].dropna()
            L.append(f"| {name} | الارتداد | {part_name} | {len(b)} ملامسة | ارتد {100 * hit:.0f}%، عشوائي أفضل منه {100 * p:.1f}% | "
                     f"30د {100 * m30:+.3f}%، 60د {100 * b60.mean():+.3f}% |")
            if primary and part_name == "الحكم":
                verdicts.append(f"⚖️ H-POC (3) الارتداد: {'تحققت' if p < 0.05 and m30 > 0 else 'لم تتحقق'} (30د {100 * m30:+.3f}%، "
                                f"أفضل من {100 * (1 - p):.0f}% من العشوائي؛ فوق 0.03%؟ {'نعم' if m30 > HURDLE_BOUNCE else 'لا'})")
    return L, verdicts, mid


def main():
    data = {s: d for s, d in load_all().items() if s in SYMS}
    L = ["# H-POC: نقطة التحكم ومنطقة القيمة لليوم السابق (اختبار، Alpaca 5د منذ 2019)", "",
         "- POC ومنطقة القيمة (70%) بدالة بطاقة قراءة السوق. SPY أساسي، والباقي تكرار وصفي. المغناطيس يقارن بمستوى مرآة على المسافة نفسها.", "",
         "| الرمز | السؤال | الجزء | العينة | النتيجة | المتوسط وفاصل 95% |", "|---|---|---|---|---|---|"]
    verdicts = []
    for s in SYMS:
        if s not in data:
            continue
        df = analyze_symbol(data[s])
        rows, v, mid = section(df, s, s == "SPY")
        L += rows
        verdicts += v
        if s == "SPY":
            L.insert(3, f"- SPY: الاكتشاف قبل {mid} والحكم بعده.")
    L += [""] + verdicts
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
