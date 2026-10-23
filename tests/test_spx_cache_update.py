"""اختبارات تحديث مخبأ شموع SPX: الإضافة الآمنة فقط، لا تغيير للقديم، لا نقص، ورفض البيانات المعيبة."""
import os
import sys
from datetime import datetime

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import spx_cache_update as sc  # noqa: E402

NY = sc.NY


def day_bars(day, base=7700.0, start="09:30", n=78):
    idx = pd.date_range(f"{day} {start}", periods=n, freq="5min", tz=NY)
    return pd.DataFrame({"Open": base, "High": base + 2, "Low": base - 2, "Close": base + 1}, index=idx)


def test_normalize_keeps_only_regular_session_and_converts_to_new_york():
    utc = day_bars("2026-10-20").tz_convert("UTC")
    pre = day_bars("2026-10-20", start="08:00", n=3)               # قبل الافتتاح
    post = day_bars("2026-10-20", start="16:00", n=3)              # بعد الإغلاق
    out = sc.normalize(pd.concat([pre, utc.tz_convert(NY), post]).tz_convert("UTC"), now=datetime(2026, 10, 21, 12, 0, tzinfo=NY))
    assert len(out) == 78 and out.index.tz is not None
    assert out.index[0].hour == 9 and out.index[0].minute == 30 and out.index[-1].hour == 15 and out.index[-1].minute == 55


def test_normalize_drops_nan_rows():
    d = day_bars("2026-10-20")
    d.iloc[5, 0] = float("nan")
    assert len(sc.normalize(d, now=datetime(2026, 10, 21, 12, 0, tzinfo=NY))) == 77


def test_today_is_excluded_before_1610_and_included_after():
    d = day_bars("2026-10-21")
    assert sc.normalize(d, now=datetime(2026, 10, 21, 15, 0, tzinfo=NY)).empty
    assert sc.normalize(d, now=datetime(2026, 10, 21, 16, 5, tzinfo=NY)).empty
    assert len(sc.normalize(d, now=datetime(2026, 10, 21, 16, 10, tzinfo=NY))) == 78
    assert len(sc.normalize(d, now=datetime(2026, 10, 22, 8, 0, tzinfo=NY))) == 78


def test_problems_detects_bad_data():
    ok = day_bars("2026-10-20")
    assert sc.problems(ok) == []
    bad = ok.copy(); bad.iloc[3, 1] = 1.0                              # أعلى < أدنى
    assert "أعلى < أدنى" in sc.problems(bad)
    bad = ok.copy(); bad.iloc[3, 3] = 5.0                              # سعر غير منطقي
    assert any("خارج النطاق" in p for p in sc.problems(bad))
    bad = ok.copy(); bad.iloc[3, 0] = bad.iloc[3, 1] + 10              # افتتاح فوق الأعلى
    assert any("افتتاح" in p for p in sc.problems(bad))


def test_merge_fills_the_gap_and_old_rows_win_on_conflict():
    old = pd.concat([day_bars("2026-10-12", 7800.0), day_bars("2026-10-13", 7810.0)])
    new = pd.concat([day_bars("2026-10-13", 9999.0),                    # يوم موجود بقيم مختلفة: يفوز القديم
                     day_bars("2026-10-14", 7820.0), day_bars("2026-10-15", 7830.0)])
    merged, added = sc.merge(old, new)
    assert added == 156 and len(merged) == len(old) + 156
    assert merged.index.is_monotonic_increasing
    assert (merged.loc[old.index] == old).all().all()                   # القديم لم يتغير
    assert merged.loc[day_bars("2026-10-14").index[0], "Open"] == 7820.0


def test_merge_is_idempotent():
    old = day_bars("2026-10-12")
    new = pd.concat([old, day_bars("2026-10-13")])
    once, a1 = sc.merge(old, new)
    twice, a2 = sc.merge(once, new)
    assert a1 == 78 and a2 == 0 and twice.equals(once)


@pytest.fixture()
def tmp_cache(tmp_path, monkeypatch):
    p = tmp_path / "cache.csv"
    monkeypatch.setattr(sc, "CACHE", str(p))
    monkeypatch.setattr(sc, "read_cache", lambda path=str(p): _read(str(p)))
    return p


def _read(path):
    if not os.path.exists(path):
        return pd.DataFrame(columns=sc.COLS)
    d = pd.read_csv(path, index_col=0)
    d.index = pd.to_datetime(d.index, utc=True).tz_convert(NY)
    return d[sc.COLS]


def test_main_writes_new_days_in_the_same_csv_format_and_second_run_is_a_noop(tmp_cache, monkeypatch, capsys):
    day_bars("2026-10-12").to_csv(tmp_cache, index_label="time")
    fetched = pd.concat([day_bars("2026-10-12"), day_bars("2026-10-13"), day_bars("2026-10-14")]).tz_convert("UTC")
    monkeypatch.setattr(sc, "fetch", lambda period: fetched)
    real_norm = sc.normalize
    monkeypatch.setattr(sc, "normalize", lambda df, now=None: real_norm(df, now=datetime(2026, 10, 20, 12, 0, tzinfo=NY)))
    assert sc.main([]) == 0
    text = tmp_cache.read_text(encoding="utf-8").splitlines()
    assert text[0] == "time,Open,High,Low,Close" and text[1].startswith("2026-10-12 09:30:00-04:00,")
    d = _read(str(tmp_cache))
    assert len(d) == 234 and sorted(set(d.index.date)) == [datetime(2026, 10, 12).date(), datetime(2026, 10, 13).date(), datetime(2026, 10, 14).date()]
    out = capsys.readouterr().out
    assert "أُضيفت 156 شمعة في 2 يوم" in out
    before = tmp_cache.read_text(encoding="utf-8")
    assert sc.main([]) == 0
    assert tmp_cache.read_text(encoding="utf-8") == before and "محدّث" in capsys.readouterr().out


def test_main_rejects_bad_yahoo_data_without_writing(tmp_cache, monkeypatch, capsys):
    day_bars("2026-10-12").to_csv(tmp_cache, index_label="time")
    before = tmp_cache.read_text(encoding="utf-8")
    bad = day_bars("2026-10-13"); bad.iloc[4, 1] = 1.0
    monkeypatch.setattr(sc, "fetch", lambda period: bad.tz_convert("UTC"))
    real_norm = sc.normalize
    monkeypatch.setattr(sc, "normalize", lambda df, now=None: real_norm(df, now=datetime(2026, 10, 20, 12, 0, tzinfo=NY)))
    assert sc.main([]) == 1
    assert tmp_cache.read_text(encoding="utf-8") == before and "رُفضت" in capsys.readouterr().out


def test_main_survives_a_yahoo_failure_without_touching_the_cache(tmp_cache, monkeypatch, capsys):
    day_bars("2026-10-12").to_csv(tmp_cache, index_label="time")
    before = tmp_cache.read_text(encoding="utf-8")

    def boom(period):
        raise ConnectionError("x-api-key: secret")

    monkeypatch.setattr(sc, "fetch", boom)
    assert sc.main([]) == 0
    out = capsys.readouterr().out
    assert tmp_cache.read_text(encoding="utf-8") == before and "ConnectionError" in out and "secret" not in out


def test_dry_run_never_writes(tmp_cache, monkeypatch, capsys):
    day_bars("2026-10-12").to_csv(tmp_cache, index_label="time")
    before = tmp_cache.read_text(encoding="utf-8")
    monkeypatch.setattr(sc, "fetch", lambda period: day_bars("2026-10-13").tz_convert("UTC"))
    real_norm = sc.normalize
    monkeypatch.setattr(sc, "normalize", lambda df, now=None: real_norm(df, now=datetime(2026, 10, 20, 12, 0, tzinfo=NY)))
    assert sc.main(["--dry-run"]) == 0
    assert tmp_cache.read_text(encoding="utf-8") == before and "لم يُكتب" in capsys.readouterr().out
