#!/usr/bin/env python3
"""H-BREAKHOLD وH-CONFLUENCE T1/T3: «الكسر والثبات» وتلاقي VWAP وFVG في بوت SPX (مسجّلة مسبقاً في CLAUDE.md 11/11/2026). اختبار فقط.

أداة H-NOCHASE نفسها: قرار البوت (`direction_points` وقيوده) كل 5 دقائق من 09:45 ببيانات متأخرة 6 دقائق حتى 15:00،
عقود SPXW حقيقية من Alpaca بسعر 100–400$، وخروج البوت. لكل ذراع سياسة يوم كاملة بقيود التكرار والوقفين الخاصة به:
  A    الحالي قبل الإصدار 18.
  BH   الكامل: صوت نطاق أول 30د بعد الثبات + الحدث الحديث يحدد الجهة + شرط المكان.
  PL   المكان وحده.            EV  الأحداث وحدها (= الإصدار 18).
  EVV  الأحداث + VWAP: صفقة الحدث لا تُقبل إلا إن وافق موضع SPY من VWAP جهتها.
  EVF  الأحداث + FVG: صفقة الحدث تنتظر عودة السعر إلى آخر فجوة قيمة 5د في جهتها (60 دقيقة)، وبلا فجوة تدخل فوراً.
تشغيل (الخادم، ALPACA): SPXBOT_DATA=/opt/spxbot/data python research/breakhold_test.py [--report]
المخرج: research/breakhold_results.md و SPXBOT_DATA/breakhold_rows.csv و breakhold_trades.csv
"""
import os
import sys
from datetime import datetime

import numpy as np
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
ARMS = {"a": "A الحالي (قبل الإصدار 18)", "bh": "BH الكسر والثبات (الكامل)", "pl": "المكان وحده", "ev": "الأحداث وحدها (الإصدار 18)",
        "evv": "الأحداث + VWAP", "evf": "الأحداث + FVG"}
FVG_WAIT = 60


def decide(arm, c, closes, minutes, highs, lows, run_m):
    """(الجهة أو None، سبب، القوة، منطقة FVG أو None) لذراع واحد في فحص واحد."""
    use_ev = arm in ("bh", "ev", "evv", "evf")
    use_pl = arm in ("bh", "pl")
    cc = dict(c)
    if use_ev and cc.get("orb") and bh.orb_hold(closes, cc["orb"]) is None:
        cc["orb"] = None                      # الكسر غير ثابت: لا صوت لنطاق أول 30د
    bull, bear, _ = ob.direction_points(cc)
    edge = abs(bull - bear)
    side = ("CALL" if bull > bear else "PUT") if edge >= ob.MIN_EDGE else None
    strength = min(10.0, edge * 1.5)
    evt = None
    if use_ev or use_pl:
        lv = bh.levels(c.get("orb"), c.get("pd_hl"), min(closes), max(closes))
        evt = bh.latest(bh.events(closes, lv, start=6), len(closes), minutes)
    why, zone = "", None
    if use_ev and evt:
        side, strength, mode = bh.apply(side, strength, evt)
        why = ("قلب" if mode == "flip" else "حدث") + ":" + evt["kind"]
        if arm == "evv" and (side == "CALL") != bool(c.get("above_vwap")):
            return None, "vwap", strength, None
        if arm == "evf":
            zone = bh.fvg_zone(highs, lows, side)
    if side and use_pl:
        if bh.place_block(side, c["S"], (c["day_hi"], c["day_lo"]), evt, run_m):
            return None, "مكان", strength, None
    return side, why, strength, zone


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
            scans[run_m] = (c, [float(x) for x in today["Close"].values], list(today.index.hour * 60 + today.index.minute),
                            [float(x) for x in today["High"].values], [float(x) for x in today["Low"].values])

        def trade(side, c, run_m):
            t0 = datetime.combine(d, datetime.min.time(), NY).replace(hour=run_m // 60, minute=run_m % 60)
            K = nc.pick_real(nc.real_bars_many(cache, d, side, nc.otm_strikes(c["S"], side)), c["S"], side, t0)
            b = nc.real_bars(cache, d, side, K) if K is not None else None
            r = nc.follow_real(b, t0) if b is not None and len(b) >= 3 else None
            return None if r is None else (r[0] * r[1] * 100, r[2], K)

        out = {"day": d.isoformat()}
        for arm in ARMS:
            last_side, last_t, stops, usd, n, pending = None, None, 0, 0.0, 0, None
            for run_m, s in scans.items():
                if not s:
                    continue
                c, closes, minutes, highs, lows = s
                if pending:
                    p_side, p_zone, p_m, p_why, p_str = pending
                    if run_m - p_m > FVG_WAIT:
                        pending = None
                    elif not bh.fvg_touch(p_zone, p_side, highs[-1], lows[-1], closes[-1]):
                        continue
                    else:
                        pending = None
                        side, why, strength, zone = p_side, p_why + "+fvg", p_str, None
                        r = trade(side, c, run_m)
                        if r is not None:
                            last_side, last_t = side, run_m
                            usd += r[0]
                            n += 1
                            stops += r[1] == "stop"
                            trades.append({"day": d.isoformat(), "arm": arm, "time": f"{run_m // 60:02d}:{run_m % 60:02d}", "side": side,
                                           "S": round(c["S"], 2), "K": r[2], "usd": round(r[0], 2), "exit": r[1], "why": why,
                                           "strength": round(strength, 1)})
                        continue
                side, why, strength, zone = decide(arm, c, closes, minutes, highs, lows, run_m)
                if not side:
                    continue
                if stops >= ob.MAX_DAILY_STOPS or (last_side == side and last_t is not None and run_m - last_t < ob.REPEAT_MINUTES):
                    continue
                if zone is not None:
                    pending = (side, zone, run_m, why, strength)
                    continue
                r = trade(side, c, run_m)
                if r is None:
                    continue
                last_side, last_t = side, run_m
                usd += r[0]
                n += 1
                stops += r[1] == "stop"
                trades.append({"day": d.isoformat(), "arm": arm, "time": f"{run_m // 60:02d}:{run_m % 60:02d}", "side": side,
                               "S": round(c["S"], 2), "K": r[2], "usd": round(r[0], 2), "exit": r[1], "why": why,
                               "strength": round(strength, 1)})
            out[f"{arm}_usd"], out[f"{arm}_n"] = round(usd, 2), n
        rows.append(out)
        print(d, {k: out[f"{k}_usd"] for k in ARMS}, flush=True)
    df = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(ROWS), exist_ok=True)
    df.to_csv(ROWS, index=False)
    pd.DataFrame(trades).to_csv(TRADES, index=False)
    return df


def verdict(df, arm="bh", base="a"):
    x = df.assign(dd=df[f"{arm}_usd"] - df[f"{base}_usd"])
    m, ci = nc.boot_day(x, "dd")
    days = sorted(x["day"].unique())
    mid = days[len(days) // 2]
    h = (x[x["day"] < mid]["dd"].mean(), x[x["day"] >= mid]["dd"].mean())
    ok = m > 0 and ci[0] > 0 and min(h) > 0
    return ("مدعوم" if ok else "غير مدعوم"), m, ci, h, nc.boot_p(x, "dd")


def calibration(t, arm="a"):
    x = t[t["arm"] == arm]
    L = ["", f"## T3 وصفي: الصفقات الرابحة حسب تقييم البوت ({ARMS[arm]})", "", "| التقييم | صفقات | رابحة | متوسط $ |", "|---|---|---|---|"]
    for lo, hi, nm in ((0, 5, "< 5"), (5, 7, "5–7"), (7, 9, "7–9"), (9, 11, "9–10")):
        g = x[(x["strength"] >= lo) & (x["strength"] < hi)]
        if len(g):
            L.append(f"| {nm} | {len(g)} | {(g['usd'] > 0).mean() * 100:.0f}% | {g['usd'].mean():+.1f}$ |")
    return L


def report(df):
    v = verdict(df)
    L = ["# H-BREAKHOLD وH-CONFLUENCE (SPX): نتائج آلية", "",
         f"- {len(df)} يوماً ({df['day'].min()} → {df['day'].max()})، لكل ذراع سياسة يوم كاملة بعقود SPXW حقيقية 100–400$.", "",
         "| الذراع | صفقات | الصافي $ | متوسط اليوم $ | فاصل 95% | أيام رابحة |", "|---|---|---|---|---|---|"]
    for k, nm in ARMS.items():
        if f"{k}_usd" not in df:
            continue
        m, ci = nc.boot_day(df, f"{k}_usd")
        L.append(f"| {nm} | {int(df[f'{k}_n'].sum())} | {df[f'{k}_usd'].sum():+,.0f}$ | {m:+.1f}$ | [{ci[0]:+.1f}، {ci[1]:+.1f}] | "
                 f"{(df[f'{k}_usd'] > 0).mean() * 100:.0f}% |")
    L += ["", "## H-BREAKHOLD: الفرق عن الحالي A (دولار لليوم)", "", "| الذراع | الفرق | فاصل 95% | p | النصفان |", "|---|---|---|---|---|"]
    for k in ("bh", "pl", "ev"):
        _, m, ci, h, p = verdict(df, k)
        L.append(f"| {ARMS[k]} | {m:+.1f}$ | [{ci[0]:+.1f}، {ci[1]:+.1f}] | {p:.3f} | {h[0]:+.1f} / {h[1]:+.1f} |")
    L += ["", f"⚖️ **H-BREAKHOLD (BH − A): {v[0]}**."]
    if "evv_usd" in df:
        L += ["", "## H-CONFLUENCE T1: الفرق عن الإصدار 18 (الأحداث وحدها)", "", "| الذراع | الفرق | فاصل 95% | p | النصفان | الحكم |", "|---|---|---|---|---|---|"]
        res = [verdict(df, k, "ev") for k in ("evv", "evf")]
        rej = nc.bh([r[4] for r in res])
        for k, r, rj in zip(("evv", "evf"), res, rej):
            ok = r[0] == "مدعوم" and rj
            L.append(f"| {ARMS[k]} | {r[1]:+.1f}$ | [{r[2][0]:+.1f}، {r[2][1]:+.1f}] | {r[4]:.3f} | {r[3][0]:+.1f} / {r[3][1]:+.1f} | "
                     f"{'مدعوم ✅' if ok else 'غير مدعوم'} |")
    t = df[df["day"] == "2026-11-10"]
    if len(t):
        r = t.iloc[0]
        L += ["", "## يوم CPI 10/11", "", " | ".join(f"{ARMS[k]}: {r[f'{k}_usd']:+,.0f}$ ({int(r[f'{k}_n'])})" for k in ARMS if f"{k}_usd" in df)]
    if os.path.exists(TRADES):
        tr = pd.read_csv(TRADES)
        if "strength" in tr:
            L += calibration(tr, "a") + calibration(tr, "ev")
    L += ["", "تحفظ: قرار البوت المعاد بلا الجدران والأخبار وحجم الكول/البوت؛ أسعار آخر صفقة في الشمعة؛ عينة أيام قليلة."]
    return "\n".join(L) + "\n"


def main(argv):
    df = pd.read_csv(ROWS) if "--report" in argv else run()
    md = report(df)
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main(sys.argv[1:])
