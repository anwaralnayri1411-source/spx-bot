"""قائمة الأحداث الاقتصادية الكبرى المشتركة بين بوت SPX وبوت الشركات (8/11/2026 بطلب المستخدم: «الأخبار للبوتين معاً»).

بوت SPX يقرأ EVENTS لقواعد المنع قبل 10:00 يوم التقارير، ويقرؤها المتداول الذكي والمتعلّم والصقر من تحليله.
بوت الشركات يعرض تحذيراً فقط (لا منع) إذا وقع حدث قبل انتهاء العقد المقترح.
أضف الأحداث هنا فقط. التواريخ بتوقيت نيويورك؛ تحقق منها من التقويم الرسمي.
"""
from datetime import date

EVENTS = {
    "2026-10-28": "قرار الفيدرالي (FOMC)",
    "2026-12-09": "قرار الفيدرالي (FOMC)",
    "2026-10-14": "تقرير التضخم CPI",   # BLS: 8:30 ص نيويورك
    "2026-10-15": "تقرير أسعار المنتجين PPI",   # صدر 8:30 ص نيويورك (شهري 1.3% مقابل 0.5%)
    "2026-11-10": "تقرير التضخم CPI",
    "2026-11-13": "تقرير أسعار المنتجين PPI",   # من تقويم الأسبوع الذي أرسله المستخدم 8/11
    "2026-12-10": "تقرير التضخم CPI",
}
AR_DAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]


def time_of(name):
    """وقت الصدور بتوقيت نيويورك: التقارير 08:30، وقرار الفيدرالي 14:00."""
    return "14:00" if "الفيدرالي" in name else "08:30"


def between(start, end):
    """[(تاريخ، اسم)] للأحداث من start إلى end شاملاً، مرتبة."""
    return sorted((date.fromisoformat(d), n) for d, n in EVENTS.items() if start <= date.fromisoformat(d) <= end)


def warning_line(start, end, sym=None):
    """سطر تحذير لعقد يعيش من start إلى end، أو "" إن لا أحداث. sym: يضيف الأثر التاريخي للحدث الأول (H-NEWSIMPACT)."""
    ev = between(start, end)
    if not ev:
        return ""
    items = "، ".join(f"{n.replace('تقرير ', '').replace('قرار ', '')} {AR_DAYS[d.weekday()]} {d.day}/{d.month} {time_of(n)}"
                      for d, n in ev)
    out = f"📰 خبر كبير قبل انتهاء العقد: {items} نيويورك"
    imp = next((x for x in (impact_line(n, sym) for _, n in ev) if x), "") if sym else ""
    return out + ("\n" + imp if imp else "")


# ---------------- H-NEWSIMPACT (21/11/2026): الأثر التاريخي لنوع الحدث (معلومة فقط، لا تغيّر التوصية) ----------------
_IMPACT = None


def _impact_table():
    global _IMPACT
    if _IMPACT is None:
        import json
        import os
        try:
            _IMPACT = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "news_impact_table.json"), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _IMPACT = {}
    return _IMPACT


def impact_kind(name):
    return "fomc" if "الفيدرالي" in name else ("jobs" if "الوظائف" in name else None)


def impact_line(name, sym, label=None):
    """سطر: كيف تحرّك الرمز تاريخياً في هذا النوع من الأيام منذ 2019، أو "" إن لا بيانات."""
    k = impact_kind(name)
    s = ((_impact_table().get("syms") or {}).get(sym) or {}).get(k) if k else None
    if not s:
        return ""
    up = s["up"] * 100
    bias = ("ميل صعود" if up >= 60 else "ميل هبوط" if up <= 40 else "لا ميل اتجاهي ثابت") if s["n"] >= 30 else "عينة صغيرة"
    size = f"الحركة {s['ratio']:.1f}× اليوم العادي (±{s['abs']:.1f}%)"
    cont = f"، واستمرت حركة أول 30د بعد القرار حتى الإغلاق {s['cont'] * 100:.0f}%" if "cont" in s else ""
    nm = name.replace("تقرير ", "").replace("قرار ", "").split(" (")[0]
    return f"📊 تاريخياً يوم {nm} ({s['n']} يوماً منذ 2019): {label or sym} صعد {up:.0f}% من الأيام — {bias}؛ {size}{cont}."
