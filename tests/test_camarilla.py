"""اختبارات مستويات كاماريلا (الدراسة والظل): المعادلات، الإشارات، المحاكاة، المرجع، وعدم تكرار السجل. بيانات مصطنعة."""
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import camarilla_shadow as sh  # noqa: E402
import camarilla_study as cs  # noqa: E402

NY = cs.NY


def lv105():
    return cs.levels(110.0, 100.0, 105.0)     # R=10: H3=107.75، H4=110.5، H5=115.5، L3=102.25، L4=99.5، L5=94.5


def make_day(d, o=None, h=None, l=None, c=None, n=78, base=105.0):
    t = list(pd.date_range(f"{d} 09:30", periods=n, freq="5min", tz=NY))
    arr = lambda x: np.full(n, base) if x is None else np.asarray(x, float)  # noqa: E731
    return {"t": t, "o": arr(o), "h": arr(h), "l": arr(l), "c": arr(c)}


def test_levels_formula():
    lv = lv105()
    assert lv["PP"] == 105.0
    assert lv["H3"] == pytest.approx(107.75) and lv["H4"] == pytest.approx(110.5)
    assert lv["H5"] == pytest.approx(115.5) and lv["L3"] == pytest.approx(102.25)
    assert lv["L4"] == pytest.approx(99.5) and lv["L5"] == pytest.approx(94.5)


def test_fade_short_signal_at_h3_rejection():
    c = np.full(78, 105.0)
    h = np.full(78, 105.5)
    l = np.full(78, 104.5)
    h[3], c[3] = 108.0, 107.0          # لمس H3 وأغلق تحته وفوق PP
    day = make_day("2026-11-02", h=h, l=l, c=c)
    i, side, entry, stop, target = cs.find_signal(day, lv105(), "fade")
    assert (i, side, entry) == (3, -1, 107.0)
    assert stop == pytest.approx(110.5) and target == 105.0


def test_fade_ignores_close_above_h3_and_below_pp():
    c = np.full(78, 105.0)
    h = np.full(78, 105.5)
    h[3], c[3] = 109.0, 108.5          # أغلق فوق H3: لا انعكاس
    h[5], c[5] = 108.0, 104.0          # أغلق تحت PP: لا إشارة بيع
    assert cs.find_signal(make_day("2026-11-02", h=h, c=c), lv105(), "fade") is None


def test_break_long_above_h4_and_mirror_short():
    c = np.full(78, 105.0)
    c[4] = 111.0
    i, side, entry, stop, target = cs.find_signal(make_day("2026-11-02", c=c), lv105(), "break")
    assert (i, side, entry) == (4, 1, 111.0) and stop == pytest.approx(107.75) and target == pytest.approx(115.5)
    c2 = np.full(78, 105.0)
    c2[2] = 99.0
    i, side, entry, stop, target = cs.find_signal(make_day("2026-11-02", c=c2), lv105(), "break")
    assert (i, side) == (2, -1) and stop == pytest.approx(102.25) and target == pytest.approx(94.5)


def test_no_signal_after_entry_window():
    c = np.full(78, 105.0)
    c[70] = 111.0                      # بعد 15:00 (الفهرس 66)
    assert cs.find_signal(make_day("2026-11-02", c=c), lv105(), "break") is None


def test_simulate_stop_first_target_and_eod():
    h = np.array([100.0, 100.0, 100.0])
    l = np.array([100.0, 100.0, 100.0])
    c = np.array([100.0, 100.0, 100.0])
    # شراء 100، وقف 98، هدف 104 (R=2): الوقف والهدف في الشمعة نفسها → الوقف أولاً
    h[1], l[1] = 105.0, 97.0
    r, kind = cs.simulate(h, l, c, 0, 1, 100.0, 98.0, 104.0)
    assert kind == "sl" and r < -1.0
    h[1], l[1] = 105.0, 99.0
    r, kind = cs.simulate(h, l, c, 0, 1, 100.0, 98.0, 104.0)
    assert kind == "tp" and r == pytest.approx(2.0, abs=0.01)
    h[1], l[1] = 101.0, 99.0
    r, kind = cs.simulate(h, l, c, 0, 1, 100.0, 98.0, 104.0)
    assert kind == "eod" and r == pytest.approx(0.0, abs=0.01)
    assert cs.simulate(h, l, c, 0, 1, 100.0, 100.0, 104.0) is None      # مخاطرة صفر


def test_simulate_short_direction():
    h = np.array([100.0, 101.0, 97.0])
    l = np.array([100.0, 95.0, 96.0])
    c = np.array([100.0, 96.0, 96.0])
    r, kind = cs.simulate(h, l, c, 0, -1, 100.0, 102.0, 96.0)           # بيع، الهدف 96 تحقق
    assert kind == "tp" and r == pytest.approx(2.0, abs=0.01)


def test_baseline_is_mean_of_both_directions():
    c = np.full(78, 105.0)
    h = np.full(78, 105.5)
    l = np.full(78, 104.5)
    h[3], c[3] = 108.0, 107.0
    c[4:] = 105.0                      # يبلغ هدف البيع PP عند الإغلاق، والمعاكس يبلغ وقفه
    h[4:], l[4:] = 105.2, 104.8
    day = make_day("2026-11-02", h=h, l=l, c=c)
    t = cs.trade_for(day, lv105(), "fade")
    assert t["side"] == -1 and t["kind"] in ("tp", "eod")
    assert -1.5 < t["base_r"] < t["r"]                  # الربح ضد المرجع المتوسط


def test_summarize_small_sample_has_no_verdict():
    m = cs.summarize([{"r": 1.0, "base_r": 0.0, "kind": "tp"}] * 5)
    assert m["n"] == 5 and "لا حكم" in m["verdict"]
    assert cs.summarize([])["n"] == 0


def test_summarize_verdict_with_enough_trades():
    good = [{"r": 0.5 + (i % 3) * 0.01, "base_r": -0.05, "kind": "tp"} for i in range(120)]
    assert "تحقق" in cs.summarize(good)["verdict"] and "لم" not in cs.summarize(good)["verdict"]
    bad = [{"r": -0.3 + (i % 3) * 0.01, "base_r": -0.05, "kind": "sl"} for i in range(120)]
    assert "لم يتحقق" in cs.summarize(bad)["verdict"]


def test_shadow_rows_only_after_start_and_complete_days_and_no_duplicates():
    prev = make_day("2026-10-30", h=np.full(78, 110.0), l=np.full(78, 100.0), c=np.full(78, 105.0))
    c = np.full(78, 105.0)
    c[4] = 111.0
    ok = make_day("2026-11-02", c=c)
    short = make_day("2026-11-03", c=c[:40], h=np.full(40, 105.0), l=np.full(40, 105.0), n=40)   # يوم ناقص: يُؤجَّل
    early = make_day("2026-10-29", c=c)                   # قبل START_DATE
    days = {date(2026, 10, 29): early, date(2026, 10, 30): prev, date(2026, 11, 2): ok, date(2026, 11, 3): short}
    rows = sh.new_rows(days, set())
    assert [(r["date"], r["strat"]) for r in rows] == [("2026-11-02", "break")]
    assert rows[0]["side"] == "CALL" and rows[0]["entry"] == 111.0 and rows[0]["PP"] == 105.0
    assert sh.new_rows(days, {("2026-11-02", "break")}) == []


def test_bot_hit_close_uses_bar_at_or_after_rec_time():
    o = np.full(78, 100.0)
    c = np.full(78, 100.0)
    c[-1] = 103.0
    o[3] = 101.0                           # 09:45
    day = make_day("2026-11-02", o=o, c=c)
    assert sh.bot_hit_close({"time": "09:45", "side": "CALL"}, day) is True      # 103 > 101
    assert sh.bot_hit_close({"time": "09:45", "side": "PUT"}, day) is False
    assert sh.bot_hit_close({"time": "17:00", "side": "CALL"}, day) is None      # بعد آخر شمعة


def test_compare_agreement_and_hits(tmp_path):
    c = np.full(78, 100.0)
    c[-1] = 103.0
    day = make_day("2026-11-02", o=np.full(78, 100.0), c=c)
    days = {date(2026, 11, 2): day}
    log = [{"date": "2026-11-02", "strat": "break", "side": "CALL", "entry": "101.0", "r": "0.8"}]
    same = sh.compare(log, [{"date": "2026-11-02", "time": "10:00", "side": "CALL"}], days)
    assert (same["agree"], same["disagree"], same["only_cam"]) == (1, 0, 0)
    assert same["cam_hits"] == [True] and same["bot_hits"] == [True] and same["r_agree"] == [0.8]
    opp = sh.compare(log, [{"date": "2026-11-02", "time": "10:00", "side": "PUT"}], days)
    assert (opp["agree"], opp["disagree"]) == (0, 1) and opp["bot_hits"] == [False] and opp["r_other"] == [0.8]
    none = sh.compare(log, [], days)
    assert none["only_cam"] == 1
    text = "\n".join(sh.compare_lines(same))
    assert "لا حكم" in text and "اتفق" in text


def test_shadow_log_append_and_report(tmp_path):
    p = tmp_path / "log.csv"
    row = {"date": "2026-11-02", "strat": "break", "side": "CALL", "time": "09:50", "entry": 111.0, "stop": 107.75,
           "target": 115.5, "r": 1.2, "kind": "tp", "base_r": 0.1, "PP": 105.0, "H3": 107.75, "H4": 110.5,
           "H5": 115.5, "L3": 102.25, "L4": 99.5, "L5": 94.5, "bars": 78}
    sh.append([row], str(p))
    sh.append([], str(p))
    log = sh.read_log(str(p))
    assert len(log) == 1 and log[0]["strat"] == "break"
    text = "\n".join(sh.report_lines(log))
    assert "اختراق H4/L4" in text and "لا حكم" in text
