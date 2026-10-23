"""اختبارات ظل الحجب: تصنيف الأسباب، الصندوق الوارد، إزالة التكرار، قواعد الخروج، التقرير، وعدم انحراف قواعده عن البوتين. بلا شبكة."""
import csv
import json
import os
import random
from datetime import datetime, timedelta

import pytest

import blocked_shadow as bs
import options_bot as ob
import stocks_bot as sb

NY = bs.NY


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bs.data_gaps, "path_for", lambda project: str(tmp_path / f"gaps_{project}.csv"))
    (tmp_path / "research").mkdir()
    return tmp_path


def t(h, m, day=22):
    return datetime(2026, 10, day, h, m, tzinfo=NY)


# ---------------- عدم الانحراف عن البوتين ----------------
def test_exit_rules_match_the_bots():
    assert bs.TAKE_PROFIT == ob.TAKE_PROFIT and bs.STOP_LOSS == ob.STOP_LOSS
    assert bs.RULES["spx"]["min_age"] == ob.MIN_AGE_MIN and bs.RULES["stocks"]["min_age"] == sb.MIN_AGE_MIN
    assert bs.RULES["spx"]["cooldown_min"] == ob.REPEAT_MINUTES
    assert bs.ROOTS == {k: v for k, v in ob.LIVE_ROOTS.items()}


def test_spx_reason_prefixes_still_match_the_bot_messages():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "options_bot.py"), encoding="utf-8").read()
    for pre, _ in bs._SPX_PREFIX:
        assert pre in src, pre
    assert bs.WEAK_PREFIX in src


# ---------------- التصنيف ----------------
def test_spx_tags_need_enough_raw_edge_and_a_known_blocker():
    assert bs.spx_tags(["مخاطر الأخبار والجيوسياسة مرتفعة: x."], 4.0, 3, 3.0) == ["news_risk"]
    assert bs.spx_tags(["مخاطر الأخبار والجيوسياسة مرتفعة: x."], 2.0, 3, 3.0) == []          # لا توصية أصلاً
    assert bs.spx_tags(["لا اتجاه واضح (صعود 3.0 مقابل هبوط 0.0)."], 3.0, 1, 3.0) == []      # فرق ضعيف لا حجب
    assert bs.spx_tags(["لا اتجاه واضح (صعود 3.0 مقابل هبوط 0.0)."], 3.0, 2, 3.0) == ["news_weak"]   # خصم الأخبار أنزله
    assert bs.spx_tags(["بعد وقت آخر دخول (x).", "حدث كبير اليوم: FOMC. x"], 5.0, 0, 3.0) == ["late_entry", "event_day"]
    assert bs.spx_tags(["صدور تقرير CPI اليوم؛ ننتظر"], 5.0, 0, 3.0) == ["report_day"]
    assert bs.spx_tags(["لا توجد عقود Call بسعر $100-$350 مع سيولة كافية الآن."], 5.0, 0, 3.0) == []   # لا عقد لنتابعه


# ---------------- الصندوق الوارد ----------------
def spx_a(side="CALL"):
    return {"bull": 6.0, "bear": 0.5, "side": side, "exp": "2026-10-22", "strength": 8.0, "c": {"S": 7650.0}}


def k(strike, price, sym="^SPX", spot=7650.0):
    return {"strike": strike, "price": price, "sym": sym, "spot": spot}


def inbox(project):
    return list(csv.DictReader(open(bs.INBOX[project], encoding="utf-8")))


def test_log_spx_keeps_first_contract_per_instrument_only():
    n = bs.log_spx(t(10, 5), spx_a(), ["news_risk"], [k(7700, 1.2), k(7710, 0.9), k(764, 1.5, "SPY", 763.0)])
    rows = inbox("spx")
    assert n == 2 and [r["sym"] for r in rows] == ["SPX", "SPY"] and [r["root"] for r in rows] == ["SPXW", "SPY"]
    assert rows[0]["tag"] == "news_risk" and rows[0]["entry"] == "1.2" and rows[0]["edge"] == "5.5"


def test_log_spx_does_nothing_without_tags_or_contracts():
    assert bs.log_spx(t(10, 5), spx_a(), [], [k(7700, 1.2)]) == 0
    assert bs.log_spx(t(10, 5), spx_a(), ["news_risk"], []) == 0
    assert bs.log_spx(t(10, 5), {**spx_a(), "block_tags": [], "blocked_contracts": []}) == 0
    assert not os.path.exists(bs.INBOX["spx"])


def test_log_spx_reads_tags_from_the_analysis_dict():
    a = {**spx_a(), "block_tags": ["late_entry", "event_day"], "blocked_contracts": [k(7700, 1.2)]}
    assert bs.log_spx(t(15, 5), a) == 1 and inbox("spx")[0]["tag"] == "late_entry+event_day"


def test_log_stocks_takes_first_contract_and_survives_bad_input():
    idea = {"tag": "same_side_cap", "ticker": "CVX", "side": "CALL", "edge": 4.0, "strength": 6.0, "S": 215.0,
            "contracts": [{"strike": 220.0, "price": 1.86, "exp": "2026-10-30"}, {"strike": 225.0, "price": 1.0, "exp": "2026-10-30"}]}
    assert bs.log_stocks(t(10, 12), [idea, {"tag": "x", "ticker": "A", "side": "PUT", "contracts": []}]) == 1
    r = inbox("stocks")[0]
    assert r["sym"] == "CVX" and r["root"] == "CVX" and r["exp"] == "2026-10-30" and r["strike"] == "220.0"


def test_inbox_failure_never_raises(monkeypatch):
    monkeypatch.setitem(bs.INBOX, "spx", os.path.join("no_such_dir", "x.csv"))
    assert bs.append_inbox("spx", [{"id": "1"}]) == 0


# ---------------- إزالة التكرار ----------------
def put_rows(project, rows):
    with open(bs.INBOX[project], "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=bs.IN_COLS)
        w.writeheader()
        w.writerows(rows)


def row(project, n, sym="SPX", side="CALL", entry=1.0, tag="news_risk", strike=7700.0, exp="2026-10-22", i=0):
    return {"id": f"{n:%H%M}-{project}-{sym}-{side}-{i}", "time": n.isoformat(timespec="minutes"), "project": project, "tag": tag,
            "sym": sym, "root": bs.ROOTS.get(sym, sym), "side": side, "strike": strike, "exp": exp, "entry": entry, "spot": 7650,
            "edge": 5, "strength": 8}


def test_spx_repeats_inside_the_cooldown_are_dropped_and_later_ones_kept():
    s = bs.load_state()
    put_rows("spx", [row("spx", t(10, 5))])
    assert bs.ingest(s, t(10, 10)) == 1
    put_rows("spx", [row("spx", t(10, 5)), row("spx", t(10, 35), i=1), row("spx", t(10, 35), side="PUT", i=2)])
    assert bs.ingest(s, t(10, 36)) == 1                      # CALL مكرر خلال 90 دقيقة يُسقَط، وPUT اتجاه مستقل يُقبل
    put_rows("spx", [row("spx", t(11, 36), i=3)])
    assert bs.ingest(s, t(11, 40)) == 1                      # بعد 90 دقيقة من أول CALL مقبول: يُقبل
    assert [(i["side"], i["entry_time"][11:16]) for i in s["open"]] == [("CALL", "10:05"), ("PUT", "10:35"), ("CALL", "11:36")]


def test_stocks_idea_is_kept_once_per_day_per_direction():
    s = bs.load_state()
    put_rows("stocks", [row("stocks", t(10, 12), "CVX", exp="2026-10-30", i=0)])
    assert bs.ingest(s, t(10, 15)) == 1
    put_rows("stocks", [row("stocks", t(10, 42), "CVX", exp="2026-10-30", i=1), row("stocks", t(10, 42), "CVX", "PUT", exp="2026-10-30", i=2)])
    assert bs.ingest(s, t(10, 45)) == 1                      # CALL نفسه في اليوم نفسه يُسقَط، وPUT يُقبل
    put_rows("stocks", [row("stocks", t(10, 12, day=23), "CVX", exp="2026-10-30", i=3)])
    assert bs.ingest(s, t(10, 15, day=23)) == 1              # يوم جديد: يُقبل
    assert len(s["open"]) == 3


def test_stale_and_priceless_rows_are_skipped_and_ids_are_not_reprocessed():
    put_rows("spx", [row("spx", t(9, 40)), row("spx", t(10, 30), entry=0.0, i=1)])
    s = bs.load_state()
    assert bs.ingest(s, t(10, 31)) == 0 and len(s["seen"]) == 2
    assert bs.ingest(s, t(10, 31)) == 0


# ---------------- قواعد الخروج ----------------
def item(project="spx", exp="2026-10-22", entry=1.0, at=t(10, 0)):
    return {"project": project, "exp": exp, "entry": entry, "entry_time": at.isoformat(timespec="minutes"), "quotes": 0,
            "peak_pnl": 0.0, "close_pnl": "", "out": "", "out_time": "", "out_pnl": ""}


def test_target_hits_immediately_but_stop_waits_for_minimum_age():
    it = item()
    assert bs.step(it, 0.5, t(10, 5)) == "" and it["close_pnl"] == -0.5          # عمر 5 د < 15 د: لا وقف
    assert bs.step(it, 0.5, t(10, 20)) == "stop" and it["out_pnl"] == -0.5
    tp = item()
    assert bs.step(tp, 1.6, t(10, 1)) == "tp"


def test_final_sale_only_on_expiry_day_and_stocks_use_their_own_age():
    it = item(exp="2026-10-22")
    assert bs.step(it, 1.0, t(15, 30)) == "final"
    later = item(exp="2026-10-30")
    assert bs.step(later, 1.0, t(15, 50)) == ""                                   # ليس يوم الانتهاء
    st = item("stocks", "2026-10-30", at=t(10, 0))
    assert bs.step(st, 0.5, t(10, 20)) == ""                                      # الشركات: عمر أدنى 30 د
    assert bs.step(st, 0.5, t(10, 31)) == "stop"


def test_expiry_closes_with_last_value_or_marks_no_data():
    it = item(exp="2026-10-22")
    it.update(close_pnl=-0.2)
    assert bs.expire_if_past(it, t(9, 40, day=23)) and it["out"] == "expired" and it["out_pnl"] == -0.2
    empty = item(exp="2026-10-22")
    assert bs.expire_if_past(empty, t(9, 40, day=23)) and empty["out"] == "no_data"
    assert not bs.expire_if_past(item(exp="2026-10-23"), t(9, 40, day=23))


def test_session_window():
    assert bs.in_session(t(9, 30)) and not bs.in_session(t(9, 29)) and not bs.in_session(t(16, 0))
    assert not bs.in_session(datetime(2026, 10, 24, 11, 0, tzinfo=NY))             # سبت


# ---------------- التشغيل كاملاً ----------------
def test_run_ingests_tracks_and_logs_closed_items_with_fake_quotes():
    n0 = t(10, 0)
    put_rows("spx", [row("spx", n0, entry=1.0)])
    occ = bs.occ("SPXW", "2026-10-22", "CALL", 7700.0)
    book = {occ: ({"bid": 1.2, "ask": 1.3}, "alpaca")}
    s = bs.run(n0 + timedelta(minutes=1), fetch=lambda occs: book)
    assert len(s["open"]) == 1 and s["open"][0]["close_pnl"] == 0.2
    book[occ] = ({"bid": 1.6, "ask": 1.7}, "alpaca")
    s = bs.run(n0 + timedelta(minutes=6), fetch=lambda occs: book)
    assert s["open"] == []
    log = list(csv.DictReader(open(bs.LOG, encoding="utf-8")))
    assert len(log) == 1 and log[0]["out"] == "tp" and log[0]["out_pnl"] == "0.6" and log[0]["tag"] == "news_risk"


def test_run_outside_session_never_fetches_and_missing_quotes_do_not_crash():
    put_rows("spx", [row("spx", t(15, 55))])
    calls = []
    bs.run(t(15, 56), fetch=lambda o: calls.append(o) or {})
    assert len(calls) == 1                                                         # داخل الجلسة: جُلب مرة
    bs.run(datetime(2026, 10, 22, 16, 30, tzinfo=NY), fetch=lambda o: calls.append(o) or {})
    assert len(calls) == 1                                                         # بعد الإغلاق: لا جلب
    s = bs.run(datetime(2026, 10, 23, 9, 45, tzinfo=NY), fetch=lambda o: {})
    assert s["open"] == []                                                         # انتهى بلا تسعيرة → no_data خارج الإحصاء


# ---------------- التقرير ----------------
def write_log(rows):
    with open(bs.LOG, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=bs.LOG_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({**{c: "" for c in bs.LOG_COLS}, **r})


def mk(n, tag, pnl, day, out="final", project="spx"):
    return {"id": str(n), "project": project, "tag": tag, "sym": "SPX", "side": "CALL", "entry_time": f"2026-10-{day:02d}T10:00-04:00",
            "out": out, "out_pnl": pnl}


def test_report_withholds_a_verdict_on_small_samples():
    write_log([mk(i, "news_risk", -0.4, 1 + i) for i in range(5)])
    bs.report()
    text = open(bs.REPORT, encoding="utf-8").read()
    assert "لا حكم بعد (5 من 30)" in text and "مخاطر أخبار مرتفعة" in text and "الحجب أصاب (المحجوب" not in text


def test_report_says_blocking_helped_only_when_interval_is_below_zero():
    rng = random.Random(4)
    write_log([mk(i, "news_risk", -0.3 + rng.uniform(-0.1, 0.1), 1 + i % 20) for i in range(40)])
    bs.report()
    assert "الحجب أصاب (المحجوب كان سيخسر)" in open(bs.REPORT, encoding="utf-8").read()


def test_report_says_blocking_missed_profit_and_handles_mixed_results():
    rng = random.Random(5)
    write_log([mk(i, "late_entry", 0.3 + rng.uniform(-0.1, 0.1), 1 + i % 20) for i in range(40)])
    bs.report()
    assert "الحجب فوّت ربحاً (المحجوب كان سيربح)" in open(bs.REPORT, encoding="utf-8").read()
    write_log([mk(i, "event_day", rng.choice([-0.4, 0.5]), 1 + i % 20) for i in range(40)])
    bs.report()
    assert "غير حاسم" in open(bs.REPORT, encoding="utf-8").read()


def test_report_ignores_no_data_rows_and_works_when_empty():
    write_log([mk(1, "news_risk", "", 1, out="no_data")])
    bs.report()
    assert "لا حالات مغلقة بعد" in open(bs.REPORT, encoding="utf-8").read()


def test_bootstrap_needs_three_days():
    assert bs.boot_ci({"a": [0.1], "b": [0.2]}) is None
    lo, hi = bs.boot_ci({"a": [0.1], "b": [0.2], "c": [0.3]})
    assert lo <= 0.2 <= hi


# ---------------- استخراج اختيار العقود من analyze لم يغيّر السلوك ----------------
def test_pick_all_tags_sorts_and_truncates_like_the_original_block(monkeypatch):
    monkeypatch.setattr(ob, "ALT_SYMBOL", "")
    monkeypatch.setattr(ob, "NDX_SYMBOL", "")
    monkeypatch.setattr(ob, "_live_spot", lambda sym, fb: fb)
    monkeypatch.setattr(ob, "_live_chain", lambda df, S: df)
    monkeypatch.setattr(ob, "spxw_rows", lambda df: df)
    monkeypatch.setattr(ob, "pick_contracts", lambda side, df, S, T, vix, *a: [{"score": 5.0}, {"score": 9.0}, {"score": 7.0}, {"score": 6.0}])

    class Ch:
        calls = puts = object()

    out = ob._pick_all("CALL", Ch(), 7650.0, 0.001, {"vix": 18.0}, "2026-10-22")
    assert [x["score"] for x in out] == [9.0, 7.0, 6.0] and all(x["sym"] == "^SPX" and x["spot"] == 7650.0 for x in out)
    assert len(out) == ob.MAX_CONTRACTS


def test_blocked_idea_carries_only_what_the_shadow_needs():
    i = {"ticker": "X", "side": "PUT", "edge": -4.0, "strength": 6.0, "S": 10.0, "contracts": [{"strike": 9, "price": 1, "exp": "2026-10-30"}], "extra": 1}
    assert sb._blocked_idea("max_ideas_cap", i) == {"tag": "max_ideas_cap", "ticker": "X", "side": "PUT", "edge": -4.0, "strength": 6.0,
                                                     "S": 10.0, "contracts": i["contracts"]}
