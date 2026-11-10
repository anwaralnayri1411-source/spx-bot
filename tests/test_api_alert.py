"""تنبيه فشل طلبات Claude (analyst.api_failed)."""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import analyst as an


class E(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.message, self.status_code = msg, code


NOW = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)


def test_kind():
    assert an.api_kind(E("Your credit balance is too low to access the Anthropic API.")) == "billing"
    assert an.api_kind(E("invalid x-api-key", 401)) == "auth"
    assert an.api_kind(E("Overloaded", 529)) == "other"


def test_billing_alerts_once_per_day(tmp_path):
    p, sent = str(tmp_path / "a.json"), []
    e = E("Your credit balance is too low")
    assert an.api_failed(e, "falcon", NOW, p, sent.append) is True
    assert an.api_failed(e, "decide", NOW, p, sent.append) is False
    assert len(sent) == 1 and "الرصيد" in sent[0] and "falcon 10:00" in sent[0]
    assert an.api_failed(e, "falcon", datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc), p, sent.append) is True


def test_other_needs_three(tmp_path):
    p, sent = str(tmp_path / "a.json"), []
    e = E("Overloaded", 529)
    assert [an.api_failed(e, "x", NOW, p, sent.append) for _ in range(4)] == [False, False, True, False]
    assert len(sent) == 1


def test_no_keys_no_side_effects(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    p = str(tmp_path / "a.json")
    assert an.api_failed(E("credit balance"), "x", NOW, p) is False and not os.path.exists(p)
