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
    for v in st["variants"]:
        for c in v["closed"]:
            q = qs.get(c.get("symbol"))
            if q and "exit_px" in c and q["bid"] > c.get("after_hi", 0):
                c["after_hi"], c["after_hi_t"] = q["bid"], now.strftime("%H:%M")


def tracked_symbols(st, day):
    return {c["symbol"] for v in st["variants"] for c in v["closed"] if "exit_px" in c and c["tdate"] >= day}


def after_rows(st, day):
    return [{"vid": v["vid"], "tdate": c["tdate"], "t": c["t"], "side": c["side"], "strike": c["strike"], "entry": c["entry"],
             "exit": c["exit_px"], "exit_t": c.get("exit_t"), "after_hi": c.get("after_hi"), "after_hi_t": c.get("after_hi_t"), "usd": c["usd"]}
            for v in st["variants"] for c in v["closed"] if c["tdate"] == day and "exit_px" in c]


def left_text(v, day):
    """متوسط ما بلغه العقد فوق سعر البيع بعد الخروج، لعقد واحد."""
    x = [(c["after_hi"] - c["exit_px"]) * 100 for c in v["closed"] if c["tdate"] == day and "exit_px" in c and c.get("after_hi")]
    return f"\n   بعد الخروج بلغت عقودها أعلى من سعر بيعها بمتوسط {sum(x) / len(x):+,.0f}$ للعقد" if x else ""


# ====================== التشغيل ======================
def load_state(day):
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        st = {"gen": 1, "variants": initial_variants(day), "last_bar": None, "evolve_day": ""}
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
    return {"S": S, "total": total, "det": det, "new_bar": new_bar, "bar": bar, "hit": hit}


def main(argv):
    now = datetime.now(NY)
    force, no_send = "--force" in argv, "--no-send" in argv
    day = now.date().isoformat()
    st = load_state(day)
    # 1) إدارة المفتوح لكل النسخ
    syms = sorted({e["symbol"] for v in st["variants"] for e in v["open"]} | (tracked_symbols(st, day) if now.time() <= ms.RTH_END else set()))
    if syms:
        try:
            qs = {q["symbol"]: q for q in ms.quotes(syms)}
        except Exception as e:  # noqa: BLE001
            print("أسعار:", e)
            qs = {}
        for v in st["variants"]:
            keep = []
            for e in v["open"]:
                q = qs.get(e["symbol"])
                closed, why = manage(e, v["params"], q["bid"], now) if q else (False, "")
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
            any(c["tdate"] == day for v in st["variants"] for c in v["closed"]):
        board = leaderboard(st, day)
        os.makedirs(DIR, exist_ok=True)
        rows = after_rows(st, day)
        if rows:
            pd.DataFrame(rows).to_csv(AFTER, mode="a", header=not os.path.exists(AFTER), index=False)
        gen_txt = evolve(st, day, random.Random(day))
        ensure_exits(st)
        with open(GENS, "a", encoding="utf-8") as f:
            f.write(gen_txt + "\n\n")
        msg = board + "\n\n" + changes_text(st)
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
    save(st)


if __name__ == "__main__":
    main(sys.argv[1:])
