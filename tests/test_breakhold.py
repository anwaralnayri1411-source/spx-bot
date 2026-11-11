"""«الكسر والثبات» (H-BREAKHOLD): دوال صافية."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import breakhold as bh

KEY = [(100.0, "أعلى أمس", True)]
RND = [(100.0, "100", False)]


def test_break_and_hold_up():
    ev = bh.events([98, 99, 101, 102, 103], KEY)
    assert [(e["kind"], e["i"]) for e in ev] == [("hold_up", 4)]


def test_pending_break_is_not_an_event():
    assert bh.events([98, 99, 101, 102], KEY) == []


def test_failed_break_is_reverse_signal_on_key_levels_only():
    ev = bh.events([98, 99, 101, 99.5, 98], KEY)
    assert [(e["kind"], e["i"]) for e in ev] == [("fail_up", 3)] and bh.DIR["fail_up"] == "PUT"
    assert bh.events([98, 99, 101, 99.5, 98], RND) == []


def test_break_down_and_hold():
    ev = bh.events([102, 101, 99, 98, 97], KEY)
    assert ev[0]["kind"] == "hold_down" and bh.DIR["hold_down"] == "PUT"


def test_small_poke_is_not_a_break():
    assert bh.events([99, 100.3, 100.4, 100.2], KEY) == []


def test_orb_levels_need_completed_range():
    lv = [(100.0, "أعلى أول 30 دقيقة", True)]
    assert bh.events([98, 101, 102, 103, 104, 104, 104, 104], lv, start=6) == []
    assert bh.events([98] * 6 + [101, 102, 103], lv, start=6)[0]["kind"] == "hold_up"


def test_latest_is_recent_and_after_ten():
    mins = [9 * 60 + 30 + 5 * i for i in range(20)]
    ev = [{"kind": "hold_up", "i": 3}, {"kind": "hold_down", "i": 15}]
    assert bh.latest(ev, 20, mins)["kind"] == "hold_down"
    assert bh.latest(ev[:1], 20, mins) is None


def test_orb_hold():
    assert bh.orb_hold([99, 101, 102, 103], (100, 90)) == "up"
    assert bh.orb_hold([99, 101, 99, 103], (100, 90)) is None
    assert bh.orb_hold([91, 89, 88, 87], (100, 90)) == "down"


def test_place_blocks_put_near_low_and_call_near_high():
    hl = (7544.0, 7470.0)
    assert bh.place_block("PUT", 7488.0, hl, None, 13 * 60) is not None          # 13:01 يوم CPI: 24% من المدى
    assert bh.place_block("PUT", 7529.7, hl, None, 13 * 60) is None              # من أعلى المدى: مسموح
    assert bh.place_block("CALL", 7540.0, hl, None, 13 * 60) is not None
    assert bh.place_block("PUT", 7488.0, hl, None, 10 * 60) is None              # قبل 10:30 لا شرط مكان
    assert bh.place_block("PUT", 7488.0, hl, {"kind": "hold_down"}, 13 * 60) is None   # كسر ثابت للأسفل يسمح
    assert bh.place_block("PUT", 7488.0, (7500.0, 7490.0), None, 13 * 60) is None      # مدى صغير


def test_levels_include_round_fifties():
    lv = bh.levels((7530, 7490), (7577, 7480), 7470, 7544)
    names = [n for _, n, _ in lv]
    assert "أعلى أول 30 دقيقة" in names and "7,500" in names and "7,550" in names


def test_apply_flip_confirm_and_weak_edge():
    assert bh.apply("PUT", 9.0, {"kind": "hold_up"}) == ("CALL", 6.0, "flip")
    assert bh.apply("CALL", 9.0, {"kind": "hold_up"}) == ("CALL", 9.0, "confirm")
    assert bh.apply(None, 2.0, {"kind": "fail_up"}) == ("PUT", 6.0, "confirm")
    assert bh.apply("PUT", 4.5, None) == ("PUT", 4.5, "")


def test_direction_points_orb_needs_hold():
    import options_bot as ob
    from datetime import datetime
    c = {"n": datetime(2026, 11, 10, 13, 0, tzinfo=ob.NY), "S": 7540.0, "sma20": 7600, "sma50": 7650, "ema9": 1, "ema21": 2, "above_vwap": False, "r30": 0.0, "gap": None,
         "map": None, "vix": 18, "vpct": 50, "max_pain": None, "es": None, "leaders": None, "orb": (7530.0, 7490.0),
         "vix_day": None, "pc": None, "pd_hl": None}
    old = ob.direction_points(dict(c))
    pend = ob.direction_points(dict(c, orb_hold=None))
    held = ob.direction_points(dict(c, orb_hold="up"))
    assert old[0] - pend[0] == 1.5 and held[0] == old[0]
    assert any("دون ثبات" in r[3] for r in pend[2])
