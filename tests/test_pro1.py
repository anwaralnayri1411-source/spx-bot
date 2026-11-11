"""أساسيات المحترف (H-PRO1، الإصدار 23): الوقف بالهيكل، النطاق المتوقع، صحة السوق، GEX مع الحجم."""
import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import livewalls as lw
import options_bot as ob

NY = ob.NY


def test_struct_stop_levels_and_clamp():
    c = {"S": 7627.0, "map": {"put_wall": (7600.0, 1), "call_wall": (7650.0, 1)}, "orb": (7604.0, 7578.0),
         "pd_hl": None, "vwap_spx": 7612.0, "pivots": []}
    assert ob.struct_stop(c, "CALL") == (7607.0, 0.00262)            # VWAP 7612 − 5
    line, frac = ob.struct_stop(c, "PUT")                            # جدار الكول 7650 + 5
    assert line == 7655.0 and abs(frac - 28 / 7627) < 1e-5
    far = {"S": 7627.0, "map": {"put_wall": (7500.0, 1)}, "pivots": []}
    assert ob.struct_stop(far, "CALL")[1] == ob.STRUCT_MAX           # مستوى بعيد: المسافة محصورة 0.6%
    assert ob.struct_stop({"S": 7627.0, "pivots": []}, "CALL") is None


def test_eval_position_struct_stop():
    n = datetime(2026, 11, 11, 10, 20, tzinfo=NY)
    base = {"entry": 4.0, "time": "2026-11-11T10:00-05:00", "side": "CALL", "status": "open", "alerts": {}, "peak": 0.0,
            "entry_spot": 7615.0, "struct_frac": 0.002, "sym": "SPX", "strike": 7620.0, "exp": "2026-11-11"}
    p = dict(base, alerts={})
    _, new = ob.eval_position(p, n, 1.6, 7605.0)          # −60% لكن السعر لم يكسر الخط (−0.13%): يبقى
    assert p["status"] == "open" and not new
    p = dict(base, alerts={})
    _, new = ob.eval_position(p, n, 2.0, 7598.0)          # كسر الخط (−0.22%)
    assert p["status"] == "stopped" and new[0][0] == "stop"
    p = dict(base, alerts={})
    ob.eval_position(p, n, 1.1, 7610.0)                   # −72.5%: الوقف الأقصى
    assert p["status"] == "stopped"
    p = dict(base, alerts={}, struct_frac=None)
    ob.eval_position(p, n, 2.2, 7612.0)                   # بلا هيكل: −45% يضرب الوقف القديم
    assert p["status"] == "stopped"


def test_em_and_block():
    rows = [{"time": "09:37", "side": "C", "strike": 7585.0, "volume": 1, "gamma": 0.01, "delta": 0.52, "bid": 17, "ask": 18, "last": 17},
            {"time": "09:37", "side": "P", "strike": 7585.0, "volume": 1, "gamma": 0.01, "delta": -0.48, "bid": 17, "ask": 18, "last": 17},
            {"time": "09:37", "side": "C", "strike": 7600.0, "volume": 1, "gamma": 0.01, "delta": 0.3, "bid": 9, "ask": 10, "last": 9}]
    df = pd.DataFrame(rows)
    assert lw.expected_move(df) == (7585.0, 35.0, "09:37")
    assert lw.vol_gex(df, 7600.0, "10:00") > 0
    c = {"S": 7630.0, "day_open": 7590.0, "em": (7585.0, 35.0, "09:37")}
    assert "ملاحقة" in ob.em_block(c, "CALL") and ob.em_block(c, "PUT") is None
    assert ob.em_block(c, "CALL", {"kind": "retest_up"}) is None
    assert "النطاق المتوقع" in ob.em_line({"c": c})[0]


def test_internals_votes():
    up = {s: 0.004 for s in ob.SECTORS} | {"RSP": 0.003, "QQQ": 0.005, "IWM": -0.001}
    assert ob.internals_votes(up) == [1, 1, 0]
    assert ob.internals_votes({}) == []


def test_stop_text_and_price():
    k = {"price": 4.0, "struct": (7607.0, 0.00262), "spot": 7627.0}
    assert abs(ob.stop_price(k) - 1.2) < 1e-9 and abs(ob.stop_price({"price": 4.0}) - 2.4) < 1e-9
    assert "7,607" in ob.struct_text(k, "SPX", "CALL")
