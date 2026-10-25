"""اختبارات دراسة أحداث الرسوم: نسب الحدث ليوم التداول، الدمج، البيتا، النافذة، والحكم. بيانات مصطنعة."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import china_tariff_event_study as ct  # noqa: E402


def idx():
    return pd.bdate_range("2016-12-01", "2020-02-14")


def test_weekend_event_maps_to_next_trading_day_and_overlapping_same_direction_events_merge():
    i = idx()
    assert ct.reaction_day("2018-12-01", i) == pd.Timestamp("2018-12-03")                # سبت ← اثنين
    ev = [("2018-04-02", -1, "a"), ("2018-04-03", -1, "b"), ("2018-04-04", -1, "c"), ("2018-05-20", +1, "d"), ("2018-05-21", +1, "e")]
    out = ct.merge_events(ev, i)
    assert [(d.strftime("%Y-%m-%d"), s) for d, s, _ in out] == [("2018-04-02", -1), ("2018-04-04", -1), ("2018-05-21", +1)]
    # 04-03 يلي 04-02 مباشرة فيُهمل؛ 04-04 بعده بيومين فيدخل؛ 05-20 (أحد) ← 05-21 ثم 05-21 نفسه مكرر فيُهمل
    assert ct.reaction_day("2030-01-01", i) is None


def test_fixed_event_list_follows_the_registered_selection_rule():
    dates = [d for d, _, _ in ct.EVENTS]
    assert dates == sorted(dates) and len(dates) == len(set(dates))
    for excluded in ("2018-01-22", "2018-03-01", "2018-04-05", "2018-07-06", "2018-08-23", "2019-09-01", "2018-12-11", "2019-05-09"):
        assert excluded not in dates
    assert all(s in (-1, 1) for _, s, _ in ct.EVENTS) and 20 <= len(ct.EVENTS) <= 30


def test_beta_recovers_the_true_loading_and_window_return_spans_two_days():
    rng = np.random.default_rng(1)
    i = idx()
    m = rng.normal(0.0004, 0.01, len(i))
    spy = pd.Series(100 * np.cumprod(1 + m), index=i)
    stock = pd.Series(50 * np.cumprod(1 + 1.4 * m + rng.normal(0, 0.003, len(i))), index=i)
    assert ct.beta(stock, spy) == pytest.approx(1.4, abs=0.15)
    d = pd.Timestamp("2018-06-15")
    pos = i.get_loc(d)
    assert ct.window_return(spy, d) == pytest.approx(spy.iloc[pos + 1] / spy.iloc[pos - 1] - 1)
    assert ct.window_return(spy, i[0]) is None


def prices_with_effect(effect, seed=2):
    rng = np.random.default_rng(seed)
    i = idx()
    m = rng.normal(0.0004, 0.01, len(i))
    out = {"SPY": pd.Series(100 * np.cumprod(1 + m), index=i)}
    ev = ct.merge_events(ct.EVENTS, i)
    for t in ct.TICKERS:
        r = 1.2 * m + rng.normal(0, 0.012, len(i))
        for d, s, _ in ev:
            r[i.get_loc(d)] += s * effect
        out[t] = pd.Series(50 * np.cumprod(1 + r), index=i)
    return out


def test_planted_event_effect_is_detected_and_pure_noise_is_not():
    rows, spy_row, n = ct.run(prices_with_effect(0.02))
    assert n >= 18
    assert all(ct.judge(v)[0] == "تحققت" for _, v in rows)
    rows0, _, _ = ct.run(prices_with_effect(0.0, seed=3))
    assert sum(1 for _, v in rows0 if ct.judge(v)[0] == "تحققت") <= 1
    assert ct.judge(np.array([0.1, 0.2]))[0] == "لا حكم"


def test_decision_rule_and_report_text():
    rows, spy_row, n = ct.run(prices_with_effect(0.03, seed=4))
    spy_row = np.abs(spy_row) + 0.01                                       # فحص سلامة ناجح مصطنع
    text = "\n".join(ct.report(rows, spy_row, n, "تجربة"))
    assert "نبني طبقة الصافي والإلغاء" in text and "NVDA" in text and "98.3%" in text
    rows0, spy0, n0 = ct.run(prices_with_effect(0.0, seed=5))
    assert "لا نبني" in "\n".join(ct.report(rows0, spy0, n0, "x"))


def test_main_survives_download_failure(monkeypatch):
    monkeypatch.setattr(ct, "load_prices", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert ct.main([]) == 1
