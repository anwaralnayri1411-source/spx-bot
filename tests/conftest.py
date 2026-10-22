"""إعداد الاختبارات: المستودع على مسار الاستيراد، وبلا شبكة (الاختبارات لا تستدعي ياهو ولا تيليجرام)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "deploy")):
    if p not in sys.path:
        sys.path.insert(0, p)
for k in ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_EXTRA_CHAT_IDS", "ALPACA_KEY", "ALPACA_SECRET", "ANTHROPIC_API_KEY"):
    os.environ.pop(k, None)
