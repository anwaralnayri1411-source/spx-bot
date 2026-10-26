"""اختبارات «روتين ما قبل الافتتاح»: شموع الساعة، مستويات السيولة، الكنس، CISD وIFVG، الدخول والهدف، المرآة، ومعايرة العدم. بيانات مصطنعة."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import preopen_routine_study as pr  # noqa: E402
from test_fibo_entry_study import random_series  # noqa: E402

LEVELS = [99.5, 101.5]


def day(patch=None):
    """يوم مصمم: افتتاح 100، كنس تحت 99.5 عند الشمعة 2، تأكيد عند 4 (إغلاق 100.2 فوق CISD=100 وفوق IFVG=99.8)، هدف 101.5 عند 6."""
    b = [(100.0, 100.1, 99.8, 99.9), (99.9, 99.95, 99.6, 99.65), (99.65, 99.7, 99.3, 99.4), (99.4, 99.9, 99.35, 99.85),
         (99.85, 100.3, 99.8, 100.2), (100.2, 100.8, 100.1, 100.7), (100.7, 101.6, 100.6, 101.5)]
    b += [(101.5, 101.55, 101.45, 101.5)] * (78 - len(b))
    if patch:
        for i, v in patch.items():
            b[i] = v
    a = np.array(b, float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def test_hourly_bars_and_liquidity_levels():
    o = np.linspace(100, 101, 78)
    hh = pr.hourly(o, o + 0.1, o - 0.1, o)
    assert len(hh[0]) == 7 and hh[1][0] == pytest.approx(o[11] + 0.1) and hh[3][-1] == pytest.approx(o[-1])
    rng = np.random.default_rng(1)
    days = []
    for _ in range(2):
        c = 100 + np.cumsum(rng.normal(0, 0.1, 78))
        days.append((c, c + 0.05, c - 0.05, c))
    lv = pr.liquidity_levels(days)
    assert len(lv) >= 2 and max(lv) >= days[-1][1].max() - 1e-9                            # أعلى أمس ضمن المستويات
    assert pr.liquidity_levels([]) == []


def test_cisd_and_ifvg_helpers():
    o, h, l, c = day()
    assert pr.cisd_level(o, c, 2) == 100.0                                                # السلسلة الهابطة 0–2: افتتاح الشمعة 0
    assert pr.last_bear_fvg_top(h, l, 2) == pytest.approx(99.8)                            # h[2]=99.7 < l[0]=99.8
    assert pr.cisd_level(np.array([1.0, 1.0]), np.array([2.0, 2.0]), 1) is None


def test_bull_trade_enters_on_confirmation_and_hits_the_opposite_liquidity():
    o, h, l, c = day()
    r, kind, d = pr.day_trade(o, h, l, c, LEVELS)
    entry, stop = 100.2, 99.3
    cost = 2 * pr.COST * entry / (entry - stop)
    assert d == 1 and kind == "tp" and r == pytest.approx((101.5 - entry) / (entry - stop) - cost)


def test_no_trade_without_sweep_confirmation_target_or_with_too_much_risk():
    o, h, l, c = day()
    assert pr.day_trade(o, h, l, c, [98.0, 101.5]) is None                                  # لا كنس: أقرب مستوى بيع 98 لم يُكسر
    o2, h2, l2, c2 = day({4: (99.85, 99.95, 99.8, 99.9)} | {k: (99.9, 99.95, 99.85, 99.9) for k in range(5, 78)})
    assert pr.day_trade(o2, h2, l2, c2, LEVELS) is None                                     # لا تأكيد
    assert pr.day_trade(o, h, l, c, [99.5]) is None                                         # لا مستوى فوق الدخول
    o3, h3, l3, c3 = day({2: (99.65, 99.7, 98.5, 99.4)})
    assert pr.day_trade(o3, h3, l3, c3, LEVELS) is None                                     # خطر > 1%


def test_bearish_mirror_gives_the_same_result():
    o, h, l, c = day()
    m = lambda x: 200.0 - x
    up = pr.day_trade(o, h, l, c, LEVELS)
    dn = pr.day_trade(m(o), m(l), m(h), m(c), [m(x) for x in LEVELS])
    assert dn[2] == -1 and dn[1] == "tp" and dn[0] == pytest.approx(up[0], abs=0.02)


def test_null_calibration_on_random_walks():
    df = pd.concat([pr.run_series(random_series(900, s)) for s in (11, 12)], ignore_index=True)
    real = df[df["kind"] == "real"]
    assert len(real) > 80 and abs(real["R"].mean()) < 0.3


def test_verdict_report_and_main(tmp_path, monkeypatch):
    ok = {"n": 400, "mean": 0.2, "ci": {95.0: (0.05, 0.35), 99.6: (0.0, 0.4)}, "tp": 0.4, "diff": (400, 0.1, {95.0: (-0.1, 0.3), 99.6: (-0.2, 0.4)})}
    bad = dict(ok, ci={95.0: (-0.1, 0.3), 99.6: (-0.2, 0.4)})
    assert pr.verdict({"صناديق (مع SPY)": ok, "شركات": ok}, ok) == "H-PREOPEN: تحققت"
    assert pr.verdict({"صناديق (مع SPY)": ok, "شركات": bad}, ok) == "H-PREOPEN: لم تتحقق"
    assert "لا حكم بعد" in pr.verdict({"صناديق (مع SPY)": ok, "شركات": ok}, dict(ok, n=10))
    assert "⚖️" in "\n".join(pr.report({"صناديق (مع SPY)": ok, "شركات": ok, "الكل": ok}, "x"))
    monkeypatch.setattr(pr, "OUT", str(tmp_path / "r.md"))
    assert pr.main([str(tmp_path / "none")]) == 0
