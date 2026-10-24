"""اختبارات المحاكي الموحّد: المزايا بلا نظرة للمستقبل، المعايرة، الضوابط، واكتشاف إشارة مزروعة. بيانات مصطنعة."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import option_buyer_cost as obc  # noqa: E402
import strategy_sim as sim  # noqa: E402


def make(n=500, vol=0.18, plant=0.0, seed=1):
    """شموع 78 يومياً؛ plant: بعد أول 30 دقيقة تتحرك الشموع 12..23 باتجاه حركة أول 30 دقيقة بمقدار plant."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2021-01-04", periods=n)
    std = vol / np.sqrt(252 * 78)
    O, C, p = [], [], 700.0
    for _ in days:
        ret = rng.normal(0, std, 78)
        first = np.sign(ret[:6].sum())
        ret[12:24] += plant * first / 12
        c = p * np.cumprod(1 + ret)
        O.append(np.r_[p, c[:-1]])
        C.append(c)
        p = float(c[-1])
    o, c = np.concatenate(O), np.concatenate(C)
    base = pd.DatetimeIndex(days).tz_localize("America/New_York") + pd.Timedelta(hours=9, minutes=30)
    t = pd.DatetimeIndex([b + pd.Timedelta(minutes=5 * i) for b in base for i in range(78)])
    d = pd.DataFrame({"t": t, "o": o, "h": np.maximum(o, c), "l": np.minimum(o, c), "c": c, "v": 1e5})
    return d, pd.Series(18.0, index=pd.DatetimeIndex(days))


def prepared(**kw):
    d, vix = make(**kw)
    F = sim.features(d)
    S = obc.samples(d).merge(F, on=["day", "i"], how="inner").reset_index(drop=True)
    return d, vix, S


def test_features_use_only_information_up_to_the_entry_bar():
    d, vix, S = prepared(n=30)
    g = d[d["t"].dt.date == S["day"].iloc[40].date()]
    i = int(S["i"].iloc[40])
    prev = d[d["t"].dt.date < S["day"].iloc[40].date()].iloc[-1]
    assert S["gap"].iloc[40] == pytest.approx(g["o"].iloc[0] / prev["c"] - 1)
    assert S["r_first"].iloc[40] == pytest.approx(g["c"].iloc[5] / g["o"].iloc[0] - 1)
    base = g["c"].iloc[i - 12] if i >= 12 else g["o"].iloc[0]
    assert S["r_prev60"].iloc[40] == pytest.approx(g["c"].iloc[i] / base - 1)
    d2 = d.copy()
    d2.loc[d2["t"] > g["t"].iloc[i], "c"] *= 1.2                            # تغيير المستقبل لا يغيّر المزايا
    S2 = obc.samples(d2).merge(sim.features(d2), on=["day", "i"], how="inner")
    assert S2["r_prev60"].iloc[40] == pytest.approx(S["r_prev60"].iloc[40])


def test_calibration_hits_the_target_random_mean():
    d, vix, S = prepared(n=300)
    for dte, target in ((0, -0.036), (1, -0.03)):
        m, got = sim.calibrate_mult(S, vix, dte, target)
        assert abs(got - target) < 0.004 and 0.3 <= m <= 1.3


def test_random_control_costs_about_the_target_and_always_call_is_not_special():
    d, vix, S = prepared(n=600, seed=2)
    m, _ = sim.calibrate_mult(S, vix, 1, -0.03)
    call, put, up, days = sim.option_returns(S, vix, 1, m)
    rnd = sim.run_signal(S, sim.make_random(3), call, put, up, days)["all"]
    assert abs(rnd["mean"] - (-0.03)) < 0.015 and rnd["n"] == len(S)
    ac = sim.run_signal(S, sim.sig_always_call, call, put, up, days)["all"]
    assert ac["mean"] < 0.03 and abs(ac["hit"] - 0.5) < 0.05


def test_planted_signal_is_detected_and_noise_signals_are_not_profitable():
    d, vix, S = prepared(n=800, plant=0.006, seed=4)
    m, _ = sim.calibrate_mult(S[S["i"] == 11].reset_index(drop=True), vix, 1, -0.03)
    S = S[S["i"] == 11].reset_index(drop=True)
    call, put, up, days = sim.option_returns(S, vix, 1, m)
    r = sim.run_signal(S, sim.sig_first30_follow, call, put, up, days)
    assert r["second"]["hit"] > 0.6 and r["second"]["mean"] > 0 and sim.verdict(r["second"]) == "رابحة بعد الكلفة"
    d, vix, S = prepared(n=800, plant=0.0, seed=5)
    S = S[S["i"] == 11].reset_index(drop=True)
    m, _ = sim.calibrate_mult(S, vix, 1, -0.03)
    call, put, up, days = sim.option_returns(S, vix, 1, m)
    noise = sim.run_signal(S, sim.sig_first30_follow, call, put, up, days)
    assert sim.verdict(noise["second"]) != "رابحة بعد الكلفة"


def test_gap_signal_skips_small_gaps_and_summaries_handle_empty():
    f = pd.DataFrame({"gap": [0.01, -0.01, 0.001, 0.0]})
    assert list(sim.sig_gap_follow(f)) == [1.0, -1.0, 0.0, 0.0]
    assert sim.summarize_trades([], [], []) is None
    assert sim.verdict(None) == "لا حكم بعد (عينة قليلة)"
    assert sim.erosion(None) is None


def test_load_targets_prefers_real_calibration_file(tmp_path):
    p = tmp_path / "wl.json"
    p.write_text('{"ATM 0DTE": {"random": -0.05}, "ATM 1DTE": {"random": -0.02}}', encoding="utf-8")
    assert sim.load_targets(str(p)) == {0: -0.05, 1: -0.02}
    assert sim.load_targets(str(tmp_path / "none.json")) == sim.TARGET_DEFAULT


def test_main_runs_end_to_end_on_a_synthetic_file(tmp_path, monkeypatch):
    d, vix = make(n=400, plant=0.004)
    out = d.assign(t=d["t"].dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%M:%SZ"))[["t", "o", "h", "l", "c", "v"]]
    p = tmp_path / "spy.csv"
    out.to_csv(p, index=False)
    monkeypatch.setattr(obc, "load_vix", lambda: vix.shift(1).fillna(18.0))
    monkeypatch.setattr(sim, "OUT", str(tmp_path / "r.md"))
    monkeypatch.setattr(sim, "SPLIT", "2021-09-01")
    monkeypatch.setattr(sim, "WL", str(tmp_path / "none.json"))
    assert sim.main([str(p)]) == 0
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "عشوائي (ضابط)" in text and "تتبّع الفجوة" in text and "ATM 0DTE" in text and "ATM 1DTE" in text and "الثاني" in text
    assert sim.main([str(tmp_path / "none.csv")]) == 0
