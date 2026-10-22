"""اختبارات منطق بوت SPX الأساسي: التسعير، نقاط الاتجاه، اختيار العقد، حجم الصفقة، وقواعد الوقف والهدف.
الهدف: ألا يكسر أي تعديل مستقبلي منطقاً سليماً دون أن نعرف. لا شبكة ولا بيانات حية."""
import math
from datetime import datetime

import pandas as pd
import pytest

import options_bot as ob


# ---------- التسعير واليونانيات ----------
def test_call_put_parity():
    S, K, T, s = 7700.0, 7700.0, 0.01, 0.18
    call, put = ob.bs_price(S, K, T, s, "CALL"), ob.bs_price(S, K, T, s, "PUT")
    # C - P = S - K*exp(-rT)
    assert call - put == pytest.approx(S - K * math.exp(-ob.RISK_FREE * T), abs=1e-6)


def test_delta_bounds_and_atm_near_half():
    d_call = ob.abs_delta(7700, 7700, 0.01, 0.18, "CALL")
    d_put = ob.abs_delta(7700, 7700, 0.01, 0.18, "PUT")
    assert 0.45 < d_call < 0.55 and 0.45 < d_put < 0.55
    assert d_call + d_put == pytest.approx(1.0)
    assert ob.abs_delta(7700, 7900, 0.01, 0.18, "CALL") < 0.15          # بعيد خارج المال (2.6% فوق السعر)
    assert ob.abs_delta(7700, 7500, 0.01, 0.18, "CALL") > 0.9           # داخل المال (2.6%)


def test_price_increases_with_volatility_and_time():
    base = ob.bs_price(7700, 7750, 0.005, 0.15, "CALL")
    assert ob.bs_price(7700, 7750, 0.005, 0.25, "CALL") > base
    assert ob.bs_price(7700, 7750, 0.02, 0.15, "CALL") > base


def test_prob_beyond_is_a_probability_and_monotonic():
    near = ob.prob_beyond(7700, 7710, 0.01, 0.18, "CALL")
    far = ob.prob_beyond(7700, 7800, 0.01, 0.18, "CALL")
    assert 0 < far < near < 1
    assert ob.prob_beyond(7700, 7690, 0.01, 0.18, "PUT") < 0.5


def test_gamma_zero_for_invalid_inputs():
    assert ob.bs_gamma(7700, 7700, 0, 0.18) == 0.0
    assert ob.bs_gamma(7700, 7700, 0.01, 0) == 0.0


# ---------- حجم الصفقة ----------
def test_qty_never_exceeds_risk_budget_or_cap():
    q, per_loss = ob.qty_for(100.0)
    assert per_loss == pytest.approx(100.0 * ob.STOP_LOSS)
    assert 1 <= q <= ob.MAX_QTY
    assert q * per_loss <= ob.RISK_BUDGET_USD
    q, _ = ob.qty_for(1.0)
    assert q == ob.MAX_QTY                                               # سقف الكمية
    q, _ = ob.qty_for(1000.0)
    assert q == 1                                                        # عقد واحد على الأقل حتى لو تجاوز الميزانية


# ---------- نقاط الاتجاه ----------
def base_c(**over):
    c = {"n": datetime(2026, 10, 19, 11, 0, tzinfo=ob.NY), "S": 7700.0, "sma20": 7650.0, "sma50": 7600.0,
         "ema9": 7701.0, "ema21": 7695.0, "above_vwap": True, "r30": 0.002, "gap": None, "map": None,
         "max_pain": None, "es": None, "leaders": None, "orb": None, "vix_day": None, "pc": None, "pd_hl": None}
    c.update(over)
    return c


def test_bullish_inputs_give_more_bull_than_bear():
    bull, bear, rows = ob.direction_points(base_c())
    assert bull > bear and bear == 0 and rows


def test_bearish_inputs_mirror():
    c = base_c(S=7500.0, sma20=7550.0, sma50=7600.0, ema9=7499.0, ema21=7505.0, above_vwap=False, r30=-0.002)
    bull, bear, _ = ob.direction_points(c)
    assert bear > bull and bull == 0


def test_neutral_inputs_stay_below_the_signal_threshold():
    c = base_c(S=7625.0, sma20=7650.0, sma50=7600.0, ema9=7620.0, ema21=7630.0, above_vwap=True, r30=0.0)
    bull, bear, _ = ob.direction_points(c)
    assert abs(bull - bear) < ob.MIN_EDGE


def test_negative_gamma_amplifies_short_term_points():
    pos = ob.direction_points(base_c(map={"gex": 1, "call_wall": None, "put_wall": None}))[0]
    neg = ob.direction_points(base_c(map={"gex": -1, "call_wall": None, "put_wall": None}))[0]
    assert neg > pos


def test_call_wall_nearby_adds_bear_points():
    c = base_c(map={"gex": 0, "call_wall": (7710.0, 5000), "put_wall": None})
    _, bear, rows = ob.direction_points(c)
    assert bear >= 1 and any(r[0] == "الجدران" and r[2] == 1 for r in rows)


def test_orb_breakout_adds_bull_points():
    _, _, rows = ob.direction_points(base_c(orb=(7690.0, 7650.0)))
    assert next(r for r in rows if "نطاق" in r[0])[1] == 1.5


# ---------- اختيار العقد ----------
def chain(rows):
    return pd.DataFrame(rows, columns=["strike", "bid", "ask", "lastPrice", "openInterest", "volume", "impliedVolatility"])


def good_row(strike, ask=2.0):
    return (strike, ask - 0.1, ask, ask, 2000, 3000, 0.20)


def test_picks_liquid_otm_calls_within_price_band():
    df = chain([good_row(7710, 2.0), good_row(7720, 1.2), good_row(7690, 9.0)])      # الأخير داخل المال
    out = ob.pick_contracts("CALL", df, 7700.0, 0.01, 18.0)
    assert out and all(k["strike"] > 7700 for k in out)
    assert all(ob.CONTRACT_MIN_USD <= k["cost"] <= ob.CONTRACT_MAX_USD for k in out)
    assert out == sorted(out, key=lambda k: -k["score"])
    assert len(out) <= ob.MAX_CONTRACTS


def test_rejects_too_expensive_wide_spread_and_illiquid():
    ob.REJ.clear()
    df = chain([(7705, 4.9, 5.1, 5.0, 2000, 3000, 0.2),                 # 510$ فوق الحد
                (7710, 0.5, 2.0, 1.2, 2000, 3000, 0.2),                 # فرق عرض/طلب واسع جداً
                (7715, 1.4, 1.5, 1.5, 10, 10, 0.2)])                    # سيولة ضعيفة
    assert ob.pick_contracts("CALL", df, 7700.0, 0.01, 18.0) == []
    assert {"سعر خارج النطاق", "فرق الشراء/البيع واسع", "سيولة ضعيفة"} <= set(ob.REJ)


def test_put_side_picks_strikes_below_spot():
    df = chain([good_row(7690, 2.0), good_row(7710, 2.0)])
    out = ob.pick_contracts("PUT", df, 7700.0, 0.01, 18.0)
    assert out and all(k["strike"] < 7700 for k in out)


def test_spxw_rows_prefers_daily_contracts_when_present():
    df = pd.DataFrame({"contractSymbol": ["SPX261030C07700000", "SPXW261030C07700000"], "strike": [7700, 7700]})
    assert list(ob.spxw_rows(df)["contractSymbol"]) == ["SPXW261030C07700000"]
    only = pd.DataFrame({"contractSymbol": ["SPY261030C00770000"], "strike": [770]})
    assert len(ob.spxw_rows(only)) == 1                                  # بلا SPXW: لا نُسقط شيئاً


# ---------- قواعد الوقف والهدف ----------
def pos(**over):
    p = {"entry": 2.0, "time": "2026-10-19T10:00-04:00", "alerts": {}, "status": "open", "peak": 0.0,
         "strike": 7710.0, "side": "CALL", "sym": "SPX"}
    p.update(over)
    return p


NOON = datetime.fromisoformat("2026-10-19T12:00-04:00")


def test_stop_fires_once_at_minus_forty_percent_after_min_age():
    p = pos()
    pnl, new = ob.eval_position(p, NOON, 1.1, 7700.0)                    # -45%
    assert [k for k, _ in new] == ["stop"] and p["status"] == "stopped"
    _, again = ob.eval_position(p, NOON, 1.0, 7700.0)
    assert again == []                                                    # لا تكرار بعد الوقف


def test_no_stop_alert_before_min_age():
    p = pos(time="2026-10-19T11:55-04:00")                                # عمره 5 دقائق فقط
    _, new = ob.eval_position(p, NOON, 1.0, 7700.0)
    assert new == [] and p["status"] == "open"


def test_target_fires_at_plus_fifty_percent():
    p = pos()
    _, new = ob.eval_position(p, NOON, 3.1, 7720.0)
    assert [k for k, _ in new] == ["tp"] and p["status"] == "tp"


def test_giveback_after_target():
    p = pos()
    ob.eval_position(p, NOON, 3.2, 7720.0)                                # بلغ الهدف، القمة +60%
    _, new = ob.eval_position(p, NOON, 2.3, 7712.0)                       # الربح تراجع إلى +15% (أقل من نصف القمة)
    assert "giveback" in [k for k, _ in new]


def test_final_half_hour_warns_once_and_depends_on_moneyness():
    p = pos()
    late = datetime.fromisoformat("2026-10-19T15:35-04:00")
    _, new = ob.eval_position(p, late, 1.5, 7690.0)                       # خارج المال
    assert [k for k, _ in new] == ["exp_final"] and "خارج المال" in new[0][1]
    _, new = ob.eval_position(p, late, 1.5, 7690.0)
    assert new == []


def test_tracks_peak_profit():
    p = pos()
    ob.eval_position(p, NOON, 2.6, 7705.0)
    assert p["peak"] == pytest.approx(0.3)
    ob.eval_position(p, NOON, 2.2, 7703.0)
    assert p["peak"] == pytest.approx(0.3)                                # القمة لا تنخفض
