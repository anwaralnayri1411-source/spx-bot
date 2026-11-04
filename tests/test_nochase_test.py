"""اختبارات منع المطاردة (H-NOCHASE). لا شبكة."""
import os
import sys
from datetime import datetime, timedelta

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import nochase_test as nc  # noqa: E402


def test_ext_and_case_4nov():
    em = nc.em_pct(17.9)
    e = nc.ext_of(7702.3, 7635.8, "CALL", em)
    assert 0.7 < e < 0.9                                    # صعود +66 نقطة ≈ 0.77 من الحركة المتوقعة
    assert nc.ext_of(7702.3, 7635.8, "PUT", em) < 0


def test_near_vwap_and_orb():
    em = 0.01
    assert nc.near_vwap(100.1, 100.0, "CALL", em)           # +0.1% ≤ 0.15%
    assert not nc.near_vwap(100.3, 100.0, "CALL", em)
    assert nc.near_vwap(99.0, 100.0, "CALL", em)            # تحت VWAP مقبول
    assert nc.near_vwap(99.9, 100.0, "PUT", em) and not nc.near_vwap(99.7, 100.0, "PUT", em)
    assert nc.orb_break(105, (104, 100), "CALL") and not nc.orb_break(103, (104, 100), "CALL")
    assert nc.orb_break(99, (104, 100), "PUT") and not nc.orb_break(99, None, "PUT")
    assert nc.ret60_ok(0.001, "CALL") and nc.ret60_ok(-0.001, "PUT") and not nc.ret60_ok(None, "CALL")


def _bars(vals, t0):
    idx = [t0 + timedelta(minutes=5 * i) for i in range(len(vals))]
    return pd.DataFrame({"o": vals, "h": vals, "l": vals, "c": vals}, index=idx)


def test_follow_real_rules():
    t0 = datetime(2026, 11, 4, 10, 0, tzinfo=nc.NY)
    e, r, k = nc.follow_real(_bars([2.0, 2.2, 3.2, 1.0], t0), t0)
    assert k == "tp" and r >= 0.5
    e, r, k = nc.follow_real(_bars([2.0, 1.0, 1.0, 1.0, 1.0], t0), t0)
    assert k == "stop" and r < -0.4                         # الوقف بعد 15 دقيقة لا قبلها
    late = datetime(2026, 11, 4, 15, 20, tzinfo=nc.NY)
    assert nc.follow_real(_bars([2.0, 2.1, 2.1], late), late)[2] == "final"
    assert nc.follow_real(_bars([2.0], t0), t0 + timedelta(hours=1)) is None


def test_bh_and_verdict():
    assert nc.bh([0.01, 0.04]) == [True, True] and nc.bh([0.03, 0.5]) == [False, False]
    days = [f"2026-08-{d:02d}" for d in range(1, 29)]
    df = pd.DataFrame({"day": days, "a_usd": [-50.0, -30.0] * 14, "b_usd": [0.0, 10.0] * 14, "ext": 0.6,
                       "c_usd": 0.0, "d_usd": 0.0})
    assert nc.verdict(df)[0] == "مدعوم"
    assert nc.verdict(df.assign(b_usd=df["a_usd"] - 5))[0] == "غير مدعوم"
    assert "الحكم الرئيسي" in nc.report(df)
