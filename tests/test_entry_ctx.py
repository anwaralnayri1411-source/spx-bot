"""اختبارات سياق لحظة التوصية في سجل صفقات الشركات: يُسجَّل للتفسير فقط ولا يكسر شيئاً."""
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import options_bot as ob  # noqa: E402
import stocks_bot as sb  # noqa: E402

NY = ob.NY
N = datetime(2026, 10, 26, 11, 0, tzinfo=NY)


def idea(**kw):
    i = {"ticker": "CVX", "side": "CALL", "S": 232.0, "strength": 6.0, "bull": 7.5, "bear": 2.0, "news_b": 1.0, "news_e": 0.0, "flow_b": 0.0, "flow_e": 1.0,
         "dates": {"earn": date(2026, 10, 30), "exdiv": date(2026, 11, 14)},
         "contracts": [{"strike": 240.0, "exp": "2026-11-06", "price": 1.5, "cost": 150.0, "dte": 11, "delta": 0.314, "z": 0.7777, "score": 7.4}]}
    i.update(kw)
    return i


def test_ctx_records_news_flow_dates_and_contract_profile():
    c = sb._entry_ctx(idea(), idea()["contracts"][0], N)
    assert c["news_b"] == 1.0 and c["flow_e"] == 1.0 and c["bull"] == 7.5 and c["dte"] == 11 and c["delta"] == 0.31 and c["z"] == 0.78
    assert c["earn_in_life"] is True and c["exdiv_in_life"] is False and c["exdiv"] == "2026-11-14"


def test_exdiv_and_earnings_flags_depend_on_the_contract_life():
    short = dict(idea()["contracts"][0], exp="2026-10-28")
    c = sb._entry_ctx(idea(), short, N)
    assert c["earn_in_life"] is False
    c2 = sb._entry_ctx(idea(dates={"earn": None, "exdiv": date(2026, 10, 27)}), short, N)
    assert c2["exdiv_in_life"] is True and c2["earn_in_life"] is False


def test_ctx_never_raises_and_add_position_stores_it_without_changing_other_fields():
    assert sb._entry_ctx({}, {}, N) == {}
    assert sb._entry_ctx(None, None, N) == {}
    P = {"positions": []}
    sb.add_position(P, N, idea(), 770.0)
    p = P["positions"][0]
    assert p["ctx"]["dte"] == 11 and p["entry"] == 1.5 and p["status"] == "open" and p["earn"] == "2026-10-30"
    P2 = {"positions": []}
    bad = idea()
    bad["contracts"] = [{"strike": 240.0, "exp": "bad", "price": 1.5}]
    sb.add_position(P2, N, bad, 770.0)
    assert "ctx" not in P2["positions"][0] and P2["positions"][0]["entry"] == 1.5
