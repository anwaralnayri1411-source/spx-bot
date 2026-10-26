"""ماذا فعل العقد بعد إقفاله بالقاعدة؟ (عرض وتسجيل فقط؛ لا يغيّر أي قرار ولا قاعدة خروج). بطلب المستخدم 26/10/2026.

عقد أُقفل بالهدف قد يواصل الصعود، وعقد أُقفل بالوقف قد يرتد (في اليوم نفسه أو اليوم التالي). نعرض بجانب النتيجة:
  بعد الهدف: أعلى سعر بلغه بعدها وآخر سعر.   بعد الوقف: هل ارتد فوق سعر الدخول وإلى أين، وآخر سعر.
النسب كلها من سعر دخول البوت؛ الأسعار للسهم الواحد (×100 للعقد).
تنبيه ثابت: الأرقام قياس بفحوص دورية (15–30 دقيقة) وبسعر الوسط، وبالتالي لا تُباع عند القمم؛ هي لدراسة قاعدة الخروج لا لتوقع تنفيذ.
"""
from typing import Iterable, Optional, Tuple


def from_path(pnls: Iterable[float]) -> Tuple[Optional[float], Optional[float]]:
    """(أعلى ربح، آخر ربح) من مسار ربح/خسارة بعد الإقفال؛ (None, None) إن كان فارغاً."""
    vals = [float(x) for x in pnls if x is not None]
    if not vals:
        return None, None
    return max(vals), vals[-1]


def _price(entry: float, pnl: float) -> float:
    return entry * (1 + pnl)


def after_text(kind: str, entry: float, peak: Optional[float], last: Optional[float], tp: float, extra: float = 0.02) -> str:
    """سطر عربي قصير لما بعد الإقفال. kind: 'tp' أو 'stop'. peak: أعلى ربح بعد الإقفال (نسبة)، last: آخر ربح. فارغ إن لم يتوفر شيء."""
    if peak is None and last is None:
        return ""
    if kind == "tp":
        head = (f"بعد الهدف: بلغ {_price(entry, peak):.2f}$ ({peak * 100:+.0f}%)" if peak is not None and peak > tp + extra
                else "بعد الهدف: لم يتجاوزه")
    else:
        head = (f"بعد الوقف: ارتد إلى {_price(entry, peak):.2f}$ ({peak * 100:+.0f}%)" if peak is not None and peak > 0
                else "بعد الوقف: لم يرتد فوق سعر الدخول")
    tail = f" | آخر سعر {_price(entry, last):.2f}$ ({last * 100:+.0f}%)" if last is not None else ""
    return head + tail


def summarize(rows) -> dict:
    """ملخص وصفي لقائمة عقود مقفلة: [{kind, peak, last}] → أعداد وأنصبة الارتداد بعد الوقف والارتفاع بعد الهدف."""
    stops = [r for r in rows if r["kind"] == "stop" and r.get("peak") is not None or r["kind"] == "stop" and r.get("last") is not None]
    tps = [r for r in rows if r["kind"] == "tp" and (r.get("peak") is not None or r.get("last") is not None)]

    def share(rs, f):
        return sum(1 for r in rs if f(r)) / len(rs) if rs else None
    pk = lambda r: r.get("peak") if r.get("peak") is not None else float("-inf")
    return {"n_stop": len(stops), "n_tp": len(tps),
            "stop_back_above_entry": share(stops, lambda r: pk(r) > 0),
            "stop_reached_tp": share(stops, lambda r: pk(r) >= 0.5),
            "tp_went_higher": share(tps, lambda r: pk(r) > 0.52)}
