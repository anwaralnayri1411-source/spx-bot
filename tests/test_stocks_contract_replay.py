"""اختبارات استعادة عقود المحترفين للشركات: الانتهاء الشهري، اختيار السترايك بالدلتا، محاكاة قواعد الخروج، والتقرير. بيانات مصطنعة."""
import os
import sys
from datetime import date, datetime, timedelta

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import options_bot as ob  # noqa: E402
import stocks_contract_replay as sr  # noqa: E402

NY = sr.NY


def test_monthly_expiry_is_the_third_friday_between_28_and_50_days():
    e = sr.monthly_expiry(date(2026, 10, 15))
    assert e == date(2026, 11, 20) and e.weekday() == 4 and 28 <= (e - date(2026, 10, 15)).days <= 50
    assert sr.monthly_expiry(date(2026, 10, 25)) == date(2026, 12, 18)          # الشهري الأقرب أقل من 28 يوماً فننتقل للتالي
    assert sr.third_friday(2026, 11) == date(2026, 11, 20)


def test_occ_symbol_format():
    assert sr.occ("CVX", date(2026, 11, 20), "CALL", 240.0) == "CVX261120C00240000"
    assert sr.occ("V", date(2026, 11, 20), "PUT", 412.5) == "V261120P00412500"


def test_iv_calibration_recovers_the_volatility_used_to_price():
    price = ob.bs_price(215.0, 220.0, 0.03, 0.42, "CALL")
    iv = sr.calibrate_iv(215.0, 220.0, 0.03, price, "CALL", ob.bs_price)
    assert abs(iv - 0.42) < 1e-3
    assert sr.calibrate_iv(215.0, 220.0, 0.03, 999.0, "CALL", ob.bs_price) is None


def test_strike_for_delta_hits_the_target_for_calls_and_puts():
    for side in ("CALL", "PUT"):
        k = sr.strike_for_delta(100.0, 0.1, 0.3, side, 0.70, ob.abs_delta)
        assert abs(ob.abs_delta(100.0, k, 0.1, 0.3, side) - 0.70) < 0.01
    kc = sr.strike_for_delta(100.0, 0.1, 0.3, "CALL", 0.70, ob.abs_delta)
    kp = sr.strike_for_delta(100.0, 0.1, 0.3, "PUT", 0.70, ob.abs_delta)
    assert kc < 100.0 < kp                                                      # دلتا 0.70 = داخل المال


def test_candidate_strikes_cover_several_grids_around_the_continuous_strike():
    c = sr.candidate_strikes(92.3)
    assert 92.0 in c and 92.5 in c and 90.0 in c and 95.0 in c and c == sorted(c)


def bars(start, n, prices, step_min=5):
    idx = [start + timedelta(minutes=step_min * i) for i in range(n)]
    p = list(prices) + [prices[-1]] * (n - len(prices))
    return pd.DataFrame({"o": p, "h": p, "l": p, "c": p}, index=pd.DatetimeIndex(idx))


T0 = datetime(2026, 10, 20, 10, 0, tzinfo=NY)


def test_entry_uses_first_bar_after_the_recommendation_and_skips_distant_ones():
    near = bars(T0 + timedelta(minutes=10), 5, [2.0])
    assert sr.first_bar_after(near, T0) == near.index[0]                       # بعد 10 دقائق: داخل نافذة 30 دقيقة
    far = bars(T0 + timedelta(minutes=90), 5, [2.0])
    assert sr.first_bar_after(far, T0) is None                                 # بعد 90 دقيقة: يُهمل (سعر غير عادل)


def test_target_and_stop_follow_the_bot_rule_and_stop_waits_for_minimum_age():
    up = bars(T0, 40, [2.0] * 3 + [4.0])
    v, why, _ = sr.simulate(up, None, T0, "CALL", 100.0, date(2026, 10, 30), "r1")
    assert why == "tp" and v == sr.TP
    down = bars(T0, 40, [2.0] * 2 + [0.5])                                    # هبوط مبكر (قبل 30 د) ثم يبقى منخفضاً
    v, why, t = sr.simulate(down, None, T0, "CALL", 100.0, date(2026, 10, 30), "r1")
    assert why == "stop" and (t - T0).total_seconds() / 60 >= sr.MIN_AGE_MIN


def test_pro_rule_stops_on_the_stock_not_the_contract_price():
    b = bars(T0, 40, [2.0])                                                     # سعر العقد ثابت
    idx = b.index
    stock = pd.Series([100.0] * 10 + [98.0] * 30, index=idx)                    # السهم نزل 2% ضد Call
    v, why, _ = sr.simulate(b, stock, T0, "CALL", 100.0, date(2026, 10, 30), "r2")
    assert why == "stock_stop"
    up = pd.Series([100.0] * 10 + [102.0] * 30, index=idx)
    _, why, _ = sr.simulate(b, up, T0, "CALL", 100.0, date(2026, 10, 30), "r2")
    assert why != "stock_stop"
    _, why, _ = sr.simulate(b, pd.Series([100.0] * 40, index=idx), T0, "PUT", 100.0, date(2026, 10, 30), "r2")
    assert why != "stock_stop"
    _, why, _ = sr.simulate(b, pd.Series([102.0] * 10 + [102.0] * 30, index=idx), T0, "PUT", 100.0, date(2026, 10, 30), "r2")
    assert why == "stock_stop"                                                  # PUT: ارتفاع السهم ضده


def test_time_stop_exits_at_the_last_bar_of_a_day_with_seven_days_or_less_left():
    day = datetime(2026, 10, 23, 15, 40, tzinfo=NY)
    b = bars(day, 6, [2.0])                                                     # 15:40 → 16:05
    _, why, t = sr.simulate(b, None, day - timedelta(minutes=5), "CALL", 100.0, date(2026, 10, 30), "r2")
    assert why == "time" and t.hour == 15 and t.minute == 55
    _, why, _ = sr.simulate(b, None, day - timedelta(minutes=5), "CALL", 100.0, date(2026, 11, 20), "r2")
    assert why == "end"                                                         # بعيد عن الانتهاء: لا وقف زمني


def test_pick_strike_prefers_the_strike_with_real_bars_closest_to_the_target_delta():
    S, T, iv = 100.0, 0.1, 0.3
    exp = date(2026, 11, 20)
    b = bars(T0, 20, [2.0])
    have = {sr.occ("X", exp, "CALL", 90.0): b, sr.occ("X", exp, "CALL", 95.0): b}
    k = sr.pick_strike([85.0, 90.0, 95.0, 100.0], have, "X", exp, "CALL", S, iv, T, 0.70, ob.abs_delta, T0)
    assert k in (90.0, 95.0)
    thin = {sr.occ("X", exp, "CALL", 90.0): b.iloc[:2]}
    assert sr.pick_strike([90.0], thin, "X", exp, "CALL", S, iv, T, 0.70, ob.abs_delta, T0) is None


def test_report_has_sanity_check_and_paired_differences():
    rows = []
    for i, (bot, atm) in enumerate([(-0.4, 0.1), (0.5, 0.3), (-0.4, 0.2)]):
        for v, x in (("bot", bot), ("atm", atm)):
            for r in ("r1", "r2"):
                rows.append({"id": f"p{i}", "variant": v, "rule": r, "pnl": x, "why": "tp" if x > 0 else "stop", "recorded": -0.4 if v == "bot" else None})
    text = "\n".join(sr.report(pd.DataFrame(rows)))
    assert "ضابط" in text and "على السعر (0.50) − عقد البوت" in text and "أفضل في 2 من 3" in text and "المنتهية فقط" in text and "النتيجة نفسها في 2 من 3" in text
    assert "لا نتائج بعد" in "\n".join(sr.report(pd.DataFrame()))


def test_run_end_to_end_with_fake_alpaca_data_replays_the_bot_contract_and_the_alternatives():
    p = {"id": "CVX:CALL:2026-11-20:240", "ticker": "CVX", "side": "CALL", "exp": "2026-11-20", "strike": 240.0, "entry": 1.9,
         "entry_spot": 232.0, "time": "2026-10-20T10:00-04:00", "closed": {"pnl": -0.4}}
    t0 = datetime.fromisoformat(p["time"])

    def fake_fetch(syms, start, end):
        out = {}
        for s in syms:
            if s.startswith("CVX261120C002") or s.startswith("CVX261120C0023") or s.startswith("CVX261120C0022"):
                out[s] = bars(t0, 120, [2.0] * 60 + [1.0] * 60)
        return out

    idx = pd.date_range(t0, periods=120, freq="5min")
    stock = pd.Series([232.0] * 120, index=idx)
    df = sr.run([p], ob, fetch=fake_fetch, get_stock=lambda tk: stock)
    assert {"bot", "atm", "itm"} <= set(df["variant"]) and set(df["rule"]) == {"r1", "r2"}
    bot = df[(df["variant"] == "bot") & (df["rule"] == "r1")].iloc[0]
    assert bot["why"] == "stop" and bot["pnl"] == -sr.STOP and bot["recorded"] == -0.4
    assert "ضابط" in chr(10).join(sr.report(df))


def test_run_skips_positions_it_cannot_calibrate_or_fetch(capsys):
    bad = {"id": "x", "ticker": "X", "side": "CALL", "exp": "2026-11-20", "strike": 100.0, "entry": 999.0, "entry_spot": 100.0,
           "time": "2026-10-20T10:00-04:00"}
    assert sr.run([bad], ob, fetch=lambda *a: {}, get_stock=lambda t: None).empty
    ok = dict(bad, entry=3.0)
    boom = sr.run([ok], ob, fetch=lambda *a: (_ for _ in ()).throw(ConnectionError("x-api-key: secret")), get_stock=lambda t: None)
    assert boom.empty and "secret" not in capsys.readouterr().out


def test_fetch_window_never_ends_in_the_future_or_inside_the_last_16_minutes():
    now = datetime(2026, 10, 23, 14, 0, tzinfo=sr.ZoneInfo("UTC"))
    capped = sr.cap_end(date(2026, 11, 20), now)
    assert capped == now - timedelta(minutes=16)                                # انتهاء في المستقبل: نقصّه إلى الآن−16 د
    past = sr.cap_end(date(2026, 10, 16), now)
    assert past == datetime(2026, 10, 16, 20, 0, tzinfo=sr.ZoneInfo("UTC"))      # انتهاء مضى: إغلاق 16:00 نيويورك
