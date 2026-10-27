#!/usr/bin/env python3
"""ظل «الأفضل مجتمعين» H-BEST (تسجيل فقط، ورقي؛ لا يرسل ولا يغيّر أي توصية). بطلب المستخدم 27/10/2026؛ الفرضية مسجّلة في CLAUDE.md قبل الكود.

يجمع ما أعطى شيئاً إيجابياً في الاختبارات: الإشارة القوية، الإجماع، عكس سحب السيولة على 15د، عقد على السعر لليوم التالي، وخروج قصير.
كل 5 دقائق (بعد اكتمال الشمعة) يشغّل تحليل بوت SPX كاملاً ويأخذ نقاط الاتجاه بالشموع اللحظية (live_bars)، ثم أربع نسخ:
  S  = فرق النقاط ≥ 7                         SC = S + زخم آخر ساعة وأول 30 دقيقة في الجهة نفسها
  SR = S + عكس سحب السيولة (آخر شمعة 15د لـSPY) في الجهة نفسها    C = فرق ≥ MIN_EDGE + زخم آخر ساعة وأول 30 دقيقة في الجهة نفسها
الدخول: زوج SPY على السعر لليوم التالي (الجهة + المعاكس) بسعر الطلب اللحظي؛ القراءة بسعر العرض بعد 15 و30 و60 دقيقة.
المهارة = عائد عقد الجهة − متوسط العقدين (الفرق عن اختيار الجهة بعملة)؛ الأساسية عند 30 دقيقة.
الملفات: best_shadow_state.json، best_shadow_log.csv، research/best_shadow_results.md. تشغيل: python best_shadow.py [--report]
"""
import csv
import json
import os
import sys
from datetime import datetime, time as dtime, timedelta

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "best_shadow_state.json")
LOG = os.path.join(ROOT, "best_shadow_log.csv")
OUT = os.path.join(ROOT, "research", "best_shadow_results.md")
VARIANTS = ("S", "SC", "SR", "C")
NAMES = {"S": "الإشارة القوية", "SC": "القوية + الإجماع", "SR": "القوية + عكس سحب السيولة", "C": "الإجماع (فرق ≥ 3)"}
STRONG = 7.0
REPEAT_MIN = 90
LAST_ENTRY = (14, 55)
HOLD = (15, 30, 60)
MIN_N, MIN_DAYS = 40, 25
SOFT_REASONS = ("لا اتجاه واضح", "لا توجد عقود")
COLS = ["variant", "entry_time", "side", "edge", "mom", "f30", "rsweep", "strike", "exp", "spot", "side_occ", "opp_occ", "side_entry", "opp_entry", "src",
        "side_15", "opp_15", "side_30", "opp_30", "side_60", "opp_60", "exit_time", "status"]


# ============================ السياق من شموع SPY (نقي) ============================
def sgn(x):
    return 1 if x > 0 else (-1 if x < 0 else 0)


def rev_sweep(b15):
    """عكس سحب السيولة على آخر شمعة 15د: كسر قاع 20 شمعة ثم الإغلاق فوقه → −1 (هبوط)، والعكس عند القمة → +1."""
    if len(b15) < 21:
        return 0
    last, prev = b15.iloc[-1], b15.iloc[-21:-1]
    lo, hi = float(prev["Low"].min()), float(prev["High"].max())
    if last["Low"] < lo and last["Close"] > lo:
        return -1
    if last["High"] > hi and last["Close"] < hi:
        return 1
    return 0


def context(b5, n):
    """{mom, f30, rsweep} من شموع SPY 5د المكتملة (فهرس نيويورك = بداية الشمعة)."""
    import pandas as pd
    done = b5[b5.index <= n - timedelta(minutes=5)]
    done = done[(done.index.time >= dtime(9, 30)) & (done.index.time < dtime(16, 0))]
    today = done[done.index.date == n.date()]
    out = {"mom": 0, "f30": 0, "rsweep": 0}
    if len(today) >= 6:
        first = today[today.index.time < dtime(10, 0)]
        if len(first) == 6:
            out["f30"] = sgn(float(first["Close"].iloc[-1]) / float(first["Open"].iloc[0]) - 1)
        back = today[today.index <= today.index[-1] - timedelta(minutes=60)]
        if len(back):
            out["mom"] = sgn(float(today["Close"].iloc[-1]) / float(back["Close"].iloc[-1]) - 1)
    r = done.resample("15min", label="left", closed="left")
    b15 = pd.DataFrame({"Low": r["Low"].min(), "High": r["High"].max(), "Close": r["Close"].last()}).dropna()
    b15 = b15[b15.index <= n - timedelta(minutes=15)]
    out["rsweep"] = rev_sweep(b15)
    return out


# ============================ القرار (نقي) ============================
def decide(n, pts, reasons, shock_level, ctx, last, min_edge):
    """{نسخة: (جهة، فرق)}. pts = (bull, bear) بالشموع اللحظية أو None."""
    if not pts or [r for r in reasons if not r.startswith(SOFT_REASONS)] or (n.hour, n.minute) > LAST_ENTRY:
        return {}
    bull, bear = pts[0], pts[1]
    e = abs(bull - bear) - (1.0 if shock_level == 2 else 0.0)
    side = "CALL" if bull > bear else "PUT"
    s = 1 if side == "CALL" else -1
    agree = ctx["mom"] == s and ctx["f30"] == s
    rules = {"S": e >= STRONG, "SC": e >= STRONG and agree, "SR": e >= STRONG and ctx["rsweep"] == s, "C": e >= min_edge and agree}
    out = {}
    for v, ok in rules.items():
        if not ok:
            continue
        t = (last.get(v) or {}).get(side)
        if t and (n - datetime.fromisoformat(t)).total_seconds() / 60 < REPEAT_MIN:
            continue
        out[v] = (side, e)
    return out


def next_trading_day(d, holidays):
    d += timedelta(days=1)
    while d.weekday() >= 5 or d.isoformat() in holidays:
        d += timedelta(days=1)
    return d


# ============================ الحالة والسجل ============================
def _load():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"open": [], "last": {}}


def _save(st):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def _append(rows):
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        w.writerows({k: r.get(k, "") for k in COLS} for r in rows)


def update_open(st, n, quote):
    """قراءة 15/30/60 دقيقة بسعر العرض؛ إغلاق بعد 60 أو نهاية اليوم. quote(occ) → bid أو None."""
    still, done = [], []
    for it in st["open"]:
        age = (n - datetime.fromisoformat(it["entry_time"])).total_seconds() / 60
        for h in HOLD:
            if age >= h - 2 and it.get(f"side_{h}") in (None, ""):
                bs, bo = quote(it["side_occ"]), quote(it["opp_occ"])
                if bs is not None and bo is not None:
                    it[f"side_{h}"], it[f"opp_{h}"] = round(bs, 2), round(bo, 2)
        if it.get("side_60") not in (None, ""):
            done.append(dict(it, exit_time=n.isoformat(timespec="minutes"), status="closed"))
        elif age > 150 or n.date().isoformat() != it["entry_time"][:10]:
            done.append(dict(it, exit_time=n.isoformat(timespec="minutes"), status="no_quote"))
        else:
            still.append(it)
    st["open"] = still
    return done


def run(n, a, cmp, ctx, lq, ob):
    st = _load()

    def bid(occ):
        z, _ = lq.best_quote(occ)
        return z["bid"] if z else None
    done = update_open(st, n, bid)
    if done:
        _append(done)
    live = cmp.get("live")
    sigs = decide(n, live, a["reasons"], a["shock"]["level"], ctx, st["last"], ob.MIN_EDGE)
    if sigs:
        S, _ = lq.spot("SPY")
        if not S:
            print("ظل الأفضل مجتمعين: لا سعر SPY لحظي، لا دخول")
            sigs = {}
        else:
            K, exp = round(S), next_trading_day(n.date(), ob.HOLIDAYS).isoformat()
    quotes = {}
    for v, (side, e) in sigs.items():
        opp = "PUT" if side == "CALL" else "CALL"
        so, oo = lq.occ("SPY", exp, side, K), lq.occ("SPY", exp, opp, K)
        for c in (so, oo):
            if c not in quotes:
                quotes[c] = lq.best_quote(c)
        (zs, src), (zo, _) = quotes[so], quotes[oo]
        if not zs or not zo:
            print(f"ظل الأفضل مجتمعين {v}: لا سعر لحظي حديث، لا دخول")
            continue
        st["open"].append({"variant": v, "entry_time": n.isoformat(timespec="minutes"), "side": side, "edge": round(e, 2), "mom": ctx["mom"], "f30": ctx["f30"],
                           "rsweep": ctx["rsweep"], "strike": K, "exp": exp, "spot": round(S, 2), "side_occ": so, "opp_occ": oo,
                           "side_entry": round(zs["ask"], 2), "opp_entry": round(zo["ask"], 2), "src": src})
        st["last"].setdefault(v, {})[side] = n.isoformat(timespec="minutes")
        print(f"ظل الأفضل مجتمعين {v}: {side} {K} لانتهاء {exp} بسعر {zs['ask']:.2f} (فرق {e:.1f})")
    _save(st)
    return sigs


# ============================ التقرير ============================
def events(rows, h=30):
    out = []
    for r in rows:
        try:
            se, oe, sx, ox = (float(r[k]) for k in ("side_entry", "opp_entry", f"side_{h}", f"opp_{h}"))
        except (TypeError, ValueError, KeyError):
            continue
        if se <= 0 or oe <= 0:
            continue
        rs, ro = sx / se - 1, ox / oe - 1
        out.append({"variant": r["variant"], "day": r["entry_time"][:10], "ret": rs, "skill": rs - (rs + ro) / 2})
    return out


def boot(by_day, n=3000, seed=37):
    days = sorted(by_day)
    if len(days) < 8:
        return None
    S = np.array([sum(by_day[d]) for d in days])
    N = np.array([len(by_day[d]) for d in days])
    idx = np.random.default_rng(seed).integers(0, len(days), (n, len(days)))
    m = S[idx].sum(1) / N[idx].sum(1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def stat(ev, key):
    by = {}
    for e in ev:
        by.setdefault(e["day"], []).append(e[key])
    v = [e[key] for e in ev]
    return {"n": len(v), "days": len(by), "mean": float(np.mean(v)) if v else None, "ci": boot(by)}


def pc(x):
    return "-" if x is None else f"{100 * x:+.1f}%"


def report(rows):
    ev = {h: events(rows, h) for h in HOLD}
    L = ["# H-BEST: الأفضل مجتمعين مقابل العشوائي (نتائج آلية، ظل)", "",
         "المهارة = عائد عقد الجهة − متوسط العقدين. زوج SPY على السعر لليوم التالي، شراء بالطلب وقراءة بالعرض. الأساسية 30 دقيقة.", "",
         "| النسخة | إشارات | أيام | مهارة 15د | مهارة 30د | فاصل 95% (30د) | مهارة 60د | العائد الصافي 30د |", "|---|---|---|---|---|---|---|---|"]
    ver = []
    for v in VARIANTS:
        s = {h: stat([e for e in ev[h] if e["variant"] == v], "skill") for h in HOLD}
        r30 = stat([e for e in ev[30] if e["variant"] == v], "ret")
        ci = "-" if not s[30]["ci"] else f"{100 * s[30]['ci'][0]:+.1f}…{100 * s[30]['ci'][1]:+.1f}%"
        L.append(f"| {v}: {NAMES[v]} | {s[30]['n']} | {s[30]['days']} | {pc(s[15]['mean'])} | {pc(s[30]['mean'])} | {ci} | {pc(s[60]['mean'])} | {pc(r30['mean'])} |")
        x = s[30]
        if x["n"] < MIN_N or x["days"] < MIN_DAYS or not x["ci"]:
            ver.append(f"H-BEST {v}: لا حكم بعد ({x['n']} من {MIN_N} إشارة، {x['days']} من {MIN_DAYS} يوماً)")
        else:
            lo, hi = x["ci"]
            ver.append(f"H-BEST {v}: " + ("تتفوق على العشوائي" if lo > 0 else "أسوأ من العشوائي" if hi < 0 else "لا تتفوق على العشوائي") + f" (مهارة 30د {pc(x['mean'])})")
    return L + [""] + [f"⚖️ {x}" for x in ver]


def main(argv):
    if "--report" in argv:
        rows = list(csv.DictReader(open(LOG, encoding="utf-8"))) if os.path.exists(LOG) else []
        L = report(rows)
        with open(OUT, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(L) + "\n")
        print("\n".join(L))
        return 0
    os.environ.setdefault("LIVE_BARS_SPX", "shadow")
    if os.environ["LIVE_BARS_SPX"] != "shadow":
        print("ظل الأفضل مجتمعين يحتاج LIVE_BARS_SPX=shadow (القرار من الشموع اللحظية في المقارنة)؛ لا تشغيل.")
        return 0
    import live_bars
    import live_quotes as lq
    import options_bot as ob
    n = ob.now_ny()
    if not ob.in_window(n):
        print("خارج وقت التداول.")
        return 0
    a = ob.analyze(True)
    try:
        ctx = context(live_bars.spy_bars(n, days=4), n)
    except Exception as e:  # noqa: BLE001
        print("سياق SPY:", type(e).__name__)
        ctx = {"mom": 0, "f30": 0, "rsweep": 0}
    run(n, a, dict(ob.LIVE_CMP), ctx, lq, ob)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
