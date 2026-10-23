"""اختبارات تجربتي الظل الجديدتين: exit_shadow (SPX/SPY) ودراسة وقف السهم للشركات. بلا شبكة."""
import os
import sys
from datetime import date, datetime

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import exit_shadow as es  # noqa: E402
import options_bot as ob  # noqa: E402
import stocks_exit_study as ss  # noqa: E402

NY = ob.NY


def test_next_trading_day_skips_weekend_and_holiday():
    assert es.next_trading_day(date(2026, 10, 23)) == date(2026, 10, 26)       # جمعة ← اثنين
    assert es.next_trading_day(date(2026, 11, 25)) == date(2026, 11, 27)       # أربعاء قبل عيد الشكر


def test_candidates_use_bot_strike_today_and_atm_next_day():
    rec = {"sym": "SPX", "side": "CALL", "strike": 7750.0, "exp": "2026-10-22"}
    c = es.candidates(rec, 7703.0)
    assert c["bot"] == ("SPXW261022C07750000", 7750.0, "2026-10-22")
    assert c["atm1"] == ("SPXW261023C07705000", 7705.0, "2026-10-23")
    spy = es.candidates({"sym": "SPY", "side": "PUT", "strike": 770.0, "exp": "2026-10-23"}, 771.4)
    assert spy["atm1"] == ("SPY261026P00771000", 771.0, "2026-10-26")


def fresh(entry=1.0):
    return {"entry": entry, "quotes": 0, "peak_pnl": 0.0, "close_pnl": "", "r5040_out": "", "r5040_time": "",
            "r5040_pnl": "", "t60_time": "", "t60_pnl": ""}


def test_target_and_stop_follow_bot_rules_including_minimum_age():
    it = fresh()
    es.step_item(it, 0.5, 11 * 60, age_rec=10, age_entry=10, final_min=930)         # −50% لكن العمر 10 د: لا وقف
    assert it["r5040_out"] == ""
    es.step_item(it, 0.5, 11 * 60 + 10, age_rec=20, age_entry=20, final_min=930)
    assert it["r5040_out"] == "stop" and it["r5040_pnl"] == -0.5
    tp = fresh()
    es.step_item(tp, 1.6, 11 * 60, age_rec=5, age_entry=5, final_min=930)
    assert tp["r5040_out"] == "tp"


def test_t60_exit_is_frozen_at_first_evaluation_after_an_hour():
    it = fresh()
    es.step_item(it, 1.1, 11 * 60, 30, 30, 930)
    assert it["t60_pnl"] == ""
    es.step_item(it, 1.2, 12 * 60, 60, 60, 930)
    es.step_item(it, 0.7, 12 * 60 + 5, 65, 65, 930)
    assert it["t60_pnl"] == pytest.approx(0.2, abs=1e-3) and it["close_pnl"] == pytest.approx(-0.3, abs=1e-3)


def test_final_minute_exits_both_rules_and_early_close_moves_it():
    it = fresh()
    es.step_item(it, 1.05, 15 * 60 + 30, 200, 100, es.final_minute(datetime(2026, 10, 22, 15, 30, tzinfo=NY)))
    assert it["r5040_out"] == "final" and it["t60_pnl"] != ""
    assert es.final_minute(datetime(2026, 11, 27, 10, 0, tzinfo=NY)) == 12 * 60 + 30     # إغلاق مبكر 13:00


def test_report_pairs_both_contracts_per_recommendation(tmp_path, monkeypatch):
    log = tmp_path / "log.csv"
    cols = es.LOG_COLS
    rows = []
    for pid, bot, atm in (("a", -0.4, 0.1), ("b", 0.5, 0.3)):
        for v, x in (("bot", bot), ("atm1", atm)):
            rows.append(",".join([ "2026-10-22", pid, v, "CALL", "1", "2026-10-22", "t", "1", "1", "x", "t", str(x), "t", str(x), "0", "0", "3", "SPX"]))
    log.write_text(",".join(cols) + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(es, "LOG", str(log))
    monkeypatch.setattr(es, "REPORT", str(tmp_path / "r.md"))
    monkeypatch.setattr(es, "load_state", lambda: {"items": []})
    es.report()
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "لا يغيّر هذا التقرير أي توصية" in text and "أفضل في 1 من 2" in text


def test_run_records_both_contracts_only_when_both_quotes_exist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(es.data_gaps, "path_for", lambda project: str(tmp_path / f"gaps_{project}.csv"))
    n = datetime(2026, 10, 22, 11, 5, tzinfo=NY)
    rec = {"id": "SPX:CALL:7750:2026-10-22", "sym": "SPX", "side": "CALL", "strike": 7750.0, "exp": "2026-10-22",
           "time": "2026-10-22T11:00-04:00"}
    monkeypatch.setattr(ob, "load_positions", lambda: {"positions": [rec]})
    monkeypatch.setattr(es.lq, "spot", lambda s: (7703.0, "tasty"))
    book = {"SPXW261022C07750000": ({"bid": 1.0, "ask": 1.2}, "alpaca")}
    monkeypatch.setattr(es.lq, "chain_quotes", lambda cs: book)
    es.run(n)
    assert es.load_state()["items"] == []                                  # ناقص عقد atm1: لا تسجيل
    book["SPXW261023C07705000"] = ({"bid": 20.0, "ask": 21.0}, "alpaca")
    es.run(n)
    st = es.load_state()
    assert {i["variant"] for i in st["items"]} == {"bot", "atm1"} and {i["entry"] for i in st["items"]} == {1.2, 21.0}


def test_run_ignores_recommendation_seen_too_late(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rec = {"id": "x", "sym": "SPX", "side": "PUT", "strike": 7700.0, "exp": "2026-10-22", "time": "2026-10-22T10:00-04:00"}
    monkeypatch.setattr(ob, "load_positions", lambda: {"positions": [rec]})
    monkeypatch.setattr(es.lq, "spot", lambda s: (7703.0, "tasty"))
    monkeypatch.setattr(es.lq, "chain_quotes", lambda cs: {})
    es.run(datetime(2026, 10, 22, 11, 5, tzinfo=NY))
    st = es.load_state()
    assert st["items"] == [] and st["skipped"] == ["x"]


# ---------------- دراسة وقف السهم ----------------
def rows(*specs):
    return pd.DataFrame([{"time": t, "stock_move": m, "pnl": p} for t, m, p in specs])


def test_stock_stop_exits_on_adverse_move_but_not_in_open_grace_window():
    r = rows(("2026-10-20T11:00-04:00", -0.005, -0.1), ("2026-10-21T09:40-04:00", -0.02, -0.3), ("2026-10-21T10:30-04:00", -0.02, -0.35))
    assert ss.replay(r, "CALL", "2026-10-20", "s15", "2026-10-20T10:30-04:00") == -0.35      # 09:40 داخل أول 30د
    assert ss.replay(r, "CALL", "2026-10-20", "s15n", "2026-10-20T10:30-04:00") == -0.3


def test_put_adverse_is_stock_up_and_bot_rule_caps_at_thresholds():
    up = rows(("2026-10-20T11:30-04:00", 0.02, -0.2))
    assert ss.replay(up, "PUT", "2026-10-20", "s15n", "2026-10-20T10:30-04:00") == -0.2
    jump = rows(("2026-10-20T11:30-04:00", 0.0, 2.7))
    assert ss.replay(jump, "CALL", "2026-10-20", "base", "2026-10-20T10:30-04:00") == 0.5
    crash = rows(("2026-10-20T11:30-04:00", 0.0, -0.9))
    assert ss.replay(crash, "CALL", "2026-10-20", "base", "2026-10-20T10:30-04:00") == -0.4


def test_no_stop_in_first_30_minutes_of_the_position():
    r = rows(("2026-10-20T10:40-04:00", -0.03, -0.6), ("2026-10-20T11:30-04:00", -0.03, -0.6))
    assert ss.replay(r, "CALL", "2026-10-20", "base", "2026-10-20T10:30-04:00") == -0.4
    assert ss.replay(r.iloc[:1], "CALL", "2026-10-20", "base", "2026-10-20T10:30-04:00") == -0.6   # لا خروج: آخر قيمة
