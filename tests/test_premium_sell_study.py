"""اختبارات بيع الأقساط: السترايكات (أساس وأدوات)، الائتمان، القيمة عند الانتهاء، R للجهة والكوندور، المزدوج، والحكم. بلا شبكة."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import premium_sell_study as ps  # noqa: E402
from test_fibo_entry_study import random_series  # noqa: E402


def test_expected_move_and_base_strikes():
    em = ps.expected_move(700.0, 16.0)
    assert em == pytest.approx(700 * 0.16 / 252 ** 0.5)
    assert ps.base_strikes(700.0, em) == (float(np.floor(700 - 0.8 * em)), float(np.ceil(700 + 0.8 * em)))


def test_tool_strikes_pick_the_nearest_level_inside_the_band_or_none():
    em = 7.0
    levels = [690.0, 694.5, 699.0, 705.2, 730.0]
    put, call = ps.tool_strikes(700.0, em, levels)
    assert put == 694.0 and call == 706.0                                   # 699 قريب جداً (<0.4em)، 730 بعيد جداً (>2em)
    assert ps.tool_strikes(700.0, em, [699.0, 730.0]) == (None, None)


def test_legs_symbols_and_width():
    assert ps.legs("put", 690.0, "2026-10-27") == ("SPY261027P00690000", "SPY261027P00687000")
    assert ps.legs("call", 710.0, "2026-10-27") == ("SPY261027C00710000", "SPY261027C00713000")


def test_credit_value_and_r():
    cr = ps.credit_of(1.00, 0.40)
    assert cr == pytest.approx(0.97 - 0.412 - 0.013)
    assert ps.credit_of(0.10, 0.08) is None and ps.credit_of(None, 0.1) is None
    assert ps.spread_value("put", 690, 695) == 0 and ps.spread_value("put", 690, 688) == 2 and ps.spread_value("put", 690, 600) == 3
    assert ps.spread_value("call", 710, 705) == 0 and ps.spread_value("call", 710, 711.5) == 1.5 and ps.spread_value("call", 710, 800) == 3
    assert ps.side_r(0.5, 0.0) == pytest.approx(0.5 / 2.5) and ps.side_r(0.5, 3.0) == pytest.approx(-1.0)
    assert ps.condor_r(0.5, 0.4, 0.0, 0.0) == pytest.approx(0.9 / 2.1) and ps.condor_r(0.5, 0.4, 3.0, 0.0) == pytest.approx(-1.0)
    assert ps.condor_r(2.0, 1.5, 0, 0) is None


def test_drawdown_and_risk_profile():
    assert ps.drawdown([0.2, 0.2, -1.0, 0.1, -0.5, 0.3]) == pytest.approx(-1.4)
    rp_ = ps.risk_profile([0.2, -1.0, 0.3])
    assert rp_["maxloss"] == pytest.approx(1 / 3) and rp_["worst"][0] == -1.0
    assert ps.risk_profile([]) is None


def bars_at(t0, price):
    return pd.DataFrame({"o": [price]}, index=pd.DatetimeIndex([t0 + pd.Timedelta(minutes=1)]))


def test_evaluate_condors_and_paired_difference():
    t0 = pd.Timestamp("2026-01-05 10:30", tz="America/New_York")
    plans = [{"day": pd.Timestamp("2026-01-05"), "t0": t0, "S": 700.0, "em": 7.0, "exp": "2026-01-06", "close_exp": 701.0,
              "strikes": {("base", "put"): 694.0, ("base", "call"): 706.0, ("tool", "put"): 692.0}}]
    bars = {}
    for side, short, ps_, pl_ in (("put", 694.0, 1.0, 0.4), ("call", 706.0, 0.9, 0.35), ("put", 692.0, 0.7, 0.25)):
        s, l = ps.legs(side, short, "2026-01-06")
        bars[s], bars[l] = bars_at(t0, ps_), bars_at(t0, pl_)
    df = ps.evaluate(plans, bars)
    assert len(df) == 3 and df["R"].notna().all() and (df["R"] > 0).all()                    # إغلاق 701 بين السترايكات: كله ربح
    cb = ps.condors(df, "base")
    assert len(cb) == 1 and cb["val"].iloc[0] > 0
    pr_ = ps.paired(df)
    assert len(pr_) == 1                                                                       # جهة البوت فقط فيها أداة وأساس
    df2 = ps.evaluate([dict(plans[0], strikes={("base", "put"): 694.0})], {})
    assert df2["R"].isna().all()                                                               # بلا أسعار: مستبعدة


def test_build_plans_uses_previous_sessions_for_levels_and_next_day_for_settlement():
    d = random_series(12, 3)
    d = d.assign(t=d["t"] + pd.Timedelta(days=0))
    vix = pd.Series(16.0, index=pd.DatetimeIndex(sorted({pd.Timestamp(x) for x in d["t"].dt.date})))
    import premium_sell_study as m
    old = m.START
    m.START = "2020-01-01"
    try:
        plans = ps.build_plans(d, vix)
    finally:
        m.START = old
    assert len(plans) == 12 - 2 - 1                                                            # جلستان تاريخ، واليوم الأخير بلا انتهاء
    p = plans[0]
    assert ("base", "put") in p["strikes"] and p["exp"] > str(p["day"].date()) and p["t0"].hour == 10 and p["t0"].minute == 30


def test_verdicts_wait_and_confirm():
    days = list(pd.bdate_range("2024-03-01", periods=320))
    rng = np.random.default_rng(1)
    cb = pd.DataFrame({"day": days, "val": rng.normal(0.08, 0.4, 320)})
    pr_ = pd.DataFrame({"day": days[:200], "val": rng.normal(0.0, 0.3, 200)})
    v1, v2 = ps.verdicts(cb, pr_)
    assert "H-SELL: تحققت" in v1 and "H-SELLTOOL: لم تتحقق" in v2
    v1b, v2b = ps.verdicts(cb.head(50), pr_.head(20))
    assert "لا حكم بعد" in v1b and "لا حكم بعد" in v2b


def test_main_without_keys_is_a_no_op(monkeypatch):
    monkeypatch.delenv("ALPACA_KEY", raising=False)
    assert ps.main([]) == 0


def test_report_has_both_verdicts_and_the_risk_profile():
    days = list(pd.bdate_range("2024-03-01", periods=60))
    rows = []
    for d in days:
        for kind in ("base", "tool"):
            for side in ("put", "call"):
                rows.append({"day": d, "kind": kind, "side": side, "short": 700.0, "credit": 0.5, "value": 0.0, "R": 0.2})
    text = "\n".join(ps.report(pd.DataFrame(rows), "تجربة"))
    assert text.count("⚖️") == 2 and "الكوندور الأساسي" in text and "أعمق هبوط" in text
    assert "لا بيانات" in "\n".join(ps.report(pd.DataFrame(columns=["day", "kind", "side", "short", "credit", "value", "R"]), "x"))
