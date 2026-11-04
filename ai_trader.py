#!/usr/bin/env python3
"""المتداول الذكي على الورق (H-AITRADER، مسجّل في CLAUDE.md 5/11/2026 بطلب المستخدم). استكشافي: ورق فقط، لا أوامر لأي وسيط.

كل 5 دقائق (09:45–16:10 نيويورك): متابعة المحفظة الورقية (وقف/هدف حدده المتداول، وإغلاق عقد اليوم 15:50).
كل 15 دقيقة (09:45–15:45): يجمع كل مدخلات البوت ويعطيها لـClaude ليقرر (شراء/بيع/انتظار) ويشرح كل شيء:
قراءة السوق، العوامل ووزنها، لماذا هذا العقد وكيف اختاره، والبدائل المرفوضة. كل قرار يُسجَّل (حتى الانتظار)،
والشراء والبيع يُرسلان لـTELEGRAM_CHAT_ID وحده، وتقرير نهاية الجلسة بعد 16:00.
قيود الكود: 2,000$، شراء بالطلب وبيع بالعرض، العقد ≤ 30% من المحفظة، ≤ 3 مراكز، لا مركزين متعاكسين على SPY
(الشرط الشرعي)، لا شراء بعد 15:15.
تشغيل: python ai_trader.py [--force] [--no-send] [--no-ai]
"""
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
DAILY_USD = 1.0
START_CASH = 2000.0
MAX_POS_PCT, MAX_OPEN = 0.30, 3
OPEN_T, DECIDE_FROM, LAST_DECIDE, LAST_BUY = dtime(9, 30), dtime(9, 45), dtime(15, 45), dtime(15, 15)
CLOSE_TODAY_AT, REPORT_AT, END_T = dtime(15, 50), dtime(16, 0), dtime(16, 10)
BAND = 0.012
UNDER = "SPY"
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
                "required": ["symbol", "qty", "why_contract", "how_chosen", "rejected", "stop_price", "target_price", "invalidation"],
                "properties": {"symbol": {"type": "string"}, "qty": {"type": "integer"},
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

RULES = """أنت متداول خيارات محترف تدير محفظة **ورقية** (لا مال حقيقي) على عقود SPY، والمستخدم يدرس طريقة تفكيرك.
القواعد الثابتة (الكود يرفض ما يخالفها):
- المحفظة 2,000$؛ تشتري بسعر الطلب (ask) وتبيع بسعر العرض (bid)؛ العقد الواحد ≤ 30% من قيمة المحفظة؛ ≤ 3 مراكز مفتوحة.
- لا تجمع CALL وPUT مفتوحين على SPY في وقت واحد (شرط شرعي): لتغيير الاتجاه بِع المركز أولاً.
- لا شراء بعد 15:15 نيويورك؛ عقد اليوم يُغلق آلياً 15:50؛ الوقف والهدف اللذان تحددهما بسعر العقد يُنفَّذان آلياً.
- اختر العقد من القائمة المعطاة فقط (حقل symbol كما هو). الانتظار قرار مشروع إن لم تتضح الصورة.
المطلوب: اقرأ كل المدخلات، وقيّم كل عامل (اتجاهه ووزنه وهل اعتمدت عليه)، ثم قرر. عند الشراء اشرح بالتفصيل لماذا هذا العقد
(السترايك والانتهاء والدلتا والسعر والفرق والسيولة)، وكيف اخترته، وما البدائل التي رفضتها ولماذا، والوقف والهدف وشرط إلغاء الفكرة.
اكتب كل النصوص بالعربية. عند عدم الشراء اجعل buy.symbol فارغاً وqty = 0. الثقة رقم من 0 إلى 1."""


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
    if any(p["side"] != row["side"] for p in st["positions"]):
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
    p = {"id": f"{now:%m%d-%H%M}-{row['side']}-{row['strike']:g}", "symbol": row["symbol"], "side": row["side"],
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


def buy_message(d, p, row, spot):
    b = d["buy"]
    L = [f"🤖 **المتداول الذكي (ورقي، تجريبي)** | شراء {p['side']} 🟢" if p["side"] == "CALL" else
         f"🤖 **المتداول الذكي (ورقي، تجريبي)** | شراء {p['side']} 🔴",
         f"**{UNDER} {p['strike']:g} {p['side']}** ينتهي {p['expiry']} ×{p['qty']} بسعر {p['entry']:.2f} "
         f"(${p['entry'] * 100 * p['qty']:,.0f}) | SPY {spot:,.2f} | دلتا {float(row.get('delta') or 0):.2f} | الثقة {d['confidence']:.0%}",
         "", f"🧠 **قراءة السوق:** {d['market_read']}", "", "📋 **العوامل التي اعتمد عليها:**", *fmt_factors(d["factors"]),
         "", f"🎯 **لماذا هذا العقد:** {b['why_contract']}", f"🔍 **كيف اختاره:** {b['how_chosen']}"]
    if b.get("rejected"):
        L += ["", "❌ **بدائل رفضها:**"] + [f"• {r['contract']}: {r['why_not']}" for r in b["rejected"]]
    L += ["", f"🛑 وقف {p['stop']:.2f} · 🎯 هدف {p['target']:.2f} · ⛔ تُلغى الفكرة إن: {p['invalidation']}",
          "⚠️ محفظة ورقية للدراسة (H-AITRADER)، لا مال حقيقي."]
    return "\n".join(L)


def sell_message(rec, reason):
    icon = "✅" if rec["pnl"] > 0 else "🛑"
    return (f"🤖 {icon} **المتداول الذكي باع** {UNDER} {rec['strike']:g} {rec['side']} ×{rec['qty']}: "
            f"{rec['entry']:.2f} ← {rec['exit']:.2f} = **{rec['pnl']:+,.0f}$**\nالسبب: {reason}")


def journal(now, spot, d, executed, note):
    os.makedirs(os.path.dirname(JOURNAL), exist_ok=True)
    L = [f"## {now:%Y-%m-%d %H:%M} نيويورك | SPY {spot:,.2f} | القرار: {d['action']} | الثقة {d['confidence']:.0%}",
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
    r = {"time": now.isoformat(timespec="minutes"), "spot": round(spot, 2), "action": d["action"],
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
    cl = [c for c in st["closed"] if c["exit_time"][:10] == day]
    pnl = sum(c["pnl"] for c in cl)
    L = [f"🤖 **المتداول الذكي — نهاية الجلسة** {day} (ورقي)", "",
         f"قرارات اليوم: {st.get('decisions_today', 0)} | صفقات مغلقة: {len(cl)} | الصافي المحقق **{pnl:+,.0f}$**",
         f"النقد {st['cash']:,.0f}$ | مراكز مفتوحة: {len(st['positions'])}"]
    for c in cl:
        L.append(f"• {c['side']} {c['strike']:g} ({c['expiry']}) ×{c['qty']}: {c['entry']:.2f} ← {c['exit']:.2f} = {c['pnl']:+,.0f}$ | {c['why']}")
    for p in st["positions"]:
        L.append(f"↪️ مفتوح: {p['side']} {p['strike']:g} ({p['expiry']}) ×{p['qty']} بسعر {p['entry']:.2f}")
    if bot_recs:
        L += ["", "🆚 **توصيات البوت اليوم:** " + "، ".join(bot_recs)]
    L += ["", "السجل الكامل لكل قرار وسببه: research/ai_trader_journal.md", "⚠️ للدراسة فقط (H-AITRADER)."]
    return "\n".join(L)


# ====================== المدخلات (شبكة) ======================
def tasty_menu(now):
    """عقود SPY لأقرب انتهاءين (اليوم/الغد) ضمن ±BAND من السعر: [{symbol, side, strike, expiry, bid, ask, delta, iv, volume, oi}]."""
    import live_quotes as lq
    S, _ = lq.spot(UNDER)
    tok = lq._tasty_token()
    ch = (lq._tasty_req(f"/option-chains/{UNDER}/nested", token=tok).get("data") or {}).get("items") or []
    exps = sorted({e["expiration-date"] for r in ch for e in r.get("expirations") or [] if e["expiration-date"] >= now.date().isoformat()})[:2]
    meta = {}
    for r in ch:
        for e in r.get("expirations") or []:
            if e["expiration-date"] in exps:
                for k in e.get("strikes") or []:
                    K = float(k["strike-price"])
                    if abs(K / S - 1) <= BAND:
                        meta[k["call"]] = ("CALL", K, e["expiration-date"])
                        meta[k["put"]] = ("PUT", K, e["expiration-date"])
    return S, quotes(list(meta), meta)


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
                row.update(side=meta[s][0], strike=meta[s][1], expiry=meta[s][2])
            out.append(row)
    return out


def gather(now):
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
    parts.append(f"## سعر SPY الآن {S:.2f}\n## قائمة العقود (symbol, side, strike, expiry, bid, ask, delta, iv, volume, oi)\n"
                 + "\n".join(f"{m['symbol']}|{m['side']}|{m['strike']:g}|{m['expiry']}|{m['bid']}|{m['ask']}|{m['delta']}|{m['iv']}|"
                             f"{m['volume']:.0f}|{m['oi']:.0f}" for m in sorted(menu, key=lambda m: (m["expiry"], m["side"], m["strike"]))))
    return "\n\n".join(parts), menu, S, recs


def portfolio_text(st, marks, now):
    L = [f"## المحفظة الورقية الآن {now:%H:%M} نيويورك: النقد {st['cash']:,.2f}$ | القيمة {equity(st, marks):,.2f}$"]
    for p in st["positions"]:
        b = marks.get(p["symbol"], {}).get("bid", p["entry"])
        L.append(f"- [{p['id']}] {p['side']} {p['strike']:g} ({p['expiry']}) ×{p['qty']} دخول {p['entry']:.2f} الآن {b:.2f} "
                 f"({(b / p['entry'] - 1) * 100:+.0f}%) | وقفك {p['stop']} هدفك {p['target']} | تُلغى إن: {p['invalidation']}")
    day = now.date().isoformat()
    for c in st["closed"]:
        if c["exit_time"][:10] == day:
            L.append(f"- مغلقة اليوم: {c['side']} {c['strike']:g} = {c['pnl']:+.0f}$ ({c['why']})")
    if len(L) == 1:
        L.append("- لا مراكز.")
    return "\n".join(L)


def ask_claude(prompt, now):
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
        r = client.messages.create(model=MODEL, max_tokens=8000, system=RULES, messages=[{"role": "user", "content": prompt}],
                                   output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}})
    except Exception as e:  # noqa: BLE001
        print("فشل الطلب:", type(e).__name__, e)
        return None
    u = {"input": getattr(r.usage, "input_tokens", 0) or 0, "output": getattr(r.usage, "output_tokens", 0) or 0}
    new = not os.path.exists(USAGE)
    with open(USAGE, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=an.USAGE_COLS)
        if new:
            w.writeheader()
        w.writerow({"time": now.isoformat(timespec="seconds"), "kind": "decide", "model": MODEL, **u,
                    "usd": round(an.cost(u, MODEL), 5), "status": r.stop_reason})
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


def main(argv):
    now = datetime.now(NY)
    force, no_send, no_ai = "--force" in argv, "--no-send" in argv, "--no-ai" in argv
    if not force and (now.weekday() >= 5 or not DECIDE_FROM <= now.time() <= END_T):
        print("خارج الجلسة.")
        return
    st = load_state()
    day = now.date().isoformat()
    if st.get("day") != day:
        st.update(day=day, decisions_today=0)
    marks = {q["symbol"]: q for q in quotes([p["symbol"] for p in st["positions"]])} if st["positions"] else {}
    for rec in auto_exits(st, marks, now):
        send(sell_message(rec, rec["why"]), no_send)
    decide_slot = now.minute % 15 < 5 and DECIDE_FROM <= now.time() <= LAST_DECIDE
    if (decide_slot or force) and not no_ai:
        text, menu, spot, recs = gather(now)
        marks.update({m["symbol"]: m for m in menu})
        prompt = text + "\n\n" + portfolio_text(st, marks, now) + f"\n\nالوقت الآن {now:%H:%M} نيويورك. قرّر."
        d = ask_claude(prompt, now)
        if d:
            st["decisions"] = st.get("decisions", 0) + 1
            st["decisions_today"] = st.get("decisions_today", 0) + 1
            row, qty, ok, note = execute(st, d, menu, marks, now, spot, no_send)
            log_row(now, spot, d, row, qty, ok, note)
            journal(now, spot, d, "نُفّذ" if ok else "لم يُنفَّذ", note)
    if now.time() >= REPORT_AT and st.get("report_day") != day:
        recs = []
        try:
            z = pd.read_csv(os.path.join(ROOT, "zero_dte_log.csv"), dtype=str)
            recs = [f"{r.time} {r.symbol} {r.side} {r.strike}" for r in z[z["date"] == day].itertuples()]
        except Exception:  # noqa: BLE001
            pass
        send(day_report(st, now, recs), no_send)
        st["report_day"] = day
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
