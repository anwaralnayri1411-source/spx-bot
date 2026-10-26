"""اختبارات عرض ما بعد الإقفال في لوحة الشركات وتقرير SPX وسجل peak_after_at. بلا شبكة ولا متصفح."""
import os
import sys
from datetime import datetime

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cards  # noqa: E402
import options_bot as ob  # noqa: E402
import stocks_bot as sb  # noqa: E402


def pos(kind="tp", peak_after=1.43, last=1.0, entry=2.0):
    return {"id": "X:CALL:2026-11-06:100", "ticker": "X", "side": "CALL", "strike": 100.0, "exp": "2026-11-06", "entry": entry, "time": "2026-10-26T10:00-04:00",
            "status": "tp" if kind == "tp" else "stopped", "closed": {"kind": kind, "time": "2026-10-26T11:00-04:00", "pnl": 0.5 if kind == "tp" else -0.4},
            "peak_after": peak_after, "last_pnl": last, "alerts": {}}


def test_stocks_post_text_for_target_stop_and_open_positions():
    assert "بعد الهدف: بلغ 4.86$ (+143%)" in sb.post_text(pos())
    assert "بعد الوقف: ارتد إلى" in sb.post_text(pos("stop", 0.2, 0.1))
    assert "لم يرتد" in sb.post_text(dict(pos("stop", None, -0.9), peak_after=None))
    assert sb.post_text({"id": "a", "entry": 1.0, "status": "open"}) == ""


def test_closed_line_shows_the_after_close_line_only_for_closed_contracts(monkeypatch):
    line = sb._closed_line(pos())
    assert "↳" in line and "بعد الهدف" in line and "\n" in line
    assert "↳" not in sb._closed_line(dict(pos(), peak_after=None, last_pnl=None))


def test_board_png_items_carry_the_post_text(monkeypatch):
    captured = {}
    monkeypatch.setattr(cards, "board_card", lambda title, sub, items, sl, tp: captured.setdefault("items", items) or b"png")
    today = ob.now_ny()
    p = pos()
    p["closed"]["time"] = today.isoformat(timespec="minutes")
    sb.board_png([(p, {"val": 1.0, "pnl": 0.0, "dte": 5})])
    assert captured["items"][0]["closed"] is True and "بعد الهدف" in captured["items"][0]["post"]


def test_cards_render_the_post_line_under_the_name(monkeypatch):
    seen = {}
    monkeypatch.setattr(cards, "make_card", lambda title, sub, tiles, rows, notes, cols: seen.update(rows=rows) or b"png")
    cards.board_card("t", "s", [{"name": "X CALL 100", "sub": "6 نوفمبر", "entry": 2.0, "now": 3.0, "pnl": 0.5, "status": "tp", "closed": True, "post": "بعد الهدف: بلغ 4.86$"}], 0.4, 0.5)
    assert "بعد الهدف: بلغ 4.86$" in seen["rows"][0]["cells"][0]
    cards.daily_card("2026-10-26", [{"name": "SPX 7695 CALL", "qty": 1, "entry": 2.0, "exit": 3.0, "status": "tp", "peak": 0.6, "pnl": 100.0, "post": "بعد الهدف: بلغ 5$"}], 0.4, 0.5)
    assert "بعد الهدف: بلغ 5$" in seen["rows"][0]["cells"][0]
    cards.daily_card("2026-10-26", [{"name": "SPX 7695 CALL", "qty": 1, "entry": 2.0, "exit": 3.0, "status": "tp", "peak": 0.6, "pnl": 100.0}], 0.4, 0.5)
    assert "dim'>بعد" not in seen["rows"][0]["cells"][0]


def test_spx_daily_report_adds_the_post_close_line_from_the_track(tmp_path, monkeypatch):
    trk = tmp_path / "t.csv"
    pd.DataFrame([
        {"time": "2026-10-26T10:05-04:00", "id": "SPX:CALL:7695:2026-10-26", "status": "open", "spot": 7690, "val": 2.0, "pnl": 0.0},
        {"time": "2026-10-26T10:35-04:00", "id": "SPX:CALL:7695:2026-10-26", "status": "tp", "spot": 7700, "val": 3.1, "pnl": 0.55},
        {"time": "2026-10-26T11:05-04:00", "id": "SPX:CALL:7695:2026-10-26", "status": "tp", "spot": 7710, "val": 5.0, "pnl": 1.5},
        {"time": "2026-10-26T11:35-04:00", "id": "SPX:CALL:7695:2026-10-26", "status": "tp", "spot": 7700, "val": 3.0, "pnl": 0.5}]).to_csv(trk, index=False)
    monkeypatch.setattr(ob, "TRACK_FILE", str(trk))
    monkeypatch.setattr(ob, "load_positions", lambda: {"positions": [{"id": "SPX:CALL:7695:2026-10-26", "time": "2026-10-26T10:00-04:00", "root": "SPXW", "strike": 7695.0,
                                                                      "side": "CALL", "entry": 2.0, "qty": 1, "peak": 1.5, "status": "open", "exp": "2026-10-26"}]})
    items = ob.daily_report_items(datetime(2026, 10, 26, 16, 0, tzinfo=ob.NY))
    assert items[0]["status"] == "tp" and "بعد الهدف: بلغ 5.00$ (+150%)" in items[0]["post"] and "آخر سعر 3.00$" in items[0]["post"]
    monkeypatch.setattr(ob, "TRACK_FILE", str(tmp_path / "none.csv"))
    assert ob.daily_report_items(datetime(2026, 10, 26, 16, 0, tzinfo=ob.NY))[0]["post"] == ""
