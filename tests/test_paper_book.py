"""اختبارات الدفتر الورقي بالدولار (H-PAPER). لا شبكة."""
from datetime import datetime

import pandas as pd

import paper_book as pb


def _write(tmp_path, name, rows):
    pd.DataFrame(rows).to_csv(tmp_path / name, index=False)


def test_trades_and_report(tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "ROOT", str(tmp_path))
    _write(tmp_path, "analyst_cards.csv", [
        {"project": "spx", "day": "2026-11-05", "entry": "3.00", "exit_kind": "هدف", "exit_ret": "0.5"},
        {"project": "stocks", "day": "2026-11-05", "entry": "2.00", "exit_kind": "وقف", "exit_ret": "-0.4"},
        {"project": "stocks", "day": "2026-11-04", "entry": "2.00", "exit_kind": "وقف", "exit_ret": "-0.4"}])   # قبل البداية
    _write(tmp_path, "level_signals_log.csv", [{"date": "2026-11-06", "entry": "2.00", "status": "مفتوحة", "exit_ret": "", "last_ret": "0.1"}])
    _write(tmp_path, "earnings_runup_log.csv", [
        {"date": "2026-11-06", "entry": "4.00", "status": "مال السوق", "pnl_usd": "200", "last_ret": "0.5"},
        {"date": "2026-11-06", "entry": "4.00", "status": "نصف", "pnl_usd": "", "last_ret": "-0.5"},       # 400 + 400×−0.5
        {"date": "2026-11-06", "entry": "4.00", "status": "مفتوحة", "pnl_usd": "", "last_ret": "0.1"}])   # عقدان ×0.1
    _write(tmp_path, "blocked_shadow_log.csv", [{"project": "stocks", "tag": "earn_in_life", "entry_time": "2026-11-05T10:12",
                                                 "entry": "3.00", "out": "stop", "out_pnl": "-0.45"}])
    t = pb.trades()
    s = t.groupby("strategy")["usd"].sum().round(2).to_dict()
    assert s == {"spx": 150.0, "stocks": -80.0, "levels": 20.0, "runup": 480.0, "blocked_earn": -135.0}
    text, alerts = pb.report(t, datetime(2026, 11, 7, 10, 0, tzinfo=pb.NY))
    assert "+150$" in text and "القاعدة حمتنا" in text and alerts == []


def test_stop_hit():
    mk = lambda xs: pd.DataFrame({"strategy": "x", "day": [f"2026-11-{5 + i:02d}" for i in range(len(xs))], "closed": True, "usd": xs})
    assert pb.stop_hit(mk([-120] * 5)) and pb.stop_hit(mk([-300, 50, -300])) and not pb.stop_hit(mk([-100, 50, -100]))


def test_rule_c_observation(tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "ROOT", str(tmp_path))
    _write(tmp_path, "rule_c_log.csv", [{"day": "2026-11-06", "c_usd": "120.5"}, {"day": "2026-11-06", "c_usd": "0.0"},
                                        {"day": "2026-11-09", "c_usd": "-150"}, {"day": "2026-11-04", "c_usd": "99"}])
    t = pb.trades()
    g = t[t["strategy"] == "obs_rule_c"]
    assert len(g) == 3 and round(g["usd"].sum(), 1) == -29.5
    text, alerts = pb.report(t, datetime(2026, 11, 10, 10, 0, tzinfo=pb.NY))
    assert "القاعدة C" in text and alerts == []
