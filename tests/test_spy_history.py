"""اختبارات منزّل شموع SPY: تنظيف الشموع، الإلحاق بلا تكرار، الاستئناف، اختيار الخلاصة، وفحص السلامة. بلا شبكة."""
import os
import sys
import urllib.error
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import spy_history as sh  # noqa: E402


def bar(t, o=400.0, h=401.0, l=399.0, c=400.5, v=1000):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def test_parse_bars_drops_bad_prices_dedupes_and_sorts():
    rows = sh.parse_bars([bar("2026-10-20T14:35:00Z"), bar("2026-10-20T14:30:00Z"), bar("2026-10-20T14:30:00Z", c=400.7),
                          bar("2026-10-20T14:40:00Z", h=398.0),                    # أعلى < أدنى
                          bar("2026-10-20T14:45:00Z", o=0.0),                      # سعر صفر
                          {"t": "x"}, None])
    assert [r[0] for r in rows] == ["2026-10-20T14:30:00Z", "2026-10-20T14:35:00Z"] and rows[0][4] == 400.7
    assert sh.parse_bars(None) == []


def test_append_is_incremental_and_never_rewrites_old_rows(tmp_path):
    p = str(tmp_path / "d" / "spy.csv.gz")
    a = sh.parse_bars([bar("2026-10-20T14:30:00Z"), bar("2026-10-20T14:35:00Z")])
    assert sh.append_rows(a, p) == 2 and sh.read_last(p) == "2026-10-20T14:35:00Z"
    b = sh.parse_bars([bar("2026-10-20T14:30:00Z", c=999.0), bar("2026-10-20T14:35:00Z"), bar("2026-10-20T14:40:00Z")])
    assert sh.append_rows(b, p) == 1                                              # القديم لا يُعدَّل ولا يتكرر
    assert sh.append_rows(b, p) == 0
    assert sh.read_last(str(tmp_path / "missing.gz")) is None


def test_fetch_range_follows_pages_and_stops(monkeypatch):
    monkeypatch.setattr(sh.time, "sleep", lambda s: None)
    pages = [{"bars": [bar("2026-10-20T14:30:00Z")], "next_page_token": "p2"}, {"bars": [bar("2026-10-20T14:35:00Z")]}]
    calls = []

    def get(q):
        calls.append(q.get("page_token"))
        return pages[len(calls) - 1]
    rows = sh.fetch_range("a", "b", "sip", get)
    assert len(rows) == 2 and calls == [None, "p2"]


def test_choose_feed_prefers_sip_and_falls_back_to_iex_on_403():
    assert sh.choose_feed(lambda q: {"bars": []}, datetime(2026, 10, 24, tzinfo=timezone.utc)) == "sip"

    def deny(q):
        raise urllib.error.HTTPError("u", 403, "forbidden", None, None)
    assert sh.choose_feed(deny, datetime(2026, 10, 24, tzinfo=timezone.utc)) == "iex"


def test_check_summarises_the_file_and_flags_short_days(tmp_path):
    p = str(tmp_path / "spy.csv.gz")
    bars = []
    for d in ("2026-10-20", "2026-10-21"):
        n = 78 if d == "2026-10-20" else 10
        for i in range(n):
            minutes = 13 * 60 + 30 + 5 * i                  # 09:30 نيويورك = 13:30 UTC صيفاً
            bars.append(bar(f"{d}T{minutes // 60:02d}:{minutes % 60:02d}:00Z", c=400.0 + i * 0.01))
    sh.append_rows(sh.parse_bars(bars), p)
    s = sh.check(p)
    assert s["days"] == 2 and s["short_days"] == 1 and s["unsorted"] == 0 and s["rows"] == 88
    assert sh.check(str(tmp_path / "none.gz")) is None


def test_main_without_keys_exits_cleanly(monkeypatch, capsys):
    monkeypatch.delenv("ALPACA_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET", raising=False)
    assert sh.main([]) == 0 and "يحتاج ALPACA_KEY" in capsys.readouterr().out


def test_main_logs_only_the_http_code_on_failure(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("ALPACA_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET", "x-api-key: secret")
    monkeypatch.setattr(sh, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sh, "FILE", str(tmp_path / "spy.csv.gz"))
    monkeypatch.setattr(sh, "META", str(tmp_path / "meta.json"))
    monkeypatch.setattr(sh, "choose_feed", lambda *a, **k: "sip")

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 429, "slow down x-api-key: secret", None, None)
    monkeypatch.setattr(sh, "fetch_range", boom)
    assert sh.main(["--from", "2026-10-01"]) == 1
    out = capsys.readouterr().out
    assert "429" in out and "secret" not in out
