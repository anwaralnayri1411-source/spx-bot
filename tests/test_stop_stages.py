"""اختبارات مراحل رفع الوقف (H-STOPSTAGES). لا شبكة."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "research"))
import stop_stages_test as ss  # noqa: E402

T0 = pd.Timestamp("2026-10-20 10:00", tz=ss.mr.NY)


def bars(rows):
    i = pd.date_range(T0, periods=len(rows), freq="1min")
    return pd.DataFrame(rows, columns=["o", "h", "l", "c"], index=i)


def run(rows, key, cost=0.0):
    return ss.simulate(bars(rows), T0, 2.0, ss.RULES[key], cost)


def test_baseline_target_and_stop():
    assert run([(2, 2.5, 1.9, 2.4), (2.4, 3.1, 2.4, 3.0)], "tp50") == 0.5
    assert run([(2, 2.2, 0.9, 1.0)], "tp50") == -0.5
    assert run([(0.8, 0.9, 0.7, 0.8)], "tp50") == -0.6            # فجوة تحت الوقف: يُنفَّذ عند الافتتاح
    assert abs(run([(2, 3.1, 2, 3)], "tp50", 0.015) - (1.5 * 0.985 - 1)) < 1e-9


def test_worst_case_inside_bar():
    assert run([(2, 3.5, 0.9, 3)], "tp50") == -0.5                # الوقف قبل الهدف في الشمعة نفسها
    # المرحلة ترفع الوقف من الشمعة التالية فقط
    assert run([(2, 3.1, 1.9, 2.0), (2.0, 2.1, 1.9, 2.0)], "wide") == -0.0


def test_wide_stages_and_half():
    r = [(2, 4.2, 2, 4), (4, 6.2, 4, 6), (6, 6.1, 3.6, 3.7)]      # +200% ← وقف 60% من القمة 6.2 = 3.72
    assert abs(run(r, "wide") - (3.72 / 2 - 1)) < 1e-9
    assert abs(run(r, "wide_half") - (0.5 + 0.5 * (3.72 / 2 - 1))) < 1e-9
    assert abs(run([(2, 2.4, 2, 2.3), (2.3, 2.4, 2.2, 2.3)], "hold") - 0.15) < 1e-9
    assert ss.simulate(bars([(2, 3, 1.9, 2)]), T0 + pd.Timedelta(days=1), 2.0, ss.RULES["tp50"]) is None


def test_verdict_needs_all_samples_and_best_day_check():
    def tab(mean, no_best, lo):
        t = pd.DataFrame([{"key": k, "mean": 0.0, "no_best": 0.0, "base_no_best": 0.0, "lo": -1.0} for k in ss.RULES])
        t.loc[t.key == "wide", ["mean", "no_best", "lo"]] = [mean, no_best, lo]
        return t
    good = tab(0.1, 0.05, 0.01)
    assert ss.verdict({"early": good, "late": good, "sig": good}) == [("wide", True, 0.05)]
    assert ss.verdict({"early": tab(-0.1, 0.05, 0.01), "late": good, "sig": good}) == []
    assert ss.verdict({"early": good, "late": good, "sig": tab(0.1, -0.05, 0.01)}) == []
    assert ss.verdict({"early": good, "late": good, "sig": tab(0.1, 0.05, -0.01)})[0][1] is False


def test_boot_diff_by_day():
    lo, hi = ss.boot_diff(np.array([1.0, 1.0, 1.2, 1.2]), np.zeros(4), np.array(["a", "a", "b", "b"]), np.random.default_rng(1), 200)
    assert 0.99 < lo <= hi < 1.21
