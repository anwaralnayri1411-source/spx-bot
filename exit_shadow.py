"""تجربة ظلّ: عقد البوت مقابل عقد ATM لليوم التالي (1DTE)، بأسعار Alpaca اللحظية، بقاعدتي خروج (تسجيل فقط).

لا يرسل ولا يغيّر أي توصية. لكل توصية SPX/SPY اليوم نشتري «على الورق» عقدين في اللحظة نفسها بسعر الطلب (ask):
  bot  = عقد البوت نفسه (سترايكه وانتهاؤه اليوم)
  atm1 = أقرب سترايك للسعر الحالي، نفس الاتجاه، ينتهي في يوم التداول التالي
ونقيّمهما بسعر البيع (bid) كل تشغيل بقاعدتين على كلٍّ منهما:
  r5040 = قاعدة البوت (+50% هدف، −40% وقف بعد 15 دقيقة من التوصية، بيع قبل الإغلاق بـ30 دقيقة)
  t60   = خروج ثابت بعد 60 دقيقة من التوصية (أو قبل الإغلاق بـ30 دقيقة إن جاء أولاً)
الفرق بين العقدين = أثر اختيار العقد؛ والفرق بين القاعدتين = أثر طريقة الخروج. الأسعار حيّة من live_quotes (Alpaca ثم Tastytrade).

الملفات (جديدة): exit_shadow_state.json، exit_shadow_log.csv، research/exit_shadow_results.md.
تشغيل: python exit_shadow.py [--report]
"""
import csv
import json
import os
import sys
from datetime import date, datetime, timedelta

import data_gaps
import live_quotes as lq
import options_bot as ob   # قراءة فقط: الإعدادات والتوقيت

STATE = "exit_shadow_state.json"
LOG = "exit_shadow_log.csv"
REPORT = os.path.join("research", "exit_shadow_results.md")
VARIANTS = ("bot", "atm1")
ROOT = {"SPX": ("SPXW", 5.0), "SPY": ("SPY", 1.0)}   # جذر OCC وخطوة السترايك
MAX_LATE_MIN = 15      # توصية نراها بعد أكثر من هذا لا تُسجَّل (المقارنة لن تكون عادلة)
HOLD_MIN = 60
FINAL_BEFORE_CLOSE_MIN = 30
LOG_COLS = ["date", "pos_id", "variant", "side", "strike", "exp", "entry_time", "entry_spot", "entry", "r5040_out",
            "r5040_time", "r5040_pnl", "t60_time", "t60_pnl", "peak_pnl", "close_pnl", "quotes", "sym"]


# ============================ دوال نقية (قابلة للاختبار) ============================
def next_trading_day(d):
    n = d + timedelta(days=1)
    while n.weekday() >= 5 or n.isoformat() in ob.HOLIDAYS:
        n += timedelta(days=1)
    return n


def atm_strike(spot, step):
    return round(spot / step) * step


def candidates(rec, spot):
    """{variant: (رمز OCC، السترايك، الانتهاء)} لتوصية من spx_positions.json."""
    root, step = ROOT[rec["sym"]]
    exp1 = next_trading_day(date.fromisoformat(rec["exp"])).isoformat()
    k1 = atm_strike(spot, step)
    k0 = float(rec["strike"])
    return {"bot": (lq.occ(root, rec["exp"], rec["side"], k0), k0, rec["exp"]),
            "atm1": (lq.occ(root, exp1, rec["side"], k1), k1, exp1)}


def final_minute(n):
    end = ob.session_end(n)
    return end[0] * 60 + end[1] - FINAL_BEFORE_CLOSE_MIN


def step_item(it, bid, now_min, age_rec, age_entry, final_min):
    """يحدّث عنصراً بتقييم جديد (bid = سعر البيع). يعدّل it في مكانه."""
    pnl = bid / it["entry"] - 1
    it["quotes"] += 1
    it["peak_pnl"] = round(max(it["peak_pnl"], pnl), 3)
    it["close_pnl"] = round(pnl, 3)
    hhmm = f"{now_min // 60:02d}:{now_min % 60:02d}"
    if not it["r5040_out"]:
        out = ("stop" if age_rec >= ob.MIN_AGE_MIN and pnl <= -ob.STOP_LOSS else
               "tp" if pnl >= ob.TAKE_PROFIT else "final" if now_min >= final_min else "")
        if out:
            it.update(r5040_out=out, r5040_time=hhmm, r5040_pnl=round(pnl, 3))
    if it["t60_pnl"] == "" and (age_entry >= HOLD_MIN or now_min >= final_min):
        it.update(t60_time=hhmm, t60_pnl=round(pnl, 3))


# ============================ التشغيل ============================
def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {"date": None, "items": [], "skipped": []}


def save_state(s):
    with open(STATE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


def finalize(state):
    items = state.get("items", [])
    state["skipped"] = []
    if not items:
        return
    new = not os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        if new:
            w.writeheader()
        for it in items:
            if not it["r5040_out"] and it["close_pnl"] != "":          # لم يُشغَّل بعد وقت البيع النهائي
                it.update(r5040_out="final", r5040_time="آخر تقييم", r5040_pnl=it["close_pnl"])
            if it["t60_pnl"] == "" and it["close_pnl"] != "":
                it.update(t60_time="آخر تقييم", t60_pnl=it["close_pnl"])
            w.writerow({k: it.get(k, "") for k in LOG_COLS})
    state["items"] = []


def run(n=None):
    n = n or ob.now_ny()
    today = n.date().isoformat()
    state = load_state()
    if state.get("date") and state["date"] != today:
        finalize(state)
    state["date"] = today
    state.setdefault("skipped", [])
    if not ob.market_day(n):
        save_state(state)
        return
    pos = [p for p in ob.load_positions()["positions"] if p.get("sym") in ROOT and p.get("exp") == today]
    done = {it["pos_id"] for it in state["items"]} | set(state["skipped"])
    new_pos = []
    for p in pos:
        if p["id"] in done:
            continue
        try:
            late = (n - datetime.fromisoformat(p["time"])).total_seconds() / 60
        except Exception:
            late = 0
        if late > MAX_LATE_MIN or (n.hour, n.minute) >= (15, 30):
            state["skipped"].append(p["id"])
            print(f"تجاهل {p['id']}: متأخر {late:.0f} دقيقة أو قرب الإغلاق.")
            continue
        new_pos.append(p)
    items = state["items"]
    if not new_pos and not items:
        save_state(state)
        print("لا توصيات SPX/SPY لتتبّعها الآن.")
        return
    spots, wanted = {}, []
    for p in new_pos:
        if p["sym"] not in spots:
            spots[p["sym"]] = lq.spot(p["sym"])[0]
        if spots[p["sym"]] is None:
            data_gaps.note("exit_shadow", "exit_shadow_spot", detail=p["sym"])
            continue
        p["_c"] = candidates(p, spots[p["sym"]])
        wanted += [c[0] for c in p["_c"].values()]
    wanted += [it["occ"] for it in items]
    q = lq.chain_quotes(wanted) if wanted else {}
    now_min = n.hour * 60 + n.minute
    fm = final_minute(n)
    stamp = n.isoformat(timespec="minutes")
    for p in new_pos:
        if "_c" not in p:
            continue
        got = {v: q.get(c[0]) for v, c in p["_c"].items()}
        if not all(got.values()):                       # نسجّل التوصية فقط إن توفر العقدان معاً (مقارنة عادلة)
            data_gaps.note("exit_shadow", "exit_shadow_quote", detail=p["sym"])
            continue
        for v, (occ, k, exp) in p["_c"].items():
            items.append({"date": today, "sym": p["sym"], "pos_id": p["id"], "variant": v, "side": p["side"],
                          "strike": k, "exp": exp, "occ": occ, "sig_time": p["time"], "entry_time": stamp,
                          "entry_spot": round(spots[p["sym"]], 2), "entry": round(got[v][0]["ask"], 2),
                          "r5040_out": "", "r5040_time": "", "r5040_pnl": "", "t60_time": "", "t60_pnl": "",
                          "peak_pnl": 0.0, "close_pnl": "", "quotes": 0})
    for it in items:
        z = q.get(it["occ"])
        if z is None or it["entry_time"] == stamp:
            continue
        try:
            age_rec = (n - datetime.fromisoformat(it["sig_time"])).total_seconds() / 60
            age_entry = (n - datetime.fromisoformat(it["entry_time"])).total_seconds() / 60
        except Exception:
            age_rec = age_entry = 0
        step_item(it, z[0]["bid"], now_min, age_rec, age_entry, fm)
    save_state(state)
    print(f"تتبّع {len(items)} عنصراً ({len({i['pos_id'] for i in items})} توصية).")


# ============================ التقرير ============================
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def summarize(rows, variant, rule):
    v = [x for x in (_num(r[f"{rule}_pnl"]) for r in rows if r["variant"] == variant) if x is not None]
    if not v:
        return None
    s = sorted(v)
    return {"n": len(v), "mean": sum(v) / len(v), "median": s[len(s) // 2], "wins": sum(1 for x in v if x > 0)}


def paired(rows, rule):
    by = {}
    for r in rows:
        x = _num(r[f"{rule}_pnl"])
        if x is not None:
            by.setdefault(r["pos_id"], {})[r["variant"]] = x
    return [d["atm1"] - d["bot"] for d in by.values() if "atm1" in d and "bot" in d]


def report():
    rows = list(csv.DictReader(open(LOG, encoding="utf-8"))) if os.path.exists(LOG) else []
    rows += [{k: str(it.get(k, "")) for k in LOG_COLS} for it in load_state().get("items", [])]   # اليوم الجاري (قد يكون ناقصاً)
    names = {"bot": "عقد البوت (اليوم)", "atm1": "ATM لليوم التالي"}
    rules = {"r5040": "قاعدة البوت +50/−40", "t60": "خروج بعد 60 دقيقة"}
    L = ["# تجربة ظلّ: عقد البوت مقابل ATM ليوم واحد (نتائج آلية)", "",
         "- أسعار Alpaca/Tastytrade اللحظية: شراء بسعر الطلب وتقييم بسعر البيع (فرق السعر محسوب)، والعمولة غير محسوبة.",
         f"- التوصيات المشمولة: {len({r['pos_id'] for r in rows})} في {len({r['date'] for r in rows})} يوم. "
         "⚠️ للمراقبة لا للحكم: لا قرار قبل 20 توصية على الأقل، والعينة الفعلية = عدد الأيام، وتوصيات اليوم نفسه مرتبطة ببعضها.",
         "- لا يغيّر هذا التقرير أي توصية.", "",
         "| العقد | القاعدة | صفقات | متوسط | وسيط | رابحة |", "|---|---|---|---|---|---|"]
    for v in VARIANTS:
        for r, rn in rules.items():
            s = summarize(rows, v, r)
            L.append(f"| {names[v]} | {rn} | {s['n']} | {100 * s['mean']:+.1f}% | {100 * s['median']:+.1f}% | {s['wins']}/{s['n']} |"
                     if s else f"| {names[v]} | {rn} | 0 | - | - | - |")
    L.append("")
    for r, rn in rules.items():
        d = paired(rows, r)
        if d:
            L.append(f"- **ATM1 − عقد البوت** ({rn}، {len(d)} توصية): متوسط {100 * sum(d) / len(d):+.1f} نقطة مئوية، "
                     f"وأفضل في {sum(1 for x in d if x > 0)} من {len(d)}.")
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    if "--report" in sys.argv:
        report()
    else:
        run()
