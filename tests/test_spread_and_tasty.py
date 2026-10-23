"""فرق الشراء/البيع عند الدخول (entry_spr) وتسجيل Tastytrade بجانب Alpaca في متابعة الشركات: تسجيل فقط، لا يغيّر قراراً."""
import json
from datetime import date, datetime, timedelta

import pandas as pd

import os
import sys

import options_bot as ob
import stocks_bot as sb

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import weekly_learning as wl  # noqa: E402

NY = ob.NY


def chains(spr=0.10):
    exp = (ob.now_ny().date() + timedelta(days=9)).isoformat()
    rows = []
    for k in (103.0, 104.0, 105.0, 106.0):
        mid = 1.5
        rows.append({"strike": k, "bid": round(mid * (1 - spr / 2), 3), "ask": round(mid * (1 + spr / 2), 3), "lastPrice": mid,
                     "openInterest": 900, "volume": 300, "impliedVolatility": 0.35})
    df = pd.DataFrame(rows)
    return [(exp, 9, df, df)]


def test_pick_contracts_exposes_the_spread_of_each_contract():
    out = sb.pick_contracts("CALL", chains(0.10), 100.0, 0.3)
    assert out and all(abs(k["spr"] - 0.10) < 0.02 for k in out)
    one_sided = chains()[0][2].assign(bid=0.0)
    got = sb.pick_contracts("CALL", [(chains()[0][0], 9, one_sided, one_sided)], 100.0, 0.3)
    assert all(k["spr"] is None for k in got)                                   # لا عرض/طلب: آخر صفقة، فرق مجهول


def idea(k):
    return {"ticker": "CVX", "side": "CALL", "S": 232.0, "strength": 6.0, "dates": {"earn": None}, "contracts": [k]}


def contract(**kw):
    k = {"strike": 240.0, "exp": "2026-10-30", "price": 1.5, "cost": 150.0, "dte": 7, "iv": 0.3, "be": 241.5, "need": 0.04, "spr": 0.083}
    k.update(kw)
    return k


def test_add_position_stores_entry_spr_without_touching_other_fields():
    P = {"positions": []}
    sb.add_position(P, datetime(2026, 10, 26, 11, 0, tzinfo=NY), idea(contract()), 770.0)
    p = P["positions"][0]
    assert p["entry_spr"] == 0.083 and p["entry"] == 1.5 and p["status"] == "open"
    P2 = {"positions": []}
    sb.add_position(P2, datetime(2026, 10, 26, 11, 0, tzinfo=NY), idea(contract(spr=None)), 770.0)
    assert "entry_spr" not in P2["positions"][0]


def test_live_prices_update_recomputes_the_spread_from_the_live_quote(monkeypatch):
    import live_quotes as lq
    monkeypatch.setattr(lq, "mode", lambda p: "1")
    monkeypatch.setattr(lq, "best_quote", lambda occ: ({"bid": 1.40, "ask": 1.60, "age": 1, "status": "ok"}, "alpaca"))
    i = idea(contract())
    sb._live_prices_stock([i])
    k = i["contracts"][0]
    assert k["price"] == 1.60 and k["bid"] == 1.40 and abs(k["spr"] - 0.2 / 1.5) < 1e-9 and k["price_src"] == "alpaca"


def test_live_shadow_stock_logs_tastytrade_next_to_alpaca(monkeypatch):
    import live_quotes as lq
    rows = []
    monkeypatch.setattr(lq, "mode", lambda p: "1")
    monkeypatch.setattr(lq, "quote", lambda c: {"bid": 1.81, "ask": 1.87, "age": 1.0, "status": "ok"})
    monkeypatch.setattr(lq, "tasty_quote", lambda c: {"bid": 1.79, "ask": 1.85, "age": 2.4, "status": "ok"})
    monkeypatch.setattr(lq, "log", lambda row, path=None: rows.append((row, path)))
    p = {"id": "UBER:PUT:2026-10-30:68", "ticker": "UBER", "exp": "2026-10-30", "side": "PUT", "strike": 68.0, "entry": 1.1}
    sb._live_shadow_stock(p, {"yahoo_val": 1.65, "src": "alpaca"})
    row, path = rows[0]
    assert path == lq.LOG_STOCKS and row["alpaca_bid"] == 1.81 and row["tasty_bid"] == 1.79 and row["tasty_ask"] == 1.85
    assert row["tasty_age_s"] == 2 and row["tasty_status"] == "ok"


def test_live_shadow_stock_without_tasty_keys_logs_empty_columns_and_never_raises(monkeypatch):
    import live_quotes as lq
    rows = []
    monkeypatch.setattr(lq, "mode", lambda p: "1")
    monkeypatch.setattr(lq, "quote", lambda c: {"bid": 1.0, "ask": 1.1, "age": 1.0, "status": "ok"})
    monkeypatch.setattr(lq, "log", lambda row, path=None: rows.append(row))
    monkeypatch.delenv("TASTY_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("TASTY_REFRESH_TOKEN", raising=False)
    p = {"id": "X", "ticker": "X", "exp": "2026-10-30", "side": "CALL", "strike": 10.0, "entry": 1.0}
    sb._live_shadow_stock(p, None)
    assert rows[0]["tasty_bid"] == "" and rows[0]["tasty_status"] == "off"


def test_the_live_log_has_the_tasty_columns_the_shadow_writes():
    import live_quotes as lq
    assert {"tasty_bid", "tasty_ask", "tasty_age_s", "tasty_status"} <= set(lq.LOG_COLS)


def test_weekly_spread_line_groups_closed_positions_by_entry_spread():
    pos = [{"entry_spr": 0.05, "closed": {"pnl": 0.5}}, {"entry_spr": 0.06, "closed": {"pnl": -0.4}},
           {"entry_spr": 0.12, "closed": {"pnl": -0.4}}, {"entry_spr": 0.30, "closed": {"pnl": -0.4}},
           {"entry_spr": 0.20}, {"closed": {"pnl": 0.5}}]
    line = wl.spread_lines(pos)[0]
    assert "≤8%: 2 (+5%)" in line and "8–15%: 1 (-40%)" in line and ">15%: 1 (-40%)" in line and "لا حكم قبل 30 توصية" in line
    assert "لا توصيات مغلقة به بعد" in wl.spread_lines([])[0]
    assert "لا توصيات مغلقة به بعد" in wl.spread_lines([{"closed": {"pnl": 0.5}}])[0]
