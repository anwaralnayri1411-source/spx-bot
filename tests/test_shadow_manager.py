"""اختبارات مدير الظلال: المحوّلات، بوابات المراحل، المجلس، فحوص التناغم، والتقرير. بلا شبكة."""
import os
import sys
from datetime import date, datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import shadow_manager as sm  # noqa: E402


def cand_row(day, slot, mom, f30, cr, pr, status="closed"):
    return {"day": day, "slot": slot, "mom": str(mom), "f30": str(f30), "agree": str(mom if mom == f30 else 0), "call_ret": str(cr), "put_ret": str(pr),
            "call_entry": "2.0", "put_entry": "2.5", "status": status, "spot": "765", "entry_time": f"{day}T{slot}-04:00"}


def test_candidates_adapter_uses_signal_side_and_skill_vs_both_sides():
    ev, tried, valid = sm.from_candidates([cand_row("2026-10-26", "10:30", 1, -1, 0.4, -0.4), cand_row("2026-10-26", "11:30", -1, -1, 0.1, 0.3),
                                           cand_row("2026-10-26", "12:30", 0, 1, 0.1, 0.3), cand_row("2026-10-26", "13:30", 1, 1, 0, 0, status="open")], "mom")
    assert tried == valid == 2
    assert ev[0]["side"] == 1 and ev[0]["ret"] == pytest.approx(0.4) and ev[0]["skill"] == pytest.approx(0.4) and ev[0]["notional"] == pytest.approx(200)
    assert ev[1]["side"] == -1 and ev[1]["skill"] == pytest.approx(0.1)


def test_edge7_prefers_spy_pair_and_counts_one_event_per_entry_time():
    t = "2026-10-26T10:35-04:00"
    rows = [{"entry_time": t, "role": "bot", "side": "CALL", "pnl": "0.37", "entry": "26"}, {"entry_time": t, "role": "opp", "side": "PUT", "pnl": "-0.44", "entry": "24"},
            {"entry_time": t, "role": "spy_bot", "side": "CALL", "pnl": "0.30", "entry": "2.6"}, {"entry_time": t, "role": "spy_opp", "side": "PUT", "pnl": "-0.40", "entry": "2.4"}]
    ev, tried, valid = sm.from_edge7(rows)
    assert len(ev) == 1 and ev[0]["ret"] == pytest.approx(0.30) and ev[0]["skill"] == pytest.approx(0.35) and ev[0]["t"] == "10:35"


def test_momentum_pairs_signal_with_opposite_and_structure_uses_return_on_risk():
    m = [{"date": "2026-10-26", "defn": "live", "role": "signal", "side": "CALL", "sig_time": "2026-10-26T13:19-04:00", "pnl": "-0.448", "entry": "0.96"},
         {"date": "2026-10-26", "defn": "live", "role": "opposite", "side": "PUT", "sig_time": "2026-10-26T13:19-04:00", "pnl": "0.509", "entry": "0.53"}]
    ev, _, _ = sm.from_momentum(m, "live")
    assert ev[0]["skill"] == pytest.approx(-0.4785) and ev[0]["side"] == 1
    s = [{"kind": "vert", "side": "PUT", "capital": "1.5", "t60_pnl": "0.3", "t60_time": "x", "date": "2026-10-26", "entry_time": "2026-10-26T10:39-04:00"},
         {"kind": "vert", "side": "CALL", "capital": "1.5", "t60_pnl": "", "t60_time": "", "date": "2026-10-26"}]
    ev, tried, valid = sm.from_structure(s, "vert")
    assert tried == valid == 1 and ev[0]["ret"] == pytest.approx(0.2) and ev[0]["side"] == -1


def synthetic(days, per_day, value, start=date(2026, 1, 1), noise=0.0):
    out = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        for j in range(per_day):
            v = value + (noise if (i + j) % 2 else -noise)
            out.append(sm.ev(d, f"1{j}:30", 1, v, v, 200))
    return out


def test_stages_follow_the_preregistered_gates():
    shadows = {"cand_mom": (synthetic(10, 2, 0.05), 20, 20),                        # عينة قليلة → مراقَب
               "cand_f30": (synthetic(30, 2, 0.02, noise=0.3), 60, 60),            # عينة كافية ومتوسط موجب، فاصل يشمل الصفر → مرشّح
               "edge7": (synthetic(70, 2, 0.01, noise=0.02), 140, 140),           # كل البوابات → عضو
               "mom_live": (synthetic(70, 2, 0.01, noise=0.02), 140, 100)}        # جودة 71% → مراقَب
    res = sm.evaluate(shadows)
    assert res["cand_mom"]["stage"] == "مراقَب" and "20 حدثاً" in res["cand_mom"]["missing"][0]
    assert res["cand_f30"]["stage"] == "مرشّح" and any("يوماً" in x for x in res["cand_f30"]["missing"])
    assert res["edge7"]["stage"] == "عضو" and res["edge7"]["status"] == "متقدم"
    assert res["mom_live"]["stage"] == "مراقَب" and "جودة البيانات" in res["mom_live"]["missing"]


def test_diversity_gate_blocks_a_second_member_that_copies_the_first():
    a = synthetic(70, 1, 0.01, noise=0.02)
    res = sm.evaluate({"edge7": (a, 70, 70), "mom_live": ([dict(e) for e in a], 70, 70)})
    assert res["edge7"]["stage"] == "عضو" and res["mom_live"]["stage"] == "مرشّح" and "تنوع" in res["mom_live"]["missing"]


def test_bh_controls_false_discoveries():
    assert sm.bh_pass({"a": 0.001, "b": 0.02, "c": 0.5}) == {"a": True, "b": True, "c": False}
    assert sm.bh_pass({"a": 0.2, "b": 0.3}) == {"a": False, "b": False}


def test_council_votes_with_info_known_at_the_slot_only():
    cand = [cand_row("2026-10-26", "10:30", 1, 1, 0.44, -0.42), cand_row("2026-10-26", "12:30", -1, 1, 0.1, 0.1), cand_row("2026-10-26", "13:30", 0, 0, 0.1, 0.1)]
    edge = [sm.ev("2026-10-26", "10:35", -1, 0.0, 0.0, 1)]                          # بعد 10:30 فلا يُحتسب في 10:30
    rows = sm.council(cand, edge, [])
    assert rows[0]["state"] == "إجماع" and rows[0]["decision"] == "CALL" and rows[0]["edge7"] is None and rows[0]["skill"] == pytest.approx(0.43)
    assert rows[1]["state"] == "خلاف" and rows[1]["decision"] == "" and rows[1]["edge7"] == -1
    assert rows[2]["state"] == "رأي واحد"
    assert "لا حكم بعد" in sm.council_verdict(rows)[0]


def test_spot_check_compares_same_asset_different_sources_within_window():
    t = datetime(2026, 10, 26, 10, 30)
    r = [("SPY", "a", t, 765.0), ("SPY", "b", t + timedelta(minutes=2), 765.3), ("SPY", "a", t + timedelta(minutes=1), 900.0),
         ("SPX", "c", t, 7680.0), ("SPX", "d", t + timedelta(minutes=10), 7600.0)]
    n, worst, bad = sm.spot_check(r, "2026-10-26")
    assert n == 2 and len(bad) == 1 and "900.00" in bad[0]
    assert sm.quote_check([{"time": "2026-10-26T10:00", "alpaca_bid": "1.0", "alpaca_ask": "1.1", "tasty_bid": "1.0", "tasty_ask": "1.1", "contract": "X"},
                           {"time": "2026-10-26T10:05", "alpaca_bid": "1.0", "alpaca_ask": "1.1", "tasty_bid": "1.3", "tasty_ask": "1.4", "contract": "Y"},
                           {"time": "2026-10-25T10:05", "alpaca_bid": "1", "alpaca_ask": "1", "tasty_bid": "", "tasty_ask": "", "contract": "Z"}], "2026-10-26")[0] == 2


def test_row_date_ignores_expiry_columns():
    assert sm.row_date({"id": "20261023T1205|x|2026-10-23", "exp": "2026-11-20", "entry_time": "2026-10-26T12:05-04:00"}) == "2026-10-26"
    assert sm.row_date({"exp": "2026-11-20", "foo": "2026-10-20"}) == "2026-10-20"


def test_report_has_all_sections_and_fits_telegram():
    shadows = {"cand_mom": (synthetic(3, 2, 0.05, start=date(2026, 10, 24)), 6, 6)}
    res = sm.evaluate(shadows)
    crows = sm.council([cand_row("2026-10-26", "10:30", 1, 1, 0.44, -0.42)], [], [])
    harmony = {"spot": (3, 0.0004, []), "quotes": (10, 0.01, []), "bad_rows": [], "gaps": 0, "silent": ["ظل التدفق"]}
    md, tg = sm.build(res, crows, "2026-10-26", harmony, [("الأخبار", "2026-10-23")])
    for part in ("مدير الظلال", "تقييم الظلال", "التناغم اليوم", "H-COUNCIL", "لم يسجّل اليوم: ظل التدفق", "الأخبار (23/10)", "10:30"):
        assert part in tg
    assert len(tg) <= 3900 and "⚖️ H-COUNCIL" in md


def test_main_outside_window_does_not_send(monkeypatch):
    monkeypatch.setattr(sm, "ny_now", lambda: datetime(2026, 10, 26, 12, 0))
    monkeypatch.setattr(sm, "send", lambda t: pytest.fail("لا يجب الإرسال"))
    assert sm.main([]) == 0


def minute_series(day, start="09:30", n=390, base=700.0, step=0.01):
    import pandas as pd
    idx = pd.date_range(f"{day} {start}", periods=n, freq="1min") + pd.Timedelta(minutes=1)
    return pd.Series([base + step * i for i in range(n)], index=idx)


def test_move_signs_and_bounds():
    px = {"2026-10-26": minute_series("2026-10-26")}
    m = sm.move(px, "2026-10-26", "10:30", 30)
    assert m == pytest.approx(0.30 / 700.59, rel=1e-6)          # سعر 10:30 = نهاية شمعة 10:29
    assert sm.move(px, "2026-10-26", "15:45", 30) is None          # بعد 16:00
    assert sm.move(px, "2026-10-27", "10:30", 30) is None          # بلا بيانات


def test_direction_scores_and_dissent_minority():
    px = {"2026-10-26": minute_series("2026-10-26", step=-0.01)}   # السوق يهبط
    cand = [cand_row("2026-10-26", "12:30", -1, 1, 0.1, 0.1)]
    ops = sm.opinions(cand, {k: ([], 0, 0) for k in sm.DIR_KEYS})
    rows = sm.direction_rows(ops, px)
    s = sm.direction_summary(rows)
    assert s["cand_mom"]["hit"] == 1.0 and s["cand_f30"]["hit"] == 0.0 and s["cand_mom"]["mean"] > 0
    crow = {"day": "2026-10-26", "slot": "12:30", "mom": -1, "f30": 1, "edge7": 1, "momentum": 1, "state": "خلاف"}
    d = sm.dissent([crow], px)[0]
    assert d["minority"] == ["mom"] and d["side"] == -1 and d["right30"] is True
    assert "زخم آخر ساعة" in sm.dissent_text(d)
    tie = sm.dissent([dict(crow, f30=-1, momentum=None)], px)[0]       # 2 مقابل 1؟ لا: mom وf30 هبوط، edge7 صعود → الأقلية edge7
    assert tie["minority"] == ["edge7"]
    md, tg = sm.build_roles(s, [d], "2026-10-26")
    assert "قراءة الاتجاه" in tg and "الأقلية محقة بعد 30د: 1 من 1" in tg and "اليوم 12:30: المخالف: زخم آخر ساعة" in tg


def test_best_adapter_scores_30_minute_skill():
    rows = [{"variant": "SC", "status": "closed", "entry_time": "2026-10-28T10:30-04:00", "side": "CALL", "side_entry": "2.0", "opp_entry": "2.0", "side_30": "2.6", "opp_30": "1.4"}]
    ev, tried, valid = sm.from_best(rows, "SC")
    assert tried == valid == 1 and ev[0]["skill"] == pytest.approx(0.3) and ev[0]["t"] == "10:30"
