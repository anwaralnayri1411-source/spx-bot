"""اختبارات الصقر (H-FALCON). لا شبكة ولا Claude."""
from datetime import datetime

import pandas as pd

import ai_trader as at
import falcon as fa

N = datetime(2026, 11, 5, 10, 0, tzinfo=fa.NY)
DAY = "2026-11-05"
MENU = [{"symbol": "SPY P", "under": "SPY", "side": "PUT", "strike": 740.0, "expiry": DAY, "bid": 2.9, "ask": 3.0, "delta": -0.4},
        {"symbol": "SPY C", "under": "SPY", "side": "CALL", "strike": 750.0, "expiry": DAY, "bid": 2.9, "ask": 3.0, "delta": 0.4}]


def eng(total=-5, adx=30, hit=None, new_bar=True, S=7500.0):
    return {"S": S, "total": total, "det": {"adx": adx}, "hit": hit or {}, "new_bar": new_bar, "bar": "b1"}


def dec(action="شراء", sym="SPY P"):
    return {"market_read": "هبوط", "factors": [], "action": action, "sell_ids": [], "sell_reason": "", "confidence": 0.6, "wait_reason": "غير واضح",
            "lessons_used": [], "buy": {"symbol": sym, "qty": 1, "short_reason": "هبوط واضح", "why_contract": "", "how_chosen": "", "rejected": [],
                                        "stop_price": 9, "target_price": 99, "invalidation": ""}}


def test_pick_signal():
    st = fa.new_state(2000)
    assert fa.pick_signal(eng(new_bar=False, hit={"PUT": (7500, 3)}), st, DAY) is None
    assert fa.pick_signal(eng(total=-2, hit={"PUT": (7500, 3)}), st, DAY) is None
    assert fa.pick_signal(eng(adx=10, hit={"PUT": (7500, 3)}), st, DAY) is None
    assert fa.pick_signal(eng(), st, DAY) is None                                          # لا خط ولا إشارة سابقة
    assert fa.pick_signal(eng(hit={"PUT": (7500, 3)}), st, DAY) == {"side": "PUT", "kind": "خط", "line": 7500}
    st["last_sig"]["PUT"] = {"S": 7510.0, "day": DAY}
    assert fa.pick_signal(eng(S=7506), st, DAY) is None                                    # تقدم 4 نقاط
    assert fa.pick_signal(eng(S=7505), st, DAY)["kind"] == "سلّم"
    assert fa.pick_signal(eng(S=7505), st, "2026-11-06") is None                           # يوم جديد


def test_may_call_limits():
    st = fa.new_state(2000)
    assert fa.may_call(st, N)
    st["last_call"] = N.isoformat()
    assert not fa.may_call(st, N.replace(minute=4)) and fa.may_call(st, N.replace(minute=5))
    st["calls_today"] = fa.MAX_CALLS
    assert not fa.may_call(st, N.replace(minute=30))
    assert not fa.may_call(fa.new_state(2000), N.replace(hour=15, minute=46))


def test_engine_rules_force_side_and_exits():
    sig = {"side": "PUT"}
    d = fa.apply_engine_rules(dec(), sig, MENU)
    assert d["buy"]["stop_price"] == 1.5 and d["buy"]["target_price"] == 4.5
    d = fa.apply_engine_rules(dec(sym="SPY C"), sig, MENU)
    assert d["action"] == "انتظار" and "خالف" in d["wait_reason"]
    assert fa.apply_engine_rules(dec("انتظار"), sig, MENU)["action"] == "انتظار"
    assert fa.decision_word(None, False) == "فشل الطلب" and fa.decision_word(dec(), True) == "قبول" and fa.decision_word(dec("انتظار"), False) == "رفض"


def test_fill_moves_and_text():
    i = pd.date_range("2026-11-05 10:01", periods=3, freq="1min", tz=fa.NY)
    m1 = pd.DataFrame({"h": [7505.0, 7501, 7499], "l": [7495.0, 7480, 7470], "c": [7500.0, 7490, 7475]}, index=i)
    s = pd.DataFrame([{"time": "2026-11-05T10:00-05:00", "side": "PUT", "S": "7500", "decision": "رفض", "fav_pts": "", "adv_pts": "", "end_pts": ""},
                      {"time": "2026-11-05T10:00-05:00", "side": "CALL", "S": "7500", "decision": "قبول", "fav_pts": "", "adv_pts": "", "end_pts": ""}])
    r = fa.fill_moves(s, m1)
    assert r.iloc[0]["fav_pts"] == 30.0 and r.iloc[0]["adv_pts"] == -5.0 and r.iloc[0]["end_pts"] == 25.0
    assert r.iloc[1]["end_pts"] == -25.0
    t = fa.signals_text(r)
    assert "إشارات المحرك: 2" in t and "المرفوضة" in t and "+25.0" in t and "-25.0" in t
    assert fa.signals_text(pd.DataFrame(columns=fa.SIG_COLS)) == "إشارات المحرك اليوم: 0"


def setup(tmp_path, monkeypatch, decision):
    for k, f in (("STATE", "s.json"), ("LOG", "l.csv"), ("JOURNAL", "j.md"), ("USAGE", "u.csv"), ("SIGNALS", "sig.csv")):
        monkeypatch.setattr(fa, k, str(tmp_path / f))
    monkeypatch.setattr(fa, "DIR", str(tmp_path))
    monkeypatch.setattr(fa, "A_STATE", str(tmp_path / "none.json"))
    for k in ("STATE", "LOG", "JOURNAL", "USAGE"):
        monkeypatch.setattr(at, k, getattr(at, k))
    monkeypatch.setattr(at, "gather", lambda n: ("مدخلات", MENU, {"SPY": 745.0}, []))
    monkeypatch.setattr(at, "load_memory", at.new_memory)
    seen = []
    monkeypatch.setattr(at, "ask_claude", lambda p, n, s=None, r=None: seen.append((p, r)) or decision)
    monkeypatch.setattr(at, "quotes", lambda syms, meta=None: [{"symbol": s, "bid": 2.9, "ask": 3.0} for s in syms])
    monkeypatch.setattr(fa.ml, "read_engine", lambda now, sess, last: eng(hit={"PUT": (7500, 3)}))

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return N
    monkeypatch.setattr(fa, "datetime", FakeDT)
    return seen


def test_main_buys_on_signal_silently(tmp_path, monkeypatch):
    seen = setup(tmp_path, monkeypatch, dec())
    sent = []
    monkeypatch.setattr(fa, "send", lambda t, n: sent.append(t))
    fa.main([])
    st = fa.load_state()
    p = st["positions"][0]
    assert p["stop"] == 1.5 and p["target"] == 4.5 and st["calls_today"] == 1 and st["last_bar"] == "b1"
    assert "إشارة المحرك الآن: PUT" in seen[0][0] and "الصقر" in seen[0][1] and not sent
    s = pd.read_csv(fa.SIGNALS, dtype=str, keep_default_na=False)
    assert s.iloc[0]["decision"] == "قبول" and s.iloc[0]["contract"] == "SPY P" and s.iloc[0]["reason"] == "هبوط واضح"
    fa.main([])                                                                             # الفاصل الزمني: لا طلب ثانٍ
    assert len(seen) == 1 and pd.read_csv(fa.SIGNALS, dtype=str).iloc[1]["decision"] == "لم يُطلب"


def test_main_records_rejection(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, dec("انتظار"))
    fa.main([])
    s = pd.read_csv(fa.SIGNALS, dtype=str, keep_default_na=False)
    assert s.iloc[0]["decision"] == "رفض" and s.iloc[0]["reason"] == "غير واضح" and not fa.load_state()["positions"]


def test_report_once_with_signals(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, dec())
    sent = []
    monkeypatch.setattr(fa, "send", lambda t, n: sent.append(t))
    monkeypatch.setattr(at, "after_stats", lambda cl: {})
    i = pd.date_range("2026-11-05 10:01", periods=2, freq="1min", tz=fa.NY)
    monkeypatch.setattr(fa.ml, "spx_m1", lambda: (pd.DataFrame({"h": [7500.0, 7500], "l": [7490.0, 7480], "c": [7495.0, 7485]}, index=i), 7485.0))
    fa.main([])
    t = N.replace(hour=16, minute=5)
    fa.datetime.now = classmethod(lambda cls, tz=None: t)
    monkeypatch.setattr(fa.ml, "read_engine", lambda now, sess, last: None)
    fa.main([])
    fa.main([])
    assert len(sent) == 1 and "🦅 **الصقر · نهاية الجلسة**" in sent[0] and "إشارات المحرك: 1" in sent[0]
    assert pd.read_csv(fa.SIGNALS).iloc[0]["end_pts"] == 15.0
