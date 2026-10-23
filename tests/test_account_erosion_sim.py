"""اختبارات محاكاة تآكل الحساب: الحالات الحدية والاتجاه الصحيح. بلا شبكة."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import account_erosion_sim as ae  # noqa: E402


def test_breakeven_matches_formula():
    assert ae.breakeven(0.08, -0.21) == pytest.approx(0.21 / 0.29)


def test_zero_size_leaves_account_unchanged_and_certain_win_always_grows():
    r = ae.simulate(0.08, -0.21, 0.5, 0.0, paths=200)
    assert np.allclose(r["final"], 1.0) and ae.summarize(r)["profit"] == 0.0
    r = ae.simulate(0.08, -0.21, 1.0, 0.1, paths=200)
    assert (r["final"] > 1).all() and (r["dd"] == 1.0).all()


def test_below_breakeven_erodes_and_larger_size_erodes_faster():
    be = ae.breakeven(0.08, -0.21)
    assert ae.expected_growth(0.08, -0.21, 0.55, 0.10) < 0 < ae.expected_growth(0.08, -0.21, 0.85, 0.10)
    small = ae.summarize(ae.simulate(0.08, -0.21, 0.55, 0.05, paths=3000))
    big = ae.summarize(ae.simulate(0.08, -0.21, 0.55, 0.25, paths=3000))
    assert small["median"] < 1.0 and big["median"] < small["median"] and big["dd_half"] > small["dd_half"]
    assert 0.55 < be < 0.8


def test_above_breakeven_grows_in_the_median_but_big_size_still_risks_deep_drawdown():
    ok = ae.summarize(ae.simulate(0.08, -0.21, 0.85, 0.10, paths=3000))
    assert ok["median"] > 1.0 and ok["profit"] > 0.8
    risky = ae.summarize(ae.simulate(0.11, -0.37, 0.80, 0.25, paths=3000))
    assert risky["dd_half"] > ok["dd_half"]


def test_report_lists_all_scenarios_and_main_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ae, "PATHS", 500)
    monkeypatch.setattr(ae, "OUT", str(tmp_path / "r.md"))
    assert ae.main([]) == 0
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "ATM 1DTE" in text and "ATM 0DTE" in text and "نسبة التعادل" in text and text.count("| 25% |") >= 10
