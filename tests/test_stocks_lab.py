"""مختبر تطوّر الشركات (H-STOCKLAB)."""
import os
import random
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stocks_lab as sl

NY = sl.NY


def _bars(day="2026-11-12", n=40, up=True, start=100.0):
    idx = pd.date_range(f"{day} 09:30", periods=n, freq="5min", tz=NY)
    prev = pd.date_range("2026-11-11 09:30", periods=78, freq="5min", tz=NY)
    rows = []
    px = start
    for _ in prev:
        rows.append((px, px + 0.2, px - 0.2, px, 1000))
    for i, _ in enumerate(idx):
        o = px
        px = px * (1.002 if up else 0.998) if i >= 2 else px
        rows.append((o, max(o, px) + 0.05, min(o, px) - 0.05, px, 1000))
    return pd.DataFrame(rows, index=prev.append(idx), columns=["Open", "High", "Low", "Close", "Volume"])


def test_engine_votes_and_orb():
    now = datetime(2026, 11, 12, 12, 55, tzinfo=NY)
    flat = _bars(up=True, start=500.0)
    flat["Close"] = 500.0
    s = sl.engine(_bars(up=True), flat, now)
    assert s["total"] == 4 and s["votes"]["rs"] == 1 and s["adx"] > 20
    assert sl.side_for(dict(sl.CONTROL), s) == "CALL"
    d = sl.engine(_bars(up=False), flat, now)
    assert d["total"] <= -3 and sl.side_for(dict(sl.CONTROL, score=3), d) == "PUT"
    # الشموع غير المكتملة لا تُحسب، وقبل شمعتين اليوم لا قراءة
    assert sl.engine(_bars(), None, datetime(2026, 11, 12, 9, 36, tzinfo=NY)) is None
    # كسر نطاق أول 30 دقيقة في الشمعة الأخيرة
    b = _bars(n=9, up=False)
    b.iloc[-1, b.columns.get_loc("Close")] = 200.0
    o = sl.engine(b, None, datetime(2026, 11, 12, 10, 20, tzinfo=NY))
    assert o["orb"] == "CALL" and o["vwap_x"] == "CALL"


def test_can_enter_cap_and_no_opposite():
    v = {"params": dict(sl.CONTROL, cap=2), "open": [{"tk": "NVDA", "side": "CALL", "tdate": "2026-11-12"}], "closed": []}
    assert not sl.can_enter(v, "NVDA", "PUT", "2026-11-12") and sl.can_enter(v, "TSLA", "PUT", "2026-11-12")
    v["open"] = []
    v["closed"] = [{"tk": "NVDA", "tdate": "2026-11-12"}] * 2
    assert not sl.can_enter(v, "NVDA", "CALL", "2026-11-12") and sl.can_enter(v, "NVDA", "CALL", "2026-11-13")


def test_pick_dte_delta_spread():
    rows = [{"side": "CALL", "dte": 1, "strike": 100, "bid": 2.0, "ask": 2.1, "delta": 0.5, "symbol": "a"},
            {"side": "CALL", "dte": 9, "strike": 100, "bid": 4.0, "ask": 4.2, "delta": 0.52, "symbol": "b"},
            {"side": "CALL", "dte": 9, "strike": 105, "bid": 2.0, "ask": 2.2, "delta": 0.31, "symbol": "c"},
            {"side": "CALL", "dte": 9, "strike": 103, "bid": 1.0, "ask": 2.0, "delta": 0.4, "symbol": "wide"},
            {"side": "CALL", "dte": 14, "strike": 100, "bid": 5.0, "ask": 5.1, "delta": 0.5, "symbol": "later"}]
    assert sl.pick(rows, "CALL", "8-21", 0.5, 100)["symbol"] == "b"
    assert sl.pick(rows, "CALL", "8-21", 0.4, 100)["symbol"] in ("b", "c")      # العريض مرفوض
    assert sl.pick(rows, "CALL", "0-2", 0.5, 100)["symbol"] == "a"
    assert sl.pick(rows, "CALL", "3-7", 0.5, 100) is None
    # الدلتا مفقودة: تُقدَّر من التذبذب
    r2 = [dict(rows[1], delta=0.0, iv=0.4)]
    assert sl.pick(r2, "CALL", "8-21", 0.5, 100)["symbol"] == "b"


def test_manage_exits():
    n = datetime(2026, 11, 12, 11, 0, tzinfo=NY)
    e = {"entry": 2.0, "tdate": "2026-11-12"}
    assert sl.manage(dict(e), dict(sl.CONTROL, exit="tp50"), 3.1, n) == (True, "هدف")
    x = dict(e)
    assert sl.manage(x, dict(sl.CONTROL, exit="tp50", stop=0.25), 1.4, n) == (True, "وقف") and x["pnl"] == -300.0
    late = dict(e)
    p = dict(sl.CONTROL, exit="late")
    assert sl.manage(late, p, 4.5, n) == (False, "") and late["sl"] == 2.0     # +125% ← الوقف على الدخول
    assert sl.manage(late, p, 1.9, n) == (True, "وقف مرحلي")
    eod = dict(e)
    assert sl.manage(eod, dict(sl.CONTROL, exit="tp100"), 2.4, datetime(2026, 11, 12, 15, 50, tzinfo=NY)) == (True, "نهاية اليوم")
    assert eod["pnl"] == 200.0
    h = dict(e)
    ph = dict(sl.CONTROL, exit="half")
    sl.manage(h, ph, 4.0, n)
    assert h["half"] and sl.manage(h, ph, 1.9, n)[0] and h["ret"] == round(0.5 + 0.5 * (1.9 / 2 - 1), 4)


def test_evolve_and_report():
    st = {"gen": 1, "variants": sl.initial_variants("2026-11-12")}
    for i, v in enumerate(st["variants"]):
        v["closed"] = [{"tdate": "2026-11-12", "tk": sl.TICKERS[i % 6], "pnl": (i - 5) * 100.0}] * 3
    txt = sl.evolve(st, "2026-11-12", random.Random(1))
    assert "الجيل 2" in txt and st["variants"][0]["params"] == sl.CONTROL
    assert len(st["last_changes"]) >= 1 and all(c["old"] != "v0" for c in st["last_changes"])
    st2 = {"gen": 1, "variants": sl.initial_variants("2026-11-12")}
    st2["variants"][3]["closed"] = [{"tdate": "2026-11-12", "tk": "NVDA", "pnl": 250.0}]
    board = sl.leaderboard(st2, "2026-11-12")
    assert "مختبر الشركات" in board and "NVDA: v3 +250$" in board and "TSLA: لا صفقات" in board
