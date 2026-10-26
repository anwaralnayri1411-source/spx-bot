"""اختبارات استراتيجية سحب السيولة + كسر الهيكل + FVG: كشف الإعداد، المحاكاة، الانعكاس للبيع، النسخة الأثرية، والحكم. بيانات مصطنعة."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import sweep_fvg_study as sw  # noqa: E402

LEVEL = 100.0


def day_arrays(extra=None, n=78):
    """يوم صاعد مصطنع: سحب عند الشمعة 3، كسر وFVG عند 5، ارتداد للفجوة عند 6، هدف عند 7، ثم هدوء عند 101."""
    bars = [(100.2, 100.4, 100.1, 100.3), (100.3, 100.35, 100.2, 100.25), (100.25, 100.3, 100.15, 100.2),
            (100.2, 100.25, 99.8, 99.9), (99.9, 100.0, 99.85, 99.95), (100.3, 100.7, 100.3, 100.6),
            (100.6, 100.65, 100.3, 100.5), (100.5, 101.1, 100.5, 101.0)]
    bars += [(101.0, 101.05, 100.95, 101.0)] * (n - len(bars))
    if extra:
        for idx, b in extra.items():
            bars[idx] = b
    a = np.array(bars, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def test_setup_is_found_with_entry_at_gap_top_and_stop_at_the_sweep_low():
    o, h, l, c = day_arrays()
    s = sw.bull_setup(o, h, l, c, LEVEL)
    assert s["k"] == 6 and s["entry"] == pytest.approx(100.3) and s["stop"] == pytest.approx(99.8) and s["risk"] == pytest.approx(0.5)


def test_no_setup_when_no_sweep_no_bos_gap_missing_or_gap_invalidated_or_risk_too_large():
    o, h, l, c = day_arrays()
    assert sw.bull_setup(o, h, l, c, 99.0) is None                                            # لا سحب تحت المستوى
    o2, h2, l2, c2 = day_arrays({5: (100.3, 100.7, 100.2, 100.6)})                            # بلا فجوة: أدناها 100.2 تحت 100.25
    assert sw.bull_setup(o2, h2, l2, c2, LEVEL) is None
    o3, h3, l3, c3 = day_arrays({6: (100.6, 100.65, 100.0, 100.1)})                           # إغلاق تحت الحد السفلي للفجوة 100.25
    assert sw.bull_setup(o3, h3, l3, c3, LEVEL) is None
    o4, h4, l4, c4 = day_arrays({3: (100.2, 100.25, 95.0, 99.9)})                             # وقف بعيد: خطر > 1%
    assert sw.bull_setup(o4, h4, l4, c4, LEVEL) is None
    o5, h5, l5, c5 = day_arrays({5: (100.3, 100.7, 100.3, 100.3)})                            # إغلاق تحت أعلى ما قبل السحب (100.4): لا كسر هيكل
    assert sw.bull_setup(o5, h5, l5, c5, LEVEL) is None


def test_simulation_target_stop_same_bar_stop_first_and_hold_to_close():
    o, h, l, c = day_arrays()
    s = sw.bull_setup(o, h, l, c, LEVEL)
    cost = 2 * sw.COST * 100.3 / 0.5
    assert sw.simulate(h, l, c, s, s["entry"] + 1.5 * s["risk"]) == pytest.approx(1.5 - cost)
    o2, h2, l2, c2 = day_arrays({7: (100.5, 101.1, 99.7, 101.0)})                              # الوقف والهدف في الشمعة نفسها: الوقف أولاً
    s2 = sw.bull_setup(o2, h2, l2, c2, LEVEL)
    assert sw.simulate(h2, l2, c2, s2, 101.05) == pytest.approx(-1.0 - cost)
    assert sw.simulate(h, l, c, s, hold_to_close=True) == pytest.approx((101.0 - 100.3) / 0.5 - cost)
    # الوقف في شمعة الدخول نفسها
    o3, h3, l3, c3 = day_arrays({6: (100.6, 100.65, 99.7, 100.5)})
    s3 = sw.bull_setup(o3, h3, l3, c3, LEVEL)
    assert s3 is not None and sw.simulate(h3, l3, c3, s3, 101.0) == pytest.approx(-1.0 - cost, abs=0.02)


def test_bearish_day_is_the_exact_mirror():
    o, h, l, c = day_arrays()
    mirror = lambda x: 200.0 - x
    a = sw.day_trade(o, h, l, c, LEVEL, +1, "t15")
    b = sw.day_trade(mirror(o), mirror(l), mirror(h), mirror(c), mirror(LEVEL), -1, "t15")
    assert a is not None and b == pytest.approx(a, abs=0.02)                                   # الكلفة تختلف قليلاً بقيمة السعر
    assert sw.day_trade(o, h, l, c, LEVEL, 0, "t15") is None


def test_events_exec_uses_yesterday_and_oracle_uses_todays_close_as_target():
    o, h, l, c = day_arrays()
    day = pd.Timestamp("2026-01-05")
    ev = sw.day_events(day, o, h, l, c, prev=(99.0, 100.0))                                    # أمس صاعد
    df = pd.DataFrame(ev)
    assert {"exec", "oracle"} <= set(df["version"]) and (df[df["version"] == "exec"]["kind"] == "real").any()
    ora = df[df["version"] == "oracle"].iloc[0]
    assert ora["R"] > 0 and ora["mode"] == "line"                                               # هدف إغلاق اليوم 101 بلغته الشمعة 7
    ev2 = pd.DataFrame(sw.day_events(day, o, h, l, c, prev=(100.0, 99.0)))                     # أمس هابط: لا صفقة exec شراء
    assert len(ev2[(ev2["version"] == "exec") & (ev2["kind"] == "real")]) == 0
    none_prev = pd.DataFrame(sw.day_events(day, o, h, l, c, prev=None), columns=["day", "version", "kind", "mode", "R"])
    assert len(none_prev[none_prev["version"] == "exec"]) == 0


def make_events(real_mean, plac_mean, n_days=400, seed=1):
    rng = np.random.default_rng(seed)
    days = list(pd.bdate_range("2023-01-02", periods=n_days))
    rows = []
    for d in days:
        if rng.random() < 0.45:
            rows.append({"day": d, "version": "exec", "kind": "real", "mode": "t15", "R": rng.normal(real_mean, 1.0)})
        for _ in range(2):
            if rng.random() < 0.45:
                rows.append({"day": d, "version": "exec", "kind": "placebo", "mode": "t15", "R": rng.normal(plac_mean, 1.0)})
    return pd.DataFrame(rows), days


def test_verdict_waits_confirms_rejects_and_reverses():
    ev, days = make_events(0.5, 0.0)
    assert "تحققت" in sw.verdict(ev, days)
    ev0, days0 = make_events(0.0, 0.0, seed=2)
    assert "لم تتحقق" in sw.verdict(ev0, days0)
    evr, daysr = make_events(-0.5, 0.0, seed=3)
    assert "عكسية" in sw.verdict(evr, daysr)
    few, dfew = make_events(0.5, 0.0, n_days=100)
    assert "لا حكم بعد" in sw.verdict(few, dfew)


def synthetic_file(tmp_path, n_days=60, seed=1):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2026-01-05", periods=n_days)
    O, C, p = [], [], 400.0
    for _ in days:
        c = p * np.cumprod(1 + rng.normal(0, 0.0007, 78))
        O.append(np.r_[p, c[:-1]])
        C.append(c)
        p = float(c[-1])
    o, c = np.concatenate(O), np.concatenate(C)
    base = pd.DatetimeIndex(days).tz_localize("America/New_York") + pd.Timedelta(hours=9, minutes=30)
    t = pd.DatetimeIndex([b + pd.Timedelta(minutes=5 * i) for b in base for i in range(78)])
    df = pd.DataFrame({"t": t.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"), "o": o, "h": np.maximum(o, c) * 1.0002, "l": np.minimum(o, c) * 0.9998, "c": c, "v": 1e5})
    p_ = tmp_path / "spy.csv"
    df.to_csv(p_, index=False)
    return str(p_)


def test_main_runs_on_a_random_file_and_handles_missing(tmp_path, monkeypatch):
    path = synthetic_file(tmp_path)
    monkeypatch.setattr(sw, "OUT", str(tmp_path / "r.md"))
    monkeypatch.setattr(sw, "SPLIT", "2026-02-15")
    assert sw.main([path]) == 0
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "الحكم" in text and "H-SWEEP" in text and "غير قابل للتنفيذ" in text
    assert sw.main([str(tmp_path / "none.csv")]) == 0


def test_null_calibration_on_random_walks_rarely_confirms(tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "MIN_REAL", 40)
    hits = 0
    for seed in range(3):
        path = synthetic_file(tmp_path, n_days=900, seed=100 + seed)
        d = sw.ls.load(path)
        ev = sw.run(d)
        days = sorted({pd.Timestamp(x) for x in d["t"].dt.date.unique()})
        second = ev[ev["day"] >= days[len(days) // 2]]
        d2 = [x for x in days if x >= days[len(days) // 2]]
        hits += "تحققت" in sw.verdict(second, d2)
    assert hits <= 1


def prev_day_arrays():
    """يوم لاختبار H-PREV: مستوى السحب = افتتاح أمس 100، هدف = إغلاق أمس 101؛ سحب عند 3، كسر+فجوة عند 5، تثبيت عند 6، ارتداد للفجوة عند 7، هدف عند 8."""
    bars = [(100.2, 100.4, 100.1, 100.3), (100.3, 100.35, 100.2, 100.25), (100.25, 100.3, 100.15, 100.2),
            (100.2, 100.25, 99.8, 99.9), (99.9, 100.0, 99.85, 99.95), (100.3, 100.7, 100.3, 100.6),
            (100.6, 100.7, 100.5, 100.65), (100.65, 100.7, 100.3, 100.5), (100.5, 101.1, 100.5, 101.0)]
    bars += [(101.0, 101.05, 100.95, 101.0)] * (78 - len(bars))
    a = np.array(bars, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def test_confirmation_delays_entry_and_start_above_rejects_days_that_open_below_the_line():
    o, h, l, c = prev_day_arrays()
    s = sw.bull_setup(o, h, l, c, 100.0, confirm=2, start_above=True)
    assert s is not None and s["k"] == 7 and s["entry"] == pytest.approx(100.3)
    c2 = c.copy()
    c2[6] = 99.9                                                                     # الشمعة التالية لا تغلق فوق الخط: لا تثبيت
    assert sw.bull_setup(o, h, l, c2, 100.0, confirm=2, start_above=True) is None
    o3 = o.copy()
    o3[0] = 99.5                                                                     # اليوم يبدأ تحت الخط
    assert sw.bull_setup(o3, h, l, c, 100.0, confirm=2, start_above=True) is None
    assert sw.bull_setup(o3, h, l, c, 100.0, confirm=2, start_above=False) is not None


def test_prev_events_use_yesterdays_open_as_level_and_close_as_target():
    o, h, l, c = prev_day_arrays()
    day = pd.Timestamp("2026-01-05")
    ev = pd.DataFrame(sw.day_events_prev(day, o, h, l, c, prev=(100.0, 101.0)))
    real = ev[ev["kind"] == "real"]
    cost = 2 * sw.COST * 100.3 / 0.5
    assert len(real) == 1 and real.iloc[0]["version"] == "prev" and real.iloc[0]["R"] == pytest.approx((101.0 - 100.3) / 0.5 - cost)
    assert sw.day_events_prev(day, o, h, l, c, prev=(101.0, 100.0)) == [] or all(e["kind"] != "real" for e in sw.day_events_prev(day, o, h, l, c, prev=(101.0, 100.0)))
    assert sw.day_events_prev(day, o, h, l, c, prev=None) == []
    ev2 = sw.day_events_prev(day, o, h, l, c, prev=(100.0, 100.2))                  # هدف قريب جداً: R صغير لكنه موجب
    assert all(e["R"] < 3 for e in ev2)
