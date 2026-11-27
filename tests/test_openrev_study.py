import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import openrev_study as orv  # noqa: E402


def _day(closes, start="2025-03-03 09:30"):
    idx = pd.date_range(start, periods=len(closes), freq="5min", tz="America/New_York")
    c = np.array(closes, dtype=float)
    o = np.r_[c[0], c[:-1]]
    df = pd.DataFrame({"o": o, "h": np.maximum(o, c) + 0.01, "l": np.minimum(o, c) - 0.01, "c": c, "v": 1000.0}, index=idx)
    df["M5"] = df["M15"] = df["M30"] = 0
    df["adx"] = 30.0
    return orv.day_frame(df)


def test_close_value_clipped():
    assert orv.close_value(orv.TP) == 500 and orv.close_value(-orv.SL) == -300
    assert orv.close_value(1.0) == 500 and orv.close_value(-1.0) == -300
    assert orv.close_value(orv.TP / 2) == 250


def test_classify_trap_and_trend():
    down = [500 - 0.6 * i for i in range(10)]                  # −1.1% حتى ~10:15
    trap = down + [down[-1] + 1.0 * i for i in range(1, 69)]   # ارتداد قوي
    g = _day(trap)
    kind, d, k, ret = orv.classify(g)
    assert kind == "trap" and d == -1 and ret is not None and ret > k
    trend = down + [down[-1] - 0.05 * i for i in range(1, 69)]
    assert orv.classify(_day(trend))[0] == "trend"
    assert orv.classify(_day([500.0] * 78))[0] is None


def test_vwap_cross_detector_against_put():
    c = [500 - 0.5 * i for i in range(10)] + [495 + 2.0 * i for i in range(1, 6)] + [505.0] * 63
    g = _day(c)
    fired = [i for i in range(len(g)) if "D2" in orv.detect(g, i, -1)]
    assert fired and 10 <= fired[0] <= 14


def test_simulate_target_stop_and_sharia():
    c = [500.0] * 5 + [500 - 0.6 * i for i in range(1, 20)] + [488.6] * 54
    g = _day(c)
    g["score"] = np.where(np.arange(len(g)) < 6, -5, 0)
    out = orv.simulate(g)
    assert out and out[0] == 500                               # PUT ثم هبوط ≥0.45%
    g2 = _day([500.0] * 5 + [500 + 0.5 * i for i in range(1, 74)])
    g2["score"] = np.where(np.arange(len(g2)) == 5, -5, np.where(np.arange(len(g2)) == 6, 5, 0))
    out2 = orv.simulate(g2)
    assert out2 == [-300]                                      # PUT يُوقف، ولا CALL والـPUT مفتوح


def test_verdict_requires_both_halves():
    base = {"combo": ("G0", None), "2019–2022": {"net": 1000, "worst": -900, "mdd": -2000},
            "2023–2026": {"net": 1000, "worst": -900, "mdd": -2000}}
    good = {"combo": ("G1", "D1"), "2019–2022": {"net": 950, "worst": -600, "mdd": -1500},
            "2023–2026": {"net": 920, "worst": -700, "mdd": -1800}}
    bad = {"combo": ("G2", "D1"), "2019–2022": {"net": 950, "worst": -600, "mdd": -1500},
           "2023–2026": {"net": 800, "worst": -700, "mdd": -1800}}
    ok = orv.verdict([base, good, bad])
    assert ok[("G1", "D1")] and not ok[("G2", "D1")]
