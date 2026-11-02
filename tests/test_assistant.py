"""اختبارات مساعد المحادثة: أداة الأخبار، وحفظ مصدر الرد في السجل. لا شبكة."""
import json
from datetime import datetime
from types import SimpleNamespace

import pandas as pd

import assistant as asst
import news_watch as nw
import options_bot as ob


def _news_env(tmp_path, monkeypatch):
    monkeypatch.setattr(asst, "LIVE", str(tmp_path))
    now = pd.Timestamp.now(tz="UTC")
    (tmp_path / "news-watch").mkdir()
    rows = [{"time_utc": (now - pd.Timedelta(hours=2)).isoformat(), "night": "x", "key": "a", "query": "q", "title": "China tariffs hiked",
             "source": "s", "new": 1, "scope": "market", "ticker": "", "importance": "high", "direction": "down", "topic": "رفع الرسوم على الصين",
             "cluster": "china", "alerted": 1},
            {"time_utc": (now - pd.Timedelta(hours=1)).isoformat(), "night": "x", "key": "b", "query": "q", "title": "China tariffs: more",
             "source": "s", "new": 1, "scope": "market", "ticker": "", "importance": "high", "direction": "down", "topic": "نفس الحدث",
             "cluster": "china", "alerted": 0},
            {"time_utc": (now - pd.Timedelta(hours=50)).isoformat(), "night": "x", "key": "c", "query": "q", "title": "Old news",
             "source": "s", "new": 1, "scope": "market", "ticker": "", "importance": "high", "direction": "up", "topic": "قديم",
             "cluster": "old", "alerted": 0},
            {"time_utc": (now - pd.Timedelta(hours=1)).isoformat(), "night": "x", "key": "d", "query": "q", "title": "Preview piece",
             "source": "s", "new": 0, "scope": "market", "ticker": "", "importance": "high", "direction": "up", "topic": "تحليل",
             "cluster": "prev", "alerted": 0}]
    pd.DataFrame(rows).to_csv(tmp_path / "news-watch" / "news_watch_log.csv", index=False)
    monkeypatch.setattr(nw, "fut_bars", lambda s: None)
    monkeypatch.setattr(ob, "fetch_headlines", lambda: {"market": [], "geo": []})
    monkeypatch.setattr(ob, "market_shock", lambda h: {"level": 3, "flags": ["VIX قفز 25% في يوم واحد"]})


def test_market_news_tool(tmp_path, monkeypatch):
    _news_env(tmp_path, monkeypatch)
    out = asst.t_news()
    assert "رفع الرسوم على الصين" in out and "نفس الحدث" not in out          # حدث واحد مرة واحدة
    assert "قديم" not in out and "تحليل" not in out                          # خارج الفترة / ليس حدثاً جديداً
    assert "المستوى 3" in out and "VIX قفز 25%" in out
    assert "ES: غير متاح" in out
    assert "market_news" in {t["name"] for t in asst.tool_defs()}


def test_market_news_tool_without_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(asst, "LIVE", str(tmp_path / "none"))
    monkeypatch.setattr(asst, "_fresh_csv", lambda name, group: pd.DataFrame())   # لا نسخة المستودع من السجلات
    monkeypatch.setattr(nw, "fut_bars", lambda s: None)
    monkeypatch.setattr(ob, "fetch_headlines", lambda: {"market": [], "geo": []})
    monkeypatch.setattr(ob, "market_shock", lambda h: {"level": 1, "flags": []})
    monkeypatch.chdir(tmp_path)
    out = asst.t_news("COIN", 12)
    assert "لا أخبار مهمة مسجّلة" in out and "أخبار COIN" in out


class FakeClient:
    def __init__(self):
        self.messages = self
        self.n = 0

    def create(self, **kw):
        self.n += 1
        if self.n == 1:
            return SimpleNamespace(stop_reason="tool_use", usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                                   content=[SimpleNamespace(type="tool_use", id="t1", name="market_news", input={})])
        return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                               content=[SimpleNamespace(type="text", text="الرسوم على الصين خبر عالي الأهمية.")])


def test_history_keeps_reply_source(tmp_path, monkeypatch):
    _news_env(tmp_path, monkeypatch)
    monkeypatch.setattr(asst, "HIST_FILE", str(tmp_path / "h.json"))
    monkeypatch.setattr(asst, "USAGE_FILE", str(tmp_path / "u.csv"))
    reply, info = asst.answer("وش الأخبار؟", datetime.fromisoformat("2026-11-02T08:00-05:00"), FakeClient())
    assert "[مصدر" not in reply                                               # المستخدم لا يرى السطر
    h = json.load(open(tmp_path / "h.json", encoding="utf-8"))
    assert h[-1]["content"].endswith("[مصدر هذا الرد: نتائج الأدوات market_news وقت 2026-11-02 08:00 نيويورك]")


def test_spx_text_labels_live_source(monkeypatch):
    import commands_bot as cb
    import live_quotes as lq
    idx = pd.DatetimeIndex([pd.Timestamp("2026-10-30", tz="America/New_York")])
    fake = SimpleNamespace(history=lambda **k: pd.DataFrame({"Close": [7580.0]}, index=idx))
    monkeypatch.setattr(cb.yf, "Ticker", lambda s: fake)
    monkeypatch.setattr(cb.ob, "vix_info", lambda: (None, None))
    monkeypatch.setattr(cb.sb, "_live_on", lambda: True)
    monkeypatch.setattr(lq, "spot", lambda s: (7616.6, "Tastytrade"))
    n = datetime.fromisoformat("2026-11-02T10:00-05:00")
    out = cb.spx_text(n)
    assert "7,616.6" in out and "السعر لحظي من Tastytrade" in out and "متأخر" not in out
    monkeypatch.setattr(lq, "spot", lambda s: (None, None))
    out = cb.spx_text(n)
    assert "7,580.0" in out and "ياهو متأخر ~15" in out
