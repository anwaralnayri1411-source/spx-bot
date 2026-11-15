"""اختبارات H-BRKSIGNS: شكل الشمعة، والأذرع على يوم مصطنع، وBH."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import brksigns_study as bs  # noqa: E402


def day(rows):
    t0 = pd.Timestamp("2026-11-16 09:30")
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"],
                        index=[t0 + pd.Timedelta(minutes=5 * i) for i in range(len(rows))])


def test_strong():
    assert bs.strong(100, 101.05, 99.95, 101, True)
    assert not bs.strong(100, 102, 99.9, 100.2, True)          # ذيل طويل وجسم صغير
    assert bs.strong(101, 101.05, 99.9, 99.95, False)


def test_break_arms_and_fake():
    flat = [(100, 100.1, 99.9, 100, 1000)] * 6                    # 09:30–09:55
    rows = flat + [(100, 100.1, 99.9, 100.0, 1000),               # 10:00
                   (100, 101.05, 99.95, 101.0, 1000),            # 10:05 كسر قوي فوق 100.5
                   (101, 101.3, 100.9, 101.2, 1000),             # تكمل
                   (101.2, 101.3, 100.45, 100.9, 1000)]          # تلمس المستوى وتغلق فوقه (إعادة اختبار)
    rows += [(100.9, 101, 100.8, 100.9, 1000)] * 70
    g = day(rows)
    ev = bs.day_events(g, [(100.5, "x", 1)])
    got = {a: (d, j) for a, d, j in ev}
    assert got["K1"] == (1, 7) and got["K3"] == (1, 9) and got["S1"] == (1, 7) and got["S12"] == (1, 8)
    assert got["RT"] == (1, 9)
    fk = day(flat + [(100, 100.1, 99.9, 100, 1000), (100, 100.8, 99.9, 100.2, 1000)] + [(100.2, 100.3, 100.1, 100.2, 1000)] * 70)
    assert ("FK", -1, 7) in bs.day_events(fk, [(100.5, "x", 1)])


def test_bh():
    assert list(bs.bh_reject([0.001, 0.02, 0.5, 0.9])) == [True, True, False, False]
    assert not np.any(bs.bh_reject([0.2, 0.3]))
