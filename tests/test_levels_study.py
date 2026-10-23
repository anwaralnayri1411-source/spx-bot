"""اختبارات دراسة المستويات واختراق أول 30 دقيقة: اللمس، محاكاة الارتداد والاختراق، المستويات، المرجع الوهمي، والحكم. بيانات مصطنعة."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import levels_study as ls  # noqa: E402


def arr(*x):
    return np.array(x, dtype=float)


def test_first_touch_detects_resistance_from_below_and_support_from_above_only_once():
    c = arr(99.0, 99.5, 100.4, 99.0)
    h = arr(99.2, 99.8, 100.6, 99.5)
    l = arr(98.8, 99.2, 99.9, 98.9)
    assert ls.first_touch(c, h, l, 100.0) == (2, -1)                     # صعد من 99.5 إلى لمس 100: مقاومة
    assert ls.first_touch(arr(101.0, 100.5, 100.2), arr(101.1, 100.6, 100.4), arr(100.8, 100.1, 99.9), 100.0) == (2, 1)
    assert ls.first_touch(arr(99.0, 99.1), arr(99.2, 99.3), arr(98.9, 99.0), 100.0) is None
    assert ls.first_touch(c, h, l, 100.0, start=3) == (3, 1)             # start يتجاوز اللمس المبكر ويجد النزول عبر المستوى
    assert ls.first_touch(c, h, l, 100.0, start=4) is None


def test_fade_hits_target_stop_and_time_exit_with_costs():
    level, b = 100.0, ls.B
    # مقاومة، ارتداد هابط يبلغ الهدف (−1.5 × 0.10%): الوقف عند 100.10، والهدف عند 99.85
    h = arr(100.0, 100.0, 99.95, 99.9)
    l = arr(99.5, 99.9, 99.7, 99.80)
    c = arr(99.6, 99.95, 99.8, 99.85)
    r = ls.sim_fade(h, l, c, 1, level, -1)
    assert abs(r - (ls.R - 2 * ls.COST * level / (b * level))) < 1e-9
    # الوقف: شمعة تتجاوز 100.10
    h2 = arr(100.0, 100.0, 100.2, 100.0)
    r2 = ls.sim_fade(h2, arr(99.5, 99.9, 99.9, 99.9), arr(99.6, 99.95, 100.1, 100.0), 1, level, -1)
    assert r2 < -1.0 and r2 > -1.1
    # خروج زمني: لا هدف ولا وقف؛ العائد جزئي
    hh, ll, cc = arr(*[100.0] * 20), arr(*[99.95] * 20), arr(*[99.98] * 20)
    r3 = ls.sim_fade(hh, ll, cc, 1, level, -1, hold=5)
    assert 0 < r3 < 1


def test_same_bar_stop_and_target_counts_as_stop_and_touch_bar_target_is_ignored():
    h = arr(100.0, 100.0, 100.2)
    l = arr(99.5, 99.9, 99.6)                 # الشمعة 2 تلمس الوقف (100.10) والهدف (99.85) معاً
    c = arr(99.6, 99.95, 99.9)
    assert ls.sim_fade(h, l, c, 1, 100.0, -1) < -1.0
    # شمعة اللمس نفسها تحوي قاعاً بلغ الهدف: يُتجاهل لأننا لا نعرف الترتيب
    h2, l2, c2 = arr(100.0, 100.05), arr(99.5, 99.7), arr(99.6, 99.8)
    r = ls.sim_fade(h2, l2, c2, 1, 100.0, -1, hold=0)
    assert r != ls.R - 2 * ls.COST / ls.B


def test_break_enters_on_close_beyond_the_level_and_returns_none_without_one():
    h = arr(99.5, 100.3, 100.9, 101.0, 101.2)
    l = arr(99.0, 99.9, 100.2, 100.4, 100.7)
    c = arr(99.4, 100.25, 100.7, 100.9, 101.1)
    r = ls.sim_break(h, l, c, 1, 100.0, -1)
    assert r is not None and r > 0
    assert ls.sim_break(arr(99.5, 99.9), arr(99.0, 99.4), arr(99.4, 99.6), 1, 100.0, -1) is None


def test_day_levels_contains_previous_day_levels_or_and_round_numbers_without_lookahead():
    n = 40
    o = np.full(n, 700.3)
    h = np.linspace(700.5, 703.0, n)
    l = np.linspace(699.8, 702.0, n)
    c = np.linspace(700.2, 702.5, n)
    lv = ls.day_levels((705.0, 695.0, 700.0), o, h, l, c)
    assert lv["PDH"] == [(705.0, 1)] and lv["PDL"] == [(695.0, 1)] and lv["PDC"] == [(700.0, 1)]
    assert lv["ORH"][0] == (float(h[:ls.OR_BARS].max()), ls.OR_BARS)
    assert lv["ORL"][0][1] == ls.OR_BARS                                        # لا يُستعمل قبل اكتمال أول 30 دقيقة
    assert all(abs(x - 700.3) / 700.3 <= 0.012 and x % 5 == 0 for x, _ in lv["ROUND5"]) and len(lv["ROUND5"]) >= 3
    assert ls.day_levels(None, o, h, l, c)["PDH"] == []


def synthetic_day(seed, n=78, level_bounce=True):
    rng = np.random.default_rng(seed)
    c = 700 + np.cumsum(rng.normal(0, 0.12, n))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) + rng.uniform(0, 0.1, n)
    l = np.minimum(o, c) - rng.uniform(0, 0.1, n)
    v = rng.uniform(1e5, 2e5, n)
    return o, h, l, c, v


def test_day_events_produce_real_and_placebo_events_with_valid_fields():
    ev = []
    for s in range(30):
        o, h, l, c, v = synthetic_day(s)
        ev += ls.day_events(f"2026-01-{1 + s:02d}", (float(h.max()) + 0.3, float(l.min()) - 0.3, float(c[-1])), o, h, l, c, v)
    df = pd.DataFrame(ev)
    assert set(df["kind"]) == {"real", "placebo"} and set(df["mode"]) == {"fade", "break"}
    assert df["R"].between(-3, 3.5).all() and df["family"].isin(ls.FAMILIES).all()


def test_orb_returns_three_results_per_breakout_day_and_none_otherwise():
    h = np.r_[np.full(6, 700.5), np.full(10, 701.5)]
    l = np.r_[np.full(6, 699.5), np.full(10, 700.9)]
    c = np.r_[np.full(6, 700.0), np.linspace(700.6, 702.2, 10)]
    o = np.r_[np.full(6, 700.0), np.linspace(700.4, 702.0, 10)]
    ev = ls.orb_events("2026-01-05", o, h, l, c)
    assert len(ev) == 3 and {e["mode"] for e in ev} >= {"breakout 1R"}
    flat = np.full(16, 700.0)
    assert ls.orb_events("d", flat, flat + 0.2, flat - 0.2, flat) == []
    assert -0.2 < ev[1]["R"] + ev[2]["R"] < 0                             # الارتداد = −الاختراق ناقص التكلفة مرتين


def test_bootstrap_intervals_need_days_and_the_verdict_demands_enough_events_and_a_positive_gap():
    few = pd.DataFrame({"day": ["a", "b"], "R": [0.1, 0.2]})
    assert ls.boot_mean_ci(few) is None
    rng = np.random.default_rng(1)
    rows = []
    for i in range(300):
        d = f"2026-02-{1 + i % 28:02d}-{i // 28}"
        for fam in ("PDH", "PDL"):
            rows.append({"day": d, "family": fam, "mode": "fade", "kind": "real", "R": 0.4 + rng.normal(0, 0.5)})
            rows.append({"day": d, "family": fam, "mode": "fade", "kind": "placebo", "R": rng.normal(0, 0.5)})
    test = pd.DataFrame(rows)
    assert "تحققت الفرضية" in ls.primary_verdict(test)
    test.loc[test["kind"] == "real", "R"] = rng.normal(0, 0.5, int((test["kind"] == "real").sum()))
    assert "لم تتحقق" in ls.primary_verdict(test)
    assert "لا حكم بعد" in ls.primary_verdict(test.head(20))


def test_report_and_main_work_on_a_small_synthetic_file(tmp_path, monkeypatch):
    rows = []
    for s in range(12):
        o, h, l, c, v = synthetic_day(s)
        day = pd.Timestamp("2026-03-02") + pd.Timedelta(days=s)
        if day.weekday() >= 5:
            continue
        for i in range(78):
            t = (day.tz_localize("America/New_York") + pd.Timedelta(hours=9, minutes=30 + 5 * i)).tz_convert("UTC")
            rows.append({"t": t.isoformat().replace("+00:00", "Z"), "o": o[i], "h": h[i], "l": l[i], "c": c[i], "v": v[i]})
    p = tmp_path / "spy.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    monkeypatch.setattr(ls, "OUT", str(tmp_path / "r.md"))
    assert ls.main([str(p)]) == 0
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "دراسة المستويات" in text and "الفرضية المسجّلة مسبقاً" in text and "اختراق أول 30 دقيقة (ORB)" in text
    assert ls.main([str(tmp_path / "missing.csv")]) == 0


def day_frame(day, half=False, n=78):
    t = [(pd.Timestamp(day).tz_localize("America/New_York") + pd.Timedelta(hours=9, minutes=30 + 5 * i)) for i in range(n)]
    vol = [1e6 if (t0.hour * 60 + t0.minute) < 780 or not half else 5e3 for t0 in t]
    rng = np.random.default_rng(len(day))
    c = 700 + np.cumsum(rng.normal(0, 0.1, n))
    return pd.DataFrame({"t": t, "o": c, "h": c + 0.1, "l": c - 0.1, "c": c, "v": vol})


def test_half_days_are_trimmed_at_1300_and_are_not_traded_but_still_define_yesterday():
    full = day_frame("2026-11-02")
    g, half = ls.trim_half_day(full)
    assert not half and len(g) == 78
    early = day_frame("2026-11-27", half=True)
    g2, half2 = ls.trim_half_day(early)
    assert half2 and len(g2) == 42 and (g2["t"].dt.hour * 60 + g2["t"].dt.minute < 780).all()
    d = pd.concat([day_frame("2026-11-25"), early, day_frame("2026-11-30")], ignore_index=True)
    ev, orb = ls.run(d)
    assert "2026-11-27" not in set(ev["day"]) and "2026-11-27" not in set(orb["day"])
    assert {"2026-11-30"} <= set(ev["day"])


def random_walk_events(seed, ndays=700):
    rng = np.random.default_rng(seed)
    ev, prev, p = [], None, 700.0
    for i in range(ndays):
        c = p * np.cumprod(1 + rng.normal(0, 0.0013, 78))
        o = np.r_[p, c[:-1]]
        h = np.maximum(o, c) * (1 + rng.uniform(0, 0.0004, 78))
        l = np.minimum(o, c) * (1 - rng.uniform(0, 0.0004, 78))
        ev += ls.day_events(f"d{i:04d}", prev, o, h, l, c, np.full(78, 1e5))
        prev, p = (float(h.max()), float(l.min()), float(c[-1])), float(c[-1])
    return pd.DataFrame(ev)


def test_null_calibration_a_random_walk_never_confirms_the_hypothesis():
    """لو كانت الدراسة منحازة لأي مستوى حقيقي مقابل الوهمي لظهر «تحققت» على بيانات بلا بنية. نتحقق على مشيين عشوائيين."""
    for seed in (11, 12):
        _, test = ls.split_halves(random_walk_events(seed))
        assert "تحققت الفرضية" not in ls.primary_verdict(test)
