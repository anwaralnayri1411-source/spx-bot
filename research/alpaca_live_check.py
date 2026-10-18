#!/usr/bin/env python3
"""فحص سلسلة خيارات SPX اللحظية من Alpaca — عرض فقط، لا يلمس البوت.

نشغّله والسوق مفتوح لنرى:
  - سعر SPY/SPX اللحظي وكم عمره بالثواني (مدى اللحظية).
  - عقود SPXW قريبة من السعر (0DTE): العرض/الطلب، آخر صفقة، الدلتا، التذبذب.
  - عمر كل تسعيرة، لنقارنها لاحقاً بتأخير yfinance (~15 دقيقة).

يحتاج السريْن: ALPACA_KEY, ALPACA_SECRET. لا يكتب أي ملف.
"""
import os
import re
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

KEY = os.environ.get("ALPACA_KEY", "").strip()
SEC = os.environ.get("ALPACA_SECRET", "").strip()
DATA = "https://data.alpaca.markets"
NY = ZoneInfo("America/New_York")
HEADERS = {
    "APCA-API-KEY-ID": KEY,
    "APCA-API-SECRET-KEY": SEC,
    "accept": "application/json",
}
OCC = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


def get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def age(ts):
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - t).total_seconds()
    except Exception:  # noqa: BLE001
        return None


def fresh_tag(a):
    if a is None:
        return "—"
    if a < 60:
        return f"{a:.0f}ث لحظي ✓"
    if a < 1200:
        return f"{a:.0f}ث"
    return f"{a/60:.0f}د قديم/مغلق"


def parse_occ(sym):
    m = OCC.match(sym)
    if not m:
        return None
    root, yymmdd, cp, strike = m.groups()
    return {
        "root": root,
        "exp": "20" + yymmdd[:2] + "-" + yymmdd[2:4] + "-" + yymmdd[4:6],
        "type": "CALL" if cp == "C" else "PUT",
        "strike": int(strike) / 1000.0,
    }


def main():
    print("=== فحص سلسلة SPX اللحظية (Alpaca) ===")
    if not KEY or not SEC:
        print("✗ المفاتيح غير موجودة.")
        return 1

    # 1) SPY كبديل لحظي، ونقدّر مستوى SPX ≈ SPY × 10
    st, d = get(f"{DATA}/v2/stocks/SPY/trades/latest")
    spx_approx = None
    if st == 200 and isinstance(d, dict):
        tr = d.get("trade", {})
        px, ts = tr.get("p"), tr.get("t")
        spx_approx = px * 10 if px else None
        print(f"\nSPY: {px}  ({fresh_tag(age(ts))})  → SPX تقديري ≈ {spx_approx:.0f}")
    else:
        print(f"\n✗ تعذّر جلب SPY ({st}): {str(d)[:160]}")
        return 1

    # 2) تاريخ اليوم بتوقيت نيويورك (انتهاء 0DTE)
    today = datetime.now(NY).date().isoformat()
    lo, hi = spx_approx - 40, spx_approx + 40

    # 3) سلسلة SPXW حول السعر، انتهاء اليوم أو الأقرب
    url = (f"{DATA}/v1beta1/options/snapshots/SPXW?feed=indicative&limit=200"
           f"&strike_price_gte={lo:.0f}&strike_price_lte={hi:.0f}"
           f"&expiration_date_gte={today}")
    st, d = get(url)
    if st != 200 or not isinstance(d, dict) or not d.get("snapshots"):
        print(f"\n✗ تعذّر جلب سلسلة SPXW ({st}): {str(d)[:200]}")
        print("  (قد تكون السلسلة فارغة خارج ساعات التداول — أعد التشغيل والسوق مفتوح.)")
        return 1

    snaps = d["snapshots"]
    rows = []
    for sym, snap in snaps.items():
        info = parse_occ(sym)
        if not info:
            continue
        q = snap.get("latestQuote", {}) or {}
        t = snap.get("latestTrade", {}) or {}
        g = snap.get("greeks", {}) or {}
        rows.append({
            "sym": sym,
            "type": info["type"],
            "exp": info["exp"],
            "strike": info["strike"],
            "bid": q.get("bp"),
            "ask": q.get("ap"),
            "last": t.get("p"),
            "iv": snap.get("impliedVolatility"),
            "delta": g.get("delta"),
            "age": age(q.get("t") or t.get("t")),
        })

    # أقرب انتهاء، ثم أقرب السترايكات للسعر
    if rows:
        nearest_exp = min(r["exp"] for r in rows)
        rows = [r for r in rows if r["exp"] == nearest_exp]
        rows.sort(key=lambda r: abs(r["strike"] - spx_approx))
        print(f"\nأقرب انتهاء: {nearest_exp}  |  عقود حول السعر:")
        hdr = f"{'النوع':<5}{'سترايك':>8}{'عرض':>8}{'طلب':>8}{'آخر':>8}{'دلتا':>7}{'IV':>6}  العمر"
        print(hdr)
        print("-" * len(hdr))
        for r in rows[:10]:
            def fmt(x, w, p=2):
                return (f"{x:>{w}.{p}f}" if isinstance(x, (int, float)) else f"{'—':>{w}}")
            print(f"{r['type']:<5}{fmt(r['strike'],8,0)}{fmt(r['bid'],8)}{fmt(r['ask'],8)}"
                  f"{fmt(r['last'],8)}{fmt(r['delta'],7)}{fmt(r['iv'],6)}  {fresh_tag(r['age'])}")

    print("\n=== انتهى. عرض فقط، لم يُغيّر أي شيء. ===")
    print("ملاحظة: قارن 'العمر' هنا بتأخير yfinance (~900 ثانية) لتقدير الفرق.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
