#!/usr/bin/env python3
"""دراسة فخ ارتداد الافتتاح (H-OPENREV، مسجّلة مسبقاً في CLAUDE.md 27/11/2026). قراءة فقط، لا تغيّر البوت.

المرحلة 1: تكرار أيام الفخ وأيام الاتجاه، وأداء أربعة كواشف (D1 زخم في الطرف، D2 VWAP، D3 كسر هيكل، D4 انقلاب M5).
المرحلة 2: v9 تقريبية على شموع SPY 5د مع حمايتين (G1 منع الدخول 30د، G2 إغلاق الكل) لكل كاشف، مقابل G0.
تشغيل (الخادم): SPXBOT_DATA=/opt/spxbot/data python research/openrev_study.py
"""
import os
import sys
from datetime import time as dtime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
SRC = os.path.join(DATA, "spy_5m.csv.gz")
OUT_MD = os.path.join(HERE, "openrev_results.md")
LAB = os.path.join(DATA, "mgt_lab", "trades.csv")

MOVE, RETR = 0.005, 0.5                     # فخ/اتجاه: حركة ≥0.5% حتى 10:15، وارتداد ≥50% قبل 11:00
TP, SL = 0.0045, 0.0020                     # معايرة s0: هدف ≈+34 نقطة، وقف ≈−14 نقطة
WIN, LOSS = 500.0, -300.0
LADDER, CAP, SCORE, ADX_MIN = 0.00143, 12, 3, 15
LAST_ENTRY, CLOSE = dtime(15, 30), dtime(15, 55)
BLOCK_BARS = 6
DETS = ("D1", "D2", "D3", "D4")
DET_NAMES = {"D1": "زخم في الطرف", "D2": "عودة عبر VWAP", "D3": "كسر هيكل MSS", "D4": "انقلاب M5 والبطيء معنا"}
ALPHA = 2 / 21


# ---------------------------- المؤشرات ----------------------------
def load(path=SRC):
    df = pd.read_csv(path)
    df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert("America/New_York")
    return df.set_index("t").sort_index()


def vote(c, e, e_back):
    up = (c > e) & (e > e_back)
    dn = (c < e) & (e < e_back)
    return np.where(up, 1, np.where(dn, -1, 0))


def htf_vote(df, minutes):
    """صوت فريم أعلى كما في القراءة الحية: الشمعة الجارية جزئية (إغلاقها = السعر الآن)."""
    b = df.index.floor(f"{minutes}min")
    g = df["c"].groupby(b).last()
    E = g.ewm(span=20, adjust=False).mean()
    pos = pd.Index(g.index).get_indexer(b)
    prev = E.shift(1).values[pos]
    back = E.shift(3).values[pos]
    e_now = prev * (1 - ALPHA) + ALPHA * df["c"].values
    return vote(df["c"].values, e_now, back)


def adx_series(df, n=14):
    h, l, c = df["h"], df["l"], df["c"]
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    ndm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pdm.ewm(alpha=1 / n, adjust=False).mean() / atr
    ndi = 100 * ndm.ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def features(df):
    df = df.copy()
    e5 = df["c"].ewm(span=20, adjust=False).mean()
    df["M5"] = vote(df["c"].values, e5.values, e5.shift(3).values)
    df["M15"] = htf_vote(df, 15)
    df["M30"] = htf_vote(df, 30)
    df["adx"] = adx_series(df).values
    return df


def rth_days(df):
    tm = df.index.time
    r = df[(tm >= dtime(9, 30)) & (tm <= dtime(15, 55))]
    for d, g in r.groupby(r.index.date):
        if len(g) == 78:
            yield d, g


def day_frame(g):
    """إضافات الجلسة: الافتتاح وVWAP والضغط وطرفا اليوم والقمم والقيعان المتأرجحة."""
    g = g.copy()
    o = float(g["o"].iloc[0])
    vw = (g["c"] * g["v"]).cumsum() / g["v"].cumsum().replace(0, np.nan)
    g["vwap"] = vw
    s = np.sign(g["c"] - o) + np.sign(g["c"] - vw)
    g["sess"] = np.sign(s).astype(int)
    g["press"] = np.sign((np.sign(g["c"] - g["o"]) * g["v"]).rolling(6, min_periods=1).sum()).astype(int)
    g["score"] = g["M5"] + g["M15"] + g["M30"] + g["sess"] + g["press"]
    g["hi"], g["lo"] = g["h"].cummax(), g["l"].cummin()
    g["r30"] = g["c"] / g["c"].shift(6) - 1
    h, l = g["h"].values, g["l"].values
    n = len(g)
    sh, sl = np.full(n, np.nan), np.full(n, np.nan)          # آخر قمة/قاع متأرجح مؤكد حتى الشمعة i
    last_h = last_l = np.nan
    for i in range(n):
        j = i - 2
        if j >= 2:
            if h[j] > max(h[j - 2], h[j - 1], h[j + 1], h[j + 2]):
                last_h = h[j]
            if l[j] < min(l[j - 2], l[j - 1], l[j + 1], l[j + 2]):
                last_l = l[j]
        sh[i], sl[i] = last_h, last_l
    g["swh"], g["swl"] = sh, sl
    return g


def detect(g, i, d):
    """هل أطلق كاشف عند إغلاق الشمعة i ضد جهة d (+1 CALL، −1 PUT)؟ يرجع مجموعة الكواشف."""
    r = g.iloc[i]
    out = set()
    c = r["c"]
    ext = r["hi"] if d > 0 else r["lo"]
    if r["r30"] == r["r30"] and d * r["r30"] >= 0.0025 and abs(c / ext - 1) <= 0.0005:
        out.add("D1")
    if i > 0:
        pc, pv = g["c"].iloc[i - 1], g["vwap"].iloc[i - 1]
        if d * (pc - pv) >= 0 and d * (c - r["vwap"]) < 0:
            out.add("D2")
        sw, psw = (r["swl"], g["swl"].iloc[i - 1]) if d > 0 else (r["swh"], g["swh"].iloc[i - 1])
        if sw == sw and d * (c - sw) < 0 and d * (pc - psw if psw == psw else 1) >= 0:
            out.add("D3")
    if r["M5"] == -d and (r["M15"] == d or r["M30"] == d):
        out.add("D4")
    return out


# ---------------------------- المرحلة 1 ----------------------------
def classify(g):
    """(النوع، الاتجاه، موضع الطرف، موضع ارتداد 50%) — النوع: trap أو trend أو None."""
    o = float(g["o"].iloc[0])
    tm = g.index.time
    w = g[tm <= dtime(10, 10)]                              # الشموع التي تنتهي حتى 10:15
    up, dn = w["h"].max() / o - 1, 1 - w["l"].min() / o
    if max(up, dn) < MOVE:
        return None, 0, None, None
    d = 1 if up >= dn else -1
    k = int(np.argmax(w["h"].values) if d > 0 else np.argmin(w["l"].values))
    ext = w["h"].iloc[k] if d > 0 else w["l"].iloc[k]
    lvl = o + (ext - o) * (1 - RETR)
    ret = None
    for i in range(k + 1, len(g)):
        if g.index[i].time() >= dtime(11, 0):
            break
        if (g["l"].iloc[i] <= lvl) if d > 0 else (g["h"].iloc[i] >= lvl):
            ret = i
            break
    if ret is not None:
        return "trap", d, k, ret
    c1015 = float(w["c"].iloc[-1])
    if d * (float(g["c"].iloc[-1]) - c1015) > 0:
        return "trend", d, k, None
    return "other", d, k, None


def stage1(days):
    rows = []
    for d, g in days:
        kind, dr, k, ret = classify(g)
        if kind not in ("trap", "trend"):
            rows.append({"day": d, "kind": kind or "quiet"})
            continue
        row = {"day": d, "kind": kind}
        for D in DETS:
            row[D] = None
        lo = g.index.time
        for i in range(len(g)):
            t = lo[i]
            if kind == "trap" and (i <= k or i > ret):
                continue
            if kind == "trend" and not (dtime(9, 45) <= t < dtime(11, 0)):
                continue
            for D in detect(g, i, dr):
                if row[D] is None:
                    row[D] = (ret - i) * 5 if kind == "trap" else 0
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------- المرحلة 2 ----------------------------
def close_value(mv):
    return float(np.clip(mv / TP * WIN if mv > 0 else mv / SL * -LOSS, LOSS, WIN))


def simulate(g, det=None, guard="G0"):
    """v9 تقريبية ليوم واحد: يرجع قائمة نتائج الصفقات بالدولار."""
    open_, done, entries = [], [], {1: [], -1: []}
    block = {1: -1, -1: -1}
    n = len(g)
    C, H, L = g["c"].values, g["h"].values, g["l"].values
    for i in range(n):
        t = g.index[i].time()
        # 1) خروج داخل الشمعة للصفقات المفتوحة من قبل
        still = []
        for e in open_:
            if e["i"] < i:
                fav = (H[i] if e["d"] > 0 else L[i]) / e["px"] - 1
                adv = (L[i] if e["d"] > 0 else H[i]) / e["px"] - 1
                if e["d"] * adv <= -SL:
                    done.append(LOSS); continue
                if e["d"] * fav >= TP:
                    done.append(WIN); continue
            still.append(e)
        open_ = still
        if t >= CLOSE:
            done += [LOSS] * len(open_)
            open_ = []
            break
        # 2) الكاشف ضد الجهة المفتوحة
        if det and open_:
            d = open_[0]["d"]
            if det in detect(g, i, d):
                if guard == "G1":
                    block[d] = i + BLOCK_BARS
                elif guard == "G2":
                    done += [close_value(d * (C[i] / e["px"] - 1)) for e in open_]
                    open_ = []
        # 3) الدخول
        if t > LAST_ENTRY:
            continue
        r = g.iloc[i]
        if r["adx"] != r["adx"] or r["adx"] < ADX_MIN or abs(r["score"]) < SCORE:
            continue
        d = 1 if r["score"] > 0 else -1
        if open_ and open_[0]["d"] != d:                    # القيد الشرعي: جهة واحدة
            continue
        if i < block[d]:
            continue
        mine = entries[d]
        if len(mine) >= CAP:
            continue
        if mine and d * (C[i] / mine[-1] - 1) < LADDER:
            continue
        mine.append(C[i])
        open_.append({"i": i, "d": d, "px": C[i]})
    return done


def stats(daily):
    s = pd.Series(daily, dtype=float)
    cum = s.cumsum()
    dd = float((cum - cum.cummax()).min()) if len(s) else 0.0
    return {"net": float(s.sum()), "worst": float(s.min()) if len(s) else 0.0, "mdd": dd}


def stage2(days, kinds):
    combos = [("G0", None)] + [(G, D) for D in DETS for G in ("G1", "G2")]
    res = {c: {} for c in combos}
    for d, g in days:
        for G, D in combos:
            res[(G, D)][d] = sum(simulate(g, D, G))
    halves = {"2019–2022": lambda d: d.year <= 2022, "2023–2026": lambda d: d.year >= 2023}
    out = []
    for c in combos:
        row = {"combo": c}
        for hn, f in halves.items():
            ds = sorted(x for x in res[c] if f(x))
            row[hn] = stats([res[c][x] for x in ds])
        row["trend"] = sum(v for x, v in res[c].items() if kinds.get(x) == "trend")
        row["trap"] = sum(v for x, v in res[c].items() if kinds.get(x) == "trap")
        out.append(row)
    return out, res


def verdict(rows):
    base = rows[0]
    ok = {}
    for r in rows[1:]:
        good = True
        for hn in ("2019–2022", "2023–2026"):
            a, b = r[hn], base[hn]
            floor = 0.9 * b["net"] if b["net"] > 0 else b["net"]
            good = good and a["worst"] > b["worst"] and a["mdd"] > b["mdd"] and a["net"] >= floor
        ok[r["combo"]] = bool(good)
    return ok


def lab_daily():
    try:
        t = pd.read_csv(LAB)
    except Exception:  # noqa: BLE001
        return {}
    t = t[(t["vid"] == "s0") & (t["sess"] == "rth") & t["usd"].notna()]
    return t.groupby("tdate")["usd"].sum().to_dict()


# ---------------------------- التقرير ----------------------------
def money(x):
    return f"{x:+,.0f}$"


def report(s1, rows, ok, res, lab):
    L = ["# فخ ارتداد الافتتاح (H-OPENREV) — نتائج آلية", "",
         "- شموع SPY 5د SIP، أيام الجلسة الكاملة. العقد تقريبي بالحركة (هدف +0.45% = +500$، وقف −0.20% = −300$، وإلا 15:55 = −300$)، "
         "معايَر من 86 صفقة s0 حقيقية. الأصوات 5 مكونات بدل 7 (لا M1 ولا التدفق تاريخياً).", ""]
    n = len(s1)
    cnt = s1["kind"].value_counts()
    L += ["## المرحلة 1: كم يتكرر الفخ", "", "| النوع | أيام | النسبة |", "|---|---|---|"]
    for k, nm in (("trap", "فخ (حركة ≥0.5% ثم ارتداد ≥50% قبل 11:00)"), ("trend", "اتجاه (بلا ارتداد، والإغلاق أبعد)"),
                  ("other", "حركة بلا ارتداد ولا استمرار"), ("quiet", "هادئ (<0.5% حتى 10:15)")):
        L.append(f"| {nm} | {cnt.get(k, 0)} | {cnt.get(k, 0) / n:.0%} |")
    tr, te = s1[s1["kind"] == "trap"], s1[s1["kind"] == "trend"]
    L += ["", "## الكواشف: من يلتقط الفخ مبكراً ومن يكذب في أيام الاتجاه", "",
          "| الكاشف | التقط الفخ قبل ارتداد 50% | وسيط الدقائق قبل الارتداد | إنذار كاذب في أيام الاتجاه (09:45–11:00) | الفارق |",
          "|---|---|---|---|---|"]
    for D in DETS:
        hit = tr[D].notna().mean() if len(tr) else 0
        med = tr[D].dropna().median() if tr[D].notna().any() else float("nan")
        fa = te[D].notna().mean() if len(te) else 0
        L.append(f"| {D} {DET_NAMES[D]} | {hit:.0%} | {med:.0f} | {fa:.0%} | {hit - fa:+.0%} |")
    L += ["", "## المرحلة 2: v9 تقريبية مع الحمايات", "",
          "| التركيبة | صافي 19–22 | أسوأ يوم | تراجع أقصى | صافي 23–26 | أسوأ يوم | تراجع أقصى | أيام الاتجاه | أيام الفخ | يحقق المعيار |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        G, D = r["combo"]
        nm = "G0 v9 كما هي" if G == "G0" else f"{G} {'منع 30د' if G == 'G1' else 'إغلاق الكل'} · {D} {DET_NAMES[D]}"
        a, b = r["2019–2022"], r["2023–2026"]
        flag = "—" if G == "G0" else ("✅" if ok[r["combo"]] else "❌")
        L.append(f"| {nm} | {money(a['net'])} | {money(a['worst'])} | {money(a['mdd'])} | {money(b['net'])} | {money(b['worst'])} | "
                 f"{money(b['mdd'])} | {money(r['trend'])} | {money(r['trap'])} | {flag} |")
    if lab:
        L += ["", "## تحقق المعايرة: أيام المختبر الحقيقية (s0) مقابل التقريب G0", "", "| اليوم | s0 الحقيقية | التقريب G0 |", "|---|---|---|"]
        g0 = res[("G0", None)]
        for d in sorted(lab):
            dd = pd.Timestamp(d).date()
            L.append(f"| {d} | {money(lab[d])} | {money(g0[dd]) if dd in g0 else '—'} |")
    passed = [c for c, v in ok.items() if v]
    L += ["", "## الحكم المسجّل مسبقاً", ""]
    L.append("لا تركيبة حققت المعيار في النصفين: تبقى v9 كما هي." if not passed else
             "حققت المعيار: " + "، ".join(f"{G}+{D}" for G, D in passed) + ". القرار للمستخدم.")
    return "\n".join(L) + "\n"


def main(argv=None):
    df = features(load())
    days = [(d, day_frame(g)) for d, g in rth_days(df)]
    print("أيام:", len(days))
    s1 = stage1(days)
    kinds = dict(zip(s1["day"], s1["kind"]))
    rows, res = stage2(days, kinds)
    ok = verdict(rows)
    md = report(s1, rows, ok, res, lab_daily())
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md)
    print(md)


if __name__ == "__main__":
    main(sys.argv[1:])
