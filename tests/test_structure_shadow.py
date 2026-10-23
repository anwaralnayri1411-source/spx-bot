"""اختبارات ظل هيكل الصفقة (سبريد شراء، عقد مجرد بالحركة المتوقعة، كوندور حديدي). بلا شبكة."""
import csv
import json
import os
import sys
from datetime import date, datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import options_bot as ob  # noqa: E402
import structure_shadow as ss  # noqa: E402

NY = ob.NY


@pytest.fixture(autouse=True)
def _no_gap_files(monkeypatch):
    monkeypatch.setattr(ss.data_gaps, "note", lambda *a, **k: None)


def t(h, m, d=(2026, 10, 26)):
    return datetime(*d, h, m, tzinfo=NY)


def test_sigma_and_leg_construction():
    assert abs(ss.sigma_day(16.0) - 0.16 / 252 ** 0.5) < 1e-12
    v = ss.vert_legs("CALL", 700.4, "2026-10-27")
    assert [lg["strike"] for lg in v] == [700.0, 703.0] and [lg["qty"] for lg in v] == [1, -1]
    p = ss.vert_legs("PUT", 700.4, "2026-10-27")
    assert [lg["strike"] for lg in p] == [700.0, 697.0]
    e = ss.em_legs("CALL", 700.0, 16.0, "2026-10-27")             # 0.5 × 700 × 0.01008 = 3.53 → سقف 704
    assert e[0]["strike"] == 704.0 and e[0]["occ"] == "SPY261027C00704000"
    assert ss.em_legs("PUT", 700.0, 16.0, "2026-10-27")[0]["strike"] == 696.0
    c = ss.condor_legs(700.0, 16.0, "2026-10-27")                  # 0.8 × 700 × 0.01008 = 5.64
    strikes = sorted((lg["side"], lg["strike"], lg["qty"]) for lg in c)
    assert strikes == [("CALL", 706.0, -1), ("CALL", 709.0, 1), ("PUT", 691.0, 1), ("PUT", 694.0, -1)]


def test_cash_uses_ask_for_buys_and_bid_for_sells():
    legs = ss.vert_legs("CALL", 700.0, "2026-10-27")
    q = {legs[0]["occ"]: {"bid": 2.0, "ask": 2.1}, legs[1]["occ"]: {"bid": 0.9, "ask": 1.0}}
    assert abs(ss.entry_cash(legs, q) - (0.9 - 2.1)) < 1e-12             # دفع 1.20
    assert abs(ss.close_cash(legs, q) - (2.0 - 1.0)) < 1e-12
    assert ss.entry_cash(legs, {legs[0]["occ"]: q[legs[0]["occ"]]}) is None
    assert ss.capital("vert", legs, -1.2) == 1.2
    assert abs(ss.capital("ic", legs, 0.8) - 2.2) < 1e-12


def test_intrinsic_value_at_expiry_for_condor():
    legs = ss.condor_legs(700.0, 16.0, "2026-10-27")
    assert ss.intrinsic_cash(legs, 700.0) == 0.0                             # داخل الجناحين: كله بلا قيمة
    assert ss.intrinsic_cash(legs, 700.0 + 10) == -3.0                       # فوق الجناح العلوي: خسارة العرض
    assert abs(ss.intrinsic_cash(legs, 707.0) - (-1.0)) < 1e-12


def vert_item(entry_cash=-1.2):
    legs = ss.vert_legs("CALL", 700.0, "2026-10-27")
    return ss.new_item("vert", "p1", t(10, 0), "CALL", "2026-10-27", 700.0, legs, entry_cash), legs


def quote(legs, long_bid, short_ask):
    return {legs[0]["occ"]: {"bid": long_bid, "ask": long_bid + 0.1}, legs[1]["occ"]: {"bid": short_ask - 0.1, "ask": short_ask}}


def test_vert_t60_and_eod_are_recorded_at_the_right_times():
    it, legs = vert_item()
    assert not ss.step(it, quote(legs, 2.4, 1.0), 701.0, t(10, 30))
    assert it["t60_pnl"] == "" and it["last_pnl"] == pytest.approx(-1.2 + 1.4)
    assert not ss.step(it, quote(legs, 2.6, 1.0), 701.0, t(11, 1))
    assert it["t60_pnl"] == pytest.approx(0.4) and it["eod_pnl"] == ""
    assert ss.step(it, quote(legs, 2.0, 1.0), 701.0, t(15, 56))
    assert it["eod_pnl"] == pytest.approx(-0.2) and it["peak"] == pytest.approx(0.4)


def test_missing_quotes_do_not_advance_but_next_day_expiry_closes_with_last_value():
    it, legs = vert_item()
    assert not ss.step(it, {}, 701.0, t(10, 30))
    assert it["quotes"] == 0
    ss.step(it, quote(legs, 2.0, 1.0), 701.0, t(10, 40))
    assert not ss.expire_if_past(it, t(15, 0))                                # نفس اليوم: لم يمضِ
    assert ss.expire_if_past(it, t(9, 40, (2026, 10, 27)))
    assert it["t60_pnl"] == it["eod_pnl"] == pytest.approx(-0.2)
    nodata, _ = vert_item()
    assert ss.expire_if_past(nodata, t(9, 40, (2026, 10, 27))) and nodata["eod_time"] == "no_data"


def condor_item(credit=0.8):
    legs = ss.condor_legs(700.0, 16.0, "2026-10-27")
    return ss.new_item("ic", "day-2026-10-26", t(10, 30), "BOTH", "2026-10-27", 700.0, legs, credit), legs


def condor_quote(legs, cost):
    """سعر إغلاق إجمالي cost: نضعه على الرجل المباعة الأولى ونبقي الباقي صفراً تقريباً."""
    q = {}
    for lg in legs:
        q[lg["occ"]] = {"bid": 0.0, "ask": 0.01}
    sold = next(lg for lg in legs if lg["qty"] < 0)
    q[sold["occ"]] = {"bid": cost - 0.01, "ask": cost}
    return q


def test_condor_take_profit_stop_and_hold_to_expiry():
    it, legs = condor_item()
    assert ss.capital("ic", legs, 0.8) == pytest.approx(2.2) and it["capital"] == pytest.approx(2.2)
    ss.step(it, condor_quote(legs, 0.40), 700.0, t(12, 0, (2026, 10, 26)))          # ربح 0.80−0.41=0.39 < 0.40
    assert it["managed_out"] == ""
    ss.step(it, condor_quote(legs, 0.30), 700.0, t(12, 30, (2026, 10, 26)))         # ربح 0.49 ≥ 0.40 → tp
    assert it["managed_out"] == "tp" and it["managed_pnl"] == pytest.approx(0.49)
    done = ss.step(it, condor_quote(legs, 0.50), 700.0, t(15, 50, (2026, 10, 27)))
    assert done and it["hold_pnl"] == pytest.approx(0.29) and it["managed_out"] == "tp"
    s, _ = condor_item()
    ss.step(s, condor_quote(s["legs"], 2.5), 700.0, t(13, 0, (2026, 10, 26)))      # خسارة 1.71 ≤ −1.6 → stop
    assert s["managed_out"] == "stop"


def test_condor_at_expiry_uses_intrinsic_when_no_quotes_for_dead_options():
    it, legs = condor_item()
    assert not ss.step(it, {}, 700.0, t(14, 0, (2026, 10, 27)))                    # قبل 15:50 بلا تسعيرة: لا شيء
    assert ss.step(it, {}, 700.0, t(15, 50, (2026, 10, 27)))
    assert it["hold_pnl"] == pytest.approx(0.8) and it["managed_out"] == "tp"
    w, _ = condor_item()
    assert ss.step(w, {}, 712.0, t(15, 55, (2026, 10, 27)))
    assert w["hold_pnl"] == pytest.approx(0.8 - 3.0)


def test_summarize_and_verdicts_wait_for_enough_cases():
    rows = [{"kind": "vert", "date": f"2026-11-{1 + i % 20:02d}", "capital": "1.0", "t60_pnl": "0.1", "pos_id": f"p{i}"} for i in range(30)]
    s = ss.summarize(rows, "vert", "t60_pnl")
    assert s["n"] == 30 and s["mean"] == pytest.approx(0.1) and s["win"] == 1.0
    assert ss.summarize(rows, "ic", "hold_pnl") is None
    v, w = ss.verdicts(rows)
    assert "لا حكم بعد" in v and "لا حكم بعد" in w


def test_vert_vs_naked_pairs_with_exit_shadow_log(tmp_path, monkeypatch):
    p = tmp_path / "exit.csv"
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["pos_id", "variant", "t60_pnl"])
        w.writeheader()
        for i in range(30):
            w.writerow({"pos_id": f"p{i}", "variant": "atm1", "t60_pnl": "-0.10"})
            w.writerow({"pos_id": f"p{i}", "variant": "bot", "t60_pnl": "0.50"})
    monkeypatch.setattr(ss, "EXIT_SHADOW_LOG", str(p))
    rows = [{"kind": "vert", "date": f"2026-11-{1 + i % 25:02d}", "capital": "1.0", "t60_pnl": "0.10", "pos_id": f"p{i}"} for i in range(30)]
    d = ss.vert_vs_naked(rows)
    assert len(d) == 30 and d[0][1] == pytest.approx(0.2)
    v, _ = ss.verdicts(rows)
    assert "تحققت" in v


class FakeQuotes:
    """تسعيرة تتناقص مع البعد عن 700: قريب غالٍ وبعيد رخيص، فيكون للكوندور ائتمان موجب."""

    def __init__(self, scale=1.0):
        self.scale = scale

    def __call__(self, occs):
        out = {}
        for o in occs:
            k = int(o[-8:]) / 1000
            p = max(0.05, 3.0 - 0.45 * abs(k - 700)) * self.scale
            out[o] = ({"bid": round(p, 2), "ask": round(p + 0.02, 2)}, "alpaca")
        return out


def test_run_records_condor_once_a_day_and_vert_plus_em_per_recommendation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rec = {"id": "r1", "sym": "SPY", "side": "CALL", "strike": 700.0, "exp": "2026-10-26", "time": t(10, 28).isoformat()}
    monkeypatch.setattr(ob, "load_positions", lambda: {"positions": [rec]})
    spot, vix = (lambda: 700.0), (lambda: 16.0)
    st = ss.run(t(10, 30), FakeQuotes(), spot, vix)
    kinds = sorted(i["kind"] for i in st["items"])
    assert kinds == ["em05", "ic", "vert"]
    st = ss.run(t(10, 35), FakeQuotes(), spot, vix)
    assert sorted(i["kind"] for i in st["items"]) == ["em05", "ic", "vert"]        # لا تكرار
    assert st["condor_days"] == ["2026-10-26"] and st["done_recs"] == ["r1"]


def test_run_skips_condor_before_a_holiday_and_when_quotes_are_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ob, "load_positions", lambda: {"positions": []})
    st = ss.run(t(10, 30, (2026, 11, 25)), FakeQuotes(), lambda: 700.0, lambda: 16.0)        # الغد عطلة: next_trading_day بعد يومين
    assert st["items"] == []
    st = ss.run(t(10, 30), lambda occs: {}, lambda: 700.0, lambda: 16.0)
    assert st["items"] == []


def test_condor_requires_minimum_credit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ob, "load_positions", lambda: {"positions": []})
    st = ss.run(t(10, 30), FakeQuotes(0.0), lambda: 700.0, lambda: 16.0)           # ائتمان ≈ 0 ← لا دخول
    assert st["items"] == []


def test_finished_items_go_to_log_and_report_is_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ob, "load_positions", lambda: {"positions": []})
    st = ss.run(t(10, 30), FakeQuotes(), lambda: 700.0, lambda: 16.0)
    assert [i["kind"] for i in st["items"]] == ["ic"]
    st = ss.run(t(15, 50, (2026, 10, 27)), lambda occs: {}, lambda: 700.0, lambda: 16.0)
    assert st["items"] == []
    rows = list(csv.DictReader(open(ss.LOG, encoding="utf-8")))
    assert len(rows) == 1 and rows[0]["kind"] == "ic" and float(rows[0]["hold_pnl"]) == pytest.approx(float(rows[0]["entry_cash"]))
    assert json.loads(rows[0]["legs"])[0]["occ"].startswith("SPY")
    ss.report()
    assert "كوندور حديدي" in open(ss.REPORT, encoding="utf-8").read()
