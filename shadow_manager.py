#!/usr/bin/env python3
"""مدير الظلال (تسجيل وتقرير فقط؛ لا يغيّر أي توصية ولا يرسل للمشتركين). بطلب المستخدم 26/10/2026؛ القواعد مسجّلة مسبقاً في research/decision_rules.md البند 7 وفي CLAUDE.md (H-COUNCIL).

يقرأ سجلات الظلال من المستودع ويعمل ثلاثة أشياء بعد إغلاق كل يوم:
  1) لوحة تقييم: لكل ظل عدد الأحداث والأيام والمهارة (عائد اتجاهه − متوسط الاتجاهين) بفاصل 95% بإعادة معاينة الأيام، ومرحلته (مراقَب/مرشّح/عضو) ببوابات ثابتة.
  2) التناغم: اتساق سعر SPY بين الظلال، سلامة صفوف اليوم، فجوات البيانات، الظلال اليومية التي لم تسجّل، واتفاق الآراء في كل موعد.
  3) المجلس (H-COUNCIL): تصويت mom وf30 وآخر edge7 وآخر إشارة زخم حي قبل كل موعد من candidates_shadow؛ صوتان على الأقل متفقان بلا خلاف → صفقة ظل.
الهرم: الظل يقترح، والمدير يقيّم ويجمع، والبوت يبقى صاحب التوصية. لا ربط بسلوك البوت إلا بموافقة صريحة من صاحب المشروع.
الملفات: shadow_manager_council.csv (يُعاد بناؤه كل تشغيل من السجلات)، shadow_manager_state.json (يوم آخر إرسال)، research/shadow_manager_results.md.
تشغيل: python shadow_manager.py [--no-send] [--force]   (يرسل مرة واحدة يومياً بعد 16:10 نيويورك إلى TELEGRAM_CHAT_ID وحده)
"""
import csv
import json
import math
import os
import re
import sys
from datetime import date, datetime

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "research", "shadow_manager_results.md")
COUNCIL = os.path.join(ROOT, "shadow_manager_council.csv")
STATE = os.path.join(ROOT, "shadow_manager_state.json")

# بوابات البند 7 (لا تُعدَّل بعد رؤية النتائج)
CAND_N, CAND_DAYS, QUALITY = 40, 25, 0.95
MEMBER_DAYS, FDR_Q, MAX_CORR, MAX_DD_USD, DEFAULT_NOTIONAL = 60, 0.10, 0.6, 500.0, 300.0
DEMOTE_DAYS = 20
SPOT_WINDOW_MIN, SPOT_MAX_DIFF = 3, 0.0010
QUOTE_MAX_DIFF = 0.05                   # وصفي (أُضيف بعد التسجيل، لا يدخل في أي حكم): فرق وسط Alpaca وTastytrade للعقد نفسه
SLOTS = ("10:30", "11:30", "12:30", "13:30")
DAILY_LOGS = (("candidates_shadow_log.csv", "ظل المرشحين"), ("structure_shadow_log.csv", "ظل الهيكل"), ("flow_shadow_log.csv", "ظل التدفق"))
WATCH_ONLY = (("gex_outcomes.csv", "جدران الخيارات (GEX)"), ("news_shadow_outcomes.csv", "الأخبار"), ("blocked_shadow_log.csv", "المحجوبة"),
              ("exit_shadow_log.csv", "الخروج"), ("pro_rules_shadow_log.csv", "قواعد المحترفين"), ("contract_shadow_log.csv", "العقد"),
              ("ladder_shadow_log.csv", "السلّم"), ("preopen_shadow_log.csv", "ما قبل الافتتاح"), ("shadow_grid_log.csv", "المستويات"),
              ("shadow_ob_score_log.csv", "مناطق الطلب"), ("stocks_sector_log.csv", "قطاعات الشركات"), ("flow_shadow_log.csv", "التدفق"))
LABELS = {"cand_mom": "زخم آخر ساعة (mom)", "cand_f30": "أول 30 دقيقة (f30)", "cand_agree": "اتفاق الاثنين (agree)",
          "edge7": "إشارة البوت القوية (edge7)", "mom_live": "ظل الزخم الحي", "mom_delayed": "ظل الزخم المتأخر",
          "struct_vert": "سبريد شراء (vert)", "struct_em05": "عقد بعيد (em05)", "struct_ic": "كوندور حديدي",
          "bot_spx": "توصيات البوت SPX/SPY", "stocks": "عقود الشركات"}
STAGE_ICON = {"مراقَب": "👁", "مرشّح": "🎓", "عضو": "⭐"}
ARROW = {1: "⬆️", -1: "⬇️", 0: "➖", None: "➖"}
DAYS_AR = ("الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد")


# ============================ قراءة ============================
def read_csv(name):
    p = os.path.join(ROOT, name)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def hhmm(iso):
    m = re.search(r"T(\d\d:\d\d)", iso or "")
    return m.group(1) if m else ""


def sgn(x):
    v = num(x)
    return None if v is None else (1 if v > 0 else -1 if v < 0 else 0)


# ============================ محوّلات الظلال ============================
# كل محوّل يعيد (أحداث، عدد الصفوف المحاولة، عدد الصالح). الحدث: day, t, side(+1 كول/−1 بوت/0), ret, skill (أو None), notional ($ لعقد واحد)
def ev(day, t, side, ret, skill, notional):
    return {"day": day, "t": t, "side": side, "ret": ret, "skill": skill, "notional": notional}


def from_candidates(rows, key):
    out, tried = [], 0
    for r in rows:
        if r.get("status") != "closed":
            continue
        s = sgn(r.get(key))
        if not s:
            continue
        tried += 1
        cr, pr, ce, pe = num(r.get("call_ret")), num(r.get("put_ret")), num(r.get("call_entry")), num(r.get("put_entry"))
        if None in (cr, pr, ce, pe) or ce <= 0 or pe <= 0:
            continue
        ret = cr if s > 0 else pr
        out.append(ev(r["day"], r.get("slot", ""), s, ret, ret - (cr + pr) / 2, 100 * (ce if s > 0 else pe)))
    return out, tried, len(out)


def pairs(rows, keyf, sig_roles, opp_roles):
    """يقرن كل صف إشارة بصفه المعاكس بالمفتاح نفسه. يعيد [(sig, opp)]."""
    opp = {keyf(r): r for r in rows if r.get("role") in opp_roles}
    return [(r, opp.get(keyf(r))) for r in rows if r.get("role") in sig_roles]


def from_edge7(rows):
    out, tried, seen = [], 0, set()
    by_time = {}
    for r in rows:
        by_time.setdefault(r.get("entry_time"), []).append(r)
    for t, g in by_time.items():
        roles = {r["role"]: r for r in g}
        sig, opp = (roles.get("spy_bot"), roles.get("spy_opp")) if "spy_bot" in roles else (roles.get("bot"), roles.get("opp"))
        if sig is None or t in seen:
            continue
        seen.add(t)
        tried += 1
        p, q, e = num(sig.get("pnl")), num(opp.get("pnl")) if opp else None, num(sig.get("entry"))
        if p is None or e is None or e <= 0:
            continue
        s = 1 if sig.get("side") == "CALL" else -1
        out.append(ev(t[:10], hhmm(t), s, p, None if q is None else p - (p + q) / 2, 100 * e))
    return out, tried, len(out)


def from_momentum(rows, defn):
    rows = [r for r in rows if r.get("defn") == defn]
    out, tried = [], 0
    for sig, opp in pairs(rows, lambda r: r.get("sig_time"), ("signal",), ("opposite",)):
        tried += 1
        p, q, e = num(sig.get("pnl")), num(opp.get("pnl")) if opp else None, num(sig.get("entry"))
        if p is None or e is None or e <= 0:
            continue
        out.append(ev(sig["date"], hhmm(sig.get("sig_time")), 1 if sig.get("side") == "CALL" else -1, p,
                      None if q is None else p - (p + q) / 2, 100 * e))
    return out, tried, len(out)


def from_structure(rows, kind):
    out, tried = [], 0
    for r in rows:
        if r.get("kind") != kind:
            continue
        cap = num(r.get("capital"))
        pnl = (num(r.get("managed_pnl")) if num(r.get("managed_pnl")) is not None else num(r.get("hold_pnl"))) if kind == "ic" else num(r.get("t60_pnl"))
        if pnl is None and kind == "ic" and not r.get("hold_time"):
            continue                    # كوندور لم ينتهِ بعد: ليس صفاً تالفاً
        if pnl is None and kind != "ic" and not r.get("t60_time"):
            continue
        tried += 1
        if pnl is None or cap is None or cap <= 0:
            continue
        side = 0 if kind == "ic" else (1 if r.get("side") == "CALL" else -1)
        out.append(ev(r["date"], hhmm(r.get("entry_time")), side, pnl / cap, None, 100 * cap))
    return out, tried, len(out)


def from_bot(rows):
    out, tried = [], 0
    for r in rows:
        if r.get("pnl") in ("", None):
            continue
        tried += 1
        p, pr = num(r.get("pnl")), num(r.get("price"))
        if p is None or pr is None or pr <= 0:
            continue
        out.append(ev(r["date"], r.get("time", ""), 1 if r.get("side") == "CALL" else -1, p / pr, None, 100 * pr))
    return out, tried, len(out)


def from_stocks(positions):
    out, tried = [], 0
    for p in positions:
        c = p.get("closed")
        if not c:
            continue
        tried += 1
        r, e = num(c.get("pnl")), num(p.get("entry"))
        if r is None or e is None or e <= 0:
            continue
        out.append(ev(str(p.get("time", ""))[:10], hhmm(p.get("time")), 1 if p.get("side") == "CALL" else -1, r, None, 100 * e))
    return out, tried, len(out)


def load(data=None):
    """{key: (events, tried, valid)} لكل ظل ذي مقياس موحّد، بترتيب ثابت (مهم لبوابة التنوع)."""
    d = data or {}
    cand = d.get("cand", read_csv("candidates_shadow_log.csv"))
    mom = d.get("momentum", read_csv("momentum_shadow_log.csv"))
    struct = d.get("structure", read_csv("structure_shadow_log.csv"))
    if "stocks" in d:
        positions = d["stocks"]
    else:
        try:
            positions = json.load(open(os.path.join(ROOT, "stocks_positions.json"), encoding="utf-8")).get("positions", [])
        except Exception:  # noqa: BLE001
            positions = []
    return {"cand_mom": from_candidates(cand, "mom"), "cand_f30": from_candidates(cand, "f30"), "cand_agree": from_candidates(cand, "agree"),
            "edge7": from_edge7(d.get("edge7", read_csv("edge7_shadow_log.csv"))),
            "mom_live": from_momentum(mom, "live"), "mom_delayed": from_momentum(mom, "delayed"),
            "struct_vert": from_structure(struct, "vert"), "struct_em05": from_structure(struct, "em05"), "struct_ic": from_structure(struct, "ic"),
            "bot_spx": from_bot(d.get("bot", read_csv("zero_dte_log.csv"))), "stocks": from_stocks(positions)}


# ============================ إحصاء ============================
def metric(e):
    return e["skill"] if e["skill"] is not None else e["ret"]


def by_day(events):
    out = {}
    for e in events:
        out.setdefault(e["day"], []).append(metric(e))
    return out


def boot(vals_by_day, n=3000, seed=7):
    """(حد أدنى، حد أعلى، p من جانب واحد) لمتوسط الأحداث بإعادة معاينة الأيام؛ None لأقل من 8 أيام."""
    days = sorted(vals_by_day)
    if len(days) < 8:
        return None
    S = np.array([sum(vals_by_day[d]) for d in days])
    N = np.array([len(vals_by_day[d]) for d in days])
    idx = np.random.default_rng(seed).integers(0, len(days), (n, len(days)))
    m = S[idx].sum(1) / N[idx].sum(1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5)), float((m <= 0).mean())


def mean_of(vals_by_day, days):
    v = [x for d in days for x in vals_by_day[d]]
    return float(np.mean(v)) if v else None


def drawdown_usd(events):
    eq, peak, dd = 0.0, 0.0, 0.0
    for e in sorted(events, key=lambda e: (e["day"], e["t"])):
        eq += e["ret"] * (e["notional"] or DEFAULT_NOTIONAL)
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return dd


def bh_pass(pvals, q=FDR_Q):
    """Benjamini-Hochberg: {key: True/False} للمفاتيح ذات p."""
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m, k_max = len(items), 0
    for i, (_, p) in enumerate(items, 1):
        if p <= q * i / m:
            k_max = i
    return {k: i < k_max for i, (k, _) in enumerate(items)}


def corr(a, b):
    common = sorted(set(a) & set(b))
    if len(common) < 10:
        return None
    x, y = np.array([np.mean(a[d]) for d in common]), np.array([np.mean(b[d]) for d in common])
    if x.std() == 0 or y.std() == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def evaluate(shadows):
    """تقييم كل ظل ومرحلته ببوابات البند 7. يعيد {key: dict}."""
    res = {}
    for k, (events, tried, valid) in shadows.items():
        vb = by_day(events)
        days = sorted(vb)
        ci = boot(vb)
        half = len(days) // 2
        top2 = sorted(days, key=lambda d: -sum(vb[d]))[:2]
        recent = boot({d: vb[d] for d in days[-DEMOTE_DAYS:]}) if len(days) >= DEMOTE_DAYS else None
        res[k] = {"n": len(events), "days": len(days), "quality": (valid / tried) if tried else None, "tried": tried,
                  "mean": mean_of(vb, days), "ci": ci, "skill_kind": "مهارة" if events and all(e["skill"] is not None for e in events) else "عائد",
                  "h1": mean_of(vb, days[:half]) if half else None, "h2": mean_of(vb, days[half:]) if half else None,
                  "no_top2": mean_of(vb, [d for d in days if d not in top2]) if len(days) > 2 else None,
                  "dd": drawdown_usd(events), "recent_hi": recent[1] if recent else None, "by_day": vb, "events": events}
    fdr = bh_pass({k: r["ci"][2] for k, r in res.items() if r["ci"]})
    members = []
    for k, r in res.items():
        miss = []
        if r["quality"] is None or r["quality"] < QUALITY:
            miss.append("جودة البيانات")
        if r["n"] < CAND_N:
            miss.append(f"{CAND_N - r['n']} حدثاً")
        if r["days"] < CAND_DAYS:
            miss.append(f"{CAND_DAYS - r['days']} يوماً")
        if r["mean"] is None or r["mean"] <= 0:
            miss.append("متوسط موجب")
        stage = "مرشّح" if not miss else "مراقَب"
        miss_m = []
        if stage == "مرشّح":
            if r["days"] < MEMBER_DAYS:
                miss_m.append(f"{MEMBER_DAYS - r['days']} يوماً")
            if not r["ci"] or r["ci"][0] <= 0:
                miss_m.append("فاصل فوق الصفر")
            if not (r["h1"] and r["h1"] > 0 and r["h2"] and r["h2"] > 0 and r["no_top2"] and r["no_top2"] > 0):
                miss_m.append("ثبات")
            if not fdr.get(k):
                miss_m.append("تصحيح المقارنات")
            if any((c := corr(r["by_day"], res[m]["by_day"])) is not None and c >= MAX_CORR for m in members):
                miss_m.append("تنوع")
            if r["dd"] > MAX_DD_USD:
                miss_m.append("المخاطرة")
            if not miss_m:
                stage = "عضو"
        if stage == "عضو" and r["recent_hi"] is not None and r["recent_hi"] < 0:
            stage, miss_m = "مراقَب", ["إيقاف: آخر 20 يوماً تحت الصفر"]
        if stage == "عضو":
            members.append(k)
        r.update(stage=stage, missing=miss if stage == "مراقَب" else miss_m, status=status_of(r))
    return res


def status_of(r):
    if r["n"] < CAND_N or r["days"] < CAND_DAYS or not r["ci"]:
        return "بيانات غير كافية"
    lo, hi, _ = r["ci"]
    return "متقدم" if lo > 0 else "عكسي" if hi < 0 else "لا ميزة بعد"


# ============================ المجلس (H-COUNCIL) ============================
def last_side_before(events, day, t):
    s = [e for e in events if e["day"] == day and e["t"] and e["t"] <= t and e["side"]]
    return max(s, key=lambda e: e["t"])["side"] if s else None


def council(cand_rows, edge7_events, mom_events):
    """صف لكل موعد candidates مغلق: الأصوات والحالة والقرار والعائد. القاعدة مثبّتة في البند 7."""
    out = []
    for r in cand_rows:
        if r.get("status") != "closed":
            continue
        day, slot = r["day"], r.get("slot", "")
        votes = {"mom": sgn(r.get("mom")) or None, "f30": sgn(r.get("f30")) or None,
                 "edge7": last_side_before(edge7_events, day, slot), "momentum": last_side_before(mom_events, day, slot)}
        have = [v for v in votes.values() if v]
        state = "بلا رأي" if not have else "رأي واحد" if len(have) == 1 else "خلاف" if len(set(have)) > 1 else "إجماع"
        side = have[0] if state == "إجماع" else 0
        cr, pr = num(r.get("call_ret")), num(r.get("put_ret"))
        ret = skill = None
        if side and cr is not None and pr is not None:
            ret = cr if side > 0 else pr
            skill = ret - (cr + pr) / 2
        out.append({"day": day, "slot": slot, **votes, "n_votes": len(have), "state": state, "decision": {1: "CALL", -1: "PUT", 0: ""}[side],
                    "ret": ret, "skill": skill, "base": None if cr is None or pr is None else (cr + pr) / 2})
    return out


def council_verdict(rows):
    ev_ = [r for r in rows if r["skill"] is not None]
    vb = {}
    for r in ev_:
        vb.setdefault(r["day"], []).append(r["skill"])
    ci = boot(vb)
    m = float(np.mean([r["skill"] for r in ev_])) if ev_ else None
    if len(ev_) < CAND_N or len(vb) < CAND_DAYS or not ci:
        return f"H-COUNCIL: لا حكم بعد ({len(ev_)} من {CAND_N} حدثاً، {len(vb)} من {CAND_DAYS} يوماً)", m, ci
    lo, hi, _ = ci
    d = f"مهارة {100 * m:+.1f}% بفاصل {100 * lo:+.1f}…{100 * hi:+.1f}"
    return "H-COUNCIL: " + ("تحققت (" + d + ")" if lo > 0 else "عكسية (" + d + ")" if hi < 0 else "لم تتحقق (" + d + " يشمل الصفر)"), m, ci


# ============================ أدوار الظلال: درجة الاتجاه والمخالفة (H-ROLE) ============================
HORIZONS = (15, 30, 60)
DIR_MIN_N, DIR_MIN_DAYS, DISSENT_MIN = 40, 25, 30
DIRECTION = os.path.join(ROOT, "shadow_manager_direction.csv")
DIR_KEYS = ("cand_mom", "cand_f30", "cand_agree", "edge7", "mom_live", "mom_delayed", "bot_spx", "struct_vert", "struct_em05")
VOTER_LABELS = {"mom": "زخم آخر ساعة", "f30": "أول 30 دقيقة", "edge7": "إشارة البوت القوية", "momentum": "ظل الزخم"}


def opinions(cand_rows, shadows):
    """{ظل: [(يوم، وقت، جهة)]}: آراء الاتجاه. للمرشحين كل موعد فيه رأي (مغلقاً كان العقد أم لا)؛ والباقي من أحداث الظل."""
    out = {}
    for key, col in (("cand_mom", "mom"), ("cand_f30", "f30"), ("cand_agree", "agree")):
        out[key] = [(r["day"], r.get("slot", ""), s) for r in cand_rows if r.get("day") and (s := sgn(r.get(col)))]
    for k in DIR_KEYS[3:]:
        out[k] = [(e["day"], e["t"], e["side"]) for e in shadows.get(k, ([], 0, 0))[0] if e["side"] and e["t"]]
    return out


def spy_minutes(days):
    """{يوم: Series إغلاق SPY لكل دقيقة بوقت نهايتها (نيويورك، بلا منطقة)}: Alpaca SIP، وإلا ياهو (آخر 7 أيام)."""
    import pandas as pd
    out = {}
    if not days:
        return out
    try:
        import urllib.parse
        import live_quotes as lq
        q = urllib.parse.urlencode({"timeframe": "1Min", "start": f"{min(days)}T13:00:00Z", "end": f"{max(days)}T21:30:00Z",
                                    "feed": "sip", "adjustment": "raw", "limit": 10000})
        bars, tok = [], None
        while True:
            d = lq._get(f"{lq.DATA}/v2/stocks/SPY/bars?{q}" + (f"&page_token={tok}" if tok else ""))
            bars += d.get("bars") or []
            tok = d.get("next_page_token")
            if not tok:
                break
        idx = pd.DatetimeIndex([pd.Timestamp(b["t"]) for b in bars]).tz_convert("America/New_York").tz_localize(None) + pd.Timedelta(minutes=1)
        s = pd.Series([float(b["c"]) for b in bars], index=idx)
    except Exception as e:  # noqa: BLE001
        print("Alpaca (دقائق SPY):", type(e).__name__, "← ياهو")
        try:
            import yfinance as yf
            h = yf.Ticker("SPY").history(period="7d", interval="1m")["Close"]
            s = pd.Series(h.values, index=h.index.tz_convert("America/New_York").tz_localize(None) + pd.Timedelta(minutes=1))
        except Exception as e2:  # noqa: BLE001
            print("ياهو (دقائق SPY):", type(e2).__name__)
            return out
    for d in days:
        g = s[s.index.date == date.fromisoformat(d)]
        if len(g):
            out[d] = g
    return out


def move(px, day, t, h):
    """حركة SPY النسبية من الوقت t (HH:MM) إلى t+h دقيقة في اليوم نفسه، أو None."""
    from datetime import timedelta
    s = px.get(day)
    if s is None or not t:
        return None
    t0 = datetime.fromisoformat(f"{day}T{t}")
    t1 = t0 + timedelta(minutes=h)
    if (t1.hour, t1.minute) > (16, 0):
        return None
    a, b = s[s.index <= t0], s[s.index <= t1]
    if a.empty or b.empty or (t0 - a.index[-1]).total_seconds() > 300 or (t1 - b.index[-1]).total_seconds() > 300:
        return None
    return float(b.iloc[-1] / a.iloc[-1] - 1)


def direction_rows(ops, px):
    out = []
    for k, lst in ops.items():
        for day, t, side in lst:
            r = {"shadow": k, "day": day, "t": t, "side": side}
            for h in HORIZONS:
                m = move(px, day, t, h)
                r[f"m{h}"] = None if m is None else side * m
            out.append(r)
    return out


def direction_summary(rows):
    """{ظل: dict} عند 30 دقيقة (الأساسية) مع 15 و60 وصفياً."""
    res = {}
    for k in DIR_KEYS:
        rs = [r for r in rows if r["shadow"] == k and r["m30"] is not None]
        if not rs:
            continue
        by = {}
        for r in rs:
            by.setdefault(r["day"], []).append(r["m30"])
        res[k] = {"n": len(rs), "days": len(by), "mean": float(np.mean([r["m30"] for r in rs])), "hit": float(np.mean([r["m30"] > 0 for r in rs])),
                  "ci": boot(by), **{f"mean{h}": (float(np.mean(v)) if (v := [r[f"m{h}"] for r in rows if r["shadow"] == k and r[f"m{h}"] is not None]) else None)
                                     for h in (15, 60)}}
    return res


def dissent(council_rows, px):
    """مواعيد الخلاف: الأقلية ومن كان محقاً بعد 15/30/60 دقيقة. التعادل 2 مقابل 2 بلا أقلية."""
    out = []
    for c in council_rows:
        if c["state"] != "خلاف":
            continue
        votes = {k: c[k] for k in ("mom", "f30", "edge7", "momentum") if c[k]}
        up = [k for k, v in votes.items() if v > 0]
        dn = [k for k, v in votes.items() if v < 0]
        if len(up) == len(dn):
            out.append({"day": c["day"], "slot": c["slot"], "minority": [], "side": 0, **{f"right{h}": None for h in HORIZONS}})
            continue
        mino, side = (up, 1) if len(up) < len(dn) else (dn, -1)
        r = {"day": c["day"], "slot": c["slot"], "minority": mino, "side": side}
        for h in HORIZONS:
            m = move(px, c["day"], c["slot"], h)
            r[f"right{h}"] = None if m is None or m == 0 else (side * m > 0)
        out.append(r)
    return out


def dissent_text(d):
    """سطر المخالف لموعد خلاف: «المخالف: زخم آخر ساعة ⬇️ ضد 3 ⬆️»."""
    if not d or not d["minority"]:
        return "تعادل"
    return f"المخالف: {'، '.join(VOTER_LABELS[k] for k in d['minority'])} {ARROW[d['side']]}"


def write_direction(rows):
    cols = ["shadow", "day", "t", "side"] + [f"m{h}" for h in HORIZONS]
    with open(DIRECTION, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if r.get(c) is None else (round(r[c], 6) if isinstance(r[c], float) else r[c])) for c in cols})


def build_roles(summary, dis, day):
    """رسالة ثانية (تيليجرام) وقسم markdown: درجة الاتجاه لكل ظل والمخالفة."""
    tg = ["🧭 <b>أدوار الظلال: قراءة الاتجاه</b> (H-ROLE)", "<i>حركة SPY في جهة رأي الظل بعد 30 دقيقة، بلا علاقة بالعقد</i>", ""]
    md = ["", "## أدوار الظلال: قراءة الاتجاه (H-ROLE)", "", "حركة SPY في جهة الرأي (الأساسية 30 دقيقة؛ 15 و60 وصفياً).", "",
          "| الظل | آراء | أيام | إصابة 30د | متوسط 30د | فاصل 95% | 15د | 60د | الحكم |", "|---|---|---|---|---|---|---|---|---|"]
    for k, s in sorted(summary.items(), key=lambda kv: -kv[1]["mean"]):
        if s["n"] < DIR_MIN_N or s["days"] < DIR_MIN_DAYS or not s["ci"]:
            v = f"لا حكم بعد ({s['n']}/{DIR_MIN_N}، {s['days']}/{DIR_MIN_DAYS} يوماً)"
        else:
            v = "يقرأ الاتجاه" if s["ci"][0] > 0 else "عكسي" if s["ci"][1] < 0 else "لا يتفوق"
        tg.append(f"• <b>{LABELS[k]}</b>: آراء {s['n']} | إصابة {100 * s['hit']:.0f}% | {100 * s['mean']:+.3f}%")
        ci = "-" if not s["ci"] else f"{100 * s['ci'][0]:+.3f}…{100 * s['ci'][1]:+.3f}%"
        m15 = "-" if s["mean15"] is None else f"{100 * s['mean15']:+.3f}%"
        m60 = "-" if s["mean60"] is None else f"{100 * s['mean60']:+.3f}%"
        md.append(f"| {LABELS[k]} | {s['n']} | {s['days']} | {100 * s['hit']:.0f}% | {100 * s['mean']:+.3f}% | {ci} | {m15} | {m60} | {v} |")
    real = [d for d in dis if d["minority"]]
    tg += ["", f"⚔️ <b>المخالفة</b>: مواعيد خلاف {len(dis)} (منها تعادل {len(dis) - len(real)})"]
    md += ["", "## المخالفة: حين يخالف صوت الأغلبية، من كان محقاً؟", ""]
    for h in HORIZONS:
        v = [d[f"right{h}"] for d in real if d[f"right{h}"] is not None]
        line = f"الأقلية محقة بعد {h}د: {sum(v)} من {len(v)}" + (f" ({100 * sum(v) / len(v):.0f}%)" if v else "")
        tg.append("   " + line)
        md.append(f"- {line}")
    v30 = [d["right30"] for d in real if d["right30"] is not None]
    md += ["", "⚖️ H-ROLE (المخالفة): " + (f"لا حكم بعد ({len(v30)} من {DISSENT_MIN} موعد خلاف)" if len(v30) < DISSENT_MIN else
                                         f"الأقلية محقة {100 * np.mean(v30):.0f}% عند 30د")]
    who = {}
    for d in real:
        for k in d["minority"]:
            if d["right30"] is not None:
                who.setdefault(k, []).append(d["right30"])
    if who:
        tg.append("   حسب المخالف (30د): " + "، ".join(f"{VOTER_LABELS[k]} {sum(v)}/{len(v)}" for k, v in who.items()))
        md.append("- حسب المخالف (30د): " + "، ".join(f"{VOTER_LABELS[k]} {sum(v)}/{len(v)}" for k, v in who.items()))
    today = [d for d in real if d["day"] == day]
    for d in today:
        res = "؛ ".join(f"{h}د {'✅' if d[f'right{h}'] else '❌' if d[f'right{h}'] is False else '➖'}" for h in HORIZONS)
        tg.append(f"   اليوم {d['slot']}: {dissent_text(d)} ← {res}")
    tg += ["", "⚠️ الحكم بعد 40 رأياً على 25 يوماً لكل ظل، و30 موعد خلاف. تعليمي وليس توصية."]
    return "\n".join(md), "\n".join(tg)[:3900]


# ============================ التناغم اليومي ============================
def _ts(t):
    """وقت السجل بلا منطقة زمنية (كل السجلات بتوقيت نيويورك): «2026-10-26T10:31-04:00» أو «2026-10-26 10:31»."""
    m = re.match(r"(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d)", t or "")
    return datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}") if m else None


def spot_readings(groups):
    """groups: {الأصل: [(المصدر، الصفوف، عمود الوقت، عمود السعر)]} → [(الأصل، المصدر، الوقت، السعر)]."""
    out = []
    for asset, srcs in groups.items():
        for src, rows, tcol, scol in srcs:
            for r in rows:
                v, t = num(r.get(scol)), _ts(r.get(tcol))
                if v and t:
                    out.append((asset, src, t, v))
    return out


def spot_check(readings, day):
    """مقارنات بين مصادر مختلفة في حدود 3 دقائق: (عدد، أقصى فرق، [مخالفات])."""
    rs = [x for x in readings if x[2].date().isoformat() == day]
    n, worst, bad = 0, 0.0, []
    for i, a in enumerate(rs):
        for b in rs[i + 1:]:
            if a[0] != b[0] or a[1] == b[1] or abs((a[2] - b[2]).total_seconds()) > SPOT_WINDOW_MIN * 60:
                continue
            n += 1
            d = abs(a[3] - b[3]) / min(a[3], b[3])
            worst = max(worst, d)
            if d > SPOT_MAX_DIFF:
                bad.append(f"{a[0]}: {a[1]} {a[3]:.2f} مقابل {b[1]} {b[3]:.2f} ({a[2]:%H:%M})")
    return n, worst, bad


def quote_check(rows, day, max_diff=None):
    """وصفي: سعر الوسط للعقد نفسه من Alpaca وTastytrade في الفحص نفسه (live_quotes). (عدد، الوسيط، [مخالفات])."""
    max_diff = QUOTE_MAX_DIFF if max_diff is None else max_diff
    diffs, bad = [], []
    for r in rows:
        if not str(r.get("time", "")).startswith(day):
            continue
        ab, aa, tb, ta = (num(r.get(c)) for c in ("alpaca_bid", "alpaca_ask", "tasty_bid", "tasty_ask"))
        if None in (ab, aa, tb, ta) or min(ab, aa, tb, ta) <= 0:
            continue
        ma, mt = (ab + aa) / 2, (tb + ta) / 2
        d = abs(ma - mt) / min(ma, mt)
        diffs.append(d)
        if d > max_diff:
            bad.append(f"{r.get('contract', '')} {ma:.2f} مقابل {mt:.2f}")
    return len(diffs), (float(np.median(diffs)) if diffs else 0.0), bad


DATE_COLS = ("date", "day", "time", "entry_time", "sig_time", "snap_time")


def row_date(r):
    """تاريخ التسجيل: من أعمدة الوقت المعروفة أولاً، وإلا أول تاريخ بترتيب الأعمدة (exp تاريخ انتهاء لا تسجيل)."""
    for v in [r.get(k) for k in DATE_COLS if r.get(k)] + [v for k, v in r.items() if k != "exp"]:
        m = re.match(r"\s*(20\d\d-\d\d-\d\d)", v) if isinstance(v, str) else None
        if m:
            return m.group(1)
    return None


def last_date(name):
    rows = read_csv(name)
    found = [d for d in (row_date(r) for r in rows[-20:]) if d]
    return (max(found) if found else None), len(rows)


def gaps_today(day):
    n = 0
    for f in sorted(os.listdir(ROOT)):
        if f.startswith("data_gaps_") and f.endswith(".csv"):
            n += sum(1 for r in read_csv(f) if any(str(v).startswith(day) for v in r.values()))
    return n


# ============================ التقرير ============================
def pc(x, signed=True):
    return "-" if x is None else (f"{100 * x:+.1f}%" if signed else f"{100 * x:.0f}%")


def ci_txt(ci):
    return "" if not ci else f" [{100 * ci[0]:+.1f}…{100 * ci[1]:+.1f}]"


def quote_line(q):
    n, med, bad = q
    if not n:
        return "➖ اتساق أسعار العقود (Alpaca/Tastytrade): لا مقارنات اليوم"
    return (f"✅ اتساق أسعار العقود (Alpaca/Tastytrade): {n} مقارنة، الفرق الوسيط {100 * med:.1f}%" if not bad else
            f"⚠️ أسعار العقود: {len(bad)} من {n} مقارنة فرقها فوق {100 * QUOTE_MAX_DIFF:.0f}% (الوسيط {100 * med:.1f}%)")


def build(res, council_rows, day, harmony, watch, dis=None):
    """(نص markdown، نص تيليجرام HTML)."""
    d = date.fromisoformat(day)
    head = f"🧭 <b>مدير الظلال</b> | {DAYS_AR[d.weekday()]} {d:%d/%m/%Y}"
    tg = [head, "<i>تقرير ما بعد الإغلاق: تسجيل فقط، لا يغيّر أي توصية</i>", "", "📊 <b>تقييم الظلال</b> (منذ بدايتها)"]
    md = [f"# مدير الظلال: تقرير {day} (نتائج آلية، ظل)", "", "القواعد مسجّلة مسبقاً في research/decision_rules.md البند 7.", "",
          "| الظل | المرحلة | الأحداث | الأيام | المقياس | المتوسط | فاصل 95% | الجودة | أقصى تراجع | الحالة | ينقصه |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    order = sorted(res, key=lambda k: ({"عضو": 0, "مرشّح": 1, "مراقَب": 2}[res[k]["stage"]], -res[k]["n"]))
    for k in order:
        r = res[k]
        if r["tried"] == 0:
            continue
        today = [e for e in r["events"] if e["day"] == day]
        t_today = f" | اليوم {len(today)}: {pc(float(np.mean([metric(e) for e in today])))}" if today else ""
        tg.append(f"{STAGE_ICON[r['stage']]} <b>{LABELS[k]}</b>: أحداث {r['n']} · أيام {r['days']} | {r['skill_kind']} {pc(r['mean'])}{ci_txt(r['ci'])} | {r['status']}{t_today}")
        if r["missing"]:
            tg.append(f"    ↳ ينقصه للمرحلة التالية: {'، '.join(r['missing'][:3])}")
        md.append(f"| {LABELS[k]} | {r['stage']} | {r['n']} | {r['days']} | {r['skill_kind']} | {pc(r['mean'])} | {ci_txt(r['ci']).strip() or '-'} | "
                  f"{pc(r['quality'], False)} | {r['dd']:,.0f}$ | {r['status']} | {'، '.join(r['missing']) or '-'} |")
    n_sp, worst, bad = harmony["spot"]
    tg += ["", "🤝 <b>التناغم اليوم</b>",
           (f"✅ اتساق سعر المؤشر بين الظلال: {n_sp} مقارنة، أقصى فرق {100 * worst:.2f}%" if not bad else f"⚠️ اختلاف سعر المؤشر بين الظلال: {'؛ '.join(bad[:3])}") if n_sp else "➖ اتساق سعر المؤشر: لا قراءات متزامنة اليوم",
           quote_line(harmony["quotes"]),
           "✅ جودة سجلات الظلال ≥ 95%" if not harmony["bad_rows"] else f"⚠️ جودة دون 95%: {'، '.join(harmony['bad_rows'])}",
           "✅ فجوات البيانات: لا شيء" if not harmony["gaps"] else f"⚠️ فجوات بيانات اليوم: {harmony['gaps']}",
           "✅ كل الظلال اليومية سجّلت" if not harmony["silent"] else f"⚠️ لم يسجّل اليوم: {'، '.join(harmony['silent'])}"]
    today_c = [c for c in council_rows if c["day"] == day]
    if today_c:
        tg.append("🗣️ الآراء في كل موعد (mom، f30، البوت، الزخم):")
        for c in today_c:
            v = " ".join(ARROW[c[x]] for x in ("mom", "f30", "edge7", "momentum"))
            res_txt = f" → {c['decision']} {pc(c['ret'])}" if c["decision"] else ""
            d = next((x for x in (dis or []) if x["day"] == c["day"] and x["slot"] == c["slot"]), None)
            who = f" ({dissent_text(d)})" if c["state"] == "خلاف" and d is not None else ""
            tg.append(f"   {c['slot']}: {v} | {c['state']}{who}{res_txt}")
    verdict, m, ci = council_verdict(council_rows)
    states = {}
    for c in council_rows:
        if c["base"] is not None:
            states.setdefault(c["state"], []).append(c)
    tg += ["", "🗳️ <b>المجلس: اختبار الدمج (H-COUNCIL)</b>", verdict + (f" | حتى الآن {pc(m)}{ci_txt(ci)}" if m is not None else "")]
    md += ["", "## التناغم", f"- اتساق سعر SPY: {n_sp} مقارنة، أقصى فرق {100 * worst:.2f}%" + (f"؛ مخالفات: {'؛ '.join(bad)}" if bad else ""),
           f"- {quote_line(harmony['quotes'])}", f"- جودة دون 95%: {'، '.join(harmony['bad_rows']) or 'لا'}", f"- فجوات البيانات اليوم: {harmony['gaps']}",
           f"- لم يسجّل اليوم: {'، '.join(harmony['silent']) or 'لا'}", "", "## المجلس (H-COUNCIL)", "", f"⚖️ {verdict}", "",
           "| الحالة | المواعيد | متوسط عائد الاتجاهين (خط الأساس) |", "|---|---|---|"]
    for s in ("إجماع", "خلاف", "رأي واحد", "بلا رأي"):
        g = states.get(s, [])
        md.append(f"| {s} | {len(g)} | {pc(float(np.mean([c['base'] for c in g]))) if g else '-'} |")
    if states:
        tg.append("الحالات حتى الآن: " + "، ".join(f"{s} {len(states.get(s, []))}" for s in ("إجماع", "خلاف", "رأي واحد", "بلا رأي")))
    w = [f"{name} ({date.fromisoformat(ld):%d/%m})" if ld else name for name, ld in watch]
    if w:
        tg += ["", "📁 <b>قيد المراقبة بلا مقياس موحّد بعد</b>: " + "، ".join(w)]
        md += ["", "## قيد المراقبة بلا مقياس موحّد", ""] + [f"- {x}" for x in w]
    tg += ["", "⚠️ عينات صغيرة؛ لا حكم قبل 40 حدثاً و25 يوماً. تعليمي وليس توصية."]
    return "\n".join(md), "\n".join(tg)[:3900]


def harmony_for(day, shadows):
    groups = {"SPY": [("المرشحين", read_csv("candidates_shadow_log.csv"), "entry_time", "spot"), ("الهيكل", read_csv("structure_shadow_log.csv"), "entry_time", "entry_spot"),
                      ("الزخم", read_csv("momentum_shadow_log.csv"), "entry_time", "entry_spy")],
              "SPX": [("الإشارات الإضافية (ياهو، متأخر)", read_csv("signals_extra_log.csv"), "time", "spot"), ("المستويات", read_csv("levels_log.csv"), "time", "spot"),
                      ("edge7", [r for r in read_csv("edge7_shadow_log.csv") if r.get("role") == "bot"], "entry_time", "spot")]}
    # جودة البيانات (البند 7): الظل الذي تقل صفوفه الصالحة عن 95% منذ بدايته
    bad = [f"{LABELS[k]} ({tried - valid} صف)" for k, (_, tried, valid) in shadows.items() if tried and valid / tried < QUALITY]
    silent = [label for f, label in DAILY_LOGS if (last_date(f)[0] or "") < day]
    return {"spot": spot_check(spot_readings(groups), day), "quotes": quote_check(read_csv("live_quotes_log.csv"), day),
            "bad_rows": bad, "gaps": gaps_today(day), "silent": silent}


def ny_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001
        return datetime.utcnow()


def send(text):
    """إلى TELEGRAM_CHAT_ID وحده (رسالة مستقلة). لا يطبع المفاتيح ولا نص الخطأ."""
    import requests
    tok, chat = os.getenv("TELEGRAM_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not tok or not chat:
        print("لا مفاتيح تيليجرام؛ لن يُرسل.")
        return False
    try:
        return requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                             data={"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20).ok
    except Exception as e:  # noqa: BLE001
        print("فشل الإرسال:", type(e).__name__)
        return False


def write_council(rows):
    cols = ["day", "slot", "mom", "f30", "edge7", "momentum", "n_votes", "state", "decision", "ret", "skill", "base"]
    with open(COUNCIL, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in cols})


def main(argv):
    now = ny_now()
    day = now.date().isoformat()
    force, no_send = "--force" in argv, "--no-send" in argv
    try:
        state = json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        state = {}
    if not force and (now.weekday() >= 5 or (now.hour, now.minute) < (16, 10) or state.get("last_sent") == day):
        print(f"خارج نافذة الإرسال أو أُرسل اليوم ({now:%Y-%m-%d %H:%M} نيويورك).")
        return 0
    shadows = load()
    cand = read_csv("candidates_shadow_log.csv")
    if not force and not any(r.get("day") == day for r in cand):
        print("لا صفوف للمرشحين اليوم (عطلة؟)؛ لا تقرير.")
        return 0
    res = evaluate(shadows)
    crows = council(cand, shadows["edge7"][0], shadows["mom_live"][0])
    write_council(crows)
    ops = opinions(cand, shadows)
    px = spy_minutes(sorted({d for lst in ops.values() for d, _, _ in lst}))
    drows = direction_rows(ops, px)
    write_direction(drows)
    dis = dissent(crows, px)
    watch = [(label, last_date(f)[0]) for f, label in WATCH_ONLY if os.path.exists(os.path.join(ROOT, f))]
    md, tg = build(res, crows, day, harmony_for(day, shadows), watch, dis)
    md2, tg2 = build_roles(direction_summary(drows), dis, day)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(md + "\n" + md2 + "\n")
    print(tg + "\n\n" + tg2)
    if not no_send:
        ok = send(tg)
        if ok:
            send(tg2)
        print("أُرسل:", ok)
        if ok:
            state["last_sent"] = day
            with open(STATE, "w", encoding="utf-8") as f:
                json.dump(state, f)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
