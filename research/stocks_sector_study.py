#!/usr/bin/env python3
"""بوت الشركات: هل التوصية ضد قطاعها أو ضد السوق تضرب الوقف أكثر؟ (ظل؛ قراءة فقط، لا يغيّر البوت ولا يرسل شيئاً).

لكل توصية في stocks_log.csv نحسب لحظة التوصية (شموع ياهو 5د، آخر 60 يوماً):
  حركة السهم وصندوق قطاعه وSPY منذ إغلاق أمس، وخلال آخر 60 دقيقة.
  «مع القطاع» = كول والقطاع صاعد منذ إغلاق أمس، أو بوت والقطاع هابط. «قوة نسبية معنا» = السهم أقوى من قطاعه لكول (أضعف لبوت).
النتيجة من stocks_track.csv (البوت يتابع كل عقد): أول وصول +50% (هدف) أو −40% بعد 30 دقيقة (وقف)، وإلا مفتوحة/آخر قيمة.
يُعاد حسابه كاملاً كل تشغيل (التوصيات القديمة تبقى في السجل إن خرجت من نافذة ياهو). ⚠️ عينة صغيرة: لا حكم قبل 40 توصية مغلقة.
تشغيل: python research/stocks_sector_study.py   ← stocks_sector_log.csv و research/stocks_sector_results.md
"""
import csv
import os
from collections import defaultdict
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RECS = os.path.join(ROOT, "stocks_log.csv")
TRACK = os.path.join(ROOT, "stocks_track.csv")
LOG = os.path.join(ROOT, "stocks_sector_log.csv")
REPORT = os.path.join(HERE, "stocks_sector_results.md")
LIVE = os.path.join(ROOT, "stocks_sector_live_log.csv")   # يكتبه stocks_sector_shadow.py لحظة التوصية (كما يراها البوت)
SL, TP, MIN_AGE = 0.40, 0.50, 30
TZ = "America/New_York"
# صندوق القطاع لكل سهم في قائمة بوت الشركات (WATCHLIST في stocks_bot.py)
SECTOR = {
    "NVDA": "SMH", "AVGO": "SMH", "AMD": "SMH", "MU": "SMH", "INTC": "SMH",
    "AAPL": "XLK", "MSFT": "XLK", "PLTR": "XLK",
    "AMZN": "XLY", "TSLA": "XLY", "RIVN": "XLY", "F": "XLY",
    "GOOGL": "XLC", "META": "XLC", "NFLX": "XLC", "DIS": "XLC", "SNAP": "XLC", "T": "XLC",
    "MA": "XLF", "V": "XLF", "JPM": "XLF", "BAC": "XLF", "SOFI": "XLF",
    "COIN": "IBIT", "MSTR": "IBIT", "HOOD": "IBIT",
    "WMT": "XLP", "PFE": "XLV", "XOM": "XLE", "CVX": "XLE",
    "BA": "XLI", "LMT": "XLI", "UBER": "XLI",
}
COLS = ["time", "ticker", "side", "id", "sector", "stock_d", "sector_d", "spy_d", "stock_h", "sector_h", "with_sector",
        "rs_with", "with_market", "outcome", "pnl", "outcome_time"]


def bars(sym, cache={}):
    if sym not in cache:
        import yfinance as yf
        h = yf.Ticker(sym).history(period="60d", interval="5m")
        h.index = h.index.tz_convert(TZ).tz_localize(None)
        cache[sym] = h["Close"].dropna()
    return cache[sym]


def moves(sym, t):
    """(منذ إغلاق أمس، آخر 60 دقيقة) حتى آخر شمعة ≤ t. None إن لم تتوفر."""
    c = bars(sym)
    past = c[c.index <= t]
    if past.empty or past.index[-1].date() != t.date():
        return None
    prev = c[c.index.date < t.date()]
    if prev.empty:
        return None
    now = float(past.iloc[-1])
    h = past[past.index <= past.index[-1] - pd.Timedelta(minutes=60)]
    hour = now / float(h.iloc[-1]) - 1 if not h.empty and h.index[-1].date() == t.date() else None
    return now / float(prev.iloc[-1]) - 1, hour


def outcomes():
    paths = defaultdict(list)
    if os.path.exists(TRACK):
        for r in csv.DictReader(open(TRACK, encoding="utf-8")):
            try:
                paths[r["id"]].append((datetime.fromisoformat(r["time"]).replace(tzinfo=None), float(r["pnl"])))
            except Exception:
                continue
    return paths


def result(path, t0):
    for t, p in sorted(path):
        if (t - t0).total_seconds() / 60 < MIN_AGE:
            continue
        if p >= TP:
            return "tp", TP, t
        if p <= -SL:
            return "stop", -SL, t
    return ("open", sorted(path)[-1][1], None) if path else ("none", None, None)


def study():
    old = {}
    if os.path.exists(LOG):
        old = {(r["time"], r["ticker"], r["side"]): r for r in csv.DictReader(open(LOG, encoding="utf-8"))}
    paths, rows = outcomes(), []
    for r in csv.DictReader(open(RECS, encoding="utf-8")):
        tk, side = r["ticker"], r["side"]
        key = (r["time"], tk, side)
        t0 = datetime.fromisoformat(r["time"]).replace(tzinfo=None)
        pid = f"{tk}:{side}:{r['exp']}:{float(r['strike']):g}"
        oc, pnl, ot = result(paths.get(pid, []), t0)
        row = dict(old.get(key, {}))
        if not row.get("stock_d"):
            sec = SECTOR.get(tk)
            try:
                ms, me, mm = moves(tk, t0), moves(sec, t0) if sec else None, moves("SPY", t0)
            except Exception as e:
                print("تعذر جلب", tk, sec, e)
                ms = me = mm = None
            if not (ms and me and mm):
                if key not in old:
                    print("لا شموع لـ", tk, r["time"], "(خارج نافذة ياهو أو بلا بيانات)")
                continue
            s = 1 if side == "CALL" else -1
            row = {"time": r["time"], "ticker": tk, "side": side, "id": pid, "sector": sec,
                   "stock_d": round(ms[0], 5), "sector_d": round(me[0], 5), "spy_d": round(mm[0], 5),
                   "stock_h": "" if ms[1] is None else round(ms[1], 5), "sector_h": "" if me[1] is None else round(me[1], 5),
                   "with_sector": int(s * me[0] > 0), "rs_with": int(s * (ms[0] - me[0]) > 0), "with_market": int(s * mm[0] > 0)}
        row.update(outcome=oc, pnl="" if pnl is None else round(pnl, 3), outcome_time="" if ot is None else ot.strftime("%m-%d %H:%M"))
        rows.append(row)
    with open(LOG, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows({k: x.get(k, "") for k in COLS} for x in rows)
    return rows


def report(rows):
    done = [r for r in rows if r["outcome"] in ("tp", "stop")]
    L = ["# بوت الشركات: التوصية مقابل قطاعها والسوق — نتائج آلية", "",
         f"آخر تحديث: {datetime.now():%Y-%m-%d %H:%M}. توصيات بشموع متاحة: {len(rows)}، مغلقة (هدف أو وقف): {len(done)}.",
         "«مع القطاع» = صندوق القطاع يتحرك باتجاه العقد منذ إغلاق أمس لحظة التوصية. ⚠️ لا حكم قبل 40 مغلقة.", "",
         "| المجموعة | مغلقة | هدف | وقف | نسبة الهدف |", "|---|---|---|---|---|"]
    for title, key in (("القطاع", "with_sector"), ("القوة النسبية (السهم مقابل قطاعه)", "rs_with"), ("السوق (SPY)", "with_market")):
        for v, lab in (("1", "معنا"), ("0", "ضدنا")):
            x = [r for r in done if str(r[key]) == v]
            if x:
                tp = sum(r["outcome"] == "tp" for r in x)
                L.append(f"| {title}: {lab} | {len(x)} | {tp} | {len(x) - tp} | {tp / len(x) * 100:.0f}% |")
    both = [r for r in done if str(r["with_sector"]) == "1" and str(r["rs_with"]) == "1"]
    if both:
        tp = sum(r["outcome"] == "tp" for r in both)
        L.append(f"| القطاع والقوة النسبية معنا معاً | {len(both)} | {tp} | {len(both) - tp} | {tp / len(both) * 100:.0f}% |")
    if os.path.exists(LIVE):   # الظل الحي: الفلتر بالبيانات المتأخرة كما رآها البوت لحظة التوصية
        res = {(r["time"], r["ticker"], r["side"]): r["outcome"] for r in rows}
        live = [dict(r, outcome=res.get((r["time"], r["ticker"], r["side"]), "")) for r in csv.DictReader(open(LIVE, encoding="utf-8"))]
        lc = [r for r in live if r["outcome"] in ("tp", "stop")]
        L += ["", f"## الظل الحي (كما رآه البوت لحظة التوصية، منذ 22/10): {len(live)} توصية، {len(lc)} مغلقة", "",
              "| الفلتر | مغلقة | هدف | وقف | نسبة الهدف | متوسط (+50/−40) |", "|---|---|---|---|---|---|"]
        for v, lab in (("1", "يبقيها"), ("0", "يستبعدها")):
            x = [r for r in lc if r["keep"] == v]
            if x:
                tp = sum(r["outcome"] == "tp" for r in x)
                L.append(f"| {lab} | {len(x)} | {tp} | {len(x) - tp} | {tp / len(x) * 100:.0f}% | "
                         f"{(tp * TP - (len(x) - tp) * SL) / len(x) * 100:+.0f}% |")
    L += ["", "## التوصيات", "", "| الوقت | السهم | النوع | القطاع | السهم يومياً | القطاع يومياً | مع القطاع | النتيجة |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['time'][:16]} | {r['ticker']} | {r['side']} | {r['sector']} | {float(r['stock_d']) * 100:+.2f}% | "
                 f"{float(r['sector_d']) * 100:+.2f}% | {'✓' if str(r['with_sector']) == '1' else '✗'} | {r['outcome']} |")
    L += ["", "الحكم: إن كانت التوصيات ضد القطاع تضرب الوقف أكثر بوضوح على عينة كافية → نعرض فلتر القطاع على المستخدم (ظل أولاً)."]
    open(REPORT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    report(study())
