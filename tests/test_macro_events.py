"""اختبارات قائمة الأحداث المشتركة. لا شبكة."""
from datetime import date

import macro_events as me
import options_bot as ob


def test_shared_list_used_by_spx_bot():
    assert ob.EVENTS is me.EVENTS and me.EVENTS["2026-11-13"].endswith("PPI")
    assert (date(2026, 11, 13), "تقرير أسعار المنتجين PPI") in ob.upcoming_events(date(2026, 11, 9))


def test_warning_line():
    w = me.warning_line(date(2026, 11, 9), date(2026, 11, 13))
    assert w.startswith("📰") and "CPI الثلاثاء 10/11 08:30" in w and "PPI الجمعة 13/11 08:30" in w
    assert me.warning_line(date(2026, 11, 14), date(2026, 11, 20)) == ""
    assert "14:00" in me.warning_line(date(2026, 12, 9), date(2026, 12, 9))
    assert [d.day for d, _ in me.between(date(2026, 11, 10), date(2026, 11, 10))] == [10]


def test_stocks_bot_shows_warning_only(monkeypatch):
    from datetime import datetime
    import stocks_bot as sb
    monkeypatch.setattr(ob, "now_ny", lambda: datetime(2026, 11, 9, 10, 0, tzinfo=ob.NY))
    assert "CPI الثلاثاء 10/11" in sb.event_warning({"exp": "2026-11-13"})
    assert sb.event_warning({"exp": "2026-11-09"}) == ""
    assert sb.event_warning({}) == ""
