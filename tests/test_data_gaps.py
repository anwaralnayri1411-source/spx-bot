"""اختبارات تسجيل فجوات البيانات: يكتب بأمان، لا يسرّب نص الخطأ، لا يكسر البوت، ولا يغيّر سلوكه."""
import csv
import inspect
from datetime import date

import pytest

import data_gaps
import options_bot as ob
import stocks_bot as sb


@pytest.fixture(autouse=True)
def gaps_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(data_gaps, "_ROOT", str(tmp_path))
    data_gaps._seen.clear()
    return tmp_path


def rows(project, tmp_path):
    p = tmp_path / f"data_gaps_{project}_log.csv"
    return list(csv.DictReader(open(p, encoding="utf-8"))) if p.exists() else []


# ---------- الدالة نفسها ----------
def test_note_writes_header_and_row(gaps_dir):
    data_gaps.note("spx", "vwap", ValueError("x"), "SPY")
    r = rows("spx", gaps_dir)
    assert len(r) == 1 and r[0]["item"] == "vwap" and r[0]["error"] == "ValueError" and r[0]["detail"] == "SPY"
    assert set(r[0]) == {"time_ny", "script", "item", "error", "detail"}


def test_note_never_records_the_error_message(gaps_dir):
    data_gaps.note("stocks", "spot_5m", RuntimeError("x-api-key: sk-ant-SECRET"), "AAPL")
    text = (gaps_dir / "data_gaps_stocks_log.csv").read_text(encoding="utf-8")
    assert "SECRET" not in text and "sk-ant" not in text and "RuntimeError" in text


def test_note_caps_rows_per_item_per_run(gaps_dir):
    for _ in range(40):
        data_gaps.note("spx", "es", OSError("x"))
    data_gaps.note("spx", "vix_day", OSError("x"))
    r = rows("spx", gaps_dir)
    assert sum(1 for x in r if x["item"] == "es") == data_gaps._PER_ITEM_CAP
    assert sum(1 for x in r if x["item"] == "vix_day") == 1               # الحدّ لكل مدخل على حدة


def test_projects_use_separate_files(gaps_dir):
    data_gaps.note("spx", "es")
    data_gaps.note("stocks", "spot_5m")
    assert len(rows("spx", gaps_dir)) == 1 and len(rows("stocks", gaps_dir)) == 1


def test_note_never_raises_even_if_the_folder_is_unwritable(monkeypatch):
    monkeypatch.setattr(data_gaps, "_ROOT", "/nonexistent/folder/for/test")
    assert data_gaps.note("spx", "vwap", ValueError("x")) is None         # لا استثناء ولا قيمة


# ---------- السلوك لم يتغيّر + الفجوة تُسجَّل ----------
class BrokenTicker:
    ticker = "AAPL"
    options = ["2026-10-26"]

    def history(self, *a, **k):
        raise ConnectionError("yahoo down")

    def option_chain(self, e):
        raise ConnectionError("yahoo down")


def test_stocks_get_chains_returns_empty_and_logs(gaps_dir):
    assert sb.get_chains(BrokenTicker(), date(2026, 10, 19)) == []         # كما كان: لا سلسلة، بلا استثناء
    assert any(x["item"] == "option_chain" for x in rows("stocks", gaps_dir))


def test_stocks_spot_now_returns_none_and_logs_both_fallbacks(gaps_dir):
    assert sb.spot_now(BrokenTicker()) is None
    items = [x["item"] for x in rows("stocks", gaps_dir)]
    assert "spot_5m" in items and "spot_5d" in items


def test_stocks_intraday_price_still_returns_the_fallback(gaps_dir):
    assert sb.intraday_price(BrokenTicker(), 123.45) == 123.45
    assert any(x["item"] == "price_5m" for x in rows("stocks", gaps_dir))


def test_spx_liquidity_map_survives_broken_chains_and_logs(gaps_dir):
    out = ob.liquidity_map(BrokenTicker(), 7700.0, [("2026-10-19", 0), ("2026-10-20", 1)])
    assert not out or isinstance(out, dict)                                # لا استثناء
    assert sum(1 for x in rows("spx", gaps_dir) if x["item"] == "option_chain") == 2


def test_analyze_logs_vwap_es_vix_and_keeps_the_existing_default():
    """analyze يحتاج شبكة فلا نشغّله؛ نثبّت بنيوياً أن المواضع الثلاثة تسجّل، وأن الافتراضي القائم لم يُمَس."""
    src = inspect.getsource(ob.analyze)
    for item in ('"vwap"', '"vwap_no_volume"', '"es"', '"vix_day"'):
        assert f'data_gaps.note("spx", {item}' in src, item
    assert '"above_vwap": True' in src                                     # السلوك الحالي موثّق: لا تغيير في هذه المرحلة
