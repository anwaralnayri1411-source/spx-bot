"""اختبارات شبكة الوقف والهدف: إعادة التشغيل، الفصل الأمامي، الفرق المزدوج، والحكم. بيانات مصطنعة."""
import json
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import exit_grid_study as eg  # noqa: E402

T0 = pd.Timestamp("2026-10-26 10:00")


def path(vals, step=15):
    return [(T0 + pd.Timedelta(minutes=step * (i + 1)), v) for i, v in enumerate(vals)]


def test_replay_hits_target_stop_with_min_age_and_falls_back_to_last_value():
    assert eg.replay(path([0.2, 0.55, 0.1]), T0, 0.4, 0.5, 30) == 0.5                       # الهدف
    assert eg.replay(path([-0.5, -0.1, 0.0]), T0, 0.4, 0.5, 30) == 0.0                      # الوقف قبل 30 دقيقة لا يُحتسب (أول فحص عند 15 د)
    assert eg.replay(path([-0.1, -0.5, 0.3]), T0, 0.4, 0.5, 30) == -0.4                     # الوقف بعد 30 دقيقة
    assert eg.replay(path([-0.1, -0.5, 0.3]), T0, None, 1.0, 30) == 0.3                     # بلا وقف: آخر قيمة
    assert eg.replay(path([-0.1, -0.5, 0.3]), T0, 0.6, 0.5, 30) == 0.3                      # وقف أوسع لم يُبلَغ
    assert eg.replay([], T0, 0.4, 0.5, 30) is None


def synthetic(n=60, stop_noise=True, seed=1):
    rng = np.random.default_rng(seed)
    entries, paths = [], {}
    for i in range(n):
        t0 = pd.Timestamp("2026-10-26 10:00") + pd.Timedelta(days=i // 2)
        pid = f"X{i}:CALL:2026-11-20"
        drift = rng.normal(0.0, 0.15)
        steps = np.cumsum(rng.normal(drift / 10, 0.12, 12))
        paths[pid] = [(t0 + pd.Timedelta(minutes=30 * (j + 1)), float(v)) for j, v in enumerate(steps)]
        entries.append({"id": pid, "t0": t0, "exp": date(2026, 11, 20)})
    return entries, paths


def test_evaluate_builds_all_rules_and_forward_split_uses_entry_day_and_expiry():
    entries, paths = synthetic()
    df = eg.evaluate(entries, paths, 30)
    assert len(df) == 60 and set(eg.RULES) <= set(df.columns)
    fwd, past = eg.split_forward(df, today=date(2026, 12, 1))
    assert len(fwd) == 60 and len(past) == 0
    fwd2, _ = eg.split_forward(df, today=date(2026, 11, 1))                                # لم تنتهِ بعد
    assert len(fwd2) == 0
    old = df.assign(day="2026-10-20")
    assert len(eg.split_forward(old, today=date(2026, 12, 1))[1]) == 60


def test_paired_difference_is_computed_per_recommendation_and_needs_eight_days():
    df = pd.DataFrame({"day": ["d1"] * 4 + ["d2"] * 4, "A": [0.1] * 8, "B": [0.3] * 8, "C": 0.0, "D": 0.0, "E": 0.0, "id": range(8), "exp": "2026-11-20"})
    r = eg.paired_ci(df, "B")
    assert r["mean"] == pytest.approx(0.2) and r["ci"] is None
    days = [f"d{i}" for i in range(12) for _ in range(4)]
    df2 = pd.DataFrame({"day": days, "A": np.random.default_rng(1).normal(0, 0.2, 48)})
    df2["B"] = df2["A"] + 0.3
    c = eg.paired_ci(df2, "B")
    assert c["ci"][0] > 0.2 and c["mean"] == pytest.approx(0.3)


def test_verdict_waits_confirms_best_candidate_and_rejects_noise():
    base = np.random.default_rng(2).normal(-0.05, 0.3, 60)
    days = [f"d{i}" for i in range(20) for _ in range(3)]
    df = pd.DataFrame({"day": days, "A": base, "B": base + 0.25, "C": base + 0.05 + np.random.default_rng(3).normal(0, 0.3, 60),
                       "D": base + np.random.default_rng(4).normal(0, 0.3, 60), "E": base, "id": range(60), "exp": "2026-11-20"})
    assert "لا حكم بعد" in eg.verdict(df.head(10), "stocks")
    v = eg.verdict(df, "stocks")
    assert "تحققت للقاعدة B" in v and "صاحب المشروع" in v
    flat = df.assign(B=df["A"], C=df["A"] + np.random.default_rng(5).normal(0, 0.3, 60), D=df["A"] + np.random.default_rng(6).normal(0, 0.3, 60))
    assert "لم تتحقق" in eg.verdict(flat, "stocks")


def test_loaders_and_main_work_on_files(tmp_path, monkeypatch):
    (tmp_path / "stocks_positions.json").write_text(json.dumps({"positions": [
        {"id": "AA:CALL:2026-11-20:100", "time": "2026-10-26T10:00-04:00", "exp": "2026-11-20"}]}), encoding="utf-8")
    rows = ["time,id,ticker,side,status,spot,val,pnl,stock_move,prob,spy,vix"] + [
        f"2026-10-26T{10 + (i * 30) // 60:02d}:{(i * 30) % 60:02d}-04:00,AA:CALL:2026-11-20:100,AA,CALL,open,1,1,{0.1 * i},0,0,1,1" for i in range(1, 8)]
    (tmp_path / "stocks_track.csv").write_text("\n".join(rows), encoding="utf-8")
    (tmp_path / "spx_track.csv").write_text("time,id,status,spot,val,pnl\n2026-10-26T10:05-04:00,SPY:CALL:771:2026-10-26,open,770,0.5,0.0\n"
                                           "2026-10-26T10:35-04:00,SPY:CALL:771:2026-10-26,open,771,0.9,0.8\n", encoding="utf-8")
    entries, paths = eg.load_stocks(str(tmp_path))
    assert len(entries) == 1 and len(paths["AA:CALL:2026-11-20:100"]) == 7
    e2, p2 = eg.load_spx(str(tmp_path))
    assert e2[0]["exp"] == date(2026, 10, 26) and e2[0]["t0"] == pd.Timestamp("2026-10-26 10:05")
    monkeypatch.setattr(eg, "OUT", str(tmp_path / "r.md"))
    monkeypatch.setattr(eg, "ROOT", str(tmp_path))
    monkeypatch.setattr(eg, "load_stocks", lambda root=None: (entries, paths))
    assert eg.main([]) == 0
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "H-EXIT (stocks)" in text and "H-EXIT (spx)" in text and "الأمامية" in text


def test_missing_files_do_not_crash_main(tmp_path, monkeypatch):
    monkeypatch.setattr(eg, "OUT", str(tmp_path / "r.md"))
    monkeypatch.setattr(eg, "ROOT", str(tmp_path / "none"))
    monkeypatch.setattr(eg, "load_stocks", lambda root=None: (_ for _ in ()).throw(FileNotFoundError("x")))
    monkeypatch.setattr(eg, "load_spx", lambda root=None: (_ for _ in ()).throw(FileNotFoundError("x")))
    assert eg.main([]) == 0
    assert "لا حكم بعد" in (tmp_path / "r.md").read_text(encoding="utf-8")
