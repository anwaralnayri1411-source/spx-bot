"""اختبارات مراقب سيولة SPX (H-SPXFLOW). لا شبكة."""
from datetime import datetime

import numpy as np
import pandas as pd

import spx_flow_watch as fw

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=fw.NY)


def _chain(vol_scale=1.0, last_at="ask"):
    rows = []
    for K in (7580, 7600, 7620, 7640, 7660):
        for side, oi in (("C", 900 if K == 7640 else 100), ("P", 1500 if K == 7600 else 100)):
            bid, ask = 4.0, 4.4
            rows.append({"time": "10:00", "symbol": f"{side}{K}", "side": side, "strike": float(K), "oi": float(oi),
                         "volume": (300 if K == 7660 else 50) * vol_scale, "gamma": 0.01, "delta": 0.5 if side == "C" else -0.5,
                         "iv": 0.18, "bid": bid, "ask": ask, "last": ask if last_at == "ask" else bid})
    return pd.DataFrame(rows, columns=fw.RAW_COLS)


def test_walls_pick_oi_and_volume():
    w = fw.walls(_chain(), 7621)
    assert w["call_wall"] == 7640 and w["put_wall"] == 7600
    assert w["vol_call_wall"] == 7660 and w["call_wall_oi"] == 900


def test_gex_sign_and_flip():
    df = _chain()
    T = fw.years_left(NOW)
    assert fw.gex_at(df, 7600, T) < 0                    # فوق جدار البوت الضخم: الغاما سالبة
    fp = fw.flip_point(df, 7621, T)
    assert fp is None or 7621 * 0.985 <= fp <= 7621 * 1.015


def test_flow_classifies_buys_and_sells():
    prev = _chain()
    buys = fw.flow(prev, _chain(vol_scale=2.0, last_at="ask"))
    sells = fw.flow(prev, _chain(vol_scale=2.0, last_at="bid"))
    assert buys["call_prem"] > 0 and buys["put_prem"] > 0 and buys["net_delta"] == 0
    assert sells["call_prem"] < 0 and sells["put_prem"] < 0
    assert fw.flow(None, _chain()) == {"call_prem": 0.0, "put_prem": 0.0, "net_delta": 0.0}


def test_flow_ignores_volume_resets():
    assert fw.flow(_chain(vol_scale=3.0), _chain())["call_prem"] == 0


def _log(days=3, gex=(5.0, -5.0, 5.0)):
    rows = []
    for d in range(days):
        date = f"2026-11-{d + 2:02d}"
        for i in range(70):
            t = (pd.Timestamp("09:35") + pd.Timedelta(minutes=5 * i)).strftime("%H:%M")
            spot = 7600 + (i % 10) * (1 if d % 2 else 2)
            rows.append({"date": date, "time": t, "spot": spot, "call_wall": 7610.0, "call_wall_oi": 1, "put_wall": 7590.0,
                         "put_wall_oi": 1, "vol_call_wall": 7610.0, "vol_put_wall": 7590.0, "net_gex_m": gex[d % len(gex)],
                         "flip": "", "call_prem": 1000.0 if i % 2 else 0.0, "put_prem": 0.0, "net_delta": 0.0, "contracts": 10})
    return pd.DataFrame(rows, columns=fw.COLS)


def test_outcomes_and_hypothesis_inputs():
    o = fw.outcomes(_log())
    r = o[(o.date == "2026-11-02") & (o.time == "10:00")].iloc[0]
    later = o[(o.date == "2026-11-02") & (o.time == "10:30")].iloc[0]
    assert abs(r.r30 - (later.spot / r.spot - 1)) < 1e-12
    assert np.isnan(o[(o.date == "2026-11-02") & (o.time == "15:20")].iloc[0].r60)   # لا لقطة بعد 60 دقيقة
    f1 = fw.f1_touches(o)
    assert set(f1.kind) <= {"call", "put"} and f1.groupby(["date", "kind"]).size().max() == 1   # أول اقتراب فقط
    f2 = fw.f2_days(o)
    assert len(f2) == 3 and (f2.gex > 0).sum() == 2
    f3 = fw.f3_obs(o)
    assert set(f3.time.str[-2:]) <= {"00", "30"} and f3.time.min() >= "10:00" and f3.time.max() <= "15:30"


def test_bh_and_evaluate(tmp_path, monkeypatch):
    assert fw.bh([0.01, 0.04, np.nan]) == [True, True, False]
    assert fw.bh([0.01, 0.2, 0.5]) == [True, False, False]
    monkeypatch.setattr(fw, "LOG", str(tmp_path / "log.csv"))
    monkeypatch.setattr(fw, "RESULTS", str(tmp_path / "r" / "res.md"))
    assert fw.evaluate() == "لا سجل بعد."
    _log().to_csv(fw.LOG, index=False)
    out = fw.evaluate()
    assert "قبل اكتمال العينة" in out and "F1" in out and "F3" in out
    assert (tmp_path / "r" / "res.md").exists()


def test_snap_writes_log_and_raw(tmp_path, monkeypatch):
    monkeypatch.setattr(fw, "LOG", str(tmp_path / "log.csv"))
    monkeypatch.setattr(fw, "RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(fw, "fetch", lambda now: (7621.0, _chain()))
    row = fw.snap(NOW)
    assert row["call_wall"] == 7640 and row["call_prem"] == 0
    assert fw.snap(NOW) is None                                           # الدقيقة نفسها مرة واحدة
    later = NOW.replace(minute=5)
    monkeypatch.setattr(fw, "fetch", lambda now: (7622.0, _chain(vol_scale=2.0).assign(time="10:05")))
    assert fw.snap(later)["call_prem"] > 0
    assert len(pd.read_csv(fw.LOG)) == 2 and len(pd.read_csv(fw.raw_path("2026-11-02"))) == 20


def test_main_gate(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(fw, "snap", lambda now: called.append(now))
    monkeypatch.setattr(fw, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: datetime(2026, 11, 1, 10, 0, tzinfo=fw.NY))}))
    fw.main([])
    assert not called and "خارج" in capsys.readouterr().out          # الأحد


def test_f4_divergence_signals():
    rows = []
    for i in range(30):
        t = (pd.Timestamp("09:35") + pd.Timedelta(minutes=5 * i)).strftime("%H:%M")
        up = t <= "10:30"
        rows.append({"date": "2026-11-02", "time": t, "spot": 7600 + i if up else 7611 - (i - 11), "call_prem": 0.0 if up else 0.0,
                     "put_prem": 1000.0 if t > "10:00" and up else 0.0, "net_gex_m": 1.0})
    o = fw.outcomes(pd.DataFrame(rows))
    f4 = fw.f4_divergence(o)
    hit = f4[f4.time == "10:30"]
    assert len(hit) == 1 and hit.kind.iloc[0] == "bear" and hit.y.iloc[0] > 0   # قمة جديدة والشد يهبط ← هبط السعر بعدها
