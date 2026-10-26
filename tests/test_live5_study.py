"""اختبارات H-LIVE5: منع التكرار، الإيقاع، الحركة اللاحقة، دقة الإعادة، والتقرير. بلا شبكة."""
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import live5_study as ls  # noqa: E402

NY = ZoneInfo("America/New_York")


def row(h, m, A=None, B=None, C=None, T=None, P=7000.0, day=date(2026, 10, 27)):
    return {"t": datetime(day.year, day.month, day.day, h, m, tzinfo=NY), "day": day, "P": P, "A": A, "B": B, "C": C, "T": T or B, "status": "ok"}


def test_signals_apply_cadence_and_90_minute_repeat_guard():
    up, down, flat = (5.0, 1.0, 7000.0), (1.0, 5.0, 7000.0), (2.0, 1.0, 7000.0)
    rows = [row(10, 5, A=up), row(10, 10, A=up), row(10, 35, A=up), row(11, 5, A=down), row(11, 35, A=up), row(12, 5, A=flat)]
    s30 = ls.signals(rows, "A", True)
    assert [(e["t"].strftime("%H:%M"), e["side"]) for e in s30] == [("10:05", 1), ("11:05", -1), ("11:35", 1)]
    s5 = ls.signals(rows, "A", False)
    assert len(s5) == 3 and s5[0]["t"].minute == 5
    nxt = [row(10, 5, A=up, day=date(2026, 10, 28))]
    assert len(ls.signals(rows + nxt, "A", True)) == 4          # يوم جديد يصفّر منع التكرار


def test_forward_moves_are_signed_by_side():
    ev = [{"t": datetime(2026, 10, 27, 10, 5, tzinfo=NY), "day": date(2026, 10, 27), "side": -1, "P": 100.0}]
    price = {datetime(2026, 10, 27, 10, 30, tzinfo=NY): 99.0, datetime(2026, 10, 27, 11, 0, tzinfo=NY): 101.0}
    out = ls.forward(ev, price)
    assert out[0]["f30"] == pytest.approx(0.01) and out[0]["f60"] == pytest.approx(-0.01)


def test_accuracy_counts_agreement_on_signal_checks_and_price_error():
    rows = [row(10, 5, B=(5, 1, 7000.0), T=(5, 1, 7000.7)), row(10, 10, B=(5, 1, 7000.0), T=(1, 5, 7000.0)), row(10, 15, B=(2, 1, 7000.0), T=(2, 1, 7000.0))]
    a = ls.accuracy(rows)
    assert a["sig_checks"] == 2 and a["agree"] == pytest.approx(0.5) and a["price_err"] == pytest.approx(0.0, abs=1e-4)


def test_report_states_preregistered_decision():
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(10)]
    rows = [row(10, 5, A=(5, 1, 7000.0), B=(5, 1, 7000.0), C=(5, 1, 7000.0), day=d) for d in days]
    ev = {k: [{"day": d, "t": rows[0]["t"], "side": 1, "P": 1.0, "f30": 0.001 * (i % 3 - 1), "f60": 0.0} for i, d in enumerate(days)] for k in "ACB"}
    L, build = ls.report(rows, ev, {"checks": 10, "sig_checks": 10, "agree": 0.99, "price_err": 0.0001, "fallback": 0, "total": 10}, "تجربة")
    text = "\n".join(L)
    assert build is True and "H-LIVE5a: تحققت" in text and "لا ضرر" in text
    L, build = ls.report(rows, ev, {"checks": 10, "sig_checks": 10, "agree": 0.80, "price_err": 0.0001, "fallback": 0, "total": 10}, "تجربة")
    assert build is False and "لم تتحقق" in "\n".join(L)
