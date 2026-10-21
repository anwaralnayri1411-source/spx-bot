"""صفحة حية لبطاقة قراءة السوق على خادم المستخدم: تُعاد كتابتها كل دقيقة أثناء الجلسة، والمتصفح يحدّثها كل 60 ثانية.

نفس بطاقة market_read.py (card_html) بلا تغيير في منطقها، مع شريط علوي: سعر SPX اللحظي من Tastytrade (إن توفر)
ووقت آخر تحديث، وتنبيه أن المستويات وأسعار العقود من ياهو (متأخرة ~15د).
الإعداد في ملف واحد على الخادم (يكتبه deploy/setup_market_live.sh): MARKET_LIVE_CONF (الافتراضي /etc/spxbot/market_live.conf)
  dir=/var/www/spxbot/m/<رمز سري>      مجلد الصفحة (يكتبه المستخدم spxbot، ويقدّمه nginx للعرض فقط)
  url=http://<عنوان الخادم>/m/<الرمز>/  الرابط الذي يُرسل مع البطاقة
بلا الملف لا يعمل شيء. لا يرسل رسائل، ولا يكتب ملفات في المستودع.
تشغيل: python market_live.py --loop   (مهمة طويلة حتى 16:05 نيويورك)   |   python market_live.py  (تحديث واحد)
"""
import html
import os
import sys
import time
import urllib.parse
from datetime import timedelta

CONF = os.getenv("MARKET_LIVE_CONF", "/etc/spxbot/market_live.conf")
REFRESH_S = 60


def conf():
    """{'dir':…, 'url':…} أو {} إن لم يُجهَّز الخادم."""
    try:
        d = dict(l.strip().split("=", 1) for l in open(CONF, encoding="utf-8") if "=" in l and not l.startswith("#"))
        return d if d.get("dir") and d.get("url") else {}
    except Exception:
        return {}


def spx_live():
    """(السعر، الوقت) من Tastytrade، أو (None، None)."""
    try:
        import live_quotes as lq
        if not (os.getenv("TASTY_CLIENT_SECRET") and os.getenv("TASTY_REFRESH_TOKEN")):
            return None, None
        q = urllib.parse.urlencode({"index": "SPX"})
        it = lq._tasty_req(f"/market-data/by-type?{q}", token=lq._tasty_token())["data"]["items"][0]
        px = float(it.get("last") or it.get("mark") or 0)
        return (px or None), it.get("updated-at")
    except Exception as e:  # noqa: BLE001
        print("تعذر SPX اللحظي:", type(e).__name__)
        return None, None


def page(d, n, live_px):
    import market_read_card as mc
    body = mc.card_html(d)
    tm = n.astimezone(__import__("options_bot").RY).strftime("%H:%M:%S")
    live = (f'SPX الآن <b dir="ltr">{live_px:,.2f}</b> (لحظي) · ' if live_px else "")
    bar = (f'<div style="position:sticky;top:0;z-index:9;background:#c9a227;color:#0b0e16;font:700 22px Tahoma,sans-serif;'
           f'padding:10px 24px;text-align:center">🔴 مباشر · {live}آخر تحديث {tm} الرياض · تتحدّث كل دقيقة'
           f'<div style="font:400 15px Tahoma,sans-serif">المستويات وأسعار العقود من بيانات متأخرة ~15 دقيقة. قراءة وليست توصية.</div></div>')
    head = (f'<meta charset="utf-8"><meta name="viewport" content="width={mc.W}"><meta http-equiv="refresh" content="{REFRESH_S}">'
            f'<meta name="robots" content="noindex,nofollow"><title>قراءة السوق · مباشر</title>')
    body = body.replace('<meta charset="utf-8">', head, 1).replace("<body>", "<body>" + bar, 1)
    return body


def closed_page(n):
    tm = n.astimezone(__import__("options_bot").RY).strftime("%Y-%m-%d %H:%M")
    return ('<!doctype html><html dir="rtl" lang="ar"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<meta name="robots" content="noindex,nofollow"><title>قراءة السوق</title>'
            '<body style="background:#0b0e16;color:#eef1f8;font:20px Tahoma,sans-serif;text-align:center;padding:60px 20px">'
            f'<h2 style="color:#c9a227">قراءة السوق · SPX</h2><p>السوق مغلق. تبدأ الصفحة الحية مع الجلسة القادمة.</p>'
            f'<p style="color:#6f7890;font-size:15px">آخر تحديث {html.escape(tm)} الرياض</p></body></html>')


def write(c, text):
    os.makedirs(c["dir"], exist_ok=True)
    tmp = os.path.join(c["dir"], ".index.html.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, os.path.join(c["dir"], "index.html"))   # كتابة ذرية: المتصفح لا يرى صفحة نصفية


def update_once(c):
    import market_read as mr
    import options_bot as ob
    n = ob.now_ny()
    if not ob.in_window(n):
        write(c, closed_page(n))
        return False
    d = mr.gather()
    px, _ = spx_live()
    write(c, page(d, n, px))
    return True


def loop(c, max_min=400):
    import options_bot as ob
    start = ob.now_ny()
    while True:
        n = ob.now_ny()
        if (n.hour, n.minute) > (16, 5) or (n - start).total_seconds() / 60 > max_min:
            break
        if (n.hour, n.minute) >= (9, 30):
            t0 = time.time()
            try:
                update_once(c)
            except Exception as e:  # noqa: BLE001
                print("خطأ:", type(e).__name__, str(e)[:150])
            print(f"{n:%H:%M} تحديث في {time.time() - t0:.0f}ث")
        nxt = (n + timedelta(minutes=1)).replace(second=5, microsecond=0)
        time.sleep(max(1.0, (nxt - ob.now_ny()).total_seconds()))
    try:
        write(c, closed_page(ob.now_ny()))
    except Exception as e:  # noqa: BLE001
        print("تعذر كتابة صفحة الإغلاق:", e)


if __name__ == "__main__":
    c = conf()
    if not c:
        print("لا إعداد للصفحة الحية على هذا الجهاز (", CONF, ")؛ لا شيء.")
        sys.exit(0)
    if "--loop" in sys.argv:
        loop(c)
    else:
        update_once(c)
