import pandas as pd

import shadow_ob_score as so


def _bars(rows):
    idx = pd.date_range("2026-10-26 09:30", periods=len(rows), freq="5min", tz="America/New_York")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def test_flat_day_has_no_zones():
    assert so.day_rows(_bars([(100, 100.05, 99.95, 100)] * 70)) == []


def test_score_is_sum_of_factors():
    # شمعة هابطة ثم اندفاع صاعد ≥0.25%، ثم عودة للمنطقة
    rows = [(100, 100.1, 99.9, 100)] * 14 + [(100, 100.0, 99.8, 99.85)] + [(99.85, 100.2, 99.85, 100.15), (100.15, 100.5, 100.1, 100.45)] \
        + [(100.45, 100.5, 100.3, 100.4)] * 4 + [(100.3, 100.3, 99.7, 99.9)] + [(99.9, 100.0, 99.8, 99.95)] * 30
    for r in so.day_rows(_bars(rows)):
        assert r[9] == r[5] + r[6] + r[7] + r[8]
