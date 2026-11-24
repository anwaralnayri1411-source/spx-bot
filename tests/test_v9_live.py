import v9_live as vl

D = "2026-11-25"


def _e(t, side="PUT", strike=6900, entry=5.0, **k):
    return {"t": f"{D}T{t}-05:00", "tdate": D, "side": side, "strike": strike, "entry": entry, "S": 6950.0,
            "score": -4, "adx": 22.0, **k}


def _run(st, v, after=False):
    sent = []

    def send(text, reply):
        sent.append((text, reply))
        return 100 + len(sent)
    vl.step(st, v, D, send, after)
    return sent


def test_first_run_sends_nothing_then_new_entry_and_exit():
    st = {"sent": {}, "closed": [], "skipped": [], "summary_day": "", "init": False}
    v = {"open": [_e("09:35")], "closed": []}
    assert _run(st, v) == []                                  # أول تشغيل لا يرسل ما سبق
    e2 = _e("09:50", strike=6890)
    v["open"].append(e2)
    s = _run(st, v)
    assert len(s) == 1 and "إضافة رقم 2" in s[0][0] and "10.00$" in s[0][0] and "2.00$" in s[0][0]
    v["open"].remove(e2)
    v["closed"].append(dict(e2, exit_px=10.2, exit_t="10:20", usd=520.0))
    s = _run(st, v)
    assert len(s) == 1 and "هدف" in s[0][0] and s[0][1] == 101 and "+520$" in s[0][0]
    assert _run(st, v) == []                                  # لا تكرار


def test_cap_550_and_stop_and_summary():
    st = {"sent": {}, "closed": [], "skipped": [], "summary_day": "", "init": True}
    v = {"open": [_e("10:00", entry=5.8)], "closed": []}
    assert _run(st, v) == [] and st["skipped"]               # فوق 5.50$ لا يُرسل
    e = _e("10:05", side="CALL", strike=7000, entry=5.0)
    v["open"].append(e)
    s = _run(st, v)
    assert "الدخول الأول اليوم" in s[0][0] and "CALL" in s[0][0]
    v["open"] = []
    v["closed"] = [dict(e, exit_px=1.9, exit_t="10:40", usd=-310.0), dict(_e("10:00", entry=5.8), exit_px=1, usd=-480.0)]
    s = _run(st, v, after=True)
    assert "وقف" in s[0][0] and "لوحة SPX" in s[1][0] and "-310$" in s[1][0] and "1 صفقة" in s[1][0]
    assert "5.00→1.90$" in s[1][0] and "🛑 وقف" in s[1][0] and len(s) == 2
    assert _run(st, v, after=True) == []


def test_old_bot_flag_on():
    import options_bot as ob
    assert ob.V9_LIVE is True


def test_mirrors_reply_to_spx_card_and_summary():
    st = {"sent": {}, "closed": [], "skipped": [], "summary_day": "", "init": True}
    e = _e("10:05", side="CALL", strike=7000, entry=5.0)
    v = {"open": [e], "closed": []}
    m = {"root": "SPY", "open": [_e("10:05", side="CALL", strike=701, entry=0.5, delta=0.3, parent=e["t"])], "closed": []}
    sent = []

    def send(text, reply):
        sent.append((text, reply))
        return 100 + len(sent)
    vl.step(st, v, D, send, mirrors=[m])
    assert len(sent) == 2 and "SPY 701 CALL" in sent[1][0] and sent[1][1] == 101
    m["closed"] = [dict(m["open"].pop(), exit_px=1.0, exit_t="10:30", usd=50.0)]
    v["closed"] = [dict(v["open"].pop(), exit_px=10.0, exit_t="10:30", usd=500.0)]
    vl.step(st, v, D, send, True, [m])
    assert "بع SPY 701" in sent[3][0] and sent[3][1] == 102
    assert "لوحة SPX" in sent[4][0] and "+500$" in sent[4][0]
    assert "لوحة SPY" in sent[5][0] and "701" in sent[5][0] and "+50$" in sent[5][0] and "✅ هدف" in sent[5][0]


def test_no_trades_single_message():
    st = {"sent": {}, "closed": [], "skipped": [], "summary_day": "", "init": True}
    s = []
    vl.step(st, {"open": [], "closed": []}, D, lambda t, r: s.append(t) or 1, True, [{"root": "SPY", "open": [], "closed": []}])
    assert len(s) == 1 and "لا توصيات" in s[0]
