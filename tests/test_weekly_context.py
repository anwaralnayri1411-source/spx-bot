"""اختبارات قسم «سياق اليوم» في الملخص الأسبوعي: لا نظرة لاحقة، حدود ثابتة، وحكم لا يصدر قبل كفاية العينة."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import weekly_learning as wl  # noqa: E402


def make_days(dates, ranges, vixes):
    return pd.DataFrame({"date": dates, "range_pct": ranges, "vix_close": vixes})


def make_trades(days, runs_per_day, bot_edge, kind="atm1", seed=3):
    """bot_edge: دالة (يوم) ← فرق متوسط بين البوت والمعاكس في ذلك اليوم."""
    rng = np.random.default_rng(seed)
    rows = []
    for d in days:
        for r in range(runs_per_day):
            opp = rng.normal(-0.05, 0.05)
            bot = opp + bot_edge(d) + rng.normal(0, 0.05)
            rows += [{"day": d, "run_m": 600 + r * 30, "edge": 5.0, "role": "bot", "kind": kind, "exit": "t60", "real": bot, "theo": 0},
                     {"day": d, "run_m": 600 + r * 30, "edge": 5.0, "role": "opp", "kind": kind, "exit": "t60", "real": opp, "theo": 0}]
    return pd.DataFrame(rows)


def test_context_uses_only_information_known_at_the_open():
    d = wl.context_days(make_days(["2026-10-20", "2026-10-21", "2026-10-22"], [0.5, 1.5, 0.9], [15.0, 20.0, 18.0]))
    assert d["prange"].tolist()[1:] == [0.5, 1.5] and np.isnan(d["prange"].iloc[0])      # حركة أمس لا اليوم
    assert d["pvix"].tolist()[1:] == [15.0, 20.0]


def test_cutoffs_come_from_the_exploration_sample_only():
    dates = [f"2026-09-{i:02d}" for i in range(1, 25)] + ["2026-10-23", "2026-10-26"]
    ranges = list(np.linspace(0.3, 1.2, 24)) + [9.0, 9.0]                                  # أيام جديدة شاذة لا تغيّر الحدود
    d = wl.context_days(make_days(dates, ranges, [16.0] * 26))
    cuts = wl.ctx_cutoffs(d)
    assert cuts["prange"][1] < 2.0


def test_pair_values_matches_bot_and_opposite_per_signal():
    t = make_trades(["2026-10-01", "2026-10-02"], 2, lambda d: 0.2)
    j = wl.pair_values(t, "atm1")
    assert len(j) == 4 and (j["value"] > 0).all() and j["value"].mean() == np.float64(j["value"].mean())


def test_bootstrap_interval_excludes_zero_for_a_strong_consistent_edge_and_includes_it_for_noise():
    days = [f"2026-09-{i:02d}" for i in range(1, 21)]
    strong = wl.pair_values(make_trades(days, 3, lambda d: 0.3), "atm1")
    lo, hi = wl.boot_ci(strong)
    assert lo > 0 and hi > lo
    noise = wl.pair_values(make_trades(days, 3, lambda d: 0.0, seed=9), "atm1")
    lo, hi = wl.boot_ci(noise)
    assert lo < 0 < hi


def test_bootstrap_needs_at_least_three_days():
    j = wl.pair_values(make_trades(["2026-09-01", "2026-09-02"], 5, lambda d: 0.3), "atm1")
    assert wl.boot_ci(j) is None


def scenario(new_days, edge_on_wide=0.3):
    """استكشاف: 30 يوماً (الثلث الأعلى حركة أمس لهم قيمة اتجاه كبيرة). ثم new_days أيام جديدة كلها مرتفعة السياق."""
    old = [f"2026-09-{i:02d}" for i in range(1, 31)]
    new = [f"2026-10-{i:02d}" for i in range(23, 23 + new_days)] if new_days else []
    rng_old = np.tile([0.4, 0.7, 1.4], 10)                                                 # حركة اليوم تصبح «أمس» لليوم التالي
    ranges = list(rng_old) + [1.5] * len(new)
    days = make_days(old + new, ranges, [16.0] * len(ranges))
    ctx = wl.context_days(days).set_index("date")["prange"]
    hi = wl.ctx_cutoffs(wl.context_days(days))["prange"][1]
    edge = lambda d: edge_on_wide if (not np.isnan(ctx.get(d, np.nan)) and ctx[d] > hi) else 0.0
    return days, make_trades(old + new, 3, edge)


def test_no_verdict_before_enough_new_signals():
    days, trades = scenario(5)                                                              # 5 أيام × 3 = 15 إشارة < 30
    text = "\n".join(wl.sec_context(trades, days))
    assert "لا حكم بعد" in text and "15 من 30" in text
    assert "لا يغيّر هذا القسم أي توصية" in text


def test_verdict_confirms_when_new_days_show_a_real_edge():
    days, trades = scenario(14, edge_on_wide=0.3)                                           # 14 × 3 = 42 إشارة جديدة
    text = "\n".join(wl.sec_context(trades, days))
    assert "تحققت الفرضية" in text


def test_verdict_rejects_when_new_days_show_no_edge():
    old = [f"2026-09-{i:02d}" for i in range(1, 31)]
    new = [f"2026-10-{i:02d}" for i in range(23, 37 - 0)][:14]
    ranges = list(np.tile([0.4, 0.7, 1.4], 10)) + [1.5] * 14
    days = make_days(old + new, ranges, [16.0] * 44)
    ctx = wl.context_days(days).set_index("date")["prange"]
    hi = wl.ctx_cutoffs(wl.context_days(days))["prange"][1]
    trades = make_trades(old + new, 3, lambda d: 0.3 if (d in old and not np.isnan(ctx.get(d, np.nan)) and ctx[d] > hi) else 0.0)
    text = "\n".join(wl.sec_context(trades, days))
    assert "لم تتحقق الفرضية" in text


def test_section_handles_missing_inputs():
    assert "لا بيانات كافية" in wl.sec_context(pd.DataFrame(), pd.DataFrame())[0]


def test_telegram_message_shows_only_the_verdict_of_section_six():
    days, trades = scenario(5)
    days = days.assign(dow=range(len(days)), opex_month=0, entries=50, ge100=10, mean_close=-0.5)
    days["dow"] = days["dow"] % 5
    data = {"z": pd.DataFrame(columns=["date", "result"]), "positions": [], "stocks_log": pd.DataFrame(), "tail_days": days,
            "tail_events": pd.DataFrame(), "snaps": pd.DataFrame(), "outcomes": pd.DataFrame(), "momentum": pd.DataFrame(),
            "news_md": "", "trades": trades}
    md, tg = wl.build(today=pd.Timestamp("2026-10-24").date(), data=data)
    assert "6) سياق اليوم" in md and "الفرضية (سُجّلت 23/10)" in md
    assert "⚖️" in tg and "الفرضية (سُجّلت" not in tg and len(tg) <= 3900
