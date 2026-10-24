"""اختبارات دراسة نظام التضخم: تحليل CPI، عدم النظرة للمستقبل، وفرق العناقيد. بيانات مصطنعة."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import regime_study as rs  # noqa: E402


def cpi_series():
    idx = pd.date_range("2019-01-01", periods=84, freq="MS")
    v = 100 * np.cumprod(1 + np.r_[np.full(36, 0.001), np.full(24, 0.006), np.full(24, 0.002)])      # تضخم منخفض ثم مرتفع ثم متوسط
    return pd.Series(v, index=idx)


def test_parse_cpi_handles_fred_csv_with_missing_values():
    text = "observation_date,CPIAUCSL\n2024-01-01,310.3\n2024-02-01,.\n2024-03-01,312.2\n"
    s = rs.parse_cpi(text)
    assert list(s.index.strftime("%Y-%m")) == ["2024-01", "2024-03"] and s.iloc[1] == pytest.approx(312.2)
    assert len(rs.parse_cpi("DATE,CPIAUCSL\n2024-01-01,1\n")) == 1


def test_known_yoy_waits_for_publication_and_never_uses_the_future():
    cpi = cpi_series()
    days = pd.to_datetime(["2019-12-15", "2020-01-15", "2020-02-20", "2022-06-30"])
    y = rs.known_yoy(cpi, days)
    assert np.isnan(y.iloc[0])                                            # لا CPI سنوي بعد
    assert np.isnan(y.iloc[1])                                            # يناير 2020 لم يُنشر (45 يوماً من 1/1 = 15/2)
    assert y.iloc[2] == pytest.approx(cpi.iloc[12] / cpi.iloc[0] - 1)     # بعد 15/2 يُعرف يناير 2020
    cut = rs.known_yoy(cpi[cpi.index <= "2022-01-01"], days)
    assert y.iloc[2] == pytest.approx(cut.iloc[2])                         # حذف المستقبل لا يغيّر القيمة المعروفة وقتها


def frame(diff, n_months=60, per_month=60, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for m in range(n_months):
        high = (m // 12) % 2 == 1
        day = pd.Timestamp("2021-01-01") + pd.DateOffset(months=m)
        for k in range(per_month):
            rows.append({"day": day + pd.Timedelta(days=k % 20), "skill": rng.normal(diff if high else 0.0, 0.05), "high": high})
    return pd.DataFrame(rows)


def test_cluster_diff_detects_a_planted_difference_and_not_a_null():
    r = rs.cluster_diff(frame(0.03))
    assert r["diff"] > 0.02 and r["ci"][0] > 0 and "يختلف" in rs.verdict(r)
    r0 = rs.cluster_diff(frame(0.0, seed=2))
    assert r0["ci"][0] < 0 < r0["ci"][1] and "لم يثبت" in rs.verdict(r0)


def test_cluster_diff_needs_both_regimes_and_enough_data():
    f = frame(0.03)
    assert rs.cluster_diff(f[f["high"]]) is None
    assert rs.cluster_diff(frame(0.03, n_months=6)) is None
    assert "لا حكم" in rs.verdict(None)


def test_report_and_main_survive_download_failure(tmp_path, monkeypatch):
    r = rs.cluster_diff(frame(0.02))
    yearly = pd.DataFrame({"n": [100, 120], "skill": [0.02, -0.01], "yoy": [0.05, 0.03]}, index=[2022, 2023])
    text = "\n".join(rs.report({0: r, 1: r}, yearly, "تجربة"))
    assert "بحسب السنة" in text and "⚖️" in text and "ATM 0DTE" in text
    monkeypatch.setattr(rs, "OUT", str(tmp_path / "r.md"))
    p = tmp_path / "spy.csv"
    p.write_text("t,o,h,l,c,v\n", encoding="utf-8")
    monkeypatch.setattr(rs, "fetch_cpi", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert rs.main([str(p)]) == 1                               # الفشل ظاهر: رمز خروج غير صفري وملف يذكر السبب
    assert "تعذر تحميل CPI" in (tmp_path / "r.md").read_text(encoding="utf-8")
    assert rs.main([str(tmp_path / "none.csv")]) == 0
