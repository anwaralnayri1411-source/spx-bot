"""لوحة تأكيد الكسر وإعادة الاختبار (H-BHVOTE، الإصدار 21)."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import breakhold as bh
import bhvote as bv

ORB = [(7604.0, "أعلى أول 30 دقيقة", True)]


def _hl(cl):
    return [x + 2 for x in cl], [x - 2 for x in cl]


def test_small_counter_close_is_retest_not_fail():
    cl = [7598, 7600, 7606, 7600.3, 7608, 7612]          # 11/11: إغلاق تحت القمة بـ3.7 ثم عودة
    hi, lo = _hl(cl)
    ev = bh.events2(cl, hi, lo, ORB)
    assert ev[0]["kind"] == "retest_up" and ev[0]["i"] == 4 and bh.DIR["retest_up"] == "CALL"
    assert bh.events(cl, ORB)[0]["kind"] == "fail_up"     # التعريف القديم كان يقلب إلى PUT


def test_fail_needs_far_or_two_closes():
    hi, lo = _hl([0] * 6)
    far = [7598, 7600, 7606, 7597, 7590]
    assert bh.events2(far, *_hl(far), ORB)[0]["kind"] == "fail_up"
    two = [7598, 7600, 7606, 7602, 7601, 7600]
    assert [e["kind"] for e in bh.events2(two, *_hl(two), ORB)][0] == "fail_up"
    rnd = [(7600.0, "7,600", False)]
    assert all(e["kind"] != "fail_up" for e in bh.events2(far, *_hl(far), rnd))


def test_retest_after_hold():
    cl = [7598, 7600, 7606, 7610, 7615, 7618, 7612, 7616]
    hi, lo = _hl(cl)
    lo[6] = 7607                                            # شمعة تلمس 7,604+3 وتغلق فوقه
    kinds = [(e["kind"], e["i"]) for e in bh.events2(cl, hi, lo, ORB)]
    assert kinds == [("hold_up", 4), ("retest_up", 6)]


def test_indicators():
    up = [100 + i * 0.5 for i in range(60)]
    assert bv.macd_hist(up[:20]) is None and bv.macd_hist(up) is not None
    zig = [100 + (i % 2) for i in range(40)]
    a_trend = bv.adx([x + 1 for x in up], [x - 1 for x in up], up)
    a_flat = bv.adx([x + 1 for x in zig], [x - 1 for x in zig], zig)
    assert a_trend > 25 > a_flat
    f = bv.fib_levels((7650, 7550))
    assert [round(p, 1) for p, _ in f] == [7588.2, 7600.0, 7611.8]


def test_levels_vote_and_rr():
    cands = [(7604.0, "أعلى أول 30 دقيقة"), (7606.5, "قمة سابقة"), (7604.8, "VWAP")]
    assert bv.stacked(7604.0, cands) == ["أعلى أول 30 دقيقة", "قمة سابقة", "VWAP"]
    assert bv.levels_vote(7604.0, cands, 7610, (7650.0, "جدار الكول")) == 1
    assert bv.levels_vote(7604.0, cands, 7645, (7650.0, "جدار الكول")) == 0        # متراكم لكن المساحة 5 نقاط
    assert bv.rr(7610, 7604, (7650.0, "")) == (40.0, 11.0, True)
    assert bv.rr(7635, 7604, (7650.0, ""))[2] is False                             # 15 نقطة مقابل 36
    assert bv.nearest_opp("PUT", 7610, [(7650.0, "a"), (7590.0, "b"), (7580.0, "c")]) == (7590.0, "b")


def test_vote_threshold_day_type():
    members = {"الاتجاه": [1, 1, -1, 0], "المال": [-1, -1, 1], "السوق الأوسع": [1, 0, 0], "الأخبار والآراء": [0, 0, 0, 0]}
    groups, score = bv.vote("CALL", members, 1)
    assert groups == {"الاتجاه": 1, "المال": -1, "المستويات": 1, "السوق الأوسع": 1, "الأخبار والآراء": 0} and score == 2
    assert bv.vote("PUT", members, -1)[1] == -2
    assert bv.day_type(-1.0, 30.0, (7620, 7600), 7610) == "trend"
    assert bv.day_type(1.0, 10.0, (7605, 7600), 7610) == "range"
    assert bv.threshold("normal", 11 * 60) == 2 and bv.threshold("normal", 12 * 60 + 30) == 3
    assert bv.rel_volume([10] * 6 + [20], 6) == 2.0 and bv.rel_volume([10, 20], 1) is None


def test_vote_board_and_screens(tmp_path, monkeypatch):
    import pandas as pd
    import options_bot as ob
    n = datetime(2026, 11, 11, 12, 10, tzinfo=ob.NY)
    log = tmp_path / "log.csv"
    log.write_text("time,S,screen,dir\n2026-11-11T12:05-05:00,7620,الصقر,1\n2026-11-11T12:05-05:00,7620,محرك MGT,1\n", encoding="utf-8")
    sc = ob.screens_now(n, str(log))
    assert sc == {"الصقر": 1, "محرك MGT": 1}
    assert ob.screens_now(datetime(2026, 11, 11, 13, 0, tzinfo=ob.NY), str(log)) == {}
    monkeypatch.setattr(ob, "flow_30", lambda n, path=None: (-21e6, -9e5))
    idx = pd.date_range("2026-11-11 09:30", periods=40, freq="5min", tz=ob.NY)
    cl = [7580 + i for i in range(40)]
    ix = pd.DataFrame({"High": [x + 1 for x in cl], "Low": [x - 1 for x in cl], "Close": cl}, index=idx)
    c = {"S": 7622.0, "above_vwap": True, "vwap": 7604.8, "vwap_spx": 7604.8, "ema9": 2, "ema21": 1, "macd": 0.5,
         "es": 0.004, "vix_day": -0.03, "leaders": None, "orb": (7604.0, 7580.0), "pd_hl": None, "pivots": [(7607.3, "قمة سابقة")],
         "day_hl": (7623.0, 7579.0), "map": {"call_wall": (7650.0, 1), "put_wall": (7450.0, 1), "gex": -1.0}}
    evt = {"kind": "retest_up", "level": 7604.0, "name": "أعلى أول 30 دقيقة", "key": True, "i": 30, "bi": 20}
    v = ob.vote_board(n, c, evt, {"level": 0}, ix, [9 * 60 + 30 + 5 * i for i in range(40)], {"tilt": 0.001}, screens=sc)
    # الاتجاه +، المال − (العلاوة والدلتا ضد)، المستويات + (متراكم ومساحة 28)، السوق +، الآراء +
    assert v["groups"]["المال"] == -1 and v["groups"]["المستويات"] == 1 and v["score"] == 3
    assert v["dtype"] == "trend" and v["thr"] == 2                                   # يوم اتجاه + وقت الغداء
    a = {"bh_vote": v}
    assert "لوحة التأكيد" in ob.vote_line(a)[0] and "مستوى متراكم" in ob.vote_line(a)[0]
    assert ob.vote_line({}) == []


def test_screens_macd_reading():
    import screens
    rd = screens.readings({"rows": [], "side": "CALL", "c": {"macd": -0.3}})
    assert rd["MACD 5د"] == -1


def test_halt_lifted(tmp_path):
    import options_bot as ob
    f = tmp_path / "o.txt"
    f.write_text("2026-11-11\n", encoding="utf-8")
    assert ob.halt_lifted(datetime(2026, 11, 11, 14, 0, tzinfo=ob.NY), str(f))
    assert not ob.halt_lifted(datetime(2026, 11, 12, 14, 0, tzinfo=ob.NY), str(f))
    assert not ob.halt_lifted(datetime(2026, 11, 11, 14, 0, tzinfo=ob.NY), str(tmp_path / "none"))
