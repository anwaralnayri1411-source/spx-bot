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
    _write(tmp_path, "earnings_runup_log.csv", [{"date": "2026-11-06", "entry": "4.00", "status": "هدف", "pnl_usd": "200", "last_ret": "0.5"}])
    _write(tmp_path, "blocked_shadow_log.csv", [{"project": "stocks", "tag": "earn_in_life", "entry_time": "2026-11-05T10:12",
                                                 "entry": "3.00", "out": "stop", "out_pnl": "-0.45"}])
    t = pb.trades()
    s = t.groupby("strategy")["usd"].sum().round(2).to_dict()
    assert s == {"spx": 150.0, "stocks": -80.0, "levels": 20.0, "runup": 200.0, "blocked_earn": -135.0}
    text, alerts = pb.report(t, datetime(2026, 11, 7, 10, 0, tzinfo=pb.NY))
    assert "+150$" in text and "القاعدة حمتنا" in text and alerts == []


def test_stop_hit():
    mk = lambda xs: pd.DataFrame({"strategy": "x", "day": [f"2026-11-{5 + i:02d}" for i in range(len(xs))], "closed": True, "usd": xs})
    assert pb.stop_hit(mk([-120] * 5)) and pb.stop_hit(mk([-300, 50, -300])) and not pb.stop_hit(mk([-100, 50, -100]))
