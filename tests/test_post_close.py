"""اختبارات ما بعد الإقفال: المسار، السطر العربي، والملخص الوصفي."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import post_close as pc  # noqa: E402


def test_from_path_returns_peak_and_last_or_nothing():
    assert pc.from_path([-0.3, 0.2, 0.1]) == (0.2, 0.1)
    assert pc.from_path([-0.5, -0.4]) == (-0.4, -0.4)
    assert pc.from_path([]) == (None, None)
    assert pc.from_path([None, 0.1]) == (0.1, 0.1)


def test_text_after_target_shows_the_higher_price_or_that_it_did_not_exceed():
    t = pc.after_text("tp", 2.0, 1.43, 1.0, 0.5)
    assert "بعد الهدف: بلغ 4.86$ (+143%)" in t and "آخر سعر 4.00$ (+100%)" in t
    assert "لم يتجاوزه" in pc.after_text("tp", 2.0, 0.51, 0.3, 0.5)
    assert pc.after_text("tp", 2.0, None, None, 0.5) == ""


def test_text_after_stop_shows_rebound_above_entry_or_none():
    t = pc.after_text("stop", 1.0, 0.07, -0.2, 0.5)
    assert "بعد الوقف: ارتد إلى 1.07$ (+7%)" in t and "آخر سعر 0.80$ (-20%)" in t
    assert "لم يرتد فوق سعر الدخول" in pc.after_text("stop", 1.0, None, -0.9, 0.5)
    assert "لم يرتد فوق سعر الدخول" in pc.after_text("stop", 1.0, -0.2, -0.3, 0.5)


def test_summary_counts_rebounds_after_stops_and_continuations_after_targets():
    rows = [{"kind": "stop", "peak": 0.6, "last": 0.5}, {"kind": "stop", "peak": 0.05, "last": -0.9}, {"kind": "stop", "peak": None, "last": -0.99},
            {"kind": "tp", "peak": 1.4, "last": 1.0}, {"kind": "tp", "peak": 0.5, "last": 0.2}]
    s = pc.summarize(rows)
    assert s["n_stop"] == 3 and s["n_tp"] == 2
    assert s["stop_back_above_entry"] == pytest.approx(2 / 3) and s["stop_reached_tp"] == pytest.approx(1 / 3) and s["tp_went_higher"] == pytest.approx(0.5)
    assert pc.summarize([])["stop_back_above_entry"] is None
