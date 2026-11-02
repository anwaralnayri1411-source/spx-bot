"""رسائل المتابعة لا تدّعي أن السعر متأخر وهو لحظي (ولا العكس): النص يتبع المصدر الفعلي للسعر."""
from datetime import datetime

import options_bot as ob
import stocks_watcher as sw

NY = ob.NY
POS = {"id": "CVX:CALL:2026-10-30:240", "ticker": "CVX", "side": "CALL", "strike": 240.0, "exp": "2026-10-30", "entry": 1.46,
       "entry_spot": 230.0, "time": "2026-10-23T10:12-04:00"}


def render(src):
    n = datetime(2026, 10, 23, 11, 30, tzinfo=NY)
    q = {"val": 1.74, "spr": 0.05, "iv": 0.3, "src": src}
    f = sw.facts(POS, n, 232.5, q)
    return f, sw.message(POS, f, [], ("أبقِ", "x"), n)


def test_live_sources_are_labeled_live_and_never_as_15_minutes_late():
    for src in ("alpaca", "tasty"):
        f, text = render(src)
        assert f["src"] == src
        assert "سعر العقد لحظي" in text and "متأخر ~15د" not in text


def test_yahoo_prices_keep_the_delay_warning():
    for src in ("yahoo", "yahoo_fallback", None):
        _, text = render(src)
        assert "سعر العقد من ياهو متأخر ~15د" in text and "لحظي" not in text.split("\n")[2]


def test_spx_age_line_separates_live_contract_prices_from_delayed_trend_bars(monkeypatch):
    monkeypatch.setattr(ob, "_live_on", lambda: True)
    assert ob.age_note(19.0) == "⏱️ أسعار العقود لحظية؛ شموع الاتجاه متأخرة ~19 دقيقة."
    monkeypatch.setattr(ob, "_live_on", lambda: False)
    assert ob.age_note(19.0) == "⏱️ البيانات متأخرة ~19 دقيقة، تحقق من السعر الحي."


def test_liquidity_lines_info_only(tmp_path):
    from datetime import datetime
    import pandas as pd
    from zoneinfo import ZoneInfo
    p = tmp_path / "f.csv"
    pd.DataFrame([{"date": "2026-11-02", "time": t, "net_gex_m": 50.0, "call_wall": 7650.0, "put_wall": 7600.0,
                   "call_prem": 0.0, "put_prem": pp} for t, pp in (("10:30", 0.0), ("10:50", 2e6))]).to_csv(p, index=False)
    n = datetime(2026, 11, 2, 10, 55, tzinfo=ZoneInfo("America/New_York"))
    L = ob.liquidity_lines("CALL", 7640.0, n, str(p))
    assert "غاما موجبة: يوم ارتداد" in L[0] and "أرضية 7,600 · سقف 7,650" in L[0] and "يميل للبوت" in L[0]
    assert any("عكس هذه التوصية" in x for x in L) and any("قريبة من السقف 7,650" in x for x in L)
    P = ob.liquidity_lines("PUT", 7640.0, n, str(p))
    assert not any("عكس" in x for x in P[1:-2]) and not any("قريبة" in x for x in P)
    assert ob.liquidity_lines("CALL", 7640.0, n.replace(hour=11, minute=20), str(p)) == []    # أقدم من 15 دقيقة
    assert ob.liquidity_lines("CALL", 7640.0, n, str(tmp_path / "none.csv")) == []
