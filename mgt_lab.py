#!/usr/bin/env python3
"""مختبر التطوّر (H-MGTLAB، مسجّل في CLAUDE.md 5/11/2026 بطلب المستخدم: «يطور نفسه ويصنع أهدافه… فأر تجارب»).

12 نسخة ورقية من ظل MGT، لكل نسخة معاييرها (عتبة القرار، ADX، سعر العقد، الوقف، الهدف، السلّم، الجلسات، الزناد) ودفترها.
تعمل كل دقيقة على البيانات اللحظية نفسها التي يجمعها ظل MGT (عيّنات ES والفرق الأساسي)، وتخرج بقواعدها هي.
بعد 16:05 كل يوم تداول: ترتيب بالدولار المحقق (آخر 5 أيام عقود)، تبقى الأفضل، وتُستبدل الأسوأ بطفرات منها ونسخة عشوائية.
النسخة v0 = ظل MGT الأصلي ضابطة لا تتغير. ورق فقط، لا Claude، لا رسائل توصية. الملفات: SPXBOT_DATA/mgt_lab/
تشغيل كل دقيقة بعد ظل MGT: python mgt_lab.py [--force] [--no-send]
"""
import json
import os
import random
import sys
from datetime import date, datetime, timedelta

import pandas as pd

import mgt_shadow as ms
import smc
import vpflow as vf

NY = ms.NY
DIR = os.path.join(os.getenv("SPXBOT_DATA", "/opt/spxbot/data"), "mgt_lab")
STATE, TRADES, GENS, AFTER = (os.path.join(DIR, f) for f in ("state.json", "trades.csv", "generations.md", "after_exit.csv"))
GRID = {"score": [2, 3, 4], "adx": [10, 15, 20, 25], "price": [1.5, 2.5, 3.5, 5.0, 7.0], "stop": [0.3, 0.4, 0.5, 0.6],
        "exit": ["tp30", "tp50", "tp100", "tp200", "half", "late", "late_half"], "ladder": [5, 10, 15], "cap": [3, 6, 12],
        "sess": ["both", "rth"], "trig": ["mml", "any"]}
CONTROL = {"score": 3, "adx": 15, "price": 3.5, "stop": 0.5, "exit": "tp50", "ladder": 5, "cap": 12, "sess": "both", "trig": "mml"}
N_VAR, KEEP, WINDOW, MIN_TRADES = 12, 6, 5, 3
CLOSE_AT = ms.dtime(15, 55)


# ====================== النسخ والتطور (دوال صافية) ======================
def random_params(rng):
    return {k: rng.choice(v) for k, v in GRID.items()}


def mutate(p, rng, n=None):
    q = dict(p)
    for k in rng.sample(list(GRID), n or rng.choice([1, 2])):
        opts = [v for v in GRID[k] if v != q[k]]
        q[k] = rng.choice(opts)
    return q


def ensure_exits(st, exits=("late", "late_half")):
    """تبقى نسخة لكل خيار خروج مراقَب: يُخصَّص من أحدث نسخة بلا صفقات، والضابطة لا تتغير."""
    for x in exits:
        if any(v["params"]["exit"] == x for v in st["variants"]):
            continue
        free = [v for v in st["variants"] if not v.get("control") and not v["open"] and not v["closed"]
                and v["params"]["exit"] not in exits]
        if free:
            free[-1]["params"]["exit"] = x


def initial_variants(day, seed=7):
    rng = random.Random(seed)
    vs = [{"vid": "v0", "born": day, "params": dict(CONTROL), "open": [], "closed": [], "control": True}]
    for i in range(1, N_VAR):
        p = mutate(CONTROL, rng, 2) if i <= 6 else random_params(rng)
        vs.append({"vid": f"v{i}", "born": day, "params": p, "open": [], "closed": []})
    return vs


def score(v, days):
    cl = [c for c in v["closed"] if c["tdate"] in days]
    return sum(c["usd"] for c in cl), len(cl)


def evolve(st, day, rng):
    """يرتّب ويستبدل الأسوأ. يرجع نص ما حدث."""
    tds = sorted({c["tdate"] for v in st["variants"] for c in v["closed"]})[-WINDOW:]
    ranked = sorted(st["variants"], key=lambda v: score(v, tds)[0], reverse=True)
    repl = [v for v in ranked if not v.get("control") and score(v, tds)[1] >= MIN_TRADES]
    n_out = max(0, min(len(repl) - KEEP, N_VAR - KEEP)) if len(repl) > KEEP else len(repl) // 2
    losers = repl[::-1][:n_out]
    parents = [v for v in ranked if v not in losers and not v.get("control")][:3] or [ranked[0]]
    st["gen"] = st.get("gen", 1) + 1
    changes = []
    lines = [f"## الجيل {st['gen']} ({day})", "الترتيب (آخر 5 أيام عقود):"]
    for v in ranked:
        s, n = score(v, tds)
        lines.append(f"- {v['vid']}{' (ضابطة)' if v.get('control') else ''}: {s:+,.0f}$ في {n} صفقة | {fmt(v['params'])}")
    for i, lo in enumerate(losers):
        par = parents[i % len(parents)]
        newp = random_params(rng) if i == len(losers) - 1 else mutate(par["params"], rng)
        lines.append(f"- استُبدلت {lo['vid']} ({score(lo, tds)[0]:+,.0f}$) ← {'عشوائية' if i == len(losers) - 1 else 'طفرة من ' + par['vid']}: {fmt(newp)}")
        changes.append({"old": lo["vid"], "parent": None if i == len(losers) - 1 else par["vid"]})
        lo.update(params=newp, born=day, open=[], closed=[], vid=f"{lo['vid'].split('g')[0]}g{st['gen']}")
    st["last_changes"] = changes
    if not losers:
        lines.append("- لا استبدال بعد (النسخ لم تبلغ 3 صفقات).")
    return "\n".join(lines)


def fmt(p):
    return (f"عتبة {p['score']} · ADX≥{p['adx']} · عقد {p['price']}$ · وقف {int(p['stop'] * 100)}% · خروج {p['exit']} · "
            f"سلّم {p['ladder']}ن×{p['cap']} · {'ليلي+رسمي' if p['sess'] == 'both' else 'رسمي'} · زناد {p['trig']}")


def plain(p):
    """إعدادات النسخة بكلمات مفهومة لرسالة تيليجرام."""
    out = {"half": "بيع نصف العقد عند +100% والباقي بوقف متحرك", "late": "رفع الوقف بمراحل متأخرة",
           "late_half": "بيع النصف عند +100% ورفع الوقف بمراحل متأخرة"}.get(p["exit"]) or f"هدف +{p['exit'][2:]}%"
    return (f"عقد بسعر {p['price']:g}$ · {out} · وقف {int(p['stop'] * 100)}%\n"
            f"   {'الجلسة الرسمية فقط' if p['sess'] != 'both' else 'ليلاً ونهاراً'} · تدخل بـ{p['score']} أصوات · "
            f"{'عند خطوط Murrey' if p['trig'] == 'mml' else 'دون انتظار خط'} · تضيف عقداً كل {p['ladder']} نقاط حتى {p['cap']}")


def side_for(p, total, adx_v):
    if adx_v != adx_v or adx_v < p["adx"]:
        return None
    return "CALL" if total >= p["score"] else ("PUT" if total <= -p["score"] else None)


def pick(chain, side, S, price):
    ok = [r for r in chain if r["side"] == side and 0.8 * price <= r["ask"] <= 1.2 * price and
          ((side == "CALL" and r["strike"] > S) or (side == "PUT" and r["strike"] < S))]
    return min(ok, key=lambda r: abs(r["ask"] - price)) if ok else None


def can_add(v, side, S, tdate):
    p = v["params"]
    mine = [e for e in v["open"] + v["closed"] if e["side"] == side and e["tdate"] == tdate]
    if len(mine) >= p["cap"]:
        return False
    if not mine:
        return True
    last = max(mine, key=lambda e: e["t"])
    return (S - last["S"]) * (1 if side == "CALL" else -1) >= p["ladder"]


def manage(e, p, bid, now):
    """قواعد خروج النسخة على مركز واحد (وحدة = عقد). يرجع (مغلق؟، سبب)."""
    E = e["entry"]
    e["peak"] = max(e.get("peak", E), bid)
    expiry = now.date().isoformat() >= e["tdate"] and now.time() >= CLOSE_AT
    if p["exit"] == "half" and e.get("half"):
        e["trail"] = max(e.get("trail", bid), bid)
        if bid <= e["trail"] * 0.5 or expiry:
            e["usd"] = round(e["half_usd"] + 0.5 * (bid - E) * 100, 2)
            return True, "متحرك" if not expiry else "انتهاء"
        return False, ""
    if p["exit"] in ("late", "late_half"):
        return manage_late(e, p, bid, expiry)
    if bid <= E * (1 - p["stop"]):
        e["usd"] = round((bid - E) * 100, 2)
        return True, "وقف"
    if p["exit"] == "half" and bid >= 2 * E:
        e["half"], e["half_usd"], e["trail"] = True, 0.5 * (bid - E) * 100, bid
        return False, ""
    if p["exit"].startswith("tp") and bid >= E * (1 + int(p["exit"][2:]) / 100):
        e["usd"] = round((bid - E) * 100, 2)
        return True, "هدف"
    if expiry:
        e["usd"] = round((bid - E) * 100, 2)
        return True, "انتهاء"
    return False, ""


def manage_late(e, p, bid, expiry):
    """المراحل المتأخرة (H-STOPSTAGES): +100% ← الدخول، +200% ← +50%، +300% ← 60% من القمة؛ ويرتفع الوقف بعد فحصه."""
    E = e["entry"]
    held = 0.5 if e.get("half") else 1.0
    sl = e.get("sl", E * (1 - p["stop"]))
    if bid <= sl or expiry:
        e["usd"] = round(e.get("half_usd", 0) + held * (bid - E) * 100, 2)
        return True, "انتهاء" if bid > sl else "وقف مرحلي"
    if p["exit"] == "late_half" and not e.get("half") and bid >= 2 * E:
        e["half"], e["half_usd"] = True, 0.5 * (bid - E) * 100
    g = e["peak"] / E - 1
    if g >= 1:
        sl = max(sl, E)
    if g >= 2:
        sl = max(sl, 1.5 * E)
    if g >= 3:
        sl = max(sl, 0.6 * e["peak"])
    e["sl"] = sl
    return False, ""


def track_after(st, qs, now):
    """أعلى عرض بلغه عقد كل صفقة مغلقة بعد خروجها. قياس فقط."""
    for v in all_variants(st):
        for c in v["closed"]:
            q = qs.get(c.get("symbol"))
            if q and "exit_px" in c and q["bid"] > c.get("after_hi", 0):
                c["after_hi"], c["after_hi_t"] = q["bid"], now.strftime("%H:%M")


def tracked_symbols(st, day):
    return {c["symbol"] for v in all_variants(st) for c in v["closed"] if "exit_px" in c and c["tdate"] >= day}


def after_rows(st, day):
    return [{"vid": v["vid"], "tdate": c["tdate"], "t": c["t"], "side": c["side"], "strike": c["strike"], "entry": c["entry"],
             "exit": c["exit_px"], "exit_t": c.get("exit_t"), "after_hi": c.get("after_hi"), "after_hi_t": c.get("after_hi_t"), "usd": c["usd"]}
            for v in all_variants(st) for c in v["closed"] if c["tdate"] == day and "exit_px" in c]


def left_text(v, day):
    """متوسط ما بلغه العقد فوق سعر البيع بعد الخروج، لعقد واحد."""
    x = [(c["after_hi"] - c["exit_px"]) * 100 for c in v["closed"] if c["tdate"] == day and "exit_px" in c and c.get("after_hi")]
    return f"\n   بعد الخروج بلغت عقودها أعلى من سعر بيعها بمتوسط {sum(x) / len(x):+,.0f}$ للعقد" if x else ""


# ====================== استراتيجية «استمرار الترند بعد التصحيح» (H-TRENDPB) ======================
# نسخ ثابتة لا تدخل التطور: كل نسخة تضيف شيئاً واحداً على سابقتها (التسجيل في CLAUDE.md 13/11/2026).
V9 = {"score": 4, "adx": 15, "price": 5.0, "stop": 0.6, "exit": "tp100", "ladder": 10, "cap": 12, "sess": "rth", "trig": "any"}
STRAT = [("s0", 0), ("s1", 1), ("s2", 2), ("s3", 3)]          # المستوى: 1 خروج الهيكل، 2 فلتر اليوم، 3 دخول الهيكل
STRAT_NAMES = {"s0": "v9 مجمّدة", "s1": "+ خروج يتبع الهيكل", "s2": "+ فلتر نوع اليوم", "s3": "+ دخول الهيكل (MSS ثم FVG)"}
S_FIRST, S_LAST, S_FORCE = ms.dtime(10, 0), ms.dtime(14, 30), ms.dtime(15, 45)
S3_CAP, SMC_N = 3, 3


def strat_variants(day):
    return [{"vid": vid, "born": day, "level": lvl, "pinned": True, "open": [], "closed": [],
             "params": {**V9, "exit": "struct" if lvl >= 1 else "tp100"}} for vid, lvl in STRAT]


def all_variants(st):
    return st["variants"] + st.get("strat", []) + st.get("vp", [])


def manage_struct(e, p, bid, now, broken=False):
    """خروج s1–s3: وقف كامل قبل +100%؛ عنده بيع النصف ووقف الباقي على الدخول ثم مراحل؛ والباقي يخرج بكسر الهيكل أو 15:45."""
    E = e["entry"]
    e["peak"] = max(e.get("peak", E), bid)
    force = now.date().isoformat() >= e["tdate"] and now.time() >= S_FORCE
    if not e.get("half"):
        if bid <= E * (1 - p["stop"]):
            e["usd"] = round((bid - E) * 100, 2)
            return True, "وقف"
        if bid >= 2 * E:
            e["half"], e["half_usd"], e["sl"] = True, 0.5 * (bid - E) * 100, E
            return False, ""
        if force:
            e["usd"] = round((bid - E) * 100, 2)
            return True, "وقت"
        return False, ""
    sl = e.get("sl", E)
    why = "وقف مرحلي" if bid <= sl else ("كسر الهيكل" if broken else ("وقت" if force else ""))
    if why:
        e["usd"] = round(e["half_usd"] + 0.5 * (bid - E) * 100, 2)
        return True, why
    g = e["peak"] / E - 1
    sl = max(sl, E)
    if g >= 2:
        sl = max(sl, 1.5 * E)
    if g >= 3:
        sl = max(sl, 0.6 * e["peak"])
    e["sl"] = sl
    return False, ""


def _ohlc(df):
    return df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close"})


def strat_ctx(m1, now):
    """هيكل 5د للجلسة الرسمية وهيكل الساعة ونطاق الافتتاح، من شموع مكتملة فقط."""
    m5 = ms.resample(m1, "5min")
    m5 = m5[m5.index + pd.Timedelta(minutes=5) <= now]
    rth = m5[(m5.index >= datetime.combine(now.date(), ms.RTH_START, NY)) & (m5.index.date == now.date())]
    h1 = ms.resample(m1, "60min")
    h1 = h1[h1.index + pd.Timedelta(minutes=60) <= now]
    st5 = smc.analyze(_ohlc(rth), n=SMC_N) if len(rth) >= 2 * SMC_N + 1 else None
    sth = smc.analyze(_ohlc(h1), n=SMC_N) if len(h1) >= 2 * SMC_N + 1 else None
    orng = rth[rth.index < datetime.combine(now.date(), S_FIRST, NY)]
    return {"m5": rth, "st5": st5, "h1_trend": sth["trend"] if sth else None,
            "or": (float(orng["h"].max()), float(orng["l"].min())) if len(orng) == 6 else None}


def structure_broken(ctx, side, since):
    """هل أغلقت شمعة 5د بعد `since` خلف المستوى المحمي ضد الجهة (MSS أو BOS معاكس)؟"""
    st5 = ctx and ctx.get("st5")
    if not st5:
        return False
    against = "down" if side == "CALL" else "up"
    t0 = pd.Timestamp(since)
    return any(ev["dir"] == against and pd.Timestamp(ev["t"]) + pd.Timedelta(minutes=5) > t0 for ev in st5["events"])


def chop_day(ctx):
    """يوم تذبذب: إغلاقات 5د بعد 10:00 تجاوزت قمة نطاق الافتتاح وقاعه كليهما."""
    if not ctx.get("or"):
        return False
    hi, lo = ctx["or"]
    after = ctx["m5"][[t >= S_FIRST for t in ctx["m5"].index.time]]["c"]
    return bool((after > hi).any() and (after < lo).any())


def near_event(now):
    import macro_events as me
    name = me.EVENTS.get(now.date().isoformat())
    if not name:
        return False
    hh, mm = map(int, me.time_of(name).split(":"))
    at = datetime.combine(now.date(), ms.dtime(hh, mm), NY)
    return at - timedelta(hours=1) <= now <= at + timedelta(minutes=15)


def day_ok(ctx, now):
    """فلتر نوع اليوم (s2، s3): النافذة الزمنية، لا تذبذب، بعيداً عن الأخبار."""
    return S_FIRST <= now.time() <= S_LAST and not chop_day(ctx) and not near_event(now)


def smc_setup(ctx, side, total, adx_v, p):
    """إعداد s3 على آخر شمعة 5د مكتملة، أو None: الساعة مع الجهة، آخر كسر معها ترك FVG، لمسة قوية الآن، خصم/غلاء، أصوات ≥4."""
    st5, d = ctx.get("st5"), ("up" if side == "CALL" else "down")
    if not st5 or ctx.get("h1_trend") != d or side_for(p, total, adx_v) != side or not st5["events"]:
        return None
    ev = st5["events"][-1]
    if ev["dir"] != d or not ev["fvg"]:
        return None
    last = st5["N"] - 1
    f = next((f for f in st5["fvgs"] if f["dir"] == d and ev["origin"] < f["i"] <= ev["i"]
              and f.get("touch_i") == last and f["touch"] == "strong"), None)
    zone = (st5.get("pd") or {}).get("zone")
    if not f or zone != ("discount" if d == "up" else "premium"):
        return None
    return {"key": f"{ctx['m5'].index[ev['i']].isoformat()}|{f['i']}", "kind": ev["kind"], "ce": round(f["ce"], 2)}


def strat_wants(v, ctx, now, side, total, adx_v, S, tdate, new_bar):
    """هل تدخل النسخة الثابتة الآن في هذه الجهة؟ يرجع (نعم/لا، الزناد، الإعداد)."""
    p, lvl = v["params"], v["level"]
    if any(e["side"] != side for e in v["open"]):                  # اتجاه واحد (الشرط الشرعي)
        return False, None, None
    if lvl >= 2 and not day_ok(ctx, now):
        return False, None, None
    if lvl == 3:
        today = [e for e in v["open"] + v["closed"] if e["tdate"] == tdate]
        if not new_bar or len(today) >= S3_CAP:
            return False, None, None
        su = smc_setup(ctx, side, total, adx_v, p)
        if not su or any(e.get("setup") == su["key"] for e in today):
            return False, None, None
        return True, su["kind"], su
    if side_for(p, total, adx_v) != side or not can_add(v, side, S, tdate):
        return False, None, None
    has = any(e["side"] == side and e["tdate"] == tdate for e in v["open"] + v["closed"])
    return bool(new_bar or has), ("ladder" if has else "any"), None


def strat_text(st, day):
    L = ["", "📐 **الاستراتيجية (s0←s3، ثابتة):**"]
    for v in st.get("strat", []):
        s_d, n_d = score(v, [day])
        s_all, n_all = sum(c["usd"] for c in v["closed"]), len(v["closed"])
        L.append(f"- {v['vid']} {STRAT_NAMES[v['vid']]}: اليوم {s_d:+,.0f}$ ({n_d}) · المجموع {s_all:+,.0f}$ ({n_all})")
    return "\n".join(L)


# ====================== البروفايل والتدفق (H-VPFLOW) ======================
# نسختان ثابتتان (التسجيل في CLAUDE.md 14/11/2026): الشكل والمناطق من بروفايل SPY أمس، والدخول بعقد SPXW نحو 6$.
VP = [("p0", "p0"), ("p1", "p1")]
VP_NAMES = {"p0": "الشكل + الامتصاص", "p1": "الشكل بلا امتصاص"}
VP_PRICE, VP_CAP, VP_STOP = 6.0, 3, 0.6
VP_FIRST, VP_LAST, VP_FRESH = ms.dtime(9, 45), ms.dtime(15, 0), timedelta(minutes=15)


def vp_variants(day):
    return [{"vid": vid, "born": day, "mode": mode, "pinned": True, "open": [], "closed": [],
             "params": {**V9, "price": VP_PRICE, "exit": "levels"}} for vid, mode in VP]


def spy_m1():
    """شموع SPY بالدقيقة (5 أيام) من ياهو بالحجم، بأعمدة o,h,l,c,v وتوقيت نيويورك."""
    import yfinance as yf
    h = yf.Ticker("SPY").history(period="5d", interval="1m", auto_adjust=False)
    h.index = h.index.tz_convert(NY)
    return vf.lower(h)


def vp_manage(e, bid, spy, now):
    """إدارة مركز p0/p1 بمستويات SPY. يرجع (مغلق؟، سبب) ويضع e['usd'] عند الإغلاق."""
    b5 = vf.completed5(spy, now, "5min")
    px = float(spy["c"].iloc[-1])
    if e.get("trend"):
        closed, why = vf.manage_trend(e, bid, vf.struct_broken(b5, e["side"], e["t"]), now, S_FORCE, VP_STOP)
    else:
        after = b5[b5.index + pd.Timedelta(minutes=5) > pd.Timestamp(e["t"])]
        c5 = float(after["c"].iloc[-1]) if len(after) else None
        closed, why = vf.manage_levels(e, bid, px, c5, now, S_FORCE, VP_STOP)
    if closed:
        e["usd"] = round(e["ret"] * e["entry"] * 100, 2)
    return closed, why


def vp_signals(v, zs, b5, now, tdate):
    """إشارات النسخة الآن: [(المنطقة، الإشارة)] بالترتيب، مع السقف والمنطقة مرة واحدة والاتجاه الواحد."""
    today = [e for e in v["open"] + v["closed"] if e["tdate"] == tdate]
    if len(today) >= VP_CAP:
        return []
    fresh = now - VP_FRESH - timedelta(minutes=5)
    first = datetime.combine(now.date(), VP_FIRST, NY) - timedelta(minutes=5)
    out = []
    for z in zs:
        if any(e.get("zone") == z["key"] for e in today) or any(e["side"] != z["side"] for e in v["open"]):
            continue
        sig = vf.scan(b5, z, v["mode"], fresh, first)
        if sig:
            out.append((z, sig))
    return out


def vp_morning(spy, day):
    """رسالة الصباح: شكل أمس ومناطقه لـSPY (مستويات SPY) والأسهم الست."""
    import stocks_lab as sl
    pf, shp, zs = vf.plan(spy, day)
    L = [f"📊 **البروفايل والتدفق · خطة اليوم** · {day.isoformat()}", "شكل جلسة أمس يحدد أين نبحث، والامتصاص يحدد متى (p0).", "",
         vf.plan_line("SPY (لعقود SPX)", pf, shp, zs)]
    try:
        data = sl.bars(sl.TICKERS)
        for tk in sl.TICKERS:
            if tk in data:
                L.append(vf.plan_line(tk, *vf.plan(vf.lower(data[tk]), day)))
    except Exception as e:  # noqa: BLE001
        L.append(f"- الشركات: تعذّرت الشموع ({e})")
    return "\n".join(L)


def vp_text(st, day):
    L = ["", "📊 **البروفايل والتدفق (p0/p1، ثابتة):**"]
    for v in st.get("vp", []):
        s_d, n_d = score(v, [day])
        s_all, n_all = sum(c["usd"] for c in v["closed"]), len(v["closed"])
        L.append(f"- {v['vid']} {VP_NAMES[v['vid']]}: اليوم {s_d:+,.0f}$ ({n_d}) · المجموع {s_all:+,.0f}$ ({n_all})")
    return "\n".join(L)


# ====================== التشغيل ======================
def load_state(day):
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        st = {"gen": 1, "variants": initial_variants(day), "last_bar": None, "evolve_day": ""}
    st.setdefault("strat", strat_variants(day))
    st.setdefault("vp", vp_variants(day))
    ensure_exits(st)
    return st


def save(st):
    os.makedirs(DIR, exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)


def log_trade(v, e, why):
    os.makedirs(DIR, exist_ok=True)
    row = {"vid": v["vid"], "params": fmt(v["params"]), **{k: e.get(k) for k in
           ("t", "sess", "tdate", "side", "strike", "entry", "S", "trigger", "score", "adx", "peak", "usd")}, "why": why,
           "closed_at": datetime.now(NY).isoformat(timespec="minutes")}
    pd.DataFrame([row]).to_csv(TRADES, mode="a", header=not os.path.exists(TRADES), index=False)


def leaderboard(st, day):
    tds = sorted({c["tdate"] for v in st["variants"] for c in v["closed"]})[-WINDOW:]
    ranked = sorted(st["variants"], key=lambda v: score(v, tds)[0], reverse=True)
    today = [score(v, [day]) for v in st["variants"]]
    L = [f"🧬 **مختبر التطور (ورقي)** · {day}", "",
         f"{len(ranked)} نسخة تتنافس: اليوم {sum(s > 0 for s, n in today)} رابحة · {sum(s < 0 for s, n in today)} خاسرة · "
         f"{sum(n == 0 for s, n in today)} بلا صفقات", "", "**أفضل 3 نسخ (آخر 5 أيام):**"]
    top = [v for v in ranked if not v.get("control")][:3]
    for medal, v in zip("🥇🥈🥉", top):
        s, n = score(v, tds)
        L.append(f"{medal} **{v['vid']}: {s:+,.0f}$** في {n} صفقة\n   {plain(v['params'])}{left_text(v, day)}")
    ctl = next((v for v in ranked if v.get("control")), None)
    if ctl:
        s, n = score(ctl, tds)
        L += ["", f"🧪 **الضابطة (إعدادات MGT الأصلية): {s:+,.0f}$** في {n} صفقة"]
    return "\n".join(L)


def changes_text(st):
    ch = st.get("last_changes") or []
    if not ch:
        return "**التغيير:** لا استبدال اليوم."
    muts = [c["parent"] for c in ch if c["parent"]]
    L = [f"**التغيير:** خرجت {len(ch)} نسخ من الأضعف ({'، '.join(c['old'] for c in ch)})"]
    if muts:
        L.append(f"دخلت {len(muts)} نسخ معدّلة من {'، '.join(dict.fromkeys(muts))}")
    if len(muts) < len(ch):
        L.append("ونسخة عشوائية للاستكشاف")
    return "\n".join(L) + "\nالجدد يبدأون من الصفر. الترتيب الكامل في generations.md على الخادم."


def spx_m1():
    """(شموع SPX المكافئ بالدقيقة من ES اللحظي، السعر الآن)."""
    sh = json.load(open(ms.STATE, encoding="utf-8"))
    basis = float(sh.get("basis") or 0)
    es, _ = ms.es_mark()
    m1 = ms.live_m1(ms.es_bars(), ms.load_ticks()).copy()
    m1[["o", "h", "l", "c"]] -= basis
    S = es - basis
    m1.loc[m1.index[-1], "c"] = S
    return m1, S


def read_engine(now, sess, last_bar):
    """قراءة المحرك الآن: السعر والتصويت والشمعة الجديدة وخطوط Murrey المعبورة، أو None إن لم تكتمل الشموع."""
    m1, S = spx_m1()
    start = datetime.combine(now.date() if now.time() >= ms.GTH_START or sess == "rth" else now.date() - timedelta(days=1),
                             ms.RTH_START if sess == "rth" else ms.GTH_START, NY)
    sb = m1[m1.index >= start]
    so = float(sb["o"].iloc[0]) if len(sb) else None
    vw = float((sb["c"] * sb["v"]).sum() / sb["v"].sum()) if len(sb) and sb["v"].sum() > 0 else None
    _side, total, det = ms.decide(m1, so, vw, ms.spy_pressure(now) if sess == "rth" else 0,
                                  (ms.flow_now(now).get("flow_lean_30m") or 0) if sess == "rth" else 0)
    m5 = ms.resample(m1, "5min")
    done5 = m5[m5.index + pd.Timedelta(minutes=5) <= now]
    if len(done5) < 3:
        return None
    bar = done5.index[-1].isoformat()
    new_bar = last_bar != bar
    lines = ms.mml_lines(S, ms.octave(float(m1["h"].max() - m1["l"].min())))
    hit = {sd: ms.crossed_line(float(done5["c"].iloc[-2]), float(done5["c"].iloc[-1]), lines, sd) if new_bar else None
           for sd in ("CALL", "PUT")}
    return {"S": S, "total": total, "det": det, "new_bar": new_bar, "bar": bar, "hit": hit, "m1": m1}


def main(argv):
    now = datetime.now(NY)
    force, no_send = "--force" in argv, "--no-send" in argv
    day = now.date().isoformat()
    st = load_state(day)
    # 1) إدارة المفتوح لكل النسخ
    syms = sorted({e["symbol"] for v in all_variants(st) for e in v["open"]} | (tracked_symbols(st, day) if now.time() <= ms.RTH_END else set()))
    if syms:
        try:
            qs = {q["symbol"]: q for q in ms.quotes(syms)}
        except Exception as e:  # noqa: BLE001
            print("أسعار:", e)
            qs = {}
        ctx = None
        if any(e.get("half") for v in st.get("strat", []) for e in v["open"]):
            try:
                ctx = strat_ctx(spx_m1()[0], now)
            except Exception as e:  # noqa: BLE001
                print("هيكل:", e)
        spy = None
        if any(v["open"] for v in st.get("vp", [])):
            try:
                spy = spy_m1()
            except Exception as e:  # noqa: BLE001
                print("SPY:", e)
        for v in all_variants(st):
            keep = []
            for e in v["open"]:
                q = qs.get(e["symbol"])
                if not q:
                    closed, why = False, ""
                elif v.get("mode"):
                    closed, why = vp_manage(e, q["bid"], spy, now) if spy is not None and len(spy) else (False, "")
                elif v["params"]["exit"] == "struct":
                    closed, why = manage_struct(e, v["params"], q["bid"], now, structure_broken(ctx, e["side"], e["t"]))
                else:
                    closed, why = manage(e, v["params"], q["bid"], now)
                if closed:
                    e["exit_px"], e["exit_t"] = q["bid"], now.strftime("%H:%M")
                    v["closed"].append(e)
                    log_trade(v, e, why)
                else:
                    keep.append(e)
            v["open"] = keep
        track_after(st, qs, now)
    # 2) التطور والتقرير بعد الجلسة
    if now.weekday() < 5 and now.time() >= ms.REPORT_AT and st.get("evolve_day") != day and \
            any(c["tdate"] == day for v in all_variants(st) for c in v["closed"]):
        board = leaderboard(st, day)
        os.makedirs(DIR, exist_ok=True)
        rows = after_rows(st, day)
        if rows:
            pd.DataFrame(rows).to_csv(AFTER, mode="a", header=not os.path.exists(AFTER), index=False)
        gen_txt = evolve(st, day, random.Random(day))
        ensure_exits(st)
        with open(GENS, "a", encoding="utf-8") as f:
            f.write(gen_txt + "\n\n")
        msg = board + "\n" + strat_text(st, day) + "\n" + vp_text(st, day) + "\n\n" + changes_text(st)
        print(msg)
        if not no_send:
            import analyst as an
            an.send(msg + "\n\n⚠️ ورقي للدراسة؛ يتداول الاتجاهين، فليس توصية.", "mgt_lab")
        st["evolve_day"] = day
    # 3) دخولات النسخ
    sess = ms.session(now)
    if not (sess == "gth" or (sess == "rth" and now.time() <= ms.RTH_LAST_ENTRY) or force):
        save(st)
        return
    try:
        eng = read_engine(now, sess, st.get("last_bar"))
    except Exception as e:  # noqa: BLE001
        print("بيانات:", e)
        save(st)
        return
    if eng is None:
        save(st)
        return
    S, total, det, new_bar, hit = eng["S"], eng["total"], eng["det"], eng["new_bar"], eng["hit"]
    st["last_bar"] = eng["bar"]
    tdate = ms.trade_date(now).isoformat()
    chain = None
    for v in st["variants"]:
        p = v["params"]
        if p["sess"] == "rth" and sess != "rth":
            continue
        sd = side_for(p, total, det["adx"])
        if not sd or not can_add(v, sd, S, tdate):
            continue
        has = any(e["side"] == sd and e["tdate"] == tdate for e in v["open"] + v["closed"])
        trig = hit[sd] if p["trig"] == "mml" else new_bar
        if not (trig or has):
            continue
        if chain is None:
            try:
                chain = ms.chain(date.fromisoformat(tdate), S)
            except Exception as e:  # noqa: BLE001
                print("السلسلة:", e)
                chain = []
        k = pick(chain, sd, S, p["price"])
        if not k:
            continue
        v["open"].append({"t": now.isoformat(timespec="minutes"), "sess": sess, "tdate": tdate, "side": sd, "strike": k["strike"],
                          "symbol": k["symbol"], "entry": k["ask"], "S": round(S, 2), "score": total, "adx": round(det["adx"], 1),
                          "trigger": "mml" if hit[sd] else ("ladder" if has else "any"), "peak": k["ask"]})
        print("دخول", v["vid"], sd, k["strike"], k["ask"])
    # 4) النسخ الثابتة للاستراتيجية (H-TRENDPB): الجلسة الرسمية فقط
    if sess == "rth" and (now.time() <= ms.RTH_LAST_ENTRY or force):
        try:
            sctx = strat_ctx(eng["m1"], now)
        except Exception as e:  # noqa: BLE001
            print("هيكل:", e)
            sctx = None
        for v in st.get("strat", []):
            if sctx is None and v["level"] >= 2:
                continue
            for sd in ("CALL", "PUT"):
                ok, trig, su = strat_wants(v, sctx, now, sd, total, det["adx"], S, tdate, new_bar)
                if not ok:
                    continue
                if chain is None:
                    try:
                        chain = ms.chain(date.fromisoformat(tdate), S)
                    except Exception as e:  # noqa: BLE001
                        print("السلسلة:", e)
                        chain = []
                k = pick(chain, sd, S, v["params"]["price"])
                if not k:
                    continue
                v["open"].append({"t": now.isoformat(timespec="minutes"), "sess": sess, "tdate": tdate, "side": sd,
                                  "strike": k["strike"], "symbol": k["symbol"], "entry": k["ask"], "S": round(S, 2),
                                  "score": total, "adx": round(det["adx"], 1), "trigger": trig, "peak": k["ask"],
                                  **({"setup": su["key"], "ce": su["ce"]} if su else {})})
                print("دخول", v["vid"], sd, k["strike"], k["ask"], trig)
                break
    # 5) البروفايل والتدفق (H-VPFLOW): الجلسة الرسمية فقط
    if sess == "rth" and (now.time() <= VP_LAST or force):
        try:
            spy = spy_m1()
            _pf, _shp, zs = vf.plan(spy, now.date())
            b5 = vf.completed5(spy, now, "5min")
        except Exception as e:  # noqa: BLE001
            print("بروفايل:", e)
            spy, zs, b5 = None, [], None
        if spy is not None and st.get("vp_msg") != day:
            st["vp_msg"] = day
            try:
                txt = vp_morning(spy, now.date())
                print(txt)
                if not no_send:
                    import analyst as an
                    an.send(txt + "\n\n⚠️ ورقي للدراسة، وليس توصية.", "mgt_lab")
            except Exception as e:  # noqa: BLE001
                print("رسالة الصباح:", e)
        if zs and b5 is not None and (now.time() >= VP_FIRST or force):
            for v in st.get("vp", []):
                for z, sig in vp_signals(v, zs, b5, now, tdate):
                    if chain is None:
                        try:
                            chain = ms.chain(date.fromisoformat(tdate), S)
                        except Exception as e:  # noqa: BLE001
                            print("السلسلة:", e)
                            chain = []
                    k = pick(chain, z["side"], S, VP_PRICE)
                    if not k:
                        continue
                    v["open"].append({"t": now.isoformat(timespec="minutes"), "sess": sess, "tdate": tdate, "side": z["side"],
                                      "strike": k["strike"], "symbol": k["symbol"], "entry": k["ask"], "S": round(S, 2),
                                      "score": total, "adx": round(det["adx"], 1), "trigger": v["mode"], "peak": k["ask"],
                                      "zone": z["key"], "stop_px": sig["stop"], "t1": z["t1"], "t2": z["t2"], "trend": z["trend"],
                                      "spy": round(float(spy["c"].iloc[-1]), 2), "sig_t": sig["t"].isoformat(),
                                      "lag": round((now - sig["t"]).total_seconds() / 60 - 5, 1)})
                    print("دخول", v["vid"], z["key"], k["strike"], k["ask"])
                    break
    save(st)


if __name__ == "__main__":
    main(sys.argv[1:])
