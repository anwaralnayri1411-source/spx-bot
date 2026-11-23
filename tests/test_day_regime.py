from datetime import datetime

import day_regime as drg


def C(h, m, S, gex, orb=(100.0, 99.0), hold=None, op=None, vw=None):
    return {"n": datetime(2026, 11, 23, h, m), "S": S, "map": {"gex": gex}, "orb": orb, "orb_hold": hold,
            "day_open": op, "vwap_spx": vw}


def test_before_ten_unclear():
    assert drg.classify(C(9, 55, 101, -5, hold="up", op=100))[0] is None


def test_trend_day():
    r, t, ch, _ = drg.classify(C(10, 40, 101.0, -5, hold="up", op=100.0, vw=100.5))
    assert r == drg.TREND and t == 3


def test_chop_day():
    r, t, ch, _ = drg.classify(C(11, 0, 99.5, 5, op=99.6, vw=99.52))
    assert r == drg.CHOP and ch == 3


def test_mixed_unclear():
    assert drg.classify(C(11, 0, 99.5, -5, op=99.6, vw=99.9))[0] is None


def test_apply_weights():
    rows = [("نقطة الألم", 0.5, 0, ""), ("نطاق أول 30 دقيقة", 1.5, 0, ""), ("الاتجاه اللحظي", 2, 0, "")]
    t = drg.apply(rows, drg.TREND)
    assert t[0][1] == 0 and abs(t[1][1] - 1.95) < 1e-9 and t[2][1] == 2
    ch = drg.apply(rows, drg.CHOP)
    assert ch[0][1] == 1.0 and ch[1][1] == 0.75
    assert drg.apply(rows, None) == rows
    assert drg.pain_hour(drg.CHOP) == 11 and drg.pain_hour(None) == 13


def test_line():
    assert drg.line({}) == []
    assert "اتجاه" in drg.line({"regime": (drg.TREND, 2, 0, ["غاما سالبة"])})[0]


def test_fit_caption():
    import options_bot as ob
    cap = "\n".join(["عنوان"] + [f"سطر {i} " + "x" * 80 for i in range(30)] + ["⏰ وقت", "⚠️ تنبيه"])
    out = ob.fit_caption(cap)
    assert len(out) <= ob.CAPTION_LIMIT and out.startswith("عنوان") and out.endswith("⚠️ تنبيه")
    assert ob.fit_caption("قصير") == "قصير"


def test_spx_usd_vix_cap():
    import options_bot as ob
    assert ob.spx_usd(18) == (ob.CONTRACT_MIN_USD, ob.CONTRACT_MAX_USD)
    assert ob.spx_usd(27.7) == (ob.CONTRACT_MIN_USD, 400)
    assert ob.spx_usd(None)[1] == ob.CONTRACT_MAX_USD


def test_strength_filter():
    import options_bot as ob
    K = [{"delta": 0.09, "score": 9}, {"delta": 0.17, "score": 7}, {"delta": 0.25, "score": 5}]
    assert [k["delta"] for k in ob.strength_filter(K, {"sig_strong": True})] == [0.25, 0.17, 0.09]
    assert [k["delta"] for k in ob.strength_filter(K, {})] == [0.17, 0.25]
    assert [k["delta"] for k in ob.strength_filter(K, {"sig_strong": True, "regime": (drg.CHOP, 0, 2, [])})] == [0.25]
