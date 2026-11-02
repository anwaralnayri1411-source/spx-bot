"""اختبارات توصيات المستويات التجريبية (H-LEVELSIG). لا شبكة."""
from datetime import datetime

import pandas as pd

import level_signals as ls

N = datetime(2026, 11, 2, 11, 0, tzinfo=ls.NY)


def _flow(gex="120", cp=("0", "0"), pp=("0", "0"), times=("10:35", "10:55")):
    return pd.DataFrame([{"date": "2026-11-02", "time": t, "net_gex_m": gex, "call_prem": c, "put_prem": p,
                          "call_wall": "7660", "put_wall": "7600"} for t, c, p in zip(times, cp, pp)])


def test_flow_state_age_and_lean():
    fs = ls.flow_state(_flow(cp=("0", "5e6"), pp=("0", "1e6")), N)
    assert fs["gex"] == 120 and fs["lean"] == 4e6 and fs["call_wall"] == 7660
    assert ls.flow_state(_flow(times=("10:05", "10:30")), N) is None          # أقدم من 15 دقيقة
    assert ls.flow_state(None, N) is None


def test_last_complete_bar():
    idx = pd.date_range("2026-11-02 10:45", periods=3, freq="5min", tz="America/New_York")
    ix = pd.DataFrame({"Open": [1, 2, 3], "High": [1, 2, 3], "Low": [1, 2, 3], "Close": [1, 2, 3]}, index=idx)
    assert ls.last_complete_bar(ix, N)["Close"] == 3                           # شمعة 10:55 اكتملت عند 11:00
    assert ls.last_complete_bar(ix, datetime(2026, 11, 2, 10, 59, tzinfo=ls.NY))["Close"] == 2
    assert ls.last_complete_bar(ix, datetime(2026, 11, 3, 11, 0, tzinfo=ls.NY)) is None


def test_detect_bounce_and_rejection():
    sup, res = [(7600.0, ["جدار البوت"])], [(7650.0, ["رقم دائري"])]
    bounce = {"Open": 7601.0, "High": 7606.0, "Low": 7599.0, "Close": 7605.0}
    assert ls.detect(bounce, res, sup) == [("CALL", "ارتداد من دعم", 7600.0, ["جدار البوت"])]
    reject = {"Open": 7649.0, "High": 7651.0, "Low": 7640.0, "Close": 7642.0}
    assert ls.detect(reject, res, sup) == [("PUT", "رفض عند مقاومة", 7650.0, ["رقم دائري"])]
    assert ls.detect({"Open": 7605.0, "High": 7606.0, "Low": 7599.0, "Close": 7601.0}, res, sup) == []   # حمراء: لا ارتداد
    assert ls.detect({"Open": 7590.0, "High": 7599.0, "Low": 7585.0, "Close": 7598.0}, res, sup) == []   # أغلق تحت الدعم


def test_allowed_filters():
    sig = ("CALL", "ارتداد من دعم", 7600.0, [])
    st = {"sent": []}
    fs = {"gex": 100.0, "lean": 0.0}
    assert ls.allowed(sig, st, fs, N, False) is None
    assert ls.allowed(sig, st, {"gex": -5.0, "lean": 0.0}, N, False) == "الغاما ليست موجبة"
    assert ls.allowed(sig, st, {"gex": 5.0, "lean": -1.0}, N, False) == "التدفق ضد الجهة"
    assert ls.allowed(sig, st, None, N, False) == "لا قراءة حديثة للمراقب"
    assert ls.allowed(sig, st, fs, N, True).startswith("متوقفة")
    assert ls.allowed(sig, st, fs, N.replace(hour=15, minute=5), False) == "خارج نافذة الدخول"
    st = {"sent": [{"level": "7600.00", "side": "PUT", "time": N.replace(hour=9, minute=55).isoformat()}]}
    assert ls.allowed(sig, st, fs, N, False) == "المستوى استُخدم"
    st = {"sent": [{"level": "7620.00", "side": "CALL", "time": N.replace(hour=10, minute=45).isoformat()}]}
    assert ls.allowed(sig, st, fs, N, False) == "الجهة نفسها خلال 30د"
    st = {"sent": [{"level": str(x), "side": "PUT", "time": N.replace(hour=10).isoformat()} for x in (1, 2, 3)]}
    assert ls.allowed(sig, st, fs, N, False) == "الحد اليومي"


def test_halt_check():
    mk = lambda rets: pd.DataFrame({"status": ["وقف" if r < 0 else "هدف" for r in rets], "exit_ret": [str(r) for r in rets]})
    assert ls.halt_check(mk([-0.4] * 5))                                          # 5 خسائر متتالية
    assert not ls.halt_check(mk([-0.4] * 4 + [0.5]))
    assert ls.halt_check(mk([-0.4, 0.5, -0.4, -0.4] * 3 + [0.5, -0.4, -0.4]))     # 15 بمتوسط ≤ −15%
    assert not ls.halt_check(mk([]))


def test_opposite_open_note():
    bot = pd.DataFrame([{"date": "2026-11-02", "time": "09:45", "side": "CALL", "strike": "7665.0", "result": ""},
                        {"date": "2026-11-02", "time": "10:15", "side": "CALL", "strike": "7670.0", "result": "ربح"}])
    assert ls.opposite_open("PUT", "2026-11-02", bot) == ("09:45", "7665.0")
    assert ls.opposite_open("CALL", "2026-11-02", bot) is None


def test_track_exits_and_messages(tmp_path, monkeypatch, capsys):
    row = {c: "" for c in ls.COLS}
    row.update(id="a", date="2026-11-02", time="10:30", side="CALL", strike="7650", occ="X", entry="2.00", status="مفتوحة")
    log = pd.DataFrame([row, {**row, "id": "b", "occ": "Y"}, {**row, "id": "c", "occ": "Z"}])
    q = {"X": 3.1, "Y": 1.1, "Z": 2.2}
    out = ls.track(N, log, True, quote=lambda o: q[o])
    assert list(out.status) == ["هدف", "وقف", "مفتوحة"]
    late = ls.track(N.replace(hour=15, minute=55), out, True, quote=lambda o: q[o])
    assert late.loc[2, "status"] == "إغلاق" and abs(float(late.loc[2, "exit_ret"]) - 0.1) < 1e-9
    printed = capsys.readouterr().out
    assert "✅" in printed and "🛑" in printed and "⏹️" in printed


def test_entry_message_has_note_and_label():
    r = {"side": "PUT", "kind": "رفض عند مقاومة", "level": "7650", "tags": "رقم دائري", "spot": "7645", "strike": "7640",
         "entry": "3.20", "time": "11:00"}
    msg = ls.entry_message(r, {}, {"gex": 10.0, "lean": 0.0}, ("09:45", "7665.0"))
    assert "تجريبية" in msg and "CALL مفتوحة" in msg and "$320" in msg and "محايد" in msg


def test_main_gate(monkeypatch, capsys):
    monkeypatch.setattr(ls, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: datetime(2026, 11, 1, 11, 0, tzinfo=ls.NY)),
                                                       "fromisoformat": datetime.fromisoformat, "combine": datetime.combine}))
    ls.main([])
    assert "خارج" in capsys.readouterr().out
