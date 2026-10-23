"""اختبارات دراسة «ثمن مشتري الخيارات»: تسعير بلاك-شولز، نسبة التعادل، وعائد المشتري العشوائي بين تذبذب محقق أقل/مساوٍ/أعلى من الضمني."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import option_buyer_cost as ob_cost  # noqa: E402


def test_black_scholes_put_call_parity_and_atm_value():
    S, K, T, sig = 700.0, 700.0, 30 / (252 * 390), 0.2
    c, p = ob_cost.bs(S, K, T, sig, True), ob_cost.bs(S, K, T, sig, False)
    assert abs((c - p) - (S - K)) < 1e-8
    assert abs(c - 0.4 * S * sig * np.sqrt(T)) / c < 0.02            # ATM ≈ 0.4·S·σ·√T
    assert ob_cost.bs(700, 720, T, sig, True) < c


def test_breakeven_accuracy_formula():
    assert abs(ob_cost.breakeven(0.5, -0.5) - 0.5) < 1e-12
    assert abs(ob_cost.breakeven(0.3, -0.6) - 0.6 / 0.9) < 1e-12
    assert ob_cost.breakeven(-0.1, -0.2) is None or ob_cost.breakeven(-0.1, -0.2) > 1


def make_days(realized_vol, iv, ndays=400, seed=1):
    """شموع 78 يومياً بتذبذب محقق سنوي realized_vol بزمن التداول؛ وVIX ثابت = iv×100."""
    rng = np.random.default_rng(seed)
    rows, p = [], 700.0
    days = pd.bdate_range("2025-01-02", periods=ndays)
    std5 = realized_vol / np.sqrt(252 * 78)
    for d in days:
        c = p * np.cumprod(1 + rng.normal(0, std5, 78))
        t = [(pd.Timestamp(d).tz_localize("America/New_York") + pd.Timedelta(hours=9, minutes=30 + 5 * i)) for i in range(78)]
        rows += [{"t": t[i], "o": c[i], "h": c[i], "l": c[i], "c": c[i], "v": 1e5} for i in range(78)]
        p = float(c[-1])
    d = pd.DataFrame(rows)
    vix = pd.Series(iv * 100, index=pd.DatetimeIndex(days))
    return d, vix


def random_mean(realized, iv, strike="ATM", dte=0):
    d, vix = make_days(realized, iv)
    S = ob_cost.samples(d)
    off, h = ob_cost.STRIKES[strike]
    res, up, days = ob_cost.returns(S, vix, 1.0, off, h, dte, 12)
    return ob_cost.summarize(res, up, days)


def test_buyer_loses_about_the_spread_when_realized_equals_implied():
    s = random_mean(0.18, 0.18)
    assert -0.10 < s["random"] < 0.0 and s["ci"][0] < s["random"] < s["ci"][1]
    assert 0.35 < s["be"] < 0.75                                             # نسبة تعادل قرب نصف: لا ميزة ولا عيب كبير


def test_buyer_wins_when_realized_vol_exceeds_implied_and_loses_more_when_it_is_lower():
    high = random_mean(0.30, 0.18)["random"]
    low = random_mean(0.09, 0.18)["random"]
    mid = random_mean(0.18, 0.18)["random"]
    assert high > mid > low and high > 0.0 and low < -0.04


def test_samples_skip_half_days_and_use_the_four_entry_times():
    d, _ = make_days(0.18, 0.18, ndays=5)
    S = ob_cost.samples(d)
    assert len(S) == 5 * 4 and set(S["i"]) == set(ob_cost.ENTRIES)
    assert (S["mins_to_close"] == [16 * 60 - (9 * 60 + 35 + 5 * i) for i in S["i"]]).all()


def test_report_and_main_run_on_a_synthetic_file(tmp_path, monkeypatch):
    d, vix = make_days(0.18, 0.18, ndays=60)
    p = tmp_path / "spy.csv"
    out = d.assign(t=d["t"].dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%M:%SZ"))[["t", "o", "h", "l", "c", "v"]]
    out.to_csv(p, index=False)
    monkeypatch.setattr(ob_cost, "load_vix", lambda: vix.shift(1).fillna(18.0))
    monkeypatch.setattr(ob_cost, "OUT", str(tmp_path / "r.md"))
    assert ob_cost.main([str(p)]) == 0
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "نسبة التعادل" in text and "تذبذب = VIX × 1.0" in text and "ATM" in text and "OTM 1%" in text
    assert ob_cost.main([str(tmp_path / "none.csv")]) == 0


def test_calibration_compares_real_prices_with_the_theory_when_the_file_exists(tmp_path):
    rows = []
    for i in range(120):
        rows.append({"day": f"2026-09-{1 + i % 25:02d}", "run_m": 600, "edge": 5, "role": "bot", "kind": "atm1", "exit": "t60", "real": -0.03, "theo": -0.07})
        rows.append({"day": f"2026-09-{1 + i % 25:02d}", "run_m": 600, "edge": 5, "role": "bot", "kind": "otm0", "exit": "t60", "real": -0.26, "theo": -0.45})
    p = tmp_path / "t.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    L = chr(10).join(ob_cost.calibration_lines({"ATM 1DTE": -0.064, "OTM 1% 0DTE": -0.371}, str(p)))
    assert "معايرة" in L and "-3.0%" in L and "-6.4%" in L and "-26.0%" in L and "-37.1%" in L and "حدّ أعلى" in L
    assert ob_cost.calibration_lines({}, str(tmp_path / "none.csv")) == []
    assert ob_cost.calibration_lines({"ATM 1DTE": -0.06}, str(p)) != []                      # يكفي مفتاح واحد
