"""الجدران الحية (H-LIVEWALLS، الإصدار 22)."""
import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import livewalls as lw


def _df():
    rows = []
    # 10:00: جدار الكول 7650 ثم يضعف في الساعة التالية (الحجم الجديد يذهب إلى 7660)
    for t, v50, v60, p00 in (("10:00", 1000, 200, 800), ("11:00", 1100, 2000, 1600)):
        rows += [{"time": t, "side": "C", "strike": 7650.0, "volume": v50, "bid": 1, "ask": 2, "last": 2},
                 {"time": t, "side": "C", "strike": 7660.0, "volume": v60, "bid": 1, "ask": 2, "last": 1},
                 {"time": t, "side": "C", "strike": 7620.0, "volume": 5000, "bid": 1, "ask": 2, "last": 2},   # تحت السعر: لا يُعدّ
                 {"time": t, "side": "P", "strike": 7600.0, "volume": p00, "bid": 1, "ask": 2, "last": 2},
                 {"time": t, "side": "P", "strike": 7580.0, "volume": 300, "bid": 1, "ask": 2, "last": 1}]
    return pd.DataFrame(rows)


def test_walls_state_and_move():
    s = lw.summary(_df(), 7630.0, "11:05")
    c, p = s["call"], s["put"]
    assert c["wall"] == (7660.0, 2000.0) and c["then_wall"] == (7650.0, 1000.0)
    assert c["state"] == "strong" and "⬆️" in lw.move_text(c)
    assert p["wall"] == (7600.0, 1600.0) and p["state"] == "normal" and p["buy"] == 1.0
    assert lw.summary(_df(), 7630.0, "09:00") is None and lw.summary(None, 7630.0, "11:00") is None


def test_fragile_wall():
    df = _df()
    df.loc[(df.time == "11:00") & (df.strike == 7660.0), "volume"] = 300     # الجدار 7650 يبقى، ونصيبه من الساعة صغير
    df.loc[(df.time == "11:00") & (df.strike == 7650.0), "volume"] = 1010
    df = pd.concat([df, pd.DataFrame([{"time": "11:00", "side": "C", "strike": 7700.0, "volume": 900, "bid": 1, "ask": 2, "last": 1},
                                      {"time": "10:00", "side": "C", "strike": 7700.0, "volume": 0, "bid": 1, "ask": 2, "last": 1}])])
    c = lw.summary(df, 7630.0, "11:00")["call"]
    assert c["wall"][0] == 7650.0 and c["state"] == "fragile"
    txt = lw.card(lw.summary(df, 7630.0, "11:00"), 7630.0, "CALL", "7:00 م")
    assert "هش" in txt and "سيناريو الصعود" in txt and "قد تُخترق" in txt


def test_apply_live_walls_and_hourly_card():
    import options_bot as ob
    n = datetime(2026, 11, 11, 11, 5, tzinfo=ob.NY)
    c = {"map": {"call_wall": (7700.0, 9), "put_wall": (7500.0, 9), "gex": -1.0}}
    ob.apply_live_walls(c, n, 7630.0, df=_df())
    assert c["map"]["call_wall"] == (7660.0, 2000.0) and c["map"]["oi_call_wall"] == (7700.0, 9)
    c2 = {"map": None}
    ob.apply_live_walls(c2, n, 7630.0, df=pd.DataFrame(columns=["time", "side", "strike", "volume", "bid", "ask", "last"]))
    assert c2["map"] is None
    sent, state = [], {}
    a = {"c": dict(c, S=7630.0), "side": "CALL", "contracts": []}
    assert ob.hourly_card(n, a, state, sent.append) and not ob.hourly_card(n, a, state, sent.append)
    assert len(sent) == 1 and "خريطة السيولة الحية" in sent[0]
    assert not ob.hourly_card(datetime(2026, 11, 11, 16, 5, tzinfo=ob.NY), a, {}, sent.append)
