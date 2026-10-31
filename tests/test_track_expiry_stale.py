"""اختبارات: صف «expired» في spx_track.csv وتحذير ثبات سعر العقد. لا شبكة."""
from datetime import datetime

import pandas as pd

import options_bot as ob

N = datetime.fromisoformat("2026-10-29T09:40:00-04:00")


def _track(tmp_path, monkeypatch, rows):
    f = tmp_path / "spx_track.csv"
    pd.DataFrame(rows, columns=["time", "id", "status", "spot", "val", "pnl"]).to_csv(f, index=False)
    monkeypatch.setattr(ob, "TRACK_FILE", str(f))


def test_expired_row_copies_last_values(tmp_path, monkeypatch):
    _track(tmp_path, monkeypatch, [
        ["2026-10-28T15:55-04:00", "SPY:CALL:770:2026-10-28", "open", 771.61, 2.17, -0.152],
        ["2026-10-28T16:00-04:00", "SPY:CALL:770:2026-10-28", "open", 772.72, 2.94, 0.148]])
    rows = ob._expired_rows([{"id": "SPY:CALL:770:2026-10-28"}], N)
    assert len(rows) == 1
    assert rows[0][1:] == ["SPY:CALL:770:2026-10-28", "expired", 772.72, 2.94, 0.148]


def test_expired_row_not_duplicated_and_skips_unknown(tmp_path, monkeypatch):
    _track(tmp_path, monkeypatch, [
        ["2026-10-28T16:00-04:00", "A", "open", 1.0, 2.0, 0.1],
        ["2026-10-29T09:40-04:00", "A", "expired", 1.0, 2.0, 0.1]])
    assert ob._expired_rows([{"id": "A"}, {"id": "NOT_TRACKED"}], N) == []


def test_expired_rows_without_track_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "TRACK_FILE", str(tmp_path / "missing.csv"))
    assert ob._expired_rows([{"id": "A"}], N) == []


def test_stale_quote_warns_after_three_same_reads_with_spot_move(capsys):
    p = {"id": "SPX:PUT:7680:2026-10-16", "sym": "SPX"}
    for spot in (7706.0, 7701.0, 7693.0, 7649.0):
        ob._note_stale_quote(p, 2.46, spot, N)
    assert "ثابت 2.46" in capsys.readouterr().out


def test_stale_quote_silent_when_spot_flat_or_price_moves(capsys):
    p = {"id": "A", "sym": "SPX"}
    for spot in (7700.0, 7700.5, 7700.2, 7700.1):          # سعر ثابت لكن الأصل ثابت
        ob._note_stale_quote(p, 1.0, spot, N)
    q = {"id": "B", "sym": "SPX"}
    for v, spot in ((1.0, 7700.0), (1.1, 7690.0), (1.2, 7680.0), (1.3, 7670.0)):   # السعر يتحرك
        ob._note_stale_quote(q, v, spot, N)
    assert capsys.readouterr().out == ""


def test_stale_counter_resets_on_change():
    p = {"id": "A", "sym": "SPX"}
    ob._note_stale_quote(p, 1.0, 7700.0, N)
    ob._note_stale_quote(p, 1.0, 7700.0, N)
    ob._note_stale_quote(p, 1.5, 7700.0, N)
    assert p["sn"] == 1 and p["sv"] == 1.5
