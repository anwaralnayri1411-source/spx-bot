"""اختبارات المعايرة الطويلة بأسعار العقود الحقيقية: التخطيط، مطابقة الشموع، الإزدواج، والتقرير. بلا شبكة."""
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import long_price_calibration as lp  # noqa: E402
import option_buyer_cost as obc  # noqa: E402

NY = "America/New_York"


def day_frame(day=date(2026, 10, 19), s=700.0):
    t = [pd.Timestamp(day).tz_localize(NY) + pd.Timedelta(hours=9, minutes=30 + 5 * i) for i in range(78)]
    c = np.full(78, s)
    return c, t


def test_plan_day_builds_both_expiries_sides_and_four_entries():
    c, t = day_frame()
    out = lp.plan_day(date(2026, 10, 19), c, t, 16.0)
    assert len(out) == 4 * 2 * 2
    s = next(x for x in out if x["i"] == 11 and x["dte"] == 1 and x["side"] == "PUT")
    assert s["sym"] == "SPY261020P00700000" and s["t0"] == t[11] + pd.Timedelta(minutes=5) and s["t1"] == s["t0"] + pd.Timedelta(minutes=60)
    z = next(x for x in out if x["dte"] == 0)
    assert z["sym"].startswith("SPY261019")
    friday = lp.plan_day(date(2026, 10, 23), *day_frame(date(2026, 10, 23)), 16.0)
    assert any(x["sym"].startswith("SPY261026") for x in friday)                     # الجمعة ← الاثنين


def bars_at(times_prices):
    idx = pd.DatetimeIndex([t for t, _ in times_prices])
    return pd.DataFrame({"o": [p for _, p in times_prices]}, index=idx)


def test_real_return_uses_first_trade_after_the_time_and_skips_stale_or_missing():
    c, t = day_frame()
    s = lp.plan_day(date(2026, 10, 19), c, t, 16.0)[0]
    b = bars_at([(s["t0"] + pd.Timedelta(minutes=5), 1.00), (s["t1"], 1.20)])
    assert lp.real_return(s, b) == pytest.approx(1.20 * (1 - lp.H) / (1.00 * (1 + lp.H)) - 1)
    assert lp.real_return(s, bars_at([(s["t0"] + pd.Timedelta(minutes=30), 1.0), (s["t1"], 1.2)])) is None      # أول صفقة بعد أكثر من 10 دقائق
    assert lp.real_return(s, bars_at([(s["t0"], 1.0)])) is None
    assert lp.real_return(s, None) is None


def test_theory_return_is_negative_for_flat_path_and_pairs_become_random_samples():
    c, t = day_frame()
    out = lp.plan_day(date(2026, 10, 19), c, t, 16.0)
    assert all(lp.theory_return(x) < 0 for x in out)                              # سعر ثابت: الثيتا والفرق فقط
    rows = []
    for x in out:
        rows.append({"day": x["day"], "i": x["i"], "dte": x["dte"], "side": x["side"], "real": -0.1 if x["side"] == "CALL" else -0.3, "theory": lp.theory_return(x), "up": True})
    P = lp.pair_up(rows)
    assert len(P) == 8 and P["real"].iloc[0] == pytest.approx(-0.2) and P["up"].all()
    assert lp.pair_up([]).empty


def test_summarize_and_report_show_each_entry_time_and_the_gap_to_theory():
    rng = np.random.default_rng(1)
    rows = []
    for d in pd.bdate_range("2025-01-02", periods=80):
        for i in obc.ENTRIES:
            for dte in (0, 1):
                up = bool(rng.random() < 0.5)
                for side in ("CALL", "PUT"):
                    win = (side == "CALL") == up
                    rows.append({"day": d, "i": i, "dte": dte, "side": side, "real": (0.12 if win else -0.2) + rng.normal(0, 0.02), "theory": (0.1 if win else -0.25), "up": up})
    P = lp.pair_up(rows)
    s = lp.summarize(P)
    assert s["n"] == 80 * 8 and s["days"] == 80 and s["be"] is not None and s["ci"][0] < s["real"] < s["ci"][1]
    text = "\n".join(lp.report(P, {"dropped": 3, "total": 100}, "تجربة"))
    for clock in ("10:30", "11:30", "12:30", "13:30"):
        assert clock in text
    assert "0DTE" in text and "1DTE" in text and "حقيقي − نظري" in text and "3 من 100" in text
    assert "لا عينات" in "\n".join(lp.report(pd.DataFrame(), {}, "x"))


def test_main_without_keys_or_data_is_a_no_op(monkeypatch, tmp_path):
    monkeypatch.delenv("ALPACA_KEY", raising=False)
    assert lp.main([]) == 0
