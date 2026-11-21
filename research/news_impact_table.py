#!/usr/bin/env python3
"""H-NEWSIMPACT: جدول الأثر التاريخي لأيام الأحداث الكبرى (FOMC، تقرير الوظائف) على SPY والأسهم الست. مسجّل في CLAUDE.md 21/11/2026.

معلومة للبطاقة فقط (لا تغيّر التوصية). شموع 5د من Alpaca منذ 2019.
تشغيل: python research/news_impact_table.py <مجلد فيه spy_5m.csv.gz و TICKER.csv.gz>   المخرج: news_impact_table.json (جذر المستودع)
"""
import json
import os
import sys
from datetime import date, timedelta

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import brksigns_study as bk  # noqa: E402  (read_bars)
import fomc_study as fs  # noqa: E402

OUT = os.path.join(ROOT, "news_impact_table.json")
SYMS = ["SPY", "NVDA", "TSLA", "AAPL", "AMZN", "META", "AMD"]


def first_fridays(y0=2019, y1=2026):
    out = set()
    for y in range(y0, y1 + 1):
        for m in range(1, 13):
            d = date(y, m, 1)
            out.add(d + timedelta(days=(4 - d.weekday()) % 7))
    return out


def stats(days, kind):
    """days: {تاريخ: (o, c, c1430_idx...)} → إحصاءات نوع الحدث مقابل الأيام العادية."""
    ev = [v for d, v in days.items() if d in kind]
    nor = [v for d, v in days.items() if d not in kind]
    if not ev or not nor:
        return None
    r = np.array([v["ret"] for v in ev])
    rn = np.array([abs(v["ret"]) for v in nor])
    out = {"n": len(ev), "up": round(float((r > 0).mean()), 3), "mean": round(float(r.mean()), 3),
           "abs": round(float(np.abs(r).mean()), 3), "ratio": round(float(np.abs(r).mean() / rn.mean()), 2)}
    cont = [np.sign(v["a"]) * np.sign(v["b"]) for v in ev if v.get("a") is not None and v["a"] != 0 and v["b"] != 0]
    if cont:
        out["cont"] = round(float((np.array(cont) > 0).mean()), 3)
    return out


def symbol_days(d5):
    days = {}
    for dt, g in d5.groupby(d5.index.date):
        if len(g) < 78:
            continue
        o, c = float(g["Open"].iloc[0]), g["Close"].to_numpy(float)
        rec = {"ret": (c[-1] / o - 1) * 100}
        # FOMC: 14:00→14:30 (الشموع 54..59) ثم 14:30→16:00
        rec["a"] = c[59] / c[53] - 1
        rec["b"] = c[-1] / c[59] - 1
        days[dt] = rec
    return days


def main(argv):
    src = argv[0] if argv else "/opt/spxbot/data"
    fomc = {date.fromisoformat(x) for x in fs.FOMC}
    jobs = first_fridays()
    table = {"built": date.today().isoformat(), "kinds": {"fomc": "قرار الفيدرالي", "jobs": "تقرير الوظائف"}, "syms": {}}
    for s in SYMS:
        p = os.path.join(src, "spy_5m.csv.gz" if s == "SPY" else f"{s}.csv.gz")
        if not os.path.exists(p):
            p = os.path.join(src, "bars_5m", f"{s}.csv.gz")
        days = symbol_days(bk.read_bars(p))
        f, j = stats(days, fomc), stats(days, jobs)
        if j:
            j.pop("cont", None)
        table["syms"][s] = {"fomc": f, "jobs": j}
        print(s, f, j, flush=True)
    json.dump(table, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
