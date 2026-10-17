"""مستويات الجاما (GEX) في الظل: SPX وSPY وQQQ وNDX وIWM وDIA + شركات قائمة المراقبة (33).
تسجيل وقياس فقط، لا يرسل ولا يغيّر أي توصية.
--snap : لقطة بعد الافتتاح بقليل (الفائدة المفتوحة OI تتحدّث ليلاً): جدار الكول/البوت، نقطة انقلاب الجاما، صافي GEX، Max Pain.
--eval : بعد الإغلاق: قمة/قاع/إغلاق اليوم لكل رمز لمقارنتها لاحقاً بالمستويات.
الافتراض المعياري: صنّاع السوق طويلو الكول وقصيرو البوت. تقريبي لأن بيانات ياهو متأخرة والـOI يومي."""
import csv
import math
import os
import sys
from datetime import date, datetime

import pandas as pd

INDICES = ["^SPX", "SPY", "QQQ", "^NDX", "IWM", "DIA"]
SNAP, OUTC = "gex_snapshots.csv", "gex_outcomes.csv"
HEAD = ["date", "time", "ticker", "spot", "exp", "dte", "call_wall", "put_wall", "flip", "net_gex_m", "max_pain",
        "oi_calls", "oi_puts", "strikes"]


def tickers():
    try:
        import stocks_bot as sb
        return INDICES + list(sb.WATCHLIST)
    except Exception:
        return INDICES


def _gamma(S, K, iv, T):
    if iv <= 0 or T <= 0 or S <= 0 or K <= 0:
        return 0.0
    sd = iv * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sd * sd) / sd
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (S * sd)


def levels(calls, puts, spot, dte):
    """يُرجع (call_wall, put_wall, flip, net_gex_بالملايين، max_pain، oi_calls، oi_puts، strikes)."""
    T = max(dte, 0.3) / 365
    c = calls[["strike", "openInterest", "impliedVolatility"]].fillna(0)
    p = puts[["strike", "openInterest", "impliedVolatility"]].fillna(0)
    if c.openInterest.sum() + p.openInterest.sum() < 50:
        return None
    above = c[c.strike >= spot]
    below = p[p.strike <= spot]
    call_wall = float(above.loc[above.openInterest.idxmax(), "strike"]) if len(above) and above.openInterest.max() > 0 else None
    put_wall = float(below.loc[below.openInterest.idxmax(), "strike"]) if len(below) and below.openInterest.max() > 0 else None
    g = {}
    for _, r in c.iterrows():
        g[r.strike] = g.get(r.strike, 0) + _gamma(spot, r.strike, r.impliedVolatility, T) * r.openInterest * 100 * spot * spot * 0.01
    for _, r in p.iterrows():
        g[r.strike] = g.get(r.strike, 0) - _gamma(spot, r.strike, r.impliedVolatility, T) * r.openInterest * 100 * spot * spot * 0.01
    ks = sorted(g)
    cum, flip, prev = 0.0, None, None
    for k in ks:
        cum += g[k]
        if prev is not None and prev < 0 <= cum and flip is None:
            flip = float(k)
        prev = cum
    # Max pain: السترايك الذي يقلّل إجمالي قيمة العقود عند الانتهاء
    best, bk = None, None
    for k in ks:
        pain = sum(max(0, k - s) * o for s, o in zip(p.strike, p.openInterest)) + \
               sum(max(0, s - k) * o for s, o in zip(c.strike, c.openInterest))
        if best is None or pain < best:
            best, bk = pain, k
    return (call_wall, put_wall, flip, round(sum(g.values()) / 1e6, 2), float(bk) if bk is not None else None,
            int(c.openInterest.sum()), int(p.openInterest.sum()), len(ks))


def snap(max_exp=2):
    import yfinance as yf
    now = datetime.now()
    rows = []
    for tk in tickers():
        try:
            t = yf.Ticker(tk)
            spot = float(t.history(period="1d")["Close"].iloc[-1])
            for e in list(t.options)[:max_exp]:
                dte = (date.fromisoformat(e) - date.today()).days
                ch = t.option_chain(e)
                lv = levels(ch.calls, ch.puts, spot, dte)
                if lv:
                    rows.append([date.today().isoformat(), now.strftime("%H:%M"), tk, round(spot, 2), e, dte, *lv])
        except Exception as ex:
            print("تعذر", tk, ex)
    _append(SNAP, HEAD, rows)
    print("سُجّل", len(rows), "صف")


def evaluate():
    import yfinance as yf
    rows = []
    for tk in tickers():
        try:
            h = yf.Ticker(tk).history(period="1d")
            r = h.iloc[-1]
            rows.append([date.today().isoformat(), tk, round(float(r.Open), 2), round(float(r.High), 2),
                         round(float(r.Low), 2), round(float(r.Close), 2)])
        except Exception as ex:
            print("تعذر", tk, ex)
    _append(OUTC, ["date", "ticker", "open", "high", "low", "close"], rows)
    print("سُجّل", len(rows), "نتيجة")


def _append(path, head, rows):
    fresh = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if fresh:
            w.writerow(head)
        w.writerows(rows)


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["--snap"]:
        snap()
    elif a == ["--eval"]:
        evaluate()
    else:
        print(__doc__)
