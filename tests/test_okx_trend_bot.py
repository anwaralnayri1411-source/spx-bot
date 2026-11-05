"""اختبارات بوت BTC على OKX (بلا شبكة)."""
import base64
import hashlib
import hmac
import importlib.util
import json
import os
from datetime import datetime, timezone

import pytest

spec = importlib.util.spec_from_file_location(
    "okx_trend_bot", os.path.join(os.path.dirname(__file__), "..", "crypto", "okx_trend_bot.py"))
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)


@pytest.fixture(autouse=True)
def tmp_files(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "STATE", str(tmp_path / "s.json"))
    monkeypatch.setattr(bot, "LOG", str(tmp_path / "l.csv"))
    monkeypatch.setattr(bot, "MSGS", str(tmp_path / "m.jsonl"))
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)


def closes(last, n=60, base=100.0):
    return [(f"2026-09-{i:02d}" if i < 31 else f"2026-10-{i-30:02d}", base) for i in range(1, n)] + \
        [("2026-11-04", last)]


def cfg(**k):
    c = dict(mode="demo", key="k", secret="s", passphrase="p", max_usd=1000.0, max_loss=300.0)
    c.update(k)
    return c


class FakeOkx:
    def __init__(self, px=100.0, usdt=5000.0, btc=1.0):
        self.px, self.bal, self.orders = px, {"USDT": usdt, "BTC": btc}, []

    def instrument(self):
        return 0.00001, "0.00000001"

    def balances(self):
        return dict(self.bal)

    def market(self, side, sz, on_placed=None):
        self.orders.append((side, sz))
        if on_placed:
            on_placed("1")
        if side == "buy":
            q = float(sz) / self.px
            self.bal["USDT"] -= float(sz)
            self.bal["BTC"] += q * 0.999
            return {"avgPx": str(self.px), "accFillSz": str(q), "fee": str(-q * 0.001), "feeCcy": "BTC",
                    "state": "filled"}
        q = float(sz)
        self.bal["BTC"] -= q
        self.bal["USDT"] += q * self.px * 0.999
        return {"avgPx": str(self.px), "accFillSz": sz, "fee": str(-q * self.px * 0.001), "feeCcy": "USDT",
                "state": "filled"}


def test_signal_above_and_below():
    assert bot.signal(closes(110))[0] == 1
    assert bot.signal(closes(90))[0] == 0
    with pytest.raises(ValueError):
        bot.signal(closes(110, n=10))


def test_live_needs_confirm():
    assert bot.config({"OKX_MODE": "live"})["mode"] == "demo"
    assert bot.config({"OKX_MODE": "live", "OKX_LIVE_CONFIRM": "yes"})["mode"] == "live"
    assert bot.config({})["mode"] == "demo"


def test_demo_header_and_signature():
    o = bot.Okx(cfg())
    h = o.headers("GET", "/api/v5/account/balance")
    assert h["x-simulated-trading"] == "1"
    exp = base64.b64encode(hmac.new(b"s", (h["OK-ACCESS-TIMESTAMP"] + "GET/api/v5/account/balance").encode(),
                                    hashlib.sha256).digest()).decode()
    assert h["OK-ACCESS-SIGN"] == exp
    assert "x-simulated-trading" not in bot.Okx(cfg(mode="live")).headers("GET", "/x")


def test_buy_uses_only_bot_sleeve_then_sell():
    f = FakeOkx(px=110)
    assert bot.run(cfg(), f, closes(110)) == "ok"
    side, sz = f.orders[0]
    assert side == "buy" and float(sz) <= 1000   # لا يلمس رصيد 5000 كاملاً
    st = json.load(open(bot.STATE))
    assert st["bot_btc"] > 0 and st["bot_usdt"] < 10
    # اليوم نفسه مرة ثانية: لا أمر
    assert bot.run(cfg(), f, closes(110)) == "skip" and len(f.orders) == 1
    # إشارة بيع: يبيع كمية البوت فقط لا BTC الموجود أصلاً (1.0)
    c = closes(90)
    c[-1] = ("2026-11-05", 90)
    f.px = 90
    bot.run(cfg(max_loss=10000), f, c)
    side, sz = f.orders[1]
    assert side == "sell" and float(sz) == pytest.approx(st["bot_btc"], rel=1e-6) and f.bal["BTC"] == pytest.approx(1.0)


def test_hold_when_cash_signal_and_no_btc():
    f = FakeOkx()
    bot.run(cfg(), f, closes(90))
    assert f.orders == []
    assert "hold" in open(bot.LOG).read()


def test_no_keys_logs_signal_only():
    assert bot.run(cfg(key=""), None, closes(110)) == "no_keys"


def test_max_loss_halts_and_sells():
    f = FakeOkx(px=110)
    bot.run(cfg(), f, closes(110))
    c = closes(50, base=40)
    c[-1] = ("2026-11-05", 50)   # فوق المتوسط (40) لكن الحصة خسرت أكثر من 300$
    f.px = 50
    bot.run(cfg(), f, c)
    st = json.load(open(bot.STATE))
    assert st.get("halted") and f.orders[-1][0] == "sell" and st["bot_btc"] < 1e-6
    c2 = list(c)
    c2[-1] = ("2026-11-06", 60)
    n = len(f.orders)
    bot.run(cfg(), f, c2)
    assert len(f.orders) == n   # متوقف: لا أوامر


def test_pending_order_is_reconciled_not_repeated():
    f = FakeOkx(px=110)
    st = bot.load_state(cfg())
    st.update(start_px=110, pending={"oid": "9", "side": "buy"})
    bot.save_state(st)
    f.order = lambda oid: {"state": "filled", "avgPx": "110", "accFillSz": str(990 / 110), "fee": "0",
                           "feeCcy": "BTC"}
    bot.run(cfg(), f, closes(110))
    assert f.orders == []   # الأمر السابق نُفّذ فلا شراء جديد
    st = json.load(open(bot.STATE))
    assert "pending" not in st and st["bot_btc"] == pytest.approx(9.0)
