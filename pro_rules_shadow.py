"""ظل «قواعد المحترفين» لبوت الشركات (تسجيل ومتابعة فقط، لا يرسل ولا يغيّر أي توصية).

السؤال: لو اختير لكل فكرة عقد بقاعدة شائعة عند المحترفين بدل عقد البوت القصير البعيد، فهل تتحسن النتائج؟ بسعر لحظي وخروج فعلي لا تقدير.
لكل فكرة يقترحها بوت الشركات (أو حجبتها سقوفه) نلتقط ثلاثة عقود معاً في اللحظة نفسها بسعر الطلب الحي:
  bot = عقد البوت نفسه (ضابط، بالمحرك نفسه)
  atm = أقرب عقد لدلتا 0.50 بانتهاء شهري 28–50 يوماً
  itm = أقرب عقد لدلتا 0.70 («بديل السهم») بالانتهاء نفسه
ثم نتابع كلاً منهما حتى الخروج بقاعدتين معاً (سعر البيع الحي):
  r1 = قاعدة البوت: هدف +50%، وقف −40% بعد 30 دقيقة، بيع قبل إغلاق يوم الانتهاء
  r2 = قاعدة محترفين: هدف +50%، ووقف على السهم 1.5% ضد الاتجاه (بعد 30 دقيقة)، ووقف زمني عند بقاء 7 أيام أو أقل (آخر الجلسة)
وسقف احتفاظ موحّد 10 أيام تقويمية لكل العقود وكل القواعد (بيع بالسعر الحي)، حتى لا نقارن عقداً قصيراً أُغلق بعقد طويل لم ينتهِ.
بنية بلا تعارض ملفات: البوت يكتب الصندوق الوارد pro_inbox_stocks.csv فقط؛ هذا السكربت (مهمة pro_rules_shadow كل 5 دقائق) يقرؤه ويتابع ويكتب
pro_rules_shadow_state.json وpro_rules_shadow_log.csv وresearch/pro_rules_shadow_results.md.
الفرضية المسجّلة مسبقاً في CLAUDE.md. ⚠️ العينة صغيرة في البداية (فكرة إلى ثلاث يومياً)، والحكم بالأيام لا بالحالات؛ أسعار حية لكن بلا تنفيذ فعلي.
تشغيل: python pro_rules_shadow.py [--report]
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
INBOX = "pro_inbox_stocks.csv"
STATE = "pro_rules_shadow_state.json"
LOG = "pro_rules_shadow_log.csv"
REPORT = os.path.join("research", "pro_rules_shadow_results.md")
IN_COLS = ["id", "idea", "time", "tag", "variant", "ticker", "side", "strike", "exp", "entry", "spot", "spr", "delta", "iv", "dte", "edge", "strength", "src"]
LOG_COLS = ["id", "idea", "tag", "variant", "ticker", "side", "strike", "exp", "entry_time", "entry", "spot", "delta", "r1_out", "r1_pnl", "r1_time",
            "r2_out", "r2_pnl", "r2_time", "peak_pnl", "close_pnl", "quotes"]
VARIANTS = {"atm": 0.50, "itm": 0.70}
EXP_MIN_DTE, EXP_MAX_DTE = 28, 50
MAX_SPREAD, MIN_OI, MAX_DELTA_GAP = 0.25, 100, 0.15
TP, STOP, MIN_AGE_MIN = 0.50, 0.40, 30             # كما في stocks_bot (يحرسه اختبار)
STOCK_STOP, TIME_STOP_DAYS = 0.015, 7
FINAL_EXPIRY = (15, 45)                             # بيع يوم الانتهاء (قاعدة الشركات)
DAY_END = (15, 50)                                  # آخر تشغيل للوقف الزمني وسقف الاحتفاظ داخل اليوم
MAX_HOLD_DAYS = 10
MAX_LATE_MIN = 30
MIN_IDEAS = 30


# ============================ جانب البوت: التقاط العقود ============================
def monthly_expiry(options, today):
    """أول انتهاء من قائمة yfinance ضمن 28–50 يوماً، مفضّلاً الجمعة الثالثة؛ وإلا None."""
    ok = []
    for e in options or []:
        try:
            dte = (date.fromisoformat(e) - today).days
        except ValueError:
            continue
        if EXP_MIN_DTE <= dte <= EXP_MAX_DTE:
            ok.append((e, dte))
    if not ok:
        return None
    third = [x for x in ok if date.fromisoformat(x[0]).weekday() == 4 and 15 <= date.fromisoformat(x[0]).day <= 21]
    return (third or ok)[0]


def pick_variant(df, side, S, T, target, abs_delta, rv=0.3):
    """أقرب عقد لدلتا target بين صفوف df (فيها strike/bid/ask/openInterest/impliedVolatility). عرض/طلب حقيقيان وفرق ≤ MAX_SPREAD وسيولة معقولة."""
    best = None
    for r in df.to_dict("records"):
        try:
            bid, ask, oi = float(r.get("bid") or 0), float(r.get("ask") or 0), float(r.get("openInterest") or 0)
            K = float(r["strike"])
        except (TypeError, ValueError):
            continue
        if not (bid > 0 and ask > 0) or ask < bid:
            continue
        spr = (ask - bid) / ((ask + bid) / 2)
        if spr > MAX_SPREAD or oi < MIN_OI:
            continue
        iv = r.get("impliedVolatility")
        iv = float(iv) if iv is not None and iv == iv and float(iv) >= 0.05 else max(rv, 0.2)
        d = abs_delta(S, K, T, iv, side)
        gap = abs(d - target)
        if gap > MAX_DELTA_GAP:
            continue
        if best is None or gap < best[0]:
            best = (gap, {"strike": K, "price": ask, "spr": spr, "delta": d, "iv": iv, "src": r.get("live_src", "yahoo")})
    return best[1] if best else None


def fetch_monthly(ticker, today, S):
    """(انتهاء، dte، DataFrame الكول، DataFrame البوت) مع عرض/طلب لحظي حيث يتوفر، أو None. شبكة: يُستبدل في الاختبار."""
    import yfinance as yf
    import live_quotes as lq
    t = yf.Ticker(ticker)
    pick = monthly_expiry(t.options, today)
    if pick is None:
        return None
    exp, dte = pick
    ch = t.option_chain(exp)
    return exp, dte, lq.overlay(ch.calls, S, 0.25), lq.overlay(ch.puts, S, 0.25)


def _row(n, idea, tag, variant, tk, side, k, exp, dte, S, edge, strength):
    return {"id": f"{n:%Y%m%dT%H%M}|{tk}|{side}|{variant}|{float(k['strike']):g}|{exp}", "idea": idea, "time": n.isoformat(timespec="minutes"),
            "tag": tag, "variant": variant, "ticker": tk, "side": side, "strike": float(k["strike"]), "exp": exp, "entry": round(float(k["price"]), 2),
            "spot": round(float(S), 2), "spr": "" if k.get("spr") is None else round(float(k["spr"]), 3), "delta": round(float(k.get("delta") or 0), 3),
            "iv": round(float(k.get("iv") or 0), 3), "dte": dte, "edge": round(abs(float(edge or 0)), 2),
            "strength": "" if strength is None else round(float(strength), 1), "src": k.get("src", "")}


def append_inbox(rows):
    if not rows:
        return 0
    try:
        new = not os.path.exists(INBOX)
        with open(INBOX, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=IN_COLS)
            if new:
                w.writeheader()
            w.writerows(rows)
        return len(rows)
    except Exception as e:  # noqa: BLE001
        data_gaps.note("pro_rules_shadow", "inbox_write", e)
        return 0


def log_ideas(n, ideas, abs_delta, fetch=None):
    """يلتقط للأفكار عقود bot وatm وitm معاً. ideas: [{tag, ticker, side, S, edge, strength, contracts}] (contracts[0] = عقد البوت).
    لا يرفع استثناءً أبداً؛ فكرة يتعذر التقاط عقد بديل لها بالكامل تُسجَّل بلا أي صف (مقارنة عادلة فقط)."""
    fetch = fetch or fetch_monthly
    out = []
    seen = set()
    for i in ideas or []:
        try:
            tk, side = i["ticker"], i["side"]
            key = f"{tk}|{side}|{n.date().isoformat()}"
            if key in seen or not i.get("contracts"):
                continue
            seen.add(key)
            S = float(i["S"])
            got = fetch(tk, n.date(), S)
            if got is None:
                continue
            exp, dte, calls, puts = got
            df = calls if side == "CALL" else puts
            T = max(dte, 1) / 365
            rv = float(i["contracts"][0].get("iv") or 0.3)
            alts = {}
            for v, target in VARIANTS.items():
                k = pick_variant(df, side, S, T, target, abs_delta, rv)
                if k is None:
                    break
                alts[v] = k
            if len(alts) < len(VARIANTS):          # نسجّل الفكرة كاملة أو لا شيء (حتى لا تُقارَن عقود غير مكتملة)
                data_gaps.note("pro_rules_shadow", "no_alt_contract", None, tk)
                continue
            b = i["contracts"][0]
            bot = {"strike": b["strike"], "price": b["price"], "delta": b.get("delta"), "iv": b.get("iv"), "src": "bot"}
            rows = [_row(n, key, i.get("tag", "sent"), "bot", tk, side, bot, b["exp"], b.get("dte", ""), S, i.get("edge"), i.get("strength"))]
            rows += [_row(n, key, i.get("tag", "sent"), v, tk, side, k, exp, dte, S, i.get("edge"), i.get("strength")) for v, k in alts.items()]
            out += rows
        except Exception as e:  # noqa: BLE001
            data_gaps.note("pro_rules_shadow", "log_idea", e, str(i.get("ticker", "")))
    return append_inbox(out)


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
    s.setdefault("ideas", [])
    return s


def save_state(s):
    s["seen"] = s["seen"][-3000:]
    s["ideas"] = s["ideas"][-1500:]
    with open(STATE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


def read_inbox():
    try:
        return list(csv.DictReader(open(INBOX, encoding="utf-8")))
    except FileNotFoundError:
        return []
    except Exception as e:  # noqa: BLE001
        data_gaps.note("pro_rules_shadow", "inbox_read", e)
        return []


def ingest(state, n):
    """مجموعات الصندوق الجديدة (فكرة كاملة bot+atm+itm) إلى المفتوح؛ فكرة (سهم، اتجاه، يوم) تُقبل مرة واحدة كالبوت الحقيقي."""
    groups = {}
    for r in read_inbox():
        if r["id"] in state["seen"]:
            continue
        groups.setdefault(r["idea"], []).append(r)
    added = 0
    for idea, rows in groups.items():
        for r in rows:
            state["seen"].append(r["id"])
        try:
            t = datetime.fromisoformat(rows[0]["time"])
            if idea in state["ideas"] or (n - t).total_seconds() > MAX_LATE_MIN * 60 or {r["variant"] for r in rows} != {"bot", "atm", "itm"}:
                continue
            if any(float(r["entry"]) <= 0 for r in rows):
                continue
            state["ideas"].append(idea)
            for r in rows:
                state["open"].append({
                    "id": r["id"], "idea": idea, "tag": r["tag"], "variant": r["variant"], "ticker": r["ticker"], "side": r["side"],
                    "strike": float(r["strike"]), "exp": r["exp"], "entry": float(r["entry"]), "entry_time": r["time"], "spot": float(r["spot"]),
                    "delta": r["delta"], "edge": r["edge"], "strength": r["strength"], "occ": occ(r["ticker"], r["exp"], r["side"], r["strike"]),
                    "r1_out": "", "r1_pnl": "", "r1_time": "", "r2_out": "", "r2_pnl": "", "r2_time": "", "peak_pnl": 0.0, "close_pnl": "", "quotes": 0})
                added += 1
        except Exception as e:  # noqa: BLE001
            data_gaps.note("pro_rules_shadow", "ingest", e)
    return added


def _set(it, rule, out, pnl, n):
    it.update({f"{rule}_out": out, f"{rule}_pnl": round(pnl, 3), f"{rule}_time": n.isoformat(timespec="minutes")})


def step(it, bid, spot, n):
    """تقييم واحد بسعر البيع الحي وسعر السهم الحي (قد يكون None). يحدّث it؛ يرجع True حين تنتهي القاعدتان."""
    pnl = bid / it["entry"] - 1
    it["quotes"] += 1
    it["peak_pnl"] = round(max(it["peak_pnl"], pnl), 3)
    it["close_pnl"] = round(pnl, 3)
    t0 = datetime.fromisoformat(it["entry_time"])
    age_min = (n - t0).total_seconds() / 60
    exp_d = date.fromisoformat(it["exp"])
    hm = (n.hour, n.minute)
    horizon = (n - t0).days >= MAX_HOLD_DAYS and hm >= DAY_END
    expiry_day = n.date() == exp_d and hm >= FINAL_EXPIRY
    adverse = None
    if spot and it.get("spot"):
        mv = spot / it["spot"] - 1
        adverse = -mv if it["side"] == "CALL" else mv
    for rule in ("r1", "r2"):
        if it[f"{rule}_out"]:
            continue
        if pnl >= TP:
            _set(it, rule, "tp", pnl, n)
        elif rule == "r1" and age_min >= MIN_AGE_MIN and pnl <= -STOP:
            _set(it, rule, "stop", pnl, n)
        elif rule == "r2" and age_min >= MIN_AGE_MIN and adverse is not None and adverse >= STOCK_STOP:
            _set(it, rule, "stock_stop", pnl, n)
        elif rule == "r2" and (exp_d - n.date()).days <= TIME_STOP_DAYS and hm >= DAY_END:
            _set(it, rule, "time", pnl, n)
        elif expiry_day:
            _set(it, rule, "final", pnl, n)
        elif horizon:
            _set(it, rule, "horizon", pnl, n)
    return bool(it["r1_out"] and it["r2_out"])


def expire_if_past(it, n):
    if n.date() <= date.fromisoformat(it["exp"]):
        return False
    for rule in ("r1", "r2"):
        if not it[f"{rule}_out"]:
            if it["close_pnl"] == "":
                it.update({f"{rule}_out": "no_data", f"{rule}_time": n.isoformat(timespec="minutes")})
            else:
                _set(it, rule, "expired", it["close_pnl"], n)
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


def _live_quotes(occs):
    import live_quotes as lq
    return lq.chain_quotes(occs)


def _live_spot(tk):
    import live_quotes as lq
    return lq.spot(tk)[0]


def run(n=None, fetch_quotes=None, fetch_spot=None):
    n = n or datetime.now(NY)
    state = load_state()
    added = ingest(state, n)
    items = state["open"]
    quotes, spots = {}, {}
    if items and in_session(n):
        try:
            quotes = (fetch_quotes or _live_quotes)([it["occ"] for it in items])
        except Exception as e:  # noqa: BLE001
            data_gaps.note("pro_rules_shadow", "quotes", e)
        for tk in {it["ticker"] for it in items if not (it["r1_out"] and it["r2_out"])}:
            try:
                spots[tk] = (fetch_spot or _live_spot)(tk)
            except Exception as e:  # noqa: BLE001
                data_gaps.note("pro_rules_shadow", "spot", e, tk)
    done, still = [], []
    for it in items:
        z = quotes.get(it["occ"])
        finished = False
        if z is not None:
            finished = step(it, float(z[0]["bid"]), spots.get(it["ticker"]), n)
        elif in_session(n):
            data_gaps.note("pro_rules_shadow", "no_quote", None, it["ticker"])
        if not finished:
            finished = expire_if_past(it, n)
        (done if finished else still).append(it)
    state["open"] = still
    append_log(done)
    save_state(state)
    print(f"ظل قواعد المحترفين: +{added} صف، {len(still)} مفتوح، {len(done)} أُغلق.")
    return state


# ============================ التقرير ============================
def boot_ci(by_day, n_boot=2000, seed=5):
    days = list(by_day)
    if len(days) < 3:
        return None
    rng = random.Random(seed)
    m = []
    for _ in range(n_boot):
        vals = [v for d in (rng.choice(days) for _ in days) for v in by_day[d]]
        m.append(sum(vals) / len(vals))
    m.sort()
    return m[int(0.025 * n_boot)], m[int(0.975 * n_boot) - 1]


def paired(rows, variant, rule="r1"):
    """فروق (variant − bot) لكل فكرة مكتملة النتيجة بالقاعدة rule: [(يوم، فرق)]."""
    by = {}
    for r in rows:
        if r["variant"] in ("bot", variant) and r.get(f"{rule}_pnl") not in ("", None) and r.get(f"{rule}_out") not in ("no_data", ""):
            by.setdefault(r["idea"], {})[r["variant"]] = (float(r[f"{rule}_pnl"]), r["entry_time"][:10])
    return [(v[variant][1], v[variant][0] - v["bot"][0]) for v in by.values() if variant in v and "bot" in v]


def verdict(diffs):
    if len(diffs) < MIN_IDEAS:
        return f"لا حكم بعد ({len(diffs)} من {MIN_IDEAS} فكرة)"
    by_day = {}
    for d, x in diffs:
        by_day.setdefault(d, []).append(x)
    ci = boot_ci(by_day)
    if ci is None:
        return "لا حكم بعد (أيام قليلة)"
    if ci[0] > 0:
        return "تحققت الفرضية: العقد البديل يتفوق على عقد البوت (الفاصل فوق الصفر)"
    if ci[1] < 0:
        return "عكسية: عقد البوت أفضل (الفاصل تحت الصفر)"
    return "لم تتحقق بعد: الفاصل يشمل الصفر"


def report():
    rows = list(csv.DictReader(open(LOG, encoding="utf-8"))) if os.path.exists(LOG) else []
    st = load_state()
    names = {"bot": "عقد البوت", "atm": "على السعر (0.50)", "itm": "عميق (0.70)"}
    L = ["# ظل قواعد المحترفين (الشركات): نتائج آلية", "",
         "- لكل فكرة: عقد البوت + عقدان شهريان (دلتا 0.50 و0.70) بسعر الطلب الحي، يُتابَعان بسعر البيع الحي. سقف احتفاظ موحّد 10 أيام. لا تغيّر أي توصية.",
         "- r1 = قاعدة البوت (+50/−40)، r2 = وقف على السهم 1.5% + وقف زمني (7 أيام للانتهاء).",
         f"- **الفرضية المسجّلة مسبقاً:** متوسط (عقد 0.50 − عقد البوت) بقاعدة r1 > 0 بفاصل 95% بإعادة معاينة الأيام يستثني الصفر، بعد {MIN_IDEAS} فكرة مكتملة على الأقل.",
         "- ⚠️ عينة صغيرة في البداية، والحكم بالأيام لا بالحالات؛ أسعار حية لكن بلا تنفيذ فعلي.",
         f"- أفكار مكتملة: {len({r['idea'] for r in rows})} | عقود مفتوحة الآن: {len(st['open'])}", ""]
    if not rows:
        return _write(L + ["لا نتائج مغلقة بعد."])
    L += ["| العقد | الخروج | صفقات | متوسط | وسيط | رابحة | هدف / وقف / آخر |", "|---|---|---|---|---|---|---|"]
    for v in ("bot", "atm", "itm"):
        for rule in ("r1", "r2"):
            g = [r for r in rows if r["variant"] == v and r[f"{rule}_pnl"] not in ("", None) and r[f"{rule}_out"] not in ("no_data", "")]
            if not g:
                continue
            x = sorted(float(r[f"{rule}_pnl"]) for r in g)
            outs = [r[f"{rule}_out"] for r in g]
            tp = outs.count("tp")
            stops = outs.count("stop") + outs.count("stock_stop")
            L.append(f"| {names[v]} | {rule} | {len(x)} | {100 * sum(x) / len(x):+.1f}% | {100 * x[len(x) // 2]:+.1f}% | {sum(1 for y in x if y > 0)}/{len(x)} | "
                     f"{100 * tp / len(x):.0f}% / {100 * stops / len(x):.0f}% / {100 * (len(x) - tp - stops) / len(x):.0f}% |")
    L.append("")
    for v in ("atm", "itm"):
        for rule in ("r1", "r2"):
            d = paired(rows, v, rule)
            if d:
                vals = [x for _, x in d]
                L.append(f"- {names[v]} − عقد البوت ({rule}، {len(d)} فكرة): متوسط {100 * sum(vals) / len(vals):+.1f} نقطة مئوية، "
                         f"أفضل في {sum(1 for x in vals if x > 0)} من {len(vals)}.")
    L.append("")
    L.append("⚖️ **الحكم على الفرضية:** " + verdict(paired(rows, "atm", "r1")))
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
