"""اختبارات ظل MGT (H-MGTCLONE). لا شبكة."""
from datetime import datetime

import numpy as np
import pandas as pd

import mgt_shadow as ms


def test_session_and_trade_date():
    assert ms.session(datetime(2026, 11, 5, 10, 0, tzinfo=ms.NY)) == "rth"
    assert ms.session(datetime(2026, 11, 4, 21, 39, tzinfo=ms.NY)) == "gth"           # مساء الأربعاء
    assert ms.session(datetime(2026, 11, 5, 3, 0, tzinfo=ms.NY)) == "gth"
    assert ms.session(datetime(2026, 11, 6, 21, 0, tzinfo=ms.NY)) is None             # مساء الجمعة
    assert ms.session(datetime(2026, 11, 8, 20, 30, tzinfo=ms.NY)) == "gth"           # مساء الأحد
    assert ms.trade_date(datetime(2026, 11, 4, 21, 39, tzinfo=ms.NY)).isoformat() == "2026-11-05"
    assert ms.trade_date(datetime(2026, 11, 6, 20, 30, tzinfo=ms.NY)).isoformat() == "2026-11-09"
    assert ms.trade_date(datetime(2026, 11, 5, 11, 0, tzinfo=ms.NY)).isoformat() == "2026-11-05"


def test_murrey_grid_matches_mgt_levels():
    assert ms.octave(120) == 156.25 and ms.octave(200) == 312.5
    lv = [round(x, 2) for x, _ in ms.mml_lines(7700, 156.25)]
    for v in (7734.38, 7695.31, 7675.78, 7714.84, 7753.91, 7792.97, 7832.03, 7617.19):  # مستويات تقاريرهم
        assert v in lv
    assert ms.crossed_line(7690, 7697, ms.mml_lines(7700, 156.25), "CALL")[0] == 7695.3125
    assert ms.crossed_line(7697, 7690, ms.mml_lines(7700, 156.25), "PUT")[0] == 7695.3125
    assert ms.crossed_line(7690, 7692, ms.mml_lines(7700, 156.25), "CALL") is None


def _m1(trend=0.5, n=300):
    idx = pd.date_range("2026-11-05 09:30", periods=n, freq="1min", tz=ms.NY)
    c = 7650 + trend * np.arange(n) + np.sin(np.arange(n)) * 0.3
    return pd.DataFrame({"o": c - 0.2, "h": c + 0.6, "l": c - 0.6, "c": c, "v": 100.0}, index=idx)


def test_decide_trend_up_down_flat():
    s, sc, d = ms.decide(_m1(0.5), 7650, 7700)
    assert s == "CALL" and sc >= 3 and d["M5"] == 1 and d["adx"] > 15
    s, sc, d = ms.decide(_m1(-0.5), 7650, 7600)
    assert s == "PUT" and sc <= -3
    s, sc, d = ms.decide(_m1(0.0), 7650, 7650)
    assert s is None


def test_pick_contract_and_ladder():
    ch = [{"side": "CALL", "strike": 7720.0, "ask": 5.0}, {"side": "CALL", "strike": 7730.0, "ask": 3.6},
          {"side": "CALL", "strike": 7740.0, "ask": 2.2}, {"side": "PUT", "strike": 7690.0, "ask": 3.4},
          {"side": "PUT", "strike": 7710.0, "ask": 3.5}]                                 # 7710 PUT داخل المال
    assert ms.pick_contract(ch, "CALL", 7700)["strike"] == 7730.0
    assert ms.pick_contract(ch, "PUT", 7700)["strike"] == 7690.0
    st = {"open": [{"side": "CALL", "tdate": "2026-11-05", "t": "2026-11-05T10:00", "S": 7700}], "closed": []}
    assert not ms.may_ladder(st, "CALL", 7703, "2026-11-05") and ms.may_ladder(st, "CALL", 7705, "2026-11-05")
    assert ms.may_ladder(st, "PUT", 7700, "2026-11-05")


def test_path_and_results():
    e = {"entry": 3.5, "peak": 3.5, "t_stop": None}
    n = datetime(2026, 11, 5, 10, 0, tzinfo=ms.NY)
    for b in (4.0, 5.5, 7.2, 12.0, 5.0):
        ms.update_path(e, b, b + 0.2, n)
    r = ms.results(e)
    assert e["t_tp50"] and e["half"] and e["trail_exit"] == 5.0
    assert r["usd_mgt"] == round((12.1 - 3.5) * 100, 2) and r["usd_real"] == 175.0
    assert r["usd_house"] == round(175 + 0.5 * (5.0 - 3.5) * 100, 2)
    e2 = {"entry": 3.5, "peak": 3.5, "t_stop": None}
    for b in (3.0, 1.7, 6.0):
        ms.update_path(e2, b, b + 0.2, n)
    r2 = ms.results(e2)
    assert e2["t_stop"] and r2["usd_real"] == -175.0 and r2["usd_mgt"] == round((6.1 - 3.5) * 100, 2)   # حسابهم من القمة رغم الوقف


def test_live_m1_prefers_live_ticks():
    hist = _m1(0.1, 120)
    t0 = hist.index[100]
    idx = pd.DatetimeIndex([t0 + pd.Timedelta(seconds=s) for s in (0, 15, 30, 60, 75, 90)])
    ticks = pd.DataFrame({"mark": [7700, 7703, 7699, 7701, 7705, 7704]}, index=idx)
    m = ms.live_m1(hist, ticks)
    assert m.index[-1] == t0 + pd.Timedelta(minutes=1) and len(m) == 102     # 100 شمعة ياهو + دقيقتان لحظيتان
    r = m.loc[t0]
    assert r["o"] == 7700 and r["h"] == 7703 and r["l"] == 7699 and r["c"] == 7699
    assert ms.live_m1(hist, None) is hist


def test_report_groups_entries_and_leads_with_real_result():
    base = {"tdate": "2026-11-05", "strike": 7600.0, "entry": 3.0, "peak": 9.0, "last_bid": 8.0}
    st = {"open": [], "closed": [
        dict(base, t="2026-11-05T02:00:00-05:00", sess="gth", side="CALL", S=7680.0, t_stop="04:00", peak=3.2, last_bid=0.0),
        dict(base, t="2026-11-05T03:00:00-05:00", sess="gth", side="CALL", S=7690.0, t_stop="05:00", peak=3.2, last_bid=0.0),
        dict(base, t="2026-11-05T09:40:00-05:00", sess="rth", side="PUT", S=7640.0, t_tp50="09:50"),
    ]}
    txt = ms.report(st, "2026-11-05")
    assert "النتيجة الواقعية: -150$" in txt and "3 عقداً: 1 رابحة · 2 خاسرة" in txt
    assert "🌙 ليلاً 02:00–03:00: **2 CALL**" in txt and "🛑 2 وقف = **-300$**" in txt
    assert "☀️ الجلسة 09:40: **1 PUT**" in txt and "✅ 1 هدف = **+150$**" in txt
    assert txt.index("النتيجة الواقعية") < txt.index("سقف نظري")



def test_price_band_follows_vix():
    assert ms.price_band(None) == (2.90, 4.00, 3.50) and ms.price_band(17) == (2.90, 4.00, 3.50)
    lo, hi, t = ms.price_band(21.25)
    assert abs(t - 4.375) < 0.01 and abs(lo - 3.625) < 0.01 and abs(hi - 5.0) < 0.01
    assert ms.price_band(50)[2] == 5.6 and ms.price_band(10)[2] == 2.8
    ch = [{"side": "PUT", "strike": 7400.0, "ask": 4.4, "bid": 4.3}, {"side": "PUT", "strike": 7410.0, "ask": 3.5, "bid": 3.4}]
    assert ms.pick_contract(ch, "PUT", 7450, 21.25)["strike"] == 7400.0 and ms.pick_contract(ch, "PUT", 7450)["strike"] == 7410.0


def test_fade_side_and_spacing():
    i = pd.date_range("2026-11-06 09:30", periods=3, freq="1min", tz=ms.NY)
    b = pd.DataFrame({"h": [7444.0, 7420, 7410], "l": [7400.0, 7380, 7390], "c": [7420.0, 7400, 7400]}, index=i)
    assert ms.fade_side(b, 7430, 0.002) == "PUT" and ms.fade_side(b, 7430, 0.001) is None
    assert ms.fade_side(b, 7390, -0.002) == "CALL" and ms.fade_side(b, 7410, -0.002) is None
    assert ms.fade_side(b.assign(h=7390.0), 7385, -0.002) is None                      # نطاق ضيق
    n = datetime(2026, 11, 6, 11, 0, tzinfo=ms.NY)
    st = {"last_fade": {"PUT": "2026-11-06T10:50-05:00"}}
    assert not ms.fade_ok(st, "PUT", n) and ms.fade_ok(st, "PUT", n.replace(minute=5)) and ms.fade_ok(st, "CALL", n)


def test_fade_quick_exit_results():
    n = datetime(2026, 11, 6, 11, 0, tzinfo=ms.NY)
    base = {"entry": 4.0, "peak": 4.0, "t": n.isoformat(timespec="minutes"), "trigger": "fade"}
    e = dict(base)
    for m, b in ((5, 4.5), (10, 5.3), (20, 9.0)):
        ms.update_path(e, b, b + 0.1, n.replace(minute=m))
    assert e["t_tp30"] == "11:10" and ms.results(e)["usd_real"] == 120.0 and ms.outcome(e) == "هدف"
    e = dict(base)
    for m, b in ((10, 3.5), (46, 3.2), (50, 1.0)):
        ms.update_path(e, b, b + 0.1, n.replace(minute=m))
    assert e["tx_bid"] == 3.2 and e["t_tx"] == "11:46" and round(ms.results(e)["usd_real"]) == -80 and ms.outcome(e) == "إغلاق"
    e = dict(base)
    ms.update_path(e, 1.9, 2.0, n.replace(minute=5))
    assert ms.results(e)["usd_real"] == -200.0 and ms.outcome(e) == "وقف"
    trend = {"entry": 4.0, "peak": 4.0, "t": n.isoformat(timespec="minutes"), "trigger": "mml"}
    ms.update_path(trend, 5.3, 5.4, n.replace(minute=5))
    assert "t_tp30" not in trend and abs(ms.results(trend)["usd_real"] - 130.0) < 1e-6           # الاتجاه: هدف +50% لم يبلغ بعد
