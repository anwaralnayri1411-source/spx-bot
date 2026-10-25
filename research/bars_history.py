#!/usr/bin/env python3
"""تنزيل شموع 5 دقائق من Alpaca لعدة رموز (صناديق QQQ وIWM وDIA وشركات قائمة البوت) لدراسة التكرار (قراءة فقط؛ H-REPL).

يعيد استعمال أدوات `spy_history.py` (الجلب بصفحاته، الإضافة الآمنة بلا تكرار، اختيار الخلاصة SIP ثم IEX) لكل رمز على حدة، وقابل لإعادة التشغيل (يكمل من آخر شمعة محفوظة).
الملفات خارج شجرة git: SPXBOT_DATA (الافتراضي /opt/spxbot/data)/bars_5m/<الرمز>.csv.gz بالتنسيق نفسه (t,o,h,l,c,v).
فشل رمز واحد لا يوقف الباقي. يحتاج ALPACA_KEY/ALPACA_SECRET. تشغيل: python research/bars_history.py [--from 2019-01-01] [--only QQQ,AAPL]
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import spy_history as sh  # noqa: E402

ETFS = ("QQQ", "IWM", "DIA")
BARS_DIR = os.path.join(sh.DATA_DIR, "bars_5m")
CHUNK_DAYS = 90
PAUSE = 0.35


def path_for(sym, base=None):
    return os.path.join(base or BARS_DIR, f"{sym}.csv.gz")


def symbols():
    import stocks_bot as sb
    return list(ETFS) + [t for t in sb.WATCHLIST if t not in ETFS]


def make_get(sym):
    url = f"https://data.alpaca.markets/v2/stocks/{sym}/bars"

    def get(params):
        req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                                     headers={"APCA-API-KEY-ID": os.getenv("ALPACA_KEY", "").strip(), "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET", "").strip()})
        return json.loads(urllib.request.urlopen(req, timeout=40).read())
    return get


def download_symbol(sym, feed, start, end, get=None, path=None):
    """يكمل ملف الرمز من آخر شمعة حتى end. يرجع عدد الشموع المضافة. يرفع عند فشل الجلب (يلتقطه المستدعي)."""
    get = get or make_get(sym)
    path = path or path_for(sym)
    last = sh.read_last(path)
    cur = datetime.fromisoformat(last.replace("Z", "+00:00")) + timedelta(minutes=5) if last else datetime.fromisoformat(start + "T00:00:00+00:00")
    total = 0
    while cur < end:
        stop = min(cur + timedelta(days=CHUNK_DAYS), end)
        rows = sh.fetch_range(cur.isoformat().replace("+00:00", "Z"), stop.isoformat().replace("+00:00", "Z"), feed, get=get)
        total += sh.append_rows(rows, path)
        cur = stop
        time.sleep(PAUSE)
    return total


def main(argv):
    if not (os.getenv("ALPACA_KEY") and os.getenv("ALPACA_SECRET")):
        print("يحتاج ALPACA_KEY وALPACA_SECRET (يعمل على الخادم).")
        return 0
    start = argv[argv.index("--from") + 1] if "--from" in argv else "2019-01-01"
    syms = argv[argv.index("--only") + 1].split(",") if "--only" in argv else symbols()
    end = datetime.now(timezone.utc) - timedelta(minutes=sh.LAG_MIN)
    try:
        feed = sh.choose_feed()
    except Exception as e:  # noqa: BLE001
        print("تعذر اختيار الخلاصة:", getattr(e, "code", type(e).__name__))
        return 1
    failed = []
    for sym in syms:
        try:
            n = download_symbol(sym, feed, start, end)
            print(f"{sym}: +{n} شمعة")
        except Exception as e:  # noqa: BLE001
            failed.append(sym)
            print(f"{sym}: فشل ({getattr(e, 'code', type(e).__name__)})")
    print(f"الخلاصة {feed}: {len(syms) - len(failed)} رمزاً سليماً، فشل {len(failed)}: {','.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
