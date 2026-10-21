"""فحص صحة Tastytrade (مصدر الأسعار الثالث في الظل) أثناء الجلسة، ورسالة للمستخدم عند توقفه وعند عودته.
لا يلمس أي توصية أو ملف بوت. فحص واحد = رمز دخول + سعر SPY من REST وعمره ≤ MAX_AGE.
رسالة «توقف» بعد فشلين متتاليين (تجنباً لعطل عابر)، ورسالة «عاد» عند أول نجاح بعدها. كل منهما مرة واحدة.
الرسالة إلى TELEGRAM_CHAT_ID فقط (عبر options_bot.send_telegram، بلا نسخ لمن أضافهم المستخدم).
الحالة: tasty_health.json.   تشغيل: python tasty_health.py
"""
import json
import os
import urllib.error
import urllib.parse

import live_quotes as lq
import options_bot as ob

STATE = "tasty_health.json"
FAILS_TO_ALERT = 2


def check():
    """يرجع (ok، سبب بالعربية)."""
    if not (os.getenv("TASTY_CLIENT_SECRET") and os.getenv("TASTY_REFRESH_TOKEN")):
        return False, "المفتاحان غير موجودين على الخادم"
    try:
        tok = lq._tasty_token()
    except urllib.error.HTTPError as e:
        return False, f"رُفض الدخول (HTTP {e.code}): ربما حُذف الـGrant أو تغيّر الـClient Secret"
    except Exception as e:  # noqa: BLE001
        return False, f"تعذر الاتصال بـ Tastytrade ({type(e).__name__})"
    try:
        q = urllib.parse.urlencode({"equity": "SPY"})
        items = (lq._tasty_req(f"/market-data/by-type?{q}", token=tok).get("data") or {}).get("items") or []
    except urllib.error.HTTPError as e:
        why = "قد يحتاج الحساب إيداعاً" if e.code == 403 else "خطأ من Tastytrade"
        return False, f"رُفض طلب الأسعار (HTTP {e.code}): {why}"
    except Exception as e:  # noqa: BLE001
        return False, f"تعذر جلب الأسعار ({type(e).__name__})"
    it = next((x for x in items if x.get("symbol") == "SPY"), None)
    if not it or not float(it.get("bid") or 0):
        return False, "لا سعر لـ SPY في الرد"
    age = lq._age(it.get("updated-at") or "")
    if age is None or age > lq.MAX_AGE:
        return False, "الأسعار قديمة" + (f" (آخر تحديث قبل {age / 60:.0f} دقيقة)" if age else "")
    return True, f"SPY {it.get('bid')}/{it.get('ask')}، عمره {age:.0f}ث"


def load():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def run():
    n = ob.now_ny()
    if not (ob.market_day(n) and (9, 35) <= (n.hour, n.minute) <= ob.session_end(n)):
        print("خارج الجلسة.")
        return
    s = load()
    ok, why = check()
    print("Tastytrade:", "ok" if ok else "فشل", "-", why)
    now = n.isoformat(timespec="minutes")
    ry = n.astimezone(ob.RY).strftime("%H:%M")
    s.update(last_check=now, last_ok=ok, last_reason=why)
    if ok:
        if s.get("alerted"):
            ob.send_telegram(f"✅ <b>Tastytrade عاد للعمل</b>\n{ob.esc(why)}\n"
                             f"كان متوقفاً منذ {s.get('down_since', '')[11:16]} نيويورك.\n🕒 {ry} الرياض")
        s.update(fails=0, alerted=False, down_since="")
    else:
        s["fails"] = s.get("fails", 0) + 1
        if not s.get("down_since"):
            s["down_since"] = now
        if s["fails"] >= FAILS_TO_ALERT and not s.get("alerted"):
            ob.send_telegram("⚠️ <b>Tastytrade توقف</b> (مصدر الأسعار الثالث في الظل)\n"
                             f"السبب: {ob.esc(why)}\n"
                             f"فشل {s['fails']} فحوص متتالية منذ {s['down_since'][11:16]} نيويورك.\n"
                             "البوت لا يتأثر: يعمل على Alpaca وياهو كالمعتاد.\n"
                             f"🕒 {ry} الرياض")
            s["alerted"] = True
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    run()
