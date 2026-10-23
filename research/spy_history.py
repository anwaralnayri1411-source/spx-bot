#!/usr/bin/env python3
"""تنزيل شموع SPY (5 دقائق) من Alpaca لسنوات، للدراسات الطويلة (مستويات الدعم والمقاومة، اختراق أول 30 دقيقة...). قراءة فقط.

لماذا: ياهو يعطي شموع 5 دقائق لـ60 يوماً فقط، فلا تُختبر أي قاعدة لحظية على عينة تكفي. Alpaca يعطي تاريخاً أطول: نجرّب خلاصة SIP (شاملة، متأخرة 15 دقيقة في الباقة المجانية)
ثم نرجع إلى IEX (أضيق: صفقات IEX فقط) إن رُفضت، ونسجّل في ملف meta أي خلاصة استُعملت لأن الفرق يؤثر على القمم والقيعان.
يحتاج ALPACA_KEY/ALPACA_SECRET (يعمل على الخادم). قابل لإعادة التشغيل: يكمل من آخر شمعة محفوظة ولا يعدّل ما سبق.
الملف يُحفظ خارج شجرة git (المهام تنظّف الشجرة قبل كل تشغيل): SPXBOT_DATA (الافتراضي /opt/spxbot/data)/spy_5m.csv.gz
تشغيل: python research/spy_history.py [--from 2019-01-01] [--check]   (--check يطبع ملخص الملف الموجود ويتحقق من سلامته)
"""
import csv
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

DATA_DIR = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
FILE = os.path.join(DATA_DIR, "spy_5m.csv.gz")
META = os.path.join(DATA_DIR, "spy_5m.meta.json")
URL = "https://data.alpaca.markets/v2/stocks/SPY/bars"
COLS = ["t", "o", "h", "l", "c", "v"]
START_DEFAULT = "2019-01-01"
LAG_MIN = 20                      # SIP المجاني متأخر 15 دقيقة: ننتهي قبل الآن بـ20


# ============================ دوال نقية ============================
def parse_bars(bars):
    """قائمة شموع Alpaca (t,o,h,l,c,v) → صفوف [t,o,h,l,c,v] مرتبة بلا تكرار وبلا أسعار غير منطقية."""
    out = {}
    for b in bars or []:
        try:
            o, h, l, c = (float(b[k]) for k in ("o", "h", "l", "c"))
            if min(o, h, l, c) <= 0 or h < l or h < max(o, c) - 1e-9 or l > min(o, c) + 1e-9:
                continue
            out[b["t"]] = [b["t"], o, h, l, c, float(b.get("v") or 0)]
        except (KeyError, TypeError, ValueError):
            continue
    return [out[k] for k in sorted(out)]


def read_last(path=None):
    """آخر وقت محفوظ (نص ISO) أو None."""
    path = path or FILE
    if not os.path.exists(path):
        return None
    last = None
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        for row in csv.reader(f):
            last = row
    return None if last is None or last[0] == "t" else last[0]


def append_rows(rows, path=None):
    """يضيف الصفوف الأحدث من آخر وقت محفوظ فقط (لا تكرار ولا تعديل). يرجع عدد المضاف."""
    path = path or FILE
    last = read_last(path)
    new = [r for r in rows if last is None or r[0] > last]
    if not new:
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fresh = not os.path.exists(path)
    with gzip.open(path, "at", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if fresh:
            w.writerow(COLS)
        w.writerows(new)
    return len(new)


def check(path=None):
    """ملخص سلامة الملف: عدد الشموع والأيام والمدى ونسبة الشموع غير المرتبة والأيام ناقصة الشموع (الجلسة العادية 78 شمعة)."""
    path = path or FILE
    if not os.path.exists(path):
        return None
    import pandas as pd
    d = pd.read_csv(path)
    d["t"] = pd.to_datetime(d["t"], utc=True).dt.tz_convert("America/New_York")
    reg = d[(d["t"].dt.hour * 60 + d["t"].dt.minute >= 570) & (d["t"].dt.hour * 60 + d["t"].dt.minute < 960)]
    per_day = reg.groupby(reg["t"].dt.date).size()
    return {"rows": len(d), "regular_rows": len(reg), "days": int(per_day.size), "first": str(d["t"].min()), "last": str(d["t"].max()),
            "unsorted": int((d["t"].diff().dt.total_seconds() < 0).sum()), "short_days": int((per_day < 70).sum()),
            "min_close": float(d["c"].min()), "max_close": float(d["c"].max())}


# ============================ الشبكة ============================
def _get(params):
    req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(params),
                                 headers={"APCA-API-KEY-ID": os.getenv("ALPACA_KEY", "").strip(),
                                          "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET", "").strip()})
    return json.loads(urllib.request.urlopen(req, timeout=40).read())


def fetch_range(start, end, feed, get=_get):
    """كل شموع [start, end) بصفحاتها. get قابلة للاستبدال في الاختبار. يرفع HTTPError عند الرفض."""
    rows, page = [], None
    while True:
        q = {"timeframe": "5Min", "start": start, "end": end, "adjustment": "raw", "feed": feed, "limit": 10000, "sort": "asc"}
        if page:
            q["page_token"] = page
        j = get(q)
        rows += parse_bars(j.get("bars"))
        page = j.get("next_page_token")
        if not page:
            return rows
        time.sleep(0.3)


def choose_feed(get=_get, now=None):
    """يجرّب SIP على نافذة قصيرة قديمة؛ إن رُفض (403) يرجع IEX."""
    t = (now or datetime.now(timezone.utc)) - timedelta(days=10)
    try:
        get({"timeframe": "5Min", "start": (t - timedelta(days=1)).isoformat(), "end": t.isoformat(), "feed": "sip", "limit": 5})
        return "sip"
    except urllib.error.HTTPError as e:
        print("SIP مرفوض (HTTP", e.code, ")؛ نستعمل IEX")
        return "iex"


def main(argv):
    if "--check" in argv:
        print(json.dumps(check(), ensure_ascii=False, indent=1))
        return 0
    if not (os.getenv("ALPACA_KEY") and os.getenv("ALPACA_SECRET")):
        print("يحتاج ALPACA_KEY وALPACA_SECRET (يعمل على الخادم).")
        return 0
    start = argv[argv.index("--from") + 1] if "--from" in argv else START_DEFAULT
    now = datetime.now(timezone.utc)
    end = now - timedelta(minutes=LAG_MIN)
    last = read_last()
    cur = datetime.fromisoformat(last.replace("Z", "+00:00")) + timedelta(minutes=5) if last else datetime.fromisoformat(start + "T00:00:00+00:00")
    try:
        feed = json.load(open(META, encoding="utf-8")).get("feed") if os.path.exists(META) else None
    except Exception:  # noqa: BLE001
        feed = None
    try:
        feed = feed or choose_feed()
    except Exception as e:  # noqa: BLE001
        print("تعذر اختيار الخلاصة:", getattr(e, "code", type(e).__name__))
        return 1
    total = 0
    while cur < end:
        stop = min(cur + timedelta(days=90), end)
        try:
            rows = fetch_range(cur.isoformat().replace("+00:00", "Z"), stop.isoformat().replace("+00:00", "Z"), feed)
        except Exception as e:  # noqa: BLE001
            print("فشل الجلب:", cur.date(), getattr(e, "code", type(e).__name__))     # رمز HTTP فقط لا نص الخطأ
            return 1
        total += append_rows(rows)
        cur = stop
        print(f"{cur.date()}: +{len(rows)} شمعة (المجموع المضاف {total})")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(META, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"feed": feed, "updated": now.isoformat(timespec="minutes"), "from": start}, f)
    print(json.dumps(check(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
