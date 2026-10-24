"""ظل «المرشحين» (تسجيل ومتابعة فقط، ورقي؛ لا يرسل ولا يغيّر أي توصية). بطلب المستخدم 24/10/2026، فرضياته مسجّلة في CLAUDE.md قبل التشغيل (H-MOM وH-F30 وH-AGREE).

لماذا: دراسات التاريخ وجدت أن زخم آخر ساعة وتتبّع أول 30 دقيقة أقرب الإشارات إلى مهارة (موجبة لكن فاصلها يلامس الصفر)، والتاريخ رُئي فلا يحكم. هذا الظل يجمع عينة جديدة نظيفة بأربع مشاهدات يومياً.
كيف: عند 10:30 و11:30 و12:30 و13:30 (نيويورك) نقيس مزايا لحظة الدخول من شموع SPY 5 دقائق (IEX لحظية) ونسجّل **الاتجاهين معاً**: كول وبوت ATM لليوم التالي (1DTE)، دخول بالطلب (ask)
وخروج بعد 60 دقيقة بالبيع (bid) بأسعار Alpaca/Tastytrade اللحظية. وللإشارات: mom = اتجاه حركة الساعة السابقة، f30 = اتجاه أول 30 دقيقة (9:30→10:00)، agree = اتفاقهما، gap = اتجاه الفجوة ≥0.4%.
المهارة = عائد الجهة التي اختارتها الإشارة − متوسط الكول والبوت (رامي العملة) على الدخول نفسه؛ فنقيس الاتجاه نفسه بلا ضجة وبأسعار حقيقية وبلا أي افتراض لنموذج تسعير.
الحكم: بعد 60 يوم تداول، فاصل 95% بإعادة معاينة الأيام يستثني الصفر. تحفظ: مزايا التاريخ من SIP والظل من IEX (قد تختلف قممها قليلاً؛ نستعمل إغلاق وافتتاح فقط).
الملفات: candidates_shadow_state.json، candidates_shadow_log.csv، research/candidates_shadow_results.md. تشغيل: python candidates_shadow.py [--report]
"""
import csv
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import numpy as np

import data_gaps
import live_quotes as lq
import options_bot as ob   # قراءة فقط: التوقيت والعطلات

STATE = "candidates_shadow_state.json"
LOG = "candidates_shadow_log.csv"
REPORT = os.path.join("research", "candidates_shadow_results.md")
SLOTS = ((10, 30), (11, 30), (12, 30), (13, 30))
SLOT_WINDOW_MIN = 9          # نقبل تشغيلاً بعد الموعد بتسع دقائق على الأكثر
HOLD_MIN = 60
EXIT_WINDOW_MIN = 20         # بعد 60 دقيقة نقبل الخروج خلال هذه المهلة
GAP_MIN = 0.004
MIN_DAYS = 60
SIGNALS = ("mom", "f30", "agree", "gap")
LOG_COLS = ["id", "day", "slot", "spot", "gap", "r_first", "r_prev60", "mom", "f30", "agree", "gap_sig", "exp", "call_occ", "put_occ",
            "call_entry", "put_entry", "entry_time", "call_exit", "put_exit", "exit_time", "call_ret", "put_ret", "status"]


# ============================ دوال نقية ============================
def sgn(x, eps=0.0):
    return 1 if x > eps else (-1 if x < -eps else 0)


def next_trading_day(d):
    n = d + timedelta(days=1)
    while n.weekday() >= 5 or n.isoformat() in ob.HOLIDAYS:
        n += timedelta(days=1)
    return n


def to_ny(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ob.NY)


def features(rows, day, slot, prev_close):
    """مزايا لحظة الدخول من شموع (t,o,h,l,c,v) لليوم `day` حتى الموعد `slot` (h,m). None إن نقصت الشموع. لا تستعمل أي شمعة بدايتها ≥ الموعد."""
    cut = slot[0] * 60 + slot[1]
    bars = []
    for r in rows:
        t = to_ny(r[0])
        m = t.hour * 60 + t.minute
        if t.date() == day and 570 <= m < cut:
            bars.append((m, r[1], r[4]))
    bars.sort()
    if not bars or bars[0][0] != 570 or bars[-1][0] != cut - 5 or len(bars) != (cut - 570) // 5:
        return None
    o0 = bars[0][1]
    c = [b[2] for b in bars]
    i = len(c) - 1
    base = c[i - 12] if i >= 12 else o0
    return {"spot": c[-1], "gap": o0 / prev_close - 1 if prev_close else 0.0, "r_first": c[5] / o0 - 1, "r_prev60": c[i] / base - 1}


def signals(f):
    """اتجاه كل إشارة: +1 كول، −1 بوت، 0 لا دخول."""
    mom, f30 = sgn(f["r_prev60"]), sgn(f["r_first"])
    return {"mom": mom, "f30": f30, "agree": mom if mom == f30 else 0, "gap": sgn(f["gap"]) if abs(f["gap"]) >= GAP_MIN else 0}


def atm_legs(spot, exp):
    k = float(round(spot))
    return lq.occ("SPY", exp, "CALL", k), lq.occ("SPY", exp, "PUT", k), k


def ask_bid(q, occ):
    z = q.get(occ)
    return (z["ask"], z["bid"]) if z and z["ask"] > 0 and z["bid"] > 0 else (None, None)


# ============================ التشغيل ============================
def load_state():
    try:
        s = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        s = {}
    s.setdefault("items", [])
    s.setdefault("done", [])
    return s


def save_state(s):
    s["done"] = s["done"][-200:]
    with open(STATE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


def append_log(rows):
    if not rows:
        return
    new = not os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in LOG_COLS})


def default_bars(start_utc, end_utc):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "research"))
    import spy_history as sh
    return sh.fetch_range(start_utc, end_utc, "iex")


def _qmap(raw):
    return {o: {"bid": z[0]["bid"], "ask": z[0]["ask"]} for o, z in raw.items()}


def prev_close_from(rows, day):
    """إغلاق آخر شمعة عادية من آخر يوم قبل `day` (بتوقيت نيويورك)."""
    last = None
    for r in rows:
        t = to_ny(r[0])
        m = t.hour * 60 + t.minute
        if t.date() < day and 570 <= m < 960:
            last = (t, r[4])
    return last[1] if last else None


def run(n=None, get_bars=None, fetch=None):
    n = n or ob.now_ny()
    get_bars = get_bars or default_bars
    fetch = fetch or (lambda occs: lq.chain_quotes(list(dict.fromkeys(occs))))
    state = load_state()
    day = n.date()
    now_m = n.hour * 60 + n.minute
    if not ob.market_day(n) and not state["items"]:
        save_state(state)
        return state
    new = []
    for slot in SLOTS:
        sid = f"{day}|{slot[0]}{slot[1]}"
        sm = slot[0] * 60 + slot[1]
        if sid in state["done"] or not ob.market_day(n) or not (sm <= now_m < sm + SLOT_WINDOW_MIN):
            continue
        last_try = now_m >= sm + SLOT_WINDOW_MIN - 5          # آخر تشغيلين داخل المهلة: بعدها نتخلى عن الموعد
        end = datetime.now(timezone.utc)
        try:
            rows = get_bars((end - timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ"))
        except Exception as e:  # noqa: BLE001
            data_gaps.note("candidates_shadow", "bars", e)
            if last_try:
                state["done"].append(sid)
            continue
        f = features(rows, day, slot, prev_close_from(rows, day))
        if f is None:
            data_gaps.note("candidates_shadow", "features", None, f"{slot[0]}{slot[1]}")
            if last_try:
                state["done"].append(sid)
            continue
        state["done"].append(sid)
        exp = next_trading_day(day).isoformat()
        call, put, k = atm_legs(f["spot"], exp)
        sig = signals(f)
        new.append({"id": sid, "day": day.isoformat(), "slot": f"{slot[0]}:{slot[1]:02d}", "spot": round(f["spot"], 2), "gap": round(f["gap"], 5),
                    "r_first": round(f["r_first"], 5), "r_prev60": round(f["r_prev60"], 5), "mom": sig["mom"], "f30": sig["f30"], "agree": sig["agree"],
                    "gap_sig": sig["gap"], "exp": exp, "call_occ": call, "put_occ": put, "entry_time": n.isoformat(timespec="minutes"),
                    "status": "open"})
    occs = [o for it in new + state["items"] for o in (it["call_occ"], it["put_occ"])]
    q = {}
    if occs and ob.market_day(n):
        try:
            q = _qmap(fetch(occs))
        except Exception as e:  # noqa: BLE001
            data_gaps.note("candidates_shadow", "quotes", e)
    for it in new:
        ca, _ = ask_bid(q, it["call_occ"])
        pa, _ = ask_bid(q, it["put_occ"])
        if ca is None or pa is None:
            data_gaps.note("candidates_shadow", "entry_quote", None, it["slot"])
            continue
        it["call_entry"], it["put_entry"] = ca, pa
        state["items"].append(it)
    done, still = [], []
    for it in state["items"]:
        if it in new:
            still.append(it)
            continue
        age = (n - datetime.fromisoformat(it["entry_time"])).total_seconds() / 60
        if it["status"] == "open" and age >= HOLD_MIN:
            _, cb = ask_bid(q, it["call_occ"])
            _, pb = ask_bid(q, it["put_occ"])
            if cb is not None and pb is not None:
                it.update(call_exit=cb, put_exit=pb, exit_time=n.isoformat(timespec="minutes"), call_ret=round(cb / it["call_entry"] - 1, 4),
                          put_ret=round(pb / it["put_entry"] - 1, 4), status="closed")
            elif age >= HOLD_MIN + EXIT_WINDOW_MIN or n.date() > date.fromisoformat(it["day"]):
                it["status"] = "no_exit"
        (done if it["status"] != "open" else still).append(it)
    state["items"] = still
    append_log(done)
    save_state(state)
    print(f"ظل المرشحين: +{len(new)} دخول، {len(still)} مفتوح، {len(done)} أُغلق.")
    return state


# ============================ التقرير ============================
def skill_rows(rows, name):
    """[(يوم، مهارة)] لصفوف مغلقة تكون فيها إشارة `name` غير صفرية: عائد الجهة المختارة − متوسط الجهتين."""
    out = []
    for r in rows:
        if r.get("status") != "closed":
            continue
        try:
            s = int(float(r[name if name != "gap" else "gap_sig"]))
            c, p = float(r["call_ret"]), float(r["put_ret"])
        except (KeyError, TypeError, ValueError):
            continue
        if s:
            out.append((r["day"], (c if s > 0 else p) - (c + p) / 2))
    return out


def boot(by_day, n=2000, seed=29):
    days = list(by_day)
    if len(days) < 10:
        return None
    S = np.array([sum(by_day[d]) for d in days])
    N = np.array([len(by_day[d]) for d in days])
    idx = np.random.default_rng(seed).integers(0, len(days), (n, len(days)))
    m = S[idx].sum(1) / N[idx].sum(1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def stat(pairs):
    if not pairs:
        return None
    by = {}
    for d, v in pairs:
        by.setdefault(d, []).append(v)
    vals = [v for _, v in pairs]
    return {"n": len(vals), "days": len(by), "mean": float(np.mean(vals)), "win": float(np.mean([v > 0 for v in vals])), "ci": boot(by)}


def verdict(name, label, s):
    if s is None or s["days"] < MIN_DAYS:
        return f"{label}: لا حكم بعد ({0 if s is None else s['days']} من {MIN_DAYS} يوماً)"
    lo, hi = s["ci"]
    desc = f"مهارة {100 * s['mean']:+.2f}%، فاصل {100 * lo:+.2f}…{100 * hi:+.2f}"
    return f"{label}: " + ("تحققت (" if lo > 0 else "عكسية (" if hi < 0 else "لم تتحقق (") + desc + (")" if lo > 0 or hi < 0 else " يشمل الصفر)")


def report():
    rows = list(csv.DictReader(open(LOG, encoding="utf-8"))) if os.path.exists(LOG) else []
    st = load_state()
    closed = [r for r in rows if r["status"] == "closed"]
    L = ["# ظل المرشحين: مهارة الزخم وأول 30 دقيقة (نتائج آلية، ورقي)", "",
         "- كول وبوت ATM ليوم الغد في 4 أوقات يومياً، دخول بالطلب وخروج بعد 60 دقيقة بالبيع (أسعار لحظية). المهارة = عائد الجهة المختارة − متوسط الجهتين على الدخول نفسه.",
         f"- **الفرضيات المسجّلة مسبقاً** (بعد {MIN_DAYS} يوماً، فاصل 95% بإعادة معاينة الأيام يستثني الصفر): H-MOM وH-F30 وH-AGREE. مغلق: {len(closed)} | مفتوح: {len(st['items'])} | بلا خروج: {sum(1 for r in rows if r['status'] == 'no_exit')}", ""]
    if not closed:
        return _write(L + ["لا نتائج مكتملة بعد."])
    base = [(r["day"], (float(r["call_ret"]) + float(r["put_ret"])) / 2) for r in closed]
    sb = stat(base)
    L += [f"- متوسط الشراء العشوائي (كلفة السوق الفعلية): {100 * sb['mean']:+.2f}% على {sb['n']} دخولاً/{sb['days']} يوماً", "",
          "| الإشارة | دخولات | أيام | متوسط المهارة | ربح الإشارة على العشوائي | فاصل 95% (%) |", "|---|---|---|---|---|---|"]
    labels = (("mom", "H-MOM: زخم آخر ساعة"), ("f30", "H-F30: تتبّع أول 30 دقيقة"), ("agree", "H-AGREE: اتفاق الاثنين"), ("gap", "(استكشافي) تتبّع الفجوة ≥0.4%"))
    stats = {}
    for name, label in labels:
        s = stats[name] = stat(skill_rows(rows, name))
        if s:
            ci = "-" if s["ci"] is None else f"{100 * s['ci'][0]:+.2f}…{100 * s['ci'][1]:+.2f}"
            L.append(f"| {label} | {s['n']} | {s['days']} | {100 * s['mean']:+.2f}% | {100 * s['win']:.0f}% | {ci} |")
    L.append("")
    for name, label in labels[:3]:
        L += ["⚖️ **" + verdict(name, label.split(":")[0], stats[name]) + "**", ""]
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
