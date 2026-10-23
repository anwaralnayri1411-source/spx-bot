"""فحص الخادم من GitHub: العطل، نافذة الجلسة، ومتى يُرسل التنبيه."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy"))
import server_alive_check as s  # noqa: E402


def test_holidays_read_from_bot():
    assert "2026-11-26" in s.holidays()


def test_session_window():
    assert s.in_session(datetime(2026, 10, 23, 11, 0, tzinfo=s.NY))
    assert not s.in_session(datetime(2026, 10, 23, 9, 50, tzinfo=s.NY))
    assert not s.in_session(datetime(2026, 10, 24, 11, 0, tzinfo=s.NY))      # السبت
    assert not s.in_session(datetime(2026, 11, 26, 11, 0, tzinfo=s.NY))      # عيد الشكر


def _run(monkeypatch, age_min):
    sent = []
    monkeypatch.setattr(s, "in_session", lambda n: True)
    t = None if age_min is None else datetime.now(timezone.utc) - timedelta(minutes=age_min)
    monkeypatch.setattr(s, "last_server_commit", lambda: (t, "x"))
    monkeypatch.setattr(s, "send", sent.append)
    s.main()
    return sent


def test_alert_only_between_60_and_120_minutes(monkeypatch):
    assert _run(monkeypatch, 20) == []
    assert len(_run(monkeypatch, 75)) == 1
    assert _run(monkeypatch, 150) == []          # لا تكرار بعد التنبيه الأول/الثاني
    assert len(_run(monkeypatch, None)) == 1
