#!/usr/bin/env python3
"""مراقب عقود الشركات اللحظي: متابعة مستمرة طوال الجلسة، ورسالة فورية عند أي حدث مهم مع «قراءتي» ونصيحة واضحة.

لا يغيّر التوصيات، ولا يكتب على ملفات بوت الشركات (يقرأ stocks_positions.json وstocks_mine.json فقط).
الرسائل تصل إلى TELEGRAM_CHAT_ID وإلى من أضافهم المستخدم في TELEGRAM_EXTRA_CHAT_IDS (بموافقته في 20/10).
وسم «🛒 عقدك» يخص صاحب الحساب، فلا يظهر في نسخ المشاهدين.

ماذا يراقب (مهمة GitHub طويلة تدور كل دقيقة، مثل research/live_watcher.py):
  - كل دقيقة: سعر السهم لحظياً من Alpaca (IEX)، ومنه: حركة حادة ضدك خلال 15 دقيقة، وكل 1% إضافي ضدك منذ الدخول.
  - كل 5 دقائق: قيمة العقد (ياهو، متأخرة ~15 دقيقة، كما في البوت): مراحل −15% و−30% و+25%
    (هدف +50% ووقف −40% وتنبيه −25% يرسلها بوت الشركات نفسه، فلا نكررها)؛ والعامل المحرّك (النفط/البتكوين) إن انقلب 2% ضدك؛
    والوقت (يومان أو أقل والسهم بعيد عن التعادل، وصباح يوم الانتهاء).
  - كل 5 دقائق: أخبار Google News الجديدة للسهم (وللنفط/البتكوين إن كان محرّكه). كل خبر جديد يقرؤه Claude Haiku 5.5
    مع تفاصيل العقد: أثره على الشركة، ومع العقد أم ضده، وأهميته، وملخص عربي. الأهمية المتوسطة والعالية تُرسل فوراً.
  - كل 10 دقائق: أخبار السوق الكبرى (الرسوم والصين، الفيدرالي والفائدة، التضخم والوظائف، الجيوسياسة، وول ستريت اليوم).
    يقرؤها Claude مع قائمة كل العقود المفتوحة ويحكم: أي عقد يتأثر، ومعه أم ضده، وبقوة أم جزئياً. خبر الشركة وحده لا يكفي:
    خبر سوق كبير قد يعكس سهماً أرباحه ممتازة. لكل خبر سوق مهم رسالة واحدة فيها كل العقود المتأثرة ونصيحة لكل عقد.
بلا حد زمني للرسائل؛ لكن كل حدث يُرسل مرة واحدة فقط (كل خبر، وكل مرحلة).
«قراءتي» قاعدة ثابتة وواضحة (أبقِ / فكّر بالخروج / الأفضل الخروج) وليست ضماناً. كل رسالة تُسجَّل في stocks_watcher_log.csv للمراجعة.

يحتاج: TELEGRAM_TOKEN، TELEGRAM_CHAT_ID، ALPACA_KEY/ALPACA_SECRET (بدونها: سعر ياهو المتأخر)، ANTHROPIC_API_KEY (بدونها: لا أخبار).
الملفات (جديدة): stocks_watcher_state.json، stocks_watcher_log.csv، messages_stocks_watcher.jsonl، stocks_watcher_health.json
تشغيل: python stocks_watcher.py [--once]
"""
import csv
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import numpy as np
import requests
import yfinance as yf

import options_bot as ob   # قراءة فقط: الوقت، أيام السوق، الحسابات
import stocks_bot as sb    # قراءة فقط: قائمة الأسهم، سعر العقد، أسماء الأيام

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "stocks_watcher_state.json")
LOG = os.path.join(ROOT, "stocks_watcher_log.csv")
MSGS = os.path.join(ROOT, "messages_stocks_watcher.jsonl")
HEALTH = os.path.join(ROOT, "stocks_watcher_health.json")
MODEL = "claude-haiku-5-5"
ALPACA = "https://data.alpaca.markets"
MAX_RUN_MIN = 345          # حد مهمة GitHub 6 ساعات (مع هامش للحفظ)
SLOW_EVERY_MIN = 5         # قيمة العقد، الأخبار، المحرّك، تحديث قائمة العقود
HEARTBEAT_MIN = 30
SHARP_MOVE = 0.0075        # حركة السهم ضدك خلال 15 دقيقة
STOCK_STEP = 0.01          # كل 1% إضافي ضدك منذ الدخول
STEPS_DOWN = (-0.15, -0.30)
STEPS_UP = (0.25,)
DRIVER_MOVE = 0.02
MACRO_EVERY_MIN = 10       # أخبار السوق الكبرى
MACRO_QUERIES = ("stock market today Wall Street", "Trump tariffs China trade", "Federal Reserve interest rates",
                 "inflation CPI jobs report economy", "geopolitical tensions markets oil")
NEWS_LOOKBACK_MIN = 60     # عند بدء التشغيل: لا نرسل أخباراً أقدم من ساعة (ولا أقدم من الدخول)
LOG_COLS = ["time", "pos_id", "ticker", "side", "event", "detail", "entry", "val", "pnl", "spot", "need", "tdays",
            "advice", "reasons"]
DRIVERS = {"oil": (("CL=F", "USO"), "النفط", "oil prices"), "btc": (("BTC-USD",), "البتكوين", "bitcoin price")}


# ============================ أدوات ============================
def load_state():
    try:
        s = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        s = {}
    for k in ("seen", "fired", "stock_level"):
        s.setdefault(k, {})
    return s


def save_state(s):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


def read_repo_json(name):
    """أحدث نسخة من ملف البوت: داخل GitHub من origin/main (البوتات تحفظ أثناء الجلسة)، ومحلياً من القرص."""
    if os.getenv("GITHUB_ACTIONS") == "true":
        try:
            subprocess.run(["git", "fetch", "-q", "--depth=1", "origin", "main"], cwd=ROOT, timeout=60, capture_output=True)
            r = subprocess.run(["git", "show", f"FETCH_HEAD:{name}"], cwd=ROOT, timeout=30, capture_output=True, text=True)
            if r.returncode == 0:
                return json.loads(r.stdout)
        except Exception as e:
            print("تعذر قراءة", name, "من origin:", e)
    try:
        return json.load(open(os.path.join(ROOT, name), encoding="utf-8"))
    except Exception:
        return {}


def watched_positions(n):
    """العقود المفتوحة عند البوت + عقودك المشتراة (بسعرك أنت إن سجّلته)، التي لم تنتهِ."""
    P = read_repo_json("stocks_positions.json").get("positions", [])
    mine = read_repo_json("stocks_mine.json").get("items", {})
    out = []
    for p in P:
        try:
            if date.fromisoformat(p["exp"]) < n.date():
                continue
        except Exception:
            continue
        m = sb.mine_for(p, mine)
        bought = bool(m and m.get("mode") == "bought")
        if sb.is_closed(p) and not bought:
            continue
        q = dict(p)
        q["_bought"] = bought
        if bought and m.get("price"):
            q["entry"] = float(m["price"])
        out.append(q)
    return out


def alpaca_prices(tickers):
    """آخر صفقة لحظية لكل سهم من Alpaca (IEX). {سهم: سعر}؛ فارغ عند الفشل أو غياب المفاتيح."""
    if not (os.getenv("ALPACA_KEY") and os.getenv("ALPACA_SECRET")) or not tickers:
        return {}
    url = f"{ALPACA}/v2/stocks/trades/latest?" + urllib.parse.urlencode({"symbols": ",".join(sorted(tickers)), "feed": "iex"})
    req = urllib.request.Request(url, headers={"APCA-API-KEY-ID": os.getenv("ALPACA_KEY"),
                                               "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET"), "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode("utf-8"))
        return {k: float(v["p"]) for k, v in (d.get("trades") or {}).items() if v.get("p")}
    except Exception as e:
        print("Alpaca:", e)
        return {}


def driver_at(syms, t_iso):
    """سعر المحرّك الآن ووقت الدخول (شموع 5 دقائق من ياهو؛ أول رمز يعمل). دخول أقدم من 5 أيام = أول شمعة متاحة."""
    h = None
    for sym in syms:
        try:
            h = yf.Ticker(sym).history(period="5d", interval="5m")["Close"].dropna()
        except Exception:
            h = None
        if h is not None and not h.empty:
            break
    if h is None or h.empty:
        return None, None
    now = float(h.iloc[-1])
    try:
        t0 = datetime.fromisoformat(t_iso)
        before = h[h.index <= t0]
        then = float(before.iloc[-1]) if len(before) else float(h.iloc[0])
    except Exception:
        then = None
    return now, then


def rss(query, since):
    """عناوين Google News الأحدث من since: [(وقت UTC، عنوان، مصدر)]."""
    out = []
    try:
        r = requests.get("https://news.google.com/rss/search",
                         params={"q": query + " when:1d", "hl": "en-US", "gl": "US", "ceid": "US:en"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        root = ET.fromstring(r.content)
    except Exception as e:
        print("أخبار:", e)
        return out
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        try:
            t = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc)
        except Exception:
            continue
        if title and t >= since:
            out.append((t, title, (it.findtext("source") or "").strip()))
    return out


def news_key(title):
    return re.sub(r"[^a-z0-9]+", " ", title.lower().rsplit(" - ", 1)[0]).strip()[:120]


# ============================ قراءة الأخبار (Claude) ============================
CALIBRATION = (
    " Calibrate importance strictly: 'high' is rare (a few times a week at most) and only for an actual new event that moves prices now: a new tariff or trade measure taking effect, a Fed decision or surprise, a major data surprise, earnings or guidance, war or escalation, a big index sell-off. Lawsuits and court filings, previews, 'what to watch' pieces, opinion and analysis, explainers, price recaps of moves that already happened, diplomatic talks with no outcome, and stories about other countries' tariffs are low."
)


NEWS_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "i": {"type": "integer"},
            "company_impact": {"type": "string", "enum": ["positive", "negative", "neutral"]},
            "contract_impact": {"type": "string", "enum": ["with", "against", "none"]},
            "importance": {"type": "string", "enum": ["low", "medium", "high"]},
            "summary_ar": {"type": "string"},
        },
        "required": ["i", "company_impact", "contract_impact", "importance", "summary_ar"],
        "additionalProperties": False}}},
    "required": ["items"],
    "additionalProperties": False,
}


def classify(p, name, heads, driver_label=None):
    """يرجع قائمة تقييمات لكل عنوان، أو None إن تعذّر (لا مفتاح أو خطأ)."""
    if not os.getenv("ANTHROPIC_API_KEY") or not heads:
        return None
    side = p["side"]
    lines = "\n".join(f"{i}. [{src}] {title}" for i, (_, title, src) in enumerate(heads))
    prompt = (
        f"You are reviewing fresh news for an open options trade.\n"
        f"Trade: {p['ticker']} ({name}) {side} option, strike {p['strike']}, expiring {p['exp']}. "
        f"A {side} gains if the stock goes {'UP' if side == 'CALL' else 'DOWN'}.\n"
        + (f"This stock is driven by {driver_label}; headlines about it are included.\n" if driver_label else "")
        + "For each headline judge: its impact on the company (positive/negative/neutral); whether it pushes the stock "
        "in the trade's favour (with), against it (against), or has no real bearing (none); and its importance for the "
        "stock's price in the next few days (low: routine, opinion, old or recycled news, listicles; medium: real "
        "news that can move the stock; high: material — earnings, guidance, regulation, lawsuits, big contracts, "
        "analyst rating changes by major banks, sharp moves in the driver). Write summary_ar as one short, plain "
        "Arabic sentence a retail trader understands. Do not guess beyond the headline." + CALIBRATION +
        "\n\nHeadlines:\n" + lines)
    return ask_claude(prompt, NEWS_SCHEMA)


def ask_claude(prompt, schema):
    """طلب واحد إلى Claude بإخراج JSON مقيّد بالمخطط. يرجع قائمة items أو None عند أي فشل."""
    key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        return None
    if not key.startswith("sk-ant-") or any(ch.isspace() for ch in key) or '"' in key:
        # لا نطبع أي جزء من المفتاح أبداً (السجلات عامة)
        print("Claude: قيمة ANTHROPIC_API_KEY بصيغة غير صحيحة (يجب أن تكون المفتاح وحده في سطر واحد). لا قراءة أخبار.")
        return None
    import anthropic
    try:
        client = anthropic.Anthropic(api_key=key, timeout=60.0)
        resp = client.messages.create(
            model=MODEL, max_tokens=6000,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}])
    except anthropic.RateLimitError:
        print("Claude: حد الطلبات، نعيد لاحقاً.")
        return None
    except anthropic.APIStatusError as e:
        print(f"Claude: خطأ {e.status_code}: {e.message}")
        return None
    except anthropic.APIConnectionError as e:
        cause = e.__cause__ or e
        print(f"Claude: خطأ اتصال ({type(cause).__name__}).")   # نوع الخطأ فقط: نصه قد يحوي الترويسات والمفتاح
        return None
    if resp.stop_reason in ("refusal", "max_tokens"):
        print("Claude: لم يكتمل الرد:", resp.stop_reason)
        return None
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text)["items"]
    except Exception as e:
        print("Claude: رد غير مفهوم:", e)
        return None


MACRO_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "i": {"type": "integer"},
            "importance": {"type": "string", "enum": ["low", "medium", "high"]},
            "summary_ar": {"type": "string"},
            "effects": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "k": {"type": "integer"},
                    "impact": {"type": "string", "enum": ["with", "against", "none"]},
                    "strength": {"type": "string", "enum": ["weak", "strong"]},
                    "why_ar": {"type": "string"},
                },
                "required": ["k", "impact", "strength", "why_ar"],
                "additionalProperties": False}},
        },
        "required": ["i", "importance", "summary_ar", "effects"],
        "additionalProperties": False}}},
    "required": ["items"],
    "additionalProperties": False,
}


def macro_prompt(positions, heads):
    book = "\n".join(f"{k}. {p['ticker']} ({sb.WATCHLIST.get(p['ticker'], (p['ticker'],))[0]}) {p['side']} option, "
                     f"gains if the stock goes {'UP' if p['side'] == 'CALL' else 'DOWN'}"
                     for k, p in enumerate(positions))
    lines = "\n".join(f"{i}. [{src}] {title}" for i, (_, title, src) in enumerate(heads))
    return (
        "You are watching market-wide news for a trader's open options positions. Company news can be overwhelmed by "
        "macro news (for example, new China tariffs can sink a stock right after strong earnings), so judge each "
        "headline's effect on EACH position, using what you know about each company's business, sector, supply "
        "chain and sensitivity to rates, trade, consumers, energy and risk appetite.\n\nOpen positions:\n" + book +
        "\n\nFor each headline give: importance for US stock prices in the next few days (low: routine, opinion, "
        "recycled, listicles; medium: real macro news; high: market-moving, such as new tariffs or trade measures, "
        "Fed decisions or surprises, major data surprises, war or escalation, big index sell-offs); a one-sentence "
        "plain Arabic summary; and effects: one entry per position it really affects (k = position number), whether "
        "it pushes that position's stock in the trade's favour (with) or against it (against), whether strongly or "
        "weakly, and why_ar: a very short Arabic reason (a few words). Leave effects empty if no position is really "
        "affected. Do not guess beyond the headline." + CALIBRATION +
        ' Use "strong" only when the headline describes something that directly hits that company\'s own business '
        "or sector now; indirect, speculative or market-wide mood effects are weak.\n\nHeadlines:\n" + lines)


def classify_macro(positions, heads):
    """أخبار السوق الكبرى مقابل كل العقود المفتوحة في طلب واحد. None عند الفشل."""
    if not os.getenv("ANTHROPIC_API_KEY") or not heads or not positions:
        return None
    return ask_claude(macro_prompt(positions, heads), MACRO_SCHEMA)


def macro_message(c, head, rows, n):
    imp = {"medium": "متوسطة", "high": "عالية"}[c["importance"]]
    L = [f"🌐 <b>خبر سوق مهم</b> (أهمية {imp}) #متابعة",
         f"📰 {ob.esc(c['summary_ar'])}", f"<i>{ob.esc(head[1][:160])}</i>", ""]
    for p, e, f, adv in rows:
        tag = ("في صالح عقدك ✅" if e["impact"] == "with" else
               ("ضد عقدك بقوة ⚠️" if e["strength"] == "strong" else "ضد عقدك جزئياً"))
        pl = f" | {f['pnl'] * 100:+.0f}%" if f.get("pnl") is not None else ""
        L.append(f"{ICON[adv[0]]} <b>{ob.esc(p['ticker'])} {p['side']}</b> {p['strike']:,.1f}: {tag}{pl}"
                 f" ({ob.esc(e['why_ar'])})")
        L.append(f"   💡 <b>قراءتي: {adv[0]}.</b> " + "، ".join(adv[1][:2]) + ".")
    L.append(f"⚠️ القرار قرارك | 🕒 {n.astimezone(ob.RY).strftime('%H:%M')} الرياض")
    return "\n".join(L)


def macro_rows(c, positions, last_f, n):
    rows = []
    for e in c["effects"]:
        if e["impact"] == "none" or not (0 <= e["k"] < len(positions)):
            continue
        p = positions[e["k"]]
        f = last_f.get(p["id"]) or facts(p, n, None, None)
        strong, high = e["strength"] == "strong", c["importance"] == "high"
        imp = ("high" if high else "medium") if strong else "low"   # التأثير الجزئي = «أبقِ وراقب» دائماً
        rows.append((p, e, f, advice(f, {"news": e["impact"], "importance": imp})))
    return rows


def check_macro(positions, last_f, n, state, start):
    """كل 10 دقائق: أخبار السوق الجديدة، ورسالة لكل خبر مهم يمس عقداً واحداً على الأقل. يرجع عدد الرسائل."""
    since = start.astimezone(timezone.utc) - timedelta(minutes=NEWS_LOOKBACK_MIN)
    seen = state.setdefault("seen_macro", [])
    fresh, keys = [], set()
    for q in MACRO_QUERIES:
        for h in rss(q, since):
            k = news_key(h[1])
            if k and k not in seen and k not in keys:
                keys.add(k)
                fresh.append(h)
    fresh = sorted(fresh)[-25:]
    if not fresh or not positions:
        return 0
    cls = classify_macro(positions, fresh)
    if cls is None:                               # لا نعلّم الأخبار «مقروءة» إلا إذا قُرئت فعلاً
        return 0
    for h in fresh:
        seen.append(news_key(h[1]))
    del seen[:-600]
    sent = 0
    for c in cls:
        if c["importance"] == "low" or not (0 <= c["i"] < len(fresh)):
            continue
        rows = macro_rows(c, positions, last_f, n)
        if not rows:
            continue
        head = fresh[c["i"]]
        loud = c["importance"] == "high" or any(e["strength"] == "strong" for _, e, _, _ in rows)
        if loud:
            shown = rows if c["importance"] == "high" else [r for r in rows if r[1]["strength"] == "strong"]
            if send(macro_message(c, head, shown, n)):
                sent += 1
        for p, e, f, adv in rows:                  # كل شيء يُسجَّل للمراجعة، المرسل وغير المرسل
            log_row(n, p, "macro" if loud else "macro_quiet",
                    f"{e['impact']}/{e['strength']}/{c['importance']}: {head[1]}", f, adv)
    return sent


# ============================ التقييم والنصيحة ============================
def tdays_left(n, exp):
    return int(np.busday_count(n.date(), date.fromisoformat(exp))) + 1   # يوم الانتهاء نفسه يوم تداول


def facts(p, n, spot, q):
    """الأرقام الأساسية للعقد الآن. q قد تكون None (لا سعر عقد)."""
    call = p["side"] == "CALL"
    d = 1 if call else -1
    f = {"spot": spot, "val": None, "pnl": None, "need": None, "prob": None, "mv": None,
         "tdays": tdays_left(n, p["exp"])}
    if spot and p.get("entry_spot"):
        f["mv"] = (spot / float(p["entry_spot"]) - 1) * d
    be = p["strike"] + p["entry"] if call else p["strike"] - p["entry"]
    if spot:
        f["need"] = d * (be / spot - 1)
    if q:
        f["val"], f["pnl"] = q["val"], q["val"] / p["entry"] - 1
        f["src"] = q.get("src")
        if spot:
            exp = date.fromisoformat(p["exp"])
            secs = max((datetime(exp.year, exp.month, exp.day, 16, 0, tzinfo=n.tzinfo) - n).total_seconds(), 1800)
            iv = min(2.0, max(0.1, q.get("iv") or 0.4))
            f["prob"] = ob.prob_beyond(spot, be, secs / (365 * 86400), iv, p["side"])
    return f


def advice(f, event):
    """(النصيحة، [أسباب]) — قاعدة ثابتة وواضحة."""
    exit_r, think_r, hold_r = [], [], []
    pnl, need, prob, td = f["pnl"], f["need"], f["prob"], f["tdays"]
    if event.get("news") == "against" and event.get("importance") == "high":
        exit_r.append("خبر مهم ضد العقد")
    elif event.get("news") == "against" and event.get("importance") == "low":
        hold_r.append("خبر ضد العقد لكن تأثيره محدود، راقب")
    elif event.get("news") == "against":
        think_r.append("خبر ضد العقد")
    if event.get("news") == "with":
        hold_r.append("الخبر في صالح العقد")
    if prob is not None and prob < 0.10 and (pnl is None or pnl < 0):
        exit_r.append(f"احتمال وصول السهم للتعادل ≈ {prob * 100:.0f}% فقط")
    if td <= 1 and need is not None and need > 0:
        exit_r.append(f"ينتهي اليوم ويحتاج {need * 100:+.1f}%")
    elif td <= 2 and need is not None and need > 0.01:
        think_r.append(f"باقٍ {td} يوم تداول ويحتاج {need * 100:+.1f}%")
    if event.get("driver_against"):
        (exit_r if (pnl is not None and pnl <= -0.30) else think_r).append(event["driver_against"])
    if event.get("sharp") or (f["mv"] is not None and f["mv"] <= -0.015):
        (exit_r if (pnl is not None and pnl <= -0.30) else think_r).append("السهم يتحرك ضدك")
    if pnl is not None and pnl <= -0.40:
        exit_r.insert(0, f"العقد تجاوز الوقف ({pnl * 100:+.0f}%)")
    elif pnl is not None and pnl <= -0.30 and not exit_r:
        think_r.append(f"العقد خسر {abs(pnl) * 100:.0f}% (الوقف عند −40%)")
    elif pnl is not None and pnl <= -0.15:
        hold_r.append(f"الفكرة قائمة لكن العقد {pnl * 100:+.0f}%؛ التزم بالوقف عند −40%")
    if pnl is not None and pnl >= 0.25 and (exit_r or think_r):
        return "فكّر بالخروج", ["احمِ ربحك الحالي"] + (exit_r + think_r)[:1]
    if exit_r:
        return "الأفضل الخروج", (exit_r + think_r)[:3]
    if think_r:
        return "فكّر بالخروج", think_r[:3]
    return "أبقِ", (hold_r or ["لا جديد يغيّر فكرة الصفقة"])[:2]


ICON = {"الأفضل الخروج": "🔴", "فكّر بالخروج": "🟠", "أبقِ": "🟢"}


def days_ar(k):
    return f"{k} يوم" if k in (1, 2) or k > 10 else f"{k} أيام"


def price_age(f):
    """وصف عمر سعر العقد بحسب مصدره الفعلي (لحظي من Alpaca/Tastytrade، وإلا ياهو المتأخر)."""
    if f.get("src") in ("alpaca", "tasty"):
        return "سعر العقد لحظي"
    return "سعر العقد من ياهو متأخر ~15د"


def message(p, f, event_lines, adv, n):
    e = p["entry"]
    L = [f"📌 <b>متابعة لحظية</b> #متابعة" + (" | 🛒 عقدك" if p.get("_bought") else ""),
         f"{ICON[adv[0]]} <b>{ob.esc(p['ticker'])} {p['side']}</b> {p['strike']:,.1f} | 📅 {sb.date_ar(p['exp'])}"
         f" | باقٍ {days_ar(f['tdays'])} تداول"]
    if f["val"] is not None:
        L.append(f"💵 دخول ${e * 100:,.0f} ← الآن ${f['val'] * 100:,.0f} | <b>{f['pnl'] * 100:+.0f}%</b> "
                 f"({(f['val'] - e) * 100:+,.0f}$) <i>({price_age(f)})</i>")
    s = []
    if f["spot"]:
        s.append(f"📈 السهم {f['spot']:,.2f}")
        if f["mv"] is not None:
            s.append(f"({'معك' if f['mv'] >= 0 else 'ضدك'} {abs(f['mv']) * 100:.1f}% منذ الدخول)")
        if f["need"] is not None:
            s.append("| تجاوز التعادل ✅" if f["need"] <= 0 else f"| يحتاج {f['need'] * 100:+.1f}% للتعادل")
    if s:
        L.append(" ".join(s))
    L += event_lines
    L.append(f"💡 <b>قراءتي: {adv[0]}.</b> " + "، ".join(adv[1]) + ".")
    L.append(f"⚠️ القرار قرارك | 🕒 {n.astimezone(ob.RY).strftime('%H:%M')} الرياض")
    return "\n".join(L)


def _post(tok, chat, text):
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      data={"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
                      timeout=20)
    if not r.ok:
        print("تليجرام:", r.text[:200])
    return r.ok


def send(text):
    """إلى TELEGRAM_CHAT_ID، ونسخة لكل من في TELEGRAM_EXTRA_CHAT_IDS (فشل أحدهم لا يؤثر على غيره).
    لا يستخدم دوال الإرسال المشتركة."""
    try:
        with open(MSGS, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), "text": text},
                                ensure_ascii=False) + "\n")
    except Exception:
        pass
    tok, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("[بلا إرسال]\n" + text + "\n")
        return False
    try:
        ok = _post(tok, chat, text)
    except Exception as e:
        print("تليجرام:", e)
        ok = False
    extra = [x.strip() for x in (os.getenv("TELEGRAM_EXTRA_CHAT_IDS") or "").replace(";", ",").split(",") if x.strip()]
    copy = text.replace(" | 🛒 عقدك", "")
    for cid in extra:
        try:
            _post(tok, cid, copy)
        except Exception as e:
            print("تعذر إرسال نسخة لمشاهد:", type(e).__name__)
    return ok


def log_row(n, p, event, detail, f, adv):
    new = not os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_COLS)
        if new:
            w.writeheader()
        r2 = lambda x: "" if x is None else round(x, 4)
        w.writerow({"time": n.isoformat(timespec="minutes"), "pos_id": p["id"], "ticker": p["ticker"], "side": p["side"],
                    "event": event, "detail": detail[:200], "entry": p["entry"], "val": r2(f["val"]), "pnl": r2(f["pnl"]),
                    "spot": r2(f["spot"]), "need": r2(f["need"]), "tdays": f["tdays"], "advice": adv[0],
                    "reasons": " | ".join(adv[1])})


# ============================ الدورة ============================
def check_position(p, n, state, spot, hist, slow, out):
    """يضيف إلى out أحداث هذا العقد: [(نوع، تفصيل للسجل، سطور الرسالة، معلومات للنصيحة)]."""
    fired = state["fired"].setdefault(p["id"], [])
    d = 1 if p["side"] == "CALL" else -1

    def fire(key, kind, detail, lines, ev):
        if key in fired:
            return
        fired.append(key)
        out.append((kind, detail, lines, ev))

    # حركة حادة خلال 15 دقيقة (كل مرة تتجدد فيها الحركة من مستوى جديد)
    if spot and hist:
        past = [px for t, px in hist if (n - t).total_seconds() >= 14 * 60]
        if past:
            mv15 = (spot / past[-1] - 1) * d
            if mv15 <= -SHARP_MOVE:
                lvl = state.setdefault("sharp", {}).get(p["id"])
                if lvl is None or (spot / lvl - 1) * d <= -SHARP_MOVE:
                    state["sharp"][p["id"]] = spot
                    fire(f"sharp@{n.isoformat(timespec='minutes')}", "sharp", f"{mv15 * 100:+.2f}% في 15د",
                         [f"📉 السهم تحرك {abs(mv15) * 100:.1f}% ضدك خلال 15 دقيقة."], {"sharp": True})
    # كل 1% إضافي ضدك منذ الدخول
    if spot and p.get("entry_spot"):
        mv = (spot / float(p["entry_spot"]) - 1) * d
        step = int(math.floor(-mv / STOCK_STEP)) if mv < 0 else 0
        if step >= 1 and step > state["stock_level"].get(p["id"], 0):
            state["stock_level"][p["id"]] = step
            fire(f"stock{step}", "stock", f"{mv * 100:+.2f}% منذ الدخول",
                 [f"↘️ السهم صار ضدك {abs(mv) * 100:.1f}% منذ الدخول."], {})
    if not slow:
        return
    f = slow["f"]
    # مراحل قيمة العقد
    if f["pnl"] is not None:
        line = [f"💵 العقد نزل إلى {f['pnl'] * 100:+.0f}% من سعر الدخول."]
        for s_ in STEPS_DOWN:
            if f["pnl"] <= s_:
                n0 = len(out)
                fire(f"down{s_}", "contract", f"{f['pnl'] * 100:+.0f}%", line, {})
                if len(out) > n0:
                    line = []                         # سطر واحد حتى لو عبر أكثر من مرحلة
        for s_ in STEPS_UP:
            if f["pnl"] >= s_:
                fire(f"up{s_}", "contract", f"{f['pnl'] * 100:+.0f}%",
                     [f"💵 العقد ارتفع {f['pnl'] * 100:+.0f}% (هدف القاعدة +50%)."], {})
    # المحرّك
    if slow.get("driver"):
        label, chg = slow["driver"]
        if chg * d <= -DRIVER_MOVE:
            fire("driver", "driver", f"{label} {chg * 100:+.1f}%",
                 [f"🛢️ {label} تحرك {chg * 100:+.1f}% منذ دخولك، وهذا ضد عقدك."],
                 {"driver_against": f"{label} انقلب ضدك"})
    # الوقت
    if f["tdays"] <= 2 and f["need"] is not None and f["need"] > 0.01 and (f["prob"] is None or f["prob"] < 0.20):
        fire(f"time{f['tdays']}", "time", f"باقٍ {f['tdays']} ويحتاج {f['need'] * 100:+.1f}%",
             [f"⏳ الوقت يقصر: باقٍ {days_ar(f['tdays'])} تداول، والسهم يحتاج {f['need'] * 100:+.1f}%"
              + (f" (احتمال ≈ {f['prob'] * 100:.0f}%)." if f["prob"] is not None else ".")], {})
    # الأخبار
    for (t, title, src), c in slow.get("news", []):
        if c["importance"] == "low" or c["contract_impact"] == "none":
            continue
        tag = {"with": "في صالح عقدك ✅", "against": "ضد عقدك ⚠️"}[c["contract_impact"]]
        imp = {"medium": "متوسطة", "high": "عالية"}[c["importance"]]
        fire(f"news:{news_key(title)}", "news", f"{c['contract_impact']}/{c['importance']}: {title}",
             [f"📰 خبر {tag} (أهمية {imp}): {ob.esc(c['summary_ar'])}",
              f"<i>{ob.esc(title[:160])}</i>"],
             {"news": c["contract_impact"], "importance": c["importance"]})


def compose(events):
    """يدمج أحداث العقد في رسالة واحدة: (معلومات النصيحة، سطور الأحداث)."""
    ev, lines = {}, []
    for kind, _, ls, e in events:
        ev.update(e)
        if kind != "stock" or len(events) == 1:     # «ضدك x%» ظاهرة أصلاً في سطر السهم
            lines += ls
    return ev, lines


def slow_inputs(p, n, state, spot, cache, start):
    """كل 5 دقائق: قيمة العقد، المحرّك، والأخبار الجديدة مقروءة بـ Claude."""
    t = yf.Ticker(p["ticker"])
    q = sb.contract_quote(t, p, cache)
    f = facts(p, n, spot, q)
    res = {"f": f, "driver": None, "news": []}
    name, dkey = sb.WATCHLIST.get(p["ticker"], (p["ticker"], None))
    if dkey in DRIVERS:
        sym, label, _ = DRIVERS[dkey]
        try:
            now, then = driver_at(sym, p["time"])
            if now and then:
                res["driver"] = (label, now / then - 1)
        except Exception as e:
            print("المحرّك:", e)
    since = max(datetime.fromisoformat(p["time"]).astimezone(timezone.utc),
                start.astimezone(timezone.utc) - timedelta(minutes=NEWS_LOOKBACK_MIN))
    heads = rss(f"{name} {p['ticker']} stock", since)
    if dkey in DRIVERS:
        heads += rss(DRIVERS[dkey][2], since)
    seen = state["seen"].setdefault(p["ticker"] + ":" + p["side"], [])
    fresh, keys = [], set()
    for h in sorted(heads):
        k = news_key(h[1])
        if k and k not in seen and k not in keys:
            keys.add(k)
            fresh.append(h)
    if fresh:
        cls = classify(p, name, fresh[:20], DRIVERS[dkey][1] if dkey in DRIVERS else None)
        if cls is not None:                       # لا نعلّم الأخبار «مقروءة» إلا إذا قُرئت فعلاً
            by = {c["i"]: c for c in cls}
            for i, h in enumerate(fresh[:20]):
                seen.append(news_key(h[1]))
                if i in by:
                    res["news"].append((h, by[i]))
            del seen[:-400]
    return res


def checkpoint(start, minutes, errors, last_err, sent, final=False):
    h = {"date": start.date().isoformat(), "start": start.strftime("%H:%M"), "now": ob.now_ny().strftime("%H:%M"),
         "minutes": minutes, "errors": errors, "last_error": last_err, "sent": sent, "final": final}
    try:
        json.dump(h, open(HEALTH, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception as e:
        print("health:", e)
    if os.getenv("GITHUB_ACTIONS") != "true" or final:
        return          # الحفظ النهائي في خطوة سير العمل
    files = [x for x in (STATE, LOG, MSGS, HEALTH) if os.path.exists(x)]
    sh = lambda *a: subprocess.run(list(a), cwd=ROOT, capture_output=True, text=True, timeout=60)
    try:
        sh("git", "config", "user.name", "bot")
        sh("git", "config", "user.email", "bot@users.noreply.github.com")
        sh("git", "add", *files)
        if sh("git", "diff", "--cached", "--quiet").returncode == 0:
            return
        sh("git", "commit", "-m", "stocks watcher heartbeat")
        for _ in range(4):
            pr = sh("git", "pull", "--rebase")
            if pr.returncode != 0:
                sh("git", "rebase", "--abort")
            elif sh("git", "push").returncode == 0:
                return
            time.sleep(5)
    except Exception as e:
        print("checkpoint:", e)


def loop(once=False, now_fn=None):
    now_fn = now_fn or ob.now_ny
    start = now_fn()
    if not ob.market_day(start):
        print("لا جلسة اليوم.")
        return
    state = load_state()
    hist, positions, slow_t, cache, macro_t, last_f = {}, [], None, {}, None, {}
    minutes = errors = sent = 0
    last_err, beat = "", start
    while True:
        n = now_fn()
        if (n.hour, n.minute) >= (16, 0) or (n - start).total_seconds() / 60 > MAX_RUN_MIN:
            break
        if (n.hour, n.minute) >= (9, 31):
            try:
                slow_due = slow_t is None or (n - slow_t).total_seconds() >= SLOW_EVERY_MIN * 60
                if slow_due:
                    positions, cache = watched_positions(n), {}
                tick = sorted({p["ticker"] for p in positions})
                px = alpaca_prices(tick)
                for tk in tick:
                    if tk not in px:                                  # بديل متأخر
                        v = sb.spot_now(yf.Ticker(tk)) if slow_due else None
                        if v:
                            px[tk] = v
                    if tk in px:
                        hist.setdefault(tk, []).append((n, px[tk]))
                        hist[tk] = [(t, v) for t, v in hist[tk] if (n - t).total_seconds() <= 20 * 60]
                for p in positions:
                    spot = px.get(p["ticker"])
                    slow = slow_inputs(p, n, state, spot, cache, start) if slow_due else None
                    if slow:
                        last_f[p["id"]] = slow["f"]
                    events = []
                    check_position(p, n, state, spot, hist.get(p["ticker"]), slow, events)
                    if not events:
                        continue
                    f = slow["f"] if slow else facts(p, n, spot, None)
                    ev, lines = compose(events)
                    adv = advice(f, ev)
                    if send(message(p, f, lines, adv, n)):
                        sent += 1
                    for kind, detail, _, _ in events:
                        log_row(n, p, kind, detail, f, adv)
                if positions and (macro_t is None or (n - macro_t).total_seconds() >= MACRO_EVERY_MIN * 60):
                    sent += check_macro(positions, last_f, n, state, start)
                    macro_t = n
                if slow_due:
                    slow_t = n
                    live_ids = {p["id"] for p in positions}
                    for k in ("fired", "stock_level", "sharp"):
                        state[k] = {i: v for i, v in state.get(k, {}).items() if i in live_ids}
                save_state(state)
                minutes += 1
            except Exception as e:
                errors += 1
                last_err = f"{n.strftime('%H:%M')} {type(e).__name__}: {str(e)[:150]}"
                print("خطأ:", last_err)
            if (n - beat).total_seconds() >= HEARTBEAT_MIN * 60:
                checkpoint(start, minutes, errors, last_err, sent)
                beat = n
        if once:
            break
        nxt = (n + timedelta(minutes=1)).replace(second=5, microsecond=0)
        time.sleep(max(1.0, (nxt - now_fn()).total_seconds()))
    checkpoint(start, minutes, errors, last_err, sent, final=True)
    print(f"انتهى: {minutes} دقيقة، {errors} أخطاء، {sent} رسالة.")


if __name__ == "__main__":
    loop(once="--once" in sys.argv)
