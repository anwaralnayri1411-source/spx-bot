"""إشارات تجريبية صامتة (لا تؤثر على التوصيات): توافق الأطر M5/M15/M30 واختراق المستويات.
تُسجَّل كل دورة SPX في signals_extra_log.csv لنقيس بعد أسبوع هل تفيد أم لا."""
import os

import pandas as pd
import yfinance as yf

import levels
import options_bot as ob

LOG = "signals_extra_log.csv"
COLS = ["time", "spot", "m5", "m15", "m30", "agree", "breakout", "level", "bot_side"]


def _trend(closes, fast=9, slow=21):
    if len(closes) < slow + 2:
        return 0
    f = closes.ewm(span=fast, adjust=False).mean().iloc[-1]
    s = closes.ewm(span=slow, adjust=False).mean().iloc[-1]
    return 1 if f > s else (-1 if f < s else 0)


def frames():
    """اتجاه كل إطار: +1 صاعد، -1 هابط (متوسط 9 مقابل 21 على إغلاقات الإطار)."""
    t = yf.Ticker("^SPX")
    px = t.history(period="5d", interval="5m")["Close"].dropna()
    out = {"m5": _trend(px)}
    for name, rule in (("m15", "15min"), ("m30", "30min")):
        out[name] = _trend(px.resample(rule).last().dropna())
    return px, out


def agreement(fr):
    v = [fr["m5"], fr["m15"], fr["m30"]]
    if all(x == 1 for x in v):
        return "up"
    if all(x == -1 for x in v):
        return "down"
    return "mixed"


def breakout(px, lv):
    """اختراق: الإغلاق الأخير (شمعة 5د مكتملة) عبر مستوى كان فوق/تحت إغلاق ما قبله."""
    if len(px) < 3:
        return "none", None
    prev, last = float(px.iloc[-3]), float(px.iloc[-2])    # آخر شمعة مكتملة وما قبلها
    for lvl in sorted([x for x in (lv.get("resistance"), lv.get("flip")) if x]):
        if prev <= lvl < last:
            return "up", lvl
    for lvl in sorted([x for x in (lv.get("support"), lv.get("flip")) if x], reverse=True):
        if prev >= lvl > last:
            return "down", lvl
    return "none", None


def snapshot(n, bot_side=""):
    px, fr = frames()
    lv = levels.compute(n)
    b, lvl = breakout(px, lv)
    row = {"time": n.strftime("%Y-%m-%d %H:%M"), "spot": round(float(px.iloc[-1]), 2), **fr,
           "agree": agreement(fr), "breakout": b, "level": "" if lvl is None else round(lvl, 1), "bot_side": bot_side}
    try:
        pd.DataFrame([row])[COLS].to_csv(LOG, mode="a", header=not os.path.exists(LOG), index=False)
    except Exception as e:
        print("تعذر حفظ الإشارات التجريبية:", e)
    return row


def review(days_back=7):
    """ملخص بعد الحدث: كم حالة اختراق/توافق، وحركة SPX بعد ~60 دقيقة بالاتجاه المتوقع."""
    try:
        d = pd.read_csv(LOG)
    except Exception:
        return None
    d["t"] = pd.to_datetime(d["time"])
    d = d[d["t"] >= d["t"].max() - pd.Timedelta(days=days_back)]
    out = {}
    for label, mask, sign in (("اختراق صاعد", d["breakout"] == "up", 1), ("كسر هابط", d["breakout"] == "down", -1),
                              ("توافق صاعد", d["agree"] == "up", 1), ("توافق هابط", d["agree"] == "down", -1)):
        moves = []
        for _, r in d[mask].iterrows():
            later = d[(d["t"].dt.date == r["t"].date()) & (d["t"] >= r["t"] + pd.Timedelta(minutes=55))]
            if len(later):
                moves.append(sign * (float(later.iloc[0]["spot"]) - float(r["spot"])))
        out[label] = (int(mask.sum()), len(moves), (sum(moves) / len(moves)) if moves else None,
                      (sum(1 for m in moves if m > 0) / len(moves)) if moves else None)
    return out
