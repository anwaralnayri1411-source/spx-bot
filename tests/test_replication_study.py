"""اختبارات تكرار الفرضيات على سلاسل أخرى: القيم، الفواصل، قاعدة «تتكرر»، وكشف أثر مزروع دون اختراع أثر من الضجيج. بيانات مصطنعة."""
import gzip
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import replication_study as rp  # noqa: E402
from test_strategy_sim import make  # noqa: E402


def series(plant, names=("QQQ", "IWM", "AAA", "BBB", "CCC"), n=400, seed0=1):
    return {nm: make(n=n, plant=plant, seed=seed0 + k)[0] for k, nm in enumerate(names)}


def test_sixty_minute_values_show_a_positive_edge_when_the_effect_is_planted():
    d = make(n=400, plant=0.006, seed=1)[0]
    v = rp.sixty_minute_values(d)
    assert set(v["hyp"]) == {"MOM", "F30", "AGREE"}
    assert v[v["hyp"] == "F30"]["val"].mean() > 0.0005 and v[v["hyp"] == "MOM"]["val"].mean() > 0
    null = rp.sixty_minute_values(make(n=400, plant=0.0, seed=2)[0])
    assert abs(null[null["hyp"] == "F30"]["val"].mean()) < 0.0004


def test_last30_and_sweep_values_have_the_expected_shape():
    d = make(n=300, plant=0.0, seed=3)[0]
    l30 = rp.last30_values(d)
    assert set(l30["hyp"]) == {"LAST30"} and len(l30) > 200
    real, plac = rp.sweep_values(d)
    assert list(real.columns) == ["day", "R"] and list(plac.columns) == ["day", "R"]


def test_day_ci_and_diff_ci_bracket_the_mean_and_need_enough_data():
    rng = np.random.default_rng(1)
    days = list(pd.bdate_range("2023-01-02", periods=300))
    df = pd.DataFrame({"day": rng.choice(days, 900), "val": rng.normal(0.001, 0.01, 900)})
    n, m, ci = rp.day_ci(df, days)
    assert n == 900 and ci[95.0][0] < m < ci[95.0][1] and ci[99.6][0] <= ci[95.0][0] and ci[99.6][1] >= ci[95.0][1]
    assert rp.day_ci(df.head(10), days) is None
    real = pd.DataFrame({"day": rng.choice(days, 400), "R": rng.normal(0.3, 1, 400)})
    plac = pd.DataFrame({"day": rng.choice(days, 400), "R": rng.normal(0.0, 1, 400)})
    n2, d2, ci2 = rp.diff_ci(real, plac, days)
    assert d2 > 0.1 and ci2[95.0][0] > 0 and rp.diff_ci(real.head(5), plac, days) is None


def test_per_series_mean_matches_the_pooled_event_mean_even_with_uneven_entries_per_day():
    days = list(pd.bdate_range("2023-01-02", periods=120))
    rows = []
    for k, d in enumerate(days):
        n = 4 if k % 2 == 0 else 1                                                      # أيام باتفاق في 4 مواعيد وأخرى في موعد واحد
        rows += [{"day": d, "val": 0.002 if n == 4 else -0.001} for _ in range(n)]
    df = pd.DataFrame(rows)
    m, t = rp.per_series_t(df)
    assert m == pytest.approx(df["val"].mean()) and m > 0                                # لا تناقض مع المتوسط المجمّع


def test_per_series_t_uses_daily_averages():
    days = list(pd.bdate_range("2023-01-02", periods=100))
    rng = np.random.default_rng(2)
    df = pd.DataFrame({"day": np.repeat(days, 4), "val": rng.normal(0.002, 0.002, 400)})
    m, t = rp.per_series_t(df)
    assert m == pytest.approx(0.002, abs=0.0006) and t > 5
    assert rp.per_series_t(df.head(40)) is None


def test_replicates_rule_needs_both_groups_and_the_economic_hurdle():
    ok = (500, 0.0010, {95.0: (0.0004, 0.0016), 99.6: (0.0001, 0.0019)})
    weak = (500, 0.0001, {95.0: (0.00002, 0.0002), 99.6: (-0.0001, 0.0003)})
    zero = (500, 0.0002, {95.0: (-0.0003, 0.0007), 99.6: (-0.0005, 0.0009)})
    assert rp.replicates("F30", {"صناديق": ok, "شركات": ok}) == "تتكرر"
    assert "اقتصادياً لا" in rp.replicates("F30", {"صناديق": ok, "شركات": weak})
    assert rp.replicates("F30", {"صناديق": ok, "شركات": zero}) == "لا تتكرر"
    assert rp.replicates("SWEEP", {"صناديق": weak, "شركات": weak}) == "تتكرر"                  # SWEEP بوحدات R: بلا حاجز
    assert "بيانات ناقصة" in rp.replicates("F30", {"صناديق": ok, "شركات": None})


def test_planted_effect_replicates_and_noise_does_not():
    stats, per, days = rp.analyze(series(0.006))
    assert rp.replicates("F30", {g: stats[(g, "F30")] for g in ("صناديق", "شركات")}) == "تتكرر"
    stats0, _, _ = rp.analyze(series(0.0, seed0=50))
    verdicts = [rp.replicates(h, {g: stats0[(g, h)] for g in ("صناديق", "شركات")}) for h in rp.HYPS + ("SWEEP",)]
    assert verdicts.count("تتكرر") <= 1
    text = "\n".join(rp.report(stats, per, days, 5, "تجربة"))
    assert "H-REPL" in text and "H-MOM" in text and "H-SWEEP" in text and text.count("⚖️") == 5


def test_main_reads_a_folder_and_handles_empty(tmp_path, monkeypatch):
    base = tmp_path / "bars"
    base.mkdir()
    for k, nm in enumerate(("QQQ", "AAA")):
        d = make(n=300, plant=0.004, seed=10 + k)[0]
        out = d.assign(t=d["t"].dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%M:%SZ"))[["t", "o", "h", "l", "c", "v"]]
        with gzip.open(base / f"{nm}.csv.gz", "wt", encoding="utf-8", newline="") as f:
            out.to_csv(f, index=False)
    monkeypatch.setattr(rp, "OUT", str(tmp_path / "r.md"))
    assert rp.main([str(base)]) == 0
    assert "H-LAST30" in (tmp_path / "r.md").read_text(encoding="utf-8")
    assert rp.main([str(tmp_path / "none")]) == 0
