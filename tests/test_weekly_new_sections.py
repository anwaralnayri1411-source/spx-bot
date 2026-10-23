"""أقسام الملخص الأسبوعي الجديدة: ظل الحجب (7) وظل العقد والخروج ودراسة وقف الشركات (8). بيانات مصطنعة، بلا شبكة."""
import os
import sys
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import weekly_learning as wl  # noqa: E402


def blocked_log(n, tag, pnl, project="spx"):
    return pd.DataFrame([{"project": project, "tag": tag, "out": "final", "out_pnl": pnl, "entry_time": f"2026-10-{1 + i % 20:02d}T10:00-04:00"}
                         for i in range(n)])


def test_blocked_section_reports_per_reason_and_withholds_verdict_on_small_samples():
    lines = wl.sec_blocked(blocked_log(5, "news_risk", -0.4))
    text = "\n".join(lines)
    assert "مخاطر أخبار مرتفعة" in text and "لا حكم بعد (5 من 30)" in text and lines[0].startswith("⚖️")


def test_blocked_section_gives_a_verdict_on_a_large_consistent_sample():
    text = "\n".join(wl.sec_blocked(blocked_log(40, "late_entry", -0.3)))
    assert "الحجب أصاب (المحجوب كان سيخسر)" in text and "الشركات" not in text


def test_blocked_section_handles_empty_and_no_data_rows():
    assert "لا حالات مغلقة بعد" in wl.sec_blocked(pd.DataFrame())[0]
    bad = pd.DataFrame([{"project": "spx", "tag": "news_risk", "out": "no_data", "out_pnl": None, "entry_time": "2026-10-01T10:00-04:00"}])
    assert "لا حالات مغلقة بعد" in wl.sec_blocked(bad)[0]


def test_exit_section_compares_the_two_contracts_under_both_rules():
    e = pd.DataFrame([{"date": "2026-10-22", "pos_id": p, "variant": v, "r5040_pnl": a, "t60_pnl": b}
                      for p, bot, atm in (("a", (-0.4, -0.3), (0.1, 0.2)), ("b", (0.5, 0.1), (0.3, 0.2)))
                      for v, (a, b) in (("bot", bot), ("atm1", atm))])
    text = "\n".join(wl.sec_exit(e, []))
    assert "2 توصيات" in text and "عقد البوت -5%" not in text
    assert "عقد البوت" in text and "ATM لغد" in text and "لا قرار قبل 20 توصية" in text


def test_exit_section_without_data_says_so_and_keeps_stocks_lines():
    L = wl.sec_exit(pd.DataFrame(), ["⚖️ سطر الشركات"])
    assert "لا نتائج بعد" in L[0] and L[1] == "⚖️ سطر الشركات"


def test_build_includes_new_sections_and_telegram_stays_short():
    days = pd.DataFrame({"date": ["2026-10-20"], "dow": [1], "opex_month": [0], "range_pct": [1.0], "entries": [10], "ge100": [1],
                         "mean_close": [-0.5], "vix_close": [18.0]})
    data = {"z": pd.DataFrame(columns=["date", "result"]), "positions": [], "stocks_log": pd.DataFrame(), "tail_days": days,
            "tail_events": pd.DataFrame(), "snaps": pd.DataFrame(), "outcomes": pd.DataFrame(), "momentum": pd.DataFrame(), "news_md": "",
            "blocked": blocked_log(5, "news_risk", -0.4), "exit_log": pd.DataFrame(), "stocks_study": ["⚖️ سطر الشركات"]}
    md, tg = wl.build(today=date(2026, 10, 24), data=data)
    assert "## 7) الحجب" in md and "## 8) العقد والخروج" in md
    assert "<b>7) الحجب" in tg and "سطر الشركات" in tg and len(tg) <= 3900


def test_build_still_works_when_new_inputs_are_missing():
    data = {"z": pd.DataFrame(columns=["date", "result"]), "positions": [], "stocks_log": pd.DataFrame(), "tail_days": pd.DataFrame(),
            "tail_events": pd.DataFrame(), "snaps": pd.DataFrame(), "outcomes": pd.DataFrame(), "momentum": pd.DataFrame(), "news_md": ""}
    md, tg = wl.build(today=date(2026, 10, 24), data=data)
    assert "## 7) الحجب" in md and len(tg) <= 3900
