import pandas as pd

import shadow_confluence as sc


def _bars(rows):
    idx = pd.date_range("2026-10-23 09:30", periods=len(rows), freq="5min", tz="America/New_York")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).reset_index(drop=True)


def test_flat_day_has_no_confluence():
    g = _bars([(100, 100.05, 99.95, 100)] * 60)
    assert sc.zones(g) == [] and sc.day_rows(g) == []


def test_touch_after_break_is_ignored():
    # منطقة صاعدة ثم إغلاق تحتها قبل أي لمس → لا حالة
    g = _bars([(100, 100.1, 99.9, 100)] * 5 + [(100, 100.0, 99.0, 99.2)] + [(99.2, 99.3, 98.5, 98.6)] * 40)
    assert sc.first_touch(g, "bull", 7, 100.2, 100.4) is None
