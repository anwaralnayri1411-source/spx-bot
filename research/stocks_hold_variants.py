#!/usr/bin/env python3
"""مدد الثبات لقاعدة «الكسر والثبات» في بوت الشركات (وصفي، 11/11/2026 بطلب المستخدم: «ساعتين كثيرة… الهادئة والمتقلبة»). اختبار فقط.

على توصيات بوت الشركات الفعلية نفسها (stocks_log + stocks_track) كما في H-CONFLUENCE T2: لكل نسخة من القاعدة
(مدة الثبات داخل اليوم، وتعريف مستوى 20 يوماً) نحسب ما كانت ستحجبه وفرق صافي اليوم عن الحالي. الممنوعة = 0$.
  المدد: 30د، 45د، 60د، 120د (الحالية)، وتكيّفية حسب تقلب السهم (هادئ 30د / متقلب 45د أو 60د).
  المستوى: «شامل» = أعلى/أدنى آخر 20 يوماً مع أمس (الحالي، فإغلاق أمس لا يكون خلفه أبداً)،
           «قبل أمس» = 20 يوماً تنتهي قبل أمس، فإغلاق أمس خلفه = ثبات بيوم كامل.
وصفي بلا حكم (مقارنات كثيرة على عينة صغيرة).
تشغيل: python research/stocks_hold_variants.py   المخرج: research/stocks_hold_variants_results.md
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import nochase_test as nc  # noqa: E402
import stocks_breakhold_test as sbt  # noqa: E402

OUT = os.path.join(HERE, "stocks_hold_variants_results.md")
NY = "America/New_York"
VOL_RV = 0.45                     # تقلب سنوي (20 يوماً) ≥ هذا = سهم متقلب
FIXED = {"30د": 6, "45د": 9, "60د": 12, "120د (الحالية)": 24}
ADAPT = {"تكيّفية 30/45": (6, 9), "تكيّفية 30/60": (6, 12)}


def block(side, S, L, prev_close, closes, hold, chase=sbt.CHASE):
    beyond = (lambda x: x > L) if side == "CALL" else (lambda x: x < L)
    last = closes[-hold:]
    unheld = beyond(S) and not beyond(prev_close) and not (len(last) == hold and all(beyond(x) for x in last))
    held = beyond(S) and not unheld
    move = S / prev_close - 1
    return unheld or ((move >= chase if side == "CALL" else move <= -chase) and not held)


def features(df):
    import yfinance as yf
    rows = []
    for tk, g in df.groupby("ticker"):
        try:
            d = yf.Ticker(tk).history(period="1y", auto_adjust=False)
            m = yf.Ticker(tk).history(period="60d", interval="5m", auto_adjust=False)
            d.index, m.index = d.index.tz_convert(NY), m.index.tz_convert(NY)
        except Exception as e:  # noqa: BLE001
            print(tk, e)
            continue
        for r in g.itertuples():
            t = pd.Timestamp(r.time).tz_convert(NY)
            day = t.date()
            past = d[d.index.date < day]
            if len(past) < 22:
                continue
            td = m[(m.index.date == day) & (m.index + pd.Timedelta(minutes=5) <= t)]
            if not len(td):
                continue
            rv = float(np.log(past["Close"]).diff().tail(20).std() * np.sqrt(252))
            rows.append({**r._asdict(), "S": float(td["Close"].iloc[-1]), "prev_close": float(past["Close"].iloc[-1]),
                         "hi_incl": float(past["High"].iloc[-20:].max()), "lo_incl": float(past["Low"].iloc[-20:].min()),
                         "hi_excl": float(past["High"].iloc[-21:-1].max()), "lo_excl": float(past["Low"].iloc[-21:-1].min()),
                         "closes": [float(x) for x in td["Close"].values], "rv": rv, "usd": r.ret * r.price * 100})
    return rows


def evaluate(rows, hold_of, lvl):
    out = []
    for r in rows:
        L = r[f"hi_{lvl}"] if r["side"] == "CALL" else r[f"lo_{lvl}"]
        out.append({"day": r["day"], "usd": r["usd"], "blocked": block(r["side"], r["S"], L, r["prev_close"], r["closes"], hold_of(r))})
    x = pd.DataFrame(out)
    x["rule"] = x["usd"].where(~x["blocked"], 0.0)
    g = x.groupby("day").agg(cur=("usd", "sum"), rule=("rule", "sum")).reset_index()
    g["dd"] = g["rule"] - g["cur"]
    m, ci = nc.boot_day(g, "dd")
    days = sorted(g["day"].unique())
    mid = days[len(days) // 2]
    h = (g[g["day"] < mid]["dd"].mean(), g[g["day"] >= mid]["dd"].mean())
    b = x[x["blocked"]]
    return {"n": int(x["blocked"].sum()), "b_usd": b["usd"].sum(), "b_win": (b["usd"] > 0).mean() * 100 if len(b) else 0,
            "keep": x["rule"].sum(), "m": m, "ci": ci, "h": h}


def main():
    recs = sbt.load_recs()
    rows = features(recs)
    vol = sum(r["rv"] >= VOL_RV for r in rows)
    L = ["# مدد الثبات لقاعدة الكسر والثبات في بوت الشركات (وصفي)", "",
         f"- {len(rows)} توصية فعلية ({recs['day'].min()} → {recs['day'].max()})؛ الحالي الصافي {sum(r['usd'] for r in rows):+,.0f}$.",
         f"- سهم متقلب = تقلب 20 يوماً ≥ {VOL_RV:.0%} سنوياً: {vol} توصية على أسهم متقلبة، {len(rows) - vol} على هادئة.", "",
         "| المدة | المستوى | تحجب | نتيجة المحجوبة | رابحة المحجوبة | نتيجة الباقية | الفرق لليوم | فاصل 95% | النصفان |",
         "|---|---|---|---|---|---|---|---|---|"]
    variants = [(nm, (lambda r, h=h: h)) for nm, h in FIXED.items()]
    variants += [(nm, (lambda r, q=q, v=v: v if r["rv"] >= VOL_RV else q)) for nm, (q, v) in ADAPT.items()]
    for lvl, lnm in (("incl", "شامل أمس (الحالي)"), ("excl", "قبل أمس")):
        for nm, f in variants:
            e = evaluate(rows, f, lvl)
            L.append(f"| {nm} | {lnm} | {e['n']} | {e['b_usd']:+,.0f}$ | {e['b_win']:.0f}% | {e['keep']:+,.0f}$ | {e['m']:+.1f}$ | "
                     f"[{e['ci'][0]:+.1f}، {e['ci'][1]:+.1f}] | {e['h'][0]:+.1f} / {e['h'][1]:+.1f} |")
    L += ["", "وصفي بلا حكم: مقارنات كثيرة على نحو 125 توصية في أسابيع قليلة؛ منع فقط بلا دخول متأخر بديل."]
    md = "\n".join(L) + "\n"
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main()
