"""اختبارات ظل القاعدة C (H-NOCHASE للأمام). لا شبكة."""
from datetime import date, datetime, timedelta

import pandas as pd

import rule_c_shadow as rc

D = date(2026, 11, 4)


def _b5(vals, start=(9, 30), hl=0.0):
    t = datetime(2026, 11, 4, *start, tzinfo=rc.NY)
    idx = [t + timedelta(minutes=5 * i) for i in range(len(vals))]
    return pd.DataFrame({"o": vals, "h": [v + hl for v in vals], "l": [v - hl for v in vals], "c": vals}, index=idx)


def test_orb_and_entry_time():
    b = _b5([100, 101, 102, 101, 100, 99] + [101, 103, 104] + [104] * 60)
    assert rc.orb(b) == (102, 99)
    rec = datetime(2026, 11, 4, 9, 46, tzinfo=rc.NY)
    t = rc.c_entry_time(b, rec, "CALL")
    assert t.strftime("%H:%M") == "10:20"                  # شمعة 10:05–10:10 أُغلقت 103 > 102 ← +10 دقائق
    assert rc.c_entry_time(b, rec, "PUT") is None          # لا كسر لأسفل
    late = datetime(2026, 11, 4, 14, 0, tzinfo=rc.NY)
    assert rc.c_entry_time(b, late, "CALL").strftime("%H:%M") == "14:10"   # التوصية بعد الكسر: أول شمعة بعدها


def test_case_4nov_no_break():
    # 4/11: نطاق أول 30د 7,636–7,705 والسعر جانبي تحته ← لا دخول
    b = _b5([7650, 7682, 7701, 7694, 7688, 7695] + [7700] * 60, hl=3)
    rec = datetime(2026, 11, 4, 9, 46, tzinfo=rc.NY)
    assert rc.c_entry_time(b, rec, "CALL") is None


def test_strikes_pick_follow():
    assert rc.otm_strikes(7702, "CALL", 5)[0] == 7705 and rc.otm_strikes(768.4, "PUT", 1)[0] == 768
    t0 = datetime(2026, 11, 4, 10, 20, tzinfo=rc.NY)
    mk = lambda p: _b5([p] * 6, start=(10, 20))
    assert rc.pick_real({7705.0: mk(5.0), 7710.0: mk(3.0), 7715.0: mk(0.5)}, 7702, t0) == 7710.0
    e, r, k = rc.follow(_b5([2.0, 2.2, 3.2], start=(10, 20)), t0)
    assert k == "هدف"
    assert rc.follow(_b5([2.0, 1.0, 1.0, 1.0], start=(10, 20)), t0)[2] == "وقف"


def test_evaluate_with_fakes():
    r = {"date": "2026-11-04", "time": "09:46", "symbol": "^SPX", "side": "CALL", "strike": "7745"}
    idx = lambda s, d: _b5([7650, 7682, 7701, 7694, 7688, 7695] + [7710] * 60, hl=1)
    opts = lambda occs, d: {o: _b5([3.0] * 10 + [3.1, 4.8] + [4.8] * 60, start=(9, 30)) for o in occs}   # 10:15 = 3.0$
    o = rc.evaluate(r, idx_bars=idx, opt_bars=opts)
    assert o["c_kind"] == "هدف" and o["c_usd"] > 0 and o["c_time"] == "10:15"
    o2 = rc.evaluate(r, idx_bars=lambda s, d: _b5([7650, 7682, 7701, 7694, 7688, 7695] + [7700] * 60, hl=3), opt_bars=opts)
    assert o2["c_kind"] == "لا دخول" and o2["c_usd"] == 0.0
