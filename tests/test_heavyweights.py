"""اختبارات الشركات الثقيلة (H-HEAVY). لا شبكة."""
from datetime import datetime, timezone

import pandas as pd

import heavyweights as hv

NOW = datetime(2026, 11, 9, 3, 30, tzinfo=timezone.utc)


def df():
    return pd.DataFrame([
        {"time_utc": "2026-11-09T03:00:00+00:00", "title": "Nvidia falls about 3% in after-hours", "ticker": "NVDA", "importance": "medium",
         "direction": "down", "topic": "نفيديا تنخفض 3%", "cluster": "a"},
        {"time_utc": "2026-11-09T02:00:00+00:00", "title": "Nvidia's board opens probe into Jensen Huang", "ticker": "", "importance": "medium",
         "direction": "down", "topic": "تحقيق مع رئيس إنفيديا", "cluster": "b"},
        {"time_utc": "2026-11-09T02:30:00+00:00", "title": "Nvidia again", "ticker": "", "importance": "medium", "direction": "down",
         "topic": "مكرر", "cluster": "b"},
        {"time_utc": "2026-11-09T01:00:00+00:00", "title": "Apple launches", "ticker": "", "importance": "low", "direction": "up",
         "topic": "منخفض الأهمية", "cluster": "c"},
        {"time_utc": "2026-11-07T01:00:00+00:00", "title": "Microsoft old", "ticker": "MSFT", "importance": "high", "direction": "up",
         "topic": "قديم", "cluster": "d"},
        {"time_utc": "2026-11-09T01:00:00+00:00", "title": "Oracle deal", "ticker": "ORCL", "importance": "high", "direction": "up",
         "topic": "ليست ثقيلة", "cluster": "e"},
    ])


def test_heavy_news_filters_and_dedupes():
    n = hv.heavy_news(df(), NOW)
    assert list(n) == ["NVDA"] and [t for t, _d, _i in n["NVDA"]] == ["نفيديا تنخفض 3%", "تحقيق مع رئيس إنفيديا"]


def test_impacts_dir_block_caption_alert():
    imp = hv.impacts(hv.heavy_news(df(), NOW), {"NVDA": -0.03})
    assert abs(imp[0]["contrib"] + 0.00225) < 1e-9 and hv.net_dir(imp) == -1
    b = hv.block_text(imp)
    assert "NVDA (وزن ≈7.5%)" in b and "-3.0%" in b and "-0.22%" in b and "متوسطة" in b and "تحقيق مع رئيس إنفيديا" in b
    assert "⚠️ عكس التوصية" in hv.caption_line(imp, "CALL") and "✅ مع التوصية" in hv.caption_line(imp, "PUT")
    assert [x["ticker"] for x in hv.alerts_due(imp, set(), "2026-11-09")] == ["NVDA"]
    assert hv.alerts_due(imp, {"2026-11-09:NVDA"}, "2026-11-09") == []
    no_move = hv.impacts(hv.heavy_news(df(), NOW), {})
    assert hv.net_dir(no_move) == -1 and "غير متاحة" in hv.block_text(no_move) and hv.alerts_due(no_move, set(), "x") == []
    assert hv.block_text([]) == "" and hv.caption_line([]) == ""
