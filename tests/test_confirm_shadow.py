"""اختبارات ظل تأكيد حركة السوق (H-CONFIRM). لا شبكة."""
from datetime import date, datetime

import numpy as np
import pandas as pd

import confirm_shadow as cs


def _spy(days=25, today_vol=300.0):
    idx, vol = [], []
    for k, d in enumerate(pd.bdate_range("2026-10-01", periods=days)):
        for m in range(0, 390, 5):
            idx.append(d + pd.Timedelta(minutes=570 + m))
            vol.append(today_vol if k == days - 1 else 100.0)
    return pd.DataFrame({"Close": 1.0, "Volume": vol}, index=pd.DatetimeIndex(idx))


def test_first_hour_volume_and_ratio():
    d5 = _spy()
    fh = cs.first_hour_volume(d5)
    assert fh.iloc[0] == 1200.0                                  # 12 شمعة × 100
    last = fh.index[-1]
    assert abs(cs.vol_ratio(fh, last) - 3.0) < 1e-9
    assert cs.vol_ratio(fh, fh.index[3]) is None                  # تاريخ قصير
    assert cs.vol_ratio(fh, date(2030, 1, 1)) is None


def test_breadth_and_classify_groups():
    moves = {s: 0.01 for s in cs.SECTORS[:9]} | {"XLC": -0.01, "XLRE": None}
    b, nb = cs.breadth(moves, "CALL")
    assert (b, nb) == (9, 10)
    c = cs.classify(0.006, 1.5, b, nb)
    assert c["group"] == "both" and c["move_day"] and c["side"] == "CALL"
    assert cs.classify(-0.001, 0.8, 3, 11)["group"] == "none" and not cs.classify(-0.001, 0.8, 3, 11)["move_day"]
    assert cs.classify(0.006, 1.5, 9, 9)["group"] == "vol_only"  # قطاعات ناقصة: لا تأكيد اتساع
    assert cs.classify(0.006, None, 9, 11)["group"] == "breadth_only"


def test_outcome_signed():
    assert cs.outcome("CALL", 100.0, 101.0, 99.0) == (0.010000000000000009, -0.010000000000000009)
    oc, on = cs.outcome("PUT", 100.0, 99.0)
    assert abs(oc - 0.01) < 1e-12 and on is None


def test_evaluate_fills_outcomes_and_report(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "LOG", str(tmp_path / "log.csv"))
    monkeypatch.setattr(cs, "RESULTS", str(tmp_path / "r" / "res.md"))
    row = {k: "" for k in cs.COLS}
    row.update(date="2026-11-02", time="10:35", spy_1030="100", side="CALL", move_day=True, group="both", event="")
    cs.append(row)
    closes = pd.Series([101.0, 102.0], index=[date(2026, 11, 2), date(2026, 11, 3)])
    md = cs.evaluate(datetime(2026, 11, 3, 17, 0), closes)
    log = cs.read_log()
    assert abs(float(log.at[0, "out_close"]) - 0.01) < 1e-9 and abs(float(log.at[0, "out_next"]) - 0.02) < 1e-9
    assert "الحجم والاتساع معاً" in md and (tmp_path / "r" / "res.md").exists()


def test_main_gate(monkeypatch, capsys):
    monkeypatch.setattr(cs, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: datetime(2026, 11, 4, 9, 50, tzinfo=cs.NY)),
                                                       "fromisoformat": datetime.fromisoformat}))
    monkeypatch.setattr(cs, "snapshot", lambda n: (_ for _ in ()).throw(AssertionError("لا يجب أن يُستدعى")))
    cs.main([])
    assert "خارج" in capsys.readouterr().out
