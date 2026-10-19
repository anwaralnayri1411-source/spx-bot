"""تجربة ظلّية: عقد البوت الحالي مقابل عقد أقرب للسعر (تسجيل فقط؛ لا يرسل ولا يغيّر أي توصية أو ملف للبوت).

لكل توصية SPX أو SPY (بديل SPX حين لا يجد البوت عقد SPX مناسباً) في spx_positions.json نتابع بأسعار ياهو الحقيقية (المتأخرة) ثلاثة عقود من نفس الاتجاه والانتهاء:
  bot  = نفس سترايك البوت (100–350$ غالباً بعيد عن السعر)
  atm  = أول سترايك على السعر أو خارجه بخطوة (CALL: أصغر سترايك ≥ السعر، PUT: أكبر سترايك ≤ السعر)
  d30  = السترايك الذي دلتاه أقرب إلى 0.30
الثلاثة تُشترى «على الورق» في اللقطة نفسها (سعر الطلب ask) فتكون المقارنة عادلة، وتُقيَّم بسعر البيع (bid) كل 5 دقائق
بقواعد البوت نفسها: هدف +50%، وقف −40% بعد 15 دقيقة من التوصية، بيع عند 15:30. ونسجّل أيضاً القمة وقيمة الإغلاق.
المقارنة بالدولار تُعرض للعقد الواحد ولكل 150$ مخاطرة عند الوقف (ميزانية البوت)، لأن عقد السعر أغلى بكثير.

الملفات (كلها جديدة): contract_shadow_state.json، contract_shadow_track.csv، contract_shadow_log.csv،
research/contract_shadow_results.md.   تشغيل: python contract_shadow.py [--report]
"""
import csv
import json
import math
import os
import sys
from datetime import datetime

import options_bot as ob   # قراءة فقط: الإعدادات والدوال الحسابية

STATE = "contract_shadow_state.json"
TRACK = "contract_shadow_track.csv"
LOG = "contract_shadow_log.csv"
REPORT = os.path.join("research", "contract_shadow_results.md")
VARIANTS = ("bot", "atm", "d30")
SYMS = {"SPX": "^SPX", "SPY": "SPY"}   # رمز التوصية في spx_positions.json ← رمز ياهو
FINAL = (15, 30)
LOG_COLS = ["date", "pos_id", "variant", "side", "strike", "exp", "entry_time", "entry_spot", "entry", "bot_entry",
            "qty_budget", "rule_out", "rule_time", "rule_pnl", "peak_pnl", "close_pnl", "stale_quotes", "quotes", "sym"]


# ============================ أدوات ============================
def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {"date": None, "items": []}


def save_state(s):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


def quote(row):
    """(سعر الشراء ask، سعر البيع bid، المصدر). المصدر 'last' يعني لا عرض/طلب: السعر قد يكون قديماً."""
    def f(k):
        v = row.get(k)
        try:
            v = float(v)
            return 0.0 if math.isnan(v) else v
        except (TypeError, ValueError):
            return 0.0
    bid, ask, last = f("bid"), f("ask"), f("lastPrice")
    if bid > 0 and ask > 0:
        return ask, bid, "quote"
    if last > 0:
        return last, last, "last"
    return None


def chain(sym, exp):
    import yfinance as yf
    t = yf.Ticker(SYMS[sym])
    ch = t.option_chain(exp)
    spot = float(t.history(period="1d", interval="5m")["Close"].dropna().iloc[-1])
    if sym == "SPX":
        return ob.spxw_rows(ch.calls), ob.spxw_rows(ch.puts), spot
    return ch.calls, ch.puts, spot


def pick_variants(side, df, S, n, bot_strike):
    """يرجع {variant: صف العقد} لنفس الاتجاه. d30 بدلتا البوت نفسها (تذبذب العقد من ياهو وزمن تقويمي)."""
    rows = {float(r["strike"]): r for r in df.to_dict("records")}
    out = {}
    if bot_strike in rows:
        out["bot"] = rows[bot_strike]
    ks = sorted(rows)
    atm = min((k for k in ks if k >= S), default=None) if side == "CALL" else max((k for k in ks if k <= S), default=None)
    if atm is not None:
        out["atm"] = rows[atm]
    T = max((n.replace(hour=16, minute=0, second=0, microsecond=0) - n).total_seconds() / 3600, 0.25) / (24 * 365)
    best = None
    for k in ks:
        if (side == "CALL" and k < S) or (side == "PUT" and k > S) or abs(k - S) / S > 0.025:
            continue
        iv = rows[k].get("impliedVolatility")
        try:
            iv = float(iv)
        except (TypeError, ValueError):
            continue
        if not iv or math.isnan(iv) or iv < 0.03 or quote(rows[k]) is None:
            continue
        dlt = ob.abs_delta(S, k, T, iv, side)
        if best is None or abs(dlt - 0.30) < abs(best[0] - 0.30):
            best = (dlt, k)
    if best:
        out["d30"] = rows[best[1]]
    return out


# ============================ التشغيل ============================
def finalize(state):
    """ينقل عناصر يوم منتهٍ إلى السجل الدائم."""
    items = state.get("items", [])
    if not items:
        return
    new = not os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        if new:
            w.writeheader()
        for it in items:
            if it.get("status") == "open" and it.get("close_pnl") not in ("", None):
                it.update(rule_out="final", rule_time="آخر تقييم", rule_pnl=it["close_pnl"])   # لم يُشغَّل بعد 15:30
            w.writerow({k: it.get(k, "") for k in LOG_COLS})
    state["items"] = []


def run(n=None):
    n = n or ob.now_ny()
    today = n.date().isoformat()
    state = load_state()
    if state.get("date") and state["date"] != today:
        finalize(state)
    state["date"] = today
    if not ob.market_day(n):
        save_state(state)
        return
    pos = [p for p in ob.load_positions()["positions"] if p.get("sym") in SYMS and p.get("exp") == today]
    have = {it["pos_id"] for it in state["items"]}
    groups = {(p["sym"], p["exp"]) for p in pos} | {(it.get("sym", "SPX"), it["exp"]) for it in state["items"]}
    if not groups:
        save_state(state)
        print("لا توصيات SPX/SPY اليوم، لا شيء للتسجيل.")
        return
    hm = (n.hour, n.minute)
    track = []
    for sym, exp in sorted(groups):
        try:
            calls, puts, S = chain(sym, exp)
        except Exception as e:
            print("تعذر جلب السلسلة:", sym, e)
            continue
        books = {"CALL": {float(r["strike"]): r for r in calls.to_dict("records")},
                 "PUT": {float(r["strike"]): r for r in puts.to_dict("records")}}
        # عناصر جديدة لكل توصية لم تُسجَّل بعد (شراء الثلاثة في اللقطة نفسها)
        for p in pos:
            if p["sym"] != sym or p["exp"] != exp or p["id"] in have or hm >= (16, 0):
                continue
            picks = pick_variants(p["side"], calls if p["side"] == "CALL" else puts, S, n, float(p["strike"]))
            for v in VARIANTS:
                r = picks.get(v)
                q = quote(r) if r is not None else None
                if q is None:
                    continue
                ask = q[0]
                state["items"].append({"date": today, "sym": sym, "pos_id": p["id"], "variant": v, "side": p["side"],
                                       "strike": float(r["strike"]), "exp": exp, "sig_time": p["time"],
                                       "entry_time": n.isoformat(timespec="minutes"), "entry_spot": round(S, 2),
                                       "entry": round(ask, 2), "bot_entry": p.get("entry"),
                                       "qty_budget": round(ob.RISK_BUDGET_USD / (ask * 100 * ob.STOP_LOSS), 3),
                                       "status": "open", "rule_out": "", "rule_time": "", "rule_pnl": "",
                                       "peak_pnl": 0.0, "close_pnl": "", "stale_quotes": 0, "quotes": 0})
            have.add(p["id"])
        # تقييم كل العناصر المفتوحة وغير المفتوحة (نتابع حتى الإغلاق لقياس القمة وقيمة الإغلاق)
        for it in state["items"]:
            if it.get("sym", "SPX") != sym or it["exp"] != exp or it["entry_time"] == n.isoformat(timespec="minutes"):
                continue
            r = books[it["side"]].get(it["strike"])
            q = quote(r) if r is not None else None
            if q is None:
                continue
            _, bid, src = q
            pnl = bid / it["entry"] - 1
            it["quotes"] += 1
            it["stale_quotes"] += 1 if src == "last" else 0
            it["peak_pnl"] = round(max(it["peak_pnl"], pnl), 3)
            it["close_pnl"] = round(pnl, 3)       # آخر قيمة؛ تصبح قيمة الإغلاق بعد آخر تشغيل
            if it["status"] == "open":
                try:
                    age = (n - datetime.fromisoformat(it["sig_time"])).total_seconds() / 60
                except Exception:
                    age = 9999
                out = ("stop" if age >= ob.MIN_AGE_MIN and pnl <= -ob.STOP_LOSS else
                       "tp" if pnl >= ob.TAKE_PROFIT else "final" if hm >= FINAL else None)
                if out:
                    it.update(status="closed", rule_out=out, rule_time=n.strftime("%H:%M"), rule_pnl=round(pnl, 3))
            track.append([n.isoformat(timespec="minutes"), it["pos_id"], it["variant"], it["strike"], round(S, 2),
                          round(bid, 2), src, round(pnl, 3), it["status"]])
    if track:
        new = not os.path.exists(TRACK)
        with open(TRACK, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "pos_id", "variant", "strike", "spot", "bid", "src", "pnl", "status"])
            w.writerows(track)
    save_state(state)
    print(f"سُجّل {len(track)} تقييماً لـ {len({it['pos_id'] for it in state['items']})} توصية (SPX/SPY).")


# ============================ التقرير ============================
def report():
    rows = []
    if os.path.exists(LOG):
        rows += list(csv.DictReader(open(LOG, encoding="utf-8")))
    st = load_state()
    rows += [{k: str(it.get(k, "")) for k in LOG_COLS} for it in st.get("items", [])]   # اليوم الجاري (قد يكون ناقصاً)
    by = {v: [r for r in rows if r["variant"] == v and r["rule_pnl"] not in ("", None)] for v in VARIANTS}
    syms = sorted({r.get("sym") or "SPX" for r in rows})
    L = ["# تجربة ظلّية: عقد البوت مقابل عقد أقرب للسعر (نتائج آلية)", "",
         f"- التوصيات المشمولة: {' و'.join(syms) if syms else 'لا شيء بعد'} (SPY = بديل البوت حين لا يجد عقد SPX ضمن 100–350$). "
         "النسب المئوية قابلة للجمع بين الرمزين؛ المبالغ بالدولار تختلف بحجم العقد.",
         "- أسعار ياهو الحقيقية المتأخرة ~15 دقيقة. الشراء بسعر الطلب والتقييم بسعر البيع، في اللقطة نفسها للعقود الثلاثة.",
         "- القاعدة نفسها للجميع: هدف +50% / وقف −40% (بعد 15 دقيقة) / بيع 15:30. «لكل 150$ مخاطرة» = الربح لو اشتريت "
         "كمية تخسر 150$ عند الوقف (كسور مسموحة للمقارنة فقط).",
         "- ⚠️ للمراقبة لا للحكم: لا قرار قبل 20 توصية على الأقل، والعينة الفعلية = عدد الأيام.", "",
         "| العقد | صفقات | أيام | هدف / وقف / 15:30 | متوسط الصفقة | للعقد الواحد | لكل 150$ مخاطرة | لو احتفظت للإغلاق | أسعار قديمة |",
         "|---|---|---|---|---|---|---|---|---|"]
    names = {"bot": "عقد البوت", "atm": "على السعر", "d30": "دلتا 0.30"}
    for v in VARIANTS:
        R = by[v]
        if not R:
            L.append(f"| {names[v]} | 0 | 0 | - | - | - | - | - | - |")
            continue
        pn = [float(r["rule_pnl"]) for r in R]
        en = [float(r["entry"]) for r in R]
        qb = [float(r["qty_budget"]) for r in R]
        cl = [float(r["close_pnl"]) for r in R if r["close_pnl"] not in ("", None)]
        k = len(R)
        outs = [r["rule_out"] for r in R]
        stale = sum(int(r["stale_quotes"] or 0) for r in R)
        tot = sum(int(r["quotes"] or 0) for r in R)
        L.append(f"| {names[v]} | {k} | {len({r['date'] for r in R})} | {100 * outs.count('tp') / k:.0f}% / "
                 f"{100 * outs.count('stop') / k:.0f}% / {100 * outs.count('final') / k:.0f}% | {100 * sum(pn) / k:+.1f}% | "
                 f"≈{sum(p * e * 100 for p, e in zip(pn, en)) / k:+.0f}$ | ≈{sum(p * e * 100 * q for p, e, q in zip(pn, en, qb)) / k:+.0f}$ | "
                 f"{(100 * sum(cl) / len(cl)) if cl else 0:+.1f}% | {stale}/{tot} |")
    pairs = {}
    for r in rows:
        if r["rule_pnl"] not in ("", None):
            pairs.setdefault(r["pos_id"], {})[r["variant"]] = r
    for v in ("atm", "d30"):
        d = [float(x[v]["rule_pnl"]) * float(x[v]["entry"]) * 100 * float(x[v]["qty_budget"]) -
             float(x["bot"]["rule_pnl"]) * float(x["bot"]["entry"]) * 100 * float(x["bot"]["qty_budget"])
             for x in pairs.values() if v in x and "bot" in x]
        if d:
            better = sum(1 for z in d if z > 0)
            L.append(f"\n- **{names[v]} − عقد البوت** (لكل 150$ مخاطرة، {len(d)} توصية): متوسط {sum(d) / len(d):+.0f}$، "
                     f"وأفضل في {better} من {len(d)}.")
    L += ["", "- في SPX عقد «على السعر» أغلى بكثير: غالباً عقد واحد منه يتجاوز ميزانية 150$ عند الوقف. في SPY الفرق أصغر، "
              "وإذا تحرك السعر بعد التوصية قد يصبح عقد البوت نفسه داخل المال وعقد «على السعر» أرخص منه.",
          "- عمود «أسعار قديمة» = تقييمات بلا عرض/طلب (آخر صفقة فقط)؛ كثرتها تعني أن نتيجة ذلك العقد غير موثوقة."]
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    if "--report" in sys.argv:
        report()
    else:
        run()
