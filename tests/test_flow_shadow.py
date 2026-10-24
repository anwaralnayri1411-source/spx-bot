"""اختبارات ظل التدفق: اختيار العقود، التجميع، النافذة والتكرار، الشذوذ، والحكم. بيانات مصطنعة، بلا شبكة."""
import csv
import os
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import flow_shadow as fs  # noqa: E402
import options_bot as ob  # noqa: E402

NY = ob.NY


@pytest.fixture(autouse=True)
def _no_gap_files(monkeypatch):
    monkeypatch.setattr(fs.data_gaps, "note", lambda *a, **k: None)


def t(h, m, d=(2026, 10, 26)):
    return datetime(*d, h, m, tzinfo=NY)


def test_choose_expiries_takes_the_two_nearest_inside_the_window():
    today = date(2026, 10, 26)
    exps = ["2026-10-27", "2026-10-30", "2026-10-31", "2026-11-06", "2026-11-20", "2026-12-18"]
    assert fs.choose_expiries(exps, today) == ["2026-10-31", "2026-11-06"]
    assert fs.choose_expiries(["2026-10-27"], today) == []


def test_band_contracts_and_side_parsing():
    rows = [{"contractSymbol": "AAPL261030C00250000", "strike": 250.0}, {"contractSymbol": "AAPL261030P00300000", "strike": 300.0},
            {"contractSymbol": "AAPL261030P00235000", "strike": 235.0}]
    assert fs.band_contracts(rows, 250.0) == ["AAPL261030C00250000", "AAPL261030P00235000"]
    assert fs.occ_side("AAPL261030C00250000") == "C" and fs.occ_side("AAPL261030P00250000") == "P"


def test_aggregate_splits_sides_and_counts_fresh_positioning_only_when_volume_exceeds_oi():
    items = {"AAPL261030C00250000": {"volume": 500, "oi": 100}, "AAPL261030C00255000": {"volume": 50, "oi": 400},
             "AAPL261030P00240000": {"volume": 200, "oi": 200}, "AAPL261030P00235000": {"volume": 300, "oi": 10}}
    a = fs.aggregate(items)
    assert a["call_vol"] == 550 and a["put_vol"] == 500 and a["call_oi"] == 500 and a["put_oi"] == 210
    assert a["call_fresh"] == 500 and a["put_fresh"] == 300


def test_direction_thresholds():
    assert fs.direction(70, 30) == 1 and fs.direction(30, 70) == -1 and fs.direction(50, 50) == 0 and fs.direction(0, 0) == 0
    assert fs.direction(65, 35) == 1 and fs.direction(35, 65) == -1


def fake_chain(tk, today):
    return 100.0, [{"contractSymbol": f"{tk}261030C00100000", "strike": 100.0}, {"contractSymbol": f"{tk}261030P00100000", "strike": 100.0}], 2


def fake_items(occs):
    return {o: {"volume": 100.0 if o[-9] == "C" else 50.0, "oi": 10.0} for o in occs}


def test_run_only_inside_the_window_and_logs_one_row_per_ticker_without_duplicates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert fs.run(t(12, 0), ["AAA"], fake_chain, fake_items) == []                   # خارج النافذة
    assert fs.run(datetime(2026, 10, 24, 15, 45, tzinfo=NY), ["AAA"], fake_chain, fake_items) == []   # سبت
    rows = fs.run(t(15, 45), ["AAA", "BBB"], fake_chain, fake_items)
    assert [r["ticker"] for r in rows] == ["AAA", "BBB"] and rows[0]["call_vol"] == 100 and rows[0]["put_vol"] == 50 and rows[0]["call_fresh"] == 100
    assert fs.run(t(15, 50), ["AAA", "BBB"], fake_chain, fake_items) == []           # لا تكرار
    assert len(list(csv.DictReader(open(fs.LOG, encoding="utf-8")))) == 2


def test_one_failing_ticker_or_empty_items_does_not_stop_the_rest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def chain(tk, today):
        if tk == "BAD":
            raise RuntimeError("x")
        return fake_chain(tk, today)

    def items(occs):
        return {} if occs and occs[0].startswith("EMPTY") else fake_items(occs)
    rows = fs.run(t(15, 45), ["BAD", "EMPTY", "OK"], chain, items)
    assert [r["ticker"] for r in rows] == ["OK"]


def log_rows(n_days=30, spike_day=25, ticker="AAA", spike_calls=True):
    rows = []
    days = pd.bdate_range("2026-09-01", periods=n_days)
    rng = np.random.default_rng(1)
    for i, d in enumerate(days):
        base = 1000 * np.exp(rng.normal(0, 0.1))
        cv, pv = base * 0.5, base * 0.5
        if i == spike_day:
            cv, pv = (6000, 1000) if spike_calls else (1000, 6000)
        rows.append({"date": d.date().isoformat(), "ticker": ticker, "call_vol": str(cv), "put_vol": str(pv)})
    return rows


def test_events_need_twenty_prior_snapshots_and_flag_the_spike_with_direction():
    ev = fs.events_from_log(log_rows())
    assert min(e["date"] for e in ev) >= "2026-09-29"                      # 20 لقطة سابقة قبل أول حدث
    spike = next(e for e in ev if e["date"] == pd.bdate_range("2026-09-01", periods=30)[25].date().isoformat())
    assert spike["z"] > 5 and spike["dir"] == 1
    assert next(e for e in fs.events_from_log(log_rows(spike_calls=False)) if e["z"] > 5)["dir"] == -1
    assert fs.events_from_log(log_rows(n_days=10)) == []


def closes_for(effect, n=400, seed=2, tickers=("AAA",)):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2026-01-01", periods=n)
    out = {"SPY": pd.Series(500 * np.cumprod(1 + rng.normal(0.0004, 0.006, n)), index=idx)}
    for tk in tickers:
        out[tk] = pd.Series(100 * np.cumprod(1 + rng.normal(0.0004, 0.012, n)), index=idx)
    return out, idx


def make_events(effect, n_events=300, seed=3):
    closes, idx = closes_for(effect, tickers=("AAA",))
    rng = np.random.default_rng(seed)
    ev = []
    s = closes["AAA"].copy()
    for k in range(n_events):
        i = int(rng.integers(30, len(idx) - 10))
        direction = int(rng.choice([-1, 1]))
        ev.append({"date": idx[i].date().isoformat(), "ticker": "AAA", "z": 3.0, "dir": direction, "share": 0.8 if direction > 0 else 0.2})
    return ev, closes, idx


def test_attach_returns_and_verdict_wait_confirm_reject_and_reverse():
    ev, closes, idx = make_events(0.0)
    sc = fs.attach_returns(ev, closes)
    assert len(sc) == len(ev)
    first = sc[0]
    i = list(idx.date).index(date.fromisoformat(first["date"]))
    expect = (closes["AAA"].iloc[i + 5] / closes["AAA"].iloc[i] - 1) - (closes["SPY"].iloc[i + 5] / closes["SPY"].iloc[i] - 1)
    assert first["ar"] == pytest.approx(expect)
    assert "لا حكم بعد" in fs.verdict(fs.stat(sc[:20]))
    assert "تحققت" not in fs.verdict(fs.stat(sc))
    planted = [dict(e, ar=e["dir"] * 0.02 + np.random.default_rng(i).normal(0, 0.03)) for i, e in enumerate(sc)]
    assert "تحققت" in fs.verdict(fs.stat(planted))
    reverse = [dict(e, ar=-e["dir"] * 0.02 + np.random.default_rng(i).normal(0, 0.03)) for i, e in enumerate(sc)]
    assert "عكسية" in fs.verdict(fs.stat(reverse))


def test_report_end_to_end_with_injected_closes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fs.report(closes={})
    assert "لا لقطات بعد" in open(fs.REPORT, encoding="utf-8").read()
    rows = log_rows()
    with open(fs.LOG, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fs.LOG_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fs.LOG_COLS})
    closes, _ = closes_for(0.0, tickers=("AAA",))
    closes = {k: v.set_axis(pd.bdate_range("2026-08-01", periods=len(v))) for k, v in closes.items()}
    fs.report(closes=closes)
    text = open(fs.REPORT, encoding="utf-8").read()
    assert "H-FLOW" in text and "⚖️" in text and "أحداث شاذة" in text
