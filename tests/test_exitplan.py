"""خطة الخروج والاحتفاظ بالرابحة (H-EXITPLAN)."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import exitplan as ep


def test_eligible_and_stages():
    p = {}
    ep.track(p, -0.10, 0.001)
    assert ep.eligible(p) and p["trough"] == -0.10
    ep.track(p, -0.40, 0.002)
    assert not ep.eligible(p) and ep.eligible(p, stop_frac=0.005) and not ep.eligible(p, stop_frac=0.003)
    assert ep.lock_for(0.6) == 0.0 and ep.lock_for(1.2) == 0.5 and ep.lock_for(2.5) == 1.0
    assert abs(ep.lock_for(5.0) - 3.0) < 1e-9


def test_runner_path():
    rn = ep.start(0.55, "t0")
    assert ep.step(rn, 0.8, 2.0, "t1") == []
    k = ep.step(rn, 1.1, 2.0, "t2")
    assert k[0][0] == "lock" and rn["lock"] == 0.5 and "3.00" in k[0][1]
    ep.step(rn, 3.5, 2.0, "t3")
    assert abs(rn["lock"] - 2.1) < 1e-9
    out = ep.step(rn, 2.0, 2.0, "t4")
    assert out[0][0] == "runner_exit" and not rn["open"] and rn["why"] == "lock"
    assert ep.total(rn) == round(0.5 * 0.55 + 0.5 * 2.0, 4)
    assert ep.step(rn, 5.0, 2.0, "t5") == []
    r2 = ep.start(0.5, "t0")
    assert ep.step(r2, 0.3, 2.0, "t1", time_exit=True)[0][0] == "runner_exit" and r2["why"] == "time"
    r3 = ep.start(0.5, "t0")
    assert ep.step(r3, -0.01, 2.0, "t1")[0][0] == "runner_exit" and r3["why"] == "lock"


def test_spx_eval_position_runner():
    import options_bot as ob
    n0 = datetime(2026, 11, 12, 10, 0, tzinfo=ob.NY)
    p = {"entry": 2.0, "time": n0.isoformat(), "status": "open", "alerts": {}, "side": "CALL", "sym": "SPX", "strike": 7600,
         "exp": "2026-11-12", "entry_spot": 7590.0}
    ob.eval_position(p, n0.replace(hour=10, minute=30), 1.8, 7588.0)
    pnl, new = ob.eval_position(p, n0.replace(hour=11), 3.1, 7610.0)
    assert p["status"] == "tp" and p["runner"]["open"] and "بِع نصف" in new[0][1]
    pnl, new = ob.eval_position(p, n0.replace(hour=11, minute=30), 4.2, 7620.0)
    assert new[0][0] == "lock" and p["runner"]["lock"] == 0.5
    pnl, new = ob.eval_position(p, n0.replace(hour=15, minute=30), 3.6, 7615.0)
    assert new[0][0] == "runner_exit" and p["runner"]["why"] == "time"
    # عقد اقترب من الوقف قبل الهدف: لا باقي
    q = {"entry": 2.0, "time": n0.isoformat(), "status": "open", "alerts": {}, "side": "CALL", "sym": "SPX", "strike": 7600,
         "exp": "2026-11-12", "entry_spot": 7590.0}
    ob.eval_position(q, n0.replace(hour=10, minute=5), 1.3, 7589.0)
    _, new = ob.eval_position(q, n0.replace(hour=11), 3.1, 7610.0)
    assert q["status"] == "tp" and "runner" not in q and "بِع الكل" in new[0][1]


def test_stocks_evaluate_runner():
    import options_bot as ob
    import stocks_bot as sb
    n = datetime(2026, 11, 12, 13, 0, tzinfo=ob.NY)
    p = {"id": "X", "ticker": "AAPL", "side": "CALL", "exp": "2026-11-27", "strike": 101.0, "entry": 2.0,
         "time": "2026-11-12T10:00-05:00", "entry_spot": 100.0, "status": "open", "peak": 0.0, "alerts": {}, "stop_frac": 0.03}
    _, new = sb.evaluate(p, n, {"val": 4.1, "iv": 0.4, "spr": 0.05}, 104.0, None)
    assert p["status"] == "tp" and p["runner"]["open"] and any("بِع نصف" in t for _, t in new)
    _, new = sb.evaluate(p, n.replace(hour=14), {"val": 6.2, "iv": 0.4, "spr": 0.05}, 106.0, None)
    assert new[0][0] == "lock" and p["runner"]["lock"] == 1.0
    _, new = sb.evaluate(p, n.replace(hour=15), {"val": 3.9, "iv": 0.4, "spr": 0.05}, 103.0, None)
    assert new[0][0] == "runner_exit" and p["closed"]["pnl"] == round(0.5 * 1.05 + 0.5 * 0.95, 4)
    assert "🏁" in sb.alert_block(p, {"val": 3.9, "pnl": 0.95, "spot": 103.0, "mv": 0.03, "need": None, "prob": None,
                                      "spr": 0.05, "dte": 15}, new)
