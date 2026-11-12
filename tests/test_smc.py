"""محرك هيكل السوق (H-SMC): حالات مبنية على رسوم الدروس."""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import smc


def _df(rows):
    idx = pd.date_range("2026-11-12 09:30", periods=len(rows), freq="5min", tz="America/New_York")
    return pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close"])


def zigzag(points, step=1.0, wick=0.1):
    """شموع تمشي بين نقاط بخطوة ثابتة: كل شمعة تفتح على إغلاق السابقة."""
    rows, px = [], points[0]
    for target in points[1:]:
        while abs(target - px) > 1e-9:
            nx = px + max(-step, min(step, target - px))
            rows.append((px, max(px, nx) + wick, min(px, nx) - wick, nx))
            px = nx
    return _df(rows)


def test_bos_protected_and_mss():
    # صعود بقمم وقيعان أعلى ثم هبوط يغلق تحت آخر قاع أعلى
    df = zigzag([100, 110, 104, 116, 109, 122, 100])
    st = smc.analyze(df, n=2)
    kinds = [(e["kind"], e["dir"]) for e in st["events"]]
    assert kinds[:2] == [("BOS", "up"), ("BOS", "up")]
    assert abs(st["events"][0]["level"] - 110.1) < 1e-9 and abs(st["events"][0]["protected"] - 103.9) < 1e-9
    assert abs(st["events"][1]["protected"] - 108.9) < 1e-9
    mss = next(e for e in st["events"] if e["kind"] == "MSS")
    assert mss["dir"] == "down" and df["Close"].iloc[mss["i"]] < 108.9
    assert df["Close"].iloc[mss["i"] - 1] >= 108.9          # أول إغلاق تحته، لا قبله
    assert st["trend"] == "down" and abs(st["protected"] - 122.1) < 1e-9
    # الكسر ترك FVG في ساقه (اندفاع)، وOB الصعود عند منبع الساق
    assert st["events"][0]["fvg"]
    ob = st["obs"][0]
    assert ob["dir"] == "up" and ob["bot"] <= 103.9 + 1e-9


def test_internal_low_is_not_mss():
    # قاع داخلي فوق القاع المحمي يُكسر: لا MSS ما دام الإغلاق فوق المحمي
    df = zigzag([100, 110, 104, 116, 112, 114, 111, 120])
    st = smc.analyze(df, n=2)
    assert all(e["dir"] == "up" for e in st["events"]) and st["trend"] == "up"


def test_wick_only_below_protected_is_not_mss():
    df = zigzag([100, 110, 104, 116, 109, 118])
    rows = df.values.tolist()
    rows.append([118, 118.2, 108.0, 117.0])               # ذيل تحت 108.9 والإغلاق فوقه
    rows += [[117, 119.2, 116.8, 119], [119, 119.3, 118.5, 119.1]]
    st = smc.analyze(_df(rows), n=2)
    assert st["trend"] == "up" and not any(e["kind"] == "MSS" for e in st["events"])
    assert any(s["side"] == "sell" for s in st["sweeps"])  # الذيل أخذ سيولة القاع ورجع = سويب


def test_no_lookahead_prefix_property():
    df = zigzag([100, 110, 104, 116, 109, 122, 100, 108, 103, 112])
    full = smc.analyze(df, n=2)
    for k in range(5, len(df) + 1):
        part = smc.analyze(df, n=2, upto=k)
        assert [(e["i"], e["kind"], e["dir"]) for e in part["events"]] == \
               [(e["i"], e["kind"], e["dir"]) for e in full["events"] if e["i"] < k]
        assert all(s["conf"] < k for s in part["swings"])
        # التحليل على الإطار المقطوع فعلياً يطابق التحليل بـ upto
        cut = smc.analyze(df.iloc[:k], n=2)
        assert [e["i"] for e in cut["events"]] == [e["i"] for e in part["events"]]


def test_fvg_strength_classes():
    base = [[100, 101, 99.5, 100.8], [100.8, 103, 100.7, 102.9], [102.9, 104, 102.0, 103.8]]   # FVG 101–102
    tail = [[103.8, 104.2, 103.5, 104]]
    def run(touch):
        st = smc.analyze(_df(base + tail + [touch]), n=2)
        return next(f for f in st["fvgs"] if f["dir"] == "up" and f["i"] == 2)
    assert run([104, 104.1, 101.6, 103.0])["touch"] == "strong"       # ذيل فقط والإغلاق فوقها
    assert run([104, 104.1, 101.4, 101.7])["touch"] == "normal"       # إغلاق داخلها
    f = run([104, 104.1, 100.5, 100.6])
    assert f["touch"] == "weak" and f["state"] == "broken"          # إغلاق تحتها = ضعيفة
    assert abs(f["ce"] - 101.5) < 1e-9


def test_eqh_sweep_and_liquidity_targets():
    df = zigzag([100, 110, 104, 110, 103, 106])
    rows = df.values.tolist() + [[106, 110.6, 105.8, 109.0], [109, 109.2, 107, 107.2], [107.2, 107.4, 106, 106.2]]
    st = smc.analyze(_df(rows), n=2)
    eqh = [q for q in st["liq"] if q["side"] == "buy" and q["eq"]]
    assert eqh, "قمتان متساويتان = سيولة EQH"
    assert any(s["side"] == "buy" and s["eq"] for s in st["sweeps"])
    snap = smc.snapshot(st)
    assert abs(snap["liq_below"]["px"] - 102.9) < 1e-9 and abs(snap["liq_above"]["px"] - 110.6) < 1e-9   # ذيل السويب صار السيولة الجديدة فوق


def test_idm_after_bos_then_taken():
    # BOS فوق 110، ثم أول قاع بعده (IDM عند 111.9) يُؤخذ قبل الصعود الحقيقي فوق 114
    df = zigzag([100, 110, 104, 114, 112, 113.5, 111.5, 118])
    st = smc.analyze(df, n=2)
    first = st["idms"][0]
    assert first["dir"] == "up" and abs(first["px"] - 111.9) < 1e-9 and first["state"] == "taken"
    assert st["trend"] == "up" and st["events"][-1]["kind"] == "BOS" and st["events"][-1]["i"] > first["taken_i"]


def test_premium_discount_and_text():
    df = zigzag([100, 110, 104, 116, 109, 122, 113])
    st = smc.analyze(df, n=2)
    pd_ = st["pd"]
    assert st["trend"] == "up" and abs(pd_["lo"] - 108.9) < 1e-9 and abs(pd_["hi"] - 122.1) < 1e-9
    assert pd_["zone"] == "discount"                            # 113 تحت 115.5
    txt = smc.text_ar(smc.snapshot(st))
    assert "الاتجاه صاعد" in txt and "الرخيصة" in txt


def test_mtf_alignment():
    up = zigzag([100, 110, 104, 116, 109, 122])
    dn = zigzag([122, 112, 118, 106, 113, 100])
    assert smc.mtf({"1h": up, "5m": up}, n=2)["aligned"] == "up"
    assert smc.mtf({"1h": up, "5m": dn}, n=2)["aligned"] is None
