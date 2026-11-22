"""H-REGIMEW (22/11/2026): نوع اليوم حياً (اتجاه/تذبذب) وأوزان الشاشات في نقاط بوت SPX. مسجّل في CLAUDE.md قبل الكود.

classify(c) يقرأ قاموس التحليل c من options_bot.analyze، وapply(rows, regime) يضرب نقاط كل شاشة بوزنها.
أي نقص في البيانات يعطي «غير واضح» فلا يتغير شيء.
"""
import csv
import os

TREND, CHOP, NONE = "trend", "chop", None
AR = {TREND: "اتجاه", CHOP: "تذبذب", NONE: "غير واضح"}
W = {
    TREND: {"نطاق أول 30 دقيقة": 1.3, "القياديات": 1.3, "حجم الكول/البوت": 1.3,
            "عقود SPX الآجلة (ES)": 1.2, "VIX اليوم": 1.2, "أمس": 1.2, "نقطة الألم": 0.0},
    CHOP: {"نطاق أول 30 دقيقة": 0.5, "القياديات": 0.7, "عقود SPX الآجلة (ES)": 0.7, "VIX اليوم": 0.7,
           "أمس": 0.7, "الجدران": 0.7, "نقطة الألم": 2.0},
}
MOVE, NEAR_VWAP = 0.003, 0.001
LOG = os.path.join(os.getenv("SPXBOT_DATA", "/opt/spxbot/data"), "regime", "log.csv")
COLS = ["time", "regime", "trend_votes", "chop_votes", "why", "bull", "bear", "bull0", "bear0", "side", "side0", "edge_ok", "edge_ok0"]


def classify(c):
    """يرجع (النوع، أصوات الاتجاه، أصوات التذبذب، الأسباب)."""
    n, S = c["n"], c["S"]
    if (n.hour, n.minute) < (10, 0) or not c.get("orb"):
        return NONE, 0, 0, []
    gex = (c.get("map") or {}).get("gex") or 0
    hi, lo = c["orb"]
    inside = lo <= S <= hi
    t, ch, why = 0, 0, []
    if gex < 0:
        t += 1
        why.append("غاما سالبة")
    elif gex > 0:
        ch += 1
        why.append("غاما موجبة")
    if not inside and c.get("orb_hold") is not None:
        t += 1
        why.append("كسر ثابت لنطاق الافتتاح")
    if inside and (n.hour, n.minute) >= (10, 30):
        ch += 1
        why.append("السعر داخل نطاق الافتتاح")
    op = c.get("day_open")
    if op and abs(S / op - 1) >= MOVE:
        t += 1
        why.append(f"تحرك {(S / op - 1) * 100:+.1f}% من الافتتاح")
    vw = c.get("vwap_spx")
    if vw and abs(S / vw - 1) <= NEAR_VWAP:
        ch += 1
        why.append("ملتصق بـVWAP")
    r = TREND if t >= 2 and t > ch else (CHOP if ch >= 2 and ch > t else NONE)
    return r, t, ch, why


def apply(rows, regime):
    w = W.get(regime) or {}
    return [(lab, b * w.get(lab, 1.0), br * w.get(lab, 1.0), note) for lab, b, br, note in rows]


def pain_hour(regime):
    return 11 if regime == CHOP else 13


def line(c):
    rg = c.get("regime")
    if not rg:
        return []
    r, _, _, why = rg
    if r is NONE:
        return [f"🧭 <b>نوع اليوم:</b> غير واضح ({'، '.join(why) or 'قبل 10:00'}) — الأوزان عادية.", ""]
    up = [k for k, v in W[r].items() if v > 1]
    dn = [k for k, v in W[r].items() if v < 1]
    return [f"🧭 <b>نوع اليوم: {AR[r]}</b> ({'، '.join(why)}) — رفع وزن: {'، '.join(up)} | خفض: {'، '.join(dn)}.", ""]


def log(n, c, bull, bear, bull0, bear0, min_edge):
    try:
        r, t, ch, why = c.get("regime") or (NONE, 0, 0, [])
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        new = not os.path.exists(LOG)
        with open(LOG, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(COLS)
            w.writerow([n.isoformat(timespec="minutes"), AR[r], t, ch, "؛".join(why), round(bull, 2), round(bear, 2),
                        round(bull0, 2), round(bear0, 2), "CALL" if bull > bear else "PUT", "CALL" if bull0 > bear0 else "PUT",
                        int(abs(bull - bear) >= min_edge), int(abs(bull0 - bear0) >= min_edge)])
    except Exception as e:  # noqa: BLE001
        print("سجل نوع اليوم:", e)
