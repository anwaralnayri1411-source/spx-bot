#!/usr/bin/env python3
"""ذاكرة سوابق الأخبار (ظل، تسجيل فقط؛ لا يرسل شيئاً ولا يلمس البوتات). بطلب المستخدم 22/10.

السؤال: عند خبر جديد عن شركة، هل تأثرت الشركة نفسها بخبر من النوع نفسه سابقاً، وصعدت أم هبطت؟
وأخبار ترامب/الإدارة: أي شركات تحركها وكيف، مقاساً لا مفترضاً.

--collect (كل 30د): عناوين Google News الجديدة لكل شركة في قائمة بوت الشركات + استعلامات سياسة ترامب.
    يصنّفها Claude Haiku في طلبات مجمّعة: نوع الحدث (EVENTS)، موضوع السياسة إن كان قراراً/تصريحاً من ترامب أو إدارته
    (THEMES)، هل هو حدث جديد (لا تحليل أو تلخيص)، الأهمية، والاتجاه المتوقع للشركة. ← news_memory_log.csv
--eval (بعد الإغلاق): حركة السهم فوق السوق (r − بيتا×SPY، بيتا من شموع 5د لـ 60 يوماً) بعد 30د و60د وحتى الإغلاق
    من شمعة نشر الخبر؛ خبر خارج الجلسة: فجوة الافتتاح (إغلاق سابق ← افتتاح) ثم من الافتتاح. خبر شركة يقاس على شركته؛
    خبر سياسة جديد (أهمية متوسطة+) يقاس على كل الشركات وصناديق القطاعات. لكل قياس «وهمي» في وقت عشوائي للسهم نفسه.
    ← news_memory_outcomes.csv و research/news_memory_results.md
--lookup TICKER EVENT: سطر السوابق كما سيظهر لاحقاً (مثال: python research/news_memory.py --lookup NVDA earnings_beat)

الاختبار في التقرير: لكل خبر (مجمّع: الشركة + النوع + اليوم) له 3 سوابق أو أكثر، هل اتجاه متوسط السوابق يتنبأ
باتجاه الحركة؟ مقابل: سوابق نوع آخر عشوائي للشركة نفسها، وتوقع Claude، و50%.
⚠️ الذاكرة تبدأ من 22/10؛ لا حكم قبل ~30 خبراً لكل نوع. بيانات ياهو متأخرة ~15د (لا يهم هنا: القياس بعد الإغلاق).
وقت النشر في Google News قد يتأخر عن الحدث نفسه. الاختبار التاريخي اليومي في research/news_history.py.
يحتاج ANTHROPIC_API_KEY (بدونه: لا تصنيف، لا تسجيل).
"""
import csv
import json
import os
import random
import sys
import zlib
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
from stocks_sector_study import SECTOR   # noqa: E402  الشركة ← صندوق قطاعها

LOG = os.path.join(ROOT, "news_memory_log.csv")
OUT = os.path.join(ROOT, "news_memory_outcomes.csv")
STATE = os.path.join(ROOT, "news_memory_state.json")
MD = os.path.join(HERE, "news_memory_results.md")
TZ = "America/New_York"
MODEL = "claude-haiku-5-5"
BATCH = 80
MIN_PRIOR = 3

EVENTS = ["earnings_beat", "earnings_miss", "guidance_up", "guidance_down", "analyst_upgrade", "analyst_downgrade",
          "deal_merger", "contract_product", "regulation", "lawsuit_probe", "management", "tariffs_trade",
          "china", "fed_rates", "macro_data", "trump_policy", "geopolitics", "crypto_move", "oil_move",
          "market_move", "other"]
THEMES = ["none", "tariffs_trade", "china", "chips", "pharma", "crypto", "energy", "defense_war", "fed",
          "autos", "tech_ai", "banks_finance", "other_policy"]
EVENT_AR = {"earnings_beat": "أرباح فوق التوقع", "earnings_miss": "أرباح دون التوقع", "guidance_up": "رفع التوقعات",
            "guidance_down": "خفض التوقعات", "analyst_upgrade": "ترقية محلل", "analyst_downgrade": "تخفيض محلل",
            "deal_merger": "صفقة/استحواذ", "contract_product": "عقد/منتج", "regulation": "تنظيم",
            "lawsuit_probe": "قضية/تحقيق", "management": "إدارة", "tariffs_trade": "رسوم/تجارة", "china": "الصين",
            "fed_rates": "الفيدرالي", "macro_data": "بيانات اقتصادية", "trump_policy": "قرار/تصريح ترامب",
            "geopolitics": "جيوسياسة", "crypto_move": "حركة الكريبتو", "oil_move": "حركة النفط",
            "market_move": "حركة السوق", "other": "أخرى"}
POLICY_Q = {"tariffs_trade": "Trump tariffs", "china": "Trump China trade", "chips": "Trump semiconductors chips",
            "pharma": "Trump drug prices pharmaceutical", "crypto": "Trump crypto bitcoin",
            "energy": "Trump oil energy", "defense_war": "Trump military strike war", "fed": "Trump Federal Reserve Powell",
            "autos": "Trump auto tariffs cars", "tech_ai": "Trump AI tech companies",
            "banks_finance": "Trump banks regulation"}
LOG_COLS = ["time_utc", "key", "ticker", "query", "title", "source", "event", "theme", "new", "importance",
            "expect", "affected"]
OUT_COLS = ["key", "ticker", "src_ticker", "time_utc", "t0", "overnight", "event", "theme", "new", "importance",
            "expect", "beta", "ar_gap", "ar30", "ar60", "arclose", "pl30", "pl60", "plclose"]


def names():
    import stocks_bot as sb
    return {tk: v[0] for tk, v in sb.WATCHLIST.items()}


# ============================ التصنيف (Claude) ============================
SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "i": {"type": "integer"},
            "event": {"type": "string", "enum": EVENTS},
            "theme": {"type": "string", "enum": THEMES},
            "new": {"type": "boolean"},
            "importance": {"type": "string", "enum": ["low", "medium", "high"]},
            "expect": {"type": "string", "enum": ["up", "down", "none"]},
            "affected": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["i", "event", "theme", "new", "importance", "expect", "affected"],
        "additionalProperties": False}}},
    "required": ["items"],
    "additionalProperties": False,
}
USAGE = {"in": 0, "out": 0, "calls": 0}


def prompt(heads, tickers):
    lines = "\n".join(f"{i}. [{tk}] {title}" for i, (tk, title) in enumerate(heads))
    return (
        "Classify each news headline for a study of how stocks react to news. The tag in brackets is the company the "
        "headline was found for (MARKET = market-wide or policy search).\n"
        "event: the kind of event. Use trump_policy for actions or statements by Donald Trump or his administration "
        "unless a more specific company event fits better (e.g. a tariff on that company's products is still tariffs_trade).\n"
        "theme: if the headline reports an action, decision, threat or statement by Trump or his administration, its "
        "most specific policy area (tariffs on chips -> chips, on drugs -> pharma, on cars -> autos; tariffs_trade only "
        "for broad or country-level tariffs and trade deals); otherwise none.\n"
        "new: true only if it reports a new event or decision (today's news); false for analysis, opinion, previews, "
        "explainers, recaps of earlier moves, listicles.\n"
        "importance for the tagged company's price (MARKET: for the broad market) in the next day.\n"
        "expect: the likely direction for the tagged company (none for MARKET or no clear effect).\n"
        f"affected: tickers from this list the event plausibly moves (empty if none): {' '.join(tickers)}.\n"
        "Judge only from the headline.\n\nHeadlines:\n" + lines)


def ask(text):
    key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not key.startswith("sk-ant-") or any(ch.isspace() for ch in key):
        print("Claude: لا مفتاح صالح (ANTHROPIC_API_KEY).")
        return None
    import anthropic
    try:
        resp = anthropic.Anthropic(api_key=key, timeout=120.0).messages.create(
            model=MODEL, max_tokens=12000,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": text}])
    except anthropic.APIStatusError as e:
        print(f"Claude: خطأ {e.status_code}")
        return None
    except anthropic.APIConnectionError as e:
        print(f"Claude: خطأ اتصال ({type(e.__cause__ or e).__name__}).")   # لا نطبع نص الخطأ: قد يحوي الترويسات
        return None
    USAGE["in"] += resp.usage.input_tokens
    USAGE["out"] += resp.usage.output_tokens
    USAGE["calls"] += 1
    if resp.stop_reason != "end_turn":
        print("Claude: لم يكتمل الرد:", resp.stop_reason)
        return None
    try:
        return json.loads(next(b.text for b in resp.content if b.type == "text"))["items"]
    except Exception as e:
        print("Claude: رد غير مفهوم:", e)
        return None


def classify(heads):
    """heads: [(ticker, title)] ← قائمة بنفس الطول من القواميس أو None لكل عنوان تعذّر تصنيفه."""
    from concurrent.futures import ThreadPoolExecutor
    tickers = sorted(SECTOR)
    out = [None] * len(heads)
    starts = list(range(0, len(heads), BATCH))
    with ThreadPoolExecutor(4) as ex:
        for s, items in zip(starts, ex.map(lambda s: ask(prompt(heads[s:s + BATCH], tickers)) or [], starts)):
            for it in items:
                if 0 <= it["i"] < min(BATCH, len(heads) - s):
                    it["affected"] = [t for t in it["affected"] if t in SECTOR]
                    out[s + it["i"]] = it
    return out


# ============================ الجمع ============================
def append(path, cols, rows):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new:
            w.writeheader()
        w.writerows(rows)


def collect():
    import stocks_watcher as sw   # rss وnews_key فقط
    now = datetime.now(timezone.utc)
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        st = {}
    seen = {k: v for k, v in st.get("seen", {}).items() if v >= (now - timedelta(days=3)).isoformat()}
    since = now - timedelta(hours=6 if not seen else 2)
    queries = [(tk, f"{nm} stock") for tk, nm in names().items()] + [("MARKET", q) for q in POLICY_Q.values()]
    fresh = []
    for tk, q in queries:
        for t, title, src in sw.rss(q, since):
            k = sw.news_key(title)
            if k and k not in seen:
                seen[k] = t.isoformat()
                fresh.append((t, k, tk, q, title, src))
    print(f"عناوين جديدة: {len(fresh)}")
    cls = classify([(tk, title) for _, _, tk, _, title, _ in fresh])
    rows = []
    for (t, k, tk, q, title, src), c in zip(fresh, cls):
        if c is None:
            seen.pop(k, None)   # يُعاد تصنيفه في التشغيل التالي
            continue
        rows.append({"time_utc": t.isoformat(), "key": k, "ticker": tk, "query": q, "title": title, "source": src,
                     "event": c["event"], "theme": c["theme"], "new": int(c["new"]), "importance": c["importance"],
                     "expect": c["expect"], "affected": " ".join(c["affected"])})
    rows.sort(key=lambda r: r["time_utc"])
    append(LOG, LOG_COLS, rows)
    json.dump({"seen": seen}, open(STATE, "w", encoding="utf-8"))
    print(f"سُجّل {len(rows)}؛ Claude: {USAGE['calls']} طلب، {USAGE['in']} رمز دخل، {USAGE['out']} رمز خرج")


# ============================ القياس بعد الإغلاق ============================
def load_bars(syms):
    import yfinance as yf
    d = yf.download(sorted(syms), period="60d", interval="5m", auto_adjust=True, progress=False, threads=True)
    d.index = d.index.tz_convert(TZ)
    d = d[(d.index.time >= pd.Timestamp("09:30").time()) & (d.index.time < pd.Timestamp("16:00").time())]
    return d["Open"], d["Close"]


def betas(close):
    r = close.pct_change()
    r = r[r.index.date == pd.Series(r.index.date, index=r.index).shift(1).values]   # بلا فجوات ليلية
    out = {}
    for s in close.columns:
        m = r[s].notna() & r["SPY"].notna()
        out[s] = float(np.polyfit(r["SPY"][m], r[s][m], 1)[0]) if m.sum() > 100 else 1.0
    return out


def reaction(opn, close, sym, t0, beta):
    """(overnight, ar_gap, ar30, ar60, arclose) من t0 (نيويورك). None إن لم تُغلق جلسة الحدث بعد أو لا شموع."""
    c, o, spy = close[sym].dropna(), opn[sym].dropna(), close["SPY"].dropna()
    if c.empty:
        return None
    days = sorted(set(c.index.date))
    d0 = next((d for d in days if d >= t0.date()), None)
    if d0 is None:
        return None
    day = c[c.index.date == d0]
    if day.index[-1].time() < pd.Timestamp("15:55").time():
        return None
    sday = spy[spy.index.date == d0]
    overnight = d0 > t0.date() or t0.time() < pd.Timestamp("09:30").time() or t0.time() >= pd.Timestamp("16:00").time()
    if t0.date() == d0 and t0.time() >= pd.Timestamp("16:00").time():
        nxt = [d for d in days if d > d0]
        if not nxt:
            return None
        return reaction(opn, close, sym, pd.Timestamp(datetime.combine(nxt[0], datetime.min.time())).tz_localize(TZ), beta)
    ar_gap = None
    if overnight:
        prev = [d for d in days if d < d0]
        if not prev:
            return None
        pc, ps = c[c.index.date == prev[-1]].iloc[-1], spy[spy.index.date == prev[-1]].iloc[-1]
        p0, s0 = float(o[o.index.date == d0].iloc[0]), float(opn["SPY"].dropna()[lambda x: x.index.date == d0].iloc[0])
        ar_gap = (p0 / pc - 1) - beta * (s0 / ps - 1)
        i = 0
    else:
        i = int((day.index <= t0 - pd.Timedelta(minutes=5)).sum())   # أول شمعة تنتهي بعد t0
        i = min(i, len(day) - 1)
        p0, s0 = float(day.iloc[i]), float(sday.iloc[min(i, len(sday) - 1)])

    def ar(k):
        j = min(k, len(day) - 1, len(sday) - 1)
        return (float(day.iloc[j]) / p0 - 1) - beta * (float(sday.iloc[j]) / s0 - 1)
    return overnight, ar_gap, ar(i + 6), ar(i + 12), ar(len(day) - 1)


def placebo(opn, close, sym, key, beta):
    """نفس القياس في وقت عشوائي (ثابت لكل خبر) من جلسات السهم المكتملة."""
    rng = random.Random(zlib.crc32(f"{key}|{sym}".encode()))
    days = sorted(set(close[sym].dropna().index.date))[:-1]
    for _ in range(10):
        d = rng.choice(days)
        t = pd.Timestamp(datetime.combine(d, datetime.min.time())).tz_localize(TZ) + pd.Timedelta(minutes=575 + 5 * rng.randrange(66))
        r = reaction(opn, close, sym, t, beta)
        if r:
            return r[2:]
    return None, None, None


def targets(r):
    """الأسهم التي يقاس عليها الخبر."""
    if r["ticker"] != "MARKET":
        return [r["ticker"]]
    if r["theme"] != "none" and r["new"] == "1" and r["importance"] in ("medium", "high"):
        return sorted(SECTOR) + sorted(set(SECTOR.values()))
    return []


def evaluate():
    if not os.path.exists(LOG):
        print("لا سجل بعد.")
        return
    log = list(csv.DictReader(open(LOG, encoding="utf-8")))
    done = set()
    if os.path.exists(OUT):
        done = {(r["key"], r["ticker"]) for r in csv.DictReader(open(OUT, encoding="utf-8"))}
    lo = datetime.now(timezone.utc) - timedelta(days=55)
    todo = [(r, s) for r in log for s in targets(r)
            if (r["key"], s) not in done and datetime.fromisoformat(r["time_utc"]) >= lo]
    if not todo:
        print("لا شيء جديد للقياس.")
    else:
        opn, close = load_bars({s for _, s in todo} | {"SPY"})
        beta = betas(close)
        rows = []
        for r, s in todo:
            if s not in close.columns:
                continue
            t0 = pd.Timestamp(r["time_utc"]).tz_convert(TZ)
            x = reaction(opn, close, s, t0, beta[s])
            if x is None:
                continue
            p = placebo(opn, close, s, r["key"], beta[s])
            f = lambda v: "" if v is None else round(v * 100, 3)   # نسبة مئوية
            rows.append({"key": r["key"], "ticker": s, "src_ticker": r["ticker"], "time_utc": r["time_utc"],
                         "t0": t0.isoformat(), "overnight": int(x[0]), "event": r["event"], "theme": r["theme"],
                         "new": r["new"], "importance": r["importance"], "expect": r["expect"],
                         "beta": round(beta[s], 3), "ar_gap": f(x[1]), "ar30": f(x[2]), "ar60": f(x[3]),
                         "arclose": f(x[4]), "pl30": f(p[0]), "pl60": f(p[1]), "plclose": f(p[2])})
        append(OUT, OUT_COLS, rows)
        print(f"قيس {len(rows)} من {len(todo)}")
    report()


# ============================ السوابق والتقرير ============================
def outcomes():
    if not os.path.exists(OUT):
        return pd.DataFrame(columns=OUT_COLS)
    d = pd.read_csv(OUT)
    d["total"] = d["ar_gap"].fillna(0) + d["arclose"]   # الحركة الكاملة فوق السوق (فجوة + حتى الإغلاق)
    d["day"] = pd.to_datetime(d["t0"], utc=True).dt.tz_convert(TZ).dt.date
    return d


def clusters(d):
    """خبر واحد لكل (شركة، نوع، يوم): العناوين المتعددة عن الحدث نفسه لا تُعدّ سوابق منفصلة."""
    d = d[(d["new"] == 1) & d["importance"].isin(["medium", "high"]) & (d["src_ticker"] != "MARKET")]
    return d.sort_values("t0").groupby(["ticker", "event", "day"], as_index=False).first().sort_values("t0")


def precedents(d, ticker, event, before=None):
    c = clusters(d)
    c = c[(c["ticker"] == ticker) & (c["event"] == event)]
    if before is not None:
        c = c[c["day"] < before]
    return c["total"].dropna()


def lookup_line(ticker, event, d=None):
    p = precedents(outcomes() if d is None else d, ticker, event)
    if len(p) == 0:
        return f"لا سوابق مسجلة لـ {ticker} من نوع «{EVENT_AR.get(event, event)}»."
    return (f"سابقاً: {len(p)} أخبار «{EVENT_AR.get(event, event)}» لـ {ticker}: {(p > 0).sum()} صعود و{(p <= 0).sum()} هبوط، "
            f"متوسط الحركة فوق السوق حتى الإغلاق {p.mean():+.2f}%" + (" (عينة صغيرة)" if len(p) < 10 else ""))


def precedent_test(c, key="total", min_prior=MIN_PRIOR, seed=7):
    """إصابة اتجاه متوسط السوابق، مقابل سوابق نوع آخر عشوائي وتوقع Claude. c: أخبار مجمّعة بترتيب زمني."""
    rng = random.Random(seed)
    hit, base, cl = [], [], []
    for _, r in c.iterrows():
        if pd.isna(r[key]) or r[key] == 0:
            continue
        prior = c[(c["ticker"] == r["ticker"]) & (c["day"] < r["day"])]
        same = prior[prior["event"] == r["event"]][key].dropna()
        if len(same) < min_prior:
            continue
        actual = r[key] > 0
        hit.append((same.mean() > 0) == actual)
        others = [e for e in prior["event"].unique() if e != r["event"] and (prior["event"] == e).sum() >= min_prior]
        if others:
            base.append((prior[prior["event"] == rng.choice(others)][key].mean() > 0) == actual)
        if r["expect"] in ("up", "down"):
            cl.append((r["expect"] == "up") == actual)
    return hit, base, cl


def pct(x):
    return f"{100 * np.mean(x):.0f}% ({sum(x)}/{len(x)})" if len(x) else "—"


def report():
    d = outcomes()
    L = ["# ذاكرة سوابق الأخبار (ظل)", "",
         f"آخر تحديث: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. الحركة = السهم فوق السوق (r − بيتا×SPY) بالنسبة المئوية؛ "
         "«الكاملة» = فجوة الافتتاح (لأخبار خارج الجلسة) + حتى الإغلاق. الوهمي = القياس نفسه في وقت عشوائي.", ""]
    if d.empty:
        L.append("لا قياسات بعد.")
    else:
        c = clusters(d)
        L += [f"أخبار شركات مقيسة (جديدة، أهمية متوسطة+، مجمّعة يومياً): **{len(c)}**", "",
              "| النوع | العدد | صعود | متوسط الكاملة | متوسط |الحركة| | |الوهمي| |", "|---|---|---|---|---|---|"]
        for e, g in c.groupby("event"):
            t = g["total"].dropna()
            L.append(f"| {EVENT_AR.get(e, e)} | {len(t)} | {pct(list(t > 0))} | {t.mean():+.2f}% | {t.abs().mean():.2f}% | "
                     f"{g['plclose'].abs().mean():.2f}% |")
        hit, base, cl = precedent_test(c)
        L += ["", f"## هل السوابق تتنبأ؟ (≥{MIN_PRIOR} سوابق من النوع نفسه للشركة نفسها)",
              f"- اتجاه متوسط السوابق: {pct(hit)}", f"- سوابق نوع آخر عشوائي (وهمي): {pct(base)}",
              f"- توقع Claude من العنوان: {pct(cl)}", "- 50% = عملة. لا حكم قبل ~30 حالة."]
        p = d[d["src_ticker"] == "MARKET"]
        if not p.empty:
            pc = p.sort_values("t0").groupby(["theme", "ticker", "day"], as_index=False).first()
            L += ["", "## قرارات/تصريحات ترامب: أكبر الحركات (≥3 أيام)",
                  "| الموضوع | السهم | أيام | صعود | متوسط الكاملة | |الوهمي| |", "|---|---|---|---|---|---|"]
            rows = []
            for (th, tk), g in pc.groupby(["theme", "ticker"]):
                t = g["total"].dropna()
                if len(t) >= 3:
                    rows.append((abs(t.mean()) / (g["plclose"].abs().mean() or 1), th, tk, t, g))
            for _, th, tk, t, g in sorted(rows, reverse=True)[:25]:
                L.append(f"| {th} | {tk} | {len(t)} | {pct(list(t > 0))} | {t.mean():+.2f}% | {g['plclose'].abs().mean():.2f}% |")
    open(MD, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "--collect":
        collect()
    elif a[0] == "--eval":
        evaluate()
    elif a[0] == "--report":
        report()
    elif a[0] == "--lookup" and len(a) == 3:
        print(lookup_line(a[1].upper(), a[2]))
    else:
        print(__doc__)
