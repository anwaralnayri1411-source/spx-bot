#!/usr/bin/env python3
"""اختبار فقط (لا يغيّر أي بوت): «السعر اللحظي على شمعة 5 دقائق» ببيانات لحظية فقط، بلا ياهو. بطلب المستخدم 27/10/2026.

الفرضية (مسجّلة هنا قبل التشغيل): نقاط اتجاه البوت (options_bot.direction_points) محسوبة في كل دقيقة على شموع 5 دقائق
تكون آخرها الشمعة الجارية بالسعر اللحظي، تعطي حركة SPY في اتجاه الإشارة بعد 30 و60 دقيقة أعلى من 95% من الاختيارات
العشوائية للاتجاه في الأوقات نفسها (10,000 اختيار). وإلا: لا تتفوق على العشوائي.
المرجع (وصفي): النسخة نفسها لكن القرار عند اكتمال الشمعة فقط.

البيانات: Alpaca وحده. شموع SPY دقيقة (مصدر SIP التاريخي، حجم كامل) وشموع يومية. أدق بيانات تاريخية متاحة لنا هي الدقيقة،
فسعر «الثانية» هنا هو إغلاق آخر دقيقة (لا تاريخ ثوانٍ عندنا). SPY بدل SPX (يتحركان معاً؛ النقاط لا تتأثر بالمقياس).
المكوّنات: المتوسطات اليومية، VWAP، ema9/21، r30، الفجوة، نطاق أول 30د، أعلى/أدنى أمس. بلا VIX (لا مصدر لحظي له بلا ياهو)،
وبلا الجدران وGEX ونقطة الألم وES والقياديات والكول/البوت. الإشارة: فرق ≥ MIN_EDGE، 9:50–14:55، منع تكرار 90 دقيقة.
تشغيل (يحتاج ALPACA_KEY/ALPACA_SECRET): python research/live_tick_test.py [--days 60]
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, time as dtime, timedelta, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import options_bot as ob  # noqa: E402

NY = ob.NY
OUT = os.path.join(HERE, "live_tick_test_results.md")
FIRST, LAST = dtime(9, 50), dtime(14, 55)
REPEAT = timedelta(minutes=ob.REPEAT_MINUTES)


# ============================ البيانات (Alpaca فقط) ============================
def _get(path, params):
    url = f"https://data.alpaca.markets{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"APCA-API-KEY-ID": os.getenv("ALPACA_KEY", ""), "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET", ""),
                                               "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def bars(tf, start, end):
    out, tok = [], None
    while True:
        p = {"timeframe": tf, "start": start, "end": end, "feed": "sip", "adjustment": "split", "limit": 10000}
        if tok:
            p["page_token"] = tok
        d = _get("/v2/stocks/SPY/bars", p)
        out += d.get("bars") or []
        tok = d.get("next_page_token")
        if not tok:
            break
    df = pd.DataFrame(out)
    df.index = pd.DatetimeIndex(pd.to_datetime(df["t"], utc=True)).tz_convert(NY)
    return df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})[["Open", "High", "Low", "Close", "Volume"]]


def load(days):
    end = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat().replace("+00:00", "Z")
    start = (datetime.now(timezone.utc) - timedelta(days=int(days * 1.5) + 10)).isoformat().replace("+00:00", "Z")
    m1 = bars("1Min", start, end)
    m1 = m1[(m1.index.time >= dtime(9, 30)) & (m1.index.time < dtime(16, 0))]
    d1 = bars("1Day", (datetime.now(timezone.utc) - timedelta(days=500)).isoformat().replace("+00:00", "Z"), end)
    return m1, d1["Close"]


# ============================ الحساب (نقي) ============================
def five_min(m1):
    """شموع 5 دقائق مكتملة من شموع الدقيقة (الفهرس = بداية الشمعة)."""
    g = m1.resample("5min", label="left", closed="left")
    out = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(), "Close": g["Close"].last(), "Volume": g["Volume"].sum()})
    return out.dropna(subset=["Close"])


def frame_at(m1_day, done5_hist, n, live):
    """شموع القرار عند الدقيقة n: الشموع المكتملة قبل بداية الشمعة الجارية + (live) الشمعة الجارية بالسعر اللحظي."""
    b = n.replace(minute=n.minute - n.minute % 5, second=0, microsecond=0)
    done = done5_hist[done5_hist.index < b]
    if not live:
        return done
    cur = m1_day[(m1_day.index >= b) & (m1_day.index < n)]
    if cur.empty:
        return done
    row = pd.DataFrame({"Open": [cur["Open"].iloc[0]], "High": [cur["High"].max()], "Low": [cur["Low"].min()], "Close": [cur["Close"].iloc[-1]],
                        "Volume": [cur["Volume"].sum()]}, index=pd.DatetimeIndex([b]))
    return pd.concat([done, row])


def points_at(ix, m1_day, n, daily_prev):
    f = ob.bar_fields(ix, n)
    S = f["S"]
    d = pd.concat([daily_prev, pd.Series([S])], ignore_index=True)
    today = m1_day[m1_day.index < n]
    vwap = float(((today["High"] + today["Low"] + today["Close"]) / 3 * today["Volume"]).sum() / today["Volume"].sum()) if today["Volume"].sum() > 0 else None
    c = {"n": n, "S": S, "sma20": float(d.rolling(20).mean().iloc[-1]), "sma50": float(d.rolling(50).mean().iloc[-1]),
         "above_vwap": True if vwap is None else S > vwap, "vwap_spx": vwap, "map": None, "max_pain": None, "es": None, "leaders": None, "pc": None, "vix_day": None}
    c.update({k: f[k] for k in ob.BAR_KEYS if k != "S"})
    bull, bear, _ = ob.direction_points(c)
    return bull, bear, S


def run(m1, daily, every_minute):
    """إشارات لكل يوم: every_minute=True → كل دقيقة بالشمعة الجارية؛ False → عند اكتمال كل شمعة 5د فقط."""
    m5 = five_min(m1)
    days = sorted(set(m1.index.date))
    events = []
    for i, day in enumerate(days):
        if i < 5:
            continue                                   # إحماء: نافذة البوت 5 أيام
        hist_days = days[i - 4:i + 1]
        m5h = m5[np.isin(m5.index.date, hist_days)]
        m1d = m1[m1.index.date == day]
        prev = daily[daily.index.date < day]
        last = {}
        n = datetime.combine(day, FIRST, tzinfo=NY)
        end = datetime.combine(day, LAST, tzinfo=NY)
        while n <= end:
            if every_minute or n.minute % 5 == 0:
                ix = frame_at(m1d, m5h, n, live=every_minute)
                if len(ix) and ix.index[-1].date() == day:
                    bull, bear, S = points_at(ix, m1d, n, prev)
                    if abs(bull - bear) >= ob.MIN_EDGE:
                        side = 1 if bull > bear else -1
                        if not (side in last and n - last[side] < REPEAT):
                            last[side] = n
                            px = m1d[m1d.index < n]["Close"]
                            p0 = float(px.iloc[-1])
                            ev = {"day": day, "t": n, "side": side, "P": p0}
                            for h in (30, 60):
                                fut = m1d[m1d.index < n + timedelta(minutes=h)]["Close"]
                                ev[f"f{h}"] = side * (float(fut.iloc[-1]) / p0 - 1) if len(fut) else None
                            events.append(ev)
            n += timedelta(minutes=1)
    return events


def vs_random(events, key, sims=10000, seed=5):
    e = [x for x in events if x.get(key) is not None]
    if not e:
        return None
    real = float(np.mean([x[key] for x in e]))
    raw = np.array([x[key] * x["side"] for x in e])
    rng = np.random.default_rng(seed)
    rand = (raw[None, :] * rng.choice([-1, 1], (sims, len(raw)))).mean(1)
    return {"n": len(e), "real": real, "lo": float(np.percentile(rand, 2.5)), "hi": float(np.percentile(rand, 97.5)),
            "p": float((rand >= real).mean()), "hit": float(np.mean([x[key] > 0 for x in e]))}


def report(res, meta):
    L = ["# السعر اللحظي على شمعة 5 دقائق مقابل العشوائي (اختبار، بيانات Alpaca فقط)", "", f"- {meta}",
         "- سعر «الثانية» = إغلاق آخر دقيقة (أدق تاريخ متاح). SPY بدل SPX. بلا VIX والجدران ونقطة الألم وES والقياديات. حركة SPY ليست ربح العقد.", "",
         "| النسخة | المدة | إشارات | حركة في اتجاه الإشارة | نطاق العشوائي 95% | إصابة الاتجاه | عشوائي أفضل منها |", "|---|---|---|---|---|---|---|"]
    names = {"tick": "لحظي كل دقيقة على الشمعة الجارية", "close": "مرجع: عند اكتمال الشمعة فقط"}
    for k in ("tick", "close"):
        for h in ("f30", "f60"):
            r = res[k][h]
            if r:
                L.append(f"| {names[k]} | {h[1:]}د | {r['n']} | {100 * r['real']:+.3f}% | {100 * r['lo']:+.3f}… {100 * r['hi']:+.3f}% | {100 * r['hit']:.0f}% | {100 * r['p']:.0f}% |")
    t = res["tick"]["f30"]
    if t:
        L += ["", f"⚖️ السعر اللحظي على شمعة 5د: {'يتفوق على العشوائي' if t['p'] < 0.05 else 'لا يتفوق على العشوائي'} (حركة 30د {100 * t['real']:+.3f}%، أفضل من {100 * (1 - t['p']):.0f}% من الاختيارات العشوائية)"]
    return L


def main(argv):
    days = int(argv[argv.index("--days") + 1]) if "--days" in argv else 60
    m1, daily = load(days)
    dl = sorted(set(m1.index.date))[-(days + 5):]
    m1 = m1[np.isin(m1.index.date, dl)]
    res = {}
    for k, every in (("tick", True), ("close", False)):
        ev = run(m1, daily, every)
        res[k] = {h: vs_random(ev, h) for h in ("f30", "f60")}
    L = report(res, f"{len(dl) - 5} يوم تداول ({dl[5]} → {dl[-1]})")
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
