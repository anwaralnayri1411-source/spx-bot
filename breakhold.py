"""«الكسر والثبات» (H-BREAKHOLD، مسجّل مسبقاً في CLAUDE.md 11/11/2026 بطلب المستخدم). دوال صافية بلا شبكة.

قاعدة المستخدم: الكسر لا يُعتمد إلا بعد اختباره بعدة شموع ثم التأكيد؛ فشل الثبات إشارة في العكس؛ والتقييم يتغير خلال اليوم ويقلب الجهة.
  - المستويات: أعلى/أدنى أول 30 دقيقة وأعلى/أدنى أمس (رئيسية)، ومضاعفات 50 نقطة (دائرية).
  - كسر: إغلاق شمعة 5د عبر المستوى بـ BUF والشمعة قبلها على الجهة الأخرى.
  - ثبات: إغلاق HOLD شمعتين تاليتين في جهة الكسر. فشل: إغلاق إحداهما عائداً عبر المستوى (رئيسية فقط).
  - المكان: لا PUT في أدنى EDGE من مدى اليوم ولا CALL في أعلاه، إلا بحدث ثبات حديث في الجهة نفسها.
"""
import math

HOLD = 2                 # شموع الثبات بعد شمعة الكسر
BUF = 0.5                # نقاط فوق/تحت المستوى لاعتبار الإغلاق كسراً
RECENT_BARS = 6          # حدث حديث = آخر 30 دقيقة (6 شموع 5د)
EVENT_FROM_M = 10 * 60   # لا أحداث تقلب الجهة قبل 10:00
PLACE_FROM_M = 10 * 60 + 30
EDGE = 0.30              # الثلث الأخير تقريباً من مدى اليوم
MIN_RANGE = 15.0         # مدى أصغر من هذا: لا شرط مكان
ROUND = 50

DIR = {"hold_up": "CALL", "fail_down": "CALL", "hold_down": "PUT", "fail_up": "PUT"}
KIND_AR = {"hold_up": "كسر للأعلى وثبات", "hold_down": "كسر للأسفل وثبات",
           "fail_up": "فشل الثبات فوق", "fail_down": "فشل الثبات تحت"}


def levels(orb=None, pd_hl=None, lo=None, hi=None):
    """[(السعر، الاسم، رئيسي؟)]: نطاق أول 30د وأمس، ومضاعفات 50 حول مدى اليوم."""
    out = []
    if orb:
        out += [(float(orb[0]), "أعلى أول 30 دقيقة", True), (float(orb[1]), "أدنى أول 30 دقيقة", True)]
    if pd_hl:
        out += [(float(pd_hl[0]), "أعلى أمس", True), (float(pd_hl[1]), "أدنى أمس", True)]
    if lo is not None and hi is not None:
        k = math.floor((lo - 100) / ROUND) * ROUND
        while k <= hi + 100:
            out.append((float(k), f"{k:,.0f}", False))
            k += ROUND
    return out


def events(closes, lvls, start=0):
    """أحداث الكسر/الثبات/الفشل على إغلاقات شموع منتهية. start: أول شمعة يُقبل عندها الكسر (لنطاق أول 30د: 6).
    يرجع [{kind, level, name, key, i}] حيث i رقم الشمعة التي اكتمل عندها الحدث."""
    out = []
    n = len(closes)
    for L, name, key in lvls:
        s0 = max(1, start if name.endswith("أول 30 دقيقة") else 1)
        for i in range(s0, n):
            up = closes[i - 1] <= L and closes[i] > L + BUF
            dn = closes[i - 1] >= L and closes[i] < L - BUF
            if not (up or dn):
                continue
            nxt = closes[i + 1:i + 1 + HOLD]
            back = [j for j, c in enumerate(nxt) if (c < L if up else c > L)]
            if back:
                if key:
                    out.append({"kind": "fail_up" if up else "fail_down", "level": L, "name": name, "key": key, "i": i + 1 + back[0]})
            elif len(nxt) == HOLD:
                out.append({"kind": "hold_up" if up else "hold_down", "level": L, "name": name, "key": key, "i": i + HOLD})
    return sorted(out, key=lambda e: (e["i"], e["key"]))


def latest(evts, n_bars, minutes, recent=RECENT_BARS, from_m=EVENT_FROM_M):
    """آخر حدث حديث (ضمن آخر recent شموع ومن from_m). minutes: دقيقة بداية كل شمعة (من منتصف الليل)."""
    ok = [e for e in evts if e["i"] >= n_bars - recent and minutes[e["i"]] + 5 >= from_m]
    return ok[-1] if ok else None


def orb_hold(closes, orb, hold=HOLD):
    """'up' إن أغلقت آخر hold+1 شموع فوق أعلى نطاق أول 30د، 'down' تحت أدناه، وإلا None."""
    if not orb or len(closes) < hold + 1:
        return None
    last = closes[-(hold + 1):]
    if all(c > orb[0] for c in last):
        return "up"
    if all(c < orb[1] for c in last):
        return "down"
    return None


def position(S, day_hl):
    """موضع السعر في مدى اليوم (0 القاع، 1 القمة)، أو None إن كان المدى أصغر من MIN_RANGE."""
    if not day_hl:
        return None
    hi, lo = day_hl
    if hi - lo < MIN_RANGE:
        return None
    return (S - lo) / (hi - lo)


def place_block(side, S, day_hl, evt, minute):
    """سبب المنع (نص) إن كان الدخول في طرف المدى في جهة الحركة، أو None."""
    if minute < PLACE_FROM_M:
        return None
    pos = position(S, day_hl)
    if pos is None:
        return None
    same_hold = evt is not None and evt["kind"] == ("hold_down" if side == "PUT" else "hold_up")
    if same_hold:
        return None
    if side == "PUT" and pos <= EDGE:
        return f"المكان: السعر في أدنى {pos * 100:.0f}% من مدى اليوم؛ لا PUT قرب القاع بلا كسر ثابت (ننتظر ارتداداً أو كسراً يثبت)."
    if side == "CALL" and pos >= 1 - EDGE:
        return f"المكان: السعر في أعلى {pos * 100:.0f}% من مدى اليوم؛ لا CALL قرب القمة بلا كسر ثابت (ننتظر تصحيحاً أو كسراً يثبت)."
    return None


EVENT_STRENGTH = 6.0     # أقل قوة لتوصية يحددها حدث كسر/ثبات


def apply(side, strength, evt):
    """(الجهة، القوة، النوع) بعد الحدث الحديث: '' بلا حدث، 'flip' إن قلب الجهة، 'confirm' إن أكدها.
    side قد يكون None (فرق النقاط دون الحد)؛ الحدث يحدد الجهة عندها."""
    if not evt:
        return side, strength, ""
    new = DIR[evt["kind"]]
    if side is not None and new != side:
        return new, EVENT_STRENGTH, "flip"
    return new, max(strength if side == new else 0.0, EVENT_STRENGTH), "confirm"


def event_text(evt):
    if not evt:
        return ""
    lv = f"{evt['level']:,.0f}"
    where = lv if evt["name"] == lv else f"{evt['name']} ({lv})"
    return f"{KIND_AR[evt['kind']]} عند {where} ← {DIR[evt['kind']]}"
