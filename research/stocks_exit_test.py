#!/usr/bin/env python3
"""H-SEXIT: قواعد خروج بوت الشركات على عقوده الحقيقية (مسجّل مسبقاً في CLAUDE.md 12/11/2026). اختبار فقط.

كل توصية في stocks_log.csv بعقد البوت نفسه وشموع Alpaca 5د، والقواعد:
  E0 الحالية: +50%، −40% من العقد بعد 30د، الانتهاء.
  E1 وقف السهم 1.5% + هدف +50% + وقف زمني (≤ 7 أيام للانتهاء).
  E2 مثل E1 بوقف سهم = ATR% اليومي (14 يوماً).
  E3 مثل E1 بهدف +100%.
تشغيل (الخادم، ALPACA): python research/stocks_exit_test.py [--report]
المخرج: research/stocks_exit_results.md و SPXBOT_DATA/stocks_exit_rows.csv
"""
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import nochase_test as nc  # noqa: E402
import stocks_contract_replay as scr  # noqa: E402

NY = ZoneInfo("America/New_York")
DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
ROWS = os.path.join(DATA, "stocks_exit_rows.csv")
OUT = os.path.join(HERE, "stocks_exit_results.md")
RULES = {"E0": "الحالية (+50%، −40% من العقد)", "E1": "وقف السهم 1.5% + هدف 50% + وقف زمني",
         "E2": "وقف السهم بمدى يومي (ATR) + هدف 50% + وقف زمني", "E3": "وقف السهم 1.5% + هدف 100% + وقف زمني"}


def simulate(bars, stock, t0, side, entry_spot, exp, tp, opt_stop=None, stock_stop=None, time_days=None):
    """(النتيجة النسبية، السبب) أو None. نفس تسعير stocks_contract_replay."""
    ts = scr.first_bar_after(bars, t0)
    if ts is None:
        return None
    entry = float(bars.loc[ts, "o"]) * (1 + scr.HS) + scr.COMM
    if entry <= 0:
        return None
    last = None
    for t, r in bars[bars.index >= ts].iterrows():
        end = t + timedelta(minutes=5)
        val = (float(r["c"]) * (1 - scr.HS) - scr.COMM) / entry - 1
        last = (val, "end", entry)
        age = (end - ts).total_seconds() / 60
        if val >= tp:
            return tp, "tp", entry
        if age >= scr.MIN_AGE_MIN:
            if opt_stop is not None and val <= -opt_stop:
                return -opt_stop, "stop", entry
            if stock_stop is not None and stock is not None:
                px = stock.asof(end)
                if px == px and px > 0:
                    adverse = -(px / entry_spot - 1) if side == "CALL" else (px / entry_spot - 1)
                    if adverse >= stock_stop:
                        return val, "stock_stop", entry
        late = end.hour * 60 + end.minute >= 15 * 60 + 55
        if time_days is not None and (exp - end.date()).days <= time_days and late:
            return val, "time", entry
        if end.date() >= exp and late:
            return val, "expiry", entry
    return last


def atr_pct(tk, day):
    import yfinance as yf
    d = yf.Ticker(tk).history(period="1y", auto_adjust=False)
    d = d[d.index.date < day]
    if len(d) < 15:
        return None
    tr = pd.concat([d["High"] - d["Low"], (d["High"] - d["Close"].shift()).abs(), (d["Low"] - d["Close"].shift()).abs()], axis=1).max(axis=1)
    return float(tr.tail(14).mean() / d["Close"].iloc[-1])


def run():
    lg = pd.read_csv(os.path.join(ROOT, "stocks_log.csv"), dtype=str, keep_default_na=False)
    stocks, rows = {}, []
    utc = ZoneInfo("UTC")
    for r in lg.itertuples():
        t0 = datetime.fromisoformat(r.time)
        exp = date.fromisoformat(r.exp)
        sym = scr.occ(r.ticker, exp, r.side, float(r.strike))
        stop_at = scr.cap_end(exp)
        if stop_at <= t0.astimezone(utc) + timedelta(minutes=10):
            continue
        try:
            bars = scr.fetch_bars([sym], t0.astimezone(utc).isoformat(), stop_at.isoformat()).get(sym)
        except Exception as e:  # noqa: BLE001
            print("فشل الجلب:", sym, getattr(e, "code", type(e).__name__))
            continue
        if bars is None or len(bars) < 3:
            continue
        if r.ticker not in stocks:
            stocks[r.ticker] = scr.stock_series(r.ticker)
        st = stocks[r.ticker]
        if st is None:
            continue
        spot = st.asof(pd.Timestamp(t0).tz_convert(NY))
        if not (spot == spot and spot > 0):
            continue
        atr = atr_pct(r.ticker, t0.date())
        out = {"day": t0.date().isoformat(), "time": r.time, "ticker": r.ticker, "side": r.side, "strike": r.strike, "exp": r.exp}
        for k, kw in (("E0", {"tp": 0.5, "opt_stop": 0.4}), ("E1", {"tp": 0.5, "stock_stop": 0.015, "time_days": 7}),
                      ("E2", {"tp": 0.5, "stock_stop": atr, "time_days": 7}), ("E3", {"tp": 1.0, "stock_stop": 0.015, "time_days": 7})):
            if k == "E2" and atr is None:
                continue
            res = simulate(bars, st, t0, r.side, float(spot), exp, **kw)
            if res is not None:
                out[f"{k}_ret"], out[f"{k}_why"], out[f"{k}_usd"] = round(res[0], 4), res[1], round(res[0] * res[2] * 100, 2)
        if "E0_usd" in out:
            rows.append(out)
            print(out["day"], r.ticker, r.side, {k: out.get(f"{k}_usd") for k in RULES}, flush=True)
    df = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(ROWS), exist_ok=True)
    df.to_csv(ROWS, index=False)
    return df


def report(df):
    L = ["# H-SEXIT: خروج بوت الشركات على عقوده الحقيقية (نتائج آلية)", "",
         f"- {len(df)} توصية لها شموع عقد حقيقية ({df['day'].min()} → {df['day'].max()}).", "",
         "| القاعدة | صفقات | الصافي $ | متوسط $ | رابحة | لم تنتهِ (آخر شمعة) |", "|---|---|---|---|---|---|"]
    for k, nm in RULES.items():
        x = df.dropna(subset=[f"{k}_usd"])
        L.append(f"| {k} {nm} | {len(x)} | {x[f'{k}_usd'].sum():+,.0f}$ | {x[f'{k}_usd'].mean():+.1f}$ | {(x[f'{k}_usd'] > 0).mean() * 100:.0f}% | "
                 f"{(x[f'{k}_why'] == 'end').sum()} |")
    L += ["", "## الفرق عن الحالية E0 (دولار لليوم)", "", "| القاعدة | الفرق | فاصل 95% | p | النصفان | الحكم |", "|---|---|---|---|---|---|"]
    res = []
    for k in ("E1", "E2", "E3"):
        x = df.dropna(subset=[f"{k}_usd", "E0_usd"])
        g = x.groupby("day").agg(a=("E0_usd", "sum"), b=(f"{k}_usd", "sum")).reset_index()
        g["dd"] = g["b"] - g["a"]
        m, ci = nc.boot_day(g, "dd")
        days = sorted(g["day"].unique())
        mid = days[len(days) // 2]
        h = (g[g["day"] < mid]["dd"].mean(), g[g["day"] >= mid]["dd"].mean())
        res.append((k, m, ci, h, nc.boot_p(g, "dd")))
    rej = nc.bh([r[4] for r in res])
    for (k, m, ci, h, p), rj in zip(res, rej):
        ok = m > 0 and ci[0] > 0 and min(h) > 0 and rj
        L.append(f"| {k} | {m:+.1f}$ | [{ci[0]:+.1f}، {ci[1]:+.1f}] | {p:.3f} | {h[0]:+.1f} / {h[1]:+.1f} | {'مدعوم ✅' if ok else 'غير مدعوم'} |")
    L += ["", "## أسباب الخروج", ""]
    for k in RULES:
        if f"{k}_why" in df:
            L.append(f"- {k}: " + "، ".join(f"{w} {c}" for w, c in df[f"{k}_why"].value_counts().items()))
    L += ["", "تحفظ: أسعار آخر صفقة في شموع 5د (العقود البعيدة قد تكون شموعها متباعدة)؛ الصفقات غير المنتهية مقيّمة بآخر شمعة؛ عينة أسابيع قليلة."]
    return "\n".join(L) + "\n"


def main(argv):
    df = pd.read_csv(ROWS) if "--report" in argv else run()
    md = report(df)
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main(sys.argv[1:])
