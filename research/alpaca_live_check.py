#!/usr/bin/env python3
"""فحص سلسلة خيارات SPX اللحظية من Alpaca — عرض فقط، لا يلمس البوت ولا يكتب أي ملف.

نشغّله والسوق مفتوح لنعرف: هل أسعار عقود SPXW لحظية فعلاً؟
  - يجرّب المصدرين: opra (بيانات البورصة الحقيقية، غالباً مدفوعة) وindicative (استرشادي، متاح مجاناً).
  - سعر SPX من السلسلة نفسها (تكافؤ الكول والبوت: S ≈ K + C − P) بدل SPY × 10 الذي ينحرف ~25 نقطة.
  - عقود انتهاء اليوم فقط (0DTE) مع قراءة كل الصفحات، فلا يقطعها سقف العدد.
  - عمر كل تسعيرة بالثواني، وملخص بالوسيط للحكم (yfinance ≈ 900 ثانية).

يحتاج السريْن: ALPACA_KEY, ALPACA_SECRET.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

KEY = os.environ.get("ALPACA_KEY", "").strip()
SEC = os.environ.get("ALPACA_SECRET", "").strip()
DATA = "https://data.alpaca.markets"
NY = ZoneInfo("America/New_York")
HEADERS = {"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SEC, "accept": "application/json"}
OCC = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")
RATIO_GUESS = 10.03     # SPX/SPY الفعلي الأسبوع الماضي 10.030–10.035 (التقدير الأول فقط؛ السعر النهائي من التكافؤ)
WIDE, SHOW = 120, 10    # نافذة الجلب حول التقدير (نقطة)، وعدد العقود المعروضة
FEEDS = ("opra", "indicative")
LIVE_S, DELAYED_S = 60, 900


def get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def age(ts, now=None):
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", ts.replace("Z", "+00:00")))
        return ((now or datetime.now(timezone.utc)) - t).total_seconds()
    except Exception:  # noqa: BLE001
        return None


def fresh_tag(a):
    if a is None:
        return "—"
    if a < LIVE_S:
        return f"{a:.0f}ث لحظي ✓"
    if a < 1200:
        return f"{a:.0f}ث"
    return f"{a / 60:.0f}د قديم/مغلق"


def parse_occ(sym):
    m = OCC.match(sym)
    if not m:
        return None
    root, yymmdd, cp, strike = m.groups()
    return {"root": root, "exp": f"20{yymmdd[:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}",
            "type": "CALL" if cp == "C" else "PUT", "strike": int(strike) / 1000.0}


def chain(und, feed, exp, lo, hi):
    """كل صفحات اللقطة لانتهاء واحد. يرجع (الحالة، {رمز: لقطة} أو نص الخطأ)."""
    snaps, token = {}, None
    for _ in range(20):
        q = {"feed": feed, "limit": 1000, "expiration_date": exp,
             "strike_price_gte": f"{lo:.0f}", "strike_price_lte": f"{hi:.0f}"}
        if token:
            q["page_token"] = token
        st, d = get(f"{DATA}/v1beta1/options/snapshots/{und}?{urllib.parse.urlencode(q)}")
        if st != 200 or not isinstance(d, dict):
            return st, d
        snaps.update(d.get("snapshots") or {})
        token = d.get("next_page_token")
        if not token:
            break
    return 200, snaps


def rows_of(snaps, now=None):
    out = []
    for sym, s in snaps.items():
        info = parse_occ(sym)
        if not info:
            continue
        q, t, g = s.get("latestQuote") or {}, s.get("latestTrade") or {}, s.get("greeks") or {}
        bid, ask = q.get("bp"), q.get("ap")
        out.append({**info, "sym": sym, "bid": bid, "ask": ask, "last": t.get("p"),
                    "mid": (bid + ask) / 2 if bid and ask and bid > 0 and ask > 0 else None,
                    "iv": s.get("impliedVolatility"), "delta": g.get("delta"),
                    "q_age": age(q.get("t"), now), "t_age": age(t.get("t"), now)})
    return out


def parity_spot(rows, guess):
    """S ≈ K + C − P لأقرب 3 سترايكات فيها كول وبوت بعرض وطلب (فائدة 0DTE مهملة)."""
    by = {}
    for r in rows:
        if r["mid"] is not None:
            by.setdefault(r["strike"], {})[r["type"]] = r["mid"]
    est = sorted((abs(k - guess), k + v["CALL"] - v["PUT"]) for k, v in by.items() if "CALL" in v and "PUT" in v)
    return median(e for _, e in est[:3]) if est else None


def fmt(x, w, p=2):
    return f"{x:>{w}.{p}f}" if isinstance(x, (int, float)) else f"{'—':>{w}}"


def run_feed(und, feed, exp, guess, now=None):
    print(f"\n----- المصدر: {feed} | {und} | انتهاء {exp} -----")
    st, snaps = chain(und, feed, exp, guess - WIDE, guess + WIDE)
    if st != 200:
        why = "غير مشمول في اشتراكك" if st in (401, 403) else "خطأ"
        print(f"  ✗ {why} ({st}): {str(snaps)[:200]}")
        return "denied" if st in (401, 403) else None
    rows = rows_of(snaps, now)
    if not rows:
        print("  ✗ السلسلة فارغة (لا عقود لهذا الانتهاء/النطاق).")
        return None
    spot = parity_spot(rows, guess)
    S = spot or guess
    print(f"  عقود مستلمة: {len(rows)} | سعر SPX من التكافؤ: {fmt(spot, 0, 2).strip()} (التقدير الأول {guess:.0f})")
    near = sorted(rows, key=lambda r: abs(r["strike"] - S))[:SHOW * 2]
    print(f"  {'النوع':<5}{'سترايك':>8}{'عرض':>8}{'طلب':>8}{'آخر':>8}{'دلتا':>7}{'IV':>6}  عمر التسعيرة / آخر صفقة")
    for r in sorted(near, key=lambda r: (r["type"], r["strike"])):
        print(f"  {r['type']:<5}{fmt(r['strike'], 8, 0)}{fmt(r['bid'], 8)}{fmt(r['ask'], 8)}{fmt(r['last'], 8)}"
              f"{fmt(r['delta'], 7)}{fmt(r['iv'], 6)}  {fresh_tag(r['q_age'])} / {fresh_tag(r['t_age'])}")
    ages = [r["q_age"] for r in near if r["q_age"] is not None]
    if not ages:
        print("  الحكم: لا أعمار للتسعيرات.")
        return {"feed": feed, "median": None, "spot": spot}
    m = median(ages)
    verdict = ("لحظي ✓" if m < LIVE_S else "متأخر مثل ياهو تقريباً ✗" if m >= DELAYED_S * 0.8 else "متأخر جزئياً")
    print(f"  الحكم: وسيط عمر التسعيرات حول السعر = {m:.0f} ثانية → {verdict}")
    return {"feed": feed, "median": m, "spot": spot}


def main(now=None):
    print("=== فحص سلسلة SPX اللحظية (Alpaca) ===")
    if not KEY or not SEC:
        print("✗ المفاتيح غير موجودة.")
        return 1
    st, d = get(f"{DATA}/v2/stocks/SPY/trades/latest")
    tr = d.get("trade", {}) if st == 200 and isinstance(d, dict) else {}
    px = tr.get("p")
    if not px:
        print(f"✗ تعذّر جلب SPY ({st}): {str(d)[:160]}")
        return 1
    guess = px * RATIO_GUESS
    print(f"SPY: {px} ({fresh_tag(age(tr.get('t'), now))}) → تقدير أول لـ SPX ≈ {guess:.0f} (×{RATIO_GUESS})")

    today = (now or datetime.now(timezone.utc)).astimezone(NY).date()
    exps = [today + timedelta(days=i) for i in range(0, 5)]
    results = []
    for feed in FEEDS:
        res = None
        for und in ("SPXW", "SPX"):
            for e in exps:                              # اليوم أولاً؛ في العطلة أقرب يوم تداول
                res = run_feed(und, feed, e.isoformat(), guess, now)
                if res is not None:
                    break
            if res is not None:
                break
        results.append((feed, None if res == "denied" else res))

    print("\n===== الخلاصة =====")
    for feed, res in results:
        if res is None:
            print(f"  {feed}: غير متاح")
        elif res["median"] is None:
            print(f"  {feed}: متاح بلا أعمار")
        else:
            print(f"  {feed}: وسيط العمر {res['median']:.0f} ثانية")
    print(f"  للمقارنة: yfinance ≈ {DELAYED_S} ثانية. أقل من {LIVE_S} ثانية = لحظي.")
    print("  تنبيه: أسعار indicative استرشادية؛ قارن بعضها بمنصة تثق بها قبل الاعتماد عليها.")
    print("=== انتهى. عرض فقط، لم يُغيّر أي شيء. ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
