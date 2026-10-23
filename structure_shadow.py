"""ظل «هيكل الصفقة» (تسجيل ومتابعة فقط، ورقي؛ لا يرسل ولا يغيّر أي توصية). بطلب المستخدم 24/10/2026، فرضياته مسجّلة في CLAUDE.md قبل التشغيل.

كلها على خيارات SPY (سترايكات 1$، سيولة عالية) لليوم التالي (1DTE)، بأسعار Alpaca/Tastytrade اللحظية: الشراء بالطلب والبيع بالبيع، فتُحسب فروق الأسعار فعلاً.
لكل توصية SPX/SPY يصدرها البوت (الاتجاه نفسه):
  vert = سبريد شراء (Debit vertical): شراء ATM وبيع سترايك أبعد بـ3$ بالاتجاه؛ مخاطرة = الدين.
  em05 = عقد مجرد عند 0.5× الحركة اليومية المتوقعة خارج السعر (الحركة = VIX÷√252 من السعر).
  ويُقيَّم كل منهما بخروج t60 (بعد 60 دقيقة) وeod (قبل الإغلاق بـ5 دقائق).
ويومياً (نحو 10:30 نيويورك) كوندور حديدي (Iron condor) بلا اتجاه، يُدخَل مرة في اليوم:
  بيع بوت وكول عند 0.8× الحركة المتوقعة وشراء جناحين أبعد بـ3$؛ ائتمان؛ أقصى خسارة = 3$ − الائتمان. لا يُدخَل قبل عطلة (الغد ليس أقرب يوم تداول).
  hold = الاحتفاظ حتى 15:50 من يوم الانتهاء (القيمة بأسعار الإغلاق أو الجوهرية)، وmanaged = إغلاق عند +50% من الائتمان أو −200% منه (أيهما أولاً) وإلا hold.
المقياس: ربح/خسارة لكل سهم ونسبة إلى المخاطرة (الدين للشراء، أقصى خسارة للكوندور). الحكم بالأيام لا بالحالات.
الملفات: structure_shadow_state.json، structure_shadow_log.csv، research/structure_shadow_results.md. تشغيل: python structure_shadow.py [--report]
"""
import csv
import json
import math
import os
import sys
from datetime import date, datetime, timedelta

import data_gaps
import live_quotes as lq
import options_bot as ob   # قراءة فقط: الإعدادات والتوقيت

STATE = "structure_shadow_state.json"
LOG = "structure_shadow_log.csv"
REPORT = os.path.join("research", "structure_shadow_results.md")
EXIT_SHADOW_LOG = "exit_shadow_log.csv"
UNDERLYING = "SPY"
WIDTH = 3.0
EM_CONDOR, EM_NAKED = 0.8, 0.5
CONDOR_START, CONDOR_END = (10, 30), (11, 30)
MIN_CREDIT = 0.10
HOLD_MIN = 60
FINAL_BEFORE_CLOSE_MIN = 5            # eod: قبل الإغلاق بـ5 دقائق
EXPIRY_FINAL = (15, 50)
TP_FRAC, SL_FRAC = 0.5, -2.0          # للكوندور: كسب 50% من الائتمان أو خسارة 200% منه
MAX_LATE_MIN = 15
MIN_VERT, MIN_IC = 20, 40
LOG_COLS = ["id", "kind", "pos_id", "date", "side", "exp", "entry_time", "entry_spot", "legs", "capital", "entry_cash", "peak", "last_pnl", "quotes",
            "t60_pnl", "t60_time", "eod_pnl", "eod_time", "hold_pnl", "hold_time", "managed_pnl", "managed_out", "managed_time"]


# ============================ دوال نقية ============================
def next_trading_day(d):
    n = d + timedelta(days=1)
    while n.weekday() >= 5 or n.isoformat() in ob.HOLIDAYS:
        n += timedelta(days=1)
    return n


def sigma_day(vix):
    """الحركة اليومية الضمنية (نسبة): VIX ÷ 100 ÷ √252."""
    return vix / 100.0 / math.sqrt(252)


def occ(exp, side, strike):
    return lq.occ(UNDERLYING, exp, side, strike)


def vert_legs(side, spot, exp):
    k = round(spot)
    far = k + WIDTH if side == "CALL" else k - WIDTH
    return [{"occ": occ(exp, side, k), "strike": float(k), "side": side, "qty": 1}, {"occ": occ(exp, side, far), "strike": float(far), "side": side, "qty": -1}]


def em_legs(side, spot, vix, exp):
    d = spot * EM_NAKED * sigma_day(vix)
    k = math.ceil(spot + d) if side == "CALL" else math.floor(spot - d)
    return [{"occ": occ(exp, side, k), "strike": float(k), "side": side, "qty": 1}]


def condor_legs(spot, vix, exp):
    d = spot * EM_CONDOR * sigma_day(vix)
    sp, sc = math.floor(spot - d), math.ceil(spot + d)
    return [{"occ": occ(exp, "PUT", sp), "strike": float(sp), "side": "PUT", "qty": -1}, {"occ": occ(exp, "PUT", sp - WIDTH), "strike": float(sp - WIDTH), "side": "PUT", "qty": 1},
            {"occ": occ(exp, "CALL", sc), "strike": float(sc), "side": "CALL", "qty": -1}, {"occ": occ(exp, "CALL", sc + WIDTH), "strike": float(sc + WIDTH), "side": "CALL", "qty": 1}]


def entry_cash(legs, q):
    """النقد الداخل (+) أو الخارج (−) عند الفتح: بيع بسعر البيع (bid)، وشراء بسعر الطلب (ask). q: {occ: {bid, ask}}. None إن نقصت تسعيرة."""
    total = 0.0
    for lg in legs:
        z = q.get(lg["occ"])
        if not z or not (z["bid"] > 0 and z["ask"] > 0):
            return None
        total += z["bid"] if lg["qty"] < 0 else -z["ask"]
    return total


def close_cash(legs, q):
    """النقد عند الإغلاق الآن: بيع الأرجل المشتراة بالبيع (bid)، وشراء المباعة بالطلب (ask). None إن نقصت تسعيرة (ask أو bid قد يكون 0 لعقد بلا قيمة)."""
    total = 0.0
    for lg in legs:
        z = q.get(lg["occ"])
        if not z or z["bid"] is None or z["ask"] is None or z["ask"] <= 0:
            return None
        total += z["bid"] if lg["qty"] > 0 else -z["ask"]
    return total


def intrinsic_cash(legs, spot):
    """قيمة الأرجل جوهرياً عند الانتهاء (الأرجل المشتراة +، المباعة −)."""
    total = 0.0
    for lg in legs:
        v = max(spot - lg["strike"], 0.0) if lg["side"] == "CALL" else max(lg["strike"] - spot, 0.0)
        total += v * lg["qty"]
    return total


def capital(kind, legs, cash):
    """المخاطرة: الدين (شراء)، أو أقصى خسارة = العرض − الائتمان (كوندور)."""
    return -cash if kind != "ic" else WIDTH - cash


def final_min(n, before=FINAL_BEFORE_CLOSE_MIN):
    end = ob.session_end(n)
    return end[0] * 60 + end[1] - before


def step(it, q, spot, n):
    """يقيّم عنصراً مفتوحاً بالتسعيرة q (أو بسعر جوهري يوم الانتهاء) ويحدّث نتائجه. يرجع True حين تكتمل كل قواعده."""
    now_m = n.hour * 60 + n.minute
    t0 = datetime.fromisoformat(it["entry_time"])
    age = (n - t0).total_seconds() / 60
    exp_d = date.fromisoformat(it["exp"])
    expiry_day = n.date() == exp_d
    x = close_cash(it["legs"], q) if q else None
    if x is None and expiry_day and now_m >= EXPIRY_FINAL[0] * 60 + EXPIRY_FINAL[1] and spot:
        x = intrinsic_cash(it["legs"], spot)
    if x is None:
        return False
    pnl = it["entry_cash"] + x
    it["quotes"] += 1
    it["last_pnl"] = round(pnl, 4)
    it["peak"] = round(max(it["peak"], pnl), 4)
    stamp = n.isoformat(timespec="minutes")
    if it["kind"] == "ic":
        credit = it["entry_cash"]
        if it["managed_out"] == "":
            if pnl >= TP_FRAC * credit:
                it.update(managed_out="tp", managed_pnl=round(pnl, 4), managed_time=stamp)
            elif pnl <= SL_FRAC * credit:
                it.update(managed_out="stop", managed_pnl=round(pnl, 4), managed_time=stamp)
        if expiry_day and now_m >= EXPIRY_FINAL[0] * 60 + EXPIRY_FINAL[1] and it["hold_pnl"] == "":
            it.update(hold_pnl=round(pnl, 4), hold_time=stamp)
            if it["managed_out"] == "":
                it.update(managed_out="hold", managed_pnl=round(pnl, 4), managed_time=stamp)
        return it["hold_pnl"] != ""
    if it["t60_pnl"] == "" and (age >= HOLD_MIN or now_m >= final_min(n)):
        it.update(t60_pnl=round(pnl, 4), t60_time=stamp)
    if it["eod_pnl"] == "" and now_m >= final_min(n):
        it.update(eod_pnl=round(pnl, 4), eod_time=stamp)
    return it["t60_pnl"] != "" and it["eod_pnl"] != ""


def expire_if_past(it, n):
    """عنصر مضى يومه بلا اكتمال: يُغلق بآخر قيمة مسجلة (أو لا بيانات)."""
    if n.date() <= date.fromisoformat(it["exp"]) and it["kind"] == "ic":
        return False
    if it["kind"] != "ic" and n.date() <= date.fromisoformat(it["entry_time"][:10]):
        return False
    last = it["last_pnl"]
    fields = ("hold", "managed") if it["kind"] == "ic" else ("t60", "eod")
    for f in fields:
        if it[f + "_pnl"] == "":
            it[f + "_pnl"] = last if last != "" else ""
            it[f + "_time"] = "آخر تقييم" if last != "" else "no_data"
    if it["kind"] == "ic" and it["managed_out"] == "":
        it["managed_out"] = "last" if last != "" else "no_data"
    return True


def new_item(kind, pos_id, n, side, exp, spot, legs, cash):
    cap = capital(kind, legs, cash)
    return {"id": f"{n:%Y%m%dT%H%M}|{kind}|{pos_id}", "kind": kind, "pos_id": pos_id, "date": n.date().isoformat(), "side": side, "exp": exp,
            "entry_time": n.isoformat(timespec="minutes"), "entry_spot": round(spot, 2), "legs": legs, "capital": round(cap, 4), "entry_cash": round(cash, 4),
            "peak": 0.0, "last_pnl": "", "quotes": 0, "t60_pnl": "", "t60_time": "", "eod_pnl": "", "eod_time": "", "hold_pnl": "", "hold_time": "",
            "managed_pnl": "", "managed_out": "", "managed_time": ""}


# ============================ التشغيل ============================
def load_state():
    try:
        s = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        s = {}
    s.setdefault("date", None)
    s.setdefault("items", [])
    s.setdefault("done_recs", [])
    s.setdefault("condor_days", [])
    return s


def save_state(s):
    s["condor_days"] = s["condor_days"][-400:]
    s["done_recs"] = s["done_recs"][-400:]
    with open(STATE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


def append_log(items):
    if not items:
        return
    new = not os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        if new:
            w.writeheader()
        for it in items:
            row = {k: it.get(k, "") for k in LOG_COLS}
            row["legs"] = json.dumps(it["legs"], ensure_ascii=False)
            w.writerow(row)


def _fetch(occs):
    return lq.chain_quotes(list(dict.fromkeys(occs)))


def _quote_map(raw):
    return {o: {"bid": z[0]["bid"], "ask": z[0]["ask"]} for o, z in raw.items()}


def in_session(n):
    return n.weekday() < 5 and (9, 30) <= (n.hour, n.minute) < (16, 0)


def run(n=None, fetch=None, get_spot=None, get_vix=None):
    n = n or ob.now_ny()
    fetch = fetch or _fetch
    get_spot = get_spot or (lambda: lq.spot(UNDERLYING)[0])
    get_vix = get_vix or (lambda: lq.spot("VIX")[0])
    state = load_state()
    if not ob.market_day(n) and not state["items"]:
        save_state(state)
        return state
    spot = vix = None
    if in_session(n):
        try:
            spot, vix = get_spot(), get_vix()
        except Exception as e:  # noqa: BLE001
            data_gaps.note("structure_shadow", "spot_vix", e)
    new = []
    if in_session(n) and spot and vix:
        # (1) توصيات SPX/SPY جديدة اليوم
        today = n.date().isoformat()
        exp = next_trading_day(n.date())
        for p in ob.load_positions()["positions"]:
            if p.get("sym") not in ("SPX", "SPY") or p.get("exp") != today or p["id"] in state["done_recs"]:
                continue
            try:
                late = (n - datetime.fromisoformat(p["time"])).total_seconds() / 60
            except Exception:
                late = 0
            state["done_recs"].append(p["id"])
            if late > MAX_LATE_MIN or (n.hour, n.minute) >= (15, 30):
                continue
            new.append(("vert", p["id"], p["side"], vert_legs(p["side"], spot, exp.isoformat())))
            new.append(("em05", p["id"], p["side"], em_legs(p["side"], spot, vix, exp.isoformat())))
        # (2) كوندور يومي مرة واحدة (10:30–11:30) إن كان الغد يوم التداول التالي مباشرة
        if CONDOR_START <= (n.hour, n.minute) < CONDOR_END and today not in state["condor_days"] and (exp - n.date()).days == 1:
            state["condor_days"].append(today)
            new.append(("ic", f"day-{today}", "BOTH", condor_legs(spot, vix, exp.isoformat())))
    occs = [lg["occ"] for _, _, _, legs in new for lg in legs] + [lg["occ"] for it in state["items"] for lg in it["legs"]]
    q = {}
    if occs and in_session(n):
        try:
            q = _quote_map(fetch(occs))
        except Exception as e:  # noqa: BLE001
            data_gaps.note("structure_shadow", "quotes", e)
    for kind, pos_id, side, legs in new:
        cash = entry_cash(legs, q)
        if cash is None or (kind == "ic" and cash < MIN_CREDIT):
            data_gaps.note("structure_shadow", "entry_quote", None, kind)
            continue
        exp_s = next_trading_day(n.date()).isoformat()
        state["items"].append(new_item(kind, pos_id, n, side, exp_s, spot, legs, cash))
    done, still = [], []
    for it in state["items"]:
        if it["entry_time"] == n.isoformat(timespec="minutes"):
            still.append(it)
            continue
        finished = step(it, q, spot, n) if in_session(n) else False
        finished = finished or expire_if_past(it, n)
        (done if finished else still).append(it)
    state["items"] = still
    append_log(done)
    save_state(state)
    print(f"ظل الهيكل: +{len(new)} طلب إدخال، {len(still)} مفتوح، {len(done)} أُغلق.")
    return state


# ============================ التقرير ============================
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def boot_ci(by_day, n=2000, seed=21):
    days = list(by_day)
    if len(days) < 5:
        return None
    import numpy as np
    S = np.array([sum(by_day[d]) for d in days])
    N = np.array([len(by_day[d]) for d in days])
    idx = np.random.default_rng(seed).integers(0, len(days), (n, len(days)))
    m = S[idx].sum(1) / N[idx].sum(1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def summarize(rows, kind, field, per_capital=True):
    vals, by_day = [], {}
    for r in rows:
        if r["kind"] != kind:
            continue
        p, cap = _num(r.get(field)), _num(r.get("capital"))
        if p is None or not cap:
            continue
        v = p / cap if per_capital else p
        vals.append(v)
        by_day.setdefault(r["date"], []).append(v)
    if not vals:
        return None
    s = sorted(vals)
    return {"n": len(vals), "days": len(by_day), "mean": sum(vals) / len(vals), "median": s[len(s) // 2], "win": sum(1 for v in vals if v > 0) / len(vals), "ci": boot_ci(by_day)}


def vert_vs_naked(rows):
    """فروق t60: سبريد الشراء − عقد ATM المجرد (من exit_shadow_log) لنفس التوصية: [(يوم، فرق نسبة)]."""
    if not os.path.exists(EXIT_SHADOW_LOG):
        return []
    naked = {}
    for r in csv.DictReader(open(EXIT_SHADOW_LOG, encoding="utf-8")):
        if r.get("variant") == "atm1" and _num(r.get("t60_pnl")) is not None:
            naked[r["pos_id"]] = _num(r["t60_pnl"])
    out = []
    for r in rows:
        if r["kind"] == "vert" and r["pos_id"] in naked and _num(r.get("t60_pnl")) is not None and _num(r.get("capital")):
            out.append((r["date"], _num(r["t60_pnl"]) / _num(r["capital"]) - naked[r["pos_id"]]))
    return out


def fmt(s, pct=True):
    if s is None:
        return "-"
    unit = (lambda x: f"{100 * x:+.1f}%") if pct else (lambda x: f"{x:+.3f}$")
    ci = "-" if s["ci"] is None else f"{unit(s['ci'][0])}…{unit(s['ci'][1])}"
    return f"{s['n']} | {s['days']} | {unit(s['mean'])} | {unit(s['median'])} | {100 * s['win']:.0f}% | {ci}"


def verdicts(rows):
    d = vert_vs_naked(rows)
    if len(d) < MIN_VERT:
        v = f"H-VERT: لا حكم بعد ({len(d)} من {MIN_VERT} توصية)"
    else:
        by = {}
        for day, x in d:
            by.setdefault(day, []).append(x)
        ci = boot_ci(by)
        m = sum(x for _, x in d) / len(d)
        v = ("H-VERT: " + ("تحققت: السبريد أفضل من العقد المجرد" if ci and ci[0] > 0 else "عكسية: العقد المجرد أفضل" if ci and ci[1] < 0 else "لم تتحقق: الفاصل يشمل الصفر")
             + f" (فرق {100 * m:+.1f} نقطة مئوية)")
    ic = summarize(rows, "ic", "hold_pnl", per_capital=False)
    if ic is None or ic["n"] < MIN_IC:
        w = f"H-IC: لا حكم بعد ({0 if ic is None else ic['n']} من {MIN_IC} دخولاً)"
    else:
        w = "H-IC: " + ("تحققت: الكوندور رابح" if ic["ci"] and ic["ci"][0] > 0 else "عكسية: خاسر" if ic["ci"] and ic["ci"][1] < 0 else "لم تتحقق: الفاصل يشمل الصفر") + f" (متوسط {ic['mean']:+.3f}$ للسهم)"
    return v, w


def report():
    rows = list(csv.DictReader(open(LOG, encoding="utf-8"))) if os.path.exists(LOG) else []
    st = load_state()
    L = ["# ظل هيكل الصفقة على SPY (نتائج آلية، ورقي)", "",
         "- كلها 1DTE على SPY بأسعار حية (شراء بالطلب وبيع بالبيع). لا تغيّر أي توصية. العينة صغيرة في البداية والحكم بالأيام.",
         f"- **الفرضيات المسجّلة مسبقاً:** H-VERT: سبريد الشراء أفضل من العقد المجرد (t60) بعد {MIN_VERT} توصية؛ H-IC: متوسط ربح الكوندور (hold) > 0 بعد {MIN_IC} دخولاً؛ بفاصل 95% يستثني الصفر.",
         f"- مكتمل: {len(rows)} | مفتوح الآن: {len(st['items'])}", ""]
    if not rows:
        return _write(L + ["لا نتائج مكتملة بعد."])
    L += ["| الهيكل | القاعدة | عناصر | أيام | متوسط (من المخاطرة) | وسيط | رابح | فاصل 95% |", "|---|---|---|---|---|---|---|---|"]
    for kind, name in (("vert", "سبريد شراء"), ("em05", "عقد مجرد 0.5×")):
        for fld, rn in (("t60_pnl", "60 دقيقة"), ("eod_pnl", "قبل الإغلاق")):
            s = summarize(rows, kind, fld)
            if s:
                L.append(f"| {name} | {rn} | {fmt(s)} |")
    for fld, rn in (("hold_pnl", "احتفاظ للانتهاء"), ("managed_pnl", "إدارة +50%/−200%")):
        s = summarize(rows, "ic", fld)
        if s:
            L.append(f"| كوندور حديدي | {rn} | {fmt(s)} |")
    sd = summarize(rows, "ic", "hold_pnl", per_capital=False)
    if sd:
        L += ["", f"- الكوندور بالدولار للسهم (احتفاظ): متوسط {sd['mean']:+.3f}$، وسيط {sd['median']:+.3f}$، فاصل " + ("-" if sd["ci"] is None else f"{sd['ci'][0]:+.3f}…{sd['ci'][1]:+.3f}")]
    v, w = verdicts(rows)
    L += ["", "⚖️ **" + v + "**", "", "⚖️ **" + w + "**"]
    return _write(L)


def _write(L):
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    if "--report" in sys.argv:
        report()
    else:
        run()
