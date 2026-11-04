"""اختبارات سياق السوق لتوصيات الشركات (H-CTXSIG). لا شبكة."""
import pandas as pd

import market_context as mc


def _bars(prev, last):
    idx = pd.DatetimeIndex(["2026-11-03 15:55", "2026-11-04 10:30"])
    return pd.DataFrame({"Close": [prev, last]}, index=idx)


def test_moves_from_prev_close():
    m = mc.moves_from({"XLK": _bars(100.0, 101.0), "XLU": _bars(50.0, 49.5), "X": None})
    assert abs(m["XLK"] - 0.01) < 1e-12 and abs(m["XLU"] + 0.01) < 1e-12 and "X" not in m


def test_read_lead_driver_breadth():
    moves = {k: 0.01 for k in mc.SECTORS} | {"XLU": -0.005, "XLP": -0.004, "XLV": -0.002, "IBIT": -0.03}
    c = mc.read(moves, "COIN", "CALL")
    assert c["lead"] == 1 and c["driver"] == "IBIT" and c["driver_al"] == -1 and c["breadth"] == 8 and c["n_sec"] == 11
    p = mc.read(moves, "COIN", "PUT")
    assert p["lead"] == -1 and p["driver_al"] == 1 and p["breadth"] == 3
    assert "driver" not in mc.read(moves, "AAPL", "CALL")
    assert mc.read({}, "COIN", "CALL") == {}
    flat = {k: 0.0005 for k in mc.SECTORS}
    assert mc.read(flat, "AAPL", "CALL")["lead"] == 0


def test_line_text():
    moves = {k: 0.01 for k in mc.SECTORS} | {"XLU": -0.005, "XLP": -0.004, "XLV": -0.002, "IBIT": -0.03}
    t = mc.line(mc.read(moves, "COIN", "CALL"))
    assert "هجومي" in t and "✅ مع العقد" in t and "البتكوين -3.0% ⚠️ ضد العقد" in t and "8/11" in t
    assert mc.line({}) == ""


def test_entry_ctx_keeps_market_context():
    import stocks_bot as sb
    from datetime import datetime
    idea = {"dates": {"earn": None, "exdiv": None}, "bull": 5.0, "bear": 1.0, "mctx": {"lead": 1, "driver_al": -1, "driver": "IBIT"}}
    k = {"exp": "2026-11-20", "dte": 16, "delta": 0.45, "z": 0.6, "score": 7}
    c = sb._entry_ctx(idea, k, datetime(2026, 11, 4, 10, 30))
    assert c["m_lead"] == 1 and c["m_driver_al"] == -1 and c["m_driver"] == "IBIT"
