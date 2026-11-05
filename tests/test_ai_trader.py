"""اختبارات المتداول الذكي الورقي (H-AITRADER). لا شبكة ولا Claude."""
from datetime import datetime

import ai_trader as at

N = datetime(2026, 11, 5, 10, 0, tzinfo=at.NY)
MENU = [
    {"symbol": "SPY 261105C00768000", "side": "CALL", "strike": 768.0, "expiry": "2026-11-05", "bid": 1.50, "ask": 1.55, "delta": 0.45},
    {"symbol": "SPY 261106C00770000", "side": "CALL", "strike": 770.0, "expiry": "2026-11-06", "bid": 2.90, "ask": 3.00, "delta": 0.40},
    {"symbol": "SPY 261105P00765000", "side": "PUT", "strike": 765.0, "expiry": "2026-11-05", "bid": 1.20, "ask": 1.25, "delta": -0.40},
]


def _dec(action="شراء", sym="SPY 261106C00770000", qty=5, stop=1.8, target=4.5):
    return {"market_read": "صعود", "factors": [{"name": "VWAP", "reading": "فوق", "direction": "صعود", "weight": "قوي", "used": True},
                                                {"name": "الأخبار", "reading": "سلبية", "direction": "هبوط", "weight": "ضعيف", "used": False}],
            "action": action, "sell_ids": [], "sell_reason": "", "confidence": 0.6, "wait_reason": "",
            "buy": {"symbol": sym, "qty": qty, "why_contract": "دلتا 0.40 وسيولة", "how_chosen": "قارنت 3 سترايكات",
                    "rejected": [{"contract": "768C اليوم", "why_not": "تآكل سريع"}], "stop_price": stop, "target_price": target,
                    "invalidation": "إغلاق تحت VWAP"}}


def test_validate_buy_budget_and_cap():
    st = at.new_state()
    why, q = at.validate_buy(st, MENU, _dec()["buy"], N, {})
    assert why is None and q == 2                         # 30% من 2,000 = 600$ ← عقدان بـ300$
    assert at.validate_buy(st, MENU, dict(_dec()["buy"], symbol="X"), N, {})[0] == "العقد ليس في القائمة"
    late = N.replace(hour=15, minute=20)
    assert at.validate_buy(st, MENU, _dec()["buy"], late, {})[0] == "لا شراء بعد 15:15"


def test_sharia_no_opposite_and_max_open():
    st = at.new_state()
    at.open_position(st, MENU[2], 1, _dec(sym=MENU[2]["symbol"])["buy"], N)       # PUT مفتوح
    assert at.validate_buy(st, MENU, _dec()["buy"], N, {})[0] == "مركز معاكس مفتوح (الشرط الشرعي)"
    st = at.new_state()
    for _ in range(3):
        at.open_position(st, MENU[0], 1, _dec(sym=MENU[0]["symbol"])["buy"], N)
    assert at.validate_buy(st, MENU, _dec()["buy"], N, {})[0] == "بلغ حد 3 مراكز"


def test_open_close_and_pnl():
    st = at.new_state()
    p = at.open_position(st, MENU[1], 2, _dec()["buy"], N)
    assert abs(st["cash"] - (2000 - 600)) < 1e-9 and p["stop"] == 1.8 and p["target"] == 4.5
    rec = at.close_position(st, p, 4.0, N, "هدف")
    assert rec["pnl"] == 200.0 and abs(st["cash"] - 2200) < 1e-9 and not st["positions"]


def test_auto_exits_stop_target_and_close_today():
    st = at.new_state()
    a = at.open_position(st, MENU[1], 1, _dec()["buy"], N)                           # غداً: وقف 1.8
    b = at.open_position(st, MENU[0], 1, _dec(sym=MENU[0]["symbol"], stop=0.5, target=9)["buy"], N)   # اليوم
    marks = {a["symbol"]: {"bid": 1.7}, b["symbol"]: {"bid": 1.6}}
    out = at.auto_exits(st, marks, N)
    assert [o["symbol"] for o in out] == [a["symbol"]] and "الوقف" in out[0]["why"]
    out = at.auto_exits(st, marks, N.replace(hour=15, minute=50))
    assert out and out[0]["symbol"] == b["symbol"] and "15:50" in out[0]["why"]


def test_messages_are_short_and_journal_keeps_reasoning():
    st = at.new_state()
    d = _dec()
    d["buy"]["short_reason"] = "السوق يهبط تحت متوسط الجلسة."
    p = at.open_position(st, MENU[1], 2, d["buy"], N)
    m = at.buy_message(d, p, MENU[1], 767.2)
    assert "SPY 770 CALL" in m and "عقد الغد" in m and "600$" in m and "السوق يهبط" in m and "وقف 1.80" in m
    assert "بدائل" not in m and m.count("\n") == 3
    rec = at.close_position(st, p, 2.5, N, "بلغ الوقف")
    assert "-100$" in at.sell_message(rec, "بلغ الوقف")
    rep = at.day_report(dict(st, decisions_today=4), N.replace(hour=16, minute=5), ["09:45 SPY CALL 765"])
    assert "نهاية الجلسة" in rep and "توصيات البوت اليوم: 1" in rep and "صافي اليوم: -100$" in rep and "(0د)" in rep
    assert at.short("قصيرة، وواضحة") == "قصيرة، وواضحة" and at.short("أولى. " + "ثانية " * 50) == "أولى"
    assert len(at.short("كلمة " * 100)) <= 151


def test_main_flow_with_fakes(tmp_path, monkeypatch):
    for k, f in (("STATE", "s.json"), ("LOG", "log.csv"), ("JOURNAL", "j.md"), ("USAGE", "u.csv")):
        monkeypatch.setattr(at, k, str(tmp_path / f))
    monkeypatch.setattr(at, "gather", lambda n: ("مدخلات", MENU, 767.2, []))
    monkeypatch.setattr(at, "ask_claude", lambda p, n: _dec())
    monkeypatch.setattr(at, "quotes", lambda syms, meta=None: [{"symbol": s, "bid": 2.95, "ask": 3.0} for s in syms])

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return N
    monkeypatch.setattr(at, "datetime", FakeDT)
    at.main(["--no-send"])
    st = at.load_state()
    assert len(st["positions"]) == 1 and st["positions"][0]["qty"] == 2 and st["decisions_today"] == 1
    j = (tmp_path / "j.md").read_text(encoding="utf-8")
    assert "لماذا" in j and "رفض 768C اليوم" in j and "نُفّذ" in j
    assert "اشترى" in (tmp_path / "log.csv").read_text(encoding="utf-8")


def test_spx_menu_row_and_cross_underlying_sharia():
    spx = {"symbol": "SPXW  261106P07600000", "under": "SPX", "side": "PUT", "strike": 7600.0, "expiry": "2026-11-06",
           "bid": 2.40, "ask": 2.50, "delta": -0.08}
    menu = MENU + [spx]
    st = at.new_state()
    why, q = at.validate_buy(st, menu, _dec(sym=spx["symbol"], qty=9)["buy"], N, {})
    assert why is None and q == 2                         # 600$ ÷ 250$ = عقدان
    p = at.open_position(st, spx, q, _dec(sym=spx["symbol"])["buy"], N)
    assert p["under"] == "SPX" and "SPX" in p["id"]
    assert at.validate_buy(st, menu, _dec()["buy"], N, {})[0] == "مركز معاكس مفتوح (الشرط الشرعي)"   # CALL على SPY ممنوع
    m = at.buy_message(_dec(sym=spx["symbol"]), p, spx, {"SPY": 767.2, "SPX": 7690.5})
    assert "SPX 7600 PUT" in m and "عقد الغد" in m
    assert "SPX 7600" in at.sell_message(at.close_position(st, p, 3.0, N, "هدف"), "هدف")
    assert at.spot_num({"SPY": 767.2, "SPX": 7690.5}) == 767.2 and at.spot_text(767.2) == "SPY 767.20"
