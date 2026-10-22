#!/usr/bin/env python3
"""حارس صحة البوتات على الخادم: يرسل لك تيليجرام فور أن يتوقف بوت مهم بصمت أو يفشل تشغيله.

المشكلة: لو مات بوت SPX صباحاً فلن تعرف، وستظن أن «لا توصيات اليوم» لأن السوق هادئ.
ماذا يفحص (أثناء الجلسة فقط: 9:50–15:50 نيويورك في أيام التداول):
  1) تشغيل دوري توقف: آخر تشغيل ناجح لـ bot / stocks / commands أقدم من الحد المسموح (أطول من فاصل الجدولة بهامش).
  2) فشل متكرر: آخر تشغيلين لمهمة مهمة انتهيا برمز خطأ (rc != 0)، مثل فشل git push أو خطأ في الكود.
  3) مهمة طويلة متوقفة: stocks_watcher ليست قيد التشغيل لدورتين فحص متتاليتين (المؤقت يعيد تشغيلها كل 5 دقائق).
  4) القرص ممتلئ (> 85%).
لا يكرر نفس التنبيه: رسالة عند بدء المشكلة، ورسالة «عاد للعمل» عند زوالها.
المصدر: /var/log/spxbot/runs.csv (يكتبه deploy/run_job.sh: بداية,نهاية,مهمة,وضع,رمز) وحالة systemd.
يعمل فقط في الوضع live (SPXBOT_MODE). الرسائل إلى TELEGRAM_CHAT_ID وحده (لا للنسخ الإضافية).
لا يطبع المفاتيح أبداً. تشغيل: python deploy/bot_watchdog.py [--dry-run]
"""
import csv
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
RUNS = os.getenv("SPXBOT_RUNS_CSV", "/var/log/spxbot/runs.csv")
STATE = os.getenv("SPXBOT_WATCHDOG_STATE", "/var/log/spxbot/watchdog_state.json")
WINDOW = ((9, 50), (15, 50))            # توقيت نيويورك
RUN_CHECKS = {                          # مهمة: أقصى صمت مسموح بالدقائق (فاصل الجدولة + هامش لتأخر التشغيل نفسه)
    "bot": 50,                          # كل 30 دقيقة
    "stocks": 50,                       # كل 30 دقيقة
    "commands": 30,                     # كل 15 دقيقة
}
SERVICE_CHECKS = ("stocks_watcher",)    # مهام طويلة يجب أن تكون قيد التشغيل طوال الجلسة
FAIL_STREAK = 2                         # عدد التشغيلات الفاشلة المتتالية قبل التنبيه
DISK_LIMIT = 85                         # نسبة %
HOLIDAYS = {"2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
            "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"}
EARLY_CLOSE = {"2026-11-27", "2026-12-24"}


# ============================ المنطق (دوال نقية قابلة للاختبار) ============================
def in_session(now):
    """هل نحن داخل نافذة المراقبة؟ now بتوقيت نيويورك."""
    d = now.date().isoformat()
    if now.weekday() >= 5 or d in HOLIDAYS:
        return False
    end = (12, 50) if d in EARLY_CLOSE else WINDOW[1]
    return WINDOW[0] <= (now.hour, now.minute) <= end


def parse_runs(rows):
    """سطور runs.csv ← [{start, end, job, mode, rc}] بوقت UTC؛ يتجاوز أي سطر تالف بدل أن ينهار."""
    out = []
    for r in rows:
        try:
            s, e, job, mode, rc = r[0], r[1], r[2], r[3], int(r[4])
            out.append({"start": datetime.fromisoformat(s.replace("Z", "+00:00")),
                        "end": datetime.fromisoformat(e.replace("Z", "+00:00")), "job": job, "mode": mode, "rc": rc})
        except (IndexError, ValueError):
            continue
    return out


def session_start(now):
    return now.replace(hour=WINDOW[0][0], minute=WINDOW[0][1], second=0, microsecond=0)


def find_problems(now, runs, services, disk_pct):
    """يرجع {مفتاح المشكلة: نص عربي} للمشاكل القائمة الآن. now بتوقيت نيويورك، services: {اسم: حالة systemd}."""
    problems = {}
    if disk_pct is not None and disk_pct > DISK_LIMIT:
        problems["disk"] = f"💾 القرص ممتلئ {disk_pct:.0f}% (الحد {DISK_LIMIT}%)."
    if not in_session(now):
        return problems
    now_utc = now.astimezone(timezone.utc)
    start_utc = session_start(now).astimezone(timezone.utc)
    for job, max_gap in RUN_CHECKS.items():
        mine = sorted((r for r in runs if r["job"] == job and r["mode"] == "live"), key=lambda r: r["end"])
        ok = [r for r in mine if r["rc"] == 0]
        last_ok = max(ok[-1]["end"] if ok else start_utc, start_utc)          # بداية الجلسة تُعدّ آخر نجاح مفترض
        gap = (now_utc - last_ok).total_seconds() / 60
        if gap > max_gap:
            last = f"آخر نجاح {ok[-1]['end'].astimezone(NY):%H:%M} نيويورك" if ok else "لا تشغيل ناجح اليوم"
            problems[f"stale:{job}"] = (f"⛔ {job}: لم يعمل منذ {gap:.0f} دقيقة (الحد {max_gap}). {last}. "
                                        f"السجل: /var/log/spxbot/{job}.log")
        tail = mine[-FAIL_STREAK:]
        if len(tail) == FAIL_STREAK and all(r["rc"] != 0 for r in tail):
            problems[f"fail:{job}"] = (f"❌ {job}: آخر {FAIL_STREAK} تشغيلات فشلت "
                                       f"(رمز {tail[-1]['rc']} عند {tail[-1]['end'].astimezone(NY):%H:%M} نيويورك).")
    for svc in SERVICE_CHECKS:
        if services.get(svc) not in ("active", "activating"):
            problems[f"down:{svc}"] = f"⛔ {svc}: غير قيد التشغيل (الحالة: {services.get(svc) or 'غير معروفة'})."
    return problems


def update_state(state, problems, now):
    """يقارن بالحالة المحفوظة: يرجع (رسائل التنبيه، الحالة الجديدة). المشكلة تُنبَّه مرة، والخدمة تحتاج فحصين متتاليين."""
    pending = dict(state.get("pending", {}))
    active = dict(state.get("active", {}))
    msgs = []
    for key, text in problems.items():
        if key.startswith("down:"):                      # فحصان متتاليان: المؤقت قد يعيد التشغيل خلال 5 دقائق
            pending[key] = pending.get(key, 0) + 1
            if pending[key] < 2:
                continue
        if key not in active:
            active[key] = now.isoformat(timespec="minutes")
            msgs.append(text)
    for key in list(pending):
        if key not in problems:
            del pending[key]
    for key in list(active):
        if key not in problems:
            del active[key]
            msgs.append(f"✅ عاد للعمل: {key.split(':', 1)[-1]} (منذ {now:%H:%M} نيويورك).")
    return msgs, {"pending": pending, "active": active}


# ============================ التنفيذ على الخادم ============================
def read_runs(path=RUNS, tail=400):
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return parse_runs(list(csv.reader(f))[-tail:])
    except OSError:
        return []


def service_state(name):
    try:
        r = subprocess.run(["systemctl", "is-active", f"spxbot@{name}.service"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception:
        return ""


def disk_used_pct(path="/"):
    try:
        u = shutil.disk_usage(path)
        return u.used / u.total * 100
    except OSError:
        return None


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(s):
    try:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False)
    except OSError as e:
        print("watchdog: تعذر حفظ الحالة:", type(e).__name__)


def send(text):
    """إلى TELEGRAM_CHAT_ID وحده. لا يطبع المفتاح ولا نص الخطأ (قد يحوي الترويسات)."""
    import requests
    tok, chat = os.getenv("TELEGRAM_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not tok or not chat:
        print("watchdog: لا مفاتيح تيليجرام، لن يُرسل:", text)
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          data={"chat_id": chat, "text": "🐕 <b>حارس البوتات</b>\n" + text, "parse_mode": "HTML"}, timeout=20)
        return r.ok
    except Exception as e:  # noqa: BLE001
        print("watchdog: فشل الإرسال:", type(e).__name__)
        return False


def main(argv):
    dry = "--dry-run" in argv
    mode = os.getenv("SPXBOT_MODE", "shadow")
    if mode != "live" and not dry:
        return 0                                          # في الظل لا رسائل
    now = datetime.now(NY)
    services = {s: service_state(s) for s in SERVICE_CHECKS} if in_session(now) else {}
    problems = find_problems(now, read_runs(), services, disk_used_pct())
    msgs, new = update_state(load_state(), problems, now)
    sent_all = True
    for m in msgs:
        print(m)
        if not dry:
            sent_all = send(m) and sent_all
    if not dry and sent_all:                              # فشل الإرسال: لا نحفظ الحالة، فيُعاد التنبيه في الفحص القادم
        save_state(new)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
