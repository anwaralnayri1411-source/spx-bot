"""لوحة تأكيد الكسر بتصويت المجموعات (H-BHVOTE، مسجّل مسبقاً في CLAUDE.md 11/11/2026؛ الإصدار 21 بقرار المستخدم). دوال صافية.

خمس مجموعات، لكل مجموعة صوت واحد في جهة الحدث (+1 مع، −1 ضد، 0 بلا بيانات أو تعادل):
الاتجاه (VWAP، EMA9/21، MACD، حجم شمعة الكسر)، المال (العلاوة، الدلتا، تضخم IV)، المستويات (تراكم وفيبوناتشي ومساحة)،
السوق الأوسع (ES، الثقيلة، VIX)، الأخبار والآراء (الصدمة، الصقر، المتداول الذكي، محرك MGT).
يُعتمد الحدث إذا (الموافقة − المخالفة) ≥ العتبة؛ العتبة من نوع اليوم (اتجاه 1، عادي 2، تذبذب 3) و+1 وقت الغداء.
"""
GROUPS = ("الاتجاه", "المال", "المستويات", "السوق الأوسع", "الأخبار والآراء")
STACK_TOL = 3.0          # نقاط حول المستوى لعدّ المستويات المتراكمة
ROOM_MIN = 10.0          # مساحة أقل من هذا إلى أقرب مستوى معاكس = صوت ضد
RR_MIN = 1.5             # الهدف ≥ 1.5 ضعف المخاطرة
RISK_PAD = 5.0           # الوقف خلف المستوى المكسور بـ5 نقاط
RVOL_MIN = 1.5
ADX_TREND = 25.0
ORB_WIDE = 0.004
LUNCH = (12 * 60, 13 * 60 + 30)
THR = {"trend": 1, "normal": 2, "range": 3}
DTYPE_AR = {"trend": "يوم اتجاه", "normal": "يوم عادي", "range": "يوم تذبذب"}


def sgn(x, eps=0.0):
    if x is None or x != x or abs(x) <= eps:
        return 0
    return 1 if x > 0 else -1


def _ema(xs, span):
    k, out, e = 2 / (span + 1), [], None
    for x in xs:
        e = x if e is None else x * k + e * (1 - k)
        out.append(e)
    return out


def macd_hist(closes, fast=12, slow=26, sig=9):
    """آخر قيمة لهيستوغرام MACD، أو None إن قلّت الشموع."""
    if len(closes) < slow + sig:
        return None
    m = [a - b for a, b in zip(_ema(closes, fast), _ema(closes, slow))]
    return m[-1] - _ema(m, sig)[-1]


def adx(highs, lows, closes, n=14):
    """ADX بطريقة وايلدر، أو None إن قلّت الشموع."""
    if len(closes) < 2 * n + 1:
        return None
    tr, pdm, ndm = [], [], []
    for i in range(1, len(closes)):
        up, dn = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        pdm.append(up if up > dn and up > 0 else 0.0)
        ndm.append(dn if dn > up and dn > 0 else 0.0)
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    s_tr, s_p, s_n = sum(tr[:n]), sum(pdm[:n]), sum(ndm[:n])
    dx = []
    for i in range(n, len(tr) + 1):
        if i > n:
            s_tr = s_tr - s_tr / n + tr[i - 1]
            s_p = s_p - s_p / n + pdm[i - 1]
            s_n = s_n - s_n / n + ndm[i - 1]
        if s_tr <= 0:
            dx.append(0.0)
            continue
        pdi, ndi = 100 * s_p / s_tr, 100 * s_n / s_tr
        dx.append(0.0 if pdi + ndi == 0 else 100 * abs(pdi - ndi) / (pdi + ndi))
    if len(dx) < n:
        return None
    a = sum(dx[:n]) / n
    for x in dx[n:]:
        a = (a * (n - 1) + x) / n
    return a


def fib_levels(day_hl):
    """مستويات فيبوناتشي 38.2/50/61.8% من مدى اليوم: [(السعر، الاسم)]."""
    if not day_hl:
        return []
    hi, lo = day_hl
    if hi - lo <= 0:
        return []
    return [(lo + (hi - lo) * r, f"فيبوناتشي {r * 100:g}%") for r in (0.382, 0.5, 0.618)]


def stacked(L, cands, tol=STACK_TOL):
    """أسماء المستويات المختلفة حول L (±tol)."""
    names = []
    for p, nm in cands:
        if p is not None and abs(p - L) <= tol and nm not in names:
            names.append(nm)
    return names


def nearest_opp(side, S, cands, min_gap=1.0):
    """(السعر، الاسم) لأقرب مستوى معاكس بعد السعر في جهة الصفقة، أو None."""
    if side == "CALL":
        return min(((p, nm) for p, nm in cands if p is not None and p > S + min_gap), default=None)
    return max(((p, nm) for p, nm in cands if p is not None and p < S - min_gap), default=None)


def rr(S, L, opp):
    """(الهدف بالنقاط، المخاطرة، مقبول؟) أو None بلا مستوى معاكس."""
    if not opp:
        return None
    reward = abs(opp[0] - S)
    risk = abs(S - L) + RISK_PAD
    return round(reward, 1), round(risk, 1), reward >= RR_MIN * risk


def levels_vote(L, stack_cands, S, opp):
    """صوت المستويات في جهة الحدث: +1 متراكم (مستويان أو أكثر)، −1 مساحة أقل من ROOM_MIN؛ ومعاً 0."""
    v = (1 if len(stacked(L, stack_cands)) >= 2 else 0) - (1 if opp and abs(opp[0] - S) < ROOM_MIN else 0)
    return v


def maj(signs):
    return sgn(sum(s for s in signs if s))


def day_type(gex, adx_v, orb, S):
    k = int(gex is not None and gex < 0) + int(adx_v is not None and adx_v >= ADX_TREND)
    k += int(bool(orb) and S > 0 and (orb[0] - orb[1]) / S >= ORB_WIDE)
    return "trend" if k >= 2 else ("range" if k == 0 else "normal")


def threshold(dtype, minute):
    return THR[dtype] + (1 if LUNCH[0] <= minute < LUNCH[1] else 0)


def rel_volume(vols, i):
    """حجم الشمعة i ÷ وسيط الشموع قبلها اليوم، أو None (أقل من 6 شموع سابقة أو بلا حجم)."""
    prev = sorted(v for v in vols[:i] if v and v > 0)
    if len(prev) < 6 or i >= len(vols) or not vols[i]:
        return None
    mid = prev[len(prev) // 2]
    return vols[i] / mid if mid > 0 else None


def vote(side, members, lv):
    """members: {مجموعة: [إشارات مطلقة +1 صعود/−1 هبوط/0]} للمجموعات غير المستويات؛ lv: صوت المستويات في جهة الحدث.
    يرجع ({مجموعة: صوت في جهة الحدث}، النتيجة)."""
    g = 1 if side == "CALL" else -1
    out = {name: (lv if name == "المستويات" else maj(members.get(name, [])) * g) for name in GROUPS}
    return out, sum(out.values())


def vote_text(groups, score, thr, dtype):
    icon = {1: "✅", -1: "❌", 0: "➖"}
    return f"{'، '.join(f'{nm} {icon[v]}' for nm, v in groups.items())} | النتيجة {score:+d} والعتبة {thr} ({DTYPE_AR[dtype]})"
