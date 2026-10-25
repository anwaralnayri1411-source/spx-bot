"""اختبارات تنزيل شموع عدة رموز: الإكمال من آخر شمعة، عزل فشل رمز، وأسماء الملفات. بلا شبكة."""
import gzip
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import bars_history as bh  # noqa: E402
import spy_history as sh  # noqa: E402


def fake_get_factory(calls):
    def get(params):
        calls.append(params["start"])
        t0 = datetime.fromisoformat(params["start"].replace("Z", "+00:00"))
        bars = [{"t": (t0.replace(microsecond=0)).strftime("%Y-%m-%dT%H:%M:%SZ"), "o": 10.0, "h": 10.5, "l": 9.5, "c": 10.2, "v": 100}]
        return {"bars": bars, "next_page_token": None}
    return get


def test_download_is_chunked_resumable_and_does_not_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(bh, "PAUSE", 0)
    calls = []
    path = str(tmp_path / "AAA.csv.gz")
    end = datetime(2019, 6, 1, tzinfo=timezone.utc)
    n1 = bh.download_symbol("AAA", "iex", "2019-01-01", end, get=fake_get_factory(calls), path=path)
    assert n1 >= 2 and len(calls) >= 2
    rows = gzip.open(path, "rt").read().strip().split("\n")
    assert rows[0].startswith("t,") and len(rows) == n1 + 1
    n2 = bh.download_symbol("AAA", "iex", "2019-01-01", end, get=fake_get_factory([]), path=path)
    assert n2 <= 1                                                                    # يكمل من آخر شمعة: لا إعادة للمقاطع القديمة


def test_paths_and_symbols_include_etfs_then_watchlist(tmp_path):
    assert bh.path_for("QQQ", str(tmp_path)).endswith("QQQ.csv.gz")
    syms = bh.symbols()
    assert syms[:3] == ["QQQ", "IWM", "DIA"] and "NVDA" in syms and len(syms) == len(set(syms))


def test_main_isolates_a_failing_symbol_and_reports_it(monkeypatch, capsys):
    monkeypatch.setenv("ALPACA_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET", "s")
    monkeypatch.setattr(sh, "choose_feed", lambda *a, **k: "iex")

    def fake_download(sym, feed, start, end, get=None, path=None):
        if sym == "BAD":
            raise RuntimeError("x")
        return 5
    monkeypatch.setattr(bh, "download_symbol", fake_download)
    rc = bh.main(["--only", "AAA,BAD,CCC"])
    out = capsys.readouterr().out
    assert rc == 1 and "AAA: +5" in out and "BAD: فشل" in out and "CCC: +5" in out and "فشل 1: BAD" in out


def test_main_without_keys_is_a_no_op(monkeypatch):
    monkeypatch.delenv("ALPACA_KEY", raising=False)
    assert bh.main([]) == 0
