#!/usr/bin/env python3
"""H-VPAV: بقية أفكار Volume Profile والـ Anchored VWAP — اختبار فقط (لا يغيّر أي بوت). بطلب المستخدم 30/10/2026 («الاثنين»).

البيانات: شموع 5د بالحجم الحقيقي منذ 2019. SPY أساسي (الحكم عليه)، وQQQ/IWM/DIA تكرار وصفي. الاكتشاف النصف الأول، والحكم النصف الثاني.
ملف الحجم: حجم كل شمعة على سعرها النموذجي (H+L+C)/3 بخانة 0.065% من السعر (كبطاقة قراءة السوق). منطقة القيمة 70%.
الاختبارات (مسجّلة قبل التشغيل؛ «الجهة» +1 صعود/−1 هبوط، والنتيجة عائد SPY في الجهة بعد المدة):
  VP1 أطراف منطقة القيمة: أول ملامسة لـVAH/VAL الأمس بعد 10:00؛ القادم من فوق يرتد للأعلى والقادم من تحت يرتد للأسفل. 30 دقيقة.
  VP2 سرعة العقد منخفضة الحجم: عند دخول السعر خانة حجمها أمس < 20% من حجم POC (LVN) مقابل خانة ≥ 80% (HVN): متوسط |عائد 30 دقيقة| أكبر في LVN.
      (سؤال سرعة لا اتجاه؛ الفرضية: نسبة LVN ÷ HVN > 1 بفاصل 95%.)
  VP3 POC غير الملموس: أقرب POC من الأيام العشرة السابقة لم يلمسه السعر بعد يومه → يلمسه السعر اليوم أكثر من المستوى المرآة على المسافة نفسها.
  VP4 هجرة POC: POC المتشكل عند 11:30 مقابل 10:30؛ صعوده = صعود. عائد من 11:30 حتى الإغلاق.
  AV1 الارتداد من Anchored VWAP: المراسي بقاعدة آلية: أعلى سعر 20 يوماً، أدنى سعر 20 يوماً، بداية الأسبوع، بداية الشهر.
      أول ملامسة بعد 10:00 قادماً من جهة الافتتاح → ارتداد نحو تلك الجهة. 30 دقيقة.
  AV2 جهة السعر من AVWAP عند 10:30 (فوقه صعود) → عائد حتى الإغلاق.
  AV3 الاستعادة: افتتاح تحت AVWAP ثم أول إغلاق 5د فوقه (أو العكس) → جهة العبور. 60 دقيقة.
  المقياس للاتجاه: الزيادة على اختيار عشوائي للجهة (العائد في الجهة − متوسط العائد الخام في المجموعة × متوسط الجهة)، ومعاينة بالأيام.
  الحكم (SPY، النصف الثاني): p < 0.05 بعد تصحيح Benjamini-Hochberg بمعدل 5% على كل الاختبارات معاً، وموجب في النصف الأول، وفوق حاجز التكلفة:
  0.03% لمدد 30–60 دقيقة و0.10% حتى الإغلاق (عقد SPY على السعر لليوم التالي). VP2 وVP3 بلا حاجز (سرعة/ملامسة، ليسا صفقة مباشرة).
  مراسي يوم الأرباح غير ممكنة (لا تاريخ أرباح محلي).
تشغيل: SPXBOT_DATA=/opt/spxbot/data python research/vp_avwap_test.py   المخرج: research/vp_avwap_test_results.md
"""
import os
import sys
from datetime import datetime, time as dtime, timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from tournament import load_all, bh  # noqa: E402

OUT = os.path.join(HERE, "vp_avwap_test_results.md")
BIN, LVN, HVN = 0.00065, 0.20, 0.80
SYMS = ("SPY", "QQQ", "IWM", "DIA")
HURDLE = {"30m": 0.0003, "60m": 0.0003, "close": 0.0010}
DIRECTIONAL = {"VP1": "30m", "VP4": "close", "AV1": "30m", "AV2": "close", "AV3": "60m"}


# ============================ ملف الحجم ============================
def profile(g):
    """(خانات Series حجم بفهرس الخانة، عرض الخانة، poc، vah، val)."""
    w = float(g["Close"].iloc[0]) * BIN
    tp = (g["High"] + g["Low"] + g["Close"]) / 3
    vol = g["Volume"].groupby((tp / w).round().astype(int)).sum().sort_index()
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
    return vol, w, keys[i] * w, keys[hi] * w, keys[lo] * w


def fwd(g, t, minutes=None):
    """إغلاق آخر شمعة انتهت عند t+minutes (أو إغلاق اليوم)، والسعر عند t."""
    p0s = g[g.index < t]["Close"]
    if p0s.empty:
        return None
    p0 = float(p0s.iloc[-1])
    if minutes is None:
        return float(g["Close"].iloc[-1]) / p0 - 1
    end = t + timedelta(minutes=minutes)
    s = g[(g.index < end)]["Close"]
    if s.empty or s.index[-1] < end - timedelta(minutes=10):
        return None
    return float(s.iloc[-1]) / p0 - 1


def first_touch(g, level, start=dtime(10, 0)):
    x = g[(g.index.time >= start) & (g.index.time < dtime(15, 30))]
    hit = x[(x["Low"] <= level) & (x["High"] >= level)]
    return None if hit.empty else hit.index[0] + timedelta(minutes=5)


def avwap(hist, anchor_t):
    """AVWAP من شمعة المرساة حتى كل شمعة بعدها: Series."""
    h = hist[hist.index >= anchor_t]
    tp = (h["High"] + h["Low"] + h["Close"]) / 3
    return (tp * h["Volume"]).cumsum() / h["Volume"].cumsum().replace(0, np.nan)


# ============================ الأحداث لكل رمز ============================
def analyze(d):
    days = sorted(set(d.index.date))
    by = {x: g for x, g in d.groupby(d.index.date)}
    prof = {x: profile(by[x]) for x in days if len(by[x]) >= 60}
    rows = []
    for i, day in enumerate(days):
        if i < 21 or day not in by or len(by[day]) < 70:
            continue
        g = by[day]
        prev = days[i - 1]
        if prev not in prof:
            continue
        o = float(g["Open"].iloc[0])
        vol, w, poc, vah, val = prof[prev]
        # VP1
        for lvl in (vah, val):
            t = first_touch(g, lvl)
            if t is not None:
                side = 1 if o > lvl else -1
                r = fwd(g, t, 30)
                if r is not None:
                    rows.append({"day": day, "test": "VP1", "side": side, "raw": r, "h": "30m"})
        # VP2: أول دخول لخانة LVN ولخانة HVN بعد 10:00
        peak = vol.max()
        cls = {k: ("L" if v < LVN * peak else "H" if v >= HVN * peak else None) for k, v in vol.items()}
        seen = set()
        x = g[(g.index.time >= dtime(10, 0)) & (g.index.time < dtime(15, 30))]
        for t, r_ in x.iterrows():
            k = int(round(((r_["High"] + r_["Low"] + r_["Close"]) / 3) / w))
            c = cls.get(k)
            if c and c not in seen:
                seen.add(c)
                rr = fwd(g, t + timedelta(minutes=5), 30)
                if rr is not None:
                    rows.append({"day": day, "test": "VP2", "node": c, "abs": abs(rr)})
            if len(seen) == 2:
                break
        # VP3: أقرب POC غير ملموس من الأيام العشرة السابقة
        naked = []
        for j in range(max(0, i - 10), i):
            dj = days[j]
            if dj not in prof:
                continue
            pj = prof[dj][2]
            later = pd.concat([by[days[k]] for k in range(j + 1, i)]) if j + 1 < i else None
            if later is None or not ((later["Low"] <= pj) & (later["High"] >= pj)).any():
                naked.append(pj)
        naked = [p for p in naked if abs(p / o - 1) > 0.001]
        if naked:
            p = min(naked, key=lambda z: abs(z - o))
            body = g[(g.index.time >= dtime(9, 35)) & (g.index.time < dtime(15, 55))]
            mirror = 2 * o - p
            rows.append({"day": day, "test": "VP3", "touch": int(((body["Low"] <= p) & (body["High"] >= p)).any()),
                         "touch_m": int(((body["Low"] <= mirror) & (body["High"] >= mirror)).any())})
        # VP4: هجرة POC المتشكل
        g1, g2 = g[g.index.time < dtime(10, 30)], g[g.index.time < dtime(11, 30)]
        if len(g1) >= 10 and len(g2) >= 20:
            p1, p2 = profile(g1)[2], profile(g2)[2]
            if p2 != p1:
                t = datetime.combine(day, dtime(11, 30))
                r = fwd(g, t)
                if r is not None:
                    rows.append({"day": day, "test": "VP4", "side": 1 if p2 > p1 else -1, "raw": r, "h": "close"})
        # المراسي
        hist = pd.concat([by[days[k]] for k in range(i - 20, i + 1)])
        before = hist[hist.index < datetime.combine(day, dtime(9, 30))]
        week0 = next((days[k] for k in range(i, -1, -1) if days[k].isocalendar()[1] != day.isocalendar()[1]), None)
        anchors = {"قمة 20 يوماً": before["High"].idxmax(), "قاع 20 يوماً": before["Low"].idxmin()}
        wk = [x for x in days[max(0, i - 6):i + 1] if x.isocalendar()[1] == day.isocalendar()[1]]
        if wk and wk[0] != day:
            anchors["بداية الأسبوع"] = by[wk[0]].index[0]
        mo = [x for x in days[max(0, i - 25):i + 1] if x.month == day.month]
        if mo and mo[0] != day:
            anchors["بداية الشهر"] = by[mo[0]].index[0]
        for name, at in anchors.items():
            av = avwap(hist, at)
            av_today = av[av.index.date == day]
            av_open = av[av.index < datetime.combine(day, dtime(9, 30))]
            if av_today.empty or av_open.empty:
                continue
            lvl_open = float(av_open.iloc[-1])
            side_open = 1 if o > lvl_open else -1
            # AV1: أول ملامسة بعد 10:00
            x = g[(g.index.time >= dtime(10, 0)) & (g.index.time < dtime(15, 30))]
            avx = av_today.reindex(x.index)
            hit = x[(x["Low"] <= avx) & (x["High"] >= avx)]
            if len(hit):
                r = fwd(g, hit.index[0] + timedelta(minutes=5), 30)
                if r is not None:
                    rows.append({"day": day, "test": "AV1", "anchor": name, "side": side_open, "raw": r, "h": "30m"})
            # AV2: الجهة عند 10:30
            t = datetime.combine(day, dtime(10, 30))
            a_t = av_today[av_today.index < t]
            c_t = g[g.index < t]["Close"]
            if len(a_t) and len(c_t):
                r = fwd(g, t)
                if r is not None:
                    rows.append({"day": day, "test": "AV2", "anchor": name, "side": 1 if float(c_t.iloc[-1]) > float(a_t.iloc[-1]) else -1, "raw": r, "h": "close"})
            # AV3: الاستعادة/الكسر
            closes = g["Close"]
            cross = closes[(np.sign(closes - av_today.reindex(closes.index)) == -side_open) & (closes.index.time < dtime(15, 0))]
            if len(cross):
                tc = cross.index[0] + timedelta(minutes=5)
                r = fwd(g, tc, 60)
                if r is not None:
                    rows.append({"day": day, "test": "AV3", "anchor": name, "side": -side_open, "raw": r, "h": "60m"})
    return pd.DataFrame(rows)


# ============================ الإحصاء ============================
def excess(df):
    """الزيادة على عشوائي بالمزيج نفسه: جهة × (العائد − متوسط العائد الخام في المجموعة)."""
    return df["side"] * (df["raw"] - df["raw"].mean())


def boot_p(v, days, n=3000, seed=67):
    x = pd.DataFrame({"v": v, "d": days}).dropna()
    if x["d"].nunique() < 20:
        return None, None
    S, N = x.groupby("d")["v"].sum().values, x.groupby("d")["v"].size().values
    idx = np.random.default_rng(seed).integers(0, len(S), (n, len(S)))
    m = S[idx].sum(1) / N[idx].sum(1)
    return float(S.sum() / N.sum()), float((m <= 0).mean())


def evaluate(df):
    """{(اختبار، مرساة): {الاكتشاف/الحكم: (متوسط، p، عدد)}}."""
    days = sorted(df["day"].unique())
    mid = days[len(days) // 2]
    res = {}
    keys = [(t, None) for t in ("VP1", "VP4")] + [(t, a) for t in ("AV1", "AV2", "AV3") for a in df.get("anchor", pd.Series(dtype=str)).dropna().unique()]
    for test, anchor in keys:
        g = df[(df["test"] == test) & ((df["anchor"] == anchor) if anchor else True)]
        if g.empty:
            continue
        out = {}
        for part, sub in (("A", g[g["day"] < mid]), ("B", g[g["day"] >= mid])):
            m, p = boot_p(excess(sub).values, sub["day"].values)
            out[part] = (m, p, len(sub))
        res[(test, anchor)] = out
    for test, col in (("VP2", None), ("VP3", None)):
        g = df[df["test"] == test]
        if g.empty:
            continue
        out = {}
        for part, sub in (("A", g[g["day"] < mid]), ("B", g[g["day"] >= mid])):
            if test == "VP2":
                L, H = sub[sub["node"] == "L"]["abs"], sub[sub["node"] == "H"]["abs"]
                if len(L) < 20 or len(H) < 20:
                    out[part] = (None, None, len(sub))
                    continue
                ratio = L.mean() / H.mean()
                rng = np.random.default_rng(71)
                bs = [rng.choice(L.values, len(L)).mean() / rng.choice(H.values, len(H)).mean() for _ in range(2000)]
                out[part] = (float(ratio), float((np.array(bs) <= 1).mean()), len(sub))
            else:
                v = (sub["touch"] - sub["touch_m"]).values
                m, p = boot_p(v, sub["day"].values)
                out[part] = (m, p, len(sub))
        res[(test, None)] = out
    return res, mid


NAMES = {"VP1": "VP1 أطراف منطقة القيمة (ارتداد 30د)", "VP2": "VP2 سرعة LVN ÷ HVN (|حركة 30د|)", "VP3": "VP3 POC غير الملموس (ملامسة − مرآة)",
         "VP4": "VP4 هجرة POC (11:30 → الإغلاق)", "AV1": "AV1 ارتداد من AVWAP (30د)", "AV2": "AV2 الجهة من AVWAP عند 10:30 (→ الإغلاق)",
         "AV3": "AV3 استعادة/كسر AVWAP (60د)"}


def fmt(test, m):
    if m is None:
        return "-"
    if test == "VP2":
        return f"×{m:.2f}"
    if test == "VP3":
        return f"{100 * m:+.1f} نقطة"
    return f"{100 * m:+.3f}%"


def report(all_res, meta):
    L = ["# H-VPAV: بقية Volume Profile والـ Anchored VWAP (اختبار، Alpaca 5د منذ 2019)", "", f"- {meta}",
         "- SPY أساسي والحكم عليه (النصف الثاني، BH 5% على كل الاختبارات، موجب في النصف الأول، فوق حاجز التكلفة للصفقات). الباقي تكرار وصفي.", "",
         "| الرمز | الاختبار | المرساة | الاكتشاف | p | الحكم | p | العينة (الحكم) |", "|---|---|---|---|---|---|---|---|"]
    for sym, (res, _) in all_res.items():
        for (test, anchor), out in res.items():
            a, b = out.get("A", (None, None, 0)), out.get("B", (None, None, 0))
            L.append(f"| {sym} | {NAMES[test]} | {anchor or '-'} | {fmt(test, a[0])} | {'-' if a[1] is None else f'{a[1]:.3f}'} | "
                     f"{fmt(test, b[0])} | {'-' if b[1] is None else f'{b[1]:.3f}'} | {b[2]} |")
    res, _ = all_res.get("SPY", ({}, None))
    keys = [k for k, o in res.items() if o.get("B", (None, None))[1] is not None]
    passed = bh(np.array([res[k]["B"][1] for k in keys])) if keys else []
    L += ["", "## الحكم (SPY)", ""]
    for k, ok in zip(keys, passed):
        test, anchor = k
        a, b = res[k].get("A", (None,)), res[k]["B"]
        sig = bool(ok) and b[0] is not None and (b[0] > (1 if test == "VP2" else 0)) and a[0] is not None and (a[0] > (1 if test == "VP2" else 0))
        cost = test not in DIRECTIONAL or (b[0] is not None and b[0] > HURDLE[DIRECTIONAL[test]])
        L.append(f"⚖️ {NAMES[test]}{' — ' + anchor if anchor else ''}: " + ("تحققت" + ("" if cost else " لكن دون حاجز التكلفة") if sig else "لم تتحقق")
                 + f" (الحكم {fmt(test, b[0])}، p {b[1]:.3f})")
    return L


def main():
    data = {s: d for s, d in load_all().items() if s in SYMS}
    all_res = {}
    for s in SYMS:
        if s in data:
            df = analyze(data[s])
            all_res[s] = evaluate(df)
            print("انتهى", s, len(df))
    mid = all_res.get("SPY", (None, "-"))[1]
    L = report(all_res, f"الرموز: {'، '.join(all_res)}؛ SPY: الاكتشاف قبل {mid}")
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(HERE))
    sys.exit(main())
