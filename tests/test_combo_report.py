"""اختبارات تقرير H-COMBO: الجذر والإشارة الواحدة، الموعد المطابق بلا نظرة للمستقبل، الربط، المهارة، والحكم. بيانات مصطنعة."""
import csv
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import combo_report as cr  # noqa: E402


def edge_rows(entry="2026-10-26T10:35-04:00", side="CALL", root="SPXW", bot=0.30, opp=-0.40):
    base = {"entry_time": entry, "side": side, "contract": f"{root}261027C07670000"}
    return [dict(base, role="bot", pnl=str(bot)), dict(base, role="opp", pnl=str(opp), side="PUT" if side == "CALL" else "CALL")]


def test_root_and_slot_helpers():
    assert cr.root_of("SPXW261027C07670000") == "SPXW" and cr.root_of("SPY261027P00765000") == "SPY" and cr.root_of("") == ""
    assert cr.slot_for("2026-10-26T10:35-04:00") == "10:30" and cr.slot_for("2026-10-26T12:29-04:00") == "11:30"
    assert cr.slot_for("2026-10-26T13:30-04:00") == "13:30" and cr.slot_for("2026-10-26T10:05-04:00") is None


def test_signals_use_one_event_per_entry_time_preferring_spy_and_compute_skill():
    rows = edge_rows(root="SPXW", bot=0.2, opp=-0.2) + edge_rows(root="SPY", bot=0.5, opp=-0.3) + edge_rows(entry="2026-10-26T11:05-04:00", root="SPXW", bot=-0.4, opp=0.1)
    s = cr.signals(rows)
    assert [x["time"][11:16] for x in s] == ["10:35", "11:05"]
    assert s[0]["root"] == "SPY" and s[0]["pnl"] == 0.5 and s[0]["skill"] == pytest.approx(0.5 - 0.1)
    assert cr.signals([{"entry_time": "x", "role": "bot", "contract": "SPY261027C0", "side": "CALL", "pnl": "bad"}]) == []
    only_bot = [r for r in edge_rows() if r["role"] == "bot"]
    assert cr.signals(only_bot) == []


def cand(day="2026-10-26", slot="10:30", agree=1, status="closed"):
    return {"day": day, "slot": slot, "agree": str(agree), "status": status}


def test_join_matches_the_latest_earlier_slot_and_flags_direction_agreement():
    sigs = cr.signals(edge_rows(side="CALL") + edge_rows(entry="2026-10-26T11:40-04:00", side="PUT", bot=-0.2, opp=0.3)
                      + edge_rows(entry="2026-10-26T10:05-04:00") + edge_rows(entry="2026-10-26T12:40-04:00"))
    joined, missing = cr.join(sigs, [cand(slot="10:30", agree=1), cand(slot="11:30", agree=1)])
    assert missing == 2                                                                         # 10:05 قبل أول موعد، و12:40 بلا صف لموعد 12:30
    by = {e["time"][11:16]: e for e in joined}
    assert by["10:35"]["combo"] is True and by["11:40"]["combo"] is False                       # بوت يحتاج agree=-1
    j2, m2 = cr.join(sigs[:1], [cand(agree=1, status="open")])
    assert m2 == 1 and j2 == []                                                                # مواعيد غير مغلقة لا تُستعمل


def test_stat_boot_and_verdict_wait_confirm_reject():
    days = [f"2026-11-{d:02d}" for d in range(1, 29)] * 3
    rng = np.random.default_rng(1)
    good = [{"day": d, "skill": rng.normal(0.08, 0.1), "pnl": 0.1} for d in days]
    bad = [{"day": d, "skill": rng.normal(0.0, 0.1), "pnl": 0.0} for d in days]
    neg = [{"day": d, "skill": rng.normal(-0.08, 0.1), "pnl": -0.1} for d in days]
    assert "تحققت" in cr.verdict(good) and "لم تتحقق" in cr.verdict(bad) and "عكسية" in cr.verdict(neg)
    assert "لا حكم بعد" in cr.verdict(good[:10]) and "لا حكم بعد" in cr.verdict([])
    few_days = [{"day": "2026-11-01", "skill": 0.1, "pnl": 0.1}] * 60
    assert "لا حكم بعد" in cr.verdict(few_days)                                                # أحداث كثيرة لكن يوم واحد


def test_report_and_main_work_with_files_and_without(tmp_path, monkeypatch):
    e = tmp_path / "e.csv"
    c = tmp_path / "c.csv"
    rows = edge_rows(side="CALL")
    with open(e, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["entry_time", "side", "role", "contract", "pnl"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in w.fieldnames})
    with open(c, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["day", "slot", "agree", "status"])
        w.writeheader()
        w.writerow(cand())
    monkeypatch.setattr(cr, "EDGE7", str(e))
    monkeypatch.setattr(cr, "CAND", str(c))
    monkeypatch.setattr(cr, "OUT", str(tmp_path / "r.md"))
    assert cr.main([]) == 0
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "combo" in text and "⚖️" in text and "H-COMBO: لا حكم بعد" in text
    monkeypatch.setattr(cr, "EDGE7", str(tmp_path / "none.csv"))
    assert cr.main([]) == 0
    assert "لا إشارات مرتبطة" in (tmp_path / "r.md").read_text(encoding="utf-8")
