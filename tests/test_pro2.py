"""بوت الشركات: أساسيات المحترف (H-PRO2)."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stocks_bot as sb

INFO = {"S": 100.0, "prev_close": 98.0, "pdl": 97.0, "pdh": 99.5, "tlo": 98.5, "thi": 100.4, "lo20": 92.0, "hi20": 104.0,
        "sma20": 96.0, "sma50": 94.0}


def test_struct_stop():
    line, frac = sb.struct_stop("CALL", 100.0, INFO)             # أقرب دعم: قمة أمس 99.5 (مقاومة تحولت دعماً) − 0.3%
    assert abs(line - 99.5 * 0.997) < 0.01
    line, frac = sb.struct_stop("PUT", 100.0, INFO)              # قمة اليوم 100.4 + 0.3% = 0.7% → محصورة عند 0.8%
    assert frac == sb.STRUCT_MIN and line == 100.8
    far = dict(INFO, pdh=None, thi=None, hi20=110.0)
    assert sb.struct_stop("PUT", 100.0, far)[1] == sb.STRUCT_MAX     # 10.3% → محصورة عند 4%
    tight = dict(INFO, tlo=99.6, pdl=99.5)
    assert sb.struct_stop("CALL", 100.0, tight)[1] == sb.STRUCT_MIN or sb.struct_stop("CALL", 100.0, tight)[1] >= sb.STRUCT_MIN
    assert sb.struct_stop("CALL", 100.0, {"S": 100.0}) is None


def test_em_and_pro_block():
    k = {"em_iv": 0.4 * math.sqrt(14 / 365), "dte": 14}
    em = sb.em_day(100.0, k)
    assert abs(em - 100 * 0.4 / math.sqrt(252)) < 1e-6           # ≈ 2.52$
    hot = dict(INFO, prev_close=97.5)                              # +2.5$ ≥ 80% من 2.52
    assert sb.pro_block("AAPL", "CALL", 100.0, hot, k, {})[0] == "chase_em"
    assert sb.pro_block("AAPL", "PUT", 100.0, hot, k, {}) is None
    secs = {s: -0.01 for s in ("XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE", "XLC")}
    secs.update(XLK=-0.02, XLY=-0.02, XLC=-0.02, XLU=0.0, XLP=0.0, XLV=0.0)
    r = sb.pro_block("AAPL", "CALL", 100.0, INFO, k, secs)
    assert r and r[0] == "market_against"


def test_evaluate_uses_struct_frac():
    from datetime import datetime
    import options_bot as ob
    n = datetime(2026, 11, 12, 13, 0, tzinfo=ob.NY)
    base = {"id": "X", "ticker": "AAPL", "side": "CALL", "exp": "2026-11-27", "strike": 101.0, "entry": 2.0,
            "time": "2026-11-12T10:00-05:00", "entry_spot": 100.0, "status": "open", "peak": 0.0, "alerts": {}}
    q = {"val": 1.5, "iv": 0.4, "spr": 0.05}
    p = dict(base, alerts={}, stop_frac=0.03)
    sb.evaluate(p, n, q, 98.0, None)                               # −2%: داخل الوقف الهيكلي 3%
    assert p["status"] == "open"
    p = dict(base, alerts={})
    sb.evaluate(p, n, q, 98.0, None)                               # بلا هيكل: 1.5% يضرب
    assert p["status"] == "stopped"
    assert "خلف المستوى" in sb.stock_stop_text("CALL", 100.0, 0.03)
