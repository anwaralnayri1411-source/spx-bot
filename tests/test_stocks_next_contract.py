"""اختبارات قاعدة عقد الشركات الجديدة (30/10/2026): انتهاء 14–21 يوماً، أقرب دلتا إلى 0.45 (≥ 0.30)، سعر 80–350$. بلا شبكة."""
import os
import sys
from datetime import date, timedelta

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import options_bot as ob  # noqa: E402
import stocks_bot as sb  # noqa: E402


def chain(rows):
    return pd.DataFrame([{"strike": k, "bid": b, "ask": a, "openInterest": 2000, "volume": 300, "impliedVolatility": 0.30} for k, b, a in rows])


def test_pick_next_nearest_delta_045_within_price_and_floor():
    S, exp = 100.0, (date.today() + timedelta(days=17)).isoformat()
    calls = chain([(95, 6.0, 6.2), (100, 2.9, 3.0), (103, 1.75, 1.8), (106, 0.95, 1.0), (115, 0.1, 0.12)])
    out = sb.pick_next("CALL", [(exp, 17, calls, calls)], S, 0.3)
    assert len(out) == 1 and out[0]["exp"] == exp
    T = 17 / 365
    ok = [k for k in (100, 103, 106) if ob.abs_delta(S, k, T, 0.30, "CALL") >= 0.30]
    want = min(ok, key=lambda k: abs(ob.abs_delta(S, k, T, 0.30, "CALL") - 0.45))
    assert out[0]["strike"] == want and 80 <= out[0]["cost"] <= 350          # 95 خارج السعر (620$)، 115 دلتا ضعيفة
    for key in ("score", "be", "z", "em_iv", "em_rv", "hist", "spr"):
        assert key in out[0]


def test_pick_next_rejects_missing_quotes_and_wide_spread():
    exp = (date.today() + timedelta(days=17)).isoformat()
    calls = chain([(100, 0.0, 3.0), (101, 1.0, 2.5)])
    assert sb.pick_next("CALL", [(exp, 17, calls, calls)], 100.0, 0.3) == []


def test_get_chains_next_keeps_only_14_to_21_days():
    today = date(2026, 10, 30)
    exps = [(today + timedelta(days=d)).isoformat() for d in (7, 14, 21, 28)]

    class T:
        options = exps

        def option_chain(self, e):
            return type("C", (), {"calls": pd.DataFrame({"strike": [1.0]}), "puts": pd.DataFrame({"strike": [1.0]})})()
    out = sb.get_chains_next(T(), today)
    assert [e for e, _, _, _ in out] == exps[1:3]


def test_rule_compare_log(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "RULE_COMPARE_LOG", str(tmp_path / "c.csv"))
    sb._rule_compare("AAPL", "CALL", [{"exp": "2026-11-20", "strike": 230.0, "price": 3.1, "delta": 0.46}], [])
    row = pd.read_csv(tmp_path / "c.csv").iloc[0]
    assert row["ticker"] == "AAPL" and row["new_delta"] == pytest.approx(0.46) and pd.isna(row["old_price"])
