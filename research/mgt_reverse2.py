#!/usr/bin/env python3
"""المرحلة 2 من الهندسة العكسية لـMGT: كل أوقات الدخول المحتملة، وحالة العقود الآجلة ES لحظة الدخول (للجلسة الليلية)،
وطرق خروج بديلة على دخولاتهم نفسها، وبنية «السلّم» (سترايكات متتالية)، واتجاه الصفقات مقابل اتجاه اليوم. قراءة فقط.
تشغيل (الخادم): SPXBOT_DATA=/opt/spxbot/data python research/mgt_reverse2.py
"""
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mgt_reverse as mr  # noqa: E402

DATA = mr.DATA
OUT = os.path.join(DATA, "mgt_reverse2_rows.csv")


def candidates(b, entry, peak):
    """كل الدقائق التي يقع فيها سعر الدخول وتبلغ بعدها القمة (±5%)."""
    lo, hi = b["l"].values, b["h"].values
    hi_after = np.maximum.accumulate(hi[::-1])[::-1]
    tol = max(mr.PX_TOL * entry, 0.05)
    return [b.index[i] for i in range(len(b)) if lo[i] - tol <= entry <= hi[i] + tol and abs(hi_after[i] / peak - 1) <= mr.PEAK_TOL]


def exits(b, t0, entry, stop):
    """طرق خروج على الدخول نفسه (حتى 15:55)."""
    x = b[(b.index >= t0) & (b.index.strftime("%H:%M") <= "15:55")]
    if x.empty:
        return {}
    hs, ls, cs = x["h"].values, x["l"].values, x["c"].values
    out = {}

    def first(cond_tp, tp_mult, use_stop=True):
        for h, l in zip(hs, ls):
            if use_stop and l <= stop:
                return stop / entry - 1
            if h >= entry * tp_mult:
                return tp_mult - 1
        return cs[-1] / entry - 1

    out["x_stop_tp50"] = first(True, 1.5)
    out["x_stop_tp100"] = first(True, 2.0)
    out["x_stop_tp200"] = first(True, 3.0)
    out["x_nostop_tp100"] = first(True, 2.0, use_stop=False)
    # نصف عند +100% والباقي بوقف متحرك 50% من القمة؛ قبل +100% وقفهم
    half, peak, ret = False, entry, None
    for h, l, c in zip(hs, ls, cs):
        if not half:
            if l <= stop:
                ret = stop / entry - 1
                break
            if h >= 2 * entry:
                half, peak = True, h
        else:
            peak = max(peak, h)
            if l <= peak * 0.5:
                ret = 0.5 * 1.0 + 0.5 * (peak * 0.5 / entry - 1)
                break
    if ret is None:
        ret = (0.5 * 1.0 + 0.5 * (cs[-1] / entry - 1)) if half else cs[-1] / entry - 1
    out["x_half100_trail"] = ret
    out["x_hold"] = cs[-1] / entry - 1
    return out


def es_state(es, t):
    """حركة ES منذ إغلاق 16:00 أمس وآخر 30/60 دقيقة لحظة t (للدخول الليلي وقبل الافتتاح)."""
    x = es[es.index <= t]
    if len(x) < 13:
        return {}
    prev = es[(es.index.date < t.date()) & (es.index.strftime("%H:%M") <= "16:00")]
    ref = float(prev["Close"].iloc[-1]) if len(prev) else np.nan
    p = float(x["Close"].iloc[-1])
    return {"es_from_close": p / ref - 1 if ref == ref else np.nan, "es_r30": p / float(x["Close"].iloc[-7]) - 1,
            "es_r60": p / float(x["Close"].iloc[-13]) - 1}


def run():
    import yfinance as yf
    trades = mr.parse_trades()
    es = yf.Ticker("ES=F").history(period="60d", interval="5m", auto_adjust=False)[["Close"]]
    es.index = es.index.tz_convert(mr.NY)
    daily = yf.Ticker("^GSPC").history(period="3mo", auto_adjust=False)
    daily.index = daily.index.tz_localize(None).date
    rows = []
    for day, g in trades.groupby("day"):
        try:
            bars = mr.opt_bars_1m([mr.occ(day, r.side, r.strike) for r in g.itertuples()], day)
        except Exception as e:  # noqa: BLE001
            print("فشل", day, e)
            continue
        dd = datetime.fromisoformat(day).date()
        dly = daily[[x <= dd for x in daily.index]]
        day_ret = float(dly["Close"].iloc[-1] / dly["Close"].iloc[-2] - 1) if len(dly) >= 2 and dly.index[-1] == dd else np.nan
        gap = float(dly["Open"].iloc[-1] / dly["Close"].iloc[-2] - 1) if len(dly) >= 2 and dly.index[-1] == dd else np.nan
        for r in g.itertuples():
            b = bars.get(mr.occ(day, r.side, r.strike))
            row = {"day": day, "side": r.side, "strike": r.strike, "entry": r.entry, "peak": r.peak, "stop": r.stop,
                   "stop_ratio": r.stop / r.entry, "day_ret": day_ret, "gap": gap, "t_riyadh": r.t_riyadh}
            if b is None or b.empty:
                rows.append(row)
                continue
            row["first_bar"] = b.index[0].strftime("%H:%M")
            row["open_px"] = float(b[b.index.strftime("%H:%M") >= "09:30"]["o"].iloc[0]) if (b.index.strftime("%H:%M") >= "09:30").any() else np.nan
            cs = candidates(b, r.entry, r.peak)
            row["n_cand"] = len(cs)
            for tag, t0 in (("early", cs[0] if cs else None), ("late", cs[-1] if cs else None)):
                if t0 is None:
                    continue
                row[f"t_{tag}"] = t0.strftime("%H:%M")
                for k, v in exits(b, t0, r.entry, r.stop).items():
                    row[f"{k}_{tag}"] = v
                for k, v in es_state(es, t0).items():
                    row[f"{k}_{tag}"] = v
            rows.append(row)
        print(day, len(g), flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False)
    return df


if __name__ == "__main__":
    run()
