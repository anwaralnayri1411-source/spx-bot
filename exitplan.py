"""خطة الخروج والاحتفاظ بالرابحة (H-EXITPLAN، مسجّلة في CLAUDE.md 12/11/2026 قبل الكود؛ للبوتين).

عند الهدف وبشرط «الدخول الصحيح»: بِع النصف واترك النصف (runner) بوقف يرتفع بمراحل من قمة ربح العقد.
دوال صافية؛ الأرباح كنسب من سعر الدخول (0.5 = +50%).
"""
TROUGH_MAX = 0.25                      # أدنى ربح منذ الدخول أعلى من −25%
ADV_HALF = 0.5                         # أو لم يتحرك الأصل ضدك أكثر من نصف مسافة الوقف الهيكلي
STAGES = ((1.0, 0.5), (2.0, 1.0))      # قمة +100% ← وقف +50%، قمة +200% ← وقف +100%
TRAIL_FROM, TRAIL_KEEP = 3.0, 0.6      # من قمة +300%: الوقف يحفظ 60% من ربح القمة


def track(p, pnl, adverse=None):
    """يحدّث أدنى ربح وأقصى حركة ضد الصفقة أثناء حياتها (قبل الهدف)."""
    if pnl < p.get("trough", 0.0):
        p["trough"] = round(pnl, 4)
    if adverse is not None and adverse > p.get("adv_max", 0.0):
        p["adv_max"] = round(adverse, 5)


def eligible(p, stop_frac=None):
    """الدخول صحيح: لم يقترب العقد من الوقف قبل الهدف."""
    if p.get("trough", 0.0) > -TROUGH_MAX:
        return True
    return bool(stop_frac) and p.get("adv_max", 0.0) < ADV_HALF * stop_frac


def start(pnl, t_iso):
    return {"open": True, "half_pnl": round(pnl, 4), "lock": 0.0, "peak": round(pnl, 4), "stage": 0, "t": t_iso}


def lock_for(peak):
    lk = 0.0
    for g, v in STAGES:
        if peak >= g:
            lk = v
    if peak >= TRAIL_FROM:
        lk = max(lk, TRAIL_KEEP * peak)
    return lk


def px(entry, g):
    return entry * (1 + g)


def step(rn, pnl, entry, t_iso, time_exit=False):
    """خطوة على الباقي: [(النوع، النص)] (runner_exit أو lock). يعدّل rn."""
    if not rn or not rn.get("open"):
        return []
    rn["peak"] = round(max(rn["peak"], pnl), 4)
    if pnl <= rn["lock"] or time_exit:
        why = "lock" if pnl <= rn["lock"] else "time"
        rn.update(open=False, exit_pnl=round(pnl, 4), exit_t=t_iso, why=why)
        tot = total(rn)
        head = (f"كسر وقف الباقي ({px(entry, rn['lock']):.2f})." if why == "lock"
                else "انتهى وقت الاحتفاظ اليوم: الخروج بقاعدة لا بالانتظار.")
        return [("runner_exit", f"{head} أغلق النصف الباقي الآن ({pnl * 100:+.0f}%). "
                                f"نتيجة الصفقة كاملة: نصف {rn['half_pnl'] * 100:+.0f}% ونصف {pnl * 100:+.0f}% = {tot * 100:+.0f}%.")]
    nl = lock_for(rn["peak"])
    if nl > rn["lock"] + 1e-9:
        rn["lock"], rn["stage"] = round(nl, 4), rn.get("stage", 0) + 1
        return [("lock", f"العقد بلغ {rn['peak'] * 100:+.0f}%. ارفع وقف النصف الباقي إلى {px(entry, nl):.2f} "
                         f"(يحفظ {nl * 100:+.0f}%).")]
    return []


def total(rn):
    """نتيجة الصفقة: نصف بالهدف ونصف بخروج الباقي (أو None إن لم يخرج)."""
    if rn.get("exit_pnl") is None:
        return None
    return round(0.5 * rn["half_pnl"] + 0.5 * rn["exit_pnl"], 4)


def tp_text(pnl, entry):
    return (f"وصل الهدف ({pnl * 100:+.0f}%) والدخول كان سليماً (لم يقترب من الوقف). الخطة: بِع نصف العقود الآن، "
            f"واترك النصف بوقف على سعر دخولك {entry:.2f}. سأرفع الوقف معك بمراحل.")
