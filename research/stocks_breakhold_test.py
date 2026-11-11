#!/usr/bin/env python3
"""H-CONFLUENCE T2/T3 (مسجّل مسبقاً في CLAUDE.md 11/11/2026): هل كان «الكسر والثبات» أو VWAP سيحسّن توصيات بوت الشركات الفعلية؟ اختبار فقط.

العينة: كل توصية في stocks_log.csv، ونتيجتها المحققة بقاعدة البوت من آخر حالة في stocks_track.csv
(هدف = +50%، وقف = −40%، مفتوحة = آخر قيمة متتبعة) بالدولار لعقد واحد. شموع 5د وأيام من ياهو.
  BH (الرئيسي): تُمنع التوصية إن كانت كسراً غير ثابت لأعلى/أدنى 20 يوماً في جهتها، أو ملاحقة (حركة اليوم ≥ 1.5% في جهتها بلا كسر ثابت).
  VWAP (ثانوي): تُمنع إن كان السعر في الجهة الخطأ من VWAP اليوم. الممنوعة = 0$.
تشغيل: python research/stocks_breakhold_test.py   المخرج: research/stocks_breakhold_results.md و SPXBOT_DATA/stocks_breakhold_rows.csv
"""
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import nochase_test as nc  # noqa: E402

DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
ROWS = os.path.join(DATA, "stocks_breakhold_rows.csv")
OUT = os.path.join(HERE, "stocks_breakhold_results.md")
NY = "America/New_York"
CHASE, HOLD_BARS, TP, SL = 0.015, 24, 0.50, -0.40


# ====================== دوال صافية ======================
def outcome(status, pnl):
    """النتيجة النسبية المحققة بقاعدة البوت."""
    if status == "tp":
        return TP
    if status == "stopped":
        return SL
    return float(pnl)


def unheld_break(side, S, lvl_hi, lvl_lo, prev_close, today_closes, hold=HOLD_BARS):
    """كسر لأعلى/أدنى 20 يوماً في جهة التوصية لم يثبت: السعر خلف المستوى، وإغلاق أمس لم يكن خلفه، وآخر hold شمعة اليوم ليست كلها خلفه."""
    L = lvl_hi if side == "CALL" else lvl_lo
    beyond = (lambda x: x > L) if side == "CALL" else (lambda x: x < L)
    if not beyond(S):
        return False
    if beyond(prev_close):
        return False
    last = today_closes[-hold:]
    return not (len(last) == hold and all(beyond(x) for x in last))


def held_break(side, S, lvl_hi, lvl_lo, prev_close, today_closes, hold=HOLD_BARS):
    L = lvl_hi if side == "CALL" else lvl_lo
    beyond = (lambda x: x > L) if side == "CALL" else (lambda x: x < L)
    return beyond(S) and not unheld_break(side, S, lvl_hi, lvl_lo, prev_close, today_closes, hold)


def chase(side, day_move, held):
    return (day_move >= CHASE if side == "CALL" else day_move <= -CHASE) and not held


def vwap_wrong(side, S, vwap):
    return vwap is not None and ((side == "CALL" and S < vwap) or (side == "PUT" and S > vwap))


# ====================== البيانات ======================
def load_recs():
    lg = pd.read_csv(os.path.join(ROOT, "stocks_log.csv"), dtype=str, keep_default_na=False)
    tr = pd.read_csv(os.path.join(ROOT, "stocks_track.csv"), dtype=str, keep_default_na=False)
    last = tr.groupby("id").tail(1).set_index("id")
    out = []
    for r in lg.itertuples():
        base = f"{r.ticker}:{r.side}:{r.exp}:{float(r.strike):g}"
        day = r.time[:10]
        k = f"{base}@{day}" if f"{base}@{day}" in last.index else (base if base in last.index else None)
        if k is None:
            continue
        st = last.loc[k]
        out.append({"time": r.time, "day": day, "ticker": r.ticker, "side": r.side, "price": float(r.price),
                    "strength": float(r.strength or 0), "ret": outcome(st["status"], st["pnl"]), "status": st["status"]})
    return pd.DataFrame(out)


def features(df):
    import yfinance as yf
    rows = []
    for tk, g in df.groupby("ticker"):
        try:
            d = yf.Ticker(tk).history(period="1y", auto_adjust=False)
            m = yf.Ticker(tk).history(period="60d", interval="5m", auto_adjust=False)
            d.index = d.index.tz_convert(NY)
            m.index = m.index.tz_convert(NY)
        except Exception as e:  # noqa: BLE001
            print(tk, e)
            continue
        for r in g.itertuples():
            t = pd.Timestamp(r.time).tz_convert(NY)
            day = t.date()
            past = d[d.index.date < day]
            if len(past) < 21:
                continue
            hi20, lo20 = float(past["High"].iloc[-20:].max()), float(past["Low"].iloc[-20:].min())
            prev_close = float(past["Close"].iloc[-1])
            td = m[(m.index.date == day) & (m.index + pd.Timedelta(minutes=5) <= t)]
            if not len(td):
                continue
            S = float(td["Close"].iloc[-1])
            closes = [float(x) for x in td["Close"].values]
            tp_ = (td["High"] + td["Low"] + td["Close"]) / 3
            vwap = float((tp_ * td["Volume"]).sum() / td["Volume"].sum()) if td["Volume"].sum() > 0 else None
            held = held_break(r.side, S, hi20, lo20, prev_close, closes)
            ub = unheld_break(r.side, S, hi20, lo20, prev_close, closes)
            ch = chase(r.side, S / prev_close - 1, held)
            rows.append({**r._asdict(), "S": S, "day_move": S / prev_close - 1, "unheld": ub, "chase": ch,
                         "vwap_wrong": vwap_wrong(r.side, S, vwap), "usd": r.ret * r.price * 100})
    return pd.DataFrame(rows).drop(columns=["Index"], errors="ignore")


def daily(df, block_col):
    x = df.assign(rule=df["usd"].where(~df[block_col], 0.0))
    g = x.groupby("day").agg(cur=("usd", "sum"), rule=("rule", "sum")).reset_index()
    g["dd"] = g["rule"] - g["cur"]
    return g


def verdict(g):
    m, ci = nc.boot_day(g, "dd")
    days = sorted(g["day"].unique())
    mid = days[len(days) // 2]
    h = (g[g["day"] < mid]["dd"].mean(), g[g["day"] >= mid]["dd"].mean())
    ok = m > 0 and ci[0] > 0 and min(h) > 0
    return ("مدعوم" if ok else "غير مدعوم"), m, ci, h, nc.boot_p(g, "dd")


def report(df):
    df = df.assign(bh=df["unheld"] | df["chase"])
    L = ["# H-CONFLUENCE T2: الكسر والثبات وVWAP على توصيات بوت الشركات الفعلية (نتائج آلية)", "",
         f"- {len(df)} توصية في {df['day'].nunique()} يوماً ({df['day'].min()} → {df['day'].max()})، النتيجة المحققة بقاعدة البوت لعقد واحد.",
         f"- الحالي: الصافي {df['usd'].sum():+,.0f}$، رابحة {(df['usd'] > 0).mean() * 100:.0f}%.", "",
         "| القاعدة | تمنع | نتيجة الممنوعة | رابحة الممنوعة | نتيجة الباقية | الفرق لليوم | فاصل 95% | p | النصفان | الحكم |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for col, nm in (("bh", "BH كسر غير ثابت أو ملاحقة (رئيسي)"), ("unheld", "كسر غير ثابت وحده (وصفي)"), ("chase", "ملاحقة وحدها (وصفي)"),
                    ("vwap_wrong", "VWAP (ثانوي)")):
        b = df[df[col]]
        k = df[~df[col]]
        v = verdict(daily(df, col))
        L.append(f"| {nm} | {len(b)} | {b['usd'].sum():+,.0f}$ | {(b['usd'] > 0).mean() * 100 if len(b) else 0:.0f}% | {k['usd'].sum():+,.0f}$ | "
                 f"{v[1]:+.1f}$ | [{v[2][0]:+.1f}، {v[2][1]:+.1f}] | {v[4]:.3f} | {v[3][0]:+.1f} / {v[3][1]:+.1f} | "
                 f"{v[0] if col in ('bh', 'vwap_wrong') else 'وصفي'} |")
    L += ["", "## T3 وصفي: الرابحة حسب تقييم البوت", "", "| التقييم | توصيات | رابحة | متوسط $ |", "|---|---|---|---|"]
    for lo, hi, nm in ((0, 6, "< 6"), (6, 7, "6–7"), (7, 8, "7–8"), (8, 9, "8–9"), (9, 11, "9–10")):
        g = df[(df["strength"] >= lo) & (df["strength"] < hi)]
        if len(g):
            L.append(f"| {nm} | {len(g)} | {(g['usd'] > 0).mean() * 100:.0f}% | {g['usd'].mean():+.1f}$ |")
    t = df[df["day"] == "2026-11-10"]
    if len(t):
        L += ["", "## يوم 10/11", "", "| الوقت | السهم | الجهة | حركة اليوم | كسر غير ثابت | ملاحقة | VWAP خطأ | النتيجة |", "|---|---|---|---|---|---|---|---|"]
        for r in t.itertuples():
            L.append(f"| {r.time[11:16]} | {r.ticker} | {r.side} | {r.day_move * 100:+.1f}% | {'✓' if r.unheld else ''} | {'✓' if r.chase else ''} | "
                     f"{'✓' if r.vwap_wrong else ''} | {r.usd:+.0f}$ |")
    L += ["", "تحفظ كبير: عينة صغيرة (أسابيع قليلة)، ومنع فقط بلا دخول متأخر بديل، والمفتوحة مقيّمة بآخر قيمة. "
          "العينة من stocks_log.csv + stocks_track.csv (stocks_positions.json يحتفظ بآخر 44 توصية فقط)."]
    return "\n".join(L) + "\n"


def main(argv):
    df = pd.read_csv(ROWS) if "--report" in argv else features(load_recs())
    if "--report" not in argv:
        os.makedirs(os.path.dirname(ROWS), exist_ok=True)
        df.to_csv(ROWS, index=False)
    md = report(df)
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main(sys.argv[1:])
