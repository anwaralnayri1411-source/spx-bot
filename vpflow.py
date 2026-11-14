#!/usr/bin/env python3
"""البروفايل والتدفق (H-VPFLOW، مسجّل في CLAUDE.md 14/11/2026 قبل الكود بموافقة المستخدم). دوال صافية مشتركة بين المختبرين.

الشكل يقول «أين» (P، b، D، ترند من بروفايل الجلسة السابقة)، والامتصاص يقول «متى» (شمعة 5د بحجم كبير دخلت المنطقة
ورُفضت فعادت إلى جسم البروفايل). الامتصاص هنا من الحجم الحقيقي ورفض السعر، لا من دلتا المبادر (غير متاحة لنا لحظياً).
الشموع: DataFrame بفهرس وقت نيويورك وأعمدة o,h,l,c,v بسعر الأداة نفسها.
"""
import math
from datetime import time as dtime

import pandas as pd

BIN_PCT, VA_PCT = 0.0005, 0.70
TREND_VA, TREND_CLOSE = 0.60, 0.20
VOL_MULT, CLV_MIN, NODE_DEPTH = 1.5, 0.5, 0.15
RTH_START, RTH_END = dtime(9, 30), dtime(16, 0)
SHAPE_AR = {"P": "P (المشترون سيطروا؛ يدافعون عن المنطقة الرفيعة تحت)", "b": "b (البائعون سيطروا؛ يدافعون عن المنطقة الرفيعة فوق)",
            "D": "D (توازن؛ بيع من الطرف الأعلى وشراء من الأدنى)", "trend_up": "ترند صاعد (لا نعاكسه)",
            "trend_down": "ترند هابط (لا نعاكسه)"}


def lower(df):
    """توحيد الأعمدة إلى o,h,l,c,v (شموع ياهو للشركات بأسماء كاملة)."""
    m = {"Open": "o", "High": "h", "Low": "l", "Close": "c", "Volume": "v"}
    return df.rename(columns=m)[["o", "h", "l", "c", "v"]]


def rth(df, day):
    """شموع الجلسة الرسمية ليوم day (date)."""
    t = df.index.time
    return df[(df.index.date == day) & (t >= RTH_START) & (t < RTH_END)]


def prev_session(df, today):
    """شموع آخر جلسة رسمية قبل today، أو None."""
    days = sorted({d for d in df.index.date if d < today})
    for d in reversed(days):
        s = rth(df, d)
        if len(s) >= 10 and s["v"].sum() > 0:
            return s
    return None


def profile(sess, bin_pct=BIN_PCT):
    """بروفايل جلسة: حجم كل شمعة موزّع بالتساوي على خانات مداها. {poc,vah,val,hi,lo,open,close,w,lvl,vol} أو None."""
    d = sess[(sess["v"] > 0) & sess["h"].notna()]
    if len(d) < 10:
        return None
    hi, lo = float(d["h"].max()), float(d["l"].min())
    w = float(d["c"].iloc[-1]) * bin_pct
    if w <= 0 or hi <= lo:
        return None
    base = math.floor(lo / w)
    n = math.floor(hi / w) - base + 1
    vol = [0.0] * n
    for h, l, v in zip(d["h"], d["l"], d["v"]):
        k0, k1 = math.floor(l / w) - base, math.floor(h / w) - base
        share = float(v) / (k1 - k0 + 1)
        for k in range(k0, k1 + 1):
            vol[k] += share
    i = max(range(n), key=lambda k: vol[k])
    a = b = i
    tot, acc = sum(vol), vol[i]
    while acc < VA_PCT * tot and (a > 0 or b < n - 1):
        up = vol[b + 1] if b < n - 1 else -1
        dn = vol[a - 1] if a > 0 else -1
        if up >= dn:
            b += 1
            acc += up
        else:
            a -= 1
            acc += dn
    lvl = [(base + k + 0.5) * w for k in range(n)]
    return {"poc": lvl[i], "vah": min((base + b + 1) * w, hi), "val": max((base + a) * w, lo), "hi": hi, "lo": lo,
            "open": float(d["o"].iloc[0]), "close": float(d["c"].iloc[-1]), "w": w, "lvl": lvl, "vol": vol}


def shape(pf):
    """P أو b أو D أو trend_up أو trend_down."""
    R = pf["hi"] - pf["lo"]
    cpos = (pf["close"] - pf["lo"]) / R
    if (pf["vah"] - pf["val"]) / R >= TREND_VA and (cpos >= 1 - TREND_CLOSE or cpos <= TREND_CLOSE):
        return "trend_up" if cpos >= 1 - TREND_CLOSE else "trend_down"
    pos = (pf["poc"] - pf["lo"]) / R
    return "P" if pos > 2 / 3 else ("b" if pos < 1 / 3 else "D")


def zones(pf, shp):
    """مناطق اليوم: {key، side، L المستوى، F الطرف البعيد، t1، t2، trend}. المنطقة بعرض أقل من خانة تُهمل."""
    R, w = pf["hi"] - pf["lo"], pf["w"]
    out = []

    def add(side, L, F, t1, t2, trend=False):
        if abs(L - F) >= w:
            out.append({"key": f"{shp}:{side}", "side": side, "L": round(L, 2), "F": round(F, 2),
                        "t1": None if t1 is None else round(t1, 2), "t2": None if t2 is None else round(t2, 2), "trend": trend})
    if shp in ("P", "D"):
        add("CALL", pf["val"], pf["lo"], pf["poc"], pf["hi"])
    if shp in ("b", "D"):
        add("PUT", pf["vah"], pf["hi"], pf["poc"], pf["lo"])
    if shp in ("trend_up", "trend_down"):
        up = shp == "trend_up"
        third = [k for k, x in enumerate(pf["lvl"]) if (x >= pf["lo"] + 2 * R / 3 if up else x <= pf["lo"] + R / 3)]
        if third:
            k = max(third, key=lambda j: pf["vol"][j])
            L = pf["lvl"][k] + (w / 2 if up else -w / 2)
            add("CALL" if up else "PUT", L, L - NODE_DEPTH * R if up else L + NODE_DEPTH * R, None, None, True)
    return out


def _sg(side):
    return 1 if side == "CALL" else -1


def absorbed(bar, z, med_vol):
    """شمعة امتصاص: دخلت المستوى وأغلقت عائدة إلى الجسم، بحجم ≥ 1.5× الوسيط، وإغلاق في نصف الرفض."""
    o, h, l, c, v = (float(bar[k]) for k in ("o", "h", "l", "c", "v"))
    if not med_vol or med_vol <= 0 or h <= l:
        return False
    if z["side"] == "CALL":
        loc, clv = l <= z["L"] and c >= z["L"], (c - l) / (h - l)
    else:
        loc, clv = h >= z["L"] and c <= z["L"], (h - c) / (h - l)
    return loc and v >= VOL_MULT * med_vol and clv >= CLV_MIN


def inside(bar, z):
    """إغلاق داخل المنطقة [F، L] (p1)."""
    c = float(bar["c"])
    lo, hi = sorted((z["L"], z["F"]))
    return lo <= c <= hi


def beyond(c, z):
    return (c - z["F"]) * _sg(z["side"]) < 0


def scan(bars5, z, mode, fresh_from, first_entry):
    """أول إشارة صالحة للمنطقة في شموع اليوم المكتملة (5د): {t، stop} أو None.
    تُفحص الشموع بالترتيب؛ إغلاقان متتاليان خلف F يميتان المنطقة. الإشارة تُقبل فقط لشمعة بدأت ≥ fresh_from و≥ first_entry."""
    streak = 0
    vols = []
    for t, b in bars5.iterrows():
        if float(b["v"]) > 0:
            vols.append(float(b["v"]))
        ok_time = t >= fresh_from and t >= first_entry
        if mode == "p0":
            med = float(pd.Series(vols).median()) if vols else 0.0
            trig = absorbed(b, z, med)
            stop = float(b["l"]) if z["side"] == "CALL" else float(b["h"])
        else:
            trig = inside(b, z)
            stop = z["F"]
        if trig and ok_time:
            return {"t": t, "stop": round(stop, 2)}
        streak = streak + 1 if beyond(float(b["c"]), z) else 0
        if streak >= 2:
            return None
    return None


def manage_levels(e, bid, px, c5, now, force_at, stop_pct):
    """إدارة بمستويات السعر (P، b، D). e: entry، side، stop_px، t1، t2، [half، half_ret]. px السعر الآن، c5 إغلاق آخر شمعة 5د
    مكتملة بعد الدخول أو None. يرجع (مغلق؟، سبب) ويضع e['ret'] كنسبة من سعر الدخول عند الإغلاق."""
    E, sg = e["entry"], _sg(e["side"])
    e["peak"] = max(e.get("peak", E), bid)
    r = bid / E - 1
    force = now.date().isoformat() > e["tdate"] or now.time() >= force_at

    def close(why):
        e["ret"] = round(e.get("half_ret", 0) + (0.5 if e.get("half") else 1.0) * r, 4)
        return True, why
    if not e.get("half"):
        if bid <= E * (1 - stop_pct):
            return close("وقف العقد")
        if c5 is not None and (c5 - e["stop_px"]) * sg < 0:
            return close("وقف السعر")
        if e.get("t2") is not None and (px - e["t2"]) * sg >= 0:
            return close("الهدف الثاني")
        if e.get("t1") is not None and (px - e["t1"]) * sg >= 0:
            e["half"], e["half_ret"] = True, round(0.5 * r, 4)
            return False, ""
        return close("وقت") if force else (False, "")
    if bid <= E:
        return close("وقف الدخول")
    if e.get("t2") is not None and (px - e["t2"]) * sg >= 0:
        return close("الهدف الثاني")
    return close("وقت") if force else (False, "")


def manage_trend(e, bid, broken, now, force_at, stop_pct):
    """خروج الترند كما في s1: وقف العقد قبل +100%؛ عنده النصف ووقف الباقي على الدخول ثم مراحل؛ كسر الهيكل أو الوقت."""
    E = e["entry"]
    e["peak"] = max(e.get("peak", E), bid)
    r = bid / E - 1
    force = now.date().isoformat() > e["tdate"] or now.time() >= force_at
    if not e.get("half"):
        if bid <= E * (1 - stop_pct):
            e["ret"] = round(r, 4)
            return True, "وقف العقد"
        if bid >= 2 * E:
            e["half"], e["half_ret"], e["sl"] = True, round(0.5 * r, 4), E
            return False, ""
        if force:
            e["ret"] = round(r, 4)
            return True, "وقت"
        return False, ""
    sl = e.get("sl", E)
    why = "وقف مرحلي" if bid <= sl else ("كسر الهيكل" if broken else ("وقت" if force else ""))
    if why:
        e["ret"] = round(e["half_ret"] + 0.5 * r, 4)
        return True, why
    g = e["peak"] / E - 1
    sl = max(sl, E)
    if g >= 2:
        sl = max(sl, 1.5 * E)
    if g >= 3:
        sl = max(sl, 0.6 * e["peak"])
    e["sl"] = sl
    return False, ""


def struct_broken(bars5, side, since, n=3):
    """هل ظهر بعد since حدث هيكل (MSS/BOS) معاكس على شموع 5د المكتملة؟"""
    import smc
    if bars5 is None or len(bars5) < 2 * n + 1:
        return False
    st = smc.analyze(bars5.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close"}), n=n)
    against = "down" if side == "CALL" else "up"
    t0 = pd.Timestamp(since)
    return any(ev["dir"] == against and pd.Timestamp(ev["t"]) + pd.Timedelta(minutes=5) > t0 for ev in st["events"])


def completed5(df1_or5, now, rule=None):
    """شموع 5د مكتملة لجلسة اليوم الرسمية (يُعاد التجميع إن أُعطيت شموع دقيقة)."""
    d = df1_or5
    if rule:
        d = d.resample(rule, label="left", closed="left").agg({"o": "first", "h": "max", "l": "min", "c": "last", "v": "sum"}).dropna()
    d = rth(d, now.date())
    return d[d.index + pd.Timedelta(minutes=5) <= now]


def plan(df, today):
    """(بروفايل الأمس، الشكل، المناطق) أو (None، None، [])."""
    s = prev_session(df, today)
    pf = profile(s) if s is not None else None
    if not pf:
        return None, None, []
    shp = shape(pf)
    return pf, shp, zones(pf, shp)


def plan_line(name, pf, shp, zs, fmt="{:,.2f}"):
    """سطر الرسالة الصباحية لأداة واحدة."""
    if not pf:
        return f"- {name}: لا بيانات كافية"
    f = fmt.format
    zt = "؛ ".join(f"{'شراء' if z['side'] == 'CALL' else 'بيع'} عند {f(z['L'])} (إبطال خلف {f(z['F'])})" for z in zs) or "لا منطقة"
    return f"- **{name}**: {SHAPE_AR[shp]} · POC {f(pf['poc'])} · القيمة {f(pf['val'])}–{f(pf['vah'])}\n   {zt}"
