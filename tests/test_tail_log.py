"""اختبارات سجل الأيام النادرة: الرموز، الشروط، حساب العوائد، وتحليل يوم كامل ببيانات مصطنعة (بلا شبكة)."""
import sys
import os
from datetime import date

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import tail_log as tl  # noqa: E402


def test_occ_symbol_format():
    assert tl.occ(date(2026, 10, 16), "PUT", 7670.0) == "SPXW261016P07670000"
    assert tl.occ(date(2026, 1, 5), "CALL", 7700.0) == "SPXW260105C07700000"


def test_monthly_opex_is_the_third_friday_only():
    assert tl.is_monthly_opex(date(2026, 10, 16))                 # الجمعة الثالثة
    assert not tl.is_monthly_opex(date(2026, 10, 23))             # جمعة عادية
    assert not tl.is_monthly_opex(date(2026, 10, 15))             # خميس


def test_strike_grid_is_multiples_of_five_within_band():
    g = tl.strike_grid(7700.0)
    assert all(k % 5 == 0 for k in g)
    assert min(g) <= 7700 * (1 - tl.STRIKE_BAND) + 5 and max(g) >= 7700 * (1 + tl.STRIKE_BAND) - 5
    assert min(g) > 7700 * 0.96 and max(g) < 7700 * 1.04


def flat_spx(px=7700.0):
    idx = pd.date_range("2026-10-16 09:30", periods=78, freq="5min", tz=tl.NY)
    df = pd.DataFrame({"Open": px, "High": px + 3, "Low": px - 3, "Close": px}, index=idx)
    df["m"] = df.index.hour * 60 + df.index.minute
    return df


def test_spot_at_uses_first_bar_open_at_or_after_the_minute():
    spx = flat_spx()
    assert tl.spot_at(spx, 10 * 60) == 7700.0
    assert tl.spot_at(spx, 17 * 60) is None


def test_candidate_legs_only_out_of_the_money():
    legs = tl.candidate_legs(7700.0, {m: 7700.0 for m in tl.ENTRY_TIMES})
    assert legs
    for m, side, K in legs:
        assert (side == "CALL" and K > 7700) or (side == "PUT" and K < 7700)
    assert {m for m, _, _ in legs} == set(tl.ENTRY_TIMES)


def bars_df(prices):
    """prices: {دقيقة: (o,h,l,c)}"""
    return pd.DataFrame(prices, index=["o", "h", "l", "c"]).T.rename_axis("m")


def test_entry_price_adds_half_spread_and_commission_and_skips_illiquid():
    b = bars_df({600: (1.00, 1.10, 0.95, 1.05)})
    assert tl.entry_price(b, 600) == pytest.approx(1.00 * (1 + tl.HS) + tl.COMMISSION)
    assert tl.entry_price(b, 700) is None                         # لم يُتداول حول الدخول


def test_follow_peak_ret60_and_close_returns():
    b = bars_df({600: (1.0, 1.0, 1.0, 1.0), 605: (1.0, 1.2, 1.0, 1.1), 610: (1.1, 6.0, 1.1, 5.0), 615: (5.0, 5.2, 2.0, 2.0),
                 660: (2.0, 2.0, 1.0, 1.0), 900: (0.2, 0.2, 0.1, 0.1)})
    entry = 1.0 * (1 + tl.HS) + tl.COMMISSION
    peak, peak_hi, peak_m, r60, rc = tl.follow(b, 600, entry)
    sell = lambda p: (p * (1 - tl.HS) - tl.COMMISSION) / entry - 1
    assert peak == pytest.approx(sell(5.0)) and peak_m == 615          # أعلى إغلاق عند الشمعة 610 (تنتهي 615)
    assert peak_hi == pytest.approx(sell(6.0))
    assert r60 == pytest.approx(sell(1.0))                              # أول شمعة تنتهي بعد 60 دقيقة (660→665)
    assert rc == pytest.approx(sell(0.1))


def test_follow_returns_none_when_no_bars_after_entry():
    assert tl.follow(bars_df({500: (1, 1, 1, 1)}), 600, 1.0) is None


def test_analyze_day_finds_the_spike_and_filters_by_price_band():
    spx = flat_spx(7700.0)
    d = date(2026, 10, 16)
    cheap_spike = bars_df({600: (1.0, 1.0, 1.0, 1.0), 610: (1.0, 3.0, 1.0, 2.5), 700: (2.5, 12.0, 2.5, 11.0), 900: (0.5, 0.5, 0.4, 0.4)})
    too_expensive = bars_df({600: (9.0, 9.0, 9.0, 9.0), 700: (12.0, 12.0, 12.0, 12.0)})
    bars = {tl.occ(d, "CALL", 7750.0): cheap_spike, tl.occ(d, "CALL", 7700.0 + 5): too_expensive}
    day, events = tl.analyze_day(d, spx, bars, {"events": "OPEX", "gap_pct": 0.1, "vix_close": 20.0, "vix_chg_pct": 5.0})
    assert day["dow"] == 4 and day["opex_month"] == 1 and day["events"] == "OPEX"
    assert day["contracts"] == 1                                         # الغالي خارج نطاق السعر
    assert day["ge100"] >= 1 and day["max_peak"] > 1.0
    assert events and all(e["peak_ret"] >= 1.0 and e["side"] == "CALL" and e["strike"] == 7750.0 for e in events)
    assert {e["entry_time"] for e in events} >= {"10:00"}
    assert day["range_pct"] == pytest.approx(6 / 7700 * 100, abs=0.01)


def test_analyze_day_without_any_bars_gives_an_empty_but_valid_row():
    day, events = tl.analyze_day(date(2026, 10, 16), flat_spx(), {}, {})
    assert events == [] and day["entries"] == 0 and day["max_peak"] == "" and day["contracts"] == 0


def test_main_without_keys_does_nothing(capsys):
    assert tl.main([]) == 0
    assert "لا مفاتيح" in capsys.readouterr().out


def test_append_rows_writes_header_once(tmp_path):
    p = tmp_path / "x.csv"
    tl.append_rows(str(p), ["a", "b"], [[1, 2]])
    tl.append_rows(str(p), ["a", "b"], [[3, 4]])
    assert p.read_text().splitlines() == ["a,b", "1,2", "3,4"]
