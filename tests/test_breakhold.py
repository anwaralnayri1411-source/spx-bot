"""«الكسر والثبات» (H-BREAKHOLD): دوال صافية."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import breakhold as bh

KEY = [(100.0, "أعلى أمس", True)]
RND = [(100.0, "100", False)]


def test_break_and_hold_up():
    ev = bh.events([98, 99, 101, 102, 103], KEY)
    assert [(e["kind"], e["i"]) for e in ev] == [("hold_up", 4)]


def test_pending_break_is_not_an_event():
    assert bh.events([98, 99, 101, 102], KEY) == []


def test_failed_break_is_reverse_signal_on_key_levels_only():
    ev = bh.events([98, 99, 101, 99.5, 98], KEY)
    assert [(e["kind"], e["i"]) for e in ev] == [("fail_up", 3)] and bh.DIR["fail_up"] == "PUT"
    assert bh.events([98, 99, 101, 99.5, 98], RND) == []


def test_break_down_and_hold():
    ev = bh.events([102, 101, 99, 98, 97], KEY)
    assert ev[0]["kind"] == "hold_down" and bh.DIR["hold_down"] == "PUT"


def test_small_poke_is_not_a_break():
    assert bh.events([99, 100.3, 100.4, 100.2], KEY) == []


def test_orb_levels_need_completed_range():
    lv = [(100.0, "أعلى أول 30 دقيقة", True)]
    assert bh.events([98, 101, 102, 103, 104, 104, 104, 104], lv, start=6) == []
    assert bh.events([98] * 6 + [101, 102, 103], lv, start=6)[0]["kind"] == "hold_up"


def test_latest_is_recent_and_after_ten():
    mins = [9 * 60 + 30 + 5 * i for i in range(20)]
    ev = [{"kind": "hold_up", "i": 3}, {"kind": "hold_down", "i": 15}]
    assert bh.latest(ev, 20, mins)["kind"] == "hold_down"
    assert bh.latest(ev[:1], 20, mins) is None


def test_orb_hold():
    assert bh.orb_hold([99, 101, 102, 103], (100, 90)) == "up"
    assert bh.orb_hold([99, 101, 99, 103], (100, 90)) is None
    assert bh.orb_hold([91, 89, 88, 87], (100, 90)) == "down"


def test_place_blocks_put_near_low_and_call_near_high():
    hl = (7544.0, 7470.0)
    assert bh.place_block("PUT", 7488.0, hl, None, 13 * 60) is not None          # 13:01 يوم CPI: 24% من المدى
    assert bh.place_block("PUT", 7529.7, hl, None, 13 * 60) is None              # من أعلى المدى: مسموح
    assert bh.place_block("CALL", 7540.0, hl, None, 13 * 60) is not None
    assert bh.place_block("PUT", 7488.0, hl, None, 10 * 60) is None              # قبل 10:30 لا شرط مكان
    assert bh.place_block("PUT", 7488.0, hl, {"kind": "hold_down"}, 13 * 60) is None   # كسر ثابت للأسفل يسمح
    assert bh.place_block("PUT", 7488.0, (7500.0, 7490.0), None, 13 * 60) is None      # مدى صغير


def test_levels_include_round_fifties():
    lv = bh.levels((7530, 7490), (7577, 7480), 7470, 7544)
    names = [n for _, n, _ in lv]
    assert "أعلى أول 30 دقيقة" in names and "7,500" in names and "7,550" in names


def test_apply_flip_confirm_and_weak_edge():
    assert bh.apply("PUT", 9.0, {"kind": "hold_up"}) == ("CALL", 6.0, "flip")
    assert bh.apply("CALL", 9.0, {"kind": "hold_up"}) == ("CALL", 9.0, "confirm")
    assert bh.apply(None, 2.0, {"kind": "fail_up"}) == ("PUT", 6.0, "confirm")
    assert bh.apply("PUT", 4.5, None) == ("PUT", 4.5, "")


def test_direction_points_orb_needs_hold():
    import options_bot as ob
    from datetime import datetime
    c = {"n": datetime(2026, 11, 10, 13, 0, tzinfo=ob.NY), "S": 7540.0, "sma20": 7600, "sma50": 7650, "ema9": 1, "ema21": 2, "above_vwap": False, "r30": 0.0, "gap": None,
         "map": None, "vix": 18, "vpct": 50, "max_pain": None, "es": None, "leaders": None, "orb": (7530.0, 7490.0),
         "vix_day": None, "pc": None, "pd_hl": None}
    old = ob.direction_points(dict(c))
    pend = ob.direction_points(dict(c, orb_hold=None))
    held = ob.direction_points(dict(c, orb_hold="up"))
    assert old[0] - pend[0] == 1.5 and held[0] == old[0]
    assert any("دون ثبات" in r[3] for r in pend[2])


def test_fvg_zone_and_touch():
    highs = [10, 11, 12, 15, 16]
    lows = [9, 10, 11.5, 13, 15]
    assert bh.fvg_zone(highs, lows, "CALL") == (12, 15)          # k=4: أدنى 15 > أعلى k−2 (12)
    assert bh.fvg_zone(highs, lows, "PUT") is None
    assert bh.fvg_touch((12, 15), "CALL", 16, 14.5, 15.2) and not bh.fvg_touch((12, 15), "CALL", 17, 15.5, 16)
    assert bh.fvg_zone([20, 19, 15, 14], [19, 18, 14, 13], "PUT") == (14, 18)


def test_stock_block():
    assert bh.stock_block("CALL", 101, 100, 90, 99, [99, 101]) is not None            # كسر اليوم بلا ثبات
    assert bh.stock_block("CALL", 101, 100, 90, 100.5, [101]) is None                  # ثابت من أمس
    assert "ملاحقة" in bh.stock_block("PUT", 95, 100, 90, 98, [97, 95])                # −3% بلا كسر
    assert bh.stock_block("PUT", 97.5, 100, 90, 98, [98, 97.5]) is None                # حركة صغيرة
    assert bh.stock_block("PUT", 89, 100, 90, 89.5, [89]) is None                      # كسر ثابت من أمس يسمح بالملاحقة


def test_apply_fvg_waits_then_enters(tmp_path, monkeypatch):
    import options_bot as ob
    from datetime import datetime
    monkeypatch.setattr(ob, "BH_PENDING", str(tmp_path / "p.json"))
    monkeypatch.setattr(ob, "_pick_all", lambda side, ch, S, T, c, exp: [{"strike": 7520.0, "price": 4.0}])
    evt = {"kind": "hold_up", "level": 7500.0, "name": "7,500", "key": False, "i": 9}
    a = {"side": "CALL", "strength": 6.0, "bh_mode": "flip", "bh_evt": evt, "contracts": [{"x": 1}], "reasons": [],
         "bh_bars": ([7490, 7495, 7505, 7510], [7492, 7497, 7508, 7512], [7488, 7493, 7502, 7506]),
         "c": {"S": 7510.0}, "exp": "2026-11-12", "_pick": (None, None)}
    n1 = datetime(2026, 11, 12, 13, 45, tzinfo=ob.NY)
    ob.apply_fvg(a, n1)
    assert a["contracts"] == [] and "ننتظر عودة السعر إلى الفجوة" in a["reasons"][0]          # فجوة 7497–7506
    b = dict(a, side="PUT", bh_mode="", contracts=[], reasons=["لا اتجاه واضح"],
             bh_bars=([7505, 7503, 7501], [7508, 7506, 7504], [7503, 7501, 7499]), c={"S": 7501.0})
    ob.apply_fvg(b, datetime(2026, 11, 12, 13, 55, tzinfo=ob.NY))
    assert b["side"] == "CALL" and b["contracts"] and b["fvg"] == (7497, 7506) and b["reasons"] == []
    c = dict(a, contracts=[], reasons=[])
    ob.apply_fvg(c, datetime(2026, 11, 12, 14, 0, tzinfo=ob.NY))      # الانتظار انتهى بالدخول: لا شيء معلق
    assert c["reasons"] == []


def test_flow_30_and_log_columns(tmp_path, monkeypatch):
    import options_bot as ob
    from datetime import datetime
    f = tmp_path / "flow.csv"
    f.write_text("date,time,call_prem,put_prem,net_delta\n2026-11-11,10:30,100,50,5\n2026-11-11,10:50,200,400,-7\n2026-11-11,11:20,9,9,9\n", encoding="utf-8")
    n = datetime(2026, 11, 11, 11, 0, tzinfo=ob.NY)
    assert ob.flow_30(n, str(f)) == (-200.0, -7.0)
    assert ob.flow_30(n, str(tmp_path / "none.csv")) == (None, None)
    monkeypatch.setattr(ob, "BH_LOG", str(tmp_path / "bh" / "log.csv"))
    monkeypatch.setattr(ob, "FLOW_LOG", str(f))
    (tmp_path / "bh").mkdir()
    (tmp_path / "bh" / "log.csv").write_text("time,S\nx,1\n", encoding="utf-8")     # ملف بأعمدة قديمة
    ob._bh_log(n, 7600.0, "CALL", "PUT", None, "", None, [])
    head = (tmp_path / "bh" / "log.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "flow_lean" in head and len(list((tmp_path / "bh").glob("log_old_*.csv"))) == 1


def test_flow_confirm_needs_both(tmp_path):
    import gzip
    import options_bot as ob
    from datetime import datetime
    n = datetime(2026, 11, 11, 11, 10, tzinfo=ob.NY)
    f = tmp_path / "flow.csv"
    f.write_text("date,time,call_prem,put_prem,net_delta\n2026-11-11,11:00,5000000,1000000,9\n", encoding="utf-8")
    raw = tmp_path / "raw"
    raw.mkdir()
    rows = "time,side,delta,iv\n10:40,C,0.4,0.20\n10:40,P,-0.4,0.25\n11:05,C,0.4,0.23\n11:05,P,-0.4,0.24\n"
    with gzip.open(raw / "2026-11-11.csv.gz", "wt", encoding="utf-8") as g:
        g.write(rows)
    up, dn = {"kind": "hold_up"}, {"kind": "fail_up"}
    r = ob.flow_confirm(n, up, str(f), str(raw))
    assert r["ok"] and r["lean"] == 4000000 and abs(r["tilt"] - 0.04) < 1e-9          # كول +0.03، بوت −0.01
    assert not ob.flow_confirm(n, dn, str(f), str(raw))["ok"]                           # PUT والتدفق للكول
    assert not ob.flow_confirm(n, up, str(f), str(tmp_path / "none"))["ok"]             # بلا لقطات: لا تأكيد
    a = {"side": "CALL", "bh_flow": r}
    assert "التدفق يؤكد" in ob.flow_line(a)[0] and ob.flow_line({"side": "CALL", "bh_flow": {"ok": False}}) == []
