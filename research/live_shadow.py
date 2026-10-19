#!/usr/bin/env python3
"""ظل لحظي لتوصيات SPX (تسجيل فقط؛ لا يرسل ولا يغيّر أي توصية أو ملف للبوت).

السؤال: كم من خسارة البوت سببه تأخر البيانات وحده؟ ومعه: هل العقد الأقرب للسعر أفضل؟
لكل توصية SPX في spx_positions.json نشتري «على الورق» بسعر الطلب اللحظي من Alpaca (indicative) أول ما نراها، ثم:
  أ) «كما يعمل البوت الآن»: عقد البوت، والخروج عند تنبيهات البوت نفسها (stop / tp / exp_final في spx_positions.json،
     وهي محسوبة من ياهو المتأخر) بسعر البيع اللحظي لحظة رؤية التنبيه.
  ب) «لو كانت المتابعة لحظية»: نفس القواعد (+50% / −40% بعد 15 دقيقة من التوصية / بيع 15:30) على سعر البيع اللحظي،
     لثلاثة عقود: bot (سترايك البوت)، atm (على السعر)، d30 (دلتا ≈ 0.30 محسوبة من السعر لأن indicative بلا دلتا).
الرقم الذهبي: توصيات خسرت في (أ) وكانت رابحة في (ب) لعقد البوت نفسه = خسارة سببها التأخير وحده.
ونسجّل الفرق بين سعر الدخول الذي عرضه البوت (ياهو) والسعر اللحظي الفعلي.

لا يُستخدم «آخر صفقة» أبداً (متأخر 15 دقيقة في indicative)، ونتجاهل أي تسعيرة عمرها > 120 ثانية.
الملفات (كلها جديدة): live_shadow_state.json، live_shadow_track.csv، live_shadow_log.csv، research/live_shadow_results.md
يحتاج: ALPACA_KEY، ALPACA_SECRET.   تشغيل: python research/live_shadow.py [--report]
"""
import csv
import json
import math
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import options_bot as ob          # noqa: E402  قراءة فقط: الإعدادات والدوال الحسابية
import alpaca_live_check as alc   # noqa: E402  قراءة سلسلة Alpaca المصحّحة

STATE = os.path.join(ROOT, "live_shadow_state.json")
TRACK = os.path.join(ROOT, "live_shadow_track.csv")
LOG = os.path.join(ROOT, "live_shadow_log.csv")
REPORT = os.path.join(HERE, "live_shadow_results.md")
FEED = "indicative"
MAX_AGE = 120            # ثانية: تسعيرة أقدم من هذا لا تُستخدم
VARIANTS = ("bot", "atm", "d30")
FINAL = (15, 30)
ALERT_KINDS = ("stop", "tp", "exp_final")
LOG_COLS = ["date", "pos_id", "variant", "side", "strike", "exp", "sig_time", "entry_time", "entry_spot", "entry",
            "bot_shown_entry", "qty_budget", "live_out", "live_time", "live_pnl", "alert_out", "alert_time", "alert_pnl",
            "peak_pnl", "close_pnl", "quotes", "stale"]


# ============================ أدوات ============================
def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {"date": None, "items": []}


def save_state(s):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


def snapshot(exp, guess):
    """{(النوع، السترايك): صف} وسعر SPX من التكافؤ، من Alpaca indicative."""
    st, snaps = alc.chain("SPXW", FEED, exp, guess - alc.WIDE, guess + alc.WIDE)
    if st != 200:
        raise RuntimeError(f"Alpaca ({st}): {str(snaps)[:160]}")
    rows = alc.rows_of(snaps)
    spot = alc.parity_spot(rows, guess)
    return {(r["type"], r["strike"]): r for r in rows}, spot


def usable(r):
    return (r is not None and r.get("bid") and r.get("ask") and r["bid"] > 0 and r["ask"] > 0
            and r.get("q_age") is not None and r["q_age"] <= MAX_AGE)


def implied_vol(price, S, K, T, side):
    lo, hi = 0.01, 5.0
    if price <= max(0.0, (S - K) if side == "CALL" else (K - S)) + 1e-6:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if ob.bs_price(S, K, T, mid, side) > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def pick(book, side, S, n, bot_strike):
    ks = sorted(k for (t, k), r in book.items() if t == side and usable(r))
    out = {}
    if bot_strike in ks:
        out["bot"] = bot_strike
    atm = min((k for k in ks if k >= S), default=None) if side == "CALL" else max((k for k in ks if k <= S), default=None)
    if atm is not None:
        out["atm"] = atm
    T = max((n.replace(hour=16, minute=0, second=0, microsecond=0) - n).total_seconds() / 3600, 0.25) / (24 * 365)
    best = None
    for k in ks:
        if (side == "CALL" and k < S) or (side == "PUT" and k > S):
            continue
        iv = implied_vol(book[(side, k)]["mid"], S, k, T, side)
        if iv is None:
            continue
        d = ob.abs_delta(S, k, T, iv, side)
        if best is None or abs(d - 0.30) < abs(best[0] - 0.30):
            best = (d, k)
    if best:
        out["d30"] = best[1]
    return out


def first_alert(p):
    al = p.get("alerts") or {}
    hits = sorted((t, k) for k, t in al.items() if k in ALERT_KINDS and t)
    return hits[0] if hits else None


# ============================ التشغيل ============================
def finalize(state):
    items = state.get("items", [])
    if not items:
        return
    new = not os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        if new:
            w.writeheader()
        for it in items:
            if it.get("live_out") in ("", None) and it.get("close_pnl") not in ("", None):
                it.update(live_out="final", live_time="آخر تقييم", live_pnl=it["close_pnl"])
            w.writerow({k: it.get(k, "") for k in LOG_COLS})
    state["items"] = []


def run(n=None):
    n = n or ob.now_ny()
    now_s = n.isoformat(timespec="minutes")
    today = n.date().isoformat()
    state = load_state()
    if state.get("date") and state["date"] != today:
        finalize(state)
    state["date"] = today
    if not ob.market_day(n):
        save_state(state)
        return
    pos = {p["id"]: p for p in ob.load_positions()["positions"] if p.get("sym") == "SPX" and p.get("exp") == today}
    if not pos and not state["items"]:
        save_state(state)
        print("لا توصيات SPX اليوم.")
        return
    st, d = alc.get(f"{alc.DATA}/v2/stocks/SPY/trades/latest")
    px = (d.get("trade") or {}).get("p") if st == 200 and isinstance(d, dict) else None
    if not px:
        print("تعذّر جلب SPY:", st, str(d)[:120])
        save_state(state)
        return
    try:
        book, S = snapshot(today, px * alc.RATIO_GUESS)
    except Exception as e:
        print("تعذّر جلب السلسلة:", e)
        save_state(state)
        return
    S = S or px * alc.RATIO_GUESS
    hm = (n.hour, n.minute)
    have = {it["pos_id"] for it in state["items"]}
    for pid, p in pos.items():
        if pid in have or hm >= (16, 0):
            continue
        for v, k in pick(book, p["side"], S, n, float(p["strike"])).items():
            ask = book[(p["side"], k)]["ask"]
            state["items"].append({
                "date": today, "pos_id": pid, "variant": v, "side": p["side"], "strike": k, "exp": today,
                "sig_time": p["time"], "entry_time": now_s, "entry_spot": round(S, 2), "entry": round(ask, 2),
                "bot_shown_entry": p.get("entry"),
                "qty_budget": round(ob.RISK_BUDGET_USD / (ask * 100 * ob.STOP_LOSS), 3),
                "live_out": "", "live_time": "", "live_pnl": "", "alert_out": "", "alert_time": "", "alert_pnl": "",
                "peak_pnl": 0.0, "close_pnl": "", "quotes": 0, "stale": 0})
    track = []
    for it in state["items"]:
        if it["entry_time"] == now_s:
            continue
        r = book.get((it["side"], it["strike"]))
        if not usable(r):
            it["stale"] += 1
            continue
        bid = r["bid"]
        pnl = bid / it["entry"] - 1
        it["quotes"] += 1
        it["peak_pnl"] = round(max(it["peak_pnl"], pnl), 3)
        it["close_pnl"] = round(pnl, 3)
        if it["live_out"] == "":                                   # (ب) قواعد البوت على السعر اللحظي
            try:
                age = (n - datetime.fromisoformat(it["sig_time"])).total_seconds() / 60
            except Exception:
                age = 9999
            out = ("stop" if age >= ob.MIN_AGE_MIN and pnl <= -ob.STOP_LOSS else
                   "tp" if pnl >= ob.TAKE_PROFIT else "final" if hm >= FINAL else None)
            if out:
                it.update(live_out=out, live_time=n.strftime("%H:%M"), live_pnl=round(pnl, 3))
        if it["variant"] == "bot" and it["alert_out"] == "":      # (أ) تنبيهات البوت الفعلية (ياهو المتأخر)
            fa = first_alert(pos.get(it["pos_id"], {}))
            if fa and fa[0] <= now_s:
                it.update(alert_out=fa[1], alert_time=fa[0][11:16], alert_pnl=round(pnl, 3))
        track.append([now_s, it["pos_id"], it["variant"], it["strike"], round(S, 2), round(bid, 2),
                      round(r["q_age"]), round(pnl, 3), it["live_out"] or "open", it["alert_out"]])
    if track:
        new = not os.path.exists(TRACK)
        with open(TRACK, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "pos_id", "variant", "strike", "spot", "bid", "quote_age_s", "pnl", "live", "alert"])
            w.writerows(track)
    save_state(state)
    print(f"سُجّل {len(track)} تقييماً، SPX ≈ {S:,.2f}.")


# ============================ التقرير ============================
def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def report():
    rows = list(csv.DictReader(open(LOG, encoding="utf-8"))) if os.path.exists(LOG) else []
    rows += [{k: str(it.get(k, "")) for k in LOG_COLS} for it in load_state().get("items", [])]
    names = {"bot": "عقد البوت", "atm": "على السعر", "d30": "دلتا 0.30"}
    L = ["# ظل لحظي لتوصيات SPX (Alpaca) — نتائج آلية", "",
         "- الشراء بسعر الطلب اللحظي (Alpaca indicative) عند أول رؤية للتوصية، والتقييم بسعر البيع اللحظي كل 5 دقائق.",
         "- «لحظي» = قواعد البوت (+50% / −40% بعد 15د / 15:30) على السعر اللحظي. «تنبيهات البوت» = الخروج عند تنبيه البوت "
         "الفعلي المحسوب من ياهو المتأخر، بالسعر اللحظي لحظة رؤيته.",
         "- ⚠️ للمراقبة لا للحكم: لا قرار قبل 20 توصية على الأقل. أسعار indicative استرشادية.", "",
         "## (ب) لو كانت المتابعة لحظية", "",
         "| العقد | صفقات | أيام | هدف / وقف / 15:30 | متوسط الصفقة | للعقد | لكل 150$ مخاطرة | لو احتفظت للإغلاق |",
         "|---|---|---|---|---|---|---|---|"]
    for v in VARIANTS:
        R = [r for r in rows if r["variant"] == v and _f(r["live_pnl"]) is not None]
        if not R:
            L.append(f"| {names[v]} | 0 | 0 | - | - | - | - | - |")
            continue
        k = len(R)
        pn = [_f(r["live_pnl"]) for r in R]
        en = [_f(r["entry"]) for r in R]
        qb = [_f(r["qty_budget"]) for r in R]
        cl = [_f(r["close_pnl"]) for r in R if _f(r["close_pnl"]) is not None]
        o = [r["live_out"] for r in R]
        L.append(f"| {names[v]} | {k} | {len({r['date'] for r in R})} | {100 * o.count('tp') / k:.0f}% / "
                 f"{100 * o.count('stop') / k:.0f}% / {100 * o.count('final') / k:.0f}% | {100 * sum(pn) / k:+.1f}% | "
                 f"≈{sum(p * e * 100 for p, e in zip(pn, en)) / k:+.0f}$ | "
                 f"≈{sum(p * e * 100 * q for p, e, q in zip(pn, en, qb)) / k:+.0f}$ | "
                 f"{(100 * sum(cl) / len(cl)) if cl else 0:+.1f}% |")
    B = [r for r in rows if r["variant"] == "bot" and _f(r["live_pnl"]) is not None and _f(r["alert_pnl"]) is not None]
    L += ["", "## (أ) مقابل (ب): تكلفة التأخير على عقد البوت نفسه", ""]
    if B:
        a = [_f(r["alert_pnl"]) for r in B]
        b = [_f(r["live_pnl"]) for r in B]
        gold = sum(1 for x, y in zip(a, b) if x < 0 < y)
        L += [f"- توصيات فيها الطريقتان: {len(B)}",
              f"- متوسط الصفقة بتنبيهات البوت (متأخر): {100 * sum(a) / len(a):+.1f}% | بالمتابعة اللحظية: {100 * sum(b) / len(b):+.1f}%",
              f"- **الرقم الذهبي — خسرت بسبب التأخير وحده: {gold} من {len(B)} ({100 * gold / len(B):.0f}%)**",
              f"- العكس (ربحت بالتأخير وخسرت باللحظي): {sum(1 for x, y in zip(a, b) if x > 0 > y)}"]
    else:
        L.append("- لا توصيات مكتملة بالطريقتين بعد.")
    G = [(_f(r["bot_shown_entry"]), _f(r["entry"])) for r in rows if r["variant"] == "bot"]
    G = [(s, e) for s, e in G if s and e]
    if G:
        gaps = [s / e - 1 for s, e in G]
        L += ["", f"- سعر الدخول الذي عرضه البوت مقابل السعر اللحظي الفعلي: متوسط الفرق {100 * sum(gaps) / len(gaps):+.0f}% "
                  f"(أكبر فرق {100 * max(gaps, key=abs):+.0f}%) على {len(G)} توصية."]
    L += ["", "- إذا كان الرقم الذهبي مرتفعاً → نقل أسعار متابعة البوت إلى Alpaca (بموافقة المستخدم بعد عينة كافية).",
          "- إذا تفوّق «على السعر» أو «دلتا 0.30» بوضوح لكل 150$ مخاطرة → مراجعة نطاق سعر العقد (100–350$)."]
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    if "--report" in sys.argv:
        report()                     # لا يحتاج مفاتيح
    elif not alc.KEY or not alc.SEC:
        print("✗ المفاتيح غير موجودة (ALPACA_KEY / ALPACA_SECRET).")
        sys.exit(1)
    else:
        run()
