"""اختبارات انعكاس يوم رد الفعل (H-EARNREV). لا شبكة."""
import os
import sys
from datetime import date, datetime, timedelta

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import earnings_reversal_study as rv  # noqa: E402

D = date(2026, 11, 4)


def _m1(pre, reg):
    """pre: [(سعر، حجم)] من 09:00؛ reg: أسعار دقيقة من 09:30 (o=h=l=c)."""
    rows = []
    t = datetime(2026, 11, 4, 9, 0, tzinfo=rv.NY)
    for p, v in pre:
        rows.append((t, p, p, p, p, v, p)); t += timedelta(minutes=1)
    t = datetime(2026, 11, 4, 9, 30, tzinfo=rv.NY)
    for p in reg:
        rows.append((t, p, p, p, p, 1000, p)); t += timedelta(minutes=1)
    df = pd.DataFrame(rows, columns=["t", "o", "h", "l", "c", "v", "vw"]).set_index("t")
    return df


def test_pm_vwap():
    m = _m1([(100, 1), (110, 3)], [100])
    assert abs(rv.pm_vwap(m) - 107.5) < 1e-9
    assert rv.pm_vwap(_m1([], [100])) is None


def test_signal_amd_like():
    # افتتاح 485 تحت PMV 492، هبوط (تصريف) ثم عبور لأعلى في شمعة 09:35–09:40
    reg = [485, 482, 480, 478, 476] + [480, 485, 490, 495, 499] + [505] * 400
    m = _m1([(492, 10)], reg)
    d, t0, flush = rv.signal(m, rv.pm_vwap(m))
    assert d == 1 and t0.strftime("%H:%M") == "09:40" and flush is True
    assert abs(rv.stock_ret(m, t0, d) - (505 / 505 - 1)) < 1e-9


def test_signal_none_and_late():
    m = _m1([(492, 10)], [485] * 400)                                   # لا عبور
    assert rv.signal(m, 492.0) is None
    m = _m1([(492, 10)], [485] * 70 + [500] * 300)                      # عبور بعد 10:30
    assert rv.signal(m, 492.0) is None
    m = _m1([(480, 10)], [485, 486, 487, 488, 479] + [470] * 300)       # فوق PMV ثم عبور لأسفل
    d, t0, flush = rv.signal(m, 480.0)
    assert d == -1 and t0.strftime("%H:%M") == "09:35" and flush is False


def test_stock_ret_direction():
    m = _m1([(100, 1)], [100] * 30 + [110] * 400)
    t0 = datetime(2026, 11, 4, 10, 0, tzinfo=rv.NY)
    assert abs(rv.stock_ret(m, t0, 1) - 0.0) < 1e-9
    assert abs(rv.stock_ret(m, datetime(2026, 11, 4, 9, 30, tzinfo=rv.NY), -1) - (-0.10)) < 1e-9


def test_opt_exit_and_expiry():
    t = datetime(2026, 11, 4, 10, 0, tzinfo=rv.NY)
    p = [(t + timedelta(minutes=5 * i), v, 5 * i) for i, v in enumerate([0.1, 0.6, -0.5])]
    cut = datetime(2026, 11, 4, 15, 55, tzinfo=rv.NY)
    assert rv.opt_exit(p, cut) == 0.5
    assert rv.opt_exit([(t, -0.35, 0)], cut) == -0.3
    assert rv.opt_exit([(t, 0.2, 0)], cut) == 0.2
    assert rv.expiry_for(date(2026, 11, 4)) == date(2026, 11, 6)       # الأربعاء → الجمعة
    assert rv.expiry_for(date(2026, 11, 5)) == date(2026, 11, 13)      # الخميس → الجمعة التالية


def test_verdict_rules():
    days = [f"2025-{m:02d}-{d:02d}" for m in range(1, 13) for d in (5, 20)]
    good = pd.DataFrame({"sym": "X", "day": days, "e1": [0.01, 0.02] * 12, "e2": [0.05, 0.10] * 12})
    assert rv.verdict(good)[0] == "مدعوم"
    part = good.assign(e2=[-0.2, 0.1] * 12)
    assert rv.verdict(part)[0] == "جزئي"
    bad = good.assign(e1=[-0.01, 0.005] * 12)
    assert rv.verdict(bad)[0] == "غير مدعوم"
    assert "الحكم" in rv.report(good.assign(c1_stock=0.0, c1_opt=0.0, with_gap=1, flush=True, beat=1.0))
