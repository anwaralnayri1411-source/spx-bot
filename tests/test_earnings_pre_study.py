"""اختبارات دراسة ما قبل إعلانات الأرباح (H-EARNPRE). لا شبكة."""
import json
import os
import sys
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import earnings_pre_study as ep  # noqa: E402


def _px(n=40, start=100.0):
    days = [d.date() for d in pd.bdate_range("2026-01-01", periods=n)]
    c = np.array([start + k for k in range(n)], float)
    return pd.DataFrame({"Open": c - 0.5, "Close": c}, index=days)


def test_event_index_rule():
    days = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]
    assert ep.event_index(pd.Timestamp("2026-01-05 16:05"), days) == 1     # بعد الإغلاق → الجلسة التالية
    assert ep.event_index(pd.Timestamp("2026-01-06 07:00"), days) == 1     # قبل الافتتاح → اليوم نفسه
    assert ep.event_index(pd.Timestamp("2026-01-04 16:00"), days) is None  # لا جلسة قبله


def test_event_row_fields():
    px = _px()
    ts = pd.Timestamp(str(px.index[29])) + pd.Timedelta(hours=16, minutes=5)
    r = ep.event_row("AMD", ts, 1.0, 1.2, 20.0, px, px)
    i = 30
    c = px["Close"].values
    assert r["d0"] == str(px.index[i]) and r["beat"] == 1
    assert abs(r["r0"] - (c[i] / c[i - 1] - 1)) < 1e-12 and abs(r["run20"] - (c[i - 1] / c[i - 21] - 1)) < 1e-12
    assert abs(r["preA"] - (c[i - 1] / c[i - 8] - 1)) < 1e-12 and abs(r["r5"] - (c[i + 5] / c[i - 1] - 1)) < 1e-12
    assert ep.event_row("AMD", pd.Timestamp(str(px.index[5])), 1, 1, 0, px, px) is None     # تاريخ قصير


def test_analyst_net_window():
    ud = pd.DataFrame({"Action": ["up", "down", "up", "main", "up"]},
                      index=pd.to_datetime(["2026-02-01", "2026-02-10", "2026-02-20", "2026-02-25", "2026-03-05"]))
    assert ep.analyst_net(ud, "2026-03-01T16:00:00") == (1, 4)              # 1/2 إلى 25/2 داخل النافذة، و5/3 بعد الإعلان
    assert ep.analyst_net(None, "2026-03-01") == (0, 0)


def test_parse_rss_and_url():
    xml = ("<rss><channel><item><title>AMD beats</title><pubDate>Tue, 04 Nov 2026 21:10:00 GMT</pubDate></item>"
           "<item><title>Chip stocks fall</title></item></channel></rss>")
    out = ep.parse_rss(xml)
    assert out[0][1] == "AMD beats" and out[0][0].startswith("2026-11-04T21:10") and out[1] == ("", "Chip stocks fall")
    assert ep.parse_rss("not xml") == []
    assert "after%3A2026-10-28+before%3A2026-11-04" in ep.gnews_url('"AMD" stock', "2026-10-28", "2026-11-04")


def test_composite_and_bh():
    assert ep.composite(2, 1, 0.0, -0.05, 0.05) == 1
    assert ep.composite(1, 0, 0.10, -0.05, 0.05) == 0          # أخبار إيجابية لكن صعود مسبق كبير
    assert ep.composite(float("nan"), -2, -0.10, -0.05, 0.05) == 0
    assert ep.bh([0.01, 0.04, 0.5]) == [True, False, False]
    assert ep.bh([0.01, 0.02, 0.5]) == [True, True, False]


def test_classify_respects_budget_and_cache(tmp_path, monkeypatch):
    for k, v in (("WORK", tmp_path), ("EVENTS", tmp_path / "events.csv"), ("NEWS_DIR", tmp_path / "news"),
                 ("CLS", tmp_path / "cls.jsonl"), ("USAGE", tmp_path / "usage.csv")):
        monkeypatch.setattr(ep, k, str(v))
    os.makedirs(ep.NEWS_DIR)
    ev = pd.DataFrame([{"sym": "AMD", "ann_day": f"2026-0{m}-05", "beat": 1, "r0": -0.03} for m in (1, 2, 3)])
    ev.to_csv(ep.EVENTS, index=False)
    for d in ev["ann_day"]:
        json.dump({"pre": [["", "AMD wins deal"]], "post": [["", "AMD guidance weak"]]}, open(os.path.join(ep.NEWS_DIR, f"AMD_{d}.json"), "w"))

    class C:
        calls = 0
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **kw):
            C.calls += 1
            items = [{"i": k, "tone": 1, "expect": "up", "reason": "guidance", "explain_ar": "توقعات ضعيفة"} for k in range(2)]
            return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
                                   content=[SimpleNamespace(type="text", text=json.dumps({"items": items}))])

    ep.stage_classify(C(), batch_size=2)
    rows = [json.loads(l) for l in open(ep.CLS, encoding="utf-8")]
    assert len(rows) == 3 and rows[0]["reason"] == "guidance" and C.calls == 2
    ep.stage_classify(C(), batch_size=2)                         # المخزّن لا يُقرأ مرتين
    assert C.calls == 2
    monkeypatch.setattr(ep, "BUDGET_USD", 0.0)
    os.remove(ep.CLS)
    ep.stage_classify(C(), batch_size=2)
    assert C.calls == 2                                          # السقف يوقف القراءة


def test_report_runs_on_synthetic(tmp_path, monkeypatch):
    monkeypatch.setattr(ep, "USAGE", str(tmp_path / "u.csv"))
    rng = np.random.default_rng(0)
    rows = []
    for k in range(300):
        d = str(pd.Timestamp("2022-01-03") + pd.Timedelta(days=5 * k))[:10]
        rows.append({"sym": f"S{k % 30}", "ann_day": d, "d0": d, "key": f"S{k % 30}_{d}", "day": d, "beat": rng.choice([1, -1]),
                     "surprise": rng.normal(), "run20": rng.normal(0, 0.05), "gap": 0.0, "r0": rng.normal(0, 0.05),
                     "r5": rng.normal(0, 0.06), "preA": rng.normal(0, 0.03), "qqq0": 0.0, "tone": rng.choice([-1, 0, 1]),
                     "expect": "up", "reason": "guidance", "explain_ar": "سبب", "analyst_net": rng.choice([-1, 0, 1])})
    md = ep.report(pd.DataFrame(rows))
    assert "P1 الصعود المسبق" in md and "P5 المركّب" in md and "مكتبة الأسباب" in md


def test_pre_only_drops_leaky_headlines():
    heads = [("2026-07-27T07:00:00+00:00", "ok"), ("2026-07-28T07:00:00+00:00", "too close"), ("2026-07-29T07:00:00+00:00", "after"),
             ("", "no date")]
    assert ep.pre_only(heads, "2026-07-29") == [("2026-07-27T07:00:00+00:00", "ok")]
