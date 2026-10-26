"""اختبارات استراتيجية FIBO ENTRY: المحاور، الإعداد، التنفيذ والخروج، المرآة، ومعايرة العدم على سير عشوائي. بيانات مصطنعة."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fibo_entry_study as fe  # noqa: E402


def arrays(patch=None, n=26):
    """سلسلة 15 دقيقة مصممة: قمة H0=102 عند 2، قاع L=99 عند 6، BOS عند 9، قمة H=105 عند 12، تأكيد عند 14، تنفيذ عند 16، هدف عند 19."""
    b = [(100.0, 100.5, 99.8, 100.3), (100.3, 101.0, 100.2, 100.9), (100.9, 102.0, 100.8, 101.6), (101.6, 101.7, 101.0, 101.1),
         (101.1, 101.2, 100.3, 100.5), (100.5, 100.6, 99.6, 99.8), (99.8, 100.0, 99.0, 99.4), (99.4, 100.2, 99.3, 100.0),
         (100.0, 101.0, 99.9, 100.8), (100.8, 102.5, 100.7, 102.3), (102.3, 103.5, 102.2, 103.4), (103.4, 104.4, 103.3, 104.3),
         (104.3, 105.0, 104.0, 104.8), (104.8, 104.9, 103.9, 104.0), (104.0, 104.1, 103.2, 103.4), (103.4, 103.5, 102.0, 102.2),
         (102.2, 102.3, 100.0, 100.5), (100.5, 102.0, 100.4, 101.8), (101.8, 103.5, 101.7, 103.2), (103.2, 105.2, 103.1, 105.0)]
    b += [(105.0, 105.1, 104.9, 105.0)] * (n - len(b))
    if patch:
        for i, v in patch.items():
            b[i] = v
    a = np.array(b, float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


@pytest.fixture(autouse=True)
def wide_leg(monkeypatch):
    monkeypatch.setattr(fe, "LEG_MAX", 0.10)                      # الساق في المثال 6% (للتوضيح فقط)


def test_pivots_require_strict_extremes_with_two_bars_each_side():
    o, h, l, c = arrays()
    assert 2 in fe.pivot_highs(h) and 12 in fe.pivot_highs(h) and 6 in fe.pivot_lows(l)
    assert 0 not in fe.pivot_highs(h) and 25 not in fe.pivot_highs(h)


def test_setup_fills_at_the_79_level_and_reaches_the_target():
    o, h, l, c = arrays()
    t, r, kind = fe.bull_trade(o, h, l, c)
    leg = 6.0
    entry, stop = 105 - 0.79 * leg, 99 - 0.05 * leg
    cost = 2 * fe.COST * entry / (entry - stop)
    assert t == 16 and kind == "tp" and r == pytest.approx((105 - entry) / (entry - stop) - cost)
    assert 3.0 < r < 3.1                                                                       # نحو 1:3 كما في الفيديو


def test_stop_hit_after_the_fill_and_in_the_fill_bar():
    o, h, l, c = arrays({17: (100.5, 102.0, 98.0, 99.0)})
    t, r, kind = fe.bull_trade(o, h, l, c)
    assert kind == "sl" and r < -1.0
    o2, h2, l2, c2 = arrays({16: (102.2, 102.3, 98.0, 100.5)})
    assert fe.bull_trade(o2, h2, l2, c2)[2] == "sl"


def test_order_is_cancelled_by_a_new_high_or_close_below_the_low_before_fill():
    o, h, l, c = arrays({15: (103.4, 106.0, 102.0, 102.2)})                                    # تجاوز H قبل الوصول إلى 0.79
    assert fe.bull_trade(o, h, l, c) is None
    o2, h2, l2, c2 = arrays({15: (103.4, 103.5, 100.9, 98.0)})                                 # إغلاق تحت L قبل التنفيذ
    assert fe.bull_trade(o2, h2, l2, c2) is None


def test_no_trade_when_price_is_already_below_the_entry_at_confirmation_or_leg_is_wrong_size(monkeypatch):
    o, h, l, c = arrays({14: (104.0, 104.1, 99.5, 100.0)})                                     # إغلاق التأكيد تحت E=100.26
    assert fe.bull_trade(o, h, l, c) is None
    monkeypatch.setattr(fe, "LEG_MAX", 0.03)
    o2, h2, l2, c2 = arrays()
    assert fe.bull_trade(o2, h2, l2, c2) is None                                               # الساق 6% أكبر من 3%
    monkeypatch.setattr(fe, "LEG_MAX", 0.10)
    monkeypatch.setattr(fe, "LEG_MIN", 0.5)
    assert fe.bull_trade(o2, h2, l2, c2) is None


def test_bearish_mirror_gives_the_same_trade():
    o, h, l, c = arrays()
    m = lambda x: 200.0 - x
    r_up = fe.day_trade(o, h, l, c)
    r_dn = fe.day_trade(m(o), m(l), m(h), m(c))
    assert r_up[1] == "tp" and r_up[2] == 1 and r_dn[1] == "tp" and r_dn[2] == -1 and r_dn[0] == pytest.approx(r_up[0], abs=0.02)


def test_agg15_groups_three_bars():
    o = np.arange(78.0)
    a = fe.agg15(o, o + 1, o - 1, o + 0.5)
    assert len(a[0]) == 26 and a[0][1] == 3 and a[1][0] == 3 and a[2][0] == -1 and a[3][0] == pytest.approx(2.5)


def random_series(n_days, seed):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2021-01-04", periods=n_days)
    O, C, p = [], [], 400.0
    for _ in days:
        c = p * np.cumprod(1 + rng.normal(0, 0.0009, 78))
        O.append(np.r_[p, c[:-1]])
        C.append(c)
        p = float(c[-1])
    o, c = np.concatenate(O), np.concatenate(C)
    base = pd.DatetimeIndex(days).tz_localize("America/New_York") + pd.Timedelta(hours=9, minutes=30)
    t = pd.DatetimeIndex([b + pd.Timedelta(minutes=5 * i) for b in base for i in range(78)])
    return pd.DataFrame({"t": t, "o": o, "h": np.maximum(o, c) * 1.0003, "l": np.minimum(o, c) * 0.9997, "c": c, "v": 1e5})


def test_null_calibration_on_random_walks_mean_r_is_near_minus_cost_and_hit_rate_near_a_quarter(monkeypatch):
    monkeypatch.setattr(fe, "LEG_MAX", 0.03)
    frames = [fe.run_series(random_series(1200, seed)) for seed in (1, 2)]
    df = pd.concat(frames, ignore_index=True)
    assert len(df) > 60
    assert abs(df["R"].mean()) < 0.30                                                          # العدم: لا ميزة
    assert 0.12 < (df["kind"] == "tp").mean() < 0.42


def test_verdict_rules_and_report(tmp_path, monkeypatch):
    ok = {"n": 400, "mean": 0.2, "ci": {95.0: (0.05, 0.35), 99.6: (0.0, 0.4)}, "tp": 0.3, "sl": 0.6}
    bad = {"n": 400, "mean": 0.0, "ci": {95.0: (-0.2, 0.2), 99.6: (-0.3, 0.3)}, "tp": 0.25, "sl": 0.6}
    assert fe.verdict({"صناديق (مع SPY)": ok, "شركات": ok}, ok) == "H-FIBO: تحققت"
    assert fe.verdict({"صناديق (مع SPY)": ok, "شركات": bad}, ok) == "H-FIBO: لم تتحقق"
    assert "لا حكم بعد" in fe.verdict({"صناديق (مع SPY)": ok, "شركات": ok}, dict(ok, n=100))
    text = "\n".join(fe.report({"صناديق (مع SPY)": ok, "شركات": bad, "الكل": ok}, "تجربة"))
    assert "⚖️" in text and "H-FIBO" in text
    monkeypatch.setattr(fe, "OUT", str(tmp_path / "r.md"))
    assert fe.main([str(tmp_path / "none")]) == 0
