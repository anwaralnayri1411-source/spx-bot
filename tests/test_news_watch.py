"""اختبارات مراقب الأخبار خارج الجلسة (H-NEWSWATCH). لا شبكة: العناوين والنموذج والعقود الآجلة مزيّفة."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

import analyst as an
import news_watch as nw

T = datetime.fromisoformat


def _paths(tmp_path, monkeypatch):
    for k, name in (("LOG", "news_watch_log.csv"), ("STATE", "news_watch_state.json"), ("NIGHTS", "news_watch_nights.csv"),
                    ("USAGE", "news_watch_usage.csv")):
        monkeypatch.setattr(nw, k, str(tmp_path / name))
    monkeypatch.setattr(nw, "ROOT", str(tmp_path))
    monkeypatch.setattr(an, "ROOT", str(tmp_path))
    sent = []
    monkeypatch.setattr(an, "send", lambda text, src="analyst": sent.append((src, text)) or True)
    return sent


class FakeClient:
    def __init__(self, items):
        self.items, self.calls = items, 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=500, output_tokens=100),
                               content=[SimpleNamespace(type="text", text=json.dumps({"items": self.items}))])


def _item(i, imp="high", scope="market", cluster="china-tariff", new=True, ticker=""):
    return {"i": i, "new": new, "scope": scope, "ticker": ticker, "importance": imp, "direction": "down", "topic": "رسوم على الصين",
            "cluster": cluster}


def _fetcher(titles):
    def f(since, seen, tickers):
        out = []
        for k, t in enumerate(titles):
            key = f"k{k}{t}"
            if key not in seen:
                seen[key] = "2026-11-02T01:00:00+00:00"
                out.append({"t": datetime(2026, 11, 2, 1, k, tzinfo=timezone.utc), "key": key, "query": "q", "title": t, "source": "s"})
        return out
    return f


def _fut(last, ref=100.0, last_t="2026-11-01T20:55"):
    idx = [T("2026-10-30T15:55"), T("2026-10-30T16:00"), T(last_t)]
    return pd.Series([ref - 1, ref, last], index=idx)


# ---------------- الوقت ----------------
def test_session_and_night_keys():
    assert nw.in_session(T("2026-11-02T10:00-05:00")) and not nw.in_session(T("2026-11-02T16:00-05:00"))
    assert not nw.in_session(T("2026-11-01T10:00-05:00"))                      # الأحد
    assert nw.night_of(T("2026-11-02T08:00-05:00")) == "2026-11-02"
    assert nw.night_of(T("2026-10-30T18:00-04:00")) == "2026-11-02"            # الجمعة مساءً → الاثنين
    assert nw.night_of(T("2026-11-01T12:00-05:00")) == "2026-11-02"
    assert nw.last_close(T("2026-11-01T20:00-05:00")) == T("2026-10-30T16:00")
    assert nw.last_close(T("2026-11-02T17:00-05:00")) == T("2026-11-02T16:00")


def test_fut_move_and_staleness():
    m, age = nw.fut_move(_fut(101.5), T("2026-11-01T21:00-05:00"), T("2026-10-30T16:00"))
    assert abs(m - 0.015) < 1e-9 and age == 5
    assert "مغلقة" in nw.fut_line({"ES": (0.01, 600)})
    assert nw.fut_move(None, T("2026-11-01T21:00-05:00"), T("2026-10-30T16:00")) == (None, None)


# ---------------- التنبيه ----------------
def test_alert_rules():
    assert nw.alertable(_item(0), {})
    assert not nw.alertable(_item(0, imp="medium"), {})
    assert not nw.alertable(_item(0, new=False), {})
    assert not nw.alertable(_item(0, scope="company", ticker="COIN"), {})
    assert nw.alertable(_item(0, scope="company", ticker="coin"), {"COIN": "PUT"})


def test_run_alerts_once_per_event_and_caps(tmp_path, monkeypatch):
    sent = _paths(tmp_path, monkeypatch)
    now = T("2026-11-01T21:00-05:00")
    items = [_item(0), _item(1), _item(2, cluster="fed-surprise"), _item(3, imp="low", cluster="x")]
    nw.run(now, client=FakeClient(items), fetcher=_fetcher(["a", "b", "c", "d"]), futures={"ES": _fut(100.2), "NQ": _fut(100.1)})
    assert len(sent) == 1 and sent[0][0] == "news_watch"
    assert sent[0][1].count("• ") == 2 and "توقّع غير مختبر" in sent[0][1]          # حدثان مختلفان، والعنوان المكرر لا يُنبَّه
    st = json.load(open(nw.STATE, encoding="utf-8"))
    assert st["alerts"]["2026-11-02"] == 2
    log = pd.read_csv(nw.LOG)
    assert len(log) == 4 and log["alerted"].sum() == 2
    # تشغيل ثانٍ بالحدث نفسه وأحداث جديدة: لا تكرار، والسقف 4
    sent.clear()
    items = [_item(0), _item(1, cluster="e3"), _item(2, cluster="e4"), _item(3, cluster="e5")]
    nw.run(now, client=FakeClient(items), fetcher=_fetcher(["e", "f", "g", "h"]), futures={"ES": _fut(100.2)})
    assert sent[0][1].count("• ") == 2
    assert json.load(open(nw.STATE, encoding="utf-8"))["alerts"]["2026-11-02"] == 4


def test_futures_alert_levels_once(tmp_path, monkeypatch):
    sent = _paths(tmp_path, monkeypatch)
    now = T("2026-11-01T21:00-05:00")
    nw.run(now, fetcher=_fetcher([]), futures={"ES": _fut(98.8), "NQ": _fut(99.0)})
    assert len(sent) == 1 and "العقود الآجلة تتحرك" in sent[0][1] and "−1.2%" in sent[0][1]
    nw.run(now, fetcher=_fetcher([]), futures={"ES": _fut(98.7)})
    assert len(sent) == 1                                                     # الحد نفسه لا يتكرر
    nw.run(now, fetcher=_fetcher([]), futures={"ES": _fut(97.9)})
    assert len(sent) == 2                                                     # تجاوز −2%


def test_stale_futures_do_not_alert(tmp_path, monkeypatch):
    sent = _paths(tmp_path, monkeypatch)
    nw.run(T("2026-10-31T12:00-04:00"), fetcher=_fetcher([]), futures={"ES": _fut(97.0, last_t="2026-10-30T17:00")})
    assert sent == []


def test_skips_inside_session(tmp_path, monkeypatch):
    sent = _paths(tmp_path, monkeypatch)
    called = []
    nw.run(T("2026-11-02T11:00-05:00"), fetcher=lambda *a: called.append(1) or [], futures={})
    assert sent == [] and called == []


def test_morning_summary_once(tmp_path, monkeypatch):
    sent = _paths(tmp_path, monkeypatch)
    nw.run(T("2026-11-01T21:00-05:00"), client=FakeClient([_item(0), _item(1, imp="medium", cluster="oil")]),
           fetcher=_fetcher(["a", "b"]), futures={"ES": _fut(100.3)})
    sent.clear()
    now = T("2026-11-02T08:35-05:00")
    nw.run(now, fetcher=_fetcher([]), futures={"ES": _fut(100.3, last_t="2026-11-02T08:30")})
    assert len(sent) == 1 and "ملخص أخبار الليل" in sent[0][1]
    assert "[عالية]" in sent[0][1] and "[متوسطة]" in sent[0][1]
    nw.run(now, fetcher=_fetcher([]), futures={"ES": _fut(100.3, last_t="2026-11-02T08:30")})
    assert len(sent) == 1
    assert json.load(open(nw.STATE, encoding="utf-8"))["morning_es"]["2026-11-02"] == 0.003


def test_no_send_writes_nothing(tmp_path, monkeypatch):
    sent = _paths(tmp_path, monkeypatch)
    nw.run(T("2026-11-01T21:00-05:00"), no_send=True, client=FakeClient([_item(0)]), fetcher=_fetcher(["a"]), futures={"ES": _fut(98.0)})
    assert sent == []
    import os
    assert not os.path.exists(nw.STATE)


def test_record_night(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    bars = {}
    for d, base in (("2026-10-30", 100.0), ("2026-11-02", 98.0)):
        rows = [{"t": T(f"{d}T09:30") + pd.Timedelta(minutes=5 * i), "Open": base + i * 0.1, "High": base + 1, "Low": base - 1,
                 "Close": base + i * 0.1} for i in range(10)]
        bars[d] = pd.DataFrame(rows)
    monkeypatch.setattr(an, "load_bars", lambda days: bars)
    st = {"alerts": {"2026-11-02": 2}, "morning_es": {"2026-11-02": -0.012}}
    assert nw.record_night("2026-11-02", st)
    assert not nw.record_night("2026-11-02", st)
    r = pd.read_csv(nw.NIGHTS).iloc[0]
    assert r["news_alerts"] == 2 and r["es_move"] == -0.012
    assert abs(r["spx_gap"] - (98.0 / 100.9 - 1)) < 1e-5


def test_fut_line_source_note():
    assert "لحظي من Tastytrade" in nw.fut_line({"ES": (-0.012, 0.1, "live"), "NQ": (-0.022, 0.1, "live")})
    assert "ياهو، متأخرة ~10د" in nw.fut_line({"ES": (-0.012, 3, "yahoo"), "NQ": (None, None, "yahoo")})
    assert "بعضها من ياهو" in nw.fut_line({"ES": (-0.012, 0.1, "live"), "NQ": (-0.02, 3, "yahoo")})


class ExplainClient:
    """أول طلب تصنيف، والثاني ترجمة وتحليل."""
    def __init__(self, items):
        self.items, self.calls = items, 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        body = {"items": self.items} if self.calls == 1 else {"items": [{"i": 0, "title_ar": "هبوط العقود الآجلة مع تقدم الديمقراطيين",
                                                                            "analysis": "ضغط على التقنية وناسداك؛ تقدير غير مختبر."}]}
        return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=500, output_tokens=100),
                               content=[SimpleNamespace(type="text", text=json.dumps(body))])


def test_alert_has_arabic_translation_and_analysis(tmp_path, monkeypatch):
    sent = _paths(tmp_path, monkeypatch)
    cl = ExplainClient([_item(0, cluster="election")])
    nw.run(T("2026-11-03T23:30-05:00"), client=cl, fetcher=_fetcher(["Stock futures hammered as Democrats gain"]),
           futures={"ES": _fut(98.8, last_t="2026-11-03T23:25"), "NQ": _fut(97.8, last_t="2026-11-03T23:25")})
    msg = sent[0][1]
    assert "**هبوط العقود الآجلة مع تقدم الديمقراطيين**" in msg and "📝 ضغط على التقنية" in msg
    assert "<i>Stock futures hammered as Democrats gain</i>" in msg and cl.calls == 2


def test_night_context_lines(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    rows = [{"time_utc": "2026-11-04T04:20:00+00:00", "night": "2026-11-04", "key": "a", "query": "q", "title": "Futures hammered",
             "source": "s", "new": "1", "scope": "market", "ticker": "", "importance": "high", "direction": "down",
             "topic": "هبوط العقود مع تقدم الديمقراطيين", "cluster": "election", "alerted": "1"},
            {"time_utc": "2026-11-04T05:00:00+00:00", "night": "2026-11-04", "key": "b", "query": "q", "title": "NVDA deal",
             "source": "s", "new": "1", "scope": "company", "ticker": "NVDA", "importance": "medium", "direction": "up",
             "topic": "صفقة جديدة لإنفيديا", "cluster": "nvda-deal", "alerted": "0"}]
    nw.append_log(rows)
    L = nw.night_context("2026-11-04", "NVDA", "CALL")
    assert L[0] == "📰 أخبار الليل: NVDA: صفقة جديدة لإنفيديا — متوقع صعود ✅ مع التوصية"
    assert "⚠️ عكس التوصية" in L[1] and "الديمقراطيين" in L[1]
    assert nw.night_context("2026-11-04", None, "PUT") == ["📰 أخبار الليل: هبوط العقود مع تقدم الديمقراطيين — متوقع هبوط ✅ مع التوصية"]
    assert nw.night_context("2026-11-05") == []
