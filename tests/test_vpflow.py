"""اختبارات H-VPFLOW: البروفايل والشكل والمناطق والامتصاص والمسح والإدارة، والربط بالمختبرين. بيانات مصطنعة."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

import mgt_lab as ml
import stocks_lab as sl
import vpflow as vf

NY = ZoneInfo("America/New_York")


def sess(day, prices, vols, start="09:30", step=1):
    """شموع دقيقة: كل شمعة مداها ±0.05 حول السعر."""
    t0 = pd.Timestamp(f"{day} {start}", tz=NY)
    idx = [t0 + pd.Timedelta(minutes=step * i) for i in range(len(prices))]
    return pd.DataFrame({"o": prices, "h": [p + 0.05 for p in prices], "l": [p - 0.05 for p in prices],
                         "c": prices, "v": vols}, index=idx)


def prof(prices, vols, close=None):
    d = sess("2026-11-13", prices, vols)
    if close is not None:
        d.loc[d.index[-1], "c"] = close
    return vf.profile(d)


def test_shapes():
    lows = [100 + i * 0.1 for i in range(20)]                      # 100 → 101.9
    # P: الحجم متجمع فوق، والإغلاق في الوسط
    pf = prof(lows + [101.8] * 30, [10] * 20 + [500] * 30, close=101.0)
    assert vf.shape(pf) == "P" and pf["poc"] > 101.5 and pf["val"] > pf["lo"]
    # b: الحجم متجمع تحت
    pf = prof([100.1] * 30 + lows, [500] * 30 + [10] * 20, close=101.0)
    assert vf.shape(pf) == "b"
    # D: الحجم في الوسط
    pf = prof(lows + [101.0] * 30, [10] * 20 + [500] * 30, close=101.0)
    assert vf.shape(pf) == "D"
    # ترند: حجم موزع بالتساوي والإغلاق عند القمة
    pf = prof(lows, [100] * 20)
    assert vf.shape(pf) == "trend_up"
    pf = prof(lows[::-1], [100] * 20)
    assert vf.shape(pf) == "trend_down"


def test_zones():
    pf = {"poc": 105.0, "vah": 106.0, "val": 103.0, "hi": 107.0, "lo": 100.0, "w": 0.05,
          "lvl": [100 + 0.05 * (k + 0.5) for k in range(140)], "vol": [1.0] * 140}
    z = vf.zones(pf, "P")
    assert z == [{"key": "P:CALL", "side": "CALL", "L": 103.0, "F": 100.0, "t1": 105.0, "t2": 107.0, "trend": False}]
    assert [x["side"] for x in vf.zones(pf, "D")] == ["CALL", "PUT"]
    assert vf.zones(pf, "b")[0]["F"] == 107.0 and vf.zones(pf, "b")[0]["t2"] == 100.0
    pf["vol"][130] = 50.0                                            # أعلى عقدة في الثلث العلوي
    zt = vf.zones(pf, "trend_up")[0]
    assert zt["trend"] and zt["side"] == "CALL" and abs(zt["L"] - (pf["lvl"][130] + 0.025)) < 0.01
    assert abs(zt["L"] - zt["F"] - 0.15 * 7) < 0.01 and zt["t1"] is None


Z = {"key": "P:CALL", "side": "CALL", "L": 103.0, "F": 100.0, "t1": 105.0, "t2": 107.0, "trend": False}
ZP = {"key": "b:PUT", "side": "PUT", "L": 106.0, "F": 107.0, "t1": 105.0, "t2": 100.0, "trend": False}


def bar(o, h, l, c, v):
    return pd.Series({"o": o, "h": h, "l": l, "c": c, "v": v})


def test_absorbed_and_inside():
    assert vf.absorbed(bar(103.5, 103.6, 102.5, 103.4, 300), Z, 100)          # ذيل تحت المستوى وإغلاق فوقه بحجم كبير
    assert not vf.absorbed(bar(103.5, 103.6, 102.5, 103.4, 120), Z, 100)      # حجم عادي
    assert not vf.absorbed(bar(103.5, 103.6, 102.5, 102.8, 300), Z, 100)      # أغلق تحت المستوى (قبول)
    assert not vf.absorbed(bar(103.5, 104.5, 102.9, 103.1, 300), Z, 100)      # إغلاق في النصف السفلي
    assert vf.absorbed(bar(105.5, 106.5, 105.4, 105.6, 300), ZP, 100)         # المرآة للـPUT
    assert vf.inside(bar(103, 103, 101, 102, 1), Z) and not vf.inside(bar(104, 104, 103.5, 103.8, 1), Z)


def b5(rows, start="2026-11-16 09:30"):
    t0 = pd.Timestamp(start, tz=NY)
    return pd.DataFrame(rows, columns=["o", "h", "l", "c", "v"], index=[t0 + pd.Timedelta(minutes=5 * i) for i in range(len(rows))])


def test_scan_p0_p1_and_death():
    early = pd.Timestamp("2026-11-16 09:00", tz=NY)
    bs = b5([(104, 104.5, 103.8, 104.2, 100), (104.2, 104.3, 103.5, 103.6, 100), (103.6, 103.7, 102.6, 103.5, 400)])
    s = vf.scan(bs, Z, "p0", early, early)
    assert s["t"] == bs.index[2] and s["stop"] == 102.6
    assert vf.scan(bs, Z, "p0", bs.index[2] + pd.Timedelta(minutes=1), early) is None      # إشارة قديمة لا تُقبل
    assert vf.scan(bs, Z, "p1", early, early) is None                                       # لم يُغلق داخل المنطقة
    bi = b5([(104, 104.5, 103.8, 104.2, 100), (104.2, 104.3, 102.5, 102.8, 100)])
    s1 = vf.scan(bi, Z, "p1", early, early)
    assert s1["t"] == bi.index[1] and s1["stop"] == 100.0                                   # أول إغلاق داخل المنطقة
    assert vf.scan(bi, Z, "p0", early, early) is None                                       # p0 لا تدخل بلا امتصاص
    dead = b5([(101, 101, 99, 99.5, 100), (99.5, 99.8, 99, 99.2, 100), (99.2, 103.5, 99, 103.4, 900)])
    assert vf.scan(dead, Z, "p0", early, early) is None                                     # إغلاقان خلف F يميتان المنطقة
    assert vf.scan(bs, Z, "p0", early, bs.index[2] + pd.Timedelta(minutes=5)) is None       # قبل وقت الدخول المسموح


NOW = datetime(2026, 11, 16, 11, 0, tzinfo=NY)


def test_manage_levels():
    e = {"entry": 6.0, "side": "CALL", "stop_px": 102.6, "t1": 105.0, "t2": 107.0, "tdate": "2026-11-16"}
    assert vf.manage_levels(dict(e), 5.0, 103.0, 102.5, NOW, ml.S_FORCE, 0.6) == (True, "وقف السعر")
    assert vf.manage_levels(dict(e), 2.0, 103.0, None, NOW, ml.S_FORCE, 0.6) == (True, "وقف العقد")
    x = dict(e)
    assert vf.manage_levels(x, 9.0, 105.1, 104.0, NOW, ml.S_FORCE, 0.6) == (False, "") and x["half"] and x["half_ret"] == 0.25
    assert vf.manage_levels(x, 15.0, 107.2, 107.0, NOW, ml.S_FORCE, 0.6) == (True, "الهدف الثاني")
    assert x["ret"] == round(0.25 + 0.5 * 1.5, 4)
    y = dict(e, half=True, half_ret=0.25)
    assert vf.manage_levels(y, 5.9, 104.0, 104.0, NOW, ml.S_FORCE, 0.6) == (True, "وقف الدخول")
    late = datetime(2026, 11, 16, 15, 46, tzinfo=NY)
    assert vf.manage_levels(dict(e), 6.5, 104.0, 104.0, late, ml.S_FORCE, 0.6) == (True, "وقت")
    p = {"entry": 6.0, "side": "PUT", "stop_px": 106.6, "t1": 105.0, "t2": 100.0, "tdate": "2026-11-16"}
    assert vf.manage_levels(dict(p), 6.0, 106.0, 106.7, NOW, ml.S_FORCE, 0.6) == (True, "وقف السعر")


def test_manage_trend():
    e = {"entry": 6.0, "side": "CALL", "tdate": "2026-11-16"}
    assert vf.manage_trend(e, 12.5, False, NOW, ml.S_FORCE, 0.6) == (False, "") and e["half"] and e["sl"] == 6.0
    assert vf.manage_trend(e, 10.0, True, NOW, ml.S_FORCE, 0.6) == (True, "كسر الهيكل")
    assert e["ret"] == round(e["half_ret"] + 0.5 * (10 / 6 - 1), 4)


def test_lab_variants_fixed_and_signals():
    st = {"variants": ml.initial_variants("2026-11-16"), "strat": ml.strat_variants("2026-11-16"), "vp": ml.vp_variants("2026-11-16")}
    assert [v["vid"] for v in st["vp"]] == ["p0", "p1"] and all(v["pinned"] and v["params"]["price"] == 6.0 for v in st["vp"])
    assert {"p0", "p1"} <= {v["vid"] for v in ml.all_variants(st)} and not any(v["vid"].startswith("p") for v in st["variants"])
    now = datetime(2026, 11, 16, 9, 50, tzinfo=NY)
    bs = b5([(104, 104.5, 103.8, 104.2, 100), (104.2, 104.3, 103.5, 103.6, 100), (103.6, 103.7, 102.6, 103.5, 400)])
    v = st["vp"][0]
    sig = ml.vp_signals(v, [Z, ZP], bs, now, "2026-11-16")
    assert [z["key"] for z, _ in sig] == ["P:CALL"]
    v["open"].append({"side": "CALL", "tdate": "2026-11-16", "zone": "P:CALL"})
    assert ml.vp_signals(v, [Z, ZP], bs, now, "2026-11-16") == []                         # المنطقة مرة واحدة
    v["open"] = [{"side": "PUT", "tdate": "2026-11-16", "zone": "x"}]
    assert ml.vp_signals(v, [Z], bs, now, "2026-11-16") == []                             # اتجاه واحد
    v["open"], v["closed"] = [], [{"side": "CALL", "tdate": "2026-11-16", "zone": f"z{i}"} for i in range(3)]
    assert ml.vp_signals(v, [Z], bs, now, "2026-11-16") == []                             # السقف 3


def test_plan_prev_session_and_stocks_vars():
    d = pd.concat([sess("2026-11-13", [100 + i * 0.1 for i in range(20)] + [101.8] * 30, [10] * 20 + [500] * 30),
                   sess("2026-11-16", [101.5] * 5, [100] * 5)])
    pf, shp, zs = vf.plan(d, datetime(2026, 11, 16).date())
    assert shp == "P" and zs[0]["side"] == "CALL" and pf["hi"] < 102
    assert "P (" in vf.plan_line("SPY", pf, shp, zs)
    st = {"variants": sl.initial_variants("2026-11-16"), "vp": sl.vp_variants("2026-11-16")}
    assert len(sl.all_vars(st)) == len(st["variants"]) + 2 and st["vp"][0]["params"]["dte"] == "3-7"
    assert "عتبة 0" in sl.fmt(st["vp"][0]["params"])
