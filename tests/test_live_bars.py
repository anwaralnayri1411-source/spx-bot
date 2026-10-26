"""اختبارات شموع SPX بذيل لحظي: الاستكمال من SPY، النسبة، الاحتياط، الأوضاع، والمقارنة في البوت. بلا شبكة."""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import live_bars as lb  # noqa: E402

NY = ZoneInfo("America/New_York")


def frame(start, n, base, step=1.0, vol=1.0):
    idx = pd.date_range(start, periods=n, freq="5min", tz=NY)
    c = [base + step * i for i in range(n)]
    return pd.DataFrame({"Open": c, "High": [x + 0.5 for x in c], "Low": [x - 0.5 for x in c], "Close": c, "Volume": [vol] * n}, index=idx)


def test_extend_replaces_yahoo_last_bar_and_keeps_only_completed_bars():
    ix = pd.concat([frame("2026-10-26 09:30", 78, 7600.0), frame("2026-10-27 09:30", 6, 7700.0, step=10.0)])   # ياهو حتى 09:55 (قد تكون ناقصة)
    spy = frame("2026-10-27 09:30", 10, 770.0, step=1.0)                                                    # Alpaca حتى 10:15 (جارية)
    n = datetime(2026, 10, 27, 10, 17, tzinfo=NY)
    out, info = lb.extend(ix, spy, n, spx_live=7795.0)
    assert info["status"] == "ok" and info["ratio_src"] == "overlap" and info["ratio"] == pytest.approx(10.0)
    assert info["added"] == 4 and info["yahoo_age_min"] == pytest.approx(22.0) and info["live_age_min"] == pytest.approx(7.0)
    assert len(out) == len(ix) + 3 and out.index[-1] == pd.Timestamp("2026-10-27 10:10", tz=NY)
    assert out["Close"].iloc[-1] == pytest.approx(7780.0) and out.loc[pd.Timestamp("2026-10-27 09:55", tz=NY), "Close"] == pytest.approx(7750.0)
    assert out["Volume"].iloc[-1] == 0


def test_extend_can_keep_the_running_bar_patched_to_live_price():
    ix = pd.concat([frame("2026-10-26 09:30", 78, 7600.0), frame("2026-10-27 09:30", 6, 7700.0, step=10.0)])
    spy = frame("2026-10-27 09:30", 10, 770.0, step=1.0)
    out, info = lb.extend(ix, spy, datetime(2026, 10, 27, 10, 17, tzinfo=NY), spx_live=7795.0, complete_only=False)
    assert out.index[-1] == pd.Timestamp("2026-10-27 10:15", tz=NY) and out["Close"].iloc[-1] == pytest.approx(7795.0) and out["High"].iloc[-1] >= 7795.0


def test_extend_uses_live_ratio_when_yahoo_has_no_bar_today_yet():
    ix = frame("2026-10-26 09:30", 78, 7600.0)
    spy = frame("2026-10-27 09:30", 3, 770.0)
    out, info = lb.extend(ix, spy, datetime(2026, 10, 27, 9, 47, tzinfo=NY), spx_live=7710.0, spy_live=771.0)
    assert info["ratio_src"] == "live" and info["added"] == 3 and out["Close"].iloc[-2] == pytest.approx(7710.0)


@pytest.mark.parametrize("spy,kw,status", [
    (None, {}, "no_spy"),
    (frame("2026-10-26 09:30", 3, 770.0), {}, "spy_not_today"),
    (frame("2026-10-27 09:30", 3, 770.0), {}, "no_ratio"),
    (frame("2026-10-27 09:30", 3, 770.0), {"spx_live": 7700.0, "spy_live": 500.0}, "bad_ratio")])
def test_extend_falls_back_to_yahoo_unchanged(spy, kw, status):
    ix = frame("2026-10-26 09:30", 78, 7600.0)
    out, info = lb.extend(ix, spy, datetime(2026, 10, 27, 9, 47, tzinfo=NY), **kw)
    assert info["status"] == status and out is ix


def test_mode_defaults_to_shadow(monkeypatch):
    monkeypatch.delenv("LIVE_BARS_SPX", raising=False)
    assert lb.mode() == "shadow"
    monkeypatch.setenv("LIVE_BARS_SPX", "1")
    assert lb.mode() == "1"
    monkeypatch.setenv("LIVE_BARS_SPX", "0")
    assert lb.mode() == "0"


def test_bot_decides_on_yahoo_in_shadow_and_on_live_in_mode_1_and_logs_both_sides(monkeypatch, tmp_path):
    import options_bot as ob
    ix = pd.concat([frame("2026-10-26 09:30", 78, 7600.0), frame("2026-10-27 09:30", 6, 7700.0, step=10.0)])
    live = pd.concat([ix, frame("2026-10-27 10:00", 4, 7600.0, step=-10.0)])     # الذيل اللحظي هابط
    n = datetime(2026, 10, 27, 10, 17, tzinfo=NY)
    info = {"status": "ok", "ratio": 10.0, "ratio_src": "overlap", "added": 4, "yahoo_last": ix.index[-1], "yahoo_age_min": 22.0, "live_age_min": 2.0}
    monkeypatch.setattr(lb, "build", lambda ix_, n_: (live, info))
    monkeypatch.setattr(lb, "LOG", str(tmp_path / "log.csv"))
    monkeypatch.setenv("LIVE_BARS_SPX", "shadow")
    used, other, inf, m = ob._live_bars(ix, n)
    assert used is ix and other is live and m == "shadow"
    monkeypatch.setenv("LIVE_BARS_SPX", "1")
    used, other, inf, m = ob._live_bars(ix, n)
    assert used is live and other is ix and m == "1"
    monkeypatch.setenv("LIVE_BARS_SPX", "0")
    assert ob._live_bars(ix, n)[1] is None
    # المقارنة: c من ياهو (وضع الظل) ونسخة لحظية تُحسب من شموعها
    bf_y, bf_l = ob.bar_fields(ix, n), ob.bar_fields(live, n)
    c = {"S": bf_y["S"], "sma20": 7650.0, "sma50": 7600.0, "map": None, "vix": 18.0, "vpct": 50.0, "above_vwap": True, "vwap_spx": 7690.0, "shock": None}
    c.update({k: bf_y[k] for k in ob.BAR_KEYS})
    monkeypatch.setattr(ob, "direction_points", lambda cc: (2.0, 1.0, []) if cc["S"] > 7690 else (1.0, 3.0, []))
    ob._log_live_bars(n, "shadow", info, c, bf_l, 2.0, 1.0)
    row = pd.read_csv(tmp_path / "log.csv").iloc[0]
    assert row["side_yahoo"] == "CALL" and row["side_live"] == "PUT" and row["S_live"] < row["S_yahoo"] and row["added"] == 4
    assert "الاتجاه نفسه في 0%" in lb.report(str(tmp_path / "log.csv"))


def test_analyze_failure_in_live_bars_never_breaks_the_bot(monkeypatch):
    import options_bot as ob

    def boom(ix_, n_):
        raise RuntimeError("x")
    monkeypatch.setattr(lb, "build", boom)
    monkeypatch.setattr(ob.data_gaps, "note", lambda *a, **k: None)
    ix = frame("2026-10-26 09:30", 10, 7600.0)
    assert ob._live_bars(ix, datetime(2026, 10, 27, 10, 0, tzinfo=NY))[0] is ix
