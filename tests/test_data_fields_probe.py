"""اختبارات أداة فحص الحقول: التسطيح، العرض الآمن، وعدم رفع استثناء. بلا شبكة."""
import os
import sys
import urllib.error
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import data_fields_probe as dp  # noqa: E402


def test_flatten_nests_and_summarizes_lists():
    d = {"latestQuote": {"bp": 1.0, "ap": 1.1, "t": "x"}, "greeks": {"delta": 0.5}, "trades": [1, 2, 3], "iv": 0.2}
    f = dp.flatten(d)
    assert f["latestQuote.bp"] == 1.0 and f["greeks.delta"] == 0.5 and f["trades"] == "list[3]" and f["iv"] == 0.2


def test_show_truncates_long_strings_and_formats_numbers():
    assert dp.show(1.2300) == "1.23" and dp.show(None) == "None" and dp.show(True) == "True"
    assert len(dp.show("x" * 100)) == 40


def test_table_handles_empty_and_lists_fields_sorted():
    assert "لا بيانات" in "\n".join(dp.table("t", None))
    text = "\n".join(dp.table("t", {"b": 2, "a": {"c": 1}}))
    assert text.index("`a.c`") < text.index("`b`")


def test_attempt_reports_http_code_without_raising():
    def boom():
        raise urllib.error.HTTPError("u", 403, "forbidden", {}, None)
    assert dp.attempt("x", boom) == (None, "HTTP 403")
    assert dp.attempt("x", lambda: {"a": 1}) == ({"a": 1}, "ok")
    assert dp.attempt("x", lambda: 1 / 0)[1] == "ZeroDivisionError"


def test_target_contract_rolls_weekend_to_monday():
    occ, d = dp.target_contract(700.4, date(2026, 10, 24))                 # سبت
    assert d == date(2026, 10, 26) and occ == "SPY261026C00700000"


def test_main_without_keys_is_a_no_op(monkeypatch):
    monkeypatch.delenv("ALPACA_KEY", raising=False)
    assert dp.main([]) == 0


def test_last_session_day_skips_weekends():
    assert dp.last_session_day(date(2026, 10, 26)) == date(2026, 10, 23)      # اثنين ← جمعة
    assert dp.last_session_day(date(2026, 10, 24)) == date(2026, 10, 23)      # سبت ← جمعة
    assert dp.last_session_day(date(2026, 10, 28)) == date(2026, 10, 27)


def test_tape_stats_summarizes_size_exchanges_and_conditions():
    t = [{"p": 1.0, "s": 5, "x": "C", "c": "g"}, {"p": 1.1, "s": 100, "x": "X", "c": "S"}, {"p": 1.1, "s": 1, "x": "C", "c": "g"}]
    s = dp.tape_stats(t)
    assert s["n"] == 3 and s["volume"] == 106 and s["max_size"] == 100 and s["exchanges"] == 2 and s["conditions"] == ["S", "g"]
    assert dp.tape_stats([]) == {"n": 0}
