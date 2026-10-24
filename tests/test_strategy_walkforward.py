"""اختبارات المهارة المزدوجة والاختيار المتدرج: يكتشف مهارة حقيقية ولا يخترع مهارة من الضجيج. بيانات مصطنعة."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import option_buyer_cost as obc  # noqa: E402
import strategy_sim as sim  # noqa: E402
import strategy_walkforward as wf  # noqa: E402
from test_strategy_sim import make  # noqa: E402


def frames_for(plant, seed, n=1300):
    d, vix = make(n=n, plant=plant, seed=seed)
    F = sim.features(d)
    S = obc.samples(d).merge(F, on=["day", "i"], how="inner")
    S = S[S["i"] == 11].reset_index(drop=True)
    m, _ = sim.calibrate_mult(S, vix, 1, -0.03)
    call, put, up, days = sim.option_returns(S, vix, 1, m)
    return {n_: wf.skill_frame(S, sim.SIGNALS[n_], call, put, up, days) for n_ in wf.CANDIDATES}


def test_skill_is_return_minus_the_coin_flip_baseline():
    F = pd.DataFrame({"gap": [0.01, -0.01, 0.0]})
    call, put = np.array([0.2, 0.2, 0.2]), np.array([-0.1, -0.1, -0.1])
    out = wf.skill_frame(F, sim.sig_gap_follow, call, put, np.ones(3, bool), pd.date_range("2024-01-01", periods=3))
    assert len(out) == 2 and out["skill"].tolist() == pytest.approx([0.2 - 0.05, -0.1 - 0.05])


def test_planted_skill_is_found_and_walk_forward_picks_it():
    fr = frames_for(plant=0.006, seed=1)
    s = wf.skill_stat(fr["تتبّع أول 30 دقيقة"])
    assert s["mean"] > 0.02 and s["ci"][0] > 0 and wf.verdict(s).startswith("تتفوق")
    rows, chosen, avg = wf.walk_forward(fr)
    assert rows and all(r["chosen"] in ("تتبّع أول 30 دقيقة", "اتفاق الزخم وأول 30") for r in rows if r["test_n"])
    sc = wf.skill_stat(chosen)
    assert sc["ci"][0] > 0 and sc["mean"] > wf.skill_stat(avg)["mean"]


def test_pure_noise_gives_no_skill_after_selection():
    hits = 0
    for seed in range(4):
        fr = frames_for(plant=0.0, seed=10 + seed, n=1100)
        _, chosen, _ = wf.walk_forward(fr)
        s = wf.skill_stat(chosen)
        if s and s["ci"] and s["ci"][0] > 0:
            hits += 1
    assert hits <= 1


def test_walk_forward_uses_only_past_years_for_selection():
    days_a = pd.to_datetime(["2020-03-02"] * 300 + ["2021-03-02"] * 300 + ["2022-03-02"] * 300 + ["2023-03-02"] * 10)
    good_then_bad = pd.DataFrame({"day": days_a, "skill": [0.05] * 600 + [-0.05] * 300 + [0.0] * 10})
    flat = pd.DataFrame({"day": days_a, "skill": [-0.01] * 910})
    rows, chosen, _ = wf.walk_forward({"A": good_then_bad, "B": flat}, min_trades=200, min_years=2)
    r2022 = next(r for r in rows if r["year"] == 2022)
    assert r2022["chosen"] == "A" and r2022["train_skill"] == pytest.approx(0.05) and r2022["test_skill"] == pytest.approx(-0.05)


def test_no_candidate_with_positive_training_skill_is_reported_not_forced():
    days = pd.to_datetime(["2020-03-02"] * 300 + ["2021-03-02"] * 300 + ["2022-03-02"] * 300)
    neg = pd.DataFrame({"day": days, "skill": [-0.02] * 900})
    rows, chosen, _ = wf.walk_forward({"A": neg}, min_trades=100, min_years=2)
    assert rows[0]["chosen"] == "لا مرشح موجب" and len(chosen) == 0
    assert wf.skill_stat(chosen) is None


def test_main_runs_end_to_end_and_handles_missing_file(tmp_path, monkeypatch):
    d, vix = make(n=900, plant=0.004)
    out = d.assign(t=d["t"].dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%M:%SZ"))[["t", "o", "h", "l", "c", "v"]]
    p = tmp_path / "spy.csv"
    out.to_csv(p, index=False)
    monkeypatch.setattr(obc, "load_vix", lambda: vix.shift(1).fillna(18.0))
    monkeypatch.setattr(wf, "OUT", str(tmp_path / "r.md"))
    monkeypatch.setattr(sim, "WL", str(tmp_path / "none.json"))
    assert wf.main([str(p)]) == 0
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "الاختيار المتدرج" in text and "ATM 0DTE" in text and "ATM 1DTE" in text and "المهارة المزدوجة" in text
    assert wf.main([str(tmp_path / "none.csv")]) == 0
