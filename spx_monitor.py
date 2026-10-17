"""متابعة خفيفة لصفقات SPX المفتوحة فقط (هدف/وقف/تراجع) كل 5 دقائق. لا تحلل السوق ولا تصدر توصيات جديدة.
تعمل فقط إن وُجدت صفقة حيّة (open/tp) في spx_positions.json. مشروع SPX فقط؛ لا علاقة له بالشركات."""
import json
import sys

import options_bot as ob


def has_live(path=ob.POS_FILE):
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:
        return False
    return any(p.get("status") in ("open", "tp") for p in d.get("positions", []))


if __name__ == "__main__":
    n = ob.now_ny()
    if not ob.in_window(n):
        print("خارج وقت التداول.")
        sys.exit(0)
    if not has_live():
        print("لا صفقات حيّة.")
        sys.exit(0)
    ob.monitor_positions(n)
    try:
        import spx_thesis
        spx_thesis.write()
    except Exception as e:
        print('تعذر تحديث اختبار وقف الفكرة:', e)
