"""اختبارات بوت الشبكة (بلا شبكة: بورصة وهمية تنفّذ الأوامر المحددة حسب السعر)."""
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

HERE = os.path.join(os.path.dirname(__file__), "..", "crypto")
spec = importlib.util.spec_from_file_location("grid_bot", os.path.join(HERE, "grid_bot.py"))
gb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gb)


@pytest.fixture(autouse=True)
def tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(gb, "STATE", str(tmp_path / "s.json"))
    monkeypatch.setattr(gb, "LOG", str(tmp_path / "l.csv"))
    monkeypatch.setattr(gb, "MSGS", str(tmp_path / "m.jsonl"))
    monkeypatch.setattr(gb.time, "sleep", lambda s: None)
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)


class FakeEx:
    def __init__(self, px=100.0):
        self.px, self.orders, self.placed = px, {}, 0

    def spec(self):
        return 0.00001, 0.00000001, 0.1

    def ticker(self):
        return self.px

    def get_cl(self, cl):
        return self.orders.get(cl)

    def place(self, cl, side, ordtype, sz, px=None, quote=False):
        assert cl not in self.orders, "duplicate clOrdId"
        self.placed += 1
        o = {"clOrdId": cl, "side": side, "ordType": ordtype, "sz": float(sz), "px": float(px or 0),
             "state": "live", "accFillSz": "0", "avgPx": "", "fee": "0", "feeCcy": ""}
        self.orders[cl] = o
        if ordtype == "market":
            q = float(sz) / self.px if quote else float(sz)
            self._fill(o, self.px, q)
        return o

    def cancel(self, cl):
        if self.orders.get(cl, {}).get("state") == "live":
            self.orders[cl]["state"] = "canceled"

    def _fill(self, o, px, q):
        o.update(state="filled", avgPx=str(px), accFillSz=str(q))
        if o["side"] == "buy":
            o.update(fee=str(-q * 0.001), feeCcy="BTC")
        else:
            o.update(fee=str(-q * px * 0.001), feeCcy="USDT")

    def move(self, px):
        """السعر ينتقل إلى px فتنفَّذ الأوامر المحددة التي عبرها."""
        for o in self.orders.values():
            if o["state"] != "live" or o["ordType"] != "limit":
                continue
            if (o["side"] == "buy" and px <= o["px"]) or (o["side"] == "sell" and px >= o["px"]):
                self._fill(o, o["px"], o["sz"])
        self.px = px


def cfg(**k):
    c = dict(mode="demo", key="k", secret="s", passphrase="p", max_usd=1000.0, max_loss=300.0,
             rng=0.10, cells=20, enabled=True)
    c.update(k)
    return c


def st():
    return json.load(open(gb.STATE))


def test_live_needs_grid_flag():
    env = {"OKX_MODE": "live", "OKX_LIVE_CONFIRM": "yes"}
    assert gb.config(env)["mode"] == "demo"
    assert gb.config(dict(env, GRID_LIVE="yes"))["mode"] == "live"


def test_init_places_one_order_per_cell_buys_below_sells_above():
    ex = FakeEx(100.0)
    assert gb.run(cfg(), ex) == "ok"
    s = st()
    lim = [o for o in ex.orders.values() if o["ordType"] == "limit"]
    assert len(lim) == 20
    for o in lim:
        assert (o["side"] == "buy" and o["px"] < 100) or (o["side"] == "sell" and o["px"] > 100)
    spent = sum(o["sz"] * o["px"] for o in lim if o["side"] == "buy")
    init = [o for o in ex.orders.values() if o["ordType"] == "market"][0]
    assert spent + init["sz"] <= 1000.01   # لا يتجاوز الحصة
    # تشغيل ثانٍ بلا حركة: لا أوامر جديدة
    n = ex.placed
    gb.run(cfg(), ex)
    assert ex.placed == n and s["gen"] == 1


def test_round_trip_flips_cells_and_books_profit():
    ex = FakeEx(100.0)
    gb.run(cfg(), ex)
    ex.move(98.5)          # تنفيذ أوامر شراء
    gb.run(cfg(), ex)
    s = st()
    assert s["fills"] >= 1
    ex.move(100.5)         # تنفيذ البيع المقابل
    gb.run(cfg(), ex)
    s = st()
    assert s["realized"] > 0
    live = [o for o in ex.orders.values() if o["state"] == "live"]
    assert len(live) == 20   # كل خلية لها أمر واحد دائماً
    assert os.path.exists(gb.LOG)


def test_missing_order_is_replaced_once_not_duplicated():
    ex = FakeEx(100.0)
    gb.run(cfg(), ex)
    s = st()
    cl = [c["cl"] for c in s["cells"] if c["cl"]][0]
    del ex.orders[cl]      # كأن الإرسال لم يصل
    gb.run(cfg(), ex)
    assert ex.get_cl(cl) is not None
    gb.run(cfg(), ex)      # لا تكرار (place يرفض المعرّف المكرر)


def test_disable_cancels_and_sells_then_reenable_rebuilds():
    ex = FakeEx(100.0)
    gb.run(cfg(), ex)
    gb.run(cfg(enabled=False), ex)
    s = st()
    assert s["stopped"] == "disabled"
    assert all(c["btc"] == 0 for c in s["cells"])
    assert not [o for o in ex.orders.values() if o["state"] == "live"]
    assert gb.run(cfg(enabled=False), ex) == "disabled"
    gb.run(cfg(), ex)
    assert st()["gen"] == 2


def test_above_range_24h_recenters():
    ex = FakeEx(100.0)
    t0 = datetime(2026, 11, 5, 8, tzinfo=timezone.utc)
    gb.run(cfg(), ex, now=t0)
    ex.move(115)
    gb.run(cfg(), ex, now=t0 + timedelta(hours=1))
    assert st()["gen"] == 1 and st()["out_above"]
    gb.run(cfg(), ex, now=t0 + timedelta(hours=26))
    assert st()["stopped"] == "recenter"
    gb.run(cfg(), ex, now=t0 + timedelta(hours=26, minutes=5))
    s = st()
    assert s["gen"] == 2 and abs(s["center"] - 115) < 1e-6


def test_sol_config_and_files_are_separate(monkeypatch):
    env = {"GRID_INST": "SOL-USDT", "GRID_RANGE": "0.05", "GRID_SOL_CELLS": "30"}
    c = gb.config(env)
    assert c["inst"] == "SOL-USDT" and c["rng"] == 0.10 and c["cells"] == 30   # GRID_RANGE لـBTC لا يمس SOL
    assert gb.config({"GRID_RANGE": "0.05"})["rng"] == 0.05
    keep = (gb.INST, gb.COIN, gb.TAG, gb.STATE, gb.LOG, gb.MSGS)
    try:
        gb.setup("SOL-USDT")
        assert gb.COIN == "SOL" and gb.STATE.endswith("grid_bot_state_SOL.json")
        ex = FakeEx(90.0)
        gb.STATE = keep[3]   # مسار مؤقت للاختبار
        gb.run(cfg(), ex)
        assert all(cl.startswith("gsol") for cl in ex.orders)
    finally:
        gb.INST, gb.COIN, gb.TAG, gb.STATE, gb.LOG, gb.MSGS = keep
