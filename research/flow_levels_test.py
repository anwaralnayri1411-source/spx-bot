#!/usr/bin/env python3
"""H-FLOWLVL: دمج Volume Profile وPOC والأوردر فلو بإعدادات متداولي التدفق (اختبار فقط). بطلب المستخدم 30/10/2026.

البيانات: ملفات الأوردر فلو بالدقيقة (orderflow_<SYM>_1m.csv.gz: شراء/بيع عدواني بقاعدة التيك، وإغلاق). ملف الحجم لليوم السابق من الحجم
الكلي بالدقيقة على سعر الإغلاق، بخانة 0.065% من السعر. المستويات: POC وVAH وVAL للأمس، وأقرب POC غير ملموس من أيام العينة السابقة.
الإعدادات (مسجّلة قبل التشغيل؛ لكل إعداد نسختان: «مع شرط التدفق» و«بدونه» على الأحداث نفسها):
  S1 الامتصاص: أول ملامسة بعد 10:00 لأي مستوى من الأربعة قادماً من جهة الافتتاح. الشرط: في الدقائق الخمس حتى الملامسة،
     اختلال ضد جهة الارتداد ≥ المئين 75 لـ|اختلال 5 دقائق| في السهم، مع حركة سعر فيها < 0.05% (بيع عدواني لا يُنزل السعر).
     الصفقة: جهة الارتداد، عائد 30 دقيقة.
  S2 قبول منطقة القيمة: افتتاح خارج منطقة قيمة الأمس، ثم أول إغلاق 5 دقائق داخلها قبل 14:30. الشرط: اختلال تلك الدقائق الخمس في جهة الدخول
     ≥ المئين 75. الصفقة: نحو الطرف المقابل حتى لمسه أو الإغلاق.
  S3 اختراق المنطقة قليلة التداول: أول دخول بعد 10:00 لخانة حجمها أمس < 20% من خانة POC. الشرط: اختلال 5 دقائق في جهة الحركة (آخر 5 دقائق)
     ≥ المئين 75. الصفقة: جهة الحركة، عائد 30 دقيقة.
  المقياس: الزيادة على عشوائي بالمزيج نفسه (إزالة متوسط كل رمز)، ومعاينة بالأيام. الفرضية لكل إعداد: «مع الشرط» > 0 وأعلى من «بدونه».
  الحكم: BH بمعدل 5% على الثلاثة (قيمة p لـ«مع الشرط»)، ثم الحاجز: SPY 0.03% (30د) و0.10% (S2)؛ الشركات 0.25% و0.30%.
تحفظ مسبق: العينة نحو 40 يوماً لكل رمز؛ أحداث «مع الشرط» قد تكون عشرات فقط، فالنتيجة مؤشر أولي لا حكم نهائي.
تشغيل: SPXBOT_DATA=/opt/spxbot/data python research/flow_levels_test.py [--syms SPY,DIS,...]   المخرج: research/flow_levels_test_results.md
"""
import os
import sys
from datetime import datetime, time as dtime, timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from orderflow_test import load, imbalance, px_at, ret  # noqa: E402
from tournament import bh  # noqa: E402

DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
OUT = os.path.join(HERE, "flow_levels_test_results.md")
BIN, LVN = 0.00065, 0.20
Q, QUIET = 75, 0.0005
DEFAULT = "SPY,DIS,UBER,SOFI,HOOD,INTC,BA,PFE,XOM,JPM,WMT"
HURDLE = {"SPY": {"S1": 0.0003, "S2": 0.0010, "S3": 0.0003}, "stk": {"S1": 0.0025, "S2": 0.0030, "S3": 0.0025}}


def day_profile(g):
    w = float(g["close"].iloc[0]) * BIN
    vol = (g["buy"] + g["sell"]).groupby((g["close"] / w).round().astype(int)).sum().sort_index()
    keys = list(vol.index)
    i = keys.index(vol.idxmax())
    lo = hi = i
    tot, acc = vol.sum(), vol.iloc[i]
    while acc < 0.7 * tot and (lo > 0 or hi < len(keys) - 1):
        a = vol.iloc[lo - 1] if lo > 0 else -1
        b = vol.iloc[hi + 1] if hi < len(keys) - 1 else -1
        if b >= a:
            hi += 1
            acc += b
        else:
            lo -= 1
            acc += a
    return {"vol": vol, "w": w, "poc": keys[i] * w, "vah": keys[hi] * w, "val": keys[lo] * w}


def q_imb(d, q=Q):
    v = []
    for _, g in d.groupby(d.index.date):
        r = g.resample("5min")[["buy", "sell"]].sum()
        tot = r["buy"] + r["sell"]
        v += list(((r["buy"] - r["sell"]) / tot.replace(0, np.nan)).abs().dropna())
    return float(np.percentile(v, q)) if v else 0.3


def analyze(d, sym):
    thr = q_imb(d)
    days = sorted(set(d.index.date))
    by = {x: g for x, g in d.groupby(d.index.date)}
    prof = {x: day_profile(by[x]) for x in days if len(by[x]) >= 300}
    rows = []
    for i, day in enumerate(days):
        if i == 0 or days[i - 1] not in prof or len(by[day]) < 300:
            continue
        g, P = by[day], prof[days[i - 1]]
        o = float(g["close"].iloc[0])
        naked = []
        for j in range(max(0, i - 10), i - 1):
            if days[j] in prof:
                pj = prof[days[j]]["poc"]
                later = pd.concat([by[days[k]] for k in range(j + 1, i)])
                if not (((later["close"] - pj).abs() / pj) < BIN).any():
                    naked.append(pj)
        levels = [P["poc"], P["vah"], P["val"]] + ([min(naked, key=lambda z: abs(z - o))] if naked else [])
        body = g[(g.index.time >= dtime(10, 0)) & (g.index.time < dtime(15, 30))]
        # S1
        for L in levels:
            if abs(o / L - 1) <= 0.001:
                continue
            side = 1 if o > L else -1
            hit = body[((body["close"] - L) * side) <= 0]
            if hit.empty:
                continue
            t = hit.index[0] + timedelta(minutes=1)
            im = imbalance(g, t)
            p0, p5 = px_at(g, t), px_at(g, t - timedelta(minutes=5))
            r = ret(g, t, 30)
            if None in (im, p0, p5, r):
                continue
            cond = (-side * im) >= thr and abs(p0 / p5 - 1) < QUIET
            rows.append({"sym": sym, "day": day, "setup": "S1", "cond": cond, "side": side, "raw": r})
        # S2
        if o > P["vah"] or o < P["val"]:
            side = -1 if o > P["vah"] else 1
            r5 = g["close"].resample("5min").last().dropna()
            inside = r5[(r5 <= P["vah"]) & (r5 >= P["val"]) & (r5.index.time < dtime(14, 30))]
            if len(inside):
                t = inside.index[0] + timedelta(minutes=5)
                im = imbalance(g, t)
                target = P["val"] if side < 0 else P["vah"]
                after = g[g.index >= t]
                p0 = px_at(g, t)
                if im is not None and p0 is not None and len(after):
                    hit = after[((after["close"] - target) * side) >= 0]
                    exitp = target if len(hit) else float(after["close"].iloc[-1])
                    rows.append({"sym": sym, "day": day, "setup": "S2", "cond": side * im >= thr, "side": side, "raw": exitp / p0 - 1})
        # S3
        peak = P["vol"].max()
        lvn = {k for k, v in P["vol"].items() if v < LVN * peak}
        for t, rr in body.iterrows():
            if int(round(rr["close"] / P["w"])) in lvn:
                t1 = t + timedelta(minutes=1)
                p0, p5 = px_at(g, t1), px_at(g, t1 - timedelta(minutes=5))
                im = imbalance(g, t1)
                r = ret(g, t1, 30)
                if None not in (p0, p5, im, r) and p0 != p5:
                    side = 1 if p0 > p5 else -1
                    rows.append({"sym": sym, "day": day, "setup": "S3", "cond": side * im >= thr, "side": side, "raw": r})
                break
    return pd.DataFrame(rows)


def stats(sub, n=3000, seed=79):
    if sub.empty or sub["day"].nunique() < 8:
        return None
    mu = sub.groupby("sym")["raw"].transform("mean")
    x = sub["side"] * (sub["raw"] - mu)
    S, N = x.groupby(sub["day"]).sum().values, x.groupby(sub["day"]).size().values
    idx = np.random.default_rng(seed).integers(0, len(S), (n, len(S)))
    m = S[idx].sum(1) / N[idx].sum(1)
    return {"n": len(sub), "mean": float(S.sum() / N.sum()), "p": float((m <= 0).mean()), "hit": float(((sub["side"] * sub["raw"]) > 0).mean())}


NAMES = {"S1": "S1 الامتصاص عند المستوى (30د)", "S2": "S2 قبول منطقة القيمة مع ضغط (حتى الطرف أو الإغلاق)", "S3": "S3 اختراق المنطقة قليلة التداول مع ضغط (30د)"}


def report(df, meta):
    L = ["# H-FLOWLVL: Volume Profile + POC + الأوردر فلو بإعدادات متداولي التدفق (اختبار)", "", f"- {meta}",
         "- لكل إعداد: «مع شرط التدفق» مقابل «بدونه» على الأحداث نفسها. الزيادة على عشوائي بالمزيج نفسه. العينة صغيرة: مؤشر أولي.", ""]
    if df.empty:
        return L + ["لا أحداث."]
    verdicts, prim = [], []
    for grp, sub_all in (("SPY", df[df["sym"] == "SPY"]), ("الشركات", df[df["sym"] != "SPY"])):
        if sub_all.empty:
            continue
        L += [f"## {grp}", "", "| الإعداد | النسخة | أحداث | الزيادة | p | في الجهة الصحيحة |", "|---|---|---|---|---|---|"]
        for k in ("S1", "S2", "S3"):
            for c, name in ((True, "مع شرط التدفق"), (False, "بدونه")):
                s = stats(sub_all[(sub_all["setup"] == k) & (sub_all["cond"] == c)])
                if not s:
                    L.append(f"| {NAMES[k]} | {name} | {int(((sub_all['setup'] == k) & (sub_all['cond'] == c)).sum())} | - | - | - |")
                    continue
                L.append(f"| {NAMES[k]} | {name} | {s['n']} | {100 * s['mean']:+.3f}% | {s['p']:.3f} | {100 * s['hit']:.0f}% |")
                if c:
                    w = stats(sub_all[(sub_all["setup"] == k) & (~sub_all["cond"])])
                    prim.append((grp, k, s, w))
        L.append("")
    if prim:
        passed = bh(np.array([x[2]["p"] for x in prim]), 0.05)
        for (grp, k, s, w), ok in zip(prim, passed):
            better = w is None or s["mean"] > w["mean"]
            hz = HURDLE["SPY" if grp == "SPY" else "stk"][k]
            good = bool(ok) and s["mean"] > 0 and better
            verdicts.append(f"⚖️ {grp} — {NAMES[k]}: " + (("تحققت" + ("" if s["mean"] > hz else f" لكن دون حاجز التكلفة {100 * hz:.2f}%")) if good else "لم تتحقق")
                            + f" (مع الشرط {100 * s['mean']:+.3f}%" + ("" if w is None else f"، بدونه {100 * w['mean']:+.3f}%") + f"، p {s['p']:.3f})")
    return L + verdicts


def main(argv):
    syms = (argv[argv.index("--syms") + 1] if "--syms" in argv else DEFAULT).split(",")
    parts, have = [], []
    for s in syms:
        p = os.path.join(DATA, f"orderflow_{s}_1m.csv.gz")
        if os.path.exists(p):
            parts.append(analyze(load(p), s))
            have.append(s)
    df = pd.concat(parts) if parts else pd.DataFrame()
    L = report(df, f"الرموز: {'، '.join(have)}؛ أحداث: {len(df)}")
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
