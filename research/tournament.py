#!/usr/bin/env python3
"""البطولة H-TOURNEY: كل الأدوات والأفكار مقابل العشوائي، منفردة ومدمجة، على عدة فريمات وعدة رموز. اختبار فقط (لا يغيّر أي بوت).
بطلب المستخدم 27/10/2026: «اجمع جميع الاستراتيجيات والأفكار والأدوات واختبرها مع العشوائي وادمج واختبر على كذا فريم وكذا مؤشر».

البروتوكول (مسجّل هنا وفي CLAUDE.md قبل التشغيل):
  البيانات: شموع 5د من Alpaca منذ 2019 لـSPY و36 رمزاً (صناديق QQQ/IWM/DIA وشركات قائمة البوت)، ساعات السوق فقط.
  الفريمات: 5د، 15د، 30د، 60د (داخل اليوم)، واليومي. أفق النتيجة: 5د→15 دقيقة، 15د→30، 30د→60، 60د→120 (في اليوم نفسه)، اليومي→اليوم التالي.
  الإشارات (جهة +1/−1/0 عند إغلاق الشمعة، بلا نظرة للمستقبل) ومعاكس كل منها: زخم شمعة وثلاث شموع، تقاطع EMA 9/21، هيستوغرام MACD،
    VWAP، اختراق نطاق أول 30 دقيقة، اتجاه أول 30 دقيقة، الفجوة ≥0.4%، أعلى/أدنى أمس، RSI14 (30/70)، بولنجر 20/2، اختراق دونشيان 20،
    سحب السيولة (كسر قاع/قمة 20 شمعة ثم الإغلاق عائداً)، الفجوة العادلة FVG، منطقة فيبوناتشي 0.5–0.618 من مدى اليوم.
  المجموعات: SPY وحده، الصناديق (SPY/QQQ/IWM/DIA)، الشركات، الكل.
  المقياس مقابل العشوائي: «الزيادة» = جهة الإشارة × (العائد اللاحق − متوسط عائد الرمز في الفترة نفسها)، أي الفرق عن اختيار عشوائي بالمزيج نفسه
    من الشراء والبيع (يزيل أثر صعود السوق العام). متوسط يومي (لكل رمز ثم بين الرموز)، وفاصل وقيمة p بإعادة معاينة الأيام.
  الاكتشاف والتحقق: النصف الأول من الأيام (حتى ~2022) للاكتشاف: يُرشَّح ما زيادته موجبة بـp < 0.05. الدمج: لكل فريم ومجموعة، أفضل 6 مرشحين
    يُدمجون أزواجاً (إشارة فقط حين يتفق الاثنان). الحكم على النصف الثاني فقط: تصحيح Benjamini-Hochberg بمعدل 5% على كل المرشحين معاً،
    وموجب في نصفي فترة التحقق، ثم الجدوى: متوسط الزيادة لكل إشارة > حاجز التكلفة (0.03% داخل اليوم، 0.05% لليومي؛ تقدير لعقد على السعر). الفائز = يجتاز الثلاثة.
تحفظات: عائد الرمز ليس ربح العقد؛ آلاف المقارنات (لهذا الاكتشاف/التحقق وBH)؛ الشموع المتتالية مترابطة (لهذا المعاينة بالأيام).
تشغيل: SPXBOT_DATA=/opt/spxbot/data python research/tournament.py   المخرج: research/tournament_results.md
"""
import glob
import os
import sys
from datetime import datetime, time as dtime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "tournament_results.md")
DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
ETFS = ("SPY", "QQQ", "IWM", "DIA")
FRAMES = {5: 3, 15: 2, 30: 2, 60: 2, "D": 1}          # فريم → عدد الشموع في أفق النتيجة
HURDLE = {"intra": 0.0003, "D": 0.0005}
P_DISC, FDR_Q, TOP_COMBO, BOOT_N = 0.05, 0.05, 6, 2000
NAMES = {"mom1": "زخم شمعة", "mom3": "زخم 3 شموع", "ema": "تقاطع EMA 9/21", "macd": "هيستوغرام MACD", "vwap": "فوق/تحت VWAP",
         "orb": "اختراق نطاق أول 30د", "f30": "اتجاه أول 30د", "gap": "الفجوة ≥0.4%", "pdhl": "أعلى/أدنى أمس", "rsi": "RSI (ارتداد 30/70)",
         "bb": "بولنجر (ارتداد)", "don": "اختراق دونشيان 20", "sweep": "سحب السيولة", "fvg": "الفجوة العادلة FVG", "fib": "فيبوناتشي 0.5–0.618"}


# ============================ البيانات ============================
def read_bars(path):
    d = pd.read_csv(path)
    d.index = pd.DatetimeIndex(pd.to_datetime(d["t"], utc=True)).tz_convert("America/New_York").tz_localize(None)
    d = d.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})[["Open", "High", "Low", "Close", "Volume"]]
    return d[(d.index.time >= dtime(9, 30)) & (d.index.time < dtime(16, 0))].sort_index()


def load_all(data=DATA):
    out = {}
    spy = os.path.join(data, "spy_5m.csv.gz")
    if os.path.exists(spy):
        out["SPY"] = read_bars(spy)
    for p in sorted(glob.glob(os.path.join(data, "bars_5m", "*.csv.gz"))):
        s = os.path.basename(p).split(".")[0]
        if s not in out:
            out[s] = read_bars(p)
    return out


def resample(d5, tf):
    if tf == 5:
        return d5
    if tf == "D":
        g = d5.groupby(d5.index.date)
        d = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(), "Close": g["Close"].last(), "Volume": g["Volume"].sum()})
        d.index = pd.DatetimeIndex(pd.to_datetime(d.index))
        return d
    r = d5.resample(f"{tf}min", label="left", closed="left", offset="30min" if tf == 60 else None)
    d = pd.DataFrame({"Open": r["Open"].first(), "High": r["High"].max(), "Low": r["Low"].min(), "Close": r["Close"].last(), "Volume": r["Volume"].sum()})
    return d.dropna(subset=["Close"])


# ============================ الإشارات (نقية، بلا نظرة للمستقبل) ============================
def sgn(x):
    return np.sign(x).fillna(0).astype(np.int8)


def signals(d, tf):
    """DataFrame إشارات int8 بالفهرس نفسه، وعمود fwd (عائد الأفق في اليوم نفسه)."""
    c, h, l, o = d["Close"], d["High"], d["Low"], d["Open"]
    day = pd.Series(d.index.date, index=d.index)
    H = FRAMES[tf]
    fwd = c.shift(-H) / c - 1
    if tf != "D":
        fwd[day.shift(-H) != day] = np.nan
    S = pd.DataFrame(index=d.index)
    same = (day.shift(1) == day) if tf != "D" else pd.Series(True, index=d.index)
    S["mom1"] = sgn((c / c.shift(1) - 1).where(same))
    S["mom3"] = sgn((c / c.shift(3) - 1).where((day.shift(3) == day) if tf != "D" else pd.Series(True, index=d.index)))
    e9, e21 = c.ewm(span=9, adjust=False).mean(), c.ewm(span=21, adjust=False).mean()
    S["ema"] = sgn(e9 - e21)
    m = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    S["macd"] = sgn(m - m.ewm(span=9, adjust=False).mean())
    delta = c.diff()
    up, dn = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(), (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    S["rsi"] = (np.where(rsi < 30, 1, np.where(rsi > 70, -1, 0))).astype(np.int8)
    mid, sd = c.rolling(20).mean(), c.rolling(20).std()
    S["bb"] = (np.where(c < mid - 2 * sd, 1, np.where(c > mid + 2 * sd, -1, 0))).astype(np.int8)
    hh, ll = h.rolling(20).max().shift(1), l.rolling(20).min().shift(1)
    S["don"] = (np.where(c > hh, 1, np.where(c < ll, -1, 0))).astype(np.int8)
    S["sweep"] = (np.where((l < ll) & (c > ll), 1, np.where((h > hh) & (c < hh), -1, 0))).astype(np.int8)
    S["fvg"] = (np.where(l > h.shift(2), 1, np.where(h < l.shift(2), -1, 0))).astype(np.int8)
    if tf != "D":
        g = d.groupby(day)
        tp = (h + l + c) / 3
        vw = (tp * d["Volume"]).groupby(day).cumsum() / d["Volume"].groupby(day).cumsum().replace(0, np.nan)
        S["vwap"] = sgn(c - vw)
        t = pd.Series(d.index.time, index=d.index)
        first = t < dtime(10, 0)
        orb_h = h.where(first).groupby(day).transform("max")
        orb_l = l.where(first).groupby(day).transform("min")
        o0 = g["Open"].transform("first")
        c10 = c.where(first).groupby(day).transform("last")
        after = ~first
        S["orb"] = (np.where(after & (c > orb_h), 1, np.where(after & (c < orb_l), -1, 0))).astype(np.int8)
        S["f30"] = (sgn(c10 / o0 - 1) * after).astype(np.int8)
        daily_c = c.groupby(day).last()
        daily_h, daily_l = h.groupby(day).max(), l.groupby(day).min()
        prev_c = day.map(daily_c.shift(1))
        gap = o0 / prev_c - 1
        S["gap"] = (np.where(gap >= 0.004, 1, np.where(gap <= -0.004, -1, 0))).astype(np.int8)
        pdh, pdl = day.map(daily_h.shift(1)), day.map(daily_l.shift(1))
        S["pdhl"] = (np.where(c > pdh, 1, np.where(c < pdl, -1, 0))).astype(np.int8)
        dh, dl = h.groupby(day).cummax(), l.groupby(day).cummin()
        rng_ = (dh - dl).replace(0, np.nan)
        r_up, r_dn = (dh - c) / rng_, (c - dl) / rng_
        S["fib"] = (np.where((c > o0) & r_up.between(0.5, 0.618), 1, np.where((c < o0) & r_dn.between(0.5, 0.618), -1, 0))).astype(np.int8)
    for k in list(S.columns):
        S[k + "~"] = (-S[k]).astype(np.int8)               # المعاكس
    S["fwd"] = fwd
    return S


def label(name):
    rev = name.endswith("~")
    base = name.rstrip("~")
    return ("عكس " if rev else "") + NAMES.get(base, base)


# ============================ التقييم ============================
class Pool:
    """إشارات كل الرموز لفريم واحد في مصفوفات numpy مسطّحة للتقييم السريع."""

    def __init__(self, frames):
        parts = []
        for sym, S in frames.items():
            S = S.dropna(subset=["fwd"])
            if S.empty:
                continue
            S = S.copy()
            S["sym"] = sym
            parts.append(S)
        A = pd.concat(parts)
        self.days = pd.Index(sorted(set(A.index.date)))
        self.day_code = self.days.get_indexer(A.index.date)
        self.syms = sorted(A["sym"].unique())
        self.sym_code = pd.Index(self.syms).get_indexer(A["sym"])
        self.cols = [c for c in A.columns if c not in ("fwd", "sym")]
        self.sig = {c: A[c].values.astype(np.int8) for c in self.cols}
        mid = self.days[len(self.days) // 2]
        self.half = (np.array(A.index.date) >= mid).astype(np.int8)
        fwd = A["fwd"].values
        mu = np.zeros_like(fwd)
        for s in range(len(self.syms)):            # إزالة متوسط الرمز في كل نصف (= المقارنة بعشوائي بالمزيج نفسه)
            for hf in (0, 1):
                k = (self.sym_code == s) & (self.half == hf)
                if k.any():
                    mu[k] = fwd[k].mean()
        self.x = fwd - mu
        self.mid = mid

    def daily(self, s, half, syms=None):
        """(مجموع الزيادة وعدد الإشارات لكل يوم فيه إشارات، متوسط الزيادة لكل إشارة، عدد الإشارات).
        كل إشارة بوزن متساوٍ: متوسط الأيام بأوزان متساوية متحيّز لأن عدد إشارات اليوم يتأثر بما يحدث بعدها."""
        m = (s != 0) & (self.half == half)
        if syms is not None:
            m &= np.isin(self.sym_code, syms)
        if not m.any():
            return None, None, 0
        v = s[m] * self.x[m]
        n_d = np.bincount(self.day_code[m], minlength=len(self.days))
        s_d = np.bincount(self.day_code[m], weights=v, minlength=len(self.days))
        k = n_d > 0
        return (s_d[k], n_d[k]), float(v.mean()), int(m.sum())


def boot_p(days_v, n=BOOT_N, seed=31):
    """(متوسط الزيادة لكل إشارة، p من جانب واحد) بإعادة معاينة الأيام (مجموع÷عدد لكل عينة)."""
    if days_v is None or len(days_v[0]) < 20:
        return None, None
    S, N = days_v
    idx = np.random.default_rng(seed).integers(0, len(S), (n, len(S)))
    mm = S[idx].sum(1) / N[idx].sum(1)
    return float(S.sum() / N.sum()), float((mm <= 0).mean())


def bh(pvals, q=FDR_Q):
    order = np.argsort(pvals)
    m, k = len(pvals), 0
    for i, j in enumerate(order, 1):
        if pvals[j] <= q * i / m:
            k = i
    passed = np.zeros(m, dtype=bool)
    passed[order[:k]] = True
    return passed


def run(data):
    syms = list(data)
    groups = {"SPY": ["SPY"], "الصناديق": [s for s in syms if s in ETFS], "الشركات": [s for s in syms if s not in ETFS], "الكل": syms}
    cands = []
    meta = {}
    for tf in FRAMES:
        frames = {s: signals(resample(d, tf), tf) for s, d in data.items()}
        P = Pool(frames)
        meta[tf] = (len(P.days), P.days[0], P.mid, P.days[-1])
        for gname, gs in groups.items():
            codes = [P.syms.index(s) for s in gs if s in P.syms]
            if not codes:
                continue
            singles = []
            for c in P.cols:
                dv, ev, n = P.daily(P.sig[c], 0, codes)
                mean, p = boot_p(dv)
                if mean is None:
                    continue
                singles.append({"tf": tf, "group": gname, "name": c, "disc_mean": mean, "disc_p": p, "disc_ev": ev, "disc_n": n, "codes": codes, "pool": P, "sig": P.sig[c]})
            cands += singles
            top = sorted([x for x in singles if x["disc_mean"] > 0], key=lambda x: x["disc_p"])[:TOP_COMBO]
            for i in range(len(top)):
                for j in range(i + 1, len(top)):
                    a, b = top[i], top[j]
                    if a["name"].rstrip("~") == b["name"].rstrip("~"):
                        continue
                    s = np.where(a["sig"] == b["sig"], a["sig"], 0).astype(np.int8)
                    dv, ev, n = P.daily(s, 0, codes)
                    mean, p = boot_p(dv)
                    if mean is None:
                        continue
                    cands.append({"tf": tf, "group": gname, "name": f"{a['name']}+{b['name']}", "disc_mean": mean, "disc_p": p, "disc_ev": ev, "disc_n": n,
                                  "codes": codes, "pool": P, "sig": s})
    sel = [c for c in cands if c["disc_mean"] > 0 and c["disc_p"] < P_DISC]
    for c in sel:
        dv, ev, n = c["pool"].daily(c["sig"], 1, c["codes"])
        c["val_mean"], c["val_p"] = boot_p(dv)
        c["val_ev"], c["val_n"] = ev, n
        if dv is not None:                         # الثبات: موجب في نصفي فترة التحقق
            S, N = dv
            h = len(S) // 2
            c["val_stable"] = bool(h and S[:h].sum() > 0 and S[h:].sum() > 0)
    sel = [c for c in sel if c.get("val_p") is not None]
    if sel:
        passed = bh(np.array([c["val_p"] for c in sel]))
        for c, ok in zip(sel, passed):
            c["bh"] = bool(ok and c["val_mean"] > 0 and c.get("val_stable"))
            c["cost_ok"] = c["val_ev"] is not None and c["val_ev"] > HURDLE["D" if c["tf"] == "D" else "intra"]
    return cands, sel, meta


def tf_name(tf):
    return "يومي" if tf == "D" else f"{tf}د"


def nm(c):
    return " + ".join(label(x) for x in c["name"].split("+"))


def report(cands, sel, meta):
    n_single = sum(1 for c in cands if "+" not in c["name"])
    L = ["# البطولة: كل الأدوات والأفكار مقابل العشوائي (اختبار، Alpaca 5د منذ 2019)", "",
         f"- مرشحون: {len(cands)} ({n_single} منفرد + {len(cands) - n_single} مدمج) على {len(FRAMES)} فريمات و4 مجموعات.",
         "- " + "؛ ".join(f"{tf_name(tf)}: {n} يوماً، اكتشاف {a} → {m}، تحقق → {b}" for tf, (n, a, m, b) in meta.items()),
         "- «الزيادة» = الفرق عن اختيار عشوائي بالمزيج نفسه من الشراء والبيع (بعد إزالة اتجاه السوق العام). حاجز التكلفة: 0.03% داخل اليوم، 0.05% لليومي.", ""]
    win = [c for c in sel if c.get("bh") and c.get("cost_ok")]
    beat = [c for c in sel if c.get("bh") and not c.get("cost_ok")]
    L += [f"## 🏆 الفائزون (تفوقوا على العشوائي في التحقق بعد التصحيح وتجاوزوا التكلفة): {len(win)}", ""]
    if win:
        L += ["| الإشارة | الفريم | المجموعة | زيادة التحقق لكل إشارة | p التحقق | إشارات | زيادة الاكتشاف |", "|---|---|---|---|---|---|---|"]
        for c in sorted(win, key=lambda c: -c["val_ev"]):
            L.append(f"| {nm(c)} | {tf_name(c['tf'])} | {c['group']} | {100 * c['val_ev']:+.3f}% | {c['val_p']:.4f} | {c['val_n']} | {100 * c['disc_ev']:+.3f}% |")
    else:
        L.append("لا أحد.")
    L += ["", f"## ✅ تفوقوا على العشوائي لكن دون حاجز التكلفة: {len(beat)}", ""]
    if beat:
        L += ["| الإشارة | الفريم | المجموعة | زيادة التحقق لكل إشارة | p التحقق | إشارات |", "|---|---|---|---|---|---|"]
        for c in sorted(beat, key=lambda c: -c["val_ev"])[:25]:
            L.append(f"| {nm(c)} | {tf_name(c['tf'])} | {c['group']} | {100 * c['val_ev']:+.3f}% | {c['val_p']:.4f} | {c['val_n']} |")
    fail = [c for c in sel if not c.get("bh")]
    L += ["", f"## ❌ بدت جيدة في الاكتشاف وفشلت في التحقق: {len(fail)} من {len(sel)} مرشحاً عبروا الاكتشاف", ""]
    if fail:
        L += ["| الإشارة | الفريم | المجموعة | زيادة الاكتشاف | زيادة التحقق |", "|---|---|---|---|---|"]
        for c in sorted(fail, key=lambda c: c["disc_p"])[:15]:
            L.append(f"| {nm(c)} | {tf_name(c['tf'])} | {c['group']} | {100 * c['disc_ev']:+.3f}% | {100 * (c['val_ev'] or 0):+.3f}% |")
    L += ["", f"⚖️ H-TOURNEY: فائزون {len(win)}؛ يتفوقون على العشوائي دون التكلفة {len(beat)}؛ فشلوا في التحقق {len(fail)}؛ لم يعبروا الاكتشاف {len(cands) - len(sel)}.", ""]
    return L


def main(argv):
    data = load_all()
    cands, sel, meta = run(data)
    L = report(cands, sel, meta)
    L.insert(2, f"- الرموز: {len(data)} ({'، '.join(sorted(data))})")
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
