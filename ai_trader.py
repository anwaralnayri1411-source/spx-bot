#!/usr/bin/env python3
"""المتداول الذكي على الورق (H-AITRADER، مسجّل في CLAUDE.md 5/11/2026 بطلب المستخدم). استكشافي: ورق فقط، لا أوامر لأي وسيط.

كل 5 دقائق (09:45–16:10 نيويورك): متابعة المحفظة الورقية (وقف/هدف حدده المتداول، وإغلاق عقد اليوم 15:50).
كل 15 دقيقة (09:45–15:45): يجمع كل مدخلات البوت ويعطيها لـClaude ليقرر (شراء/بيع/انتظار) ويشرح كل شيء:
قراءة السوق، العوامل ووزنها، لماذا هذا العقد وكيف اختاره، والبدائل المرفوضة. كل قرار يُسجَّل (حتى الانتظار)،
والشراء والبيع يُرسلان لـTELEGRAM_CHAT_ID وحده، وتقرير نهاية الجلسة بعد 16:00.
قيود الكود: 2,000$، شراء بالطلب وبيع بالعرض، العقد ≤ 30% من المحفظة، ≤ 3 مراكز، لا مركزين متعاكسين على الأداة نفسها
(الشرط الشرعي؛ SPY وSPX أداتان مختلفتان)، لا شراء بعد 15:15. القائمة: عقود SPY وSPXW لأقرب انتهاءين ضمن ±3% (تعديل 5/11 بطلب المستخدم).
المتعلّم (H-AIMEMORY): محفظة ثانية بالمدخلات نفسها + ذاكرة دروس تُحدَّث بعد كل جلسة، ولا ترسل إلا تقرير نهاية الجلسة.
تشغيل: python ai_trader.py [--force] [--no-send] [--no-ai] [--seed-review]
"""
import copy
import csv
import json
import os
import sys
import urllib.parse
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
NY = ZoneInfo("America/New_York")
STATE = os.path.join(ROOT, "ai_trader_state.json")
LOG = os.path.join(ROOT, "ai_trader_log.csv")
JOURNAL = os.path.join(ROOT, "research", "ai_trader_journal.md")
USAGE = os.path.join(ROOT, "ai_trader_usage.csv")
MODEL = os.getenv("AI_TRADER_MODEL", "claude-haiku-5-5")
REVIEW_MODEL = os.getenv("AI_REVIEW_MODEL", "claude-sonnet-5-5")
MEMORY = os.path.join(ROOT, "ai_trader_b_memory.json")
MAX_LESSONS, MAX_NEW, RULE_DAYS = 15, 3, 3
DAILY_USD = 1.0
START_CASH = 2000.0
MAX_POS_PCT, MAX_OPEN = 0.30, 3
OPEN_T, DECIDE_FROM, LAST_DECIDE, LAST_BUY = dtime(9, 30), dtime(9, 45), dtime(15, 45), dtime(15, 15)
CLOSE_TODAY_AT, REPORT_AT, END_T = dtime(15, 50), dtime(16, 0), dtime(16, 10)
BAND = 0.03
UNDERS = (("SPY", "SPY", "SPY"), ("SPX", "SPX", "SPXW"))   # (الأداة، سلسلة Tastytrade، الجذر المطلوب)
LOG_COLS = ["time", "spot", "action", "side", "expiry", "strike", "qty", "price", "confidence", "market_read", "factors",
            "why_contract", "how_chosen", "rejected", "stop", "target", "invalidation", "wait_reason", "executed", "note"]

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["market_read", "factors", "action", "sell_ids", "sell_reason", "buy", "confidence", "wait_reason"],
    "properties": {
        "market_read": {"type": "string"},
        "factors": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "reading", "direction", "weight", "used"],
            "properties": {"name": {"type": "string"}, "reading": {"type": "string"},
                           "direction": {"type": "string", "enum": ["صعود", "هبوط", "محايد"]},
                           "weight": {"type": "string", "enum": ["قوي", "متوسط", "ضعيف"]}, "used": {"type": "boolean"}}}},
        "action": {"type": "string", "enum": ["شراء", "بيع", "انتظار"]},
        "sell_ids": {"type": "array", "items": {"type": "string"}},
        "sell_reason": {"type": "string"},
        "buy": {"type": "object", "additionalProperties": False,
                "required": ["symbol", "qty", "short_reason", "why_contract", "how_chosen", "rejected", "stop_price", "target_price", "invalidation"],
                "properties": {"symbol": {"type": "string"}, "qty": {"type": "integer"}, "short_reason": {"type": "string"},
                               "why_contract": {"type": "string"}, "how_chosen": {"type": "string"},
                               "rejected": {"type": "array", "items": {
                                   "type": "object", "additionalProperties": False, "required": ["contract", "why_not"],
                                   "properties": {"contract": {"type": "string"}, "why_not": {"type": "string"}}}},
                               "stop_price": {"type": "number"}, "target_price": {"type": "number"},
                               "invalidation": {"type": "string"}}},
        "confidence": {"type": "number"},
        "wait_reason": {"type": "string"},
    },
}

RULES = """أنت متداول خيارات محترف تدير محفظة **ورقية** (لا مال حقيقي) على عقود SPY وSPX (SPXW)، والمستخدم يدرس طريقة تفكيرك.
القواعد الثابتة (الكود يرفض ما يخالفها):
- المحفظة 2,000$؛ تشتري بسعر الطلب (ask) وتبيع بسعر العرض (bid)؛ العقد الواحد ≤ 30% من قيمة المحفظة؛ ≤ 3 مراكز مفتوحة.
- لا تجمع CALL وPUT مفتوحين على الأداة نفسها في وقت واحد (شرط شرعي): لتغيير الاتجاه عليها بِع المركز أولاً. SPY صندوق وSPX مؤشر، فهما أداتان مختلفتان.
- لك حرية اختيار الأداة (SPY أو SPX) والسترايك، قريباً من السعر أو بعيداً خارج المال، ضمن الميزانية.
- لا شراء بعد 15:15 نيويورك؛ عقد اليوم يُغلق آلياً 15:50؛ الوقف والهدف اللذان تحددهما بسعر العقد يُنفَّذان آلياً.
- اختر العقد من القائمة المعطاة فقط (حقل symbol كما هو). الانتظار قرار مشروع إن لم تتضح الصورة.
المطلوب: اقرأ كل المدخلات، وقيّم كل عامل (اتجاهه ووزنه وهل اعتمدت عليه)، ثم قرر. عند الشراء اشرح بالتفصيل لماذا هذا العقد
(السترايك والانتهاء والدلتا والسعر والفرق والسيولة)، وكيف اخترته، وما البدائل التي رفضتها ولماذا، والوقف والهدف وشرط إلغاء الفكرة.
وفي short_reason اكتب سبب الشراء في جملة واحدة قصيرة واضحة (أقل من 20 كلمة) دون مصطلحات إنجليزية.
اكتب كل النصوص بالعربية. عند عدم الشراء اجعل buy.symbol فارغاً وqty = 0. الثقة رقم من 0 إلى 1."""

SCHEMA_B = copy.deepcopy(SCHEMA)
SCHEMA_B["required"].append("lessons_used")
SCHEMA_B["properties"]["lessons_used"] = {"type": "array", "items": {"type": "string"}}
RULES_B = RULES + """
لديك دروس من جلساتك السابقة في قسم «دروسك». «قاعدة» تأكدت في أيام مختلفة، و«فرضية» لم تتأكد بعد. طبّق ما ينطبق شرطه الآن،
واذكر في lessons_used أرقام الدروس التي أثرت في قرارك (مثل L1)، واتركها فارغة إن لم يؤثر درس. الدروس لا تتجاوز القواعد الثابتة."""

LESSON = {"type": "object", "additionalProperties": False, "required": ["condition", "action", "evidence"],
          "properties": {"condition": {"type": "string"}, "action": {"type": "string"}, "evidence": {"type": "string"}}}
REVIEW_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["summary", "new", "confirmed", "contradicted"],
                 "properties": {"summary": {"type": "string"}, "new": {"type": "array", "items": LESSON},
                                "confirmed": {"type": "array", "items": {"type": "string"}},
                                "contradicted": {"type": "array", "items": {"type": "string"}}}}
REVIEW_RULES = """أنت مدرب تداول يراجع جلسة متداول خيارات ورقي ليتعلم منها. لا تقيّم الربح وحده: قرار جيد قد يخسر وقرار سيئ قد يربح.
ابحث عن الأنماط القابلة للتكرار: توقيت الدخول، المطاردة، الخروج المبكر أو المتأخر، الدخول قرب الجدران، اختيار العقد، حجم المركز، قرارات الانتظار.
اكتب في new حتى 3 دروس جديدة فقط إن كانت مفيدة فعلاً، ولا تكرر درساً موجوداً. كل درس:
- condition: شرط سوقي يمكن فحصه وقت القرار، قبل معرفة ما سيحدث بعده (مثل: السعر قرب جدار البيع بعد هبوط يتجاوز 2%).
- action: ماذا يفعل عند تحقق الشرط، ضمن ما يملكه المتداول فقط: الشراء أو الانتظار، واختيار العقد، والكمية، ووقف وهدف ثابتان، وبيع المركز كاملاً.
- evidence: الدليل من صفقات اليوم بالأرقام الموجودة فقط، دون حساب مجاميع جديدة.
ممنوع الدرس الاتجاهي العام مثل «اشترِ PUT» أو «السوق هابط»: يجب أن يصلح في يوم صاعد وهابط وعرضي.
ممنوع اقتراح تجاوز حدود المحفظة أو الشرط الشرعي.
في confirmed وcontradicted ضع أرقام الدروس القديمة التي انطبق شرطها اليوم فقط: confirmed إن صدقت، وcontradicted إن خالفتها النتيجة.
summary: جملتان بالعربية عن أهم ما حدث في الجلسة. اكتب كل شيء بالعربية الواضحة دون أسماء حقول."""


# ====================== الذاكرة (دوال صافية) ======================
def new_memory():
    return {"next": 1, "lessons": [], "reviews": []}


def lesson_status(x):
    seen, against = len(x["seen"]), len(x["against"])
    return "قاعدة" if seen >= RULE_DAYS and seen >= 2 * against else "فرضية"


def apply_review(mem, rv, day):
    """يطبّق مراجعة يوم مرة واحدة. يرجع الدروس الجديدة المقبولة."""
    if day in [r["day"] for r in mem["reviews"]]:
        return []
    by_id = {x["id"]: x for x in mem["lessons"]}
    for key, ids in (("seen", rv.get("confirmed")), ("against", rv.get("contradicted"))):
        for i in ids or []:
            if i in by_id and day not in by_id[i][key]:
                by_id[i][key].append(day)
    mem["lessons"] = [x for x in mem["lessons"] if not (len(x["against"]) >= 2 and len(x["against"]) >= len(x["seen"]))]
    added = []
    for n in (rv.get("new") or [])[:MAX_NEW]:
        cond, act = str(n.get("condition") or "").strip(), str(n.get("action") or "").strip()
        if len(cond.split()) < 3 or not act:
            continue
        x = {"id": f"L{mem['next']}", "condition": cond, "action": act, "evidence": str(n.get("evidence") or "").strip(),
             "born": day, "seen": [], "against": [], "used": 0}
        mem["next"] += 1
        mem["lessons"].append(x)
        added.append(x)
    mem["lessons"].sort(key=lambda x: (lesson_status(x) == "قاعدة", len(x["seen"]) - len(x["against"]), x["born"]), reverse=True)
    mem["lessons"] = mem["lessons"][:MAX_LESSONS]
    mem["reviews"].append({"day": day, "summary": rv.get("summary", "")})
    kept = {x["id"] for x in mem["lessons"]}
    return [x for x in added if x["id"] in kept]


def memory_text(mem):
    L = ["## دروسك من الجلسات السابقة"]
    for x in mem["lessons"]:
        L.append(f"- [{x['id']}] ({lesson_status(x)}) إذا {x['condition']} ← {x['action']}")
    if len(L) == 1:
        L.append("- لا دروس بعد.")
    return "\n".join(L)


def mark_used(mem, ids):
    for x in mem["lessons"]:
        if x["id"] in (ids or []):
            x["used"] = x.get("used", 0) + 1


def load_memory():
    try:
        return json.load(open(MEMORY, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return new_memory()


def save_memory(mem):
    json.dump(mem, open(MEMORY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


# ====================== المحفظة (دوال صافية) ======================
def new_state():
    return {"cash": START_CASH, "positions": [], "closed": [], "decisions": 0, "report_day": ""}


def equity(st, marks):
    return st["cash"] + sum(p["qty"] * 100 * marks.get(p["symbol"], {}).get("bid", p["entry"]) for p in st["positions"])


def validate_buy(st, menu, buy, now, marks):
    """(None، كمية) إن صح الشراء، أو (سبب الرفض، 0)."""
    sym, qty = (buy.get("symbol") or "").strip(), int(buy.get("qty") or 0)
    row = next((r for r in menu if r["symbol"] == sym), None)
    if row is None:
        return "العقد ليس في القائمة", 0
    if now.time() > LAST_BUY:
        return "لا شراء بعد 15:15", 0
    if len(st["positions"]) >= MAX_OPEN:
        return "بلغ حد 3 مراكز", 0
    if any(p["side"] != row["side"] and p.get("under", "SPY") == row.get("under", "SPY") for p in st["positions"]):
        return "مركز معاكس مفتوح (الشرط الشرعي)", 0
    ask = float(row["ask"] or 0)
    if ask <= 0:
        return "لا سعر طلب", 0
    cap = min(st["cash"], MAX_POS_PCT * equity(st, marks))
    qty = min(max(qty, 0), int(cap // (ask * 100)))
    if qty < 1:
        return "الميزانية لا تكفي عقداً واحداً", 0
    return None, qty


def open_position(st, row, qty, buy, now):
    cost = float(row["ask"]) * 100 * qty
    st["cash"] -= cost
    p = {"id": f"{now:%m%d-%H%M}-{row.get('under', 'SPY')}-{row['side']}-{row['strike']:g}", "symbol": row["symbol"],
         "under": row.get("under", "SPY"), "side": row["side"],
         "strike": float(row["strike"]), "expiry": row["expiry"], "qty": qty, "entry": float(row["ask"]),
         "time": now.isoformat(timespec="minutes"), "stop": float(buy.get("stop_price") or 0),
         "target": float(buy.get("target_price") or 0), "invalidation": buy.get("invalidation", "")}
    st["positions"].append(p)
    return p


def close_position(st, p, bid, now, why):
    proceeds = max(float(bid), 0.0) * 100 * p["qty"]
    st["cash"] += proceeds
    pnl = proceeds - p["entry"] * 100 * p["qty"]
    st["positions"] = [x for x in st["positions"] if x["id"] != p["id"]]
    rec = dict(p, exit=float(bid), exit_time=now.isoformat(timespec="minutes"), why=why, pnl=round(pnl, 2))
    st["closed"].append(rec)
    return rec


def auto_exits(st, marks, now):
    """الوقف والهدف اللذان حددهما المتداول، وإغلاق عقد اليوم 15:50. يرجع قائمة الصفقات المغلقة."""
    out = []
    for p in list(st["positions"]):
        q = marks.get(p["symbol"])
        if not q:
            continue
        bid = float(q.get("bid") or 0)
        if p["expiry"] <= now.date().isoformat() and now.time() >= CLOSE_TODAY_AT:
            out.append(close_position(st, p, bid, now, "إغلاق عقد اليوم 15:50"))
        elif p["stop"] > 0 and bid <= p["stop"]:
            out.append(close_position(st, p, bid, now, f"بلغ الوقف الذي حدده ({p['stop']:.2f})"))
        elif p["target"] > 0 and bid >= p["target"]:
            out.append(close_position(st, p, bid, now, f"بلغ الهدف الذي حدده ({p['target']:.2f})"))
    return out


# ====================== الرسائل والسجل ======================
def fmt_factors(fs, used_only=True):
    icon = {"صعود": "🟢", "هبوط": "🔴", "محايد": "⚪"}
    L = []
    for f in fs:
        if used_only and not f.get("used"):
            continue
        L.append(f"{icon.get(f.get('direction'), '⚪')} **{f.get('name', '')}** ({f.get('weight', '')}): {f.get('reading', '')}")
    return L


def spot_text(spot):
    """spot: رقم (SPY) أو {أداة: سعر}."""
    if isinstance(spot, dict):
        return " · ".join(f"{k} {v:,.2f}" for k, v in spot.items() if v)
    return f"SPY {spot:,.2f}"


def spot_num(spot):
    return float(spot.get("SPY") or 0) if isinstance(spot, dict) else float(spot)


def short(text, n=150):
    """النص كما هو إن كان قصيراً، وإلا أول جملة بحد أقصى n حرفاً."""
    t = str(text or "").strip()
    if len(t) <= n:
        return t
    for sep in (". ", "، و", "\n"):
        if sep in t[:n]:
            t = t[:t.index(sep)]
            break
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"


def expiry_word(exp, t):
    return "عقد اليوم" if str(exp) <= str(t)[:10] else "عقد الغد"


def buy_message(d, p, row, spot):
    b = d["buy"]
    return "\n".join([
        f"🤖 **المتداول الذكي (ورقي)** · شراء {'🟢' if p['side'] == 'CALL' else '🔴'}",
        f"**{p.get('under', 'SPY')} {p['strike']:g} {p['side']}** ({expiry_word(p['expiry'], p['time'])}) ×{p['qty']} "
        f"بسعر {p['entry']:.2f} = {p['entry'] * 100 * p['qty']:,.0f}$",
        f"💡 {short(b.get('short_reason') or d['market_read'])}",
        f"🛑 وقف {p['stop']:.2f} · 🎯 هدف {p['target']:.2f}"])


def sell_message(rec, reason):
    icon = "✅" if rec["pnl"] > 0 else "🛑"
    return (f"🤖 {icon} **باع {rec.get('under', 'SPY')} {rec['strike']:g} {rec['side']}** ×{rec['qty']}: "
            f"{rec['entry']:.2f} ← {rec['exit']:.2f} = **{rec['pnl']:+,.0f}$**\n{short(reason)}")


def journal(now, spot, d, executed, note):
    os.makedirs(os.path.dirname(JOURNAL), exist_ok=True)
    L = [f"## {now:%Y-%m-%d %H:%M} نيويورك | {spot_text(spot)} | القرار: {d['action']} | الثقة {d['confidence']:.0%}",
         f"- قراءة السوق: {d['market_read']}", "- العوامل:"]
    L += [f"  - {'✔' if f.get('used') else '·'} {f.get('name')} ({f.get('direction')}، {f.get('weight')}): {f.get('reading')}"
          for f in d["factors"]]
    if d["action"] == "شراء":
        b = d["buy"]
        L += [f"- العقد: {b['symbol']} ×{b['qty']} | وقف {b['stop_price']} · هدف {b['target_price']}",
              f"- لماذا: {b['why_contract']}", f"- كيف: {b['how_chosen']}"]
        L += [f"  - رفض {r['contract']}: {r['why_not']}" for r in b.get("rejected") or []]
        L.append(f"- تُلغى إن: {b['invalidation']}")
    elif d["action"] == "بيع":
        L.append(f"- بيع {', '.join(d['sell_ids'])}: {d['sell_reason']}")
    else:
        L.append(f"- سبب الانتظار: {d['wait_reason']}")
    L.append(f"- التنفيذ: {executed} {note}".rstrip())
    with open(JOURNAL, "a", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n\n")


def log_row(now, spot, d, row, qty, executed, note):
    b = d.get("buy") or {}
    r = {"time": now.isoformat(timespec="minutes"), "spot": round(spot_num(spot), 2), "action": d["action"],
         "side": row.get("side", "") if row else "", "expiry": row.get("expiry", "") if row else "",
         "strike": row.get("strike", "") if row else "", "qty": qty, "price": row.get("ask", "") if row else "",
         "confidence": d.get("confidence"), "market_read": d.get("market_read"), "factors": json.dumps(d.get("factors"), ensure_ascii=False),
         "why_contract": b.get("why_contract", ""), "how_chosen": b.get("how_chosen", ""),
         "rejected": json.dumps(b.get("rejected") or [], ensure_ascii=False), "stop": b.get("stop_price", ""),
         "target": b.get("target_price", ""), "invalidation": b.get("invalidation", ""), "wait_reason": d.get("wait_reason", ""),
         "executed": executed, "note": note}
    new = not os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        if new:
            w.writeheader()
        w.writerow(r)


def day_report(st, now, bot_recs):
    day = now.date().isoformat()
    cl = sorted((c for c in st["closed"] if c["exit_time"][:10] == day), key=lambda c: c["time"])
    wins = sum(c["pnl"] > 0 for c in cl)
    L = [f"🤖 **المتداول الذكي · نهاية الجلسة** {day} (ورقي)", "",
         f"**صافي اليوم: {sum(c['pnl'] for c in cl):+,.0f}$** | {len(cl)} صفقات: {wins} رابحة · {len(cl) - wins} خاسرة",
         f"المحفظة {st['cash']:,.0f}$ نقداً | {st.get('decisions_today', 0)} قراراً | مفتوح: {len(st['positions'])}"]
    if cl:
        L += ["", "**الصفقات (نيويورك):**"]
    for c in cl:
        mins = int((datetime.fromisoformat(c["exit_time"]) - datetime.fromisoformat(c["time"])).total_seconds() // 60)
        L.append(f"{'✅' if c['pnl'] > 0 else '🛑'} {c['time'][11:16]} {c.get('under', 'SPY')} {c['strike']:g} {c['side']} ×{c['qty']}: "
                 f"{c['entry']:.2f} ← {c['exit']:.2f} = **{c['pnl']:+,.0f}$** ({mins}د)"
                 + (f" ثم بلغ {c['after_hi']:.2f}" if c.get("after_hi") else ""))
    for p in st["positions"]:
        L.append(f"↪️ مفتوح: {p.get('under', 'SPY')} {p['strike']:g} {p['side']} ({p['expiry']}) ×{p['qty']} بسعر {p['entry']:.2f}")
    if bot_recs:
        L += ["", f"🆚 توصيات البوت اليوم: {len(bot_recs)}"]
    L += ["", "⚠️ محفظة ورقية للدراسة. تفكيره الكامل في السجل research/ai_trader_journal.md"]
    return "\n".join(L)


# ====================== المدخلات (شبكة) ======================
def tasty_menu(now):
    """عقود SPY وSPXW لأقرب انتهاءين ضمن ±BAND من سعر كل أداة:
    ({أداة: سعر}، [{symbol, under, side, strike, expiry, bid, ask, delta, iv, volume, oi}])."""
    import live_quotes as lq
    tok = lq._tasty_token()
    spots, menu = {}, []
    for under, chain, root in UNDERS:
        try:
            S, _ = lq.spot(under)
            if not S:
                continue
            ch = (lq._tasty_req(f"/option-chains/{chain}/nested", token=tok).get("data") or {}).get("items") or []
            ch = [r for r in ch if r.get("root-symbol", root) == root]
            exps = sorted({e["expiration-date"] for r in ch for e in r.get("expirations") or []
                           if e["expiration-date"] >= now.date().isoformat()})[:2]
            meta = {}
            for r in ch:
                for e in r.get("expirations") or []:
                    if e["expiration-date"] in exps:
                        for k in e.get("strikes") or []:
                            K = float(k["strike-price"])
                            if abs(K / S - 1) <= BAND:
                                meta[k["call"]] = ("CALL", K, e["expiration-date"], under)
                                meta[k["put"]] = ("PUT", K, e["expiration-date"], under)
            spots[under] = S
            menu += quotes(list(meta), meta)
        except Exception as e:  # noqa: BLE001
            print(f"قائمة {under}: تعذرت ({type(e).__name__})")
    return spots, menu


def quotes(syms, meta=None):
    import live_quotes as lq
    tok = lq._tasty_token()
    out = []
    for i in range(0, len(syms), 50):
        q = urllib.parse.urlencode([("equity-option", s) for s in syms[i:i + 50]])
        for it in (lq._tasty_req(f"/market-data/by-type?{q}", token=tok).get("data") or {}).get("items") or []:
            s = it.get("symbol")
            f = lambda k: round(float(it.get(k) or 0), 4)
            row = {"symbol": s, "bid": f("bid"), "ask": f("ask"), "delta": f("delta"), "iv": f("volatility"),
                   "volume": f("volume"), "oi": f("open-interest")}
            if meta and s in meta:
                row.update(side=meta[s][0], strike=meta[s][1], expiry=meta[s][2], under=meta[s][3] if len(meta[s]) > 3 else "SPY")
            out.append(row)
    return out


def gather(now, max_ask=None):
    """نص المدخلات كاملاً لـClaude + قائمة العقود + السعر."""
    import options_bot as ob
    parts = []
    try:
        a = ob.analyze(False)
        c = a["c"]
        keep = {k: c.get(k) for k in ("S", "sma20", "sma50", "ema9", "ema21", "r30", "gap", "above_vwap", "vwap", "prev_close",
                                        "orb", "pd_hl", "max_pain", "pc", "es", "vix_day", "age")}
        keep["map"] = {k: v for k, v in (c.get("map") or {}).items() if k in ("gex", "call_wall", "put_wall", "flip", "walls")}
        keep["leaders"] = {k: v for k, v in (c.get("leaders") or {}).items() if k in ("day", "m30", "up", "n")}
        parts.append("## تحليل البوت (SPX)\n" + json.dumps(keep, ensure_ascii=False, default=str))
        parts.append("## نقاط الاتجاه (العامل، صعود، هبوط، القراءة)\n" + "\n".join(f"- {r[0]}: +{r[1]:g}/-{r[2]:g} {r[3]}" for r in a["rows"]))
        parts.append(f"## خلاصة البوت\nصعود {a['bull']:.1f} مقابل هبوط {a['bear']:.1f}؛ الميل {a['side']}؛ القوة {a['strength']}; "
                     f"أسباب عدم التوصية: {'; '.join(a.get('reasons') or []) or 'لا'}؛ أحداث اليوم: {a.get('events')}")
        try:
            plan = ob.build_plan(c, a["bull"], a["bear"])
            parts.append("## خطة المستويات\n" + json.dumps({k: plan.get(k) for k in ("levels", "scenarios", "has_orb")},
                                                         ensure_ascii=False, default=str)[:3000])
        except Exception as e:  # noqa: BLE001
            parts.append(f"## خطة المستويات: تعذرت ({type(e).__name__})")
    except Exception as e:  # noqa: BLE001
        parts.append(f"## تحليل البوت: تعذر ({type(e).__name__}: {e})")
    try:
        fl = pd.read_csv(os.path.join(ROOT, "spx_flow_log.csv"), dtype={"time": str})
        fl = fl[fl["date"] == now.date().isoformat()].tail(6)
        parts.append("## مراقب السيولة SPX (آخر 6 لقطات: الغاما، الجدران، نقطة الانقلاب، علاوة الكول/البوت، صافي الدلتا)\n"
                     + fl[["time", "spot", "net_gex_m", "flip", "call_wall", "put_wall", "call_prem", "put_prem", "net_delta"]].to_csv(index=False))
    except Exception as e:  # noqa: BLE001
        parts.append(f"## مراقب السيولة: لا بيانات ({type(e).__name__})")
    try:
        import news_watch as nw
        parts.append("## أخبار الليل والصباح\n" + "\n".join(nw.night_context(now.date().isoformat(), None, "CALL", limit=4)))
        import heavyweights as hv
        hb = hv.block_text(hv.current(now))
        if hb:
            parts.append(hb)
    except Exception as e:  # noqa: BLE001
        parts.append(f"## الأخبار: لا ({type(e).__name__})")
    recs = []
    try:
        z = pd.read_csv(os.path.join(ROOT, "zero_dte_log.csv"), dtype=str)
        z = z[z["date"] == now.date().isoformat()]
        recs = [f"{r.time} {r.symbol} {r.side} {r.strike} ({r.expiry}) بـ{r.price}" for r in z.itertuples()]
        parts.append("## توصيات البوت اليوم\n" + ("\n".join(recs) or "لا توصيات بعد"))
    except Exception:  # noqa: BLE001
        pass
    S, menu = tasty_menu(now)
    menu = [m for m in menu if float(m.get("ask") or 0) > 0 and (max_ask is None or float(m["ask"]) <= max_ask)]
    parts.append(f"## الأسعار الآن: {spot_text(S)} (عقد SPX ≈ 10 أضعاف SPY)\n"
                 "## قائمة العقود (symbol, under, side, strike, expiry, bid, ask, delta, iv, volume, oi)\n"
                 + "\n".join(f"{m['symbol']}|{m.get('under', 'SPY')}|{m['side']}|{m['strike']:g}|{m['expiry']}|{m['bid']}|{m['ask']}|"
                             f"{m['delta']}|{m['iv']}|{m['volume']:.0f}|{m['oi']:.0f}"
                             for m in sorted(menu, key=lambda m: (m.get("under", ""), m["expiry"], m["side"], m["strike"]))))
    return "\n\n".join(parts), menu, S, recs


def portfolio_text(st, marks, now):
    L = [f"## المحفظة الورقية الآن {now:%H:%M} نيويورك: النقد {st['cash']:,.2f}$ | القيمة {equity(st, marks):,.2f}$"]
    for p in st["positions"]:
        b = marks.get(p["symbol"], {}).get("bid", p["entry"])
        L.append(f"- [{p['id']}] {p.get('under', 'SPY')} {p['side']} {p['strike']:g} ({p['expiry']}) ×{p['qty']} دخول {p['entry']:.2f} الآن {b:.2f} "
                 f"({(b / p['entry'] - 1) * 100:+.0f}%) | وقفك {p['stop']} هدفك {p['target']} | تُلغى إن: {p['invalidation']}")
    day = now.date().isoformat()
    for c in st["closed"]:
        if c["exit_time"][:10] == day:
            L.append(f"- مغلقة اليوم: {c['side']} {c['strike']:g} = {c['pnl']:+.0f}$ ({c['why']})")
    if len(L) == 1:
        L.append("- لا مراكز.")
    return "\n".join(L)


def ask_claude(prompt, now, schema=None, system=None, model=None, kind="decide"):
    schema, system, model = schema or SCHEMA, system or RULES, model or MODEL
    sys.path.insert(0, ROOT)
    import analyst as an
    try:
        df = pd.read_csv(USAGE, dtype=str)
        spent = float(pd.to_numeric(df.loc[df["time"].str[:10] == now.date().isoformat(), "usd"], errors="coerce").sum())
    except Exception:  # noqa: BLE001
        spent = 0.0
    if spent >= DAILY_USD:
        print("بلغ السقف اليومي للتكلفة.")
        return None
    key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        print("لا ANTHROPIC_API_KEY.")
        return None
    import anthropic
    client = anthropic.Anthropic(api_key=key, timeout=180.0)
    try:
        r = client.messages.create(model=model, max_tokens=8000, system=system, messages=[{"role": "user", "content": prompt}],
                                   output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}})
    except Exception as e:  # noqa: BLE001
        print("فشل الطلب:", type(e).__name__, e)
        an.api_failed(e, kind)
        return None
    u = {"input": getattr(r.usage, "input_tokens", 0) or 0, "output": getattr(r.usage, "output_tokens", 0) or 0}
    new = not os.path.exists(USAGE)
    with open(USAGE, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=an.USAGE_COLS)
        if new:
            w.writeheader()
        w.writerow({"time": now.isoformat(timespec="seconds"), "kind": kind, "model": model, **u,
                    "usd": round(an.cost(u, model), 5), "status": r.stop_reason})
    if r.stop_reason != "end_turn":
        print("توقف غير مكتمل:", r.stop_reason)
        return None
    try:
        return json.loads(next(b.text for b in r.content if getattr(b, "type", "") == "text"))
    except Exception as e:  # noqa: BLE001
        print("رد غير مفهوم:", type(e).__name__)
        return None


# ====================== التشغيل ======================
def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return new_state()


def send(text, no_send):
    print(text)
    if not no_send:
        import analyst as an
        an.send(text, "ai_trader")


def execute(st, d, menu, marks, now, spot, no_send):
    """ينفّذ قرار Claude ضمن القيود. يرجع (الصف، الكمية، منفَّذ؟، ملاحظة)."""
    row, qty, note = None, 0, ""
    if d["action"] == "بيع":
        done = []
        for sid in d.get("sell_ids") or []:
            p = next((x for x in st["positions"] if x["id"] == sid), None)
            if p and p["symbol"] in marks:
                rec = close_position(st, p, marks[p["symbol"]].get("bid", 0), now, d.get("sell_reason", ""))
                send(sell_message(rec, d.get("sell_reason", "")), no_send)
                done.append(sid)
        return None, 0, bool(done), ("باع " + ", ".join(done)) if done else "لا مركز مطابق للبيع"
    if d["action"] == "شراء":
        row = next((r for r in menu if r["symbol"] == (d["buy"].get("symbol") or "").strip()), None)
        why, qty = validate_buy(st, menu, d["buy"], now, marks)
        if why:
            send(f"🤖 المتداول الذكي أراد الشراء ({d['buy'].get('symbol')}) لكن الكود رفض: {why}", no_send)
            return row, 0, False, f"رُفض: {why}"
        p = open_position(st, row, qty, d["buy"], now)
        send(buy_message(d, p, row, spot), no_send)
        return row, qty, True, f"اشترى {p['id']}"
    return None, 0, False, note


def after_stats(cl):
    """ماذا فعل كل عقد بعد خروجنا: {id: {hi, lo, last}}. يفشل بصمت إن تعذرت الشموع."""
    out = {}
    try:
        import rule_c_shadow as rc
        for c in cl:
            occ = c["symbol"].replace(" ", "")
            t = datetime.fromisoformat(c["exit_time"])
            b = rc.option_bars([occ], t.date()).get(occ)
            b = b[b.index > t] if b is not None else None
            if b is not None and len(b):
                out[c["id"]] = {"hi": float(b["h"].max()), "lo": float(b["l"].min()), "last": float(b["c"].iloc[-1])}
    except Exception as e:  # noqa: BLE001
        print("شموع ما بعد الخروج:", type(e).__name__, e)
    return out


def record_after(st, day):
    """يحفظ في كل صفقة مغلقة اليوم أعلى وأدنى وآخر سعر بعد الخروج. قياس فقط."""
    cl = [c for c in st["closed"] if c["exit_time"][:10] == day and "after_hi" not in c]
    stats = after_stats(cl) if cl else {}
    for c in cl:
        x = stats.get(c["id"])
        if x:
            c.update(after_hi=x["hi"], after_lo=x["lo"], after_last=x["last"])


def after_exit(cl):
    return {c["id"]: f"بعد الخروج: أعلى {c['after_hi']:.2f} وأدنى {c['after_lo']:.2f} وآخر سعر {c['after_last']:.2f}" for c in cl if "after_hi" in c}


def review_prompt(st, day, mem, log_path, post):
    today = [c for c in st["closed"] if c["exit_time"][:10] == day]
    L = [f"## جلسة {day}: {len(today)} صفقات، {sum(c['pnl'] > 0 for c in today)} رابحة بمجموع {sum(c['pnl'] for c in today if c['pnl'] > 0):+,.0f}$، "
         f"و{sum(c['pnl'] <= 0 for c in today)} خاسرة بمجموع {sum(c['pnl'] for c in today if c['pnl'] <= 0):+,.0f}$", "## الصفقات المغلقة"]
    for c in sorted((c for c in st["closed"] if c["exit_time"][:10] == day), key=lambda c: c["time"]):
        L.append(f"- [{c['id']}] {c.get('under', 'SPY')} {c['strike']:g} {c['side']} ينتهي {c['expiry']} ×{c['qty']} | دخول {c['time'][11:16]} بـ{c['entry']:.2f} "
                 f"| خروج {c['exit_time'][11:16]} بـ{c['exit']:.2f} | وقف {c['stop']} هدف {c['target']} | {c['pnl']:+.0f}$ | {c['why'][:160]} | {post.get(c['id'], '')}")
    L.append("## القرارات بالترتيب (الوقت، سعر SPY، القرار، الثقة، القراءة، سبب الانتظار)")
    try:
        d = pd.read_csv(log_path, dtype=str, keep_default_na=False)
        for r in d[d["time"].str[:10] == day].itertuples():
            L.append(f"- {r.time[11:16]} | {r.spot} | {r.action} | {r.confidence} | {r.market_read[:260]} | {r.wait_reason[:220]}")
    except Exception as e:  # noqa: BLE001
        L.append(f"- لا سجل قرارات ({type(e).__name__})")
    return "\n".join(L) + "\n\n" + memory_text(mem)


def run_review(st, now, log_path, mem=None):
    """يراجع الجلسة ويحدّث الذاكرة. يرجع (الملخص، الدروس الجديدة) أو (""، [])."""
    day = now.date().isoformat()
    mem = mem or load_memory()
    if day in [r["day"] for r in mem["reviews"]]:
        return "", []
    cl = [c for c in st["closed"] if c["exit_time"][:10] == day]
    rv = ask_claude(review_prompt(st, day, mem, log_path, after_exit(cl)), now, REVIEW_SCHEMA, REVIEW_RULES, REVIEW_MODEL, "review")
    if not rv:
        return "", []
    added = apply_review(mem, rv, day)
    save_memory(mem)
    return rv.get("summary", ""), added


def learner_report(st, now, recs, summary, added, mem):
    txt = day_report(st, now, recs).replace("🤖 **المتداول الذكي · نهاية الجلسة**", "🧠 **المتعلّم · نهاية الجلسة**", 1)
    L = [txt, ""]
    if summary:
        L.append(f"📝 {summary}")
    if added:
        L += ["", "**دروس جديدة:**"] + [f"• إذا {x['condition']} ← {x['action']}" for x in added]
    rules = sum(lesson_status(x) == "قاعدة" for x in mem["lessons"])
    L += ["", f"الذاكرة: {len(mem['lessons'])} درساً، منها {rules} قاعدة."]
    return "\n".join(L)


def b_path(path):
    d, f = os.path.split(path)
    return os.path.join(d, f.replace("ai_trader_", "ai_trader_b_") if "ai_trader_" in f else "b_" + f)


def today_recs(day):
    try:
        z = pd.read_csv(os.path.join(ROOT, "zero_dte_log.csv"), dtype=str)
        return [f"{r.time} {r.symbol} {r.side} {r.strike}" for r in z[z["date"] == day].itertuples()]
    except Exception:  # noqa: BLE001
        return []


def run_book(now, force, no_send, no_ai, cache, learner=False):
    """دورة محفظة واحدة. cache: مدخلات مشتركة بين المحفظتين في التشغيل نفسه."""
    st = load_state()
    day = now.date().isoformat()
    quiet = no_send or learner
    if st.get("day") != day:
        st.update(day=day, decisions_today=0)
    marks = {q["symbol"]: q for q in quotes([p["symbol"] for p in st["positions"]])} if st["positions"] else {}
    for rec in auto_exits(st, marks, now):
        send(sell_message(rec, rec["why"]), quiet)
    decide_slot = now.minute % 15 < 5 and DECIDE_FROM <= now.time() <= LAST_DECIDE
    if (decide_slot or force) and not no_ai:
        if "g" not in cache:
            cache["g"] = gather(now)
        text, menu, spot, recs = cache["g"]
        marks.update({m["symbol"]: m for m in menu})
        mem = load_memory() if learner else None
        head = text + ("\n\n" + memory_text(mem) if learner else "")
        prompt = head + "\n\n" + portfolio_text(st, marks, now) + f"\n\nالوقت الآن {now:%H:%M} نيويورك. قرّر."
        d = ask_claude(prompt, now, SCHEMA_B, RULES_B) if learner else ask_claude(prompt, now)
        if d:
            st["decisions"] = st.get("decisions", 0) + 1
            st["decisions_today"] = st.get("decisions_today", 0) + 1
            row, qty, ok, note = execute(st, d, menu, marks, now, spot, quiet)
            if learner and d.get("lessons_used"):
                mark_used(mem, d["lessons_used"])
                save_memory(mem)
                note = f"{note} | دروس: {', '.join(d['lessons_used'])}".strip(" |")
            log_row(now, spot, d, row, qty, ok, note)
            journal(now, spot, d, "نُفّذ" if ok else "لم يُنفَّذ", note)
    if now.time() >= REPORT_AT and st.get("report_day") != day:
        recs = today_recs(day)
        record_after(st, day)
        if learner:
            mem = load_memory()
            summary, added = run_review(st, now, LOG, mem)
            send(learner_report(st, now, recs, summary, added, mem), no_send)
        else:
            send(day_report(st, now, recs), no_send)
        st["report_day"] = day
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def start_learner(a_state):
    """أول تشغيل للمتعلّم: يبدأ بنقد A حتى تكون المقارنة من نقطة واحدة."""
    if not os.path.exists(b_path(a_state)):
        try:
            cash = json.load(open(a_state, encoding="utf-8"))["cash"]
        except Exception:  # noqa: BLE001
            cash = START_CASH
        st = dict(new_state(), cash=cash, start_cash=cash)
        json.dump(st, open(b_path(a_state), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def main(argv):
    global STATE, LOG, JOURNAL, USAGE
    now = datetime.now(NY)
    force, no_send, no_ai = "--force" in argv, "--no-send" in argv, "--no-ai" in argv
    if "--seed-review" in argv:      # بذرة الذاكرة من آخر جلسة A (مرة واحدة يدوياً)
        day = max(c["exit_time"][:10] for c in load_state()["closed"])
        t = datetime.fromisoformat(day + "T16:20:00").replace(tzinfo=NY)
        print(run_review(load_state(), t, LOG))
        return
    if not force and (now.weekday() >= 5 or not DECIDE_FROM <= now.time() <= END_T):
        print("خارج الجلسة.")
        return
    cache = {}
    start_learner(STATE)
    run_book(now, force, no_send, no_ai, cache)
    base = STATE, LOG, JOURNAL, USAGE
    STATE, LOG, JOURNAL, USAGE = (b_path(x) for x in base)
    try:
        run_book(now, force, no_send, no_ai, cache, learner=True)
    finally:
        STATE, LOG, JOURNAL, USAGE = base


if __name__ == "__main__":
    main(sys.argv[1:])
