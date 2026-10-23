"""اختبارات دراسة صدمة التقلب: الأحداث المستقلة، الأثر، الحكم، ومعايرة الصفر. بيانات مصطنعة."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import vix_shock_study as vs  # noqa: E402


def series(n=3500, rebound=0.0, seed=1, drop=False):
    """SPY مشي عشوائي؛ في كل 25 يوماً قفزة VIX +20% (وهبوط SPY 2.5% إن drop) يتبعها ارتداد بمقدار rebound موزعاً على 5 أيام.
    الهبوط يُسقط متوسط كل الأيام (نوافذ ما قبله)، فلا نستعمله في اختبارات الحكم كي تبقى القاعدة نظيفة."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2011-01-03", periods=n)
    r = rng.normal(0.0003, 0.008, n)
    vix = 16 + np.cumsum(rng.normal(0, 0.1, n))
    vix = np.clip(vix, 10, 25)
    for i in range(30, n - 8, 25):
        if drop:
            r[i] = -0.025
        vix[i] = vix[i - 1] * 1.2
        r[i + 1:i + 6] += rebound / 5
    return pd.Series(100 * np.cumprod(1 + r), index=idx), pd.Series(vix, index=idx)


def test_independent_keeps_first_of_each_cluster():
    flags = np.array([1, 1, 0, 1, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 1], dtype=bool)
    k = vs.independent(flags, gap=5)
    assert list(np.flatnonzero(k)) == [0, 8, 15]


def test_build_flags_both_shock_kinds_and_forward_return():
    spy, vix = series(200, drop=True)
    df = vs.build(spy, vix)
    row = df.iloc[10]
    assert abs(row["fwd"] - (spy.shift(-5).loc[row.name] / spy.loc[row.name] - 1)) < 1e-12
    shock = df[df["shock"]]
    assert len(shock) >= 5 and shock["shock_spy"].all() and shock["shock_vix"].mean() > 0.6


def test_stat_excess_is_relative_to_all_days_and_needs_clusters():
    spy, vix = series(3500, rebound=0.03)
    df = vs.build(spy, vix)
    ind = pd.Series(vs.independent(df["shock"].to_numpy()), index=df.index)
    s = vs.stat(df, ind)
    assert s["n"] > 100 and s["mean"] > 0.02 and s["ci"][0] > 0
    assert vs.stat(df, pd.Series(False, index=df.index)) is None


def test_verdict_confirms_planted_rebound_and_rejects_noise_and_waits_for_events():
    spy, vix = series(3500, rebound=0.03)
    assert "تحققت" in vs.verdict(vs.build(spy, vix))
    spy, vix = series(3500, rebound=0.0, seed=2)
    assert "تحققت" not in vs.verdict(vs.build(spy, vix))
    spy, vix = series(3500, rebound=-0.03, seed=3)
    assert "عكسية" in vs.verdict(vs.build(spy, vix))
    spy, vix = series(300, rebound=0.03)
    assert "لا حكم بعد" in vs.verdict(vs.build(spy, vix))


def test_report_sections_and_main_survive_load_failure(tmp_path, monkeypatch):
    spy, vix = series(3500, rebound=0.01)
    text = "\n".join(vs.report(vs.build(spy, vix), "تجربة"))
    assert "أفق 10 أيام" in text and "VIX > 30" in text and "النصف الثاني" in text and "⚖️" in text
    monkeypatch.setattr(vs, "OUT", str(tmp_path / "r.md"))
    monkeypatch.setattr(vs, "load", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert vs.main([]) == 0
    monkeypatch.setattr(vs, "load", lambda: (spy, vix))
    assert vs.main([]) == 0 and "الفرضية المسجّلة" in (tmp_path / "r.md").read_text(encoding="utf-8")
