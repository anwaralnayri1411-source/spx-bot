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
