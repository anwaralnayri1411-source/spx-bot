"""مستويات اليوم لمؤشر SPX محسوبة من سلسلة الخيارات (مجانية ومتأخرة نحو 15 دقيقة).

يحسب: أقرب جدار شراء/بيع (فائدة مفتوحة)، نقطة التحول (تقاطع جاما الصنّاع الصفرية)،
مستوى الجاذبية (Max Pain)، ونطاق الحركة المتوقع لساعة و4 ساعات وما تبقى من اليوم.
"""
import math
import os
import sys
import time
from datetime import date

import numpy as np
import pandas as pd
import yfinance as yf

import options_bot as ob

LOG = "levels_log.csv"
COLS = ["time", "spot", "flip", "support", "resistance", "gravity", "em_1h", "em_4h", "em_rest", "atm_iv"]


def _chain_rows(t, exps, S):
    rows = []
    for e, dte in exps:
        try:
            ch = t.option_chain(e)
        except Exception:
            continue
        T = max(dte, 0.25) / 365
        for side, df in (("c", ch.calls), ("p", ch.puts)):
            for r in df.itertuples():
                K = float(r.strike)
                if abs(K - S) / S > 0.03:
                    continue
                oi = 0.0 if pd.isna(r.openInterest) else float(r.openInterest)
                iv = 0.0 if pd.isna(r.impliedVolatility) else float(r.impliedVolatility)
                if oi > 0:
                    rows.append((side, K, oi, iv, T))
        time.sleep(0.2)
    return rows


def _gex_at(rows, Sx):
    g = 0.0
    for side, K, oi, iv, T in rows:
        if iv <= 0.03:
            continue
        v = ob.bs_gamma(Sx, K, T, iv) * oi * 100 * Sx * Sx * 0.01
        g += v if side == "c" else -v
    return g


def compute(n=None):
    n = n or ob.now_ny()
    t = yf.Ticker("^SPX")
    bars = t.history(period="5d", interval="5m")["Close"].dropna()
    if bars.empty:
        raise RuntimeError("لا توجد بيانات SPX الآن.")
    S = float(bars.iloc[-1])
    opts = list(t.options)
    today = n.date()
    exps = [(e, (date.fromisoformat(e) - today).days) for e in opts
            if 0 <= (date.fromisoformat(e) - today).days <= 2][:3]
    if not exps:
        raise RuntimeError("لا توجد سلسلة خيارات قريبة.")
    rows = _chain_rows(t, exps, S)
    if not rows:
        raise RuntimeError("لا توجد فائدة مفتوحة كافية.")

    # الجدران: أكبر فائدة مفتوحة فوق السعر (شراء) وتحته (بيع)
    calls, puts = {}, {}
    for side, K, oi, iv, T in rows:
        (calls if side == "c" else puts)[K] = (calls if side == "c" else puts).get(K, 0) + oi
    # الجدار المهم الأقرب: ضمن 1.2% من السعر وبفائدة مفتوحة لا تقل عن 40% من أكبر جدار في النطاق
    def nearest_wall(book, up):
        band = {k: v for k, v in book.items() if (k > S if up else k < S) and abs(k - S) / S <= 0.012}
        if not band:
            return None
        top = max(band.values())
        ok = [k for k, v in band.items() if v >= 0.4 * top]
        return min(ok, key=lambda k: abs(k - S))

    resistance = nearest_wall(calls, True)
    support = nearest_wall(puts, False)

    # نقطة التحول: أقرب مستوى تتغير عنده إشارة صافي جاما
    flip = None
    grid = np.arange(S * 0.985, S * 1.015, max(S * 0.0005, 1.0))
    vals = [_gex_at(rows, x) for x in grid]
    best = None
    for i in range(1, len(grid)):
        if vals[i - 1] == 0 or vals[i - 1] * vals[i] < 0:
            x = float(grid[i])
            if best is None or abs(x - S) < abs(best - S):
                best = x
    flip = best
    net_gex = _gex_at(rows, S)

    # الجاذبية (Max Pain) لأقرب انتهاء
    gravity = None
    try:
        ch0 = t.option_chain(exps[0][0])
        gravity = ob.max_pain(ch0.calls, ch0.puts)
    except Exception:
        pass

    # نطاق الحركة المتوقع من التذبذب الضمني للعقود القريبة من السعر
    iv_atm = None
    try:
        ch0 = t.option_chain(exps[0][0])
        near = pd.concat([ch0.calls, ch0.puts])
        near = near[(near["strike"] - S).abs() <= S * 0.003]
        near = near[near["impliedVolatility"] > 0.03]
        if len(near):
            iv_atm = float(near["impliedVolatility"].median())
    except Exception:
        pass
    vix, _ = ob.vix_info()
    sig = vix / 100 if vix else None          # التذبذب الضمني للمؤشر (VIX) أوثق من IV المتأخر للعقود
    if iv_atm is None or iv_atm < 0.08:
        iv_atm = sig
    close = n.replace(hour=16, minute=0, second=0, microsecond=0)
    rest_h = max((close - n).total_seconds() / 3600, 0.0) if n.weekday() < 5 else 6.5
    rest_h = min(rest_h, 6.5)

    def em(h):
        return None if iv_atm is None else S * iv_atm * math.sqrt(max(h, 0) / (24 * 365))

    return {"time": n.strftime("%Y-%m-%d %H:%M"), "spot": S, "flip": flip, "support": support,
            "resistance": resistance, "gravity": gravity, "net_gex": net_gex, "atm_iv": iv_atm,
            "em_1h": em(1), "em_4h": em(4), "em_rest": em(rest_h) if rest_h > 0 else None}


def block(d):
    S = d["spot"]

    def dist(x):
        return "" if x is None else f" ({x - S:+,.0f})"

    L = [f"📍 SPX {S:,.1f}"]
    if d.get("net_gex") is not None:
        L.append("🧲 جاما الصنّاع موجبة، ميل للتماسك" if d["net_gex"] > 0 else "🌪️ جاما الصنّاع سالبة، الحركة قد تتسع")
    if d["resistance"]:
        L.append(f"⬆️ أقرب جدار فوق: <b>{d['resistance']:,.0f}</b>{dist(d['resistance'])}")
    if d["support"]:
        L.append(f"⬇️ أقرب دعم تحت: <b>{d['support']:,.0f}</b>{dist(d['support'])}")
    if d["flip"]:
        L.append(f"🔁 نقطة التحول: {d['flip']:,.0f}{dist(d['flip'])}")
    if d["gravity"]:
        L.append(f"🎯 الجاذبية: {d['gravity']:,.0f}{dist(d['gravity'])}")
    if d["em_1h"]:
        rest = f" · المتبقي ±{d['em_rest']:.0f}" if d.get("em_rest") else ""
        L.append(f"📏 الحركة المتوقعة: ساعة ±{d['em_1h']:.0f} · 4 ساعات ±{d['em_4h']:.0f}{rest}")
    return "\n".join(L)


def log(d):
    try:
        new = not os.path.exists(LOG)
        pd.DataFrame([{k: (round(d[k], 2) if isinstance(d.get(k), float) else d.get(k)) for k in COLS}]).to_csv(
            LOG, mode="a", header=new, index=False)
    except Exception as e:
        print("تعذر حفظ المستويات:", e)


if __name__ == "__main__":
    d = compute()
    log(d)
    txt = "📐 <b>مستويات SPX من بياناتنا</b> #تحليل\n" + block(d)
    if "--compare" in sys.argv:
        txt += ("\n\n<b>مقارنة مع تقرير المحلل (08:00 نيويورك، قبل الافتتاح)</b>\n"
                "دعم 7,798 · مقاومة 7,810 · تحول 7,811 · جاذبية 7,767 · ساعة ±10 · 4 ساعات ±21\n"
                "<i>التوقيتان مختلفان: بياناتنا متأخرة 15 دقيقة والسوق الآن مفتوح، فالمقارنة تقريبية.</i>")
    ob.send_telegram(txt)
