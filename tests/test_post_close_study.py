"""اختبارات دراسة ما بعد الإقفال: استخراج الصفوف من الشركات وSPX، الملخص، والتقرير. بلا شبكة."""
import os
import sys
from datetime import date

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import post_close_study as pcs  # noqa: E402


def stock_pos(pid, kind, peak_after=None, last=-0.9, exp="2026-10-23", peak_at=None, closed_time="2026-10-20T11:00-04:00"):
    return {"id": pid, "status": "tp" if kind == "tp" else "stopped", "closed": {"kind": kind, "time": closed_time, "pnl": 0.5 if kind == "tp" else -0.4},
            "peak_after": peak_after, "last_pnl": last, "exp": exp, "peak_after_at": peak_at}


def test_stock_rows_classify_kind_expiry_and_days_to_peak():
    rows = pcs.stock_rows([
        stock_pos("a", "stop", 0.6, 0.5, peak_at="2026-10-22T10:00-04:00"),
        stock_pos("b", "tp", 1.4, 1.0),
        stock_pos("c", "stop", None, -0.99, exp="2026-11-30"),
        {"id": "d", "status": "open"}], today=date(2026, 10, 26))
    assert [r["kind"] for r in rows] == ["stop", "tp", "stop"]
    assert rows[0]["days_to_peak"] == 2 and rows[0]["expired"] is True and rows[2]["expired"] is False


def test_spx_rows_take_the_path_after_the_first_closed_status():
    t = pd.DataFrame([
        {"time": "2026-10-26T10:05", "id": "SPX:CALL:7690:2026-10-26", "status": "open", "pnl": 0.0},
        {"time": "2026-10-26T10:35", "id": "SPX:CALL:7690:2026-10-26", "status": "stopped", "pnl": -0.45},
        {"time": "2026-10-26T11:05", "id": "SPX:CALL:7690:2026-10-26", "status": "stopped", "pnl": 0.2},
        {"time": "2026-10-26T11:35", "id": "SPX:CALL:7690:2026-10-26", "status": "stopped", "pnl": -0.8},
        {"time": "2026-10-26T10:05", "id": "SPX:PUT:7680:2026-10-26", "status": "open", "pnl": 0.1}])
    rows = pcs.spx_rows(t, today=date(2026, 10, 27))
    assert len(rows) == 1 and rows[0]["kind"] == "stop" and rows[0]["peak"] == pytest.approx(0.2) and rows[0]["last"] == pytest.approx(-0.8) and rows[0]["expired"] is True


def test_report_has_both_sources_shares_and_examples(tmp_path, monkeypatch):
    stock = pcs.stock_rows([stock_pos("a", "stop", 0.6, 0.5, peak_at="2026-10-22T10:00-04:00"), stock_pos("b", "stop", None, -0.9), stock_pos("c", "tp", 1.4, 1.0)], today=date(2026, 10, 26))
    text = "\n".join(pcs.report(stock, [], "تجربة"))
    assert "## الشركات" in text and "## SPX/SPY" in text and "50%" in text and "أمثلة ارتد" in text and "بعد 2 يوم" in text
    monkeypatch.setattr(pcs, "OUT", str(tmp_path / "r.md"))
    monkeypatch.setattr(pcs, "ROOT", str(tmp_path / "none"))
    assert pcs.main([]) == 0
    assert "وصفي" in (tmp_path / "r.md").read_text(encoding="utf-8")
