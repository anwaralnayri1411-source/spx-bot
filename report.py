"""تقارير SPX الدورية بقالب موحد ومختصر: بداية اليوم، منتصفه، نهايته.
القالب: نبض السوق (لون + جملة) ← نطاقات الحركة ← مستويات مهمة ← أقرب المستويات ← أخبار اليوم ← حالة السوق."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import yfinance as yf

import levels
import options_bot as ob

NY, RY = ZoneInfo("America/New_York"), ZoneInfo("Asia/Riyadh")
TITLES = {"open": "🌅 <b>تقرير بداية اليوم</b>", "mid": "🕐 <b>تقرير منتصف اليوم</b>", "close": "🌇 <b>تقرير نهاية اليوم</b>"}

# أحداث أسبوعية ثابتة: (يوم الأسبوع 0=الاثنين، الساعة بتوقيت نيويورك، الاسم، التأثير) ، التأثير: 2 قوي، 1 متوسط
WEEKLY = [(2, (10, 30), "مخزونات النفط الخام", 1), (3, (8, 30), "طلبات إعانة البطالة", 1)]
# أوقات الأحداث المؤرخة في EVENTS (تحتاج توقيتاً)
EVENT_TIME = {"قرار الفيدرالي (FOMC)": ((14, 0), 2), "تقرير التضخم CPI": ((8, 30), 2), "تقرير الوظائف (تقريبي)": ((8, 30), 2)}


def _ry(day, hm):
    dt = datetime(day.year, day.month, day.day, hm[0], hm[1], tzinfo=NY)
    return dt.astimezone(RY).strftime("%H:%M")


def events_today(n):
    """أحداث اليوم مرتبة بالوقت: [(وقت الرياض, اسم, تأثير)]."""
    d, out = n.date(), []
    for wd, hm, name, imp in WEEKLY:
        if d.weekday() == wd:
            out.append((_ry(d, hm), name, imp))
    for dd, name in ob.upcoming_events(d, 0):
        if dd == d:
            hm, imp = EVENT_TIME.get(name, ((8, 30), 2))
            out.append((_ry(d, hm), name, imp))
    return sorted(out)


def _closes(sym, period="10d"):
    try:
        return yf.Ticker(sym).history(period=period)["Close"].dropna()
    except Exception:
        return None


def pulse(n, d):
    """(أيقونة، نص، جملة السبب) من تغير المؤشر منذ إغلاق الأمس وVIX وجاما الصنّاع."""
    S = d["spot"]
    h = _closes("^SPX")
    prev = None
    if h is not None and len(h):
        prev = float(h[h.index.date < n.date()].iloc[-1]) if (h.index.date < n.date()).any() else float(h.iloc[-2])
    chg = (S / prev - 1) if prev else None
    if chg is None:
        icon, word = "⚪", "محايد"
    elif chg <= -0.0015:
        icon, word = "🔴", "سلبي"
    elif chg >= 0.0015:
        icon, word = "🟢", "إيجابي"
    else:
        icon, word = "⚪", "محايد"
    parts = []
    if chg is not None:
        parts.append(f"المؤشر {'يرتفع' if chg > 0 else 'ينخفض'} {abs(chg) * 100:.1f}% منذ إغلاق الأمس" if abs(chg) >= 0.0005
                     else "المؤشر شبه ثابت منذ إغلاق الأمس")
    vx = _closes("^VIX")
    if vx is not None and len(vx) >= 2:
        v, pv = float(vx.iloc[-1]), float(vx.iloc[-2])
        lvl = "منخفض" if v < 16 else ("متوسط" if v < 22 else "مرتفع")
        parts.append(f"مؤشر التذبذب VIX {lvl} عند {v:.1f} ({'أعلى' if v > pv else 'أدنى'} من أمس {pv:.1f})")
    if d.get("net_gex") is not None:
        parts.append("جاما الصنّاع موجبة، ميل للتماسك حول الجدران" if d["net_gex"] > 0
                     else "جاما الصنّاع سالبة، ميل للاندفاع مع الاتجاه")
    return icon, word, ". ".join(parts) + "."


def market_state(d):
    S, em = d["spot"], d.get("em_rest") or d.get("em_4h")
    if not em:
        return "⚪ غير محدد"
    pct = em / S
    neg = d.get("net_gex") is not None and d["net_gex"] < 0
    if pct < 0.004 and not neg:
        return "🟢 هادئ، حركات محدودة متوقعة"
    if pct < 0.008:
        return "🟡 نشط، حركة معتدلة" + ("، قد تتسع مع جاما سالبة" if neg else "")
    return "🔴 متقلب، حركات واسعة محتملة"


def round_barriers(S):
    up = (int(S // 50) + 1) * 50
    dn = int(S // 50) * 50
    if dn == S:
        dn -= 50
    return dn, up


def build(kind, n=None, extras=None, d=None):
    n = n or ob.now_ny()
    d = d or levels.compute(n)
    try:
        levels.log(d)
    except Exception:
        pass
    S = d["spot"]
    icon, word, why = pulse(n, d)
    L = [f"{TITLES[kind]} #تقرير", "",
         f"{icon} <b>نبض السوق: {word}</b>", f"<blockquote>{ob.esc(why)}</blockquote>", ""]
    if d.get("em_1h"):
        L += ["📊 <b>نطاقات الحركة</b>",
              f"   المتبقي للتداول: ±{d['em_rest']:.0f} نقطة" if d.get("em_rest") else "   السوق خارج الجلسة",
              f"   4 ساعات: ±{d['em_4h']:.0f} | ساعة: ±{d['em_1h']:.0f}", ""]
    ml = []
    if d["support"]:
        ml.append(f"   دعم: <b>{d['support']:,.0f}</b>")
    if d["resistance"]:
        ml.append(f"   مقاومة: <b>{d['resistance']:,.0f}</b>")
    if d["flip"]:
        side = "تحت" if S < d["flip"] else "فوق"
        ml.append(f"   نقطة التحول: {d['flip']:,.0f} (السعر {side} بـ {abs(S - d['flip']):.0f})")
    if d["gravity"]:
        ml.append(f"   مستوى الجاذبية: {d['gravity']:,.0f} ({d['gravity'] - S:+.0f})")
    if ml:
        L += ["📌 <b>مستويات مهمة</b>"] + ml + [""]
    dn_b, up_b = round_barriers(S)
    L += ["🎯 <b>أقرب المستويات الآن</b>"]
    cand = []
    for lv, name in ((d["resistance"], "مقاومة"), (d["support"], "دعم"), (d["flip"], "انقلاب الجاما"),
                     (d["gravity"], "جاذبية"), (up_b, "حاجز نفسي"), (dn_b, "حاجز نفسي")):
        if lv:
            cand.append((lv, name))
    merged = {}
    for lv, name in cand:
        merged.setdefault(round(lv), []).append(name)
    cand = [(k, " + ".join(dict.fromkeys(v))) for k, v in merged.items()]
    ups = sorted([c for c in cand if c[0] > S], key=lambda c: c[0] - S)[:2]
    dns = sorted([c for c in cand if c[0] <= S], key=lambda c: S - c[0])[:2]
    for lv, name in ups:
        L.append(f"   ⬆️ {lv:,.0f} — {name} (يبعد {lv - S:.0f} نقطة)")
    for lv, name in dns:
        L.append(f"   ⬇️ {lv:,.0f} — {name} (يبعد {S - lv:.0f} نقطة)")
    L.append("")
    if extras:
        L += extras + [""]
    ev = events_today(n)
    L.append("📰 <b>أخبار اليوم المؤثرة</b>")
    if ev:
        for t, name, imp in ev:
            L.append(f"   {'🔥' if imp == 2 else '🟠'} {t} — {name}")
        top = max(i for _, _, i in ev)
        L.append("   📌 " + ("أحداث قوية، توقع تذبذباً حول وقتها." if top == 2
                             else "أحداث متوسطة التأثير، لا يُتوقع أن تغيّر الاتجاه وحدها."))
    else:
        L.append("   لا أحداث اقتصادية كبرى اليوم.")
    L.append("   🔥 تأثير قوي · 🟠 تأثير متوسط (الأوقات بتوقيت الرياض)")
    L += ["", f"📈 <b>حالة السوق:</b> {market_state(d)}", "",
          f"🕒 {n.astimezone(RY).strftime('%H:%M')} الرياض | {n.strftime('%H:%M')} نيويورك",
          "⚠️ <i>تعليمي وليست توصية. البيانات مجانية ومتأخرة نحو 15 دقيقة.</i>", "👁 <b>عين السوق | Market Eye</b>"]
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    ob.send_telegram(build(sys.argv[1] if len(sys.argv) > 1 else "mid"))
