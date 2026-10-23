"""سطر السعر في توصية الشركات: مصدره ووقته وفرق الشراء/البيع، ونصيحة الأمر المحدد عند اتساع الفرق."""
from datetime import datetime

import options_bot as ob
import stocks_bot as sb

NOW = datetime(2026, 10, 23, 13, 12, tzinfo=ob.NY)          # 20:12 بتوقيت الرياض


def contract(**kw):
    k = {"strike": 325.0, "exp": "2026-10-30", "price": 1.93, "cost": 193.0, "need": 0.024, "score": 6.0, "flow": 0.0, "iv": 0.28, "dte": 7,
         "delta": 0.26, "prob": 0.21, "be": 323.07, "price_src": "alpaca", "bid": 1.72}
    k.update(kw)
    return k


def test_live_quote_shows_ask_bid_spread_and_riyadh_time():
    line = sb.quote_line(contract(), NOW)
    assert "📡 لحظي 20:12 الرياض" in line and "طلب <b>1.93</b>" in line and "بيع <b>1.72</b>" in line and "فرق 12%" in line


def test_wide_spread_adds_the_limit_order_advice_with_the_mid_price():
    line = sb.quote_line(contract(), NOW)
    assert "أمراً محدداً" in line and "1.82" in line
    tight = sb.quote_line(contract(bid=1.90), NOW)
    assert "أمراً محدداً" not in tight and "فرق 2%" in tight


def test_missing_live_quote_says_the_price_is_delayed_yahoo():
    for k in (contract(price_src="yahoo_fallback"), contract(price_src=None, bid=None), contract(bid=None)):
        line = sb.quote_line(k, NOW)
        assert "ياهو متأخر ~15د" in line and "📡" not in line


def test_the_caption_contains_the_quote_line():
    i = {"side": "PUT", "ticker": "JPM", "strength": 6.5, "dates": {"earn": None}, "S": 331.09, "contracts": [contract()]}
    cap = sb.idea_caption(i)
    assert "📡 لحظي" in cap and "بسعر حتى <b>1.93</b>" in cap and "تعليمي وليست توصية" in cap
