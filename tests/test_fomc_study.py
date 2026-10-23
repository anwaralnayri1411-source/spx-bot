"""اختبارات دراسة أيام الفيدرالي: إحصاءات اليوم، التحقق من التواريخ بمدى الحركة، والتقرير. بيانات مصطنعة."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import fomc_study as fs  # noqa: E402


def make_data(fomc_days, ndays=300, spike=0.004, drift_after=0.0, seed=2):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2025-01-02", periods=ndays)
    rows, p = [], 700.0
    for d in days:
        ret = rng.normal(0, 0.0004, 78)
        if str(d.date()) in fomc_days:
            ret[fs.I_1400] += spike * rng.choice([-1, 1])           # قفزة الإعلان
            ret[fs.I_1405:fs.I_1430] += drift_after / 5
        c = p * np.cumprod(1 + ret)
        o = np.r_[p, c[:-1]]
        t = [(pd.Timestamp(d).tz_localize("America/New_York") + pd.Timedelta(hours=9, minutes=30 + 5 * i)) for i in range(78)]
        rows += [{"t": t[i], "o": o[i], "h": max(o[i], c[i]) * 1.0002, "l": min(o[i], c[i]) * 0.9998, "c": c[i], "v": 1e5} for i in range(78)]
        p = float(c[-1])
    return pd.DataFrame(rows), pd.Series(18.0, index=pd.DatetimeIndex(days))


def test_day_stats_windows_use_the_expected_bars_and_need_a_full_day():
    n = 78
    c = np.linspace(700, 701, n)
    s = fs.day_stats(c, c + 0.1, c - 0.1, np.r_[700, c[:-1]])
    assert s["move_pm"] > 0 and s["r_30"] > s["r_5"] > 0 and abs(s["post"] - (c[-1] / c[fs.I_1430 - 1] - 1)) < 1e-12
    assert fs.day_stats(c[:50], c[:50], c[:50], c[:50]) is None


def test_validation_accepts_only_dates_with_a_real_release_spike():
    days = [str(d.date()) for d in pd.bdate_range("2025-01-02", periods=300)]
    good = set(days[40:300:30])                                     # أيام فيها قفزة
    bad = days[20]                                                   # تاريخ خاطئ بلا قفزة
    d, vix = make_data(good)
    X = fs.build(d, vix)
    X["isfomc"] = X["day"].isin(pd.to_datetime(sorted(good) + [bad]))
    X, n_ok = fs.validated(X)
    assert n_ok == len(good) and not X.loc[X["day"] == pd.Timestamp(bad), "ok"].iloc[0]


def test_report_shows_volatility_ratio_pricing_and_continuation_sections():
    days = [str(d.date()) for d in pd.bdate_range("2025-01-02", periods=300)]
    fomc = set(days[10:300:6])
    d, vix = make_data(fomc, spike=0.005)
    X = fs.build(d, vix)
    X["isfomc"] = X["day"].isin(pd.to_datetime(sorted(fomc)))
    X, n_ok = fs.validated(X)
    text = "\n".join(fs.report(X, len(fomc), n_ok, "تجربة"))
    assert n_ok >= 20 and "(أ) التذبذب" in text and "(ب) هل سُعّر الحدث" in text and "(ج) الاستمرار" in text
    a = float(X[X["ok"]]["rng_rel"].mean() / X[~X["isfomc"]]["rng_rel"].mean())
    assert a > 1.5 and f"{a:.2f}×" in text


def test_report_refuses_to_conclude_with_few_events():
    d, vix = make_data(set(), ndays=60)
    X = fs.build(d, vix)
    X["ok"] = False
    assert "لا استنتاج" in "\n".join(fs.report(X, 60, 0, "x"))


def test_continuation_is_detected_when_the_move_keeps_going():
    days = [str(d.date()) for d in pd.bdate_range("2025-01-02", periods=300)]
    fomc = set(days[10:300:5])
    d, vix = make_data(fomc, spike=0.005, drift_after=0.0)
    X = fs.build(d, vix)
    X["isfomc"] = X["day"].isin(pd.to_datetime(sorted(fomc)))
    X, n_ok = fs.validated(X)
    ev = X[X["ok"]]
    v = np.sign(ev["r_30"]) * ev["post"]
    assert len(v) > 20 and abs(float(v.mean())) < 0.003                 # بلا استمرار مبرمج: يقترب من الصفر
    assert fs.boot(v) is not None and fs.boot([1.0, 2.0]) is None


def test_main_runs_on_a_synthetic_file(tmp_path, monkeypatch):
    days = [str(d.date()) for d in pd.bdate_range("2025-01-02", periods=300)]
    d, vix = make_data(set(days[10:300:6]), spike=0.005)
    out = d.assign(t=d["t"].dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%M:%SZ"))[["t", "o", "h", "l", "c", "v"]]
    p = tmp_path / "spy.csv"
    out.to_csv(p, index=False)
    import option_buyer_cost as obc
    monkeypatch.setattr(obc, "load_vix", lambda: vix.shift(1).fillna(18.0))
    monkeypatch.setattr(fs, "OUT", str(tmp_path / "r.md"))
    monkeypatch.setattr(fs, "FOMC", days[10:300:6])
    assert fs.main([str(p)]) == 0
    assert "أيام اجتماعات الفيدرالي" in (tmp_path / "r.md").read_text(encoding="utf-8")
    assert fs.main([str(tmp_path / "none.csv")]) == 0
