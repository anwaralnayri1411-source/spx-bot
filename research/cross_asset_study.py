#!/usr/bin/env python3
"""حساسية شركات بوت الشركات للأصول الأخرى (دراسة فقط، لا تلمس البوت). بطلب المستخدم 22/10.
الارتباط فوق السوق (بعد حذف SPY من الطرفين) مقابل IBIT (بتكوين)، USO (نفط)، UUP (دولار)، IEF معكوساً (عائد 10 سنوات)،
وصندوق القطاع. يومي لسنتين + كل 30د لآخر 60 يوماً. الوهمي: 500 إزاحة دائرية للعامل. الثبات: إشارة الارتباط في 8 فترات (60 يوماً).
ارتباط متزامن لا تنبؤ مسبق. تشغيل (على الخادم؛ ياهو محجوب محلياً): python research/cross_asset_study.py research/cross_asset_results.csv"""
import numpy as np, pandas as pd, yfinance as yf, sys
TK = ["AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AVGO","AMD","NFLX","PLTR","UBER","MA","V","JPM","BAC","DIS",
      "WMT","INTC","SOFI","F","PFE","SNAP","MU","RIVN","T","BA","XOM","CVX","COIN","MSTR","HOOD","LMT"]
SECTOR = {"NVDA":"SMH","AVGO":"SMH","AMD":"SMH","MU":"SMH","INTC":"SMH","AAPL":"XLK","MSFT":"XLK","PLTR":"XLK",
          "AMZN":"XLY","TSLA":"XLY","RIVN":"XLY","F":"XLY","GOOGL":"XLC","META":"XLC","NFLX":"XLC","DIS":"XLC","SNAP":"XLC",
          "T":"XLC","MA":"XLF","V":"XLF","JPM":"XLF","BAC":"XLF","SOFI":"XLF","COIN":"IBIT","MSTR":"IBIT","HOOD":"IBIT",
          "WMT":"XLP","PFE":"XLV","XOM":"XLE","CVX":"XLE","BA":"XLI","LMT":"XLI","UBER":"XLI"}
FAC = {"BTC": "IBIT", "OIL": "USO", "USD": "UUP", "Y10": "IEF"}
ETF = sorted(set(SECTOR.values()) | set(FAC.values()) | {"SPY"})
rng = np.random.default_rng(7)

def resid(y, x):
    b = np.polyfit(x, y, 1)[0]
    return y - b * x

def test(s, f, spy, min_shift, n=500):
    m = s.notna() & f.notna() & spy.notna()
    s, f, spy = s[m].values, f[m].values, spy[m].values
    rs, rf = resid(s, spy), resid(f, spy)
    c = np.corrcoef(rs, rf)[0, 1]
    beta = np.polyfit(rf, rs, 1)[0]
    L = len(rs)
    null = np.array([np.corrcoef(rs, np.roll(rf, k))[0, 1] for k in rng.integers(min_shift, L - min_shift, n)])
    p = (np.sum(np.abs(null) >= abs(c)) + 1) / (n + 1)
    return c, beta, p, L, rs, rf, np.corrcoef(s, f)[0, 1]

def windows(rs, rf, w):
    out = []
    for i in range(0, len(rs) - w + 1, w):
        out.append(np.corrcoef(rs[i:i+w], rf[i:i+w])[0, 1])
    return out

def load(period, interval):
    d = yf.download(TK + ETF, period=period, interval=interval, auto_adjust=True, progress=False, threads=True)["Close"]
    return d

def main():
    rows = []
    D = load("2y", "1d").pct_change()
    I = load("60d", "5m")
    I.index = I.index.tz_convert("America/New_York")
    I = I.between_time("09:30", "16:00")
    I = I.groupby(I.index.date, group_keys=False).apply(lambda g: g.resample("30min").last()).pct_change()
    I = I[I.index.time != pd.Timestamp("09:30").time()]   # drop overnight gaps
    for tk in TK:
        for name, sym in list(FAC.items()) + [("SECTOR", SECTOR[tk])]:
            if name == "SECTOR" and sym == "IBIT":
                continue
            sg = -1 if name == "Y10" else 1
            c, b, p, L, rs, rf, raw = test(D[tk], sg * D[sym], D["SPY"], 20)
            wins = windows(rs, rf, 60)
            same = np.mean([np.sign(x) == np.sign(c) for x in wins])
            ci, bi, pi, Li, *_ = test(I[tk], sg * I[sym], I["SPY"], 13 * 3)
            rows.append(dict(tk=tk, factor=name, etf=sym, raw_d=raw, r_d=c, beta_d=b, p_d=p, n_d=L,
                             win_same=same, win_min=min(wins), win_max=max(wins), r_i=ci, beta_i=bi, p_i=pi, n_i=Li))
    df = pd.DataFrame(rows)
    df.to_csv(sys.argv[1] if len(sys.argv) > 1 else "research/cross_asset_results.csv", index=False)
    print(df.round(3).to_string())

main()
