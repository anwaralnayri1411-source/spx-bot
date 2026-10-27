"""اختبارات ظل «الأفضل مجتمعين» H-BEST: السياق، القرار، الدخول والقراءات، والتقرير. بلا شبكة."""
import csv
import os
import sys
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import best_shadow as bs  # noqa: E402

NY = ZoneInfo("America/New_York")


def t(h, m, d=28):
    return datetime(2026, 10, d, h, m, tzinfo=NY)


def bars(day, closes, start="09:30"):
    idx = pd.date_range(f"{day} {start}", periods=len(closes), freq="5min", tz=NY)
    return pd.DataFrame({"Open": closes, "High": [c + 0.05 for c in closes], "Low": [c - 0.05 for c in closes], "Close": closes}, index=idx)


def test_context_momentum_first30_and_reverse_sweep():
    b = bars("2026-10-28", [700 + 0.1 * i for i in range(20)])            # صعود مستمر حتى 11:05
    ctx = bs.context(b, t(11, 10))
    assert ctx["mom"] == 1 and ctx["f30"] == 1
    b15 = pd.DataFrame({"Low": [100.0] * 20 + [99.0], "High": [101.0] * 21, "Close": [100.5] * 20 + [100.2]})
    assert bs.rev_sweep(b15) == -1                                        # كسر القاع ثم عاد فوقه → هبوط
    b15h = pd.DataFrame({"Low": [100.0] * 21, "High": [101.0] * 20 + [102.0], "Close": [100.5] * 20 + [100.8]})
    assert bs.rev_sweep(b15h) == 1


def test_decide_variants():
    ctx = {"mom": 1, "f30": 1, "rsweep": 1}
    assert set(bs.decide(t(10, 30), (8.0, 0.5), [], 0, ctx, {}, 3.0)) == {"S", "SC", "SR", "C"}
    assert set(bs.decide(t(10, 30), (8.0, 0.5), [], 0, dict(ctx, mom=-1, rsweep=0), {}, 3.0)) == {"S"}
    assert set(bs.decide(t(10, 30), (4.0, 0.5), [], 0, ctx, {}, 3.0)) == {"C"}
    assert bs.decide(t(10, 30), (8.0, 0.5), ["حدث كبير اليوم: FOMC."], 0, ctx, {}, 3.0) == {}
    assert bs.decide(t(15, 0), (8.0, 0.5), [], 0, ctx, {}, 3.0) == {}
    assert bs.decide(t(10, 30), None, [], 0, ctx, {}, 3.0) == {}
    assert "S" not in bs.decide(t(10, 30), (8.0, 0.5), [], 0, ctx, {"S": {"CALL": t(9, 50).isoformat()}}, 3.0)
    assert "S" not in bs.decide(t(10, 30), (7.5, 0.0), [], 2, ctx, {}, 3.0)          # صدمة مستوى 2 تخصم نقطة


class FakeLQ:
    def __init__(self):
        self.bids = {"C": 2.0, "P": 2.0}

    def spot(self, sym):
        return 700.4, "tasty"

    def occ(self, root, exp, side, k):
        return f"{root}{exp}{side[0]}{k}"

    def best_quote(self, c):
        s = "C" if "C70" in c else "P"
        return ({"ask": 2.0, "bid": self.bids[s]}, "alpaca")


def test_run_enters_and_reads_15_30_60(tmp_path, monkeypatch):
    for k in ("STATE", "LOG", "OUT"):
        monkeypatch.setattr(bs, k, str(tmp_path / k))
    ob = SimpleNamespace(MIN_EDGE=3.0, HOLIDAYS=set())
    a = {"reasons": [], "shock": {"level": 0}}
    lq = FakeLQ()
    ctx = {"mom": 1, "f30": 1, "rsweep": 0}
    assert set(bs.run(t(10, 30), a, {"live": (8.0, 0.0, 7000.0)}, ctx, lq, ob)) == {"S", "SC", "C"}
    assert bs._load()["open"][0]["exp"] == "2026-10-29" and bs._load()["open"][0]["strike"] == 700
    for (h, m), bid in (((10, 45), 2.4), ((11, 0), 2.6), ((11, 30), 3.0)):
        lq.bids = {"C": bid, "P": 4.0 - bid}
        bs.run(t(h, m), a, {"live": (1.0, 0.0, 7000.0)}, ctx, lq, ob)
    rows = list(csv.DictReader(open(bs.LOG, encoding="utf-8")))
    assert len(rows) == 3 and float(rows[0]["side_15"]) == 2.4 and float(rows[0]["side_30"]) == 2.6 and rows[0]["status"] == "closed"
    e = bs.events(rows, 30)[0]
    assert e["ret"] == pytest.approx(0.3) and e["skill"] == pytest.approx(0.3 - (0.3 - 0.3) / 2)
    assert "H-BEST S: لا حكم بعد (1 من 40" in "\n".join(bs.report(rows))
