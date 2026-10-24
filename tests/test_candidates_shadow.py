"""اختبارات ظل المرشحين: المزايا بلا نظرة للمستقبل، الإشارات، دورة الدخول والخروج، والمهارة. بلا شبكة."""
import csv
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import candidates_shadow as cs  # noqa: E402
import options_bot as ob  # noqa: E402

NY = ob.NY
DAY = datetime(2026, 10, 26, tzinfo=NY).date()


@pytest.fixture(autouse=True)
def _no_gap_files(monkeypatch):
    monkeypatch.setattr(cs.data_gaps, "note", lambda *a, **k: None)


def t(h, m, d=(2026, 10, 26)):
    return datetime(*d, h, m, tzinfo=NY)


def row(h, m, o, c, d=(2026, 10, 26)):
    return [t(h, m, d).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), o, max(o, c), min(o, c), c, 1e5]


def bars(until_min=10 * 60 + 25, o0=700.0, step=0.1):
    """شموع اليوم من 9:30 حتى بداية آخر شمعة until_min، تصاعدية؛ وآخر شمعة أمس 16:00-5 بإغلاق 699."""
    rows = [row(15, 55, 699.0, 699.0, (2026, 10, 23))]
    p, m = o0, 570
    while m <= until_min:
        rows.append(row(m // 60, m % 60, p, p + step))
        p += step
        m += 5
    return rows


def test_features_need_complete_bars_and_ignore_the_future():
    rows = bars()
    f = cs.features(rows, DAY, (10, 30), 699.0)
    assert f["gap"] == pytest.approx(700.0 / 699.0 - 1) and f["r_first"] == pytest.approx((700.6 / 700.0) - 1, abs=1e-4)
    assert f["spot"] == pytest.approx(700.0 + 12 * 0.1)
    assert cs.features(bars(until_min=10 * 60 + 20), DAY, (10, 30), 699.0) is None            # الشمعة الأخيرة ناقصة
    rows2 = rows + [row(10, 30, 900.0, 900.0)]                                              # شمعة الموعد نفسه لا تُستعمل
    assert cs.features(rows2, DAY, (10, 30), 699.0)["spot"] == f["spot"]
    assert cs.features([r for r in rows if not r[0].startswith("2026-10-26T13:30")], DAY, (10, 30), 699.0) is None       # تنقص الشمعة الأولى


def test_prev60_uses_the_bar_twelve_earlier_after_the_first_hour():
    rows = bars(until_min=11 * 60 + 25)
    f = cs.features(rows, DAY, (11, 30), 699.0)
    c = [r[4] for r in rows if r[0].startswith("2026-10-26")]
    assert f["r_prev60"] == pytest.approx(c[23] / c[11] - 1)


def test_signal_mapping_agree_gap_and_zero_cases():
    f = {"gap": 0.005, "r_first": 0.002, "r_prev60": 0.001}
    assert cs.signals(f) == {"mom": 1, "f30": 1, "agree": 1, "gap": 1}
    assert cs.signals({"gap": -0.001, "r_first": 0.002, "r_prev60": -0.001}) == {"mom": -1, "f30": 1, "agree": 0, "gap": 0}
    assert cs.signals({"gap": 0.0, "r_first": 0.0, "r_prev60": 0.0}) == {"mom": 0, "f30": 0, "agree": 0, "gap": 0}


def fake_quotes(ask, bid):
    def fetch(occs):
        return {o: ({"bid": bid, "ask": ask}, "alpaca") for o in occs}
    return fetch


def test_entry_then_exit_after_60_minutes_logs_both_sides(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    get = lambda s, e: bars()
    st = cs.run(t(10, 31), get, fake_quotes(1.00, 0.95))
    assert len(st["items"]) == 1 and st["items"][0]["mom"] == 1 and st["items"][0]["call_entry"] == 1.0
    cs.run(t(10, 35), get, fake_quotes(1.0, 0.95))                                        # لا تكرار لنفس الموعد
    assert len(cs.load_state()["items"]) == 1
    st = cs.run(t(11, 32), lambda s, e: bars(), fake_quotes(1.5, 1.20))
    assert st["items"] == []
    rows = list(csv.DictReader(open(cs.LOG, encoding="utf-8")))
    assert len(rows) == 1 and rows[0]["status"] == "closed" and float(rows[0]["call_ret"]) == pytest.approx(0.2) and float(rows[0]["put_ret"]) == pytest.approx(0.2)


def test_missing_entry_quote_or_bars_creates_nothing_and_retries_until_last_chance(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    st = cs.run(t(10, 31), lambda s, e: bars(), lambda occs: {})
    assert st["items"] == [] and cs.load_state()["done"] == ["2026-10-26|1030"]                 # الدخول بلا تسعيرة: لا عنصر
    monkeypatch.undo()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cs.data_gaps, "note", lambda *a, **k: None)
    os.remove(cs.STATE)
    cs.run(t(10, 31), lambda s, e: bars(until_min=10 * 60 + 15), fake_quotes(1, 0.9))          # بيانات ناقصة: نعيد المحاولة
    assert cs.load_state()["done"] == []
    cs.run(t(10, 36), lambda s, e: bars(until_min=10 * 60 + 15), fake_quotes(1, 0.9))          # آخر فرصة: نتخلى
    assert cs.load_state()["done"] == ["2026-10-26|1030"]


def test_weekend_and_off_slot_do_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cs.run(datetime(2026, 10, 24, 10, 31, tzinfo=NY), lambda s, e: bars(), fake_quotes(1, 1))["items"] == []
    assert cs.run(t(10, 50), lambda s, e: bars(), fake_quotes(1, 1))["items"] == []


def test_skill_is_chosen_side_minus_average_and_verdict_waits_for_sixty_days():
    rows = []
    for i in range(70):
        rows.append({"day": f"2026-11-{1 + i % 28:02d}-{i}", "status": "closed", "mom": "1", "f30": "0", "agree": "0", "gap_sig": "0",
                     "call_ret": "0.10", "put_ret": "-0.20"})
    pairs = cs.skill_rows(rows, "mom")
    assert len(pairs) == 70 and pairs[0][1] == pytest.approx(0.10 - (-0.05))
    assert cs.skill_rows(rows, "f30") == []
    s = cs.stat(pairs)
    assert s["days"] == 70 and "تحققت" in cs.verdict("mom", "H-MOM", s)
    assert "لا حكم بعد" in cs.verdict("mom", "H-MOM", cs.stat(pairs[:20]))
    bad = [dict(r, call_ret="-0.2", put_ret="0.1") for r in rows]
    assert "عكسية" in cs.verdict("mom", "H-MOM", cs.stat(cs.skill_rows(bad, "mom")))


def test_report_writes_a_file_even_without_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cs.report()
    assert "لا نتائج مكتملة" in open(cs.REPORT, encoding="utf-8").read()


def test_log_keeps_both_sides_of_the_book_at_entry_and_exit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    get = lambda s, e: bars()
    cs.run(t(10, 31), get, fake_quotes(1.00, 0.95))
    cs.run(t(11, 32), get, fake_quotes(1.5, 1.20))
    r = list(csv.DictReader(open(cs.LOG, encoding="utf-8")))[0]
    assert (float(r["call_entry"]), float(r["call_bid0"]), float(r["call_exit"]), float(r["call_ask1"])) == (1.0, 0.95, 1.2, 1.5)


def curated_rows(n=70, market_ret=0.10):
    rows = []
    for i in range(n):
        rows.append({"day": f"2026-11-{1 + i % 28:02d}-{i}", "status": "closed", "slot": "10:30" if i % 2 else "11:30", "agree": "1" if i % 3 else "-1",
                     "call_entry": "1.00", "call_bid0": "0.96", "call_exit": "1.10", "call_ask1": "1.14", "call_ret": str(market_ret),
                     "put_entry": "1.00", "put_bid0": "0.96", "put_exit": "1.10", "put_ask1": "1.14", "put_ret": str(market_ret)})
    rows.append({"day": "2026-12-01", "status": "closed", "slot": "13:30", "agree": "1", "call_ret": "9", "put_ret": "9"})          # خارج الموعدين
    rows.append({"day": "2026-12-02", "status": "closed", "slot": "10:30", "agree": "0", "call_ret": "9", "put_ret": "9"})          # بلا اتفاق
    return rows


def test_curated_policy_uses_only_agree_and_the_two_morning_slots_and_gives_mid_upper_bound():
    market, mid = cs.curated_pairs(curated_rows())
    assert len(market) == 70 and len(mid) == 70
    assert market[0][1] == pytest.approx(0.10) and mid[0][1] == pytest.approx((1.12 / 0.98) - 1)
    assert mid[0][1] > market[0][1]                                       # الأمر المحدد حدّ أعلى
    assert "تحققت" in cs.curated_verdict(cs.stat(market))
    assert "لا حكم بعد" in cs.curated_verdict(cs.stat(market[:10]))
    assert "عكسية" in cs.curated_verdict(cs.stat(cs.curated_pairs(curated_rows(market_ret=-0.1))[0]))
    assert cs.curated_pairs([{"status": "open"}]) == ([], [])


def test_report_has_the_curated_section_with_both_execution_models(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rows = curated_rows()
    for r in rows:
        r.setdefault("mom", "0"); r.setdefault("f30", "0"); r.setdefault("gap_sig", "0")
    with open(cs.LOG, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cs.LOG_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cs.LOG_COLS})
    cs.report()
    text = open(cs.REPORT, encoding="utf-8").read()
    assert "H-CURATED" in text and "أمر سوقي" in text and "أمر محدد" in text and "⚖️" in text
