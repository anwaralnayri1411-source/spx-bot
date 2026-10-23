"""اختبارات مقارنة درجة الأخبار (Claude مقابل الكلمات): نافذة بلا نظرة لاحقة، الدرجات، الحركة فوق السوق، والحكم. بيانات مصطنعة، بلا شبكة."""
import os
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import news_score_compare as nc  # noqa: E402
import stocks_bot as sb  # noqa: E402

NY = nc.NY


def news_rows(items):
    """items: (وقت نيويورك ISO، شركة، عنوان، new، importance، expect)"""
    rows = [{"t": pd.Timestamp(t).tz_localize(NY), "ticker": tk, "key": title.lower(), "title": title, "new": nw, "importance": imp, "expect": ex}
            for t, tk, title, nw, imp, ex in items]
    return pd.DataFrame(rows)


# ---------------- النافذة ----------------
def test_window_runs_from_previous_close_to_1015_and_skips_weekends_and_holidays():
    a, b = nc.window(date(2026, 10, 26))                       # الاثنين ← إغلاق الجمعة
    assert a == datetime(2026, 10, 23, 16, 0, tzinfo=NY) and b == datetime(2026, 10, 26, 10, 15, tzinfo=NY)
    a, _ = nc.window(date(2026, 11, 27))                       # بعد عيد الشكر
    assert a == datetime(2026, 11, 25, 16, 0, tzinfo=NY)


def test_headlines_after_the_decision_time_are_never_counted():
    n = news_rows([("2026-10-23 09:00", "CVX", "Oil jumps, Chevron upgrade", 1, "high", "up"),
                   ("2026-10-23 10:16", "CVX", "Chevron plunges on probe", 1, "high", "down"),         # بعد 10:15: يُهمل
                   ("2026-10-22 15:59", "CVX", "Old news before previous close", 1, "high", "down")])    # قبل نافذة اليوم
    rows = nc.day_rows(n, date(2026, 10, 23), ["CVX"], sb.news_points, {}, now=datetime(2026, 10, 23, 12, 0, tzinfo=NY))
    assert len(rows) == 1 and rows[0]["claude"] == 2.0 and rows[0]["n_heads"] == 1


# ---------------- الدرجات ----------------
def test_claude_score_weights_importance_and_ignores_old_or_neutral_news():
    n = news_rows([("2026-10-23 08:00", "X", "a", 1, "high", "up"), ("2026-10-23 08:01", "X", "b", 1, "low", "down"),
                   ("2026-10-23 08:02", "X", "c", 0, "high", "down"),          # تحليل لا خبر جديد
                   ("2026-10-23 08:03", "X", "d", 1, "high", "none")])         # بلا اتجاه
    assert nc.claude_score(n) == 2.0 - 0.5


def test_words_score_uses_the_bots_function_on_the_same_headlines():
    bull = nc.words_score(["Nvidia beats estimates, stock surges"], sb.news_points)
    bear = nc.words_score(["Chevron plunges after probe and lawsuit"], sb.news_points)
    assert bull > 0 > bear


def test_a_day_with_no_scored_news_produces_no_row():
    n = news_rows([("2026-10-23 08:00", "CVX", "Chevron schedules a meeting", 1, "low", "none")])
    assert nc.day_rows(n, date(2026, 10, 23), ["CVX"], sb.news_points, {}, now=datetime(2026, 10, 23, 12, 0, tzinfo=NY)) == []


# ---------------- الحركة فوق السوق ----------------
def prices(day, stock_end, spy_end, next_stock, next_spy, anchor=100.0, spy_anchor=500.0):
    five_idx = pd.DatetimeIndex([datetime.combine(day, t, NY) for t in (nc.time(10, 5), nc.time(10, 10), nc.time(10, 15))])
    five = lambda v: pd.DataFrame({"Close": [v * 0.99, v, v * 1.5]}, index=five_idx)
    days = pd.date_range("2026-07-01", periods=80, freq="B", tz=NY)
    rng = np.random.default_rng(1)
    spy_r = rng.normal(0, 0.01, len(days))
    stock_c = 100 * np.cumprod(1 + 1.0 * spy_r)
    spy_c = 500 * np.cumprod(1 + spy_r)
    sd = pd.DataFrame({"Close": stock_c}, index=days)
    md = pd.DataFrame({"Close": spy_c}, index=days)
    for name, frame, e, nx in (("s", sd, stock_end, next_stock), ("m", md, spy_end, next_spy)):
        frame.loc[pd.Timestamp(day).tz_localize(NY), "Close"] = e
        frame.loc[pd.Timestamp(date(2026, 10, 27)).tz_localize(NY), "Close"] = nx
    return {"CVX": (five(anchor), sd), "SPY": (five(spy_anchor), md)}


def test_abnormal_return_subtracts_beta_times_market_and_uses_the_1010_bar_not_later_bars():
    pr = prices(date(2026, 10, 26), 103.0, 505.0, 106.0, 510.0)
    n = news_rows([("2026-10-26 08:00", "CVX", "Chevron upgrade, stock surges", 1, "high", "up")])
    row = nc.day_rows(n, date(2026, 10, 26), ["CVX"], sb.news_points, pr, now=datetime(2026, 10, 26, 17, 0, tzinfo=NY))[0]
    assert row["anchor"] == 100.0                                      # لا 150 (شمعة 10:15 اللاحقة)
    assert abs(row["ar_day"] - (0.03 - 1.0 * 0.01)) < 0.02             # بيتا ≈ 1: 3% − 1%
    assert abs(row["ar_next"] - (0.06 - 1.0 * 0.02)) < 0.03


def test_returns_stay_empty_until_the_close_has_happened():
    pr = prices(date(2026, 10, 26), 103.0, 505.0, 106.0, 510.0)
    n = news_rows([("2026-10-26 08:00", "CVX", "Chevron upgrade, stock surges", 1, "high", "up")])
    row = nc.day_rows(n, date(2026, 10, 26), ["CVX"], sb.news_points, pr, now=datetime(2026, 10, 26, 11, 0, tzinfo=NY))[0]
    assert row["ar_day"] is None and row["ar_next"] is None


def test_merge_fills_missing_returns_but_keeps_finished_rows():
    old = [{"day": "2026-10-26", "ticker": "A", "ar_day": np.nan, "ar_next": np.nan, "anchor": 10.0, "claude": 1.0},
           {"day": "2026-10-26", "ticker": "B", "ar_day": 0.01, "ar_next": 0.02, "anchor": 10.0, "claude": 1.0}]
    new = [{"day": "2026-10-26", "ticker": "A", "ar_day": 0.03, "ar_next": None, "anchor": 10.0, "claude": 2.0},
           {"day": "2026-10-26", "ticker": "B", "ar_day": 0.09, "ar_next": 0.09, "anchor": 10.0, "claude": 5.0}]
    m = {r["ticker"]: r for r in nc.merge_rows(old, new)}
    assert m["A"]["ar_day"] == 0.03 and m["B"]["claude"] == 1.0


# ---------------- الإحصاء والحكم ----------------
def frame(n_days, per_day, predictive, seed=2):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_days):
        for j in range(per_day):
            c = float(rng.choice([-2.0, -1.0, 1.0, 2.0]))
            ar = (0.01 * np.sign(c) if predictive else 0.0) + rng.normal(0, 0.01)
            rows.append({"day": f"2026-11-{1 + i:02d}", "ticker": f"T{j}", "claude": c, "words": float(rng.choice([-1, 1])), "ar_day": ar, "ar_next": ar})
    return pd.DataFrame(rows)


def test_verdict_confirms_a_real_edge_only_with_enough_cases_and_a_positive_interval():
    s = nc.summary(nc.signed(frame(25, 6, True), "claude", "next"))
    assert s["n"] == 150 and s["hit"] > 0.6 and "تحققت الفرضية" in nc.verdict(s)
    small = nc.summary(nc.signed(frame(5, 6, True), "claude", "next"))
    assert "لا حكم بعد (30 من 100)" in nc.verdict(small)


def test_verdict_does_not_confirm_noise_and_flags_a_reversed_signal():
    assert "لم تتحقق بعد" in nc.verdict(nc.summary(nc.signed(frame(25, 6, False), "claude", "next")))
    rev = frame(25, 6, True)
    rev["ar_next"] = -rev["ar_next"]
    assert "عكسية" in nc.verdict(nc.summary(nc.signed(rev, "claude", "next")))


def test_disagreement_counts_who_was_right_when_the_methods_differ():
    df = pd.DataFrame({"claude": [1.0, 1.0, -1.0], "words": [-1.0, -1.0, -1.0], "ar_next": [0.02, -0.02, -0.03], "day": ["a", "b", "c"]})
    d = nc.disagreement(df, "next")
    assert d == {"n": 2, "claude": 1, "words": 1}


def test_report_separates_exploration_from_judged_days_and_survives_empty_input():
    rows = frame(25, 6, True).to_dict("records")
    for r in rows:
        r.update(n_heads=1, n_scored=1, anchor=1.0, partial=0)
    text = "\n".join(nc.report(rows))
    assert "الحكم على الفرضية" in text and "استكشاف" in text and "تحققت الفرضية" in text
    assert "لا بيانات بعد" in "\n".join(nc.report([]))
