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
FVG_WAIT = 60            # الإصدار 19: أقصى انتظار لعودة السعر إلى الفجوة (دقيقة)
STOCK_CHASE, STOCK_HOLD = 0.015, 24   # الشركات: حركة اليوم للملاحقة، وشموع 5د للثبات داخل اليوم (ساعتان)
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


def stock_block(side, S, hi20, lo20, prev_close, today_closes, hold=STOCK_HOLD, chase=STOCK_CHASE):
    """بوت الشركات: سبب الحجب (نص) إن كانت التوصية كسراً غير ثابت لأعلى/أدنى 20 يوماً في جهتها، أو ملاحقة بلا كسر ثابت؛ وإلا None."""
    L = hi20 if side == "CALL" else lo20
    beyond = (lambda x: x > L) if side == "CALL" else (lambda x: x < L)
    last = today_closes[-hold:]
    unheld = beyond(S) and not beyond(prev_close) and not (len(last) == hold and all(beyond(x) for x in last))
    held = beyond(S) and not unheld
    move = S / prev_close - 1
    word = "أعلى" if side == "CALL" else "أدنى"
    if unheld:
        return f"كسر {word} 20 يوماً ({L:,.2f}) لم يثبت بعد؛ ننتظر إغلاق يوم أو ساعتين خلفه"
    if (move >= chase if side == "CALL" else move <= -chase) and not held:
        return f"ملاحقة: السهم تحرك {move * 100:+.1f}% اليوم في جهة التوصية بلا كسر ثابت"
    return None


def fvg_zone(highs, lows, side, look=6):
    """آخر فجوة قيمة 5د في جهة side ضمن آخر look شموع: (أسفل، أعلى) أو None.
    صاعدة: أدنى الشمعة k > أعلى الشمعة k−2. هابطة: أعلى الشمعة k < أدنى الشمعة k−2."""
    n = len(highs)
    for k in range(n - 1, max(1, n - look) - 1, -1):
        if k < 2:
            break
        if side == "CALL" and lows[k] > highs[k - 2]:
            return (highs[k - 2], lows[k])
        if side == "PUT" and highs[k] < lows[k - 2]:
            return (highs[k], lows[k - 2])
    return None


def fvg_touch(zone, side, hi, lo, close):
    """الشمعة المنتهية لمست المنطقة وأغلقت داخلها أو في جهة الصفقة."""
    bot, top = zone
    if side == "CALL":
        return lo <= top and close >= bot
    return hi >= bot and close <= top


# ====================== H-BHVOTE (الإصدار 21): إعادة الاختبار والفشل الجديد ======================
RETEST_TOL = 5.0         # إغلاق معاكس حتى 5 نقاط = إعادة اختبار لا فشل؛ ولمس المستوى حتى 5 نقاط
RETEST_BARS = 12         # نافذة إعادة الاختبار بعد شمعة الكسر (ساعة)
DIR.update({"retest_up": "CALL", "retest_down": "PUT"})
KIND_AR.update({"retest_up": "كسر للأعلى وإعادة اختبار ناجحة", "retest_down": "كسر للأسفل وإعادة اختبار ناجحة"})


def events2(closes, highs, lows, lvls, start=0, tol=RETEST_TOL, window=RETEST_BARS):
    """أحداث الإصدار 21 (H-BHVOTE): ثبات كما في events، والفشل (رئيسية فقط، قبل الثبات) = إغلاق معاكس أبعد من tol
    أو إغلاقان معاكسان متتاليان، وإعادة الاختبار (كل المستويات) = (أ) إغلاق معاكس واحد ≤ tol قبل الثبات ثم إغلاق في
    جهة الكسر بـBUF، أو (ب) بعد الثبات: ابتعاد إغلاق أكثر من tol ثم لمس المستوى حتى tol ثم إغلاق في جهة الكسر بـBUF.
    يرجع [{kind, level, name, key, i, bi}] حيث bi شمعة الكسر."""
    out = []
    n = len(closes)
    for L, name, key in lvls:
        s0 = max(1, start if name.endswith("أول 30 دقيقة") else 1)
        for i in range(s0, n):
            up = closes[i - 1] <= L and closes[i] > L + BUF
            dn = closes[i - 1] >= L and closes[i] < L - BUF
            if not (up or dn):
                continue
            g = 1 if up else -1
            base = {"level": L, "name": name, "key": key, "bi": i}
            held, opp_run, had_opp, away, touched = False, 0, False, False, False
            for j in range(i + 1, min(i + 1 + window, n)):
                c = (closes[j] - L) * g                  # المسافة في جهة الكسر (موجبة = في جهته)
                if not held:
                    if c < 0:
                        opp_run += 1
                        if c < -tol or opp_run >= 2:
                            if key:
                                out.append(dict(base, kind="fail_up" if up else "fail_down", i=j))
                            break
                        continue
                    if (opp_run or had_opp) and c > BUF:             # (أ) إغلاق معاكس واحد صغير ثم عودة: إعادة اختبار
                        out.append(dict(base, kind="retest_up" if up else "retest_down", i=j))
                        break
                    had_opp = had_opp or opp_run > 0
                    opp_run = 0
                    away = away or c > tol
                    if not had_opp and j == i + HOLD:
                        held = True
                        out.append(dict(base, kind="hold_up" if up else "hold_down", i=j))
                    continue
                ext = (lows[j] - L) if up else (L - highs[j])   # أقرب نقطة من المستوى في الشمعة
                if away and ext <= tol:
                    touched = True
                if touched and c > BUF:
                    out.append(dict(base, kind="retest_up" if up else "retest_down", i=j))
                    break
                away = away or c > tol
                if c < 0:
                    break                                 # عاد عبر المستوى بعد الثبات: ينتهي هذا الكسر (الكسر المعاكس يُرصد وحده)
    return sorted(out, key=lambda e: (e["i"], e["key"]))
