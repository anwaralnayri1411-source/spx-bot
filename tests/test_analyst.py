"""اختبارات المحلل الذكي (H-ANALYST). لا شبكة: النموذج وتيليجرام مزيّفان."""
from datetime import datetime
from types import SimpleNamespace

import pandas as pd

import analyst as an

T = datetime.fromisoformat


def _paths(tmp_path, monkeypatch):
    for k, name in (("CARDS", "analyst_cards.csv"), ("STATE", "analyst_state.json"), ("USAGE", "analyst_usage.csv"),
                    ("JOURNAL", "research/analyst_journal.md"), ("RESULTS", "research/analyst_results.md")):
        monkeypatch.setattr(an, k, str(tmp_path / name))
    monkeypatch.setattr(an, "ROOT", str(tmp_path))
    monkeypatch.setattr(an, "LOGDIR", str(tmp_path / "logs"))


class FakeClient:
    def __init__(self, text):
        self.text, self.calls = text, 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=self.text)],
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200))


# ---------------- النتيجة ----------------
def test_first_exit_stop_then_rebound():
    rows = [(T("2026-10-27T14:10"), "open", 0.05), (T("2026-10-27T14:30"), "stopped", -0.55),
            (T("2026-10-27T15:00"), "stopped", 1.2), (T("2026-10-27T16:00"), "stopped", 0.9)]
    o = an.path_outcome(rows, T("2026-10-27T14:05"))
    assert o["exit_kind"] == "وقف" and o["exit_ret"] == -0.55 and o["mins"] == 25
    assert o["peak_before"] == 0.05 and o["after_peak"] == 1.2 and o["last"] == 0.9


def test_first_exit_target_and_unresolved():
    o = an.path_outcome([(T("2026-10-27T10:00"), "open", 0.2), (T("2026-10-27T10:20"), "tp", 0.55)])
    assert o["exit_kind"] == "هدف" and o["exit_ret"] == 0.55 and o["after_peak"] is None
    o = an.path_outcome([(T("2026-10-27T10:00"), "open", 0.2), (T("2026-10-27T10:20"), "open", -0.1)])
    assert o["exit_kind"] == "بلا حسم" and o["exit_ret"] == -0.1 and o["peak_before"] == 0.2
    assert an.path_outcome([])["exit_ret"] is None


# ---------------- الشموع: معلومة معروفة وقتها فقط ----------------
def _bars():
    rows = []
    for d, base in (("2026-10-26", 100.0), ("2026-10-27", 101.0)):
        for i in range(20):
            t = T(f"{d}T09:30") + pd.Timedelta(minutes=5 * i)
            c = base + i * 0.1
            rows.append({"t": t, "Open": c - 0.05, "High": c + 0.1, "Low": c - 0.1, "Close": c, "day": d})
    df = pd.DataFrame(rows)
    return {d: g.reset_index(drop=True) for d, g in df.groupby("day")}


def test_bar_features_use_only_completed_bars():
    b = _bars()
    f = an.bar_features(b, "2026-10-27", T("2026-10-27T10:40"))     # 14 شمعة مكتملة (آخرها 10:35)
    last = 101.0 + 13 * 0.1
    assert abs(f["r30"] - (last / (101.0 + 7 * 0.1) - 1)) < 1e-9
    assert abs(f["r60"] - (last / (101.0 + 1 * 0.1) - 1)) < 1e-9
    assert abs(f["gap"] - ((101.0 - 0.05) / (100.0 + 19 * 0.1) - 1)) < 1e-9
    assert f["pos"] > 0.9
    assert an.bar_features(b, "2026-10-28", T("2026-10-28T10:40"))["r30"] is None


def test_move_bucket_relative_to_side():
    assert an.move_b(0.002, "PUT") == "ضد الاتجاه" and an.move_b(-0.002, "PUT") == "مع الاتجاه"
    assert an.move_b(0.0001, "CALL") == "مسطح" and an.move_b(None, "CALL") == ""


# ---------------- سياق الظلال ----------------
def test_shadow_context_uses_last_info_before_signal():
    logs = {"cand": pd.DataFrame([{"day": "2026-10-27", "slot": "10:30", "mom": "1", "f30": "-1"},
                                  {"day": "2026-10-27", "slot": "12:30", "mom": "-1", "f30": "-1"}]),
            "edge7": pd.DataFrame([{"entry_time": "2026-10-27T12:05-04:00", "role": "bot", "side": "CALL"}]),
            "mom": pd.DataFrame([{"date": "2026-10-27", "defn": "live", "role": "signal", "side": "PUT", "sig_time": "2026-10-27T11:00-04:00"},
                                 {"date": "2026-10-27", "defn": "live", "role": "signal", "side": "CALL", "sig_time": "2026-10-27T13:00-04:00"}]),
            "live_bars": pd.DataFrame([{"time": "2026-10-27T12:05-04:00", "side_live": "CALL"}]),
            "news": pd.DataFrame([{"time": "2026-10-27 12:05", "level": "3", "kind": "allowed_half"}])}
    c = an.shadow_context("2026-10-27", T("2026-10-27T12:05"), "CALL", logs)
    assert c["cand_mom"] == "مع" and c["cand_f30"] == "ضد"         # موعد 10:30 لا 12:30
    assert c["edge7"] == "نعم" and c["mom_live"] == "ضد" and c["live_side"] == "مع"
    assert c["news_level"] == "3" and c["news_kind"] == "allowed_half"


def test_shadow_context_blank_when_shadow_not_running():
    empty = {k: pd.DataFrame() for k in ("cand", "edge7", "mom", "live_bars", "news")}
    c = an.shadow_context("2026-10-06", T("2026-10-06T10:23"), "CALL", empty)
    assert all(v == "" for v in c.values())


# ---------------- حارس الأرقام ----------------
def test_guard_accepts_copied_numbers_and_rejects_invented():
    facts = '{"exit_ret_pct": -55.0, "strike": 7725, "time": "14:05", "mean_pct": -27.2}'
    assert an.unverified("أُوقفت عند −55% بعد 25 دقيقة، والمتوسط −27.2% على 7725", facts) == []
    assert an.unverified("نسبة النجاح 63% في هذا النوع", facts) == [63.0]


def test_daily_reading_guard_drops_invented_numbers(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    F = {"day": "2026-10-27", "spx": [{"time": "14:05", "sym": "SPX", "side": "CALL", "strike": 7725.0, "exit_kind": "وقف", "exit_ret": -0.55}],
         "stocks_new": [], "stocks_open": [], "blocked": None, "health": [], "tot_spx": None, "tot_stocks": None}
    now = T("2026-10-27T16:45-04:00")
    ok, st = an.daily_reading(F, "نص", now, FakeClient("أُوقفت **SPX CALL 7725** عند −55%: دخلت متأخرة."))
    assert st == "ok" and "7725" in ok
    bad, st = an.daily_reading(F, "نص", now, FakeClient("أُوقفت عند −55%، وهذا النوع يربح 68% عادة."))
    assert bad is None and st == "guard"


def test_daily_cap_blocks_model_call(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    an.log_usage({"time": "2026-10-27T16:45:00-04:00", "kind": "daily", "usd": an.DAILY_USD})
    c = FakeClient("x")
    assert an.ask("s", "u", an.MODEL, "daily", T("2026-10-27T17:00-04:00"), c) is None and c.calls == 0


# ---------------- تنبيه العقود المتعاكسة (تنبيه فقط) ----------------
def _card(**k):
    base = {"project": "spx", "day": "2026-10-27", "exit_time": "", "exit_kind": "بلا حسم", "exp": "2026-10-27"}
    return dict(base, **k)


def test_opposite_overlap_spx_family():
    cards = [_card(id="SPX:CALL", sym="SPX", side="CALL", time="10:30", exit_time="11:00", exit_kind="وقف"),
             _card(id="SPY:PUT", sym="SPY", side="PUT", time="10:45")]
    out = an.opposite_overlaps("2026-10-27", cards)
    assert len(out) == 1 and "SPX/SPY" in out[0]
    cards[1]["time"] = "11:30"
    assert an.opposite_overlaps("2026-10-27", cards) == []


# ---------------- الجداول ----------------
def test_tables_hide_small_cells():
    cards = [dict(_card(id=str(i), sym="SPX", side="CALL", time="12:00"), tod="المنتصف" if i < 5 else "الإغلاق",
                  exit_kind="هدف" if i % 2 else "وقف", exit_ret=0.5 if i % 2 else -0.4) for i in range(7)]
    md = an.weekly_md(cards, "2026-10-31")
    assert "| وقت اليوم | المنتصف | 5 | 40% |" in md
    assert "| وقت اليوم | الإغلاق | 2 | - | - |" in md


# ---------------- التشغيل ----------------
def test_run_daily_gates(tmp_path, monkeypatch, capsys):
    _paths(tmp_path, monkeypatch)
    assert an.run_daily(T("2026-10-31T17:00-04:00")) == 0          # السبت
    assert an.run_daily(T("2026-10-30T15:00-04:00")) == 0          # قبل 16:40
    assert an.run_daily(T("2026-10-30T17:00-04:00")) == 0          # لا جلسة في السجلات
    out = capsys.readouterr().out
    assert "خارج نافذة" in out and "لا جلسة" in out


def test_send_only_to_owner(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    monkeypatch.setenv("TELEGRAM_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    monkeypatch.setenv("TELEGRAM_EXTRA_CHAT_IDS", "222,333")
    sent = []
    import requests
    monkeypatch.setattr(requests, "post", lambda url, data, timeout: sent.append(data) or SimpleNamespace(ok=True))
    assert an.send("**عنوان**\n" + "سطر\n" * 2000)
    assert len(sent) >= 2 and {d["chat_id"] for d in sent} == {"111"}
    assert sent[0]["text"].startswith("<b>عنوان</b>")
