"""اختبارات مختبر التطوّر (H-MGTLAB). لا شبكة."""
import random
from datetime import datetime

import mgt_lab as ml

N = datetime(2026, 11, 5, 10, 0, tzinfo=ml.NY)


def test_initial_variants_and_control():
    vs = ml.initial_variants("2026-11-05")
    assert len(vs) == 12 and vs[0]["control"] and vs[0]["params"] == ml.CONTROL
    for v in vs[1:]:
        assert all(v["params"][k] in ml.GRID[k] for k in ml.GRID)
    assert len({ml.fmt(v["params"]) for v in vs}) >= 10                       # نسخ مختلفة


def test_side_pick_and_ladder():
    p = dict(ml.CONTROL)
    assert ml.side_for(p, 4, 20) == "CALL" and ml.side_for(p, -3, 16) == "PUT"
    assert ml.side_for(p, 4, 12) is None and ml.side_for(p, 2, 30) is None
    ch = [{"side": "CALL", "strike": 7720.0, "ask": 6.8}, {"side": "CALL", "strike": 7730.0, "ask": 3.4},
          {"side": "CALL", "strike": 7745.0, "ask": 1.6}]
    assert ml.pick(ch, "CALL", 7700, 3.5)["strike"] == 7730 and ml.pick(ch, "CALL", 7700, 1.5)["strike"] == 7745
    assert ml.pick(ch, "CALL", 7700, 7.0)["strike"] == 7720 and ml.pick(ch, "PUT", 7700, 3.5) is None
    v = {"params": dict(p, ladder=10, cap=2), "open": [{"side": "CALL", "tdate": "d", "t": "1", "S": 7700}], "closed": []}
    assert not ml.can_add(v, "CALL", 7705, "d") and ml.can_add(v, "CALL", 7710, "d")
    v["open"].append({"side": "CALL", "tdate": "d", "t": "2", "S": 7710})
    assert not ml.can_add(v, "CALL", 7730, "d")                               # السقف 2


def test_manage_exits():
    p = dict(ml.CONTROL)
    e = {"entry": 3.5, "tdate": "2026-11-05"}
    assert ml.manage(e, p, 5.3, N) == (True, "هدف") and e["usd"] == 180.0
    e = {"entry": 3.5, "tdate": "2026-11-05"}
    assert ml.manage(e, p, 1.7, N) == (True, "وقف")
    h = dict(p, exit="half")
    e = {"entry": 3.0, "tdate": "2026-11-05"}
    assert ml.manage(e, h, 6.5, N) == (False, "") and e["half"]
    assert ml.manage(e, h, 12.0, N) == (False, "")
    closed, why = ml.manage(e, h, 5.9, N)
    assert closed and why == "متحرك" and e["usd"] == round(0.5 * 350 + 0.5 * 290, 2)
    e = {"entry": 3.5, "tdate": "2026-11-05"}
    assert ml.manage(e, p, 3.0, N.replace(hour=15, minute=56))[1] == "انتهاء"


def test_evolve_replaces_losers_keeps_control():
    st = {"gen": 1, "variants": ml.initial_variants("2026-11-05")}
    for i, v in enumerate(st["variants"]):
        v["closed"] = [{"tdate": "2026-11-05", "usd": (i - 6) * 50.0} for _ in range(4)]
    txt = ml.evolve(st, "2026-11-05", random.Random(1))
    assert st["gen"] == 2 and "استُبدلت" in txt and "ضابطة" in txt
    assert st["variants"][0]["params"] == ml.CONTROL and st["variants"][0]["closed"]          # الضابطة باقية
    fresh = [v for v in st["variants"] if not v["closed"]]
    assert 1 <= len(fresh) <= 6 and all(v["vid"].endswith("g2") for v in fresh)
    st2 = {"gen": 1, "variants": ml.initial_variants("2026-11-05")}
    assert "لا استبدال" in ml.evolve(st2, "2026-11-05", random.Random(1))


def test_daily_message_is_plain():
    st = {"gen": 1, "variants": ml.initial_variants("2026-11-05")}
    for i, v in enumerate(st["variants"]):
        v["closed"] = [{"tdate": "2026-11-05", "usd": (i - 6) * 50.0} for _ in range(4)]
    board = ml.leaderboard(st, "2026-11-05")
    assert "أفضل 3 نسخ" in board and "الضابطة" in board and "🥇" in board and "🥉" in board
    assert "tp" not in board and "mml" not in board and "ADX" not in board
    ml.evolve(st, "2026-11-05", random.Random(1))
    txt = ml.changes_text(st)
    assert "خرجت" in txt and "عشوائية" in txt and "generations.md" in txt
    assert "لا استبدال" in ml.changes_text({"last_changes": []})
    assert "نصف العقد" in ml.plain(dict(ml.CONTROL, exit="half"))



def test_late_stages_exit():
    p = dict(ml.CONTROL, exit="late", stop=0.5)
    e = {"entry": 2.0, "tdate": "2026-11-05"}
    assert ml.manage(e, p, 2.9, N) == (False, "") and ml.manage(e, p, 4.2, N) == (False, "") and e["sl"] == 2.0
    assert ml.manage(e, p, 6.5, N) == (False, "") and e["sl"] == 3.0
    assert ml.manage(e, p, 8.5, N) == (False, "") and abs(e["sl"] - 5.1) < 1e-9
    assert ml.manage(e, p, 5.0, N) == (True, "وقف مرحلي") and e["usd"] == 300.0
    h = dict(p, exit="late_half")
    e = {"entry": 2.0, "tdate": "2026-11-05"}
    ml.manage(e, h, 4.0, N)
    assert e["half"] and e["half_usd"] == 100.0
    assert ml.manage(e, h, 2.0, N) == (True, "وقف مرحلي") and e["usd"] == 100.0
    e = {"entry": 2.0, "tdate": "2026-11-05"}
    assert ml.manage(e, p, 3.0, N.replace(hour=15, minute=56))[1] == "انتهاء"
    assert ml.manage({"entry": 2.0, "tdate": "2026-11-05"}, p, 0.9, N)[1] == "وقف مرحلي"
    assert "مراحل متأخرة" in ml.plain(p)


def test_ensure_exits_keeps_control_and_trades():
    st = {"gen": 2, "variants": ml.initial_variants("2026-11-05")}
    for v in st["variants"]:
        if v["params"]["exit"] in ("late", "late_half"):
            v["params"]["exit"] = "tp50"
    for v in st["variants"][:8]:
        v["closed"] = [{"usd": 1}]
    before = [dict(v["params"]) for v in st["variants"][:8]]
    ml.ensure_exits(st)
    exits = [v["params"]["exit"] for v in st["variants"]]
    assert exits.count("late") == 1 and exits.count("late_half") == 1
    assert [v["params"] for v in st["variants"][:8]] == before
    ml.ensure_exits(st)
    assert [v["params"]["exit"] for v in st["variants"]] == exits


def test_after_exit_tracking():
    c = {"symbol": "S", "tdate": "2026-11-05", "t": "x", "side": "PUT", "strike": 7000.0, "entry": 2.0, "exit_px": 3.0, "exit_t": "10:00", "usd": 100.0}
    old = {"symbol": "O", "tdate": "2026-11-05", "usd": 1.0}
    st = {"variants": [{"vid": "v1", "open": [], "closed": [c, old]}]}
    assert ml.tracked_symbols(st, "2026-11-05") == {"S"} and ml.tracked_symbols(st, "2026-11-06") == set()
    ml.track_after(st, {"S": {"bid": 5.0}, "O": {"bid": 9.0}}, N)
    ml.track_after(st, {"S": {"bid": 4.0}}, N.replace(hour=11))
    assert c["after_hi"] == 5.0 and c["after_hi_t"] == "10:00" and "after_hi" not in old
    assert ml.after_rows(st, "2026-11-05")[0]["after_hi"] == 5.0
    assert "+200$" in ml.left_text(st["variants"][0], "2026-11-05")


# ====================== H-TRENDPB: النسخ الثابتة s0–s3 ======================
import pandas as pd  # noqa: E402

D = "2026-11-05"


def _st5(events, fvgs, N, zone):
    return {"events": events, "fvgs": fvgs, "N": N, "pd": {"zone": zone}}


def _ctx(st5=None, h1="up", m5=None, orng=None):
    idx = pd.date_range("2026-11-05 09:30", periods=12, freq="5min", tz=ml.NY)
    return {"st5": st5, "h1_trend": h1, "m5": m5 if m5 is not None else pd.DataFrame({"c": range(12)}, index=idx), "or": orng}


def test_strat_variants_fixed_and_outside_evolution():
    sv = ml.strat_variants(D)
    assert [v["vid"] for v in sv] == ["s0", "s1", "s2", "s3"] and all(v["pinned"] for v in sv)
    assert sv[0]["params"] == ml.V9 and all(v["params"] == dict(ml.V9, exit="struct") for v in sv[1:])
    st = {"gen": 1, "variants": ml.initial_variants(D), "strat": sv}
    for v in st["variants"]:
        v["closed"] = [{"tdate": D, "usd": -500.0}] * 3
    ml.evolve(st, D, random.Random(1))
    assert [v["vid"] for v in st["strat"]] == ["s0", "s1", "s2", "s3"]          # التطور لا يمس النسخ الثابتة
    assert len(ml.all_variants(st)) == 16
    assert "s3" in ml.strat_text(st, D) and "دخول الهيكل" in ml.strat_text(st, D)


def test_manage_struct_half_runner_and_exits():
    p = dict(ml.V9, exit="struct")
    e = {"entry": 5.0, "tdate": D}
    assert ml.manage_struct(e, p, 1.9, N) == (True, "وقف") and e["usd"] == -310.0
    e = {"entry": 5.0, "tdate": D}
    assert ml.manage_struct(e, p, 10.0, N) == (False, "") and e["half"] and e["half_usd"] == 250.0
    assert ml.manage_struct(e, p, 16.0, N) == (False, "") and e["sl"] == 7.5      # قمة +220% ← الوقف +50%
    closed, why = ml.manage_struct(e, p, 7.4, N)
    assert closed and why == "وقف مرحلي" and e["usd"] == round(250 + 0.5 * 240, 2)
    e = {"entry": 5.0, "tdate": D}
    ml.manage_struct(e, p, 10.0, N)
    assert ml.manage_struct(e, p, 9.0, N, broken=True) == (True, "كسر الهيكل") and e["usd"] == 450.0
    e = {"entry": 5.0, "tdate": D}
    assert ml.manage_struct(e, p, 4.0, N.replace(hour=15, minute=45)) == (True, "وقت")
    e = {"entry": 5.0, "tdate": D}
    assert ml.manage_struct(e, p, 4.0, N, broken=True) == (False, "")             # الكسر يخص الباقي فقط


def test_structure_broken_only_after_entry():
    t = pd.Timestamp("2026-11-05 11:00", tz=ml.NY)
    st5 = {"events": [{"dir": "down", "t": t}]}
    assert ml.structure_broken({"st5": st5}, "CALL", "2026-11-05T11:02-05:00")     # الشمعة أُغلقت 11:05 بعد الدخول
    assert not ml.structure_broken({"st5": st5}, "CALL", "2026-11-05T11:10-05:00")
    assert not ml.structure_broken({"st5": st5}, "PUT", "2026-11-05T10:00-05:00")
    assert not ml.structure_broken(None, "CALL", "2026-11-05T10:00-05:00")


def test_day_filter_window_chop_and_events():
    idx = pd.date_range("2026-11-05 09:30", periods=12, freq="5min", tz=ml.NY)
    trend = pd.DataFrame({"c": [100, 101, 102, 101, 102, 103, 104, 105, 106, 107, 108, 109]}, index=idx)
    chop = pd.DataFrame({"c": [100, 101, 102, 101, 102, 103, 104, 99, 97, 101, 100, 100]}, index=idx)
    assert not ml.chop_day(_ctx(m5=trend, orng=(103.5, 99.5)))
    assert ml.chop_day(_ctx(m5=chop, orng=(103.5, 99.5)))
    ok = _ctx(m5=trend, orng=(103.5, 99.5))
    assert ml.day_ok(ok, N.replace(hour=10, minute=30)) and not ml.day_ok(ok, N.replace(hour=9, minute=55))
    assert not ml.day_ok(ok, N.replace(hour=14, minute=31))
    assert ml.near_event(datetime(2026, 12, 9, 13, 30, tzinfo=ml.NY))                 # قبل قرار الفيدرالي 14:00
    assert ml.near_event(datetime(2026, 12, 9, 14, 10, tzinfo=ml.NY))
    assert not ml.near_event(datetime(2026, 12, 9, 14, 20, tzinfo=ml.NY))
    assert not ml.near_event(datetime(2026, 11, 13, 10, 30, tzinfo=ml.NY))             # PPI 08:30 انتهت نافذته


def test_smc_setup_needs_every_condition():
    p = dict(ml.V9)
    ev = {"dir": "up", "kind": "MSS", "fvg": True, "i": 8, "origin": 4}
    f = {"dir": "up", "i": 6, "touch_i": 11, "touch": "strong", "ce": 101.25}
    good = _ctx(_st5([ev], [f], 12, "discount"))
    su = ml.smc_setup(good, "CALL", 4, 20, p)
    assert su and su["kind"] == "MSS" and su["ce"] == 101.25
    assert ml.smc_setup(good, "CALL", 3, 20, p) is None                               # الأصوات أقل من 4
    assert ml.smc_setup(good, "PUT", -4, 20, p) is None
    assert ml.smc_setup(_ctx(_st5([ev], [f], 12, "discount"), h1="down"), "CALL", 4, 20, p) is None
    assert ml.smc_setup(_ctx(_st5([ev], [f], 12, "premium")), "CALL", 4, 20, p) is None
    assert ml.smc_setup(_ctx(_st5([dict(ev, fvg=False)], [f], 12, "discount")), "CALL", 4, 20, p) is None
    assert ml.smc_setup(_ctx(_st5([ev], [dict(f, touch="normal")], 12, "discount")), "CALL", 4, 20, p) is None
    assert ml.smc_setup(_ctx(_st5([ev], [dict(f, touch_i=10)], 12, "discount")), "CALL", 4, 20, p) is None
    assert ml.smc_setup(_ctx(_st5([ev], [dict(f, i=3)], 12, "discount")), "CALL", 4, 20, p) is None   # فجوة خارج ساق الكسر


def test_strat_wants_one_direction_cap_and_once():
    sv = {v["vid"]: v for v in ml.strat_variants(D)}
    now = N.replace(hour=10, minute=30)
    ctx = _ctx(_st5([{"dir": "up", "kind": "BOS", "fvg": True, "i": 8, "origin": 4}],
                    [{"dir": "up", "i": 6, "touch_i": 11, "touch": "strong", "ce": 1.0}], 12, "discount"), orng=(50, -5))
    s0 = sv["s0"]
    assert ml.strat_wants(s0, None, now, "CALL", 4, 20, 7700, D, True)[0]
    assert not ml.strat_wants(s0, None, now, "CALL", 4, 20, 7700, D, False)[0]      # بلا شمعة جديدة ولا مركز
    s0["open"].append({"side": "PUT", "tdate": D, "t": "1", "S": 7700})
    assert not ml.strat_wants(s0, None, now, "CALL", 4, 20, 7700, D, True)[0]       # لا CALL مع PUT مفتوح
    assert not ml.strat_wants(sv["s2"], ctx, N.replace(hour=9, minute=50), "CALL", 4, 20, 7700, D, True)[0]
    assert ml.strat_wants(sv["s2"], ctx, now, "CALL", 4, 20, 7700, D, True)[0]
    s3 = sv["s3"]
    ok, trig, su = ml.strat_wants(s3, ctx, now, "CALL", 4, 20, 7700, D, True)
    assert ok and trig == "BOS"
    s3["open"].append({"side": "CALL", "tdate": D, "t": "x", "S": 7700, "setup": su["key"]})
    assert not ml.strat_wants(s3, ctx, now, "CALL", 4, 20, 7700, D, True)[0]        # الإعداد نفسه مرة واحدة
    s3["open"] = [{"side": "CALL", "tdate": D, "t": str(i), "S": 7700, "setup": f"k{i}"} for i in range(3)]
    assert not ml.strat_wants(s3, dict(ctx), now, "CALL", 4, 20, 7700, D, True)[0]  # السقف 3 في اليوم


def test_strat_ctx_on_minute_bars():
    import numpy as np
    idx = pd.date_range("2026-11-04 09:30", "2026-11-05 11:00", freq="1min", tz=ml.NY)
    px = 7600 + np.cumsum(np.sin(np.arange(len(idx)) / 37.0) * 0.8 + 0.05)
    m1 = pd.DataFrame({"o": px, "h": px + 0.6, "l": px - 0.6, "c": px + 0.1, "v": 100.0}, index=idx)
    now = datetime(2026, 11, 5, 11, 0, tzinfo=ml.NY)
    ctx = ml.strat_ctx(m1, now)
    assert len(ctx["m5"]) == 18 and ctx["m5"].index[0].hour == 9                     # شموع اليوم المكتملة فقط
    assert ctx["or"] and ctx["or"][0] >= ctx["or"][1] and ctx["st5"] is not None
    assert ctx["h1_trend"] in ("up", "down", None)
    ml.chop_day(ctx), ml.day_ok(ctx, now)
