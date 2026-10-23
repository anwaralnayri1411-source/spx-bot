#!/usr/bin/env python3
"""فحص من خارج الخادم (يعمل على GitHub Actions): هل الخادم حيّ؟ (منذ 23/10)

حارس الخادم (deploy/bot_watchdog.py) لا يستطيع التنبيه إن توقف الخادم كله. هنا نقرأ آخر التزام كتبه الخادم في المستودع
(المؤلف spx-bot-server أو bot؛ مهام الخادم تلتزم كل بضع دقائق أثناء الجلسة). أثناء الجلسة (10:00–15:45 نيويورك، أيام التداول)
إن كان أقدم من 60 دقيقة نرسل تنبيهاً إلى TELEGRAM_CHAT_ID وحده. بلا حالة محفوظة: التنبيه فقط بين 60 و120 دقيقة من الصمت،
فيصل مرة أو مرتين لكل انقطاع (GitHub يشغّل كل ~30 دقيقة وقد يتأخر). لا يغيّر شيئاً ولا يلتزم. مكتبة بايثون القياسية فقط.
تشغيل: python deploy/server_alive_check.py [--dry-run]
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SERVER_AUTHORS = {"spx-bot-server", "bot"}
STALE_MIN, QUIET_AFTER_MIN = 60, 120
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def holidays():
    """عطل السوق من options_bot.HOLIDAYS (قراءة نصية، بلا استيراد البوت وتبعياته)."""
    txt = open(os.path.join(ROOT, "options_bot.py"), encoding="utf-8").read()
    m = re.search(r"^HOLIDAYS = \{(.*?)\}", txt, re.S | re.M)
    return set(re.findall(r'"(\d{4}-\d{2}-\d{2})"', m.group(1))) if m else set()


def in_session(n):
    return n.weekday() < 5 and n.date().isoformat() not in holidays() and (10, 0) <= (n.hour, n.minute) <= (15, 45)


def last_server_commit():
    repo = os.getenv("GITHUB_REPOSITORY", "anwaralnayri1411-source/spx-bot")
    h = {"Accept": "application/vnd.github+json", "User-Agent": "server-alive-check"}
    if os.getenv("GITHUB_TOKEN"):
        h["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/commits?per_page=100", headers=h)
    for c in json.loads(urllib.request.urlopen(req, timeout=20).read()):
        a = (c.get("commit") or {}).get("author") or {}
        if a.get("name") in SERVER_AUTHORS:
            return datetime.fromisoformat(a["date"].replace("Z", "+00:00")), (c["commit"]["message"] or "").splitlines()[0]
    return None, None


def send(text):
    tok, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        print("لا مفاتيح تيليجرام:", text)
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=20)


def main(dry=False):
    n = datetime.now(NY)
    if not in_session(n):
        print("خارج الجلسة، لا فحص.")
        return
    t, msg = last_server_commit()
    age = None if t is None else (datetime.now(timezone.utc) - t).total_seconds() / 60
    print(f"آخر التزام من الخادم: {t} ({msg}) منذ {age if age is None else round(age)} دقيقة")
    if age is None or STALE_MIN <= age < QUIET_AFTER_MIN:
        text = ("⚠️ الخادم صامت: لا التزام من مهام البوت منذ "
                + ("أكثر من 100 التزام" if age is None else f"{round(age)} دقيقة")
                + " أثناء الجلسة. قد يكون الخادم متوقفاً. تحقق منه (ssh) أو من لوحة Hetzner.")
        print(text) if dry else send(text)


if __name__ == "__main__":
    main(dry="--dry-run" in sys.argv)
