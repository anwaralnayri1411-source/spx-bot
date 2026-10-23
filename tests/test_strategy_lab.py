"""اختبارات مختبر الاستراتيجيات: الإحصاء، فرق الخمسي، تغذية الميزات، إطار المؤشر، والتقرير. بيانات مصطنعة، بلا شبكة."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import strategy_lab as sl  # noqa: E402


def frame(n_dates=40, per=8, predictive=True, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_dates):
        for j in range(per):
            e = float(rng.choice([-4.0, -2.0, 2.0, 4.0]))
            ar = (0.01 * np.sign(e) if predictive else 0.0) + rng.normal(0, 0.01)
            rows.append({"date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=5 * i), "t": f"T{j}", "edge": e, "ar": ar, "rand": rng.choice([-1, 1]),
                         "mom12_1": e, "mom20": e, "rev5": e, "rsi2": e, "brk20": np.sign(e), "trend": e})
    return pd.DataFrame(rows)


def test_boot_ci_needs_three_groups_and_brackets_the_mean():
    assert sl.boot_ci({"a": [1.0], "b": [2.0]}) is None
    lo, hi = sl.boot_ci({"a": [0.1], "b": [0.2], "c": [0.3], "d": [0.2]})
    assert lo <= 0.2 <= hi


def test_a_real_edge_has_an_interval_above_zero_and_noise_includes_zero():
    s = sl.evaluate(frame(predictive=True), "edge", 0)
    assert s["hit"] > 0.7 and s["ci"][0] > 0
    n = sl.evaluate(frame(predictive=False), "edge", 0)
    assert n["ci"][0] < 0 < n["ci"][1]


def test_evaluate_skips_small_samples_and_threshold_filters_weak_signals():
    assert sl.evaluate(frame(2, 4), "edge", 0) is None
    strong = sl.evaluate(frame(), "edge", 3.0)
    assert strong["n"] < sl.evaluate(frame(), "edge", 0)["n"]


def test_quintile_spread_detects_ranking_value_and_reports_halves():
    sp = sl.quintile_spread(frame(predictive=True))
    assert sp["mean"] > 0 and sp["ci"][0] > 0 and sp["win"] > 0.8 and sp["first"] > 0 and sp["second"] > 0
    assert sl.quintile_spread(frame(5, 8)) is None


def test_stock_features_signs():
    n = 300
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    c = np.linspace(50, 150, n)
    up = pd.DataFrame({"Close": c, "High": c, "Low": c}, index=idx)
    f = sl.stock_features(up)
    assert f["mom12_1"] > 0 and f["mom20"] > 0 and f["rev5"] < 0 and f["brk20"] == 1 and f["trend"] == 2
    down = up.iloc[::-1].copy()
    down.index = idx
    g = sl.stock_features(down)
    assert g["brk20"] == -1 and g["trend"] == -2


def test_stock_rows_demeans_returns_and_uses_the_bot_function_without_lookahead():
    n = 400
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    rng = np.random.default_rng(3)

    def mk(drift):
        c = 100 * np.cumprod(1 + rng.normal(drift, 0.01, n))
        return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1e6}, index=idx)
    D = {"SPY": mk(0.0003), "A": mk(0.001), "B": mk(0.0), "C": mk(-0.001)}
    seen = []

    def fake_tp(df, spy5, drv, vol):
        seen.append(len(df))
        return 3.0, 1.0, [], {}
    R = sl.stock_rows(D, ["A", "B", "C"], fake_tp, 5)
    assert len(R) > 0 and abs(R.groupby("date")["ar"].mean()).max() < 1e-12            # متوسط كل تاريخ = 0
    assert set(R["edge"].round(2)) <= {1.5, 2.5}                                         # 3−1 ثم +0.5 للصعود أو −0.5 لهبوط السوق
    assert max(seen) < n - 5                                                           # لا يرى بيانات المستقبل


def hourly(days=60):
    idx, rows = [], []
    rng = np.random.default_rng(4)
    p = 400.0
    for d in pd.bdate_range("2026-01-05", periods=days):
        for k, hh in enumerate([9, 10, 11, 12, 13, 14, 15]):
            o = p
            p = p * (1 + rng.normal(0, 0.002))
            idx.append(pd.Timestamp(d).tz_localize("America/New_York") + pd.Timedelta(hours=hh, minutes=30 if hh == 9 else 30))
            rows.append({"Open": o, "High": max(o, p), "Low": min(o, p), "Close": p})
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))


def test_index_frame_aligns_hourly_and_daily_and_builds_returns_without_nans():
    h = hourly(80)
    days = pd.bdate_range("2025-10-01", periods=260)
    spy = pd.Series(np.linspace(380, 420, len(days)), index=days)
    vix = pd.Series(np.full(len(days), 18.0), index=days)
    X = sl.index_frame(h, {"spy": spy, "vix": vix})
    assert len(X) > 20 and not X[["gap", "fh", "rest", "next"]].isna().any().any()
    sig = sl.index_signals(X)
    assert set(sig) >= {"أول ساعة: استمرار", "فجوة الافتتاح: ملء", "عشوائي (مرجع)"}
    assert (sig["أول ساعة: استمرار"] == -sig["أول ساعة: انعكاس"]).all()


def test_report_lists_every_rule_and_survives_missing_sections():
    R = frame()
    X = pd.DataFrame({"rest": np.random.default_rng(1).normal(0, 0.005, 120), "next": np.random.default_rng(2).normal(0, 0.005, 120),
                      "fh": np.random.default_rng(3).normal(0, 0.003, 120), "gap": np.random.default_rng(4).normal(0, 0.004, 120),
                      "prev_ret": np.random.default_rng(5).normal(0, 0.008, 120), "o": 410.0, "sma50": 400.0,
                      "vix_chg": np.random.default_rng(6).normal(0, 0.06, 120)}, index=pd.bdate_range("2026-01-01", periods=120))
    text = "\n".join(sl.report(R, X, ["- تنبيه"]))
    for name, _, _ in sl.STOCK_RULES:
        assert name in text
    assert "فرق أعلى 20%" in text and "أول ساعة: استمرار" in text and "- تنبيه" in text
    empty = "\n".join(sl.report(None, None, []))
    assert empty.count("لا بيانات") == 2


def test_main_with_no_fetch_writes_the_report(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "OUT", str(tmp_path / "r.md"))
    assert sl.main(["--no-fetch"]) == 0
    assert "مختبر الاستراتيجيات" in (tmp_path / "r.md").read_text(encoding="utf-8")
