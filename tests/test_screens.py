"""اختبارات سجل دقة الشاشات (H-SCREENS). لا شبكة."""
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import screens as sc

NY = ZoneInfo("America/New_York")


def test_readings_from_all_screens():
    a = {"rows": [("الاتجاه اليومي", 1, 0, ""), ("الجدران", 0, 0, ""), ("زخم 30 دقيقة", 0, 1.0, "")], "side": "PUT",
         "shock": {"level": 3}}
    flow = pd.DataFrame({"net_delta": [5.0, -3.0], "call_prem": [1.0, 1.0], "put_prem": [4.0, 0.5], "flip": [7400, 7400], "spot": [7410, 7390]})
    r = sc.readings(a, flow, {"total": -4, "adx": 20.0}, "CALL", 1, None)
    assert r["الاتجاه اليومي"] == 1 and r["الجدران"] == 0 and r["زخم 30 دقيقة"] == -1 and r["خلاصة بوت SPX"] == -1
    assert r["صدمة الأخبار"] == -1 and r["صافي الدلتا"] == -1 and r["علاوة الكول/البوت 30د"] == -1 and r["موقع نقطة الانقلاب"] == -1
    assert r["محرك MGT"] == -1 and r["أطراف النطاق"] == 1 and r["المتداول الذكي"] == 1 and "الصقر" not in r
    assert sc.readings(eng={"total": -4, "adx": 10.0})["محرك MGT"] == 0
    assert sc.readings(fade="none")["أطراف النطاق"] == 0


def test_recent_side():
    now = datetime(2026, 11, 9, 11, 0, tzinfo=NY)
    df = pd.DataFrame({"time": ["2026-11-09T10:50-05:00"], "side": ["PUT"], "action": ["شراء"]})
    assert sc.recent_side(df, now, 15, ok=lambda r: r["action"] == "شراء") == -1
    assert sc.recent_side(df, now, 5) is None
    assert sc.recent_side(df.assign(action="انتظار"), now, 15, ok=lambda r: r["action"] == "شراء") == 0


def test_score_and_day_type():
    i = pd.date_range("2026-11-09 09:30", periods=25, freq="5min", tz=NY)
    close = pd.Series(np.linspace(7400, 7424, 25), index=i)
    bars = pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1, "Close": close})
    log = pd.DataFrame({"time": ["2026-11-09T09:30-05:00"] * 3, "S": 7400, "screen": ["صاعدة", "هابطة", "محايدة"], "dir": [1, -1, 0]})
    s = sc.score(log, bars).set_index("screen")
    assert s.loc["صاعدة", "hit30"] == 1.0 and s.loc["هابطة", "hit30"] == 0.0 and "محايدة" not in s.index
    assert s.loc["صاعدة", "m30"] > 0 and s.loc["هابطة", "mclose"] < 0
    assert sc.day_type(bars)[0] == "اتجاه"
    flat = bars.assign(Close=7400.0, High=7420.0, Low=7380.0)
    assert sc.day_type(flat)[0] == "تذبذب"


def test_day_text_short():
    s = pd.DataFrame({"screen": list("abcde"), "n": [10, 10, 10, 10, 2], "hit30": [0.9, 0.7, 0.5, 0.2, 1.0],
                      "m30": 0.0, "hit60": 0.5, "mclose": 0.0})
    t = sc.day_text("2026-11-09", "اتجاه", 0.01, s, s.assign(day="2026-11-09"))
    assert "a 90% (10)" in t and "d 20% (10)" in t and "e 100%" not in t.split("📊")[0] and t.count("\n") <= 7



def test_panel_text_latest_reading_with_accuracy():
    now = datetime(2026, 11, 9, 11, 0, tzinfo=NY)
    log = pd.DataFrame({"time": ["2026-11-09T10:50-05:00"] * 2 + ["2026-11-09T10:55-05:00"] * 3, "S": 7400,
                        "screen": ["أ", "ب", "أ", "ب", "ج"], "dir": [1, 1, -1, 1, 0]})
    cum = pd.DataFrame({"screen": ["أ", "أ"], "n": [10, 30], "hit30": [0.4, 0.8], "day": ["2026-11-06", "2026-11-07"]})
    t = sc.panel_text(log, cum, now)
    assert "صعود 1 · هبوط 1 · محايد 1" in t and "(10:55)" in t
    assert "- أ: هبوط | دقة 70% من 40 قراءة في 2 يوم" in t and "- ج: محايد | لا سجل بعد" in t
    assert sc.panel_text(log, cum, now.replace(hour=12)) == "" and sc.panel_text(None, None, now) == ""



def test_night_window_and_readings():
    assert sc.in_night(datetime(2026, 11, 8, 18, 0, tzinfo=NY)) and sc.in_night(datetime(2026, 11, 9, 9, 29, tzinfo=NY))
    assert not sc.in_night(datetime(2026, 11, 9, 9, 30, tzinfo=NY)) and not sc.in_night(datetime(2026, 11, 13, 19, 0, tzinfo=NY))
    assert not sc.in_night(datetime(2026, 11, 7, 3, 0, tzinfo=NY))                     # السبت
    ch = {"أوروبا": -0.008, "آسيا": 0.02, "الدولار": 0.003, "VIX": 0.01, "الذهب": 0.0005, "البتكوين": None}
    r = sc.night_readings(7400, 7450, 7410, {"total": -4, "adx": 22.0}, ch, datetime(2026, 11, 9, 4, 0, tzinfo=NY))
    assert r["ES منذ التسوية"] == -1 and r["زخم ES 60د"] == -1 and r["محرك MGT ليلي"] == -1
    assert r["أوروبا"] == -1 and r["آسيا"] == 1 and r["الدولار"] == -1 and r["VIX"] == 0 and r["الذهب"] == 0 and "البتكوين" not in r
    late = sc.night_readings(changes=ch, now=datetime(2026, 11, 8, 22, 0, tzinfo=NY))
    assert "أوروبا" not in late and late["آسيا"] == 1


def test_card_and_night_line():
    rd = {"ES منذ التسوية": -1, "أوروبا": -1, "آسيا": 1, "الدولار": -1, "VIX": 0}
    assert sc.consensus(rd) == (-1, 1, 3)
    t = sc.card_text(rd, -0.006, "2026-11-09")
    assert "**هبوط**" in t and "1 صعود · 3 هبوط من 5" in t and "-0.60%" in t and "أوروبا ↓" in t and "الدولار ↑" in t and t.count("\n") <= 3
    pre = {"day": "2026-11-09", "dir": -1, "up": 1, "dn": 3, "es_ch": -0.006}
    assert sc.night_line(pre, "2026-11-09").startswith("## قراءة الليل (09:00): هبوط") and sc.night_line(pre, "2026-11-10") == ""
