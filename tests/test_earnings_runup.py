"""اختبارات انجراف ما قبل الأرباح (H-PAPER R3). لا شبكة."""
from datetime import date, datetime

import pandas as pd

import earnings_runup as er


def test_trading_days_and_exit_day():
    assert er.trading_days_between(date(2026, 11, 4), date(2026, 11, 11)) == 5     # الخميس..الأربعاء
    assert er.exit_day(pd.Timestamp("2026-11-11 16:05")) == date(2026, 11, 11)      # بعد الإغلاق: يوم الإعلان
    assert er.exit_day(pd.Timestamp("2026-11-16 07:00")) == date(2026, 11, 13)      # قبل افتتاح الاثنين: الجمعة


def test_reaction_day_and_expiry_after_d0():
    assert er.reaction_day(date(2026, 11, 11)) == date(2026, 11, 12)
    assert er.reaction_day(date(2026, 11, 13)) == date(2026, 11, 16)                 # الجمعة → الاثنين
    opts = ["2026-11-06", "2026-11-13", "2026-11-20"]
    assert er.pick_expiry(opts, date(2026, 11, 12)) == "2026-11-13"                  # أول جمعة بعد D0
    assert er.pick_expiry(opts, date(2026, 11, 13)) == "2026-11-13"                  # D0 نفسه جمعة
    assert er.pick_expiry(opts, date(2026, 11, 16)) == "2026-11-20"
    assert er.pick_expiry(opts, date(2026, 11, 23)) is None


def test_pick_call_delta_cost_liquidity():
    calls = pd.DataFrame([
        {"strike": 95.0, "bid": 6.0, "ask": 6.3, "openInterest": 500, "impliedVolatility": 0.4},   # دلتا عالية
        {"strike": 102.0, "bid": 2.0, "ask": 2.2, "openInterest": 500, "impliedVolatility": 0.4},
        {"strike": 103.0, "bid": 1.6, "ask": 1.8, "openInterest": 50, "impliedVolatility": 0.4},   # سيولة ضعيفة
        {"strike": 120.0, "bid": 0.05, "ask": 0.10, "openInterest": 900, "impliedVolatility": 0.4},  # رخيص جداً
    ])
    k = er.pick_call(calls, 100.0, 9 / 365)
    assert k["strike"] == 102.0 and 0.30 <= k["delta"] <= 0.60 and abs(k["cost"] - 220.0) < 1e-9
    assert er.pick_call(calls.iloc[[3]], 100.0, 9 / 365) is None


def test_halted_rules():
    mk = lambda pnl: pd.DataFrame({"status": ["وقف" if p < 0 else "مال السوق" for p in pnl], "pnl_usd": [str(p) for p in pnl]})
    assert er.halted(mk([-120] * 5))
    assert er.halted(mk([-300, 100, -350]))
    assert not er.halted(mk([-120] * 4 + [150]))


def test_track_house_money(capsys):
    row = {c: "" for c in er.COLS}
    row.update(ticker="AMD", strike="500", occ="X", entry="4.00", status="مفتوحة", exit_day="2026-11-11", d0="2026-11-12")
    log = pd.DataFrame([row, dict(row, occ="Y"), dict(row, occ="Z")])
    q = {"X": 8.2, "Y": 2.3, "Z": 4.4}
    n = datetime(2026, 11, 10, 12, 0, tzinfo=er.NY)
    out = er.track(n, log, True, quote=lambda o: q[o])
    assert list(out.status) == ["نصف", "وقف", "مفتوحة"]
    assert out.at[0, "pnl_usd"] == "" and abs(float(out.at[1, "pnl_usd"]) - 2 * -170.0) < 1e-9   # الوقف على العقدين
    out = er.track(datetime(2026, 11, 11, 15, 35, tzinfo=er.NY), out, True, quote=lambda o: q[o])
    assert out.at[2, "status"] == "إغلاق" and abs(float(out.at[2, "pnl_usd"]) - 80.0) < 1e-9       # لم يتضاعف: يُباع الاثنان
    assert out.at[0, "status"] == "نصف"                                                            # الثاني يعبر الإعلان
    q["X"] = 1.0                                                                                   # انهار بعد الإعلان (−75%)
    out = er.track(datetime(2026, 11, 12, 15, 35, tzinfo=er.NY), out, True, quote=lambda o: q[o])
    assert out.at[0, "status"] == "مال السوق" and abs(float(out.at[0, "pnl_usd"]) - 100.0) < 1e-9  # 400 − 300
    o = capsys.readouterr().out
    assert "💰" in o and "✅" in o and "🛑" in o


def test_entry_message_text():
    r = {"ticker": "AMD", "earn_ts": "2026-11-11T16:05:00", "exit_day": "2026-11-11", "d0": "2026-11-12", "strike": "500",
         "exp": "2026-11-13", "delta": "0.45", "entry": "4.00"}
    m = er.entry_message(r)
    assert "📅" in m and "$800" in m and "+100%" in m and "مال السوق" in m and "تجريبية" in m and "2026-11-12" in m
