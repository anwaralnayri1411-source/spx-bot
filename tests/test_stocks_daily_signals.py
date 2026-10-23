"""اختبارات إشارات الفجوة والنفط والتدفق للشركات. بيانات مصطنعة."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import stocks_daily_signals as sd  # noqa: E402


def market(n=1800, cont=0.0, seed=1, tickers=("A", "B", "C", "D")):
    """أسهم عشوائية؛ بعد فجوة ≥2% يتبع الافتتاح→الإغلاق اتجاه الفجوة بمقدار cont (استمرار) أو عكسه (سالب)."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-02", periods=n)
    spy_o = pd.Series(300.0, index=idx)
    spy_c = pd.Series(300.0, index=idx)
    out = {}
    for t in tickers:
        o, c, prev = [], [], 100.0
        for i in range(n):
            gap = rng.choice([0.0, 0.03, -0.03], p=[0.9, 0.05, 0.05]) + rng.normal(0, 0.003)
            op = prev * (1 + gap)
            oc = rng.normal(0, 0.006) + (cont * np.sign(gap) if abs(gap) >= 0.02 else 0)
            cl = op * (1 + oc)
            o.append(op)
            c.append(cl)
            prev = cl
        out[t] = (pd.Series(o, index=idx), pd.Series(c, index=idx))
    return out, spy_o, spy_c


def events(**kw):
    m, so, sc = market(**kw)
    ev = []
    for t, (o, c) in m.items():
        ev += sd.gap_events(t, o, c, so, sc)
    return pd.DataFrame(ev)


def test_gap_events_use_previous_close_and_subtract_spy():
    idx = pd.bdate_range("2024-01-02", periods=4)
    o = pd.Series([100, 103.0, 103, 103], index=idx)
    c = pd.Series([100, 105.0, 103, 103], index=idx)
    so = pd.Series([400.0] * 4, index=idx)
    sc = pd.Series([400, 404.0, 400, 400], index=idx)                       # SPY +1% في اليوم الثاني
    ev = sd.gap_events("X", o, c, so, sc)
    assert len(ev) == 1 and ev[0]["date"] == idx[1]
    assert abs(ev[0]["gap"] - 0.03) < 1e-12 and abs(ev[0]["ar"] - ((105 / 103 - 1) - 0.01)) < 1e-12


def test_gap_verdict_two_sided_and_waits_for_events():
    E = events(cont=0.004)
    s = sd.stat(E["gap"], E["ar"], E["cluster"])
    assert "الفجوة تستمر" in sd.judge("H", s, 200, True)
    E = events(cont=-0.004, seed=2)
    assert "الفجوة تنعكس" in sd.judge("H", sd.stat(E["gap"], E["ar"], E["cluster"]), 200, True)
    E = events(cont=0.0, seed=3)
    assert "لم تتحقق" in sd.judge("H", sd.stat(E["gap"], E["ar"], E["cluster"]), 200, True)
    assert "لا حكم بعد" in sd.judge("H", sd.stat(E["gap"][:50], E["ar"][:50], E["cluster"][:50]), 200, True)


def oil_world(n=2000, lead=0.0, seed=4):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-02", periods=n)
    r_oil = rng.normal(0, 0.02, n)
    oil = pd.Series(60 * np.cumprod(1 + r_oil), index=idx)
    spy = pd.Series(300 * np.cumprod(1 + rng.normal(0.0003, 0.008, n)), index=idx)
    o5 = oil.pct_change(5)
    st = {}
    for t in ("XOM", "CVX"):
        r = rng.normal(0, 0.01, n)
        for i in range(6, n - 6):
            if abs(o5.iloc[i]) >= 0.04:
                r[i + 1:i + 6] += lead * np.sign(o5.iloc[i]) / 5
        st[t] = pd.Series(100 * np.cumprod(1 + r), index=idx)
    return oil, st, spy


def test_oil_events_are_independent_and_sign_matches_forward_window():
    oil, st, spy = oil_world(600)
    ev = sd.oil_events(oil, st, spy)
    dates = sorted({e["date"] for e in ev})
    pos = [oil.index.get_loc(d) for d in dates]
    assert all(b - a >= 5 for a, b in zip(pos, pos[1:])) and len(ev) == 2 * len(dates)
    e = ev[0]
    f = st[e["ticker"]].shift(-5).loc[e["date"]] / st[e["ticker"]].loc[e["date"]] - 1
    s = spy.shift(-5).loc[e["date"]] / spy.loc[e["date"]] - 1
    assert abs(e["ar"] - (f - s)) < 1e-12


def test_oil_verdict_confirms_lead_and_rejects_noise():
    oil, st, spy = oil_world(lead=0.03)
    E = pd.DataFrame(sd.oil_events(oil, st, spy))
    assert "تحققت" in sd.judge("H", sd.stat(E["oil5"], E["ar"], E["cluster"]), 30, False)
    oil, st, spy = oil_world(lead=0.0, seed=5)
    E = pd.DataFrame(sd.oil_events(oil, st, spy))
    assert "تحققت" not in sd.judge("H", sd.stat(E["oil5"], E["ar"], E["cluster"]), 30, False)


def test_oil_ignores_negative_price_glitches():
    oil, st, spy = oil_world(300)
    oil.iloc[100] = -37.0
    sd.oil_events(oil, st, spy)                                           # لا استثناء


def test_flow_section_needs_enough_rows_and_reports_correlation():
    close = {"SPY": pd.Series(100.0 + np.arange(60), index=pd.bdate_range("2026-08-03", periods=60))}
    assert "لا سجل" in sd.flow_section(None, close)[0]
    days = close["SPY"].index[:10]
    rows = [{"time": f"{d.date()}T10:45-04:00", "ticker": "SPY", "ratio": 1.2} for d in days]
    assert "ملاحظة فقط" in sd.flow_section(pd.DataFrame(rows), close)[0]
    rng = np.random.default_rng(1)
    idx = pd.bdate_range("2026-08-03", periods=60)
    close = {"SPY": pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.005, 60)), index=idx)}
    for t in ("A", "B", "C"):
        close[t] = pd.Series(50 * np.cumprod(1 + rng.normal(0, 0.01, 60)), index=idx)
    rows = [{"time": f"{d.date()}T10:45-04:00", "ticker": t, "ratio": float(np.exp(rng.normal(0, 0.5)))} for d in idx[:50] for t in ("A", "B", "C")]
    lines = sd.flow_section(pd.DataFrame(rows), close)
    assert "ارتباط" in lines[0]


def test_report_and_main_survive_missing_data(tmp_path, monkeypatch):
    E = events(cont=0.004)
    oil, st, spy = oil_world(lead=0.03)
    O = sd.oil_events(oil, st, spy)
    text = "\n".join(sd.report(E.to_dict("records"), O, ["- x"], "تجربة"))
    assert "H-SGAP: تحققت" in text and "H-OIL: تحققت" in text and "النصف الثاني" in text
    assert "لا حكم بعد" in "\n".join(sd.report([], [], ["- x"], "فارغ"))
    monkeypatch.setattr(sd, "OUT", str(tmp_path / "r.md"))
    monkeypatch.setattr(sd, "load", lambda tickers: (_ for _ in ()).throw(RuntimeError("x")))
    assert sd.main([]) == 0
