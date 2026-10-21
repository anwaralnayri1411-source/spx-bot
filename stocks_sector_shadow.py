"""ظل فلتر القطاع لبوت الشركات (لا يؤثر على التوصيات): عند كل توصية شركات جديدة نسجّل حالة قطاعها كما يراها البوت
لحظتها (شموع ياهو 5د المتأخرة) في stocks_sector_live_log.csv، وهل كان الفلتر سيبقيها.
الفلتر المقترح (من research/stocks_sector_study.py، 21/10): القطاع يتحرك باتجاه العقد منذ إغلاق أمس **و** السهم أقوى من قطاعه
لكول (أضعف لبوت). النتيجة (هدف/وقف) يربطها research/stocks_sector_study.py بعد الإغلاق."""
import csv
import os
from datetime import datetime

LOG = "stocks_sector_live_log.csv"
COLS = ["time", "ticker", "side", "sector", "stock_d", "sector_d", "spy_d", "sector_h", "with_sector", "rs_with",
        "keep", "data_time"]


def _moves(c, t):
    """(منذ إغلاق أمس، آخر 60 دقيقة) من سلسلة إغلاقات 5د (فهرس بتوقيت نيويورك بلا منطقة)."""
    today = c[c.index.date == t.date()]
    prev = c[c.index.date < t.date()]
    if today.empty or prev.empty:
        return None, None, None
    now = float(today.iloc[-1])
    h = today[today.index <= today.index[-1] - __import__("pandas").Timedelta(minutes=60)]
    return now / float(prev.iloc[-1]) - 1, (now / float(h.iloc[-1]) - 1) if not h.empty else None, today.index[-1]


def snapshot(n, ideas):
    import yfinance as yf
    from research.stocks_sector_study import SECTOR
    syms = sorted({i["ticker"] for i in ideas} | {SECTOR[i["ticker"]] for i in ideas if i["ticker"] in SECTOR} | {"SPY"})
    raw = yf.download(syms, period="5d", interval="5m", group_by="ticker", progress=False, threads=True)
    t = n.replace(tzinfo=None)

    def closes(sym):
        c = raw[sym]["Close"].dropna()
        c.index = c.index.tz_convert("America/New_York").tz_localize(None)
        return c

    rows = []
    for i in ideas:
        tk, side = i["ticker"], i["side"]
        sec = SECTOR.get(tk)
        if not sec:
            continue
        try:
            sd, _, dt = _moves(closes(tk), t)
            ed, eh, _ = _moves(closes(sec), t)
            md, _, _ = _moves(closes("SPY"), t)
        except Exception as e:
            print("ظل القطاع: تعذر", tk, sec, e)
            continue
        if sd is None or ed is None:
            continue
        s = 1 if side == "CALL" else -1
        ws, rs = int(s * ed > 0), int(s * (sd - ed) > 0)
        rows.append({"time": n.isoformat(timespec="minutes"), "ticker": tk, "side": side, "sector": sec,
                     "stock_d": round(sd, 5), "sector_d": round(ed, 5), "spy_d": "" if md is None else round(md, 5),
                     "sector_h": "" if eh is None else round(eh, 5), "with_sector": ws, "rs_with": rs,
                     "keep": int(ws and rs), "data_time": dt.strftime("%H:%M") if dt is not None else ""})
    if not rows:
        return
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        w.writerows(rows)
    print("ظل القطاع:", ", ".join(f"{r['ticker']} {r['side']} {'يبقى' if r['keep'] else 'يُستبعد'}" for r in rows))
