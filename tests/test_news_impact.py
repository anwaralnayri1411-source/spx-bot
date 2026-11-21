"""H-NEWSIMPACT: سطر الأثر التاريخي (صياغة الميل والحجم) وربطه بالتحذير."""
from datetime import date

import macro_events as m


def test_impact_line_wording(monkeypatch):
    t = {"syms": {"SPY": {"fomc": {"n": 61, "up": 0.48, "mean": -0.1, "abs": 0.8, "ratio": 1.3, "cont": 0.48},
                          "jobs": {"n": 87, "up": 0.62, "mean": 0.1, "abs": 0.6, "ratio": 1.0}},
                  "X": {"jobs": {"n": 10, "up": 0.9, "mean": 1, "abs": 1, "ratio": 1}}}}
    monkeypatch.setattr(m, "_IMPACT", t)
    a = m.impact_line("قرار الفيدرالي (FOMC)", "SPY", "SPX")
    assert "SPX صعد 48%" in a and "لا ميل اتجاهي ثابت" in a and "1.3×" in a and "48%." in a
    assert "ميل صعود" in m.impact_line("تقرير الوظائف (تقريبي)", "SPY")
    assert "عينة صغيرة" in m.impact_line("تقرير الوظائف (تقريبي)", "X")
    assert m.impact_line("تقرير التضخم CPI", "SPY") == "" and m.impact_line("قرار الفيدرالي (FOMC)", "ZZZ") == ""
    w = m.warning_line(date(2026, 12, 8), date(2026, 12, 9), "SPY")
    assert w.startswith("📰") and "\n📊 تاريخياً يوم الفيدرالي" in w
    assert "\n" not in m.warning_line(date(2026, 12, 8), date(2026, 12, 9))


def test_table_file_has_all_symbols():
    m._IMPACT = None
    syms = m._impact_table()["syms"]
    assert {"SPY", "NVDA", "TSLA", "AAPL", "AMZN", "META", "AMD"} <= set(syms) and syms["SPY"]["fomc"]["n"] >= 50
