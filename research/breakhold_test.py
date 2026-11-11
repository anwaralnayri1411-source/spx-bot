#!/usr/bin/env python3
"""H-BREAKHOLD: هل تحسّن قاعدة «الكسر والثبات» نتيجة بوت SPX بالدولار؟ (مسجّل مسبقاً في CLAUDE.md 11/11/2026). اختبار فقط.

أداة H-NOCHASE نفسها: قرار البوت (`direction_points` وقيوده) كل 5 دقائق من 09:45 ببيانات متأخرة 6 دقائق حتى 15:00،
عقود SPXW حقيقية من Alpaca بسعر 100–400$، وخروج البوت. لكل ذراع سياسة يوم كاملة بقيود التكرار والوقفين الخاصة به:
  A    الحالي.
  BH   الكامل (الرئيسي): صوت نطاق أول 30د بعد الثبات فقط + الحدث الحديث يحدد الجهة + شرط المكان.
  PL   المكان وحده (وصفي).
  EV   الأحداث وحدها مع شرط الثبات لنطاق أول 30د (وصفي).
تشغيل (الخادم، ALPACA): SPXBOT_DATA=/opt/spxbot/data python research/breakhold_test.py [--report]
المخرج: research/breakhold_results.md و SPXBOT_DATA/breakhold_rows.csv
"""
import os
import sys
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import options_bot as ob  # noqa: E402
import backtest_spx_v2 as bt  # noqa: E402
import nochase_test as nc  # noqa: E402
import breakhold as bh  # noqa: E402

NY = ob.NY
DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
ROWS = os.path.join(DATA, "breakhold_rows.csv")
TRADES = os.path.join(DATA, "breakhold_trades.csv")
OUT = os.path.join(HERE, "breakhold_results.md")
ARMS = {"a": "A الحالي", "bh": "BH الكسر والثبات (الكامل)", "pl": "المكان وحده", "ev": "الأحداث وحدها"}


def decide(arm, c, closes, minutes, run_m):
    """(الجهة أو None، سبب) لذراع واحد في فحص واحد."""
    use_ev = arm in ("bh", "ev")
    use_pl = arm in ("bh", "pl")
    cc = dict(c)
    if use_ev and cc.get("orb") and bh.orb_hold(closes, cc["orb"]) is None:
        cc["orb"] = None                      # الكسر غير ثابت: لا صوت لنطاق أول 30د
    bull, bear, _ = ob.direction_points(cc)
    side = ("CALL" if bull > bear else "PUT") if abs(bull - bear) >= ob.MIN_EDGE else None
    evt = None
    if use_ev or use_pl:
        lv = bh.levels(c.get("orb"), c.get("pd_hl"), min(closes), max(closes))
        evt = bh.latest(bh.events(closes, lv, start=6), len(closes), minutes)
    why = ""
    if use_ev and evt:
        new = bh.DIR[evt["kind"]]
        why = ("قلب" if side and new != side else "حدث") + ":" + evt["kind"]
        side = new
    if side and use_pl:
        day_hl = (c["day_hi"], c["day_lo"])
        if bh.place_block(side, c["S"], day_hl, evt if use_ev or use_pl else None, run_m):
            return None, "مكان"
    return side, why


def run():
    data = nc.load()
    spx = data["spx"]
    dates = sorted(set(spx.index.date))
    rows, trades, cache = [], [], {}
    for di, d in enumerate(dates):
        g, mm = bt.day_bars(spx, d)
        if len(g) < 70 or di == 0:
            continue
        prev_d = dates[di - 1]
        scans = {}
        for run_m in range(nc.FIRST_M, nc.LAST_M + 1, 5):
            cutoff = run_m - nc.DELAY
            c = bt.build_c(data, d, prev_d, cutoff, run_m)
            if c is None or bt.events_block(d, run_m):
                scans[run_m] = None
                continue
            today = bt._upto(spx, d, cutoff)
            c["day_hi"], c["day_lo"] = float(today["High"].max()), float(today["Low"].min())
            closes = [float(x) for x in today["Close"].values]
            minutes = list(today.index.hour * 60 + today.index.minute)
            scans[run_m] = (c, closes, minutes)
        out = {"day": d.isoformat()}
        for arm in ARMS:
            last_side, last_t, stops, usd, n = None, None, 0, 0.0, 0
            for run_m, s in scans.items():
                if not s:
                    continue
                side, why = decide(arm, *s, run_m)
                if not side:
                    continue
                if stops >= ob.MAX_DAILY_STOPS or (last_side == side and last_t is not None and run_m - last_t < ob.REPEAT_MINUTES):
                    continue
                c = s[0]
                t0 = datetime.combine(d, datetime.min.time(), NY).replace(hour=run_m // 60, minute=run_m % 60)
                K = nc.pick_real(nc.real_bars_many(cache, d, side, nc.otm_strikes(c["S"], side)), c["S"], side, t0)
                b = nc.real_bars(cache, d, side, K) if K is not None else None
                r = nc.follow_real(b, t0) if b is not None and len(b) >= 3 else None
                if r is None:
                    continue
                last_side, last_t = side, run_m
                u = r[0] * r[1] * 100
                usd += u
                n += 1
                stops += r[2] == "stop"
                trades.append({"day": d.isoformat(), "arm": arm, "time": f"{run_m // 60:02d}:{run_m % 60:02d}", "side": side,
                               "S": round(c["S"], 2), "K": K, "usd": round(u, 2), "exit": r[2], "why": why})
            out[f"{arm}_usd"], out[f"{arm}_n"] = round(usd, 2), n
        rows.append(out)
        print(d, {k: out[f"{k}_usd"] for k in ARMS}, flush=True)
    df = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(ROWS), exist_ok=True)
    df.to_csv(ROWS, index=False)
    pd.DataFrame(trades).to_csv(TRADES, index=False)
    return df


def verdict(df, arm="bh"):
    x = df.assign(dd=df[f"{arm}_usd"] - df["a_usd"])
    m, ci = nc.boot_day(x, "dd")
    days = sorted(x["day"].unique())
    mid = days[len(days) // 2]
    h = (x[x["day"] < mid]["dd"].mean(), x[x["day"] >= mid]["dd"].mean())
    ok = m > 0 and ci[0] > 0 and min(h) > 0
    return ("مدعوم" if ok else "غير مدعوم"), m, ci, h, nc.boot_p(x, "dd")


def report(df):
    v = verdict(df)
    L = ["# H-BREAKHOLD: الكسر والثبات في بوت SPX (نتائج آلية)", "",
         f"- {len(df)} يوماً ({df['day'].min()} → {df['day'].max()})، لكل ذراع سياسة يوم كاملة بعقود SPXW حقيقية 100–400$.", "",
         "| الذراع | صفقات | الصافي $ | متوسط اليوم $ | فاصل 95% | أيام رابحة |", "|---|---|---|---|---|---|"]
    for k, nm in ARMS.items():
        m, ci = nc.boot_day(df, f"{k}_usd")
        L.append(f"| {nm} | {int(df[f'{k}_n'].sum())} | {df[f'{k}_usd'].sum():+,.0f}$ | {m:+.1f}$ | [{ci[0]:+.1f}، {ci[1]:+.1f}] | "
                 f"{(df[f'{k}_usd'] > 0).mean() * 100:.0f}% |")
    L += ["", "## الفرق عن الحالي (دولار لليوم)", "", "| الذراع | الفرق | فاصل 95% | p | النصفان |", "|---|---|---|---|---|"]
    for k in ("bh", "pl", "ev"):
        _, m, ci, h, p = verdict(df, k)
        L.append(f"| {ARMS[k]} | {m:+.1f}$ | [{ci[0]:+.1f}، {ci[1]:+.1f}] | {p:.3f} | {h[0]:+.1f} / {h[1]:+.1f} |")
    L += ["", f"⚖️ **الحكم الرئيسي (BH − A): {v[0]}** (المكان وحده والأحداث وحدها وصفيان بلا حكم)."]
    t = df[df["day"] == "2026-11-10"]
    if len(t):
        r = t.iloc[0]
        L += ["", "## يوم CPI 10/11", "", " | ".join(f"{ARMS[k]}: {r[f'{k}_usd']:+,.0f}$ ({int(r[f'{k}_n'])} صفقة)" for k in ARMS)]
    L += ["", "تحفظ: قرار البوت المعاد بلا الجدران والأخبار وحجم الكول/البوت؛ أسعار آخر صفقة في الشمعة؛ عينة أيام قليلة."]
    return "\n".join(L) + "\n"


def main(argv):
    df = pd.read_csv(ROWS) if "--report" in argv else run()
    md = report(df)
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main(sys.argv[1:])
