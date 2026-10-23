"""اختبارات ظل قواعد المحترفين: التقاط العقود، الصندوق الوارد، إزالة التكرار، قاعدتا الخروج، سقف الاحتفاظ، والتقرير. بلا شبكة."""
import csv
import os
import random
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

import options_bot as ob
import pro_rules_shadow as ps
import stocks_bot as sb

NY = ps.NY


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ps.data_gaps, "path_for", lambda project: str(tmp_path / f"gaps_{project}.csv"))
    (tmp_path / "research").mkdir()


def t(h, m, day=20):
    return datetime(2026, 10, day, h, m, tzinfo=NY)


def test_exit_constants_match_the_stocks_bot():
    assert ps.TP == sb.TAKE_PROFIT and ps.STOP == sb.STOP_LOSS and ps.MIN_AGE_MIN == sb.MIN_AGE_MIN


# ---------------- التقاط العقود ----------------
def test_monthly_expiry_prefers_the_third_friday_inside_the_window():
    opts = ["2026-10-23", "2026-11-13", "2026-11-20", "2026-11-27", "2026-12-18"]
    assert ps.monthly_expiry(opts, date(2026, 10, 15)) == ("2026-11-20", 36)
    assert ps.monthly_expiry(["2026-10-30"], date(2026, 10, 15)) is None
    assert ps.monthly_expiry(["2026-11-13"], date(2026, 10, 15)) == ("2026-11-13", 29)        # لا جمعة ثالثة: أول ما في النافذة


def chain(spot=100.0):
    rows = []
    for k in range(80, 125, 5):
        mid = max(0.2, spot - k + 4.0) if k < spot else max(0.2, 4.0 - (k - spot) * 0.25)
        rows.append({"strike": float(k), "bid": round(mid * 0.97, 2), "ask": round(mid * 1.03, 2), "openInterest": 500, "impliedVolatility": 0.30})
    return pd.DataFrame(rows)


def test_pick_variant_chooses_the_closest_delta_and_rejects_wide_or_illiquid_quotes():
    df = chain()
    a = ps.pick_variant(df, "CALL", 100.0, 36 / 365, 0.50, ob.abs_delta)
    i = ps.pick_variant(df, "CALL", 100.0, 36 / 365, 0.70, ob.abs_delta)
    assert a and i and i["strike"] < a["strike"] and abs(a["delta"] - 0.5) < 0.15 and a["price"] > 0
    wide = df.assign(bid=df["ask"] * 0.5)
    assert ps.pick_variant(wide, "CALL", 100.0, 36 / 365, 0.50, ob.abs_delta) is None
    thin = df.assign(openInterest=10)
    assert ps.pick_variant(thin, "CALL", 100.0, 36 / 365, 0.50, ob.abs_delta) is None
    assert ps.pick_variant(df.assign(bid=0.0), "CALL", 100.0, 36 / 365, 0.50, ob.abs_delta) is None


def idea(tag="sent", tk="CVX", side="CALL"):
    return {"tag": tag, "ticker": tk, "side": side, "S": 100.0, "edge": 4.0, "strength": 6.0,
            "contracts": [{"strike": 105.0, "price": 0.9, "exp": "2026-10-30", "dte": 10, "delta": 0.2, "iv": 0.3}]}


def fake_fetch(tk, today, S):
    return "2026-11-20", 31, chain(S), chain(S)


def inbox_rows():
    return list(csv.DictReader(open(ps.INBOX, encoding="utf-8")))


def test_log_ideas_writes_bot_atm_and_itm_together_with_one_idea_key():
    n = ps.log_ideas(t(10, 12), [idea()], ob.abs_delta, fake_fetch)
    rows = inbox_rows()
    assert n == 3 and [r["variant"] for r in rows] == ["bot", "atm", "itm"] and len({r["idea"] for r in rows}) == 1
    assert rows[0]["strike"] == "105.0" and rows[0]["exp"] == "2026-10-30" and rows[1]["exp"] == "2026-11-20"


def test_an_idea_without_both_alternatives_is_not_recorded_at_all():
    def thin(tk, today, S):
        return "2026-11-20", 31, chain(S).assign(openInterest=0), chain(S)
    assert ps.log_ideas(t(10, 12), [idea()], ob.abs_delta, thin) == 0
    assert not os.path.exists(ps.INBOX)
    assert ps.log_ideas(t(10, 12), [idea()], ob.abs_delta, lambda *a: None) == 0


def test_same_ticker_and_side_is_logged_once_per_call_and_failures_never_raise():
    assert ps.log_ideas(t(10, 12), [idea(), idea(tag="max_ideas_cap"), idea(side="PUT")], ob.abs_delta, fake_fetch) == 6

    def boom(*a):
        raise ConnectionError("x-api-key: secret")
    assert ps.log_ideas(t(10, 42), [idea(tk="XOM")], ob.abs_delta, boom) == 0
    assert ps.log_ideas(t(10, 42), [{"ticker": "Z"}], ob.abs_delta, fake_fetch) == 0


# ---------------- الصندوق والتتبع ----------------
def test_ingest_takes_complete_fresh_ideas_once_and_ignores_stale_or_incomplete_ones():
    ps.log_ideas(t(10, 12), [idea()], ob.abs_delta, fake_fetch)
    s = ps.load_state()
    assert ps.ingest(s, t(10, 15)) == 3 and {i["variant"] for i in s["open"]} == {"bot", "atm", "itm"}
    assert ps.ingest(s, t(10, 20)) == 0                                         # نفس الصفوف لا تُعاد
    ps.log_ideas(t(10, 42), [idea()], ob.abs_delta, fake_fetch)                  # الفكرة نفسها (سهم، اتجاه، يوم) لاحقاً: تُهمل
    assert ps.ingest(s, t(10, 45)) == 0
    ps.log_ideas(t(10, 12, day=21), [idea(tk="XOM")], ob.abs_delta, fake_fetch)
    assert ps.ingest(ps.load_state(), t(12, 0, day=21)) == 0                      # أقدم من 30 دقيقة


def item(variant="bot", side="CALL", exp="2026-10-30", entry=1.0, spot=100.0, at=t(10, 0)):
    return {"id": variant, "idea": "i", "variant": variant, "ticker": "X", "side": side, "strike": 100.0, "exp": exp, "entry": entry,
            "entry_time": at.isoformat(timespec="minutes"), "spot": spot, "occ": "O", "r1_out": "", "r1_pnl": "", "r1_time": "",
            "r2_out": "", "r2_pnl": "", "r2_time": "", "peak_pnl": 0.0, "close_pnl": "", "quotes": 0}


def test_target_closes_both_rules_at_once():
    it = item()
    assert ps.step(it, 1.6, 100.0, t(10, 5)) is True and it["r1_out"] == it["r2_out"] == "tp" and it["r1_pnl"] == 0.6


def test_bot_stop_waits_for_30_minutes_and_stock_stop_only_applies_to_r2():
    it = item()
    assert ps.step(it, 0.5, 98.0, t(10, 20)) is False and it["r1_out"] == "" and it["r2_out"] == ""   # عمر 20 د
    assert ps.step(it, 0.5, 100.0, t(10, 35)) is False
    assert it["r1_out"] == "stop" and it["r2_out"] == ""                                                # r1 وقف −40%؛ r2 السهم لم يتحرك ضده
    assert ps.step(it, 0.7, 98.4, t(10, 40)) is True and it["r2_out"] == "stock_stop"


def test_stock_stop_follows_the_side_for_puts():
    it = item(side="PUT")
    ps.step(it, 1.0, 98.0, t(10, 40))
    assert it["r2_out"] == ""                                                                           # السهم نزل: في صالح PUT
    ps.step(it, 1.0, 101.6, t(10, 45))
    assert it["r2_out"] == "stock_stop"


def test_time_stop_and_horizon_and_expiry_day():
    near = item(exp="2026-10-23")
    ps.step(near, 1.0, 100.0, t(15, 50, day=20))
    assert near["r2_out"] == "time" and near["r1_out"] == ""                                            # 3 أيام للانتهاء: وقف زمني لـ r2 فقط
    far = item(exp="2026-11-20", at=t(10, 0, day=10))
    assert ps.step(far, 1.1, 100.0, t(15, 50, day=20)) is True and far["r1_out"] == far["r2_out"] == "horizon"
    day = item(exp="2026-10-20")
    assert ps.step(day, 1.0, 100.0, t(15, 45)) is True and day["r1_out"] == "final"


def test_expired_items_close_with_last_value_or_no_data():
    it = item(exp="2026-10-20")
    it["close_pnl"] = -0.2
    assert ps.expire_if_past(it, t(9, 40, day=21)) and it["r1_out"] == "expired" and it["r2_pnl"] == -0.2
    e = item(exp="2026-10-20")
    assert ps.expire_if_past(e, t(9, 40, day=21)) and e["r1_out"] == "no_data"


def test_run_end_to_end_logs_a_finished_idea_and_keeps_unfinished_ones_open():
    ps.log_ideas(t(10, 12), [idea()], ob.abs_delta, fake_fetch)
    book = {}

    def quotes(occs):
        return {o: ({"bid": book.get(o, 1.0), "ask": 1.1}, "alpaca") for o in occs}
    s = ps.run(t(10, 15), quotes, lambda tk: 100.0)
    assert len(s["open"]) == 3
    entries = {i["variant"]: i["entry"] for i in s["open"]}
    book = {i["occ"]: entries[i["variant"]] * 1.6 for i in s["open"] if i["variant"] in ("bot", "atm")}
    s = ps.run(t(10, 20), quotes, lambda tk: 100.0)
    assert [i["variant"] for i in s["open"]] == ["itm"]
    log = list(csv.DictReader(open(ps.LOG, encoding="utf-8")))
    assert {r["variant"] for r in log} == {"bot", "atm"} and all(r["r1_out"] == "tp" for r in log)


# ---------------- التقرير ----------------
def log_rows(n, bot, alt, variant="atm"):
    rows = []
    for i in range(n):
        d = f"2026-11-{1 + i % 20:02d}T10:00-04:00"
        for v, x in (("bot", bot(i)), (variant, alt(i))):
            rows.append({"idea": f"i{i}", "variant": v, "entry_time": d, "r1_out": "final", "r1_pnl": x, "r2_out": "final", "r2_pnl": x})
    return rows


def write_log(rows):
    with open(ps.LOG, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ps.LOG_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({**{c: "" for c in ps.LOG_COLS}, **r})


def test_verdict_needs_enough_ideas_and_an_interval_that_excludes_zero():
    small = ps.paired(log_rows(5, lambda i: -0.4, lambda i: 0.2), "atm")
    assert "لا حكم بعد (5 من 30" in ps.verdict(small)
    rng = random.Random(2)
    good = ps.paired(log_rows(40, lambda i: -0.2 + rng.uniform(-0.05, 0.05), lambda i: 0.2 + rng.uniform(-0.05, 0.05)), "atm")
    assert "تحققت الفرضية" in ps.verdict(good)
    bad = ps.paired(log_rows(40, lambda i: 0.2 + rng.uniform(-0.05, 0.05), lambda i: -0.2 + rng.uniform(-0.05, 0.05)), "atm")
    assert "عكسية" in ps.verdict(bad)
    noise = ps.paired(log_rows(40, lambda i: rng.choice([-0.4, 0.5]), lambda i: rng.choice([-0.4, 0.5])), "atm")
    assert "لم تتحقق بعد" in ps.verdict(noise)


def test_report_shows_each_contract_and_the_paired_difference_and_handles_empty():
    write_log(log_rows(6, lambda i: -0.4, lambda i: 0.2))
    ps.report()
    text = open(ps.REPORT, encoding="utf-8").read()
    assert "على السعر (0.50)" in text and "عقد البوت" in text and "6 فكرة" in text and "الحكم على الفرضية" in text and "لا حكم بعد (6 من 30" in text
    os.remove(ps.LOG)
    ps.report()
    assert "لا نتائج مغلقة بعد" in open(ps.REPORT, encoding="utf-8").read()
