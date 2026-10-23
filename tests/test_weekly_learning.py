"""اختبارات الملخص الأسبوعي: الجمل العربية، الأقسام ببيانات مصطنعة، وحدود رسالة تيليجرام."""
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import weekly_learning as wl  # noqa: E402


def test_count_words_follow_arabic_agreement():
    assert wl.cnt(1, "يوم", "أيام", "يوماً") == "1 يوم"
    assert wl.cnt(4, "يوم", "أيام", "يوماً") == "4 أيام"
    assert wl.cnt(10, "يوم", "أيام", "يوماً") == "10 أيام"
    assert wl.cnt(11, "يوم", "أيام", "يوماً") == "11 يوماً"


def test_week_starts_on_monday():
    assert wl.week_start(date(2026, 10, 23)) == date(2026, 10, 19)
    assert wl.week_start(date(2026, 10, 19)) == date(2026, 10, 19)


def tail_days(n=20, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        rng_pct = float(rng.uniform(0.3, 1.8))
        rate = min(0.9, rng_pct / 2.5)                          # القفزات تتبع الحركة، لا يوم الأسبوع
        rows.append({"date": f"2026-09-{i + 1:02d}", "dow": i % 5, "opex_month": int(i % 20 == 0), "range_pct": rng_pct,
                     "entries": 50, "ge100": int(50 * rate), "mean_close": -0.5})
    return pd.DataFrame(rows)


def test_tail_section_reports_weekday_rates_and_range_correlation():
    lines = wl.sec_tail(tail_days(), pd.DataFrame())
    text = "\n".join(lines)
    assert "الجمعة" in text and "الارتباط" in text and "الربع الأعلى" in text
    assert "قوي" in text                                         # البيانات المصطنعة مرتبطة بالحركة بقوة


def test_tail_section_refuses_to_conclude_on_tiny_samples():
    assert "لا بيانات كافية" in wl.sec_tail(tail_days(3), pd.DataFrame())[0]


def test_walls_section_counts_held_and_touched_days():
    snaps = pd.DataFrame({"date": ["d1", "d2", "d3"], "time": ["10:00"] * 3, "ticker": ["^SPX"] * 3, "dte": [0] * 3,
                          "call_wall": [7800.0, 7800.0, 7800.0], "put_wall": [7600.0, 7600.0, 7600.0]})
    out = pd.DataFrame({"date": ["d1", "d2", "d3"], "ticker": ["^SPX"] * 3, "high": [7700.0, 7803.0, 7798.0],
                        "low": [7650.0, 7620.0, 7590.0]})
    text = "\n".join(wl.sec_walls(snaps, out))
    assert "بيانات 3 أيام" in text
    assert "لم يُكسر في 2 من 3" in text                          # الكول: كُسر في d2 فقط، والبوت: كُسر في d3 فقط


def test_walls_section_needs_at_least_two_days():
    assert "لا يكفي" in wl.sec_walls(pd.DataFrame({"date": ["d1"], "time": ["10:00"], "ticker": ["^SPX"], "dte": [0],
                                                    "call_wall": [1.0], "put_wall": [1.0]}),
                                     pd.DataFrame({"date": ["d1"], "ticker": ["^SPX"], "high": [1.0], "low": [1.0]}))[0]


MD = """# x
| النوع | العدد | صعود | متوسط الكاملة | متوسط |الحركة| |
|---|---|---|---|---|
| تخفيض محلل | 5 | 0% (0/5) | -3.03% | 3.03% |
| صين | 2 | 50% | +0.1% | 1% |
| ترقية محلل | 11 | 27% (3/11) | -1.60% | 1.96% |
"""


def test_news_section_keeps_only_types_with_five_or_more_cases():
    text = "\n".join(wl.sec_news(MD))
    assert "تخفيض محلل (5 حالات)" in text and "ترقية محلل (11 حالة)" in text and "صين" not in text


def test_week_section_counts_closed_contracts_and_net_dollars():
    pos = [{"entry": 1.0, "closed": {"kind": "tp", "time": "2026-10-20T10:00-04:00", "pnl": 0.5}},
           {"entry": 2.0, "closed": {"kind": "stop", "time": "2026-10-21T10:00-04:00", "pnl": -0.4}},
           {"entry": 1.0, "closed": {"kind": "tp", "time": "2026-10-01T10:00-04:00", "pnl": 0.5}},     # قبل هذا الأسبوع
           {"entry": 1.0}]
    z = pd.DataFrame({"date": ["2026-10-20", "2026-10-05"], "result": ["ربح", "خسارة"]})
    text = "\n".join(wl.sec_week(z, pos, pd.DataFrame(), date(2026, 10, 19)))
    assert "أُغلق 2 عقود هذا الأسبوع: 1 هدف و1 وقف" in text
    assert "-30$" in text                                        # الصافي: +50 − 80
    assert "1 توصية هذا الأسبوع" in text and "1 منها رابحة" in text


def test_build_returns_markdown_and_a_telegram_message_within_limits():
    data = {"z": pd.DataFrame(columns=["date", "result"]), "positions": [], "stocks_log": pd.DataFrame(),
            "tail_days": tail_days(), "tail_events": pd.DataFrame(), "snaps": pd.DataFrame(), "outcomes": pd.DataFrame(),
            "momentum": pd.DataFrame(), "news_md": MD}
    md, tg = wl.build(today=date(2026, 10, 23), data=data)
    assert md.startswith("# ما تعلمناه") and "تعليمي وليس توصية" in md
    assert len(tg) <= 3900 and "عينات صغيرة" in tg and "<b>" in tg
