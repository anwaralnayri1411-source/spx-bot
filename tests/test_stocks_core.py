"""اختبارات منطق بوت الشركات ومراقبها: الإحصاء التاريخي، حالة العقد المقفل، التقييم، والنصيحة.
لا شبكة: كل البيانات مصطنعة."""
from datetime import datetime, timedelta

import numpy as np
import pytest

import options_bot as ob
import stocks_bot as sb
import stocks_watcher as sw

NY = ob.NY


# ---------- الاحتمال التاريخي ----------
def test_hist_stats_returns_none_when_history_is_too_short():
    assert sb.hist_stats([100.0] * 12, 3, 0.02, "CALL") is None


def test_hist_stats_steady_uptrend_always_reaches_target_for_calls_never_for_puts():
    closes = list(100 * (1.01 ** np.arange(60)))              # +1% كل يوم
    up = sb.hist_stats(closes, 3, 0.02, "CALL")               # 3 أيام ≈ +3% ≥ 2%
    dn = sb.hist_stats(closes, 3, 0.02, "PUT")
    assert up["prob"] == 1.0 and dn["prob"] == 0.0
    assert up["n"] == len(closes) - 3


def test_hist_stats_probability_is_bounded_and_mixed_for_noise():
    rng = np.random.default_rng(1)
    closes = list(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300))))
    p = sb.hist_stats(closes, 5, 0.02, "CALL")["prob"]
    assert 0.0 < p < 1.0


# ---------- حالة العقد المقفل ----------
def test_closed_info_for_tp_stop_and_open():
    p = sb.make_pos("AAPL", "CALL", "2026-10-30", 350, 1.9, "2026-10-19T10:47-04:00")
    assert sb.closed_info(p) is None and not sb.is_closed(p)
    p["closed"] = {"kind": "tp", "time": "2026-10-19T11:00-04:00", "pnl": 0.5}
    assert sb.closed_info(p) == ("tp", "2026-10-19T11:00-04:00", 0.5) and sb.is_closed(p)
    q = sb.make_pos("AAPL", "CALL", "2026-10-30", 355, 1.9, "2026-10-19T10:47-04:00")
    q["status"] = "stopped"                                    # سجل قديم بلا حقل closed
    kind, _, pnl = sb.closed_info(q)
    assert kind == "stop" and pnl == -sb.STOP_LOSS


def test_pos_id_is_stable():
    assert sb.pos_id("BAC", "PUT", "2026-10-30", 51.0) == "BAC:PUT:2026-10-30:51"


# ---------- تقييم العقد ----------
def open_pos(**over):
    p = sb.make_pos("WMT", "CALL", "2026-10-30", 120, 1.43, "2026-10-20T10:45-04:00", entry_spot=118.9, entry_spy=768.0)
    p.update(over)
    return p


NOW = datetime(2026, 10, 20, 12, 0, tzinfo=NY)


def kinds(alerts):
    return [k for k, _ in alerts]


def test_evaluate_stop_closes_the_position_once():
    p = open_pos()
    info, new = sb.evaluate(p, NOW, {"val": 0.80, "spr": 0.05, "iv": 0.3}, 117.0, 768.0)     # -44%
    assert kinds(new) == ["stop"] and p["closed"]["kind"] == "stop" and p["status"] == "stopped"
    _, again = sb.evaluate(p, NOW + timedelta(minutes=5), {"val": 0.7, "spr": 0.05, "iv": 0.3}, 116.0, 768.0)
    assert again == []                                         # بعد الإقفال لا تنبيهات جديدة


def test_evaluate_take_profit_closes_with_plus_fifty():
    p = open_pos()
    _, new = sb.evaluate(p, NOW, {"val": 2.2, "spr": 0.05, "iv": 0.3}, 121.5, 768.0)         # +54%
    assert kinds(new) == ["tp"] and p["closed"] == {"kind": "tp", "time": p["closed"]["time"], "pnl": sb.TAKE_PROFIT}


def test_evaluate_no_loss_alert_in_first_half_hour():
    p = open_pos(time="2026-10-20T11:50-04:00")                # عمره 10 دقائق فقط
    _, new = sb.evaluate(p, NOW, {"val": 0.8, "spr": 0.05, "iv": 0.3}, 117.0, 768.0)
    assert new == [] and not sb.is_closed(p)


def test_evaluate_early_warning_before_stop():
    p = open_pos()
    _, new = sb.evaluate(p, NOW, {"val": 1.05, "spr": 0.05, "iv": 0.3}, 118.5, 768.0)        # -27%
    assert "warn" in kinds(new) and not sb.is_closed(p)


def test_evaluate_reports_stock_move_against_you_and_probability():
    p = open_pos()
    info, _ = sb.evaluate(p, NOW, {"val": 1.2, "spr": 0.05, "iv": 0.3}, 117.0, 768.0)
    assert info["mv"] < 0 and 0 < info["prob"] < 1 and info["need"] > 0


# ---------- المراقب: الحقائق والنصيحة ----------
def facts_for(p, spot, val):
    return sw.facts(p, NOW, spot, {"val": val, "iv": 0.3} if val is not None else None)


def test_facts_without_contract_price_leave_pnl_empty():
    f = facts_for(open_pos(), 118.0, None)
    assert f["pnl"] is None and f["val"] is None and f["need"] is not None


def test_facts_pnl_and_need_direction_for_puts():
    p = sb.make_pos("BAC", "PUT", "2026-10-30", 51, 0.81, "2026-10-19T10:47-04:00", entry_spot=51.1)
    f = sw.facts(p, NOW, 50.5, {"val": 1.04, "iv": 0.25})
    assert f["pnl"] == pytest.approx(1.04 / 0.81 - 1) and f["mv"] > 0          # السهم نزل = لصالح البوت
    assert f["need"] > 0                                                         # ما زال فوق نقطة التعادل 50.19


def base_facts(**over):
    f = {"pnl": -0.1, "need": 0.02, "prob": 0.25, "tdays": 8, "mv": -0.003, "val": 1.3, "spot": 118.0}
    f.update(over)
    return f


def test_advice_hold_when_nothing_new():
    adv, reasons = sw.advice(base_facts(), {})
    assert adv == "أبقِ" and reasons


def test_advice_exit_on_strong_news_against():
    adv, _ = sw.advice(base_facts(), {"news": "against", "importance": "high"})
    assert adv == "الأفضل الخروج"


def test_advice_partial_news_never_escalates():
    adv, _ = sw.advice(base_facts(), {"news": "against", "importance": "low"})
    assert adv == "أبقِ"                                       # التأثير الجزئي = أبقِ وراقب دائماً


def test_advice_exit_when_stop_level_passed():
    adv, reasons = sw.advice(base_facts(pnl=-0.45), {})
    assert adv == "الأفضل الخروج" and any("الوقف" in r for r in reasons)


def test_advice_think_about_exit_when_deep_loss():
    adv, _ = sw.advice(base_facts(pnl=-0.33), {})
    assert adv == "فكّر بالخروج"


def test_advice_exit_when_expiring_today_and_far():
    adv, _ = sw.advice(base_facts(tdays=1, need=0.03, prob=0.05), {})
    assert adv == "الأفضل الخروج"


def test_advice_protect_profit_when_up_and_bad_news():
    adv, reasons = sw.advice(base_facts(pnl=0.30), {"news": "against", "importance": "high"})
    assert adv == "فكّر بالخروج" and "احمِ ربحك" in reasons[0]


def test_advice_good_news_supports_holding():
    adv, reasons = sw.advice(base_facts(pnl=0.05), {"news": "with", "importance": "medium"})
    assert adv == "أبقِ" and any("صالح" in r for r in reasons)


# ---------- أخبار السوق: تقليل الضجيج ----------
def test_macro_rows_weak_effect_is_always_low_importance():
    pos = [open_pos()]
    c = {"importance": "high", "effects": [{"k": 0, "impact": "against", "strength": "weak", "why_ar": "x"}]}
    rows = sw.macro_rows(c, pos, {pos[0]["id"]: base_facts()}, NOW)
    assert len(rows) == 1 and rows[0][3][0] == "أبقِ"


def test_macro_rows_ignore_none_and_bad_indexes():
    pos = [open_pos()]
    c = {"importance": "medium", "effects": [{"k": 0, "impact": "none", "strength": "strong", "why_ar": ""},
                                              {"k": 7, "impact": "against", "strength": "strong", "why_ar": ""}]}
    assert sw.macro_rows(c, pos, {}, NOW) == []


def test_macro_rows_strong_and_high_gives_exit_advice():
    pos = [open_pos()]
    c = {"importance": "high", "effects": [{"k": 0, "impact": "against", "strength": "strong", "why_ar": "x"}]}
    rows = sw.macro_rows(c, pos, {pos[0]["id"]: base_facts()}, NOW)
    assert rows[0][3][0] == "الأفضل الخروج"


def test_news_key_ignores_source_suffix_and_punctuation():
    a = sw.news_key("Amazon to Invest €16B in Germany - The Crypto Basic")
    b = sw.news_key("Amazon to invest €16B in Germany! - Yahoo Finance")
    assert a == b and a


# ---------- حماية المفتاح ----------
def test_malformed_api_key_is_refused_without_printing_it(monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", 'sk-ant-abc\n--header "x-api-key: sk-ant-SECRET"')
    assert sw.ask_claude("hi", sw.NEWS_SCHEMA) is None
    out = capsys.readouterr().out
    assert "SECRET" not in out and "sk-ant" not in out


def test_missing_api_key_returns_none_quietly(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert sw.ask_claude("hi", sw.NEWS_SCHEMA) is None
