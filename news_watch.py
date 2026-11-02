#!/usr/bin/env python3
"""مراقب الأخبار خارج الجلسة (H-NEWSWATCH، مسجّل مسبقاً في CLAUDE.md 2/11/2026 بطلب المستخدم).

تنبيه واستعداد فقط: لا يغيّر التوصيات ولا فلتر الأخبار في البوت. يرسل إلى TELEGRAM_CHAT_ID وحده.
  - كل 15 دقيقة خارج الجلسة: عناوين Google News الجديدة يصنّفها Claude Haiku (حدث جديد؟ النطاق، الأهمية، الاتجاه).
    تنبيه فوري لخبر «عالي» الأهمية يخص السوق أو شركة فيها عقد مفتوح (مرة لكل حدث، حتى 4 في الليلة)،
    أو لتجاوز العقود الآجلة ES ±1% ثم ±2% منذ إغلاق آخر جلسة (الحَكَم: هل تحرك السوق فعلاً؟).
  - أيام العمل 8:30–9:30 نيويورك: ملخص صباحي واحد بأهم أخبار الليل وحركة ES/NQ.
  - بعد كل جلسة: سطر في news_watch_nights.csv (تنبيهات الليلة، حركة ES، فجوة افتتاح SPX وأول 30 دقيقة) للحكم على فائدته.
الملفات: news_watch_log.csv، news_watch_state.json، news_watch_nights.csv، news_watch_usage.csv، messages_news_watch.jsonl.
تشغيل: python news_watch.py [--no-send] [--force-summary]
"""
import csv
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

import analyst as an

ROOT = os.path.dirname(os.path.abspath(__file__))
NY = ZoneInfo("America/New_York")
LOG =os.path.join(ROOT, "news_watch_log.csv")
STATE = os.path.join(ROOT, "news_watch_state.json")
NIGHTS = os.path.join(ROOT, "news_watch_nights.csv")
USAGE = os.path.join(ROOT, "news_watch_usage.csv")
MODEL = os.getenv("NEWS_WATCH_MODEL", "claude-haiku-5-5")
DAILY_USD = float(os.getenv("NEWS_WATCH_DAILY_USD", "0.2"))
MAX_ALERTS = 4               # تنبيهات أخبار في الليلة الواحدة
ES_LEVELS = (0.01, 0.02)     # حدود تنبيه العقود الآجلة
FUT_STALE_MIN = 45           # آخر شمعة أقدم من هذا = العقود الآجلة مغلقة (العطلة)
SUMMARY_WINDOW = ((8, 30), (9, 30))
BATCH = 60
QUERIES = ["stock market today", "Wall Street stocks S&P 500", "stock futures", "Federal Reserve rates Powell",
           "Trump tariffs", "Trump China trade", "Treasury yields bonds", "oil prices OPEC", "geopolitical tensions markets",
           "Nvidia Apple Microsoft stocks"]
LOG_COLS = ["time_utc", "night", "key", "query", "title", "source", "new", "scope", "ticker", "importance", "direction",
            "topic", "cluster", "alerted"]
NIGHT_COLS = ["night", "news_alerts", "high_market", "es_move", "spx_gap", "spx_r30", "abs_gap"]
IMP_AR = {"high": "عالية", "medium": "متوسطة", "low": "منخفضة"}
DIR_AR = {"up": "صعود", "down": "هبوط", "unclear": "غير واضح"}
SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {
    "type": "object",
    "properties": {"i": {"type": "integer"}, "new": {"type": "boolean"},
                   "scope": {"type": "string", "enum": ["market", "company", "other"]},
                   "ticker": {"type": "string"},
                   "importance": {"type": "string", "enum": ["low", "medium", "high"]},
                   "direction": {"type": "string", "enum": ["up", "down", "unclear"]},
                   "topic": {"type": "string"}, "cluster": {"type": "string"}},
    "required": ["i", "new", "scope", "ticker", "importance", "direction", "topic", "cluster"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False}


# ============================ الوقت ============================
def in_session(now):
    return now.weekday() < 5 and (9, 30) <= (now.hour, now.minute) < (16, 0)


def night_of(now):
    """تاريخ الجلسة القادمة التي تسبقها هذه الليلة (قبل 9:30 يوم عمل = اليوم؛ غير ذلك = يوم العمل التالي)."""
    d = now.date()
    if now.weekday() < 5 and (now.hour, now.minute) < (9, 30):
        return d.isoformat()
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


def last_close(now):
    """وقت إغلاق آخر جلسة (16:00 نيويورك، بلا منطقة زمنية) قبل now."""
    d = now.date()
    if now.weekday() < 5 and (now.hour, now.minute) >= (16, 0):
        return datetime(d.year, d.month, d.day, 16, 0)
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return datetime(d.year, d.month, d.day, 16, 0)


# ============================ العقود الآجلة ============================
def fut_bars(sym):
    try:
        import yfinance as yf
        h = yf.Ticker(sym).history(period="5d", interval="5m", prepost=True)
        if not len(h):
            return None
        h.index = [t.tz_convert("America/New_York").replace(tzinfo=None) for t in h.index]
        return h["Close"].astype(float)
    except Exception as e:  # noqa: BLE001
        print("العقود الآجلة:", sym, type(e).__name__)
        return None


def fut_move(close, now, ref_t):
    """(حركة منذ إغلاق ref_t، عمر آخر شمعة بالدقائق) أو (None, None)."""
    if close is None or not len(close):
        return None, None
    ref = close[close.index <= ref_t]
    if not len(ref):
        return None, None
    nn = now.replace(tzinfo=None)
    age = (nn - close.index[-1]).total_seconds() / 60
    return float(close.iloc[-1]) / float(ref.iloc[-1]) - 1, age


def fut_line(moves):
    parts = []
    for sym, (m, age) in moves.items():
        if m is None:
            parts.append(f"{sym}: غير متاح")
        elif age is not None and age > FUT_STALE_MIN:
            parts.append(f"{sym} {an.pct(m, 1)} (مغلقة الآن)")
        else:
            parts.append(f"{sym} {an.pct(m, 1)}")
    return "العقود الآجلة منذ الإغلاق: " + " | ".join(parts) + " (ياهو، متأخرة ~10د)"


# ============================ الأخبار ============================
def open_tickers():
    try:
        P = json.load(open(os.path.join(ROOT, "stocks_positions.json"), encoding="utf-8")).get("positions", [])
    except Exception:  # noqa: BLE001
        return {}
    today = date.today().isoformat()
    return {p["ticker"]: p.get("side", "") for p in P if not p.get("closed") and p.get("status") != "stopped" and str(p.get("exp", "")) >= today}


def fetch(since, seen, tickers):
    import stocks_watcher as sw
    fresh = []
    for q in QUERIES + [f"{tk} stock" for tk in sorted(tickers)]:
        for t, title, src in sw.rss(q, since):
            k = sw.news_key(title)
            if k and k not in seen:
                seen[k] = t.isoformat()
                fresh.append({"t": t, "key": k, "query": q, "title": title, "source": src})
    return fresh


def prompt(items, tickers):
    import stocks_watcher as sw
    lines = "\n".join(f"{i}. {x['title']}" for i, x in enumerate(items))
    held = ", ".join(sorted(tickers)) or "none"
    return ("You screen headlines published outside US market hours for a trader of SPX 0DTE and single-stock options. "
            "For each numbered headline return: new (true only for an actual new event, not analysis, preview, recap or opinion); "
            "scope ('market' if it can move the whole US index, 'company' if it is about one company, else 'other'); "
            f"ticker (US ticker if scope is company, else ''); held tickers: {held}; importance (low/medium/high); "
            "direction of the expected effect on the index (or on the company if scope is company): up/down/unclear; "
            "topic: 3-8 words in Arabic; cluster: a short English slug naming the underlying event so that several headlines "
            "about the same event share it (e.g. 'fed-cut-dec', 'china-tariff-hike')." + sw.CALIBRATION + "\n\nHeadlines:\n" + lines)


def classify(items, tickers, now, client=None):
    """قائمة بنفس الطول (قاموس أو None)."""
    out = [None] * len(items)
    for s in range(0, len(items), BATCH):
        part = items[s:s + BATCH]
        res = ask(prompt(part, tickers), now, client)
        for it in res or []:
            if 0 <= it.get("i", -1) < len(part):
                out[s + it["i"]] = it
    return out


def ask(text, now, client=None):
    day = now.date().isoformat()
    try:
        df = pd.read_csv(USAGE, dtype=str)
        spent = float(pd.to_numeric(df.loc[df["time"].str[:10] == day, "usd"], errors="coerce").sum())
    except Exception:  # noqa: BLE001
        spent = 0.0
    if spent >= DAILY_USD:
        print("سقف المراقب اليومي بُلغ؛ بلا تصنيف.")
        return None
    if client is None:
        key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
        if not key:
            print("لا ANTHROPIC_API_KEY؛ بلا تصنيف.")
            return None
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=key, timeout=120.0)
        except Exception as e:  # noqa: BLE001
            print("anthropic:", type(e).__name__)
            return None
    try:
        r = client.messages.create(model=MODEL, max_tokens=12000, messages=[{"role": "user", "content": text}],
                                   output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}})
    except Exception as e:  # noqa: BLE001
        print("فشل التصنيف:", type(e).__name__)
        return None
    u = {"input": getattr(r.usage, "input_tokens", 0) or 0, "output": getattr(r.usage, "output_tokens", 0) or 0}
    new = not os.path.exists(USAGE)
    with open(USAGE, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=an.USAGE_COLS)
        if new:
            w.writeheader()
        w.writerow({"time": now.isoformat(timespec="seconds"), "kind": "classify", "model": MODEL, **u,
                    "usd": round(an.cost(u, MODEL), 5), "status": r.stop_reason})
    if r.stop_reason != "end_turn":
        return None
    try:
        return json.loads(next(b.text for b in r.content if getattr(b, "type", "") == "text"))["items"]
    except Exception as e:  # noqa: BLE001
        print("رد غير مفهوم:", type(e).__name__)
        return None


def alertable(c, tickers):
    return bool(c and c["new"] and c["importance"] == "high" and
                (c["scope"] == "market" or (c["scope"] == "company" and c["ticker"].upper() in tickers)))


def item_line(x, tickers):
    c = x["c"]
    t = x["t"].astimezone(NY)
    held = f"\n   يخص عقدك المفتوح: {c['ticker'].upper()} {tickers[c['ticker'].upper()]}" if c["ticker"].upper() in tickers else ""
    return (f"• {x['title']}\n   {t:%H:%M} نيويورك | {c['topic']} | الأثر المتوقع: {DIR_AR.get(c['direction'], '-')} "
            f"(توقّع غير مختبر){held}")


# ============================ السجلات والحالة ============================
def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_state(st):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)


def _csv(path):
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False) if os.path.exists(path) else pd.DataFrame()
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def append_log(rows):
    new = not os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        if new:
            w.writeheader()
        w.writerows(rows)


def night_items(night, min_imp=("high", "medium")):
    d = _csv(LOG)
    if not len(d):
        return d
    d = d[(d["night"] == night) & (d["new"] == "1") & d["importance"].isin(min_imp) & d["scope"].isin(["market", "company"])]
    d = d.assign(rank=d["importance"].map({"high": 0, "medium": 1})).sort_values(["rank", "time_utc"])
    return d.drop_duplicates("cluster")


# ============================ القياس بعد الجلسة ============================
def record_night(day, st):
    """سطر لليلة التي سبقت جلسة day: التنبيهات وحركة ES (من الملخص الصباحي) وفجوة SPX وأول 30 دقيقة."""
    done = set(_csv(NIGHTS).get("night", pd.Series(dtype=str)))
    if day in done:
        return False
    bars = an.load_bars([day])
    g, prev = bars.get(day), [d for d in bars if d < day]
    gap = r30 = None
    if g is not None and len(g) >= 6 and prev:
        pc = float(bars[max(prev)]["Close"].iloc[-1])
        op = float(g["Open"].iloc[0])
        gap, r30 = op / pc - 1, float(g["Close"].iloc[5]) / op - 1
    hi = night_items(day, ("high",))
    row = {"night": day, "news_alerts": st.get("alerts", {}).get(day, 0),
           "high_market": int(len(hi[hi["scope"] == "market"])) if len(hi) else 0,
           "es_move": st.get("morning_es", {}).get(day, ""), "spx_gap": "" if gap is None else round(gap, 5),
           "spx_r30": "" if r30 is None else round(r30, 5), "abs_gap": "" if gap is None else round(abs(gap), 5)}
    new = not os.path.exists(NIGHTS)
    with open(NIGHTS, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=NIGHT_COLS)
        if new:
            w.writeheader()
        w.writerow(row)
    return True


# ============================ التشغيل ============================
def run(now, no_send=False, force_summary=False, client=None, fetcher=None, futures=None):
    st = load_state()
    if in_session(now) and not force_summary:
        print("داخل الجلسة: البوت ومراقب الشركات يغطيانها.")
        return 0
    night = night_of(now)
    if now.weekday() < 5 and (now.hour, now.minute) >= (16, 0):
        try:
            if record_night(now.date().isoformat(), st):
                print("سُجّلت ليلة", now.date().isoformat())
        except Exception as e:  # noqa: BLE001
            print("تعذر تسجيل الليلة:", type(e).__name__)
    tickers = open_tickers()
    seen = {k: v for k, v in st.get("seen", {}).items() if v >= (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()}
    last = st.get("last_check")
    since = (datetime.fromisoformat(last) - timedelta(minutes=20)) if last else datetime.now(timezone.utc) - timedelta(hours=3)
    items = (fetcher or fetch)(since, seen, tickers)
    print(f"عناوين جديدة: {len(items)}")
    cls = classify(items, tickers, now, client) if items else []
    rows, alerts = [], []
    alerted = set(st.get("clusters", {}).get(night, []))
    n_alerts = st.get("alerts", {}).get(night, 0)
    for x, c in zip(items, cls):
        if c is None:
            seen.pop(x["key"], None)      # يُعاد تصنيفه في التشغيل التالي
            continue
        x["c"] = c
        hit = alertable(c, tickers) and c["cluster"] not in alerted and n_alerts + len(alerts) < MAX_ALERTS
        if hit:
            alerts.append(x)
            alerted.add(c["cluster"])
        rows.append({"time_utc": x["t"].isoformat(), "night": night, "key": x["key"], "query": x["query"], "title": x["title"],
                     "source": x["source"], "new": int(c["new"]), "scope": c["scope"], "ticker": c["ticker"].upper(),
                     "importance": c["importance"], "direction": c["direction"], "topic": c["topic"], "cluster": c["cluster"],
                     "alerted": int(hit)})
    if rows:
        append_log(rows)
    ref_t = last_close(now)
    fut = futures if futures is not None else {"ES": fut_bars("ES=F"), "NQ": fut_bars("NQ=F")}
    moves = {k: fut_move(v, now, ref_t) for k, v in fut.items()}
    msgs = []
    if alerts:
        msgs.append("📰 **خبر مهم خارج الجلسة**\n\n" + "\n\n".join(item_line(x, tickers) for x in alerts) + "\n\n" + fut_line(moves))
    es, age = moves.get("ES", (None, None))
    lv_done = set(st.get("es_levels", {}).get(night, []))
    if es is not None and age is not None and age <= FUT_STALE_MIN:
        for lv in ES_LEVELS:
            key = f"{'+' if es > 0 else '-'}{lv}"
            if abs(es) >= lv and key not in lv_done:
                lv_done.add(key)
                hi = night_items(night, ("high",))
                why = ("\nأهم خبر في الليلة: " + hi["title"].iloc[0]) if len(hi) else "\nلم ألتقط خبراً عالي الأهمية؛ السبب غير معروف بعد."
                msgs.append(f"📉 **العقود الآجلة تتحرك بقوة**\n{fut_line(moves)}{why}" if es < 0 else
                            f"📈 **العقود الآجلة تتحرك بقوة**\n{fut_line(moves)}{why}")
    (s0, s1) = SUMMARY_WINDOW
    if (now.weekday() < 5 and s0 <= (now.hour, now.minute) < s1 and st.get("summary") != now.date().isoformat()) or force_summary:
        day = now.date().isoformat()
        d = night_items(day if not force_summary else night)
        lines = [f"🌅 **ملخص أخبار الليل قبل الافتتاح · {day}**", "", fut_line(moves), ""]
        if len(d):
            for r in d.head(8).itertuples():
                lines.append(f"• [{IMP_AR.get(r.importance, '')}] {r.topic} — الأثر المتوقع: {DIR_AR.get(r.direction, '-')}\n   {r.title}")
        else:
            lines.append("لا أخبار مهمة جديدة منذ الإغلاق.")
        lines += ["", "الاتجاهات المتوقعة غير مختبرة؛ الحكم على فائدة التنبيهات بعد 30 ليلة (H-NEWSWATCH)."]
        msgs.append("\n".join(lines))
        if not no_send:
            st["summary"] = day
            st.setdefault("morning_es", {})[day] = "" if es is None else round(es, 5)
    for m in msgs:
        print(m)
        if not no_send:
            an.send(m, "news_watch")
    if not no_send:
        st["seen"] = seen
        st["last_check"] = datetime.now(timezone.utc).isoformat()
        st.setdefault("clusters", {})[night] = sorted(alerted)
        st.setdefault("alerts", {})[night] = n_alerts + len(alerts)
        st.setdefault("es_levels", {})[night] = sorted(lv_done)
        for k in ("clusters", "alerts", "es_levels", "morning_es"):       # آخر 10 ليالٍ تكفي
            st[k] = dict(sorted(st.get(k, {}).items())[-10:])
        save_state(st)
    return 0


def main(argv):
    return run(an.ny_now(), "--no-send" in argv, "--force-summary" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
