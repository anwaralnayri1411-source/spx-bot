"""اختبارات قاعدة عقد SPY الجديدة (27/10/2026): اليوم التالي، أقرب دلتا إلى 0.45 بسعر ≤ الحد، ثم حتى 0.30، وإلا لا عقد؛
وتنبيه التآكل، وتنبيه آخر 30 دقيقة يوم الانتهاء فقط، وتاريخ الانتهاء لكل عقد. بلا شبكة."""
import os
import sys
from datetime import date, datetime

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import options_bot as ob  # noqa: E402

S, T = 700.0, 1.0 / 365


def chain(rows):
    return pd.DataFrame([{"strike": k, "bid": b, "ask": a, "openInterest": 1000, "volume": 500, "impliedVolatility": 0.18} for k, b, a in rows])


def delta(k):
    return ob.abs_delta(S, k, T, 0.18, "CALL")


def test_picks_delta_closest_to_045_under_the_price_cap():
    ks = [698, 700, 701, 702, 704, 706]
    df = chain([(k, round(max(S - k, 0) + 6.0 - 0.9 * (k - 698), 2) - 0.05, round(max(S - k, 0) + 6.0 - 0.9 * (k - 698), 2)) for k in ks])
    out = ob.choose_spy("CALL", df, S, T, 18)
    assert len(out) == 1
    target = min((k for k in ks if delta(k) >= 0.30 and 100 <= 100 * df[df.strike == k].ask.iloc[0] <= ob.CONTRACT_MAX_USD),
                 key=lambda k: abs(delta(k) - 0.45))
    assert out[0]["strike"] == target and abs(out[0]["delta"] - 0.45) < 0.2


def test_moves_further_out_when_near_contracts_cost_too_much_and_stops_at_delta_030():
    df = chain([(700, 4.9, 5.0), (701, 4.4, 4.5), (702, 3.4, 3.5), (705, 2.4, 2.5)])     # 700 و701 فوق 400$
    assert delta(702) >= 0.30 > delta(705)
    out = ob.choose_spy("CALL", df, S, T, 18)
    assert [k["strike"] for k in out] == [702]
    far = chain([(700, 4.9, 5.0), (720, 1.4, 1.5)])                                    # الوحيد ضمن السعر دلتاه < 0.30
    assert ob.choose_spy("CALL", far, S, T, 18) == []


def test_rejects_wide_spread_and_missing_quotes():
    ob.REJ.clear()
    df = chain([(700, 2.5, 3.5), (701, 0.0, 3.0)])
    assert ob.choose_spy("CALL", df, S, T, 18) == []
    assert ob.REJ.get("SPY: فرق الشراء/البيع واسع") == 1 and ob.REJ.get("SPY: بلا عرض/طلب") == 1


def test_next_trading_day_skips_weekend_and_holidays():
    assert ob.next_trading_day(date(2026, 10, 30)) == date(2026, 11, 2)
    assert ob.next_trading_day(date(2026, 11, 25)) == date(2026, 11, 27)


def pos(exp="2026-10-28", side="CALL", entry_spot=700.0, t="2026-10-27T10:05"):
    return {"id": "SPY:CALL:700:" + exp, "sym": "SPY", "root": "SPY", "side": side, "strike": 700.0, "exp": exp, "entry": 3.0, "qty": 1,
            "time": t, "entry_spot": entry_spot, "status": "open", "peak": 0.0, "alerts": {}}


def n_at(h, m, d=27):
    return datetime(2026, 10, d, h, m, tzinfo=ob.NY)


def test_decay_advice_fires_once_when_loss_is_from_time_not_direction():
    p = pos()
    _, new = ob.eval_position(p, n_at(11, 30), 2.3, 700.05)            # −23% والسعر لم يتحرك ضدنا
    assert [k for k, _ in new] == ["decay"] and "ننصح بالخروج" in new[0][1]
    _, again = ob.eval_position(p, n_at(11, 45), 2.2, 700.0)
    assert again == []
    q = pos()
    _, new = ob.eval_position(q, n_at(11, 30), 2.3, 698.0)              # الخسارة من الاتجاه (−0.29%) → لا نصيحة تآكل
    assert new == []
    r = pos(exp="2026-10-27")                                            # عقد اليوم نفسه: لا نصيحة تآكل
    assert ob.eval_position(r, n_at(11, 30), 2.3, 700.0)[1] == []


def test_last_30_minutes_alert_only_on_expiry_day():
    p = pos()
    assert [k for k, _ in ob.eval_position(p, n_at(15, 35), 3.1, 700.5)[1]] == []
    p2 = pos()
    p2["time"] = "2026-10-27T10:05"
    assert [k for k, _ in ob.eval_position(p2, n_at(15, 35, d=28), 3.1, 700.5)[1]] == ["exp_final"]


def test_positions_use_each_contracts_own_expiry(monkeypatch, tmp_path):
    monkeypatch.setattr(ob, "POS_FILE", str(tmp_path / "p.json"))
    monkeypatch.setattr(ob, "_live_entry", lambda p: None)
    a = {"side": "CALL", "exp": "2026-10-27", "c": {"S": 7000.0},
         "contracts": [{"sym": "^SPX", "strike": 7010.0, "price": 2.0, "cost": 200.0, "spot": 7000.0, "contractSymbol": "SPXW"},
                       {"sym": "SPY", "strike": 700.0, "price": 3.0, "cost": 300.0, "spot": 700.0, "exp": "2026-10-28"}]}
    ob.add_positions(n_at(10, 5), a)
    P = ob.load_positions()["positions"]
    assert {p["sym"]: p["exp"] for p in P} == {"SPX": "2026-10-27", "SPY": "2026-10-28"}
    assert any(p["id"] == "SPY:CALL:700:2026-10-28" for p in P)
