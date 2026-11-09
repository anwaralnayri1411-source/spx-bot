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
