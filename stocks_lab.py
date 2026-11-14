#!/usr/bin/env python3
"""مختبر تطوّر الشركات (H-STOCKLAB، مسجّل في CLAUDE.md 12/11/2026 قبل الكود بموافقة المستخدم).

12 نسخة ورقية تتداول خيارات 6 أسهم كبيرة (NVDA TSLA AAPL AMZN META AMD) في الجلسة الرسمية، لكل نسخة معاييرها
(العتبة، ADX، مدة العقد، الدلتا، الوقف، الخروج، وقت البدء، الزناد، السقف) ودفترها. كل المراكز يومية تُغلق 15:50.
النتيجة = العائد% × 1000$ لكل صفقة. بعد 16:05: ترتيب آخر 5 أيام، تبقى الأفضل وتُستبدل الأسوأ بطفرات ونسخة عشوائية.
v0 ضابطة قريبة من بوت الشركات لا تتغير. ورق فقط، منفصل عن البوتات، لا Claude، لا رسائل توصية. الملفات: SPXBOT_DATA/stocks_lab/
تشغيل كل 5 دقائق: python stocks_lab.py [--force] [--no-send]
"""
import json
import math
import os
import random
import sys
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

import pandas as pd

import bhvote as bv
import vpflow as vf

NY = ZoneInfo("America/New_York")
DIR = os.path.join(os.getenv("SPXBOT_DATA", "/opt/spxbot/data"), "stocks_lab")
STATE, TRADES, GENS = (os.path.join(DIR, f) for f in ("state.json", "trades.csv", "generations.md"))
TICKERS = ["NVDA", "TSLA", "AAPL", "AMZN", "META", "AMD"]
GRID = {"score": [2, 3, 4], "adx": [0, 15, 20, 25], "dte": ["0-2", "3-7", "8-21"], "delta": [0.3, 0.4, 0.5, 0.6],
        "stop": [0.25, 0.35, 0.5], "exit": ["tp30", "tp50", "tp100", "half", "late", "late_half"],
        "start": ["0945", "1030", "1130"], "trig": ["orb", "vwap", "any"], "cap": [1, 2]}
CONTROL = {"score": 3, "adx": 20, "dte": "8-21", "delta": 0.5, "stop": 0.35, "exit": "late", "start": "1030", "trig": "any", "cap": 1}
DTE = {"0-2": (0, 2), "3-7": (3, 7), "8-21": (8, 21)}
N_VAR, KEEP, WINDOW, MIN_TRADES = 12, 6, 5, 3
RTH_START, LAST_ENTRY, CLOSE_AT, RTH_END, REPORT_AT = dtime(9, 30), dtime(15, 0), dtime(15, 50), dtime(16, 0), dtime(16, 5)
ORB_BARS, MAX_SPR, UNIT, STRIKE_BAND = 6, 0.15, 1000, 0.10


def start_time(code):
    return dtime(int(code[:2]), int(code[2:]))


# ====================== النسخ والتطور (دوال صافية) ======================
def random_params(rng):
    return {k: rng.choice(v) for k, v in GRID.items()}


def mutate(p, rng, n=None):
    q = dict(p)
    for k in rng.sample(list(GRID), n or rng.choice([1, 2])):
        q[k] = rng.choice([v for v in GRID[k] if v != q[k]])
    return q


def initial_variants(day, seed=11):
    rng = random.Random(seed)
    vs = [{"vid": "v0", "born": day, "params": dict(CONTROL), "open": [], "closed": [], "control": True}]
    for i in range(1, N_VAR):
        vs.append({"vid": f"v{i}", "born": day, "params": mutate(CONTROL, rng, 2) if i <= 6 else random_params(rng),
                   "open": [], "closed": []})
    return vs


def score(v, days, tk=None):
    cl = [c for c in v["closed"] if c["tdate"] in days and (tk is None or c["tk"] == tk)]
    return round(sum(c["pnl"] for c in cl), 2), len(cl)


def window_days(st):
    return sorted({c["tdate"] for v in st["variants"] for c in v["closed"]})[-WINDOW:]


def evolve(st, day, rng):
    """يرتّب ويستبدل الأسوأ (نفس منطق H-MGTLAB). يرجع نص ما حدث."""
    tds = window_days(st)
    ranked = sorted(st["variants"], key=lambda v: score(v, tds)[0], reverse=True)
    repl = [v for v in ranked if not v.get("control") and score(v, tds)[1] >= MIN_TRADES]
    n_out = max(0, min(len(repl) - KEEP, N_VAR - KEEP)) if len(repl) > KEEP else len(repl) // 2
    losers = repl[::-1][:n_out]
    parents = [v for v in ranked if v not in losers and not v.get("control")][:3] or [ranked[0]]
    st["gen"] = st.get("gen", 1) + 1
    changes, lines = [], [f"## الجيل {st['gen']} ({day})", "الترتيب (آخر 5 أيام، $ لكل 1000$ في الصفقة):"]
    for v in ranked:
        s, n = score(v, tds)
        lines.append(f"- {v['vid']}{' (ضابطة)' if v.get('control') else ''}: {s:+,.0f}$ في {n} صفقة | {fmt(v['params'])}")
    for i, lo in enumerate(losers):
        par = parents[i % len(parents)]
        rnd = i == len(losers) - 1
        newp = random_params(rng) if rnd else mutate(par["params"], rng)
        lines.append(f"- استُبدلت {lo['vid']} ({score(lo, tds)[0]:+,.0f}$) ← {'عشوائية' if rnd else 'طفرة من ' + par['vid']}: {fmt(newp)}")
        changes.append({"old": lo["vid"], "parent": None if rnd else par["vid"]})
        lo.update(params=newp, born=day, open=[], closed=[], vid=f"{lo['vid'].split('g')[0]}g{st['gen']}")
    st["last_changes"] = changes
    if not losers:
        lines.append("- لا استبدال بعد (النسخ لم تبلغ 3 صفقات).")
    return "\n".join(lines)


EXIT_AR = {"half": "نصف عند +100% والباقي متحرك", "late": "وقف يرتفع بمراحل", "late_half": "نصف عند +100% ووقف بمراحل"}
TRIG_AR = {"orb": "كسر نطاق أول 30د", "vwap": "عبور VWAP", "any": "أي شمعة"}


def fmt(p):
    return (f"عتبة {p['score']} · ADX≥{p['adx']} · {p['dte']} يوم · دلتا {p['delta']} · وقف {int(p['stop'] * 100)}% · "
            f"خروج {p['exit']} · بدء {p['start'][:2]}:{p['start'][2:]} · زناد {p['trig']} · سقف {p['cap']}")


def plain(p):
    out = EXIT_AR.get(p["exit"]) or f"هدف +{p['exit'][2:]}%"
    return (f"عقد {p['dte']} يوم بدلتا {p['delta']} · {out} · وقف {int(p['stop'] * 100)}%\n"
            f"   تدخل بـ{p['score']} أصوات من {p['start'][:2]}:{p['start'][2:]} عند {TRIG_AR[p['trig']]} · حتى {p['cap']} لكل سهم يومياً")


# ====================== محرك القرار (دوال صافية) ======================
def completed(df, now):
    """الشموع المكتملة (5 دقائق) حتى now."""
    return df[df.index + pd.Timedelta(minutes=5) <= now]


def day_ret(df, day):
    t = df[df.index.date == day]
    return float(t["Close"].iloc[-1] / t["Open"].iloc[0] - 1) if len(t) else None


def engine(df, spy, now):
    """قراءة سهم واحد على آخر شمعة مكتملة: {S، total، votes، adx، bar، orb، vwap_x}، أو None."""
    b = completed(df.dropna(subset=["Close"]), now)
    today = b[b.index.date == now.date()]
    if len(today) < 2:
        return None
    c, h, lo = b["Close"].tolist(), b["High"].tolist(), b["Low"].tolist()
    S, prev = float(today["Close"].iloc[-1]), float(today["Close"].iloc[-2])
    tp = (today["High"] + today["Low"] + today["Close"]) / 3
    vol = float(today["Volume"].sum())
    vw = float((tp * today["Volume"]).sum() / vol) if vol > 0 else None
    r, rs = day_ret(b, now.date()), day_ret(completed(spy.dropna(subset=["Close"]), now), now.date()) if spy is not None else None
    votes = {"vwap": bv.sgn(S - vw) if vw else 0, "ema": bv.sgn(bv._ema(c, 9)[-1] - bv._ema(c, 21)[-1]),
             "macd": bv.sgn(bv.macd_hist(c)), "rs": bv.sgn(r - rs, 0.001) if r is not None and rs is not None else 0}
    orb = None
    if len(today) > ORB_BARS:
        oh, ol = float(today["High"].iloc[:ORB_BARS].max()), float(today["Low"].iloc[:ORB_BARS].min())
        orb = "CALL" if prev <= oh < S else ("PUT" if prev >= ol > S else None)
    vx = None
    if vw:
        vx = "CALL" if prev <= vw < S else ("PUT" if prev >= vw > S else None)
    return {"S": S, "total": sum(votes.values()), "votes": votes, "adx": bv.adx(h, lo, c) or 0.0,
            "bar": today.index[-1].isoformat(), "orb": orb, "vwap_x": vx}


def side_for(p, sig):
    if sig["adx"] < p["adx"]:
        return None
    return "CALL" if sig["total"] >= p["score"] else ("PUT" if sig["total"] <= -p["score"] else None)


def triggered(p, sig, side):
    return {"orb": sig["orb"] == side, "vwap": sig["vwap_x"] == side, "any": True}[p["trig"]]


def can_enter(v, tk, side, tdate):
    """السقف اليومي، ومركز واحد مفتوح لكل سهم، ولا CALL وPUT معاً على السهم نفسه."""
    if any(e["tk"] == tk for e in v["open"]):
        return False
    return sum(e["tk"] == tk and e["tdate"] == tdate for e in v["closed"]) < v["params"]["cap"]


def bs_delta(S, K, T, iv, side):
    if not (S > 0 and K > 0 and T > 0 and iv > 0):
        return None
    d1 = (math.log(S / K) + 0.5 * iv * iv * T) / (iv * math.sqrt(T))
    nd = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
    return nd if side == "CALL" else nd - 1


def pick(rows, side, bucket, delta, S):
    """أقرب انتهاء ضمن مدة النسخة، ثم العقد الأقرب للدلتا المستهدفة بفرق سعر ≤ 15%، أو None."""
    lo, hi = DTE[bucket]
    exps = sorted({r["dte"] for r in rows if lo <= r["dte"] <= hi})
    if not exps:
        return None
    ok = []
    for r in rows:
        if r["side"] != side or r["dte"] != exps[0] or r["bid"] <= 0 or r["ask"] <= 0:
            continue
        if (r["ask"] - r["bid"]) / ((r["ask"] + r["bid"]) / 2) > MAX_SPR:
            continue
        d = abs(r["delta"]) if r.get("delta") else abs(bs_delta(S, r["strike"], max(r["dte"], 0.5) / 365, r.get("iv") or 0, side) or 0)
        if d > 0:
            ok.append((abs(d - delta), r))
    return min(ok, key=lambda x: x[0])[1] if ok else None


def manage(e, p, bid, now):
    """قواعد خروج النسخة على مركز واحد. ret كنسبة من سعر الدخول. يرجع (مغلق؟، سبب)."""
    E = e["entry"]
    e["peak"] = max(e.get("peak", E), bid)
    eod = now.time() >= CLOSE_AT or now.date().isoformat() > e["tdate"]
    r = bid / E - 1

    def close(ret, why):
        e["ret"] = round(ret, 4)
        e["pnl"] = round(ret * UNIT, 2)
        return True, why
    held = 0.5 if e.get("half") else 1.0
    if p["exit"] == "half" and e.get("half"):
        e["trail"] = max(e.get("trail", bid), bid)
        if bid <= e["trail"] * 0.5 or eod:
            return close(e["half_ret"] + 0.5 * r, "نهاية اليوم" if eod else "متحرك")
        return False, ""
    if p["exit"] in ("late", "late_half"):
        sl = e.get("sl", E * (1 - p["stop"]))
        if bid <= sl or eod:
            return close(e.get("half_ret", 0) + held * r, "نهاية اليوم" if bid > sl else "وقف مرحلي")
        if p["exit"] == "late_half" and not e.get("half") and bid >= 2 * E:
            e["half"], e["half_ret"] = True, 0.5 * r
        g = e["peak"] / E - 1
        sl = max(sl, E) if g >= 1 else sl
        sl = max(sl, 1.5 * E) if g >= 2 else sl
        sl = max(sl, 0.6 * e["peak"]) if g >= 3 else sl
        e["sl"] = sl
        return False, ""
    if bid <= E * (1 - p["stop"]):
        return close(r, "وقف")
    if p["exit"] == "half" and bid >= 2 * E:
        e["half"], e["half_ret"], e["trail"] = True, 0.5 * r, bid
        return False, ""
    if p["exit"].startswith("tp") and r >= int(p["exit"][2:]) / 100:
        return close(r, "هدف")
    if eod:
        return close(r, "نهاية اليوم")
    return False, ""


# ====================== البيانات ======================
def bars(tickers):
    """{رمز: شموع 5 دقائق لـ5 أيام بتوقيت نيويورك} من ياهو بطلب واحد."""
    import yfinance as yf
    raw = yf.download(tickers, period="5d", interval="5m", group_by="ticker", auto_adjust=False, progress=False, prepost=False)
    out = {}
    for t in tickers:
        try:
            d = raw[t].dropna(how="all")
            d.index = d.index.tz_convert(NY)
            out[t] = d
        except Exception:  # noqa: BLE001
            pass
    return out


def chain(tk, S, buckets, today):
    """عقود tk لأقرب انتهاء في كل مدة مطلوبة ضمن ±10% من السعر، من Tastytrade."""
    import live_quotes as lq
    import mgt_shadow as ms
    tok = lq._tasty_token()
    items = (lq._tasty_req(f"/option-chains/{tk}/nested", token=tok).get("data") or {}).get("items") or []
    exps = {}
    for it in items:
        if it.get("root-symbol") != tk:
            continue
        for e in it.get("expirations") or []:
            exps[e["expiration-date"]] = e
    want = set()
    for b in buckets:
        lo, hi = DTE[b]
        ds = sorted(x for x in exps if lo <= (date.fromisoformat(x) - today).days <= hi)
        if ds:
            want.add(ds[0])
    meta = {}
    for x in want:
        dte = (date.fromisoformat(x) - today).days
        for k in exps[x].get("strikes") or []:
            K = float(k["strike-price"])
            if abs(K / S - 1) <= STRIKE_BAND:
                meta[k["call"]], meta[k["put"]] = ("CALL", K, x, dte), ("PUT", K, x, dte)
    rows = []
    for q in ms.quotes(list(meta)):
        m = meta.get(q["symbol"])
        if m:
            rows.append(dict(q, side=m[0], strike=m[1], exp=m[2], dte=m[3]))
    return rows


# ====================== البروفايل والتدفق (H-VPFLOW) ======================
# نسختان ثابتتان لا تدخلان التطور (التسجيل في CLAUDE.md 14/11/2026): شكل بروفايل السهم أمس ومناطقه، والامتصاص على 5د.
VP = [("p0", "p0"), ("p1", "p1")]
VP_NAMES = {"p0": "الشكل + الامتصاص", "p1": "الشكل بلا امتصاص"}
VP_PARAMS = {"score": 0, "adx": 0, "dte": "3-7", "delta": 0.5, "stop": 0.5, "exit": "levels", "start": "0945", "trig": "vp", "cap": 1}
VP_FIRST, VP_FRESH = dtime(9, 45), pd.Timedelta(minutes=15)


def vp_variants(day):
    return [{"vid": vid, "born": day, "mode": mode, "pinned": True, "open": [], "closed": [], "params": dict(VP_PARAMS)}
            for vid, mode in VP]


def all_vars(st):
    return st["variants"] + st.get("vp", [])


def vp_wants(st, tk, df, now, tdate):
    """[(النسخة، المنطقة، الإشارة)] لسهم واحد على آخر الشموع المكتملة."""
    if now.time() < VP_FIRST or now.time() > LAST_ENTRY:
        return []
    d = vf.lower(df)
    _pf, _shp, zs = vf.plan(d, now.date())
    if not zs:
        return []
    b5 = vf.completed5(d, now)
    fresh = pd.Timestamp(now) - VP_FRESH - pd.Timedelta(minutes=5)
    first = pd.Timestamp(datetime.combine(now.date(), VP_FIRST, NY)) - pd.Timedelta(minutes=5)
    out = []
    for v in st.get("vp", []):
        for z in zs:
            if not can_enter(v, tk, z["side"], tdate):
                break
            sig = vf.scan(b5, z, v["mode"], fresh, first)
            if sig:
                out.append((v, z, sig))
                break
    return out


def vp_manage(e, bid, df, now):
    d = vf.lower(df)
    b5 = vf.completed5(d, now)
    px = float(d["c"].dropna().iloc[-1])
    if e.get("trend"):
        closed, why = vf.manage_trend(e, bid, vf.struct_broken(b5, e["side"], e["t"]), now, CLOSE_AT, VP_PARAMS["stop"])
    else:
        after = b5[b5.index + pd.Timedelta(minutes=5) > pd.Timestamp(e["t"])]
        c5 = float(after["c"].iloc[-1]) if len(after) else None
        closed, why = vf.manage_levels(e, bid, px, c5, now, CLOSE_AT, VP_PARAMS["stop"])
    if closed:
        e["pnl"] = round(e["ret"] * UNIT, 2)
    return closed, why


def vp_text(st, day):
    L = ["", "📊 **البروفايل والتدفق (p0/p1، ثابتة):**"]
    for v in st.get("vp", []):
        s_d, n_d = score(v, [day])
        s_all, n_all = sum(c["pnl"] for c in v["closed"]), len(v["closed"])
        L.append(f"- {v['vid']} {VP_NAMES[v['vid']]}: اليوم {s_d:+,.0f}$ ({n_d}) · المجموع {s_all:+,.0f}$ ({n_all})")
    return "\n".join(L)


# ====================== التقرير والتشغيل ======================
def load_state(day):
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        st = {"gen": 1, "variants": initial_variants(day), "last_bar": {}, "evolve_day": ""}
    st.setdefault("vp", vp_variants(day))
    return st


def save(st):
    os.makedirs(DIR, exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)


def log_trade(v, e, why):
    os.makedirs(DIR, exist_ok=True)
    row = {"vid": v["vid"], "params": fmt(v["params"]), **{k: e.get(k) for k in
           ("t", "tdate", "tk", "side", "exp", "strike", "entry", "exit_px", "exit_t", "S", "score", "adx", "trigger", "peak", "ret", "pnl")},
           "why": why}
    pd.DataFrame([row]).to_csv(TRADES, mode="a", header=not os.path.exists(TRADES), index=False)


def leaderboard(st, day):
    tds = window_days(st)
    ranked = sorted(st["variants"], key=lambda v: score(v, tds)[0], reverse=True)
    today = [score(v, [day]) for v in st["variants"]]
    L = [f"🧬 **مختبر الشركات (ورقي)** · {day}", "",
         f"{len(ranked)} نسخة على {' '.join(TICKERS)}: اليوم {sum(s > 0 for s, n in today)} رابحة · "
         f"{sum(s < 0 for s, n in today)} خاسرة · {sum(n == 0 for s, n in today)} بلا صفقات",
         "النتيجة = $ لكل 1000$ في كل صفقة.", "", "**أفضل 3 نسخ (آخر 5 أيام):**"]
    for medal, v in zip("🥇🥈🥉", [v for v in ranked if not v.get("control")][:3]):
        s, n = score(v, tds)
        L.append(f"{medal} **{v['vid']}: {s:+,.0f}$** في {n} صفقة\n   {plain(v['params'])}")
    ctl = next((v for v in ranked if v.get("control")), None)
    if ctl:
        s, n = score(ctl, tds)
        L += ["", f"🧪 **الضابطة (قريبة من بوت الشركات): {s:+,.0f}$** في {n} صفقة"]
    L += ["", "**الأفضل لكل سهم (آخر 5 أيام):**"]
    for tk in TICKERS:
        best = max(st["variants"], key=lambda v: score(v, tds, tk)[0])
        s, n = score(best, tds, tk)
        L.append(f"- {tk}: " + (f"{best['vid']} {s:+,.0f}$ في {n} ({best['params']['dte']} يوم، دلتا {best['params']['delta']}، "
                                f"{best['params']['exit']})" if n else "لا صفقات"))
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


def manage_open(st, now):
    syms = sorted({e["symbol"] for v in all_vars(st) for e in v["open"]})
    if not syms:
        return
    try:
        import mgt_shadow as ms
        qs = {q["symbol"]: q for q in ms.quotes(syms)}
    except Exception as ex:  # noqa: BLE001
        print("أسعار:", ex)
        return
    vtk = sorted({e["tk"] for v in st.get("vp", []) for e in v["open"]})
    data = {}
    if vtk:
        try:
            data = bars(vtk)
        except Exception as ex:  # noqa: BLE001
            print("شموع:", ex)
    for v in all_vars(st):
        keep = []
        for e in v["open"]:
            q = qs.get(e["symbol"])
            if v.get("mode"):
                ok = q and q["bid"] > 0 and e["tk"] in data
                closed, why = vp_manage(e, q["bid"], data[e["tk"]], now) if ok else (False, "")
            else:
                closed, why = manage(e, v["params"], q["bid"], now) if q and q["bid"] > 0 else (False, "")
            if closed:
                e["exit_px"], e["exit_t"] = q["bid"], now.strftime("%H:%M")
                v["closed"].append(e)
                log_trade(v, e, why)
            else:
                keep.append(e)
        v["open"] = keep


def enter(st, now, force=False):
    day = now.date()
    if not force and not (now.weekday() < 5 and RTH_START <= now.time() <= LAST_ENTRY):
        return
    try:
        data = bars(TICKERS + ["SPY"])
    except Exception as ex:  # noqa: BLE001
        print("شموع:", ex)
        return
    tdate, last = day.isoformat(), st.setdefault("last_bar", {})
    for tk in TICKERS:
        if tk not in data:
            continue
        sig = engine(data[tk], data.get("SPY"), now)
        if not sig or (last.get(tk) == sig["bar"] and not force):
            continue
        last[tk] = sig["bar"]
        want = []
        for v in st["variants"]:
            p = v["params"]
            sd = side_for(p, sig)
            if sd and now.time() >= start_time(p["start"]) and triggered(p, sig, sd) and can_enter(v, tk, sd, tdate):
                want.append((v, sd))
        try:
            vpw = vp_wants(st, tk, data[tk], now, tdate)
        except Exception as ex:  # noqa: BLE001
            print("بروفايل", tk, ex)
            vpw = []
        if not want and not vpw:
            continue
        try:
            rows = chain(tk, sig["S"], {v["params"]["dte"] for v, _ in want} | ({VP_PARAMS["dte"]} if vpw else set()), day)
        except Exception as ex:  # noqa: BLE001
            print("السلسلة", tk, ex)
            continue
        for v, sd in want:
            k = pick(rows, sd, v["params"]["dte"], v["params"]["delta"], sig["S"])
            if not k:
                continue
            trig = "orb" if sig["orb"] == sd else ("vwap" if sig["vwap_x"] == sd else "any")
            v["open"].append({"t": now.isoformat(timespec="minutes"), "tdate": tdate, "tk": tk, "side": sd, "exp": k["exp"],
                              "strike": k["strike"], "symbol": k["symbol"], "entry": k["ask"], "S": round(sig["S"], 2),
                              "score": sig["total"], "adx": round(sig["adx"], 1), "trigger": trig, "peak": k["ask"]})
            print("دخول", v["vid"], tk, sd, k["exp"], k["strike"], k["ask"])
        for v, z, sg in vpw:
            k = pick(rows, z["side"], VP_PARAMS["dte"], VP_PARAMS["delta"], sig["S"])
            if not k:
                continue
            v["open"].append({"t": now.isoformat(timespec="minutes"), "tdate": tdate, "tk": tk, "side": z["side"], "exp": k["exp"],
                              "strike": k["strike"], "symbol": k["symbol"], "entry": k["ask"], "S": round(sig["S"], 2),
                              "score": sig["total"], "adx": round(sig["adx"], 1), "trigger": v["mode"], "peak": k["ask"],
                              "zone": z["key"], "stop_px": sg["stop"], "t1": z["t1"], "t2": z["t2"], "trend": z["trend"],
                              "sig_t": sg["t"].isoformat(), "lag": round((pd.Timestamp(now) - sg["t"]).total_seconds() / 60 - 5, 1)})
            print("دخول", v["vid"], tk, z["key"], k["exp"], k["strike"], k["ask"])


def main(argv):
    now = datetime.now(NY)
    force, no_send = "--force" in argv, "--no-send" in argv
    day = now.date().isoformat()
    st = load_state(day)
    if now.weekday() < 5 and RTH_START <= now.time() <= dtime(16, 30) or force:
        manage_open(st, now)
    if now.weekday() < 5 and now.time() >= REPORT_AT and st.get("evolve_day") != day and \
            any(c["tdate"] == day for v in all_vars(st) for c in v["closed"]):
        board = leaderboard(st, day)
        gen_txt = evolve(st, day, random.Random("stocks" + day))
        os.makedirs(DIR, exist_ok=True)
        with open(GENS, "a", encoding="utf-8") as f:
            f.write(gen_txt + "\n\n")
        msg = board + "\n" + vp_text(st, day) + "\n\n" + changes_text(st)
        print(msg)
        if not no_send:
            import analyst as an
            an.send(msg + "\n\n⚠️ ورقي للدراسة؛ يتداول الاتجاهين، فليس توصية.", "stocks_lab")
        st["evolve_day"] = day
    enter(st, now, force)
    save(st)


if __name__ == "__main__":
    main(sys.argv[1:])
