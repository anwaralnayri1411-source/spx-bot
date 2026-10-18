#!/usr/bin/env python3
"""اختبار اتصال Alpaca — تسجيل فقط، لا يلمس البوت ولا يرسل أي توصية.

الهدف: نتأكد أن المفاتيح تعمل، وأن Alpaca يعطي سعر SPY لحظياً، ونقيس
كم بُعد الطابع الزمني عن "الآن" (أي مدى لحظية البيانات)، ونفحص هل تغطية
خيارات SPX/SPXW متاحة في الخطة الحالية.

التشغيل محلياً أو على GitHub Actions بعد ضبط السريْن:
    ALPACA_KEY, ALPACA_SECRET
لا يكتب أي ملف، فقط يطبع تقريراً.
"""
import os
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone

KEY = os.environ.get("ALPACA_KEY", "").strip()
SEC = os.environ.get("ALPACA_SECRET", "").strip()
DATA = "https://data.alpaca.markets"

HEADERS = {
    "APCA-API-KEY-ID": KEY,
    "APCA-API-SECRET-KEY": SEC,
    "accept": "application/json",
}


def get(url):
    """طلب GET بسيط يرجع (الحالة، البيانات أو نص الخطأ)."""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return e.code, body
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def age_seconds(ts):
    """عمر الطابع الزمني بالثواني مقابل الآن (UTC)."""
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - t).total_seconds()
    except Exception:  # noqa: BLE001
        return None


def main():
    print("=== اختبار اتصال Alpaca ===")
    if not KEY or not SEC:
        print("✗ المفاتيح غير موجودة. اضبط ALPACA_KEY و ALPACA_SECRET.")
        return 1
    print(f"المفتاح: ...{KEY[-4:]} (موجود)")

    # 1) آخر صفقة على SPY — اختبار البيانات اللحظية
    st, data = get(f"{DATA}/v2/stocks/SPY/trades/latest")
    print("\n[1] آخر صفقة SPY:")
    if st == 200 and isinstance(data, dict):
        tr = data.get("trade", {})
        px, ts = tr.get("p"), tr.get("t")
        a = age_seconds(ts) if ts else None
        print(f"  السعر: {px}  |  الوقت: {ts}")
        if a is not None:
            fresh = "لحظي ✓" if a < 120 else ("متأخر" if a < 1200 else "قديم/السوق مغلق")
            print(f"  عمر البيانات: {a:.0f} ثانية  ← {fresh}")
    else:
        print(f"  ✗ فشل ({st}): {str(data)[:200]}")

    # 2) أحدث سعر (quote) SPY — Bid/Ask
    st, data = get(f"{DATA}/v2/stocks/SPY/quotes/latest")
    print("\n[2] أحدث عرض/طلب SPY:")
    if st == 200 and isinstance(data, dict):
        q = data.get("quote", {})
        print(f"  Bid: {q.get('bp')}  Ask: {q.get('ap')}  |  الوقت: {q.get('t')}")
    else:
        print(f"  ✗ فشل ({st}): {str(data)[:200]}")

    # 3) تغطية خيارات SPY (بديل قريب من SPX) — لقطة سلسلة
    st, data = get(f"{DATA}/v1beta1/options/snapshots/SPY?limit=5")
    print("\n[3] خيارات SPY (snapshots):")
    if st == 200 and isinstance(data, dict):
        snaps = data.get("snapshots", {})
        print(f"  ✓ متاح — عدد العقود في اللقطة: {len(snaps)}")
        for sym in list(snaps)[:3]:
            print(f"    {sym}")
    else:
        print(f"  ✗ غير متاح ({st}): {str(data)[:200]}")

    # 4) تغطية خيارات SPX/SPXW المؤشرية — الأهم للبوت
    for und in ("SPX", "SPXW"):
        st, data = get(f"{DATA}/v1beta1/options/snapshots/{und}?limit=5")
        print(f"\n[4] خيارات {und} (مؤشر):")
        if st == 200 and isinstance(data, dict) and data.get("snapshots"):
            print(f"  ✓ متاح — عدد العقود: {len(data['snapshots'])}")
        else:
            print(f"  ✗ غير متاح/فارغ ({st}): {str(data)[:160]}")

    print("\n=== انتهى. هذا اختبار اتصال فقط، لم يُغيّر أي شيء. ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
