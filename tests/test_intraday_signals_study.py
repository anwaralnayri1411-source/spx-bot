"""اختبارات دراسة الفجوة وآخر 30 دقيقة: بناء الأيام، استبعاد أيام التوزيع، الحكم، ومعايرة الصفر على مشي عشوائي. بيانات مصطنعة."""
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import intraday_signals_study as iss  # noqa: E402


def make(n_days=900, gap_drift=0.0, last_drift=0.0, seed=1, start="2019-01-02"):
    """شموع 5 دقائق عشوائية؛ gap_drift: يتبع اتجاه الفجوة 9:35→10:30 بهذا المقدار؛ last_drift: آخر 30 دقيقة تتبع إشارة أول 30."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start, periods=n_days)
    O, C, p = [], [], 400.0
    for _ in days:
        gap = rng.choice([-1, 0, 1], p=[0.15, 0.7, 0.15]) * 0.006
        ret = rng.normal(0, 0.0006, 78)
        o0 = p * (1 + gap)
        ret[1:12] += gap_drift * np.sign(gap) / 11
        first = ret[:6].sum()
        ret[72:78] += last_drift * np.sign(first) / 6
        c = o0 * np.cumprod(1 + ret)
        O.append(np.r_[o0, c[:-1]])
        C.append(c)
        p = float(c[-1])
    o, c = np.concatenate(O), np.concatenate(C)
    base = pd.DatetimeIndex(days).tz_localize("America/New_York") + pd.Timedelta(hours=9, minutes=30)
    t = pd.DatetimeIndex([b + pd.Timedelta(minutes=5 * i) for b in base for i in range(78)])
    return pd.DataFrame({"t": t, "o": o, "h": np.maximum(o, c) * 1.0001, "l": np.minimum(o, c) * 0.9999, "c": c, "v": 1e5})


def test_third_friday_and_ex_div_detection():
    assert iss.is_ex_div_day(date(2026, 12, 18)) and iss.is_ex_div_day(date(2026, 9, 18))
    assert not iss.is_ex_div_day(date(2026, 12, 11)) and not iss.is_ex_div_day(date(2026, 10, 16))
    assert not iss.is_ex_div_day(date(2026, 12, 25))


def test_build_days_gap_uses_previous_close_and_skips_ex_div_and_first_day():
    d = make(60)
    X = iss.build_days(d)
    assert len(X) >= 55 and X["day"].min() > pd.Timestamp("2019-01-02")
    day = X.iloc[5]
    g = d[d["t"].dt.date == day["day"].date()]
    prev = d[d["t"].dt.date < day["day"].date()].iloc[-1]
    assert abs(day["gap"] - (g["o"].iloc[0] / prev["c"] - 1)) < 1e-12
    assert abs(day["r_1030"] - (g["c"].iloc[11] / g["c"].iloc[0] - 1)) < 1e-12
    assert abs(day["r_last30"] - (g["c"].iloc[-1] / g["c"].iloc[71] - 1)) < 1e-12


def test_edge_ignores_zero_signals_and_boot_needs_20():
    e = iss.edge([1, -1, 0, 1], [0.01, -0.02, 5.0, -0.01])
    assert e["n"] == 3 and e["mean"] == pytest.approx((0.01 + 0.02 - 0.01) / 3) and e["ci"] is None
    assert iss.edge([0, 0], [1.0, 2.0]) is None


def test_judge_waits_then_confirms_rejects_or_reverses():
    rng = np.random.default_rng(3)
    big = lambda mu: iss.edge(np.ones(400), rng.normal(mu, 0.01, 400))
    assert "لا حكم بعد" in iss.judge("X", big(0.002), 500)
    assert "تحققت" in iss.judge("X", big(0.002), 150)
    assert "لم تتحقق" in iss.judge("X", big(0.0), 150)
    assert "عكسية" in iss.judge("X", big(-0.002), 150)


def test_study_detects_planted_effects_and_stays_quiet_on_pure_noise(monkeypatch):
    monkeypatch.setattr(iss, "SPLIT", "2020-06-01")
    X = iss.build_days(make(1000, gap_drift=0.003, last_drift=0.002, seed=4))
    text = "\n".join(iss.report(X, "مصطنع"))
    assert "H-GAP: تحققت" in text and "H-LAST30: تحققت" in text
    Y = iss.build_days(make(1000, seed=5))
    quiet = "\n".join(iss.report(Y, "مصطنع"))
    assert "H-GAP: تحققت" not in quiet and "H-LAST30: تحققت" not in quiet


def test_null_calibration_has_few_false_positives(monkeypatch):
    monkeypatch.setattr(iss, "SPLIT", "2020-06-01")
    monkeypatch.setattr(iss, "MIN_GAP_EVENTS", 50)
    monkeypatch.setattr(iss, "MIN_LAST30_DAYS", 100)
    hits = 0
    for seed in range(6):
        X = iss.build_days(make(800, seed=100 + seed))
        t = "\n".join(iss.report(X, "x"))
        hits += ("H-GAP: تحققت" in t) + ("H-LAST30: تحققت" in t) + ("H-GAP: عكسية" in t) + ("H-LAST30: عكسية" in t)
    assert hits <= 1                                              # 12 اختباراً بمستوى 5%: ≤1 متوقع تقريباً


def test_main_runs_on_file_and_handles_missing(tmp_path, monkeypatch):
    d = make(300)
    out = d.assign(t=d["t"].dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%M:%SZ"))[["t", "o", "h", "l", "c", "v"]]
    p = tmp_path / "spy.csv"
    out.to_csv(p, index=False)
    monkeypatch.setattr(iss, "OUT", str(tmp_path / "r.md"))
    assert iss.main([str(p)]) == 0
    assert "النصف الثاني" in (tmp_path / "r.md").read_text(encoding="utf-8")
    assert iss.main([str(tmp_path / "none.csv")]) == 0
