#!/usr/bin/env python3
"""H-REGIME2: كاشف نوع اليوم حياً (اتجاه/تذبذب) عند 10:30 و11:30 و12:30 — الطابق الأول من نظام الشاشات. مسجّل في CLAUDE.md 22/11/2026.
اختبار فقط. شموع SPY 5د (Alpaca منذ 2019) وVIX اليومي (إغلاق أمس). انحدار لوجستي (numpy) يُدرَّب على النصف الأول ويُحكم على الثاني.
تشغيل: python research/regime_detector_study.py <مسار spy_5m.csv.gz أو مجلده>   المخرج: research/regime_detector_results.md
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brksigns_study as bk  # noqa: E402

OUT = os.path.join(HERE, "regime_detector_results.md")
SPLIT = pd.Timestamp("2022-11-23").date()
CHECKS = {"10:30": 11, "11:30": 23, "12:30": 35}     # فهرس آخر شمعة مكتملة (9:30 = 0)
FEATS = ["gap", "orbw", "brk", "vwd", "side", "mv", "adx", "vix"]
NAMES = {"gap": "الفجوة", "orbw": "عرض نطاق أول 30د", "brk": "ثبات كسر النطاق", "vwd": "البعد عن VWAP", "side": "الثبات في جهة VWAP",
         "mv": "الحركة من الافتتاح", "adx": "ADX", "vix": "VIX أمس"}
NB = 2000


def adx(h, l, c, n=14):
    up, dn = np.diff(h, prepend=h[0]), -np.diff(l, prepend=l[0])
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.maximum.reduce([h - l, np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))])
    tr[0] = h[0] - l[0]
    a = 1 / n
    sm = lambda x: pd.Series(x).ewm(alpha=a, adjust=False).mean().to_numpy()
    atr = sm(tr)
    pdi, mdi = 100 * sm(pdm) / atr, 100 * sm(mdm) / atr
    dx = 100 * np.abs(pdi - mdi) / np.maximum(pdi + mdi, 1e-9)
    return sm(dx)


def vix_daily():
    import yfinance as yf
    v = yf.Ticker("^VIX").history(period="max", interval="1d")["Close"]
    v.index = v.index.tz_localize(None).date
    return v


def rows(d5, vix):
    a = adx(d5["High"].to_numpy(float), d5["Low"].to_numpy(float), d5["Close"].to_numpy(float))
    d5 = d5.assign(adx=a)
    days = [(dt, g) for dt, g in d5.groupby(d5.index.date) if len(g) >= 78]
    out, hist = [], []
    vk = sorted(vix.index)
    for k, (dt, g) in enumerate(days):
        o, h, l, c, v = (g[x].to_numpy(float) for x in ("Open", "High", "Low", "Close", "Volume"))
        orbh, orbl = h[:6].max(), l[:6].min()
        hist.append({"orbw": orbh - orbl, "rng": h.max() - l.min(), "close": c[-1]})
        if k < 21:
            continue
        prev = hist[-21:-1]
        med_orb = np.median([x["orbw"] for x in prev])
        adr = np.mean([x["rng"] for x in prev])
        pc = hist[-2]["close"]
        vprev = [x for x in vk if x < dt]
        vx = float(vix[vprev[-1]]) if vprev else np.nan
        tp = (h + l + c) / 3
        vw = np.cumsum(tp * v) / np.maximum(np.cumsum(v), 1)
        for t, j in CHECKS.items():
            s = slice(6, j + 1)
            above, below = (c[s] > orbh).sum(), (c[s] < orbl).sum()
            sd = np.sign(c[j] - vw[j]) or 1
            hi, lo = h[j + 1:].max(), l[j + 1:].min()
            er = abs(c[-1] - c[j]) / max(hi - lo, 1e-9)
            out.append({"day": dt, "T": t, "gap": abs(o[0] / pc - 1), "orbw": (orbh - orbl) / max(med_orb, 1e-9),
                        "brk": max(above, below) / (j - 5), "vwd": abs(c[j] - vw[j]) / adr,
                        "side": float(np.mean(np.sign(c[s] - vw[s]) == sd)), "mv": abs(c[j] - o[0]) / adr,
                        "adx": float(g["adx"].iloc[j]), "vix": vx, "trend": int(er >= 0.5),
                        "follow": np.sign(c[j] - o[0]) * (c[-1] / c[j] - 1) * 1e4,
                        "fade": np.sign(vw[j] - c[j]) * (c[min(j + 12, len(c) - 1)] / c[j] - 1) * 1e4})
    return pd.DataFrame(out).dropna()


def logit_fit(X, y, it=50, lam=1e-3):
    X1 = np.c_[np.ones(len(X)), X]
    w = np.zeros(X1.shape[1])
    for _ in range(it):
        p = 1 / (1 + np.exp(-X1 @ w))
        W = p * (1 - p)
        H = X1.T @ (X1 * W[:, None]) + lam * np.eye(len(w))
        w -= np.linalg.solve(H, X1.T @ (p - y) + lam * w)
    return w


def predict(w, X):
    return 1 / (1 + np.exp(-(np.c_[np.ones(len(X)), X] @ w)))


def auc(p, y):
    r = pd.Series(p).rank().to_numpy()
    n1 = y.sum()
    n0 = len(y) - n1
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def boot(fn, n, seed=0):
    rng = np.random.default_rng(seed)
    return np.percentile([fn(rng.integers(0, n, n)) for _ in range(NB)], [2.5, 97.5])


def bh(ps, q=0.05):
    ps = np.asarray(ps)
    o = np.argsort(ps)
    m = len(ps)
    ok = [k for k in range(m) if ps[o[k]] <= q * (k + 1) / m]
    rej = np.zeros(m, bool)
    if ok:
        rej[o[:max(ok) + 1]] = True
    return rej


def pval(fn, n, seed=1):
    """p ثنائي تقريبي من توزيع الإقلاع حول الصفر."""
    rng = np.random.default_rng(seed)
    bs = np.array([fn(rng.integers(0, n, n)) for _ in range(NB)])
    return min(1.0, 2 * min((bs <= 0).mean(), (bs >= 0).mean()))


def main(argv):
    p = argv[0] if argv else "/opt/spxbot/data/spy_5m.csv.gz"
    if os.path.isdir(p):
        p = os.path.join(p, "spy_5m.csv.gz")
    df = rows(bk.read_bars(p), vix_daily())
    L = ["# H-REGIME2: كاشف نوع اليوم حياً (نتائج آلية)", "",
         f"- {df['day'].nunique()} يوماً ({df['day'].min()} → {df['day'].max()}). «اتجاه» = كفاءة بقية اليوم ≥ 0.5. التدريب على النصف الأول، والحكم على الثاني.", ""]
    tests, blocks = [], []
    for t in CHECKS:
        d = df[df["T"] == t].reset_index(drop=True)
        tr, te = d[d["day"] < SPLIT], d[d["day"] >= SPLIT].reset_index(drop=True)
        mu, sd = tr[FEATS].mean(), tr[FEATS].std()
        w = logit_fit(((tr[FEATS] - mu) / sd).to_numpy(), tr["trend"].to_numpy())
        pt, pe = predict(w, ((tr[FEATS] - mu) / sd).to_numpy()), predict(w, ((te[FEATS] - mu) / sd).to_numpy())
        q1, q2 = np.quantile(pt, [1 / 3, 2 / 3])
        y = te["trend"].to_numpy()
        a = auc(pe, y)
        aci = boot(lambda i: auc(pe[i], y[i]) if 0 < y[i].sum() < len(i) else 0.5, len(y))
        pa = pval(lambda i: (auc(pe[i], y[i]) if 0 < y[i].sum() < len(i) else 0.5) - 0.5, len(y))
        hi_, lo_ = pe >= q2, pe <= q1
        f, fa = te["follow"].to_numpy(), te["fade"].to_numpy()

        def dd(x, A, B):
            return lambda i: x[i][A[i]].mean() - x[i][B[i]].mean()
        d2 = (f[hi_].mean(), f[lo_].mean(), boot(lambda i: f[i][hi_[i]].mean(), len(y)), boot(dd(f, hi_, lo_), len(y)))
        d3 = (fa[lo_].mean(), fa[hi_].mean(), boot(lambda i: fa[i][lo_[i]].mean(), len(y)), boot(dd(fa, lo_, hi_), len(y)))
        p2 = max(pval(lambda i: f[i][hi_[i]].mean(), len(y)), pval(dd(f, hi_, lo_), len(y)))
        p3 = max(pval(lambda i: fa[i][lo_[i]].mean(), len(y)), pval(dd(fa, lo_, hi_), len(y)))
        tests += [(t, "D1", pa, aci[0] > 0.5), (t, "D2", p2, d2[2][0] > 0 and d2[3][0] > 0), (t, "D3", p3, d3[2][0] > 0 and d3[3][0] > 0)]
        acc = ((pe >= 0.5).astype(int) == y).mean()
        B = [f"## عند {t}", "", f"- أيام الحكم {len(te)}، منها اتجاه {y.mean() * 100:.0f}%. AUC = **{a:.3f}** [{aci[0]:.3f}، {aci[1]:.3f}]، دقة التصنيف عند 0.5: {acc * 100:.0f}%.",
             f"- الثلث الأعلى (اتجاه متوقع): اتجاه فعلاً {y[hi_].mean() * 100:.0f}% من {hi_.sum()} يوماً | الثلث الأدنى (تذبذب متوقع): اتجاه {y[lo_].mean() * 100:.0f}% من {lo_.sum()}.",
             f"- **D2 اتباع اتجاه الصباح حتى الإغلاق:** الثلث الأعلى {d2[0]:+.1f} نقطة أساس [{d2[2][0]:+.1f}، {d2[2][1]:+.1f}]، الأدنى {d2[1]:+.1f}، الفرق [{d2[3][0]:+.1f}، {d2[3][1]:+.1f}].",
             f"- **D3 العودة إلى VWAP خلال 60د:** الثلث الأدنى {d3[0]:+.1f} [{d3[2][0]:+.1f}، {d3[2][1]:+.1f}]، الأعلى {d3[1]:+.1f}، الفرق [{d3[3][0]:+.1f}، {d3[3][1]:+.1f}].",
             "- أوزان المدخلات (معيارية؛ موجب = يرجّح الاتجاه): " + "، ".join(f"{NAMES[k]} {x:+.2f}" for k, x in zip(FEATS, w[1:])), ""]
        blocks += B
    rej = bh([x[2] for x in tests])
    L += ["| الوقت | الفرضية | p | الفاصل يحقق الشرط | BH | الحكم |", "|---|---|---|---|---|---|"]
    for (t, h, p_, ok), r in zip(tests, rej):
        L.append(f"| {t} | {h} | {p_:.3f} | {'نعم' if ok else 'لا'} | {'نعم' if r else 'لا'} | {'مدعومة ✅' if ok and r else 'غير مدعومة'} |")
    L += [""] + blocks + ["تحفظ: SPY بدل SPX؛ العائد على الأصل بلا كلفة؛ بلا شاشات الجدران والتدفق وES (لا تاريخ لها)."]
    md = "\n".join(L) + "\n"
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main(sys.argv[1:])
