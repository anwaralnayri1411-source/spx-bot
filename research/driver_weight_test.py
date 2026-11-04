#!/usr/bin/env python3
"""H-DRVW: هل يتحسن بوت الشركات إذا أضفنا وزن محرك كل شركة المقاس (البتكوين/النفط/الذهب/السندات) لحركته اليوم؟
(مسجّل مسبقاً في CLAUDE.md 4/11/2026). اختبار فقط، لا يغيّر أي بوت.

القاعدة الحالية: stocks_bot.tech_points على الشمعة اليومية حتى أمس + نصف نقطة SPY، |الفرق| ≥ MIN_EDGE، دخول 10:30.
الموزونة: + بيتا × حركة المحرك اليوم حتى 10:30 (٪) بسقف ±2، حيث المحرك = الأعلى |ارتباطاً| (≥0.30) بين عوائد
السهم فوق SPY وعوائد IBIT/USO/GLD/TLT في آخر 120 جلسة قبل اليوم.
DW1: متوسط إشارات الموزونة − الحالية > 0 بفاصل 95% (إعادة معاينة الأيام) وموجب في النصفين.
تشغيل (الخادم): SPXBOT_DATA=/opt/spxbot/data python research/driver_weight_test.py   المخرج: research/driver_weight_results.md
"""
import glob
import os
import sys
from datetime import time as dtime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from confirm_study import read_bars  # noqa: E402

DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
OUT = os.path.join(HERE, "driver_weight_results.md")
START = pd.Timestamp("2024-04-01").date()
CANDS = {"IBIT": "البتكوين", "USO": "النفط", "GLD": "الذهب", "TLT": "السندات"}
WIN, CORR_MIN, CAP = 120, 0.30, 2.0
ETFS = {"SPY", "QQQ", "IWM", "DIA"} | set(CANDS) | {"SMH", "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE", "XLC"}
N_BOOT = 3000


def daily(d5):
    g = d5.groupby(d5.index.date)
    t = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(), "Close": g["Close"].last(),
                      "Volume": g["Volume"].sum()})
    at = d5[d5.index.time == dtime(10, 25)]["Close"]
    at.index = at.index.date
    t["p1030"] = at
    return t


def driver_for(sym_ex, cands_ret, k):
    """(اسم المحرك، بيتا) من آخر WIN جلسة قبل الموضع k، أو (None، 0)."""
    best, beta = None, 0.0
    y = sym_ex.iloc[max(0, k - WIN):k]
    if len(y) < WIN * 0.8:
        return None, 0.0
    bc = 0.0
    for name, r in cands_ret.items():
        x = r.reindex(y.index)
        ok = x.notna() & y.notna()
        if ok.sum() < WIN * 0.8:
            continue
        c = np.corrcoef(x[ok], y[ok])[0, 1]
        if abs(c) >= CORR_MIN and abs(c) > abs(bc):
            bc, best = c, name
            beta = float(np.polyfit(x[ok], y[ok], 1)[0])
    return best, beta


def run():
    import stocks_bot as sb
    spy = daily(read_bars("SPY"))
    spy_ret = spy["Close"].pct_change()
    cd = {c: daily(read_bars(c)) for c in CANDS if read_bars(c) is not None}
    cands_ret = {c: d["Close"].pct_change() for c, d in cd.items()}
    paths = [os.path.basename(p).split(".")[0] for p in glob.glob(os.path.join(DATA, "bars_5m", "*.csv.gz"))]
    rows = []
    for sym in sorted(s for s in paths if s not in ETFS):
        t = daily(read_bars(sym)).dropna(subset=["Close"])
        ex = (t["Close"].pct_change() - spy_ret.reindex(t.index))
        days = list(t.index)
        for k in range(130, len(days) - 6):
            d = days[k]
            if d < START or np.isnan(t["p1030"].iloc[k]):
                continue
            df = t.iloc[k - 130:k]
            sd = spy["Close"][spy.index < d]
            spy5 = float(sd.iloc[-1] / sd.iloc[-6] - 1)
            bull, bear, _, _ = sb.tech_points(df, spy5, None, float(df["Volume"].iloc[-1]))
            if float(sd.iloc[-1]) > float(sd.rolling(20).mean().iloc[-1]):
                bull += 0.5
            else:
                bear += 0.5
            drv, beta = driver_for(ex, cands_ret, k)
            pts = 0.0
            if drv and d in cd[drv].index and not np.isnan(cd[drv].at[d, "p1030"]):
                prev = cd[drv]["Close"][cd[drv].index < d].iloc[-1]
                dm = cd[drv].at[d, "p1030"] / prev - 1
                pts = float(np.clip(beta * dm * 100, -CAP, CAP))
            entry, c5 = float(t["p1030"].iloc[k]), float(t["Close"].iloc[k + 5])
            for rule, b_, e_ in (("base", bull, bear), ("weighted", bull + max(pts, 0), bear + max(-pts, 0))):
                edge = b_ - e_
                if abs(edge) >= sb.MIN_EDGE:
                    s = 1 if edge > 0 else -1
                    rows.append({"rule": rule, "sym": sym, "day": str(d), "side": "CALL" if s > 0 else "PUT", "r5": s * (c5 / entry - 1),
                                 "driver": drv or "", "beta": round(beta, 2), "pts": round(pts, 2)})
        print(sym, flush=True)
    return pd.DataFrame(rows)


def boot_diff(a, b, n=N_BOOT, seed=0):
    u = np.unique(np.r_[a["day"].values, b["day"].values])
    ba = {d: g["r5"].values for d, g in a.groupby("day")}
    bb = {d: g["r5"].values for d, g in b.groupby("day")}
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        s = rng.choice(u, len(u))
        xa = np.concatenate([ba.get(d, np.array([])) for d in s])
        xb = np.concatenate([bb.get(d, np.array([])) for d in s])
        if len(xa) and len(xb):
            out.append(xa.mean() - xb.mean())
    return float(a["r5"].mean() - b["r5"].mean()), tuple(np.percentile(out, [2.5, 97.5]))


def report(df):
    b, w = df[df["rule"] == "base"], df[df["rule"] == "weighted"]
    d, (lo, hi) = boot_diff(w, b)
    days = sorted(df["day"].unique())
    mid = days[len(days) // 2]
    h = [w[w["day"] < mid]["r5"].mean() - b[b["day"] < mid]["r5"].mean(), w[w["day"] >= mid]["r5"].mean() - b[b["day"] >= mid]["r5"].mean()]
    ok = lo > 0 and min(h) > 0
    key = lambda x: set(zip(x["sym"], x["day"], x["side"]))
    kb, kw = key(b), key(w)
    bsd = {(r.sym, r.day): r.side for r in b.itertuples()}
    added = w[[(r.sym, r.day) not in bsd for r in w.itertuples()]]
    flipped = w[[(r.sym, r.day) in bsd and bsd[(r.sym, r.day)] != r.side for r in w.itertuples()]]
    wsd = {(r.sym, r.day) for r in w.itertuples()}
    removed = b[[(r.sym, r.day) not in wsd for r in b.itertuples()]]
    L = ["# H-DRVW: وزن محرك الشركة المقاس في نقاط البوت (نتائج آلية)", "",
         f"- {df['sym'].nunique()} شركة، {df['day'].min()} → {df['day'].max()}. دخول 10:30، النتيجة حركة السهم بإشارة الصفقة حتى إغلاق +5.",
         f"- المحرك لكل شركة ويوم: الأعلى |ارتباطاً| (≥{CORR_MIN}) بين عوائدها فوق SPY وعوائد {'، '.join(f'{k} ({v})' for k, v in CANDS.items())} لآخر {WIN} جلسة؛ "
         f"الوزن = بيتا × حركة المحرك حتى 10:30 بسقف ±{CAP:.0f}.", "",
         "| القاعدة | إشارات | +5 أيام | رابحة |", "|---|---|---|---|",
         f"| الحالية | {len(b)} | {b['r5'].mean() * 100:+.2f}% | {(b['r5'] > 0).mean() * 100:.0f}% |",
         f"| الموزونة بالمحرك | {len(w)} | {w['r5'].mean() * 100:+.2f}% | {(w['r5'] > 0).mean() * 100:.0f}% |", "",
         f"- **DW1:** الفرق {d * 100:+.2f} نقطة، فاصل 95% [{lo * 100:+.2f}، {hi * 100:+.2f}]، النصفان {h[0] * 100:+.2f} / {h[1] * 100:+.2f} → "
         f"**{'تحققت ✅' if ok else 'لم تتحقق'}**", "",
         "## ما غيّره الوزن (وصفي)", "", "| الإشارات | العدد | +5 أيام | رابحة |", "|---|---|---|---|"]
    for name, g in (("أضافها الوزن (جديدة)", added), ("قلبها الوزن (CALL↔PUT)", flipped), ("ألغاها الوزن", removed)):
        if len(g):
            L.append(f"| {name} | {len(g)} | {g['r5'].mean() * 100:+.2f}% | {(g['r5'] > 0).mean() * 100:.0f}% |")
    L += ["", "## حسب المحرك (القاعدة الموزونة، إشارات لها محرك)", "", "| المحرك | إشارات | متوسط الوزن المطلق | +5 أيام | شركات |", "|---|---|---|---|---|"]
    for drv, g in w[w["driver"] != ""].groupby("driver"):
        L.append(f"| {CANDS.get(drv, drv)} | {len(g)} | {g['pts'].abs().mean():.2f} | {g['r5'].mean() * 100:+.2f}% | {' '.join(sorted(g['sym'].unique()))} |")
    L += ["", "تحفظ: حركة السهم ليست ربح العقد؛ الارتباط الماضي قد يتغير؛ IBIT منذ 2024."]
    return "\n".join(L) + "\n"


def main():
    df = run()
    md = report(df)
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main()
