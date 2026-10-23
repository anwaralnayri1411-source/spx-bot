"""اختبارات دراسة الانجراف بعد الأرباح: يوم رد الفعل، بناء الأحداث، فاصل العناقيد، وحكم الفرضية. بيانات مصطنعة."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import pead_study as ps  # noqa: E402

NY = "America/New_York"


def prices(n=400, seed=1, drift_after=None):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n)
    r = rng.normal(0, 0.01, n)
    return pd.Series(100 * np.cumprod(1 + r), index=idx), pd.Series(400 * np.cumprod(1 + rng.normal(0, 0.005, n)), index=idx)


def test_reaction_day_before_open_after_close_and_unknown_time():
    td = pd.bdate_range("2026-10-12", periods=10)                         # الاثنين 12/10 ...
    t = lambda s: pd.Timestamp(s, tz=NY)
    assert ps.reaction_day(t("2026-10-14 07:00"), td) == pd.Timestamp("2026-10-14")             # قبل الافتتاح
    assert ps.reaction_day(t("2026-10-14 16:05"), td) == pd.Timestamp("2026-10-15")             # بعد الإغلاق
    assert ps.reaction_day(t("2026-10-16 16:30"), td) == pd.Timestamp("2026-10-19")             # جمعة بعد الإغلاق ← الاثنين
    assert ps.reaction_day(t("2026-10-14 00:00"), td) is None                                    # وقت مجهول
    assert ps.reaction_day(t("2026-10-17 07:00"), td) is None                                    # سبت: ليس يوم تداول


def test_build_events_filters_small_surprises_and_computes_abnormal_returns():
    close, spy = prices()
    day = close.index[100]
    earn = pd.DataFrame({"Reported EPS": [1.1, 1.0, np.nan, 1.2], "Surprise(%)": [10.0, 1.0, np.nan, -6.0]},
                        index=[pd.Timestamp(day, tz=NY) + pd.Timedelta(hours=7),
                               pd.Timestamp(close.index[150], tz=NY) + pd.Timedelta(hours=7),
                               pd.Timestamp(close.index[200], tz=NY) + pd.Timedelta(hours=7),
                               pd.Timestamp(close.index[250], tz=NY) + pd.Timedelta(hours=17)])
    ev = ps.build_events("X", earn, close, spy)
    assert len(ev) == 2 and ev[0]["surprise"] == 10.0 and ev[1]["date"] == close.index[251]    # بعد الإغلاق: اليوم التالي
    i = 100
    assert abs(ev[0]["ar_r"] - ((close.iloc[i] / close.iloc[i - 1] - 1) - (spy.iloc[i] / spy.iloc[i - 1] - 1))) < 1e-12
    assert abs(ev[0]["ar_d"] - ((close.iloc[i + 5] / close.iloc[i] - 1) - (spy.iloc[i + 5] / spy.iloc[i] - 1))) < 1e-12


def test_cluster_bootstrap_needs_enough_clusters_and_brackets_the_mean():
    assert ps.cluster_ci([0.1] * 20, ["a"] * 10 + ["b"] * 10) is None
    rng = np.random.default_rng(2)
    v = rng.normal(0.01, 0.02, 400)
    cl = np.repeat([f"q{i}" for i in range(40)], 10)
    lo, hi = ps.cluster_ci(v, cl)
    assert lo < v.mean() < hi


def events(n, effect, seed=3):
    rng = np.random.default_rng(seed)
    sur = rng.choice([-10.0, -5.0, 5.0, 10.0], n)
    ar_d = effect * np.sign(sur) + rng.normal(0, 0.03, n)
    return pd.DataFrame({"ticker": "X", "date": pd.date_range("2018-01-01", periods=n, freq="7D"), "surprise": sur,
                         "ar_r": 0.02 * np.sign(sur) + rng.normal(0, 0.03, n), "ar_d": ar_d,
                         "q": [f"{d.year}Q{(d.month - 1) // 3 + 1}" for d in pd.date_range("2018-01-01", periods=n, freq="7D")]})


def test_verdict_confirms_a_real_drift_rejects_noise_and_waits_for_enough_events():
    assert "تحققت الفرضية" in ps.verdict(events(500, 0.01))
    assert "لم تتحقق" in ps.verdict(events(500, 0.0))
    assert "لا حكم بعد" in ps.verdict(events(50, 0.01))
    assert "عكسية" in ps.verdict(events(500, -0.01))


def test_report_has_all_rows_and_empty_case():
    text = "\n".join(ps.report(events(300, 0.005), "تجربة"))
    assert "المفاجأة × الانجراف" in text and "رد الفعل في يوم الإعلان" in text and "النصف الأول" in text and "الحكم على الفرضية" in text
    assert "لا أحداث" in "\n".join(ps.report(pd.DataFrame(), "x"))
