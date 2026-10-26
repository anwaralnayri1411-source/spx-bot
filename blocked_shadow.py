"""ظل الحجب (تسجيل ومتابعة فقط، لا يرسل ولا يغيّر أي توصية): هل كان الحجب في صالحنا؟

الفكرة: كلما منعت شروط البوت توصية كان فرقها كافياً (فرق النقاط ≥ الحد الأدنى)، نلتقط العقد الذي كان سيقترحه بسعره الحي،
ثم نتابعه بقواعد الخروج نفسها (هدف +50%، وقف −40% بعد عمر أدنى، وبيع قبل الإغلاق في يوم الانتهاء) حتى يُغلق، دون إرسال شيء
ودون الحكم عليه عند إغلاق اليوم (عقود الشركات تُتابَع أياماً). ثم نقارن لكل سبب حجب: هل متوسط ما كان سيحدث سالب (الحجب أصاب)؟

بنيته (مفصولة حتى لا يتعارض حفظ الملفات بين مهام الخادم):
  - البوتان يكتبان «صندوق وارد» فقط: blocked_inbox_spx.csv (بوت SPX)، blocked_inbox_stocks.csv (بوت الشركات). سطر لكل عقد محجوب.
  - هذا السكربت (مهمة blocked_shadow كل 5 دقائق) يقرأ الصندوقين، يطبّق إزالة التكرار كما يفعل البوت الحقيقي، يتابع المفتوح بأسعار
    live_quotes (Alpaca ثم Tastytrade)، ويكتب blocked_shadow_state.json وblocked_shadow_log.csv (المغلق) وتقرير research/blocked_shadow_results.md.
أسباب الحجب المسجَّلة: بوت SPX: late_entry، report_day، event_day، news_risk، news_weak (أخبار مستوى 2 خفّضت الفرق)، daily_stop، repeat_side.
بوت الشركات: market_warning (تحذير سوق: لا Call)، same_side_cap، max_ideas_cap، open_same_dir. لا نسجّل «لا اتجاه واضح» ولا «لا عقد مناسب» (لا توصية أصلاً).
تحفّظات: الأسعار نظرية الأثر (لا تنفيذ فعلي)، العينة صغيرة في البداية، وتوصيات اليوم نفسه مرتبطة ببعضها فالحكم بالأيام لا بالحالات.
تشغيل: python blocked_shadow.py [--report]
"""
import csv
import json
import os
import random
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import data_gaps

NY = ZoneInfo("America/New_York")
INBOX = {"spx": "blocked_inbox_spx.csv", "stocks": "blocked_inbox_stocks.csv"}
IN_COLS = ["id", "time", "project", "tag", "sym", "root", "side", "strike", "exp", "entry", "spot", "edge", "strength"]
STATE = "blocked_shadow_state.json"
LOG = "blocked_shadow_log.csv"
REPORT = os.path.join("research", "blocked_shadow_results.md")
LOG_COLS = ["id", "project", "tag", "sym", "side", "strike", "exp", "entry_time", "entry", "edge", "strength",
            "out", "out_time", "out_pnl", "peak_pnl", "quotes"]
# قواعد الخروج نفسها في البوتين (options_bot / stocks_bot)؛ اختبار في tests/test_blocked_shadow.py يمنع الانحراف عنها
TAKE_PROFIT, STOP_LOSS = 0.50, 0.40
RULES = {"spx": {"min_age": 15, "final": (15, 30), "cooldown_min": 90},
         "stocks": {"min_age": 30, "final": (15, 45), "cooldown_min": None}}   # الشركات: مرة لكل سهم واتجاه في اليوم
ROOTS = {"SPX": "SPXW", "SPY": "SPY", "NDX": "NDXP"}
MAX_LATE_MIN = 30           # سطر وارد أقدم من هذا عند أول معالجة لا يُتابَع (سعر دخوله لم يعد عادلاً)
MIN_FOR_VERDICT = 30
LABELS = {"late_entry": "بعد وقت آخر دخول", "report_day": "يوم صدور تقرير (قبل 10:00)", "event_day": "حدث كبير اليوم",
          "news_risk": "مخاطر أخبار مرتفعة (مستوى 3)", "news_weak": "أخبار مستوى 2 خفّضت الفرق",
          "daily_stop": "حد الخسارة اليومي", "repeat_side": "تكرار نفس الاتجاه (قبل 90 د)",
          "market_warning": "تحذير سوق (لا Call)", "same_side_cap": "سقف الاتجاه الواحد", "max_ideas_cap": "سقف 3 أفكار",
          "open_same_dir": "مركز مفتوح بنفس الاتجاه"}
_SPX_PREFIX = (("بعد وقت آخر دخول", "late_entry"), ("صدور ", "report_day"), ("حدث كبير اليوم", "event_day"),
               ("مخاطر الأخبار", "news_risk"), ("⛔ حد الخسارة اليومي", "daily_stop"))
WEAK_PREFIX = "لا اتجاه واضح"


# ============================ جانب البوتين: تصنيف وكتابة الصندوق الوارد ============================
def spx_tags(reasons, raw_edge, shock_level, min_edge):
    """أسباب الحجب لفحص SPX. لا شيء ما لم يكن الفرق الخام كافياً (وإلا فلا توصية أصلاً)."""
    if raw_edge < min_edge:
        return []
    tags = []
    for r in reasons:
        for pre, tag in _SPX_PREFIX:
            if r.startswith(pre) and tag not in tags:
                tags.append(tag)
        if r.startswith(WEAK_PREFIX) and shock_level == 2 and "news_weak" not in tags:
            tags.append("news_weak")      # الفرق الخام كاف لكن خصم الأخبار (−1) أنزله تحت الحد
    return tags


def _row(project, n, tag, sym, side, k, exp, spot, edge, strength):
    strike = float(k["strike"])
    return {"id": f"{n:%Y%m%dT%H%M}|{project}|{tag}|{sym}|{side}|{strike:g}|{exp}", "time": n.isoformat(timespec="minutes"),
            "project": project, "tag": tag, "sym": sym, "root": ROOTS.get(sym, sym), "side": side, "strike": strike,
            "exp": exp, "entry": round(float(k["price"]), 2), "spot": "" if spot is None else round(float(spot), 2),
            "edge": round(float(edge), 2), "strength": "" if strength is None else round(float(strength), 1)}


def append_inbox(project, rows):
    """يضيف أسطراً لصندوق المشروع الوارد. لا يرفع استثناءً أبداً (فشله لا يمسّ البوت)."""
    if not rows:
        return 0
    path = INBOX[project]
    try:
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=IN_COLS)
            if new:
                w.writeheader()
            w.writerows(rows)
        return len(rows)
    except Exception as e:  # noqa: BLE001
        data_gaps.note("blocked_shadow", "inbox_write", e, project)
        return 0


def log_spx(n, a, tags=None, contracts=None):
    """بوت SPX: a = ناتج analyze. tags/contracts تُمرَّر صراحةً لحالة التكرار؛ وإلا تُقرأ من a['block_tags'] وa['blocked_contracts']."""
    tags = tags if tags is not None else a.get("block_tags") or []
    contracts = contracts if contracts is not None else a.get("blocked_contracts") or []
    if not tags or not contracts:
        return 0
    raw = abs(a["bull"] - a["bear"])
    rows, seen = [], set()
    for k in contracts:          # كالبوت الحقيقي: أول عقد لكل أداة فقط
        sym = str(k.get("sym", "^SPX")).lstrip("^")
        if sym in seen:
            continue
        seen.add(sym)
        rows.append(_row("spx", n, "+".join(tags), sym, a["side"], k, k.get("exp", a["exp"]), k.get("spot", a["c"]["S"]), raw, a.get("strength")))
    return append_inbox("spx", rows)


def log_stocks(n, blocked):
    """بوت الشركات: blocked = قائمة {tag, ticker, side, edge, strength, S, contracts}."""
    rows = []
    for b in blocked or []:
        if not b.get("contracts"):
            continue
        k = b["contracts"][0]        # كالبوت الحقيقي: العقد الأول لكل فكرة
        rows.append(_row("stocks", n, b["tag"], b["ticker"], b["side"], k, k["exp"], b.get("S"), abs(b.get("edge", 0)), b.get("strength")))
    return append_inbox("stocks", rows)


# ============================ المتتبع ============================
def occ(root, exp, side, strike):
    d = exp.replace("-", "")[2:]
    return f"{root}{d}{'C' if side == 'CALL' else 'P'}{int(round(float(strike) * 1000)):08d}"


def in_session(n):
    return n.weekday() < 5 and (9, 30) <= (n.hour, n.minute) < (16, 0)


def load_state():
    try:
        s = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        s = {}
    s.setdefault("open", [])
    s.setdefault("seen", [])
    s.setdefault("last", {})
    return s


def save_state(s):
    s["seen"] = s["seen"][-3000:]
    with open(STATE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


def read_inbox(project):
    try:
        return list(csv.DictReader(open(INBOX[project], encoding="utf-8")))
    except FileNotFoundError:
        return []
    except Exception as e:  # noqa: BLE001
        data_gaps.note("blocked_shadow", "inbox_read", e, project)
        return []


def _dedupe_key(row):
    if row["project"] == "stocks":
        return f"stocks|{row['sym']}|{row['side']}|{row['time'][:10]}"
    return f"spx|{row['side']}|{row['sym']}"


def ingest(state, n):
    """ينقل أسطر الصندوقين الجديدة إلى المفتوح بعد إزالة التكرار (كما كان البوت سيفعل لو أرسل). يرجع عدد المقبول."""
    added = 0
    for project in INBOX:
        for r in read_inbox(project):
            if r["id"] in state["seen"]:
                continue
            state["seen"].append(r["id"])
            try:
                t = datetime.fromisoformat(r["time"])
                entry = float(r["entry"])
                if (n - t).total_seconds() > MAX_LATE_MIN * 60 or entry <= 0:
                    continue
                key = _dedupe_key(r)
                cool = RULES[project]["cooldown_min"]
                last = state["last"].get(key)
                if last and (cool is None or (t - datetime.fromisoformat(last)).total_seconds() < cool * 60):
                    continue
                state["last"][key] = t.isoformat(timespec="minutes")
                state["open"].append({
                    "id": r["id"], "project": project, "tag": r["tag"], "sym": r["sym"], "side": r["side"],
                    "strike": float(r["strike"]), "exp": r["exp"], "entry": entry, "entry_time": r["time"],
                    "edge": r["edge"], "strength": r["strength"], "occ": occ(r["root"], r["exp"], r["side"], r["strike"]),
                    "peak_pnl": 0.0, "close_pnl": "", "quotes": 0, "out": "", "out_time": "", "out_pnl": ""})
                added += 1
            except Exception as e:  # noqa: BLE001
                data_gaps.note("blocked_shadow", "ingest_row", e, project)
    return added


def step(it, bid, n):
    """تقييم واحد بسعر البيع bid. يعدّل it في مكانه ويرجع سبب الإغلاق أو ''."""
    pnl = bid / it["entry"] - 1
    it["quotes"] += 1
    it["peak_pnl"] = round(max(it["peak_pnl"], pnl), 3)
    it["close_pnl"] = round(pnl, 3)
    age = (n - datetime.fromisoformat(it["entry_time"])).total_seconds() / 60
    rule = RULES[it["project"]]
    exp_d = date.fromisoformat(it["exp"])
    if pnl >= TAKE_PROFIT:
        out = "tp"
    elif age >= rule["min_age"] and pnl <= -STOP_LOSS:
        out = "stop"
    elif n.date() == exp_d and (n.hour, n.minute) >= rule["final"]:
        out = "final"
    else:
        return ""
    it.update(out=out, out_time=n.isoformat(timespec="minutes"), out_pnl=round(pnl, 3))
    return out


def expire_if_past(it, n):
    """عقد تجاوز تاريخ انتهائه: يُغلق بآخر قيمة مسجّلة (أو no_data إن لم تصلنا أي تسعيرة)."""
    if n.date() <= date.fromisoformat(it["exp"]):
        return False
    if it["close_pnl"] == "":
        it.update(out="no_data", out_time=n.isoformat(timespec="minutes"))
    else:
        it.update(out="expired", out_time=n.isoformat(timespec="minutes"), out_pnl=it["close_pnl"])
    return True


def append_log(items):
    if not items:
        return
    new = not os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        if new:
            w.writeheader()
        for it in items:
            w.writerow({k: it.get(k, "") for k in LOG_COLS})


def _live_fetch(occs):
    import live_quotes as lq
    return lq.chain_quotes(occs)


def run(n=None, fetch=None):
    n = n or datetime.now(NY)
    state = load_state()
    added = ingest(state, n)
    items = state["open"]
    quotes = {}
    if items and in_session(n):
        try:
            quotes = (fetch or _live_fetch)([it["occ"] for it in items])
        except Exception as e:  # noqa: BLE001
            data_gaps.note("blocked_shadow", "quotes", e)
    done, still = [], []
    for it in items:
        z = quotes.get(it["occ"])
        closed = False
        if z is not None:
            closed = bool(step(it, float(z[0]["bid"]), n))
        elif in_session(n):
            data_gaps.note("blocked_shadow", "no_quote", None, it["sym"])
        if not closed:
            closed = expire_if_past(it, n)
        (done if closed else still).append(it)
    state["open"] = still
    append_log(done)
    save_state(state)
    print(f"ظل الحجب: +{added} جديد، {len(still)} مفتوح، {len(done)} أُغلق.")
    return state


# ============================ التقرير ============================
def boot_ci(by_day, n_boot=2000, seed=1):
    """فاصل 95% لمتوسط القيم بإعادة معاينة الأيام (العينة الفعلية = الأيام). by_day: {يوم: [قيم]}. None إن قلّت الأيام عن 3."""
    days = list(by_day)
    if len(days) < 3:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        vals = [v for d in (rng.choice(days) for _ in days) for v in by_day[d]]
        means.append(sum(vals) / len(vals))
    means.sort()
    return means[int(0.025 * n_boot)], means[int(0.975 * n_boot) - 1]


def verdict(vals, by_day):
    if len(vals) < MIN_FOR_VERDICT:
        return f"لا حكم بعد ({len(vals)} من {MIN_FOR_VERDICT})"
    ci = boot_ci(by_day)
    if ci is None:
        return "لا حكم بعد (أيام قليلة)"
    lo, hi = ci
    if hi < 0:
        return "الحجب أصاب (المحجوب كان سيخسر)"
    if lo > 0:
        return "الحجب فوّت ربحاً (المحجوب كان سيربح)"
    return "غير حاسم (الفاصل يشمل الصفر)"


def _baseline(path):
    try:
        pos = json.load(open(path, encoding="utf-8"))["positions"]
    except Exception:
        return None
    v = [float(p["closed"]["pnl"]) for p in pos if p.get("closed") and p["closed"].get("pnl") is not None]
    return (sum(v) / len(v), len(v)) if v else None


def report():
    rows = list(csv.DictReader(open(LOG, encoding="utf-8"))) if os.path.exists(LOG) else []
    rows = [r for r in rows if r["out"] not in ("no_data", "") and r["out_pnl"] not in ("", None)]
    st = load_state()
    L = ["# ظل الحجب: هل كان الحجب في صالحنا؟ (نتائج آلية)", "",
         "- لكل عقد محجوب: سعر الطلب الحي لحظة الحجب، ثم قواعد البوت (+50% / −40% بعد عمر أدنى / بيع قبل الإغلاق في يوم الانتهاء) حتى الإغلاق.",
         "- النتيجة = ما كان سيحدث لو لم يُحجب (متوسط سالب ⇒ الحجب أصاب). أسعار حية لكن بلا تنفيذ فعلي، والتوصية المحجوبة لم تُرسل.",
         f"- ⚠️ لا حكم قبل {MIN_FOR_VERDICT} حالة على الأقل لكل سبب، وبفاصل ثقة يعيد معاينة الأيام لا الحالات (توصيات اليوم نفسه مرتبطة).",
         f"- مغلق: {len(rows)} | مفتوح الآن: {len(st['open'])} (يُتابَع حتى هدف/وقف/انتهاء).", ""]
    groups = {}
    for r in rows:
        groups.setdefault((r["project"], r["tag"]), []).append(r)
    if not groups:
        L.append("لا حالات مغلقة بعد.")
    else:
        L += ["| البوت | سبب الحجب | حالات | أيام | متوسط | وسيط | هدف / وقف / آخر | الحكم |", "|---|---|---|---|---|---|---|---|"]
        for (proj, tag), rs in sorted(groups.items()):
            vals = [float(r["out_pnl"]) for r in rs]
            by_day = {}
            for r in rs:
                by_day.setdefault(r["entry_time"][:10], []).append(float(r["out_pnl"]))
            s = sorted(vals)
            outs = [r["out"] for r in rs]
            name = " + ".join(LABELS.get(t, t) for t in tag.split("+"))
            L.append(f"| {'SPX' if proj == 'spx' else 'الشركات'} | {name} | {len(vals)} | {len(by_day)} | {100 * sum(vals) / len(vals):+.1f}% | "
                     f"{100 * s[len(s) // 2]:+.1f}% | {100 * outs.count('tp') / len(vals):.0f}% / {100 * outs.count('stop') / len(vals):.0f}% / "
                     f"{100 * (len(vals) - outs.count('tp') - outs.count('stop')) / len(vals):.0f}% | {verdict(vals, by_day)} |")
    L.append("")
    for proj, path, name in (("spx", "spx_positions.json", "توصيات SPX المرسلة"), ("stocks", "stocks_positions.json", "توصيات الشركات المرسلة")):
        b = _baseline(path)
        if b:
            L.append(f"- للمقارنة، متوسط {name} المغلقة: {100 * b[0]:+.1f}% ({b[1]} توصية).")
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    if "--report" in sys.argv:
        report()
    else:
        run()
