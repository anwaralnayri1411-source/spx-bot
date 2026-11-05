"""اختبارات المتداول الذكي الورقي (H-AITRADER). لا شبكة ولا Claude."""
import json
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
    monkeypatch.setattr(at, "ask_claude", lambda p, n, *a, **k: _dec())
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



def _rv(new=(), confirmed=(), contradicted=()):
    return {"summary": "جلسة", "new": list(new), "confirmed": list(confirmed), "contradicted": list(contradicted)}


L1 = {"condition": "السعر قرب جدار البيع بعد هبوط كبير", "action": "لا تدخل PUT جديداً", "evidence": "خسارتان"}


def test_memory_lifecycle():
    m = at.new_memory()
    assert [x["id"] for x in at.apply_review(m, _rv([L1]), "2026-11-05")] == ["L1"]
    assert at.apply_review(m, _rv([L1]), "2026-11-05") == [] and len(m["lessons"]) == 1     # مرة واحدة لليوم
    assert at.lesson_status(m["lessons"][0]) == "فرضية" and "[L1] (فرضية) إذا السعر" in at.memory_text(m)
    for d in ("2026-11-06", "2026-11-09", "2026-11-10"):
        at.apply_review(m, _rv(confirmed=["L1", "L9"]), d)
    assert at.lesson_status(m["lessons"][0]) == "قاعدة"
    at.mark_used(m, ["L1"])
    assert m["lessons"][0]["used"] == 1
    bad = {"condition": "هبوط", "action": "اشتر PUT", "evidence": ""}
    assert at.apply_review(m, _rv([bad]), "2026-11-11") == []                                # شرط غير قابل للفحص
    m = at.new_memory()
    at.apply_review(m, _rv([L1]), "2026-11-05")
    at.apply_review(m, _rv(contradicted=["L1"]), "2026-11-06")
    assert m["lessons"]
    at.apply_review(m, _rv(contradicted=["L1"]), "2026-11-09")
    assert not m["lessons"]                                                                  # تناقض يومين دون تأكيد


def test_memory_cap_keeps_rules():
    m = at.new_memory()
    at.apply_review(m, _rv([L1]), "2026-10-01")
    for i, d in enumerate(("2026-10-02", "2026-10-03", "2026-10-04")):
        at.apply_review(m, _rv(confirmed=["L1"]), d)
    for i in range(10):
        at.apply_review(m, _rv([dict(L1, condition=f"شرط سوقي رقم {i}")] * 3), f"2026-10-{10 + i}")
    assert len(m["lessons"]) == at.MAX_LESSONS and m["lessons"][0]["id"] == "L1"


def test_learner_runs_beside_a_with_shared_inputs(tmp_path, monkeypatch):
    for k, f in (("STATE", "ai_trader_state.json"), ("LOG", "ai_trader_log.csv"), ("JOURNAL", "ai_trader_journal.md"),
                 ("USAGE", "ai_trader_usage.csv"), ("MEMORY", "ai_trader_b_memory.json")):
        monkeypatch.setattr(at, k, str(tmp_path / f))
    calls, prompts, sent = [], [], []
    monkeypatch.setattr(at, "gather", lambda n: calls.append(1) or ("مدخلات", MENU, 767.2, []))
    monkeypatch.setattr(at, "quotes", lambda syms, meta=None: [{"symbol": s, "bid": 2.95, "ask": 3.0} for s in syms])
    monkeypatch.setattr(at, "send", lambda text, no_send: sent.append((text, no_send)))

    def fake(prompt, now, schema=None, system=None, model=None, kind="decide"):
        prompts.append((prompt, schema))
        return dict(_dec(), lessons_used=["L1"]) if schema is at.SCHEMA_B else _dec()
    monkeypatch.setattr(at, "ask_claude", fake)
    m = at.new_memory()
    at.apply_review(m, _rv([L1]), "2026-11-04")
    at.save_memory(m)
    json.dump(dict(at.new_state(), cash=3195.0), open(at.STATE, "w", encoding="utf-8"))

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return N
    monkeypatch.setattr(at, "datetime", FakeDT)
    at.main([])
    assert len(calls) == 1 and len(prompts) == 2                     # جمع واحد وقراران
    assert "دروسك" not in prompts[0][0] and "[L1]" in prompts[1][0]
    b = json.load(open(tmp_path / "ai_trader_b_state.json", encoding="utf-8"))
    assert b["start_cash"] == 3195.0 and len(b["positions"]) == 1
    assert at.load_memory()["lessons"][0]["used"] == 1
    assert [q for _, q in sent] == [False, True]                     # A يرسل الشراء، والمتعلّم صامت
    assert "دروس: L1" in (tmp_path / "ai_trader_b_log.csv").read_text(encoding="utf-8")
    assert at.STATE.endswith("ai_trader_state.json")


def test_learner_report_and_review_once(tmp_path, monkeypatch):
    monkeypatch.setattr(at, "MEMORY", str(tmp_path / "m.json"))
    monkeypatch.setattr(at, "after_exit", lambda cl: {})
    n = []
    monkeypatch.setattr(at, "ask_claude", lambda *a, **k: n.append(a) or _rv([L1]))
    st = at.new_state()
    at.close_position(st, at.open_position(st, MENU[1], 1, _dec()["buy"], N), 4.0, N, "هدف")
    t = N.replace(hour=16, minute=6)
    mem = at.load_memory()
    summary, added = at.run_review(st, t, str(tmp_path / "none.csv"), mem)
    assert summary == "جلسة" and added and n[0][2] is at.REVIEW_SCHEMA and "الصفقات المغلقة" in n[0][0]
    assert at.run_review(st, t, str(tmp_path / "none.csv"), at.load_memory()) == ("", []) and len(n) == 1
    txt = at.learner_report(st, t, [], summary, added, mem)
    assert "🧠 **المتعلّم" in txt and "دروس جديدة" in txt and "الذاكرة: 1 درساً" in txt
