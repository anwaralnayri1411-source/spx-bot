#!/usr/bin/env python3
"""بيانات الأوردر فلو لـSPY: كل صفقات Alpaca (SIP) لعدد من أيام التداول الأخيرة → دلتا كل دقيقة (قراءة فقط). بطلب المستخدم 30/10/2026.

جهة كل صفقة بقاعدة التيك: سعر أعلى من آخر سعر مختلف = شراء عدواني (+1)، أدنى = بيع عدواني (−1)، والمساوي يرث جهة سابقه.
لكل دقيقة (ساعات السوق): حجم الشراء، حجم البيع، عدد الصفقات، وإغلاق الدقيقة. قابل للاستئناف: يتخطى الأيام المحفوظة.
الملف خارج شجرة git: SPXBOT_DATA/orderflow_SPY_1m.csv.gz. يحتاج ALPACA_KEY/ALPACA_SECRET (الخادم). ثقيل: ملايين الصفقات يومياً.
تشغيل: python research/orderflow_data.py [--days 40] [--sym SPY]
"""
import gzip
import json
import os
import sys
import time as _time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

NY = ZoneInfo("America/New_York")
DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")


def tick_signs(prices):
    """قاعدة التيك على مصفوفة أسعار: +1/−1، والمساوي يرث (البداية 0)."""
    s = np.zeros(len(prices), dtype=np.int8)
    last_diff, cur = None, 0
    for i, p in enumerate(prices):
        if last_diff is not None and p != last_diff:
            cur = 1 if p > last_diff else -1
        if last_diff is None or p != last_diff:
            last_diff = p
        s[i] = cur
    return s


def aggregate(ts, prices, sizes):
    """صفقات يوم → DataFrame بالدقيقة: buy, sell, n, close (فهرس وقت نيويورك لبداية الدقيقة)."""
    if len(prices) == 0:
        return pd.DataFrame(columns=["buy", "sell", "n", "close"])
    sg = tick_signs(np.asarray(prices, dtype=float))
    df = pd.DataFrame({"t": pd.DatetimeIndex(ts).floor("min"), "p": prices, "v": sizes, "s": sg})
    df["buy"] = np.where(df["s"] > 0, df["v"], 0)
    df["sell"] = np.where(df["s"] < 0, df["v"], 0)
    g = df.groupby("t")
    out = pd.DataFrame({"buy": g["buy"].sum(), "sell": g["sell"].sum(), "n": g.size(), "close": g["p"].last()})
    return out


def fetch_day(sym, day):
    """كل صفقات يوم واحد (9:30–16:00 نيويورك) من Alpaca SIP: (أوقات، أسعار، أحجام)."""
    start = datetime.combine(day, datetime.min.time(), NY).replace(hour=9, minute=30).astimezone(timezone.utc)
    end = start + timedelta(hours=6, minutes=30)
    ts, px, sz, page = [], [], [], None
    while True:
        q = {"start": start.isoformat().replace("+00:00", "Z"), "end": end.isoformat().replace("+00:00", "Z"), "limit": 10000, "feed": "sip"}
        if page:
            q["page_token"] = page
        req = urllib.request.Request(f"https://data.alpaca.markets/v2/stocks/{sym}/trades?" + urllib.parse.urlencode(q),
                                     headers={"APCA-API-KEY-ID": os.getenv("ALPACA_KEY", "").strip(), "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET", "").strip()})
        for attempt in range(4):
            try:
                j = json.loads(urllib.request.urlopen(req, timeout=60).read())
                break
            except Exception:  # noqa: BLE001
                if attempt == 3:
                    raise
                _time.sleep(5 * (attempt + 1))
        for t in j.get("trades") or []:
            ts.append(t["t"])
            px.append(float(t["p"]))
            sz.append(float(t["s"]))
        page = j.get("next_page_token")
        if not page:
            break
    idx = pd.to_datetime(pd.Series(ts), utc=True).dt.tz_convert(NY).dt.tz_localize(None)
    return idx, px, sz


def main(argv):
    days_n = int(argv[argv.index("--days") + 1]) if "--days" in argv else 40
    sym = argv[argv.index("--sym") + 1] if "--sym" in argv else "SPY"
    if not (os.getenv("ALPACA_KEY") and os.getenv("ALPACA_SECRET")):
        print("يحتاج ALPACA_KEY وALPACA_SECRET (الخادم).")
        return 0
    path = os.path.join(DATA, f"orderflow_{sym}_1m.csv.gz")
    old = pd.read_csv(path, parse_dates=["t"]) if os.path.exists(path) else pd.DataFrame(columns=["t", "buy", "sell", "n", "close"])
    have = set(pd.to_datetime(old["t"]).dt.date) if len(old) else set()
    bd = pd.bdate_range(end=date.today() - timedelta(days=1), periods=days_n + 10)
    todo = [d.date() for d in bd if d.date() not in have][-days_n:]
    for k, day in enumerate(todo):
        try:
            ts, px, sz = fetch_day(sym, day)
        except Exception as e:  # noqa: BLE001
            print("فشل", day, getattr(e, "code", type(e).__name__))
            continue
        if len(px) < 1000:
            print("تخطي (عطلة أو لا بيانات):", day)
            continue
        agg = aggregate(ts, px, sz).reset_index()
        new = not os.path.exists(path)
        with gzip.open(path, "at", encoding="utf-8", newline="") as f:
            agg.to_csv(f, index=False, header=new, lineterminator="\n")
        print(f"{k + 1}/{len(todo)} {day}: {len(px):,} صفقة، {len(agg)} دقيقة")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
