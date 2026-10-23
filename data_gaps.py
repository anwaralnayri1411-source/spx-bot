"""تسجيل «فجوات البيانات»: كلما فشل جلب مدخل يغذّي القرار وابتلع البوت الخطأ بصمت، نسجّل ذلك في ملف للمراجعة.

لماذا: في `options_bot.analyze` لو فشل جلب VWAP يبقى `above_vwap=True` فيُحسب الاتجاه اللحظي «صاعداً» بنقطتين دون أن
يعلم أحد؛ وفشل ES أو VIX يُسقط نقاطهما بصمت. لا نعرف كم مرة يحدث هذا، فنقيسه قبل أن نغيّر أي قاعدة.

مرحلة تسجيل فقط (23/10/2026): هذه الدالة لا تغيّر أي قرار ولا رسالة ولا تُرجع شيئاً للبوت، ولا ترفع استثناءً أبداً.
الملفات (واحد لكل مشروع حتى لا يتعارض حفظهما في git): data_gaps_spx_log.csv (بوت SPX)، data_gaps_stocks_log.csv (الشركات).
الأعمدة: الوقت (نيويورك)، السكربت، المدخل، نوع الخطأ (اسم الصنف فقط)، تفصيل قصير ثابت (رمز السهم مثلاً).
لا نسجّل نص الخطأ أبداً: قد يحمل ترويسات أو مفاتيح، والسجلات عامة.
سقف: 5 سطور لكل مدخل في كل تشغيل، حتى لا يمتلئ الملف عند انقطاع ياهو كله.
"""
import csv
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
_ROOT = os.path.dirname(os.path.abspath(__file__))
_PER_ITEM_CAP = 5
_COLS = ["time_ny", "script", "item", "error", "detail"]
_seen = {}


def path_for(project):
    return os.path.join(_ROOT, f"data_gaps_{project}_log.csv")


def note(project, item, exc=None, detail=""):
    """سجّل فجوة. project: 'spx' | 'stocks'. item: اسم المدخل (vwap, es, vix, option_chain, spot_5m...).
    exc: الاستثناء إن وُجد (نسجّل اسم صنفه فقط). detail: نص قصير ثابت من المستدعي. لا ترفع استثناءً أبداً."""
    try:
        key = (project, item)
        _seen[key] = _seen.get(key, 0) + 1
        if _seen[key] > _PER_ITEM_CAP:
            return
        path = path_for(project)
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(_COLS)
            w.writerow([datetime.now(NY).isoformat(timespec="minutes"), os.path.basename(sys.argv[0]), item,
                        type(exc).__name__ if exc is not None else "", str(detail)[:40]])
    except Exception:  # noqa: BLE001  — التسجيل لا يجوز أن يكسر البوت أبداً
        pass
