"""محرك هيكل السوق (H-SMC، المرحلة 1).

يقرأ الشموع بالترتيب شمعةً شمعة كما يراها المتداول لحظتها: القمة أو القاع لا يُعرف إلا بعد
n شموع تؤكده، ولا يُستعمل أي سعر لاحق. التعريفات مسجّلة في CLAUDE.md (H-SMC):
الاتجاه، وBOS/MSS بالإغلاق، والقاع/القمة المحمية، وOrder Block عند منبع الساق، وFVG وقوتها،
والسيولة (القمم والقيعان وEQH/EQL) والسويب، والاستدراج IDM، والمنطقة الغالية/الرخيصة.

الاستعمال:
    st = analyze(df)            # df بأعمدة Open/High/Low/Close وفهرس زمني
    snap = snapshot(st)         # ملخص اللحظة الأخيرة
    mtf({"1h": df1h, "15m": df15})
"""
from __future__ import annotations

import pandas as pd

OB_LOOKBACK = 5      # البحث عن آخر شمعة معاكسة قبل منبع الساق
EQ_WINDOW = 60       # أقصى بُعد (بالشموع) بين قمتين متساويتين


def _atr(df, n=14):
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=1).mean().to_numpy(float)


def analyze(df, n=3, eq_atr=0.1, upto=None):
    """يمشي على الشموع حتى `upto` (غير شامل) ويرجع حالة الهيكل كاملة."""
    o, h, l, c = (df[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    atr = _atr(df)
    N = len(df) if upto is None else min(upto, len(df))
    t = list(df.index)
    st = {"n": n, "N": N, "trend": None, "protected": None, "prot_i": None, "events": [], "swings": [],
          "fvgs": [], "obs": [], "liq": [], "sweeps": [], "idm": None, "idms": [], "times": t[:N],
          "close": float(c[N - 1]) if N else None}
    sh = sl = None          # أبعد قمة/قاع مؤكد لم يُكسر بإغلاق (هدف الـBOS)

    def event(i, kind, d, level, origin):
        if d == "up":
            prot, ob = float(l[origin]), _ob(o, h, l, c, origin, "up")
        else:
            prot, ob = float(h[origin]), _ob(o, h, l, c, origin, "down")
        disp = any(f["dir"] == d and origin < f["i"] <= i for f in st["fvgs"])
        st["events"].append({"i": i, "t": t[i], "kind": kind, "dir": d, "level": float(level),
                             "origin": origin, "protected": prot, "fvg": disp})
        st["trend"], st["protected"], st["prot_i"] = d, prot, origin
        ob.update(event_i=i, kind=kind, fvg=disp)
        st["obs"].append(ob)
        if st["idm"] and st["idm"]["px"] is None:
            st["idm"]["state"] = "skipped"
        st["idm"] = {"dir": d, "after": i, "px": None, "i": None, "state": "wait"}
        st["idms"].append(st["idm"])

    for i in range(N):
        # 1) تأكيد القمة/القاع عند j = i − n
        j = i - n
        if j >= n:
            if h[j] > h[j - n:j].max() and h[j] >= h[j + 1:i + 1].max():
                s = {"i": j, "conf": i, "kind": "H", "px": float(h[j])}
                st["swings"].append(s)
                _add_liq(st, s, "buy", eq_atr * atr[i])
                if sh is None or sh.get("broken") or s["px"] > sh["px"]:
                    sh = dict(s)
                idm = st["idm"]
                if idm and idm["dir"] == "down" and idm["px"] is None and j > idm["after"]:
                    idm.update(px=s["px"], i=j, state="open")
            if l[j] < l[j - n:j].min() and l[j] <= l[j + 1:i + 1].min():
                s = {"i": j, "conf": i, "kind": "L", "px": float(l[j])}
                st["swings"].append(s)
                _add_liq(st, s, "sell", eq_atr * atr[i])
                if sl is None or sl.get("broken") or s["px"] < sl["px"]:
                    sl = dict(s)
                idm = st["idm"]
                if idm and idm["dir"] == "up" and idm["px"] is None and j > idm["after"]:
                    idm.update(px=s["px"], i=j, state="open")

        # 2) الكسر بالإغلاق
        tr = st["trend"]
        if tr == "up":
            if c[i] < st["protected"]:                       # MSS هابط: إغلاق تحت القاع المحمي
                origin = st["prot_i"] + int(h[st["prot_i"]:i + 1].argmax())
                event(i, "MSS", "down", st["protected"], origin)
                sl = None
            elif sh and not sh.get("broken") and c[i] > sh["px"]:
                origin = sh["i"] + int(l[sh["i"]:i + 1].argmin())
                event(i, "BOS", "up", sh["px"], origin)
                sh["broken"] = True
        elif tr == "down":
            if c[i] > st["protected"]:                       # MSS صاعد: إغلاق فوق القمة المحمية
                origin = st["prot_i"] + int(l[st["prot_i"]:i + 1].argmin())
                event(i, "MSS", "up", st["protected"], origin)
                sh = None
            elif sl and not sl.get("broken") and c[i] < sl["px"]:
                origin = sl["i"] + int(h[sl["i"]:i + 1].argmax())
                event(i, "BOS", "down", sl["px"], origin)
                sl["broken"] = True
        else:
            if sh and not sh.get("broken") and c[i] > sh["px"]:
                event(i, "BOS", "up", sh["px"], sh["i"] + int(l[sh["i"]:i + 1].argmin()))
                sh["broken"] = True
            elif sl and not sl.get("broken") and c[i] < sl["px"]:
                event(i, "BOS", "down", sl["px"], sl["i"] + int(h[sl["i"]:i + 1].argmax()))
                sl["broken"] = True

        # 3) تحديث FVG القائمة ثم رصد الجديدة
        for f in st["fvgs"]:
            if f["state"] == "broken" or f["i"] >= i:
                continue
            if f["dir"] == "up" and l[i] <= f["top"]:
                cls = "weak" if c[i] < f["bot"] else ("normal" if c[i] <= f["top"] else "strong")
            elif f["dir"] == "down" and h[i] >= f["bot"]:
                cls = "weak" if c[i] > f["top"] else ("normal" if c[i] >= f["bot"] else "strong")
            else:
                continue
            if f["touch"] is None:
                f.update(touch=cls, touch_i=i)
            if cls == "weak":
                f.update(state="broken", broken_i=i)
            else:
                f["state"] = "tested"
        if i >= 2:
            if l[i] > h[i - 2]:
                st["fvgs"].append(_fvg("up", h[i - 2], l[i], i))
            elif h[i] < l[i - 2]:
                st["fvgs"].append(_fvg("down", h[i], l[i - 2], i))

        # 4) Order Blocks: اختبار 50% أو كسر بالإغلاق
        for b in st["obs"]:
            if b["state"] == "broken" or b["event_i"] >= i:
                continue
            if b["dir"] == "up":
                if c[i] < b["bot"]:
                    b.update(state="broken", broken_i=i)
                elif l[i] <= b["mid"] and b["state"] == "fresh":
                    b.update(state="tested", test_i=i)
            else:
                if c[i] > b["top"]:
                    b.update(state="broken", broken_i=i)
                elif h[i] >= b["mid"] and b["state"] == "fresh":
                    b.update(state="tested", test_i=i)

        # 5) السيولة: سويب (ذيل يتجاوز والإغلاق يرجع) أو أُخذت بالكسر
        for q in st["liq"]:
            if q["state"] != "open" or q["conf"] >= i:
                continue
            if q["side"] == "buy" and h[i] > q["px"]:
                q.update(state="swept" if c[i] <= q["px"] else "taken", at=i)
            elif q["side"] == "sell" and l[i] < q["px"]:
                q.update(state="swept" if c[i] >= q["px"] else "taken", at=i)
            else:
                continue
            if q["state"] == "swept":
                st["sweeps"].append({"i": i, "t": t[i], "side": q["side"], "px": q["px"], "eq": q["eq"]})

        # 6) IDM: يُعد مأخوذاً عند تجاوزه
        idm = st["idm"]
        if idm and idm["state"] == "open" and i > idm["i"] + n - 1:
            if (idm["dir"] == "up" and l[i] < idm["px"]) or (idm["dir"] == "down" and h[i] > idm["px"]):
                idm.update(state="taken", taken_i=i)

    st["pd"] = _premium_discount(st, h, l, N)
    return st


def _fvg(d, bot, top, i):
    bot, top = float(bot), float(top)
    return {"dir": d, "bot": bot, "top": top, "ce": (bot + top) / 2, "i": i, "state": "fresh", "touch": None}


def _ob(o, h, l, c, origin, d):
    """آخر شمعة معاكسة عند منبع الساق (حتى OB_LOOKBACK قبله)؛ وإلا شمعة المنبع نفسها."""
    k = origin
    for x in range(origin, max(-1, origin - OB_LOOKBACK - 1), -1):
        if (d == "up" and c[x] < o[x]) or (d == "down" and c[x] > o[x]):
            k = x
            break
    if d == "up":
        bot, top = float(min(l[k], l[origin])), float(h[k])
    else:
        bot, top = float(l[k]), float(max(h[k], h[origin]))
    return {"dir": d, "i": k, "bot": bot, "top": top, "mid": (bot + top) / 2, "state": "fresh"}


def _add_liq(st, s, side, tol):
    eq = False
    for q in reversed(st["liq"]):
        if q["side"] != side or s["i"] - q["i"] > EQ_WINDOW:
            continue
        if q["state"] == "open" and abs(q["px"] - s["px"]) <= tol:
            eq = q["eq"] = True
            break
    st["liq"].append({"side": side, "px": s["px"], "i": s["i"], "conf": s["conf"], "eq": eq, "state": "open"})


def _premium_discount(st, h, l, N):
    if st["trend"] is None or not N:
        return None
    k = st["prot_i"]
    if st["trend"] == "up":
        lo, hi = st["protected"], float(h[k:N].max())
    else:
        lo, hi = float(l[k:N].min()), st["protected"]
    eq = (lo + hi) / 2
    return {"lo": lo, "hi": hi, "eq": eq, "zone": "premium" if st["close"] > eq else "discount"}


def snapshot(st):
    """ملخص اللحظة الأخيرة: ما يحتاجه المتداول والبوت."""
    px = st["close"]
    if px is None:
        return None
    above = [q for q in st["liq"] if q["state"] == "open" and q["side"] == "buy" and q["px"] > px]
    below = [q for q in st["liq"] if q["state"] == "open" and q["side"] == "sell" and q["px"] < px]
    act = lambda xs: [x for x in xs if x["state"] != "broken"]
    last = st["events"][-1] if st["events"] else None
    fvgs = act(st["fvgs"])
    obs = act(st["obs"])
    return {
        "trend": st["trend"], "protected": st["protected"], "last": last,
        "liq_above": min(above, key=lambda q: q["px"]) if above else None,
        "liq_below": max(below, key=lambda q: q["px"]) if below else None,
        "eq_above": min((q for q in above if q["eq"]), key=lambda q: q["px"], default=None),
        "eq_below": max((q for q in below if q["eq"]), key=lambda q: q["px"], default=None),
        "fvg": fvgs[-1] if fvgs else None,
        "fvg_with": next((f for f in reversed(fvgs) if f["dir"] == st["trend"]), None),
        "ob": next((b for b in reversed(obs) if b["dir"] == st["trend"]), None),
        "idm": st["idm"], "pd": st["pd"],
        "sweep": st["sweeps"][-1] if st["sweeps"] else None,
    }


def mtf(frames, n=3):
    """frames: {"1d": df, "1h": df, ...} بترتيب من الأكبر للأصغر. يرجع ملخص كل فريم وهل تتوافق."""
    out = {k: snapshot(analyze(df, n=n)) for k, df in frames.items()}
    trends = [s["trend"] for s in out.values() if s]
    out["aligned"] = trends[0] if trends and all(x == trends[0] for x in trends) else None
    return out


TREND_AR = {"up": "صاعد", "down": "هابط", None: "غير محدد"}


def text_ar(snap, fmt="{:,.2f}"):
    """سطر مختصر للبطاقة أو التنبيه."""
    if not snap:
        return "الهيكل: لا بيانات"
    f = fmt.format
    parts = [f"الاتجاه {TREND_AR[snap['trend']]}"]
    if snap["protected"] is not None:
        parts.append(("القاع المحمي " if snap["trend"] == "up" else "القمة المحمية ") + f(snap["protected"]))
    if snap["liq_above"]:
        parts.append("سيولة فوق " + f(snap["liq_above"]["px"]) + (" (EQH)" if snap["liq_above"]["eq"] else ""))
    if snap["liq_below"]:
        parts.append("سيولة تحت " + f(snap["liq_below"]["px"]) + (" (EQL)" if snap["liq_below"]["eq"] else ""))
    if snap["pd"]:
        parts.append("السعر في المنطقة " + ("الغالية" if snap["pd"]["zone"] == "premium" else "الرخيصة"))
    idm = snap["idm"]
    if idm and idm["state"] in ("open", "taken"):
        parts.append("الاستدراج " + ("أُخذ" if idm["state"] == "taken" else "لم يُؤخذ بعد ") +
                     ("" if idm["state"] == "taken" else f(idm["px"])))
    return " | ".join(parts)
