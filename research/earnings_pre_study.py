#!/usr/bin/env python3
"""H-EARNPRE: هل يمكن توقع اتجاه رد فعل السهم على الأرباح مما كان معروفاً قبل الإعلان؟
(مسجّل مسبقاً في CLAUDE.md 4/11/2026 بطلب المستخدم). دراسة فقط، لا تغيّر أي بوت.

المراحل (كلها بتخزين مؤقت في SPXBOT_DATA/earnings_pre/ فتُستأنف إن انقطعت):
  events   : الإعلانات والمفاجأة والأسعار اليومية من ياهو لـ100 شركة → events.csv
  analysts : رفع/خفض التصنيف في 30 يوماً قبل كل إعلان (ياهو)
  news     : عناوين أخبار جوجل قبل الإعلان (7 أيام) وبعده (يومان)
  classify : Claude Haiku يقرأ العناوين: نبرة ما قبل الإعلان، وسبب الحركة المخالفة بعده (سقف 5$)
  report   : الفرضيات P1–P3 وP5 والوصفي → research/earnings_pre_results.md
تشغيل (الخادم): SPXBOT_DATA=/opt/spxbot/data python research/earnings_pre_study.py [--stage all|events|analysts|news|classify|report] [--limit N]
"""
import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, time as dtime, timedelta, timezone
from email.utils import parsedate_to_datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
WORK = os.path.join(DATA, "earnings_pre")
EVENTS = os.path.join(WORK, "events.csv")
NEWS_DIR = os.path.join(WORK, "news")
CLS = os.path.join(WORK, "classified.jsonl")
USAGE = os.path.join(WORK, "usage.csv")
OUT = os.path.join(HERE, "earnings_pre_results.md")
START, END = pd.Timestamp("2019-01-01"), pd.Timestamp("2026-10-24")
MODEL = os.getenv("EARNPRE_MODEL", "claude-haiku-5-5")
BUDGET_USD = float(os.getenv("EARNPRE_BUDGET_USD", "5"))
PRICES = (0.10, 0.50)          # Haiku 5.5: $/مليون رمز دخل/خرج
PRE_DAYS, POST_DAYS, ANALYST_DAYS = 7, 2, 30
MAX_HEADS = 25
N_BOOT = 3000
REASONS = ["guidance", "revenue", "priced_in", "negative_news", "positive_news", "market", "sector", "call_tone", "unclear"]
REASON_AR = {"guidance": "التوقعات المستقبلية", "revenue": "الإيرادات/جودة الأرباح", "priced_in": "صعود مسبق/بيع على الخبر",
             "negative_news": "خبر سلبي", "positive_news": "خبر إيجابي", "market": "السوق العام", "sector": "القطاع/منافس",
             "call_tone": "مكالمة الإدارة", "unclear": "غير واضح"}


# ====================== العينة ======================
def universe():
    import stocks_bot as sb
    base = list(sb.WATCHLIST)
    extra = [s for s in sb.EXTRA if s not in base][:100 - len(base)]
    names = {k: v[0] for k, v in sb.WATCHLIST.items()}
    return base + extra, names


# ====================== دوال صافية ======================
def event_index(ts, days):
    """موضع D0 في أيام التداول المرتبة: الإعلان بعد 9:30 → الجلسة التالية (قاعدة pead_test)."""
    d = ts.date()
    i = int(np.searchsorted(np.array(days), d))
    if ts.time() >= dtime(9, 30) and i < len(days) and days[i] == d:
        i += 1
    return i if 0 < i < len(days) else None


def event_row(sym, ts, est, rep, surp, px, qqq):
    """صف الإعلان من الأسعار اليومية (Open/Close بفهرس تواريخ)؛ None إن نقصت البيانات."""
    days = list(px.index)
    i = event_index(ts, days)
    if i is None or i < 22 or i + 5 >= len(days):
        return None
    c, o = px["Close"].values, px["Open"].values
    prev = c[i - 1]
    q = qqq.reindex([days[i - 1], days[i]])["Close"].values if qqq is not None else [np.nan, np.nan]
    return {"sym": sym, "ts": ts.isoformat(), "ann_day": str(ts.date()), "d0": str(days[i]), "eps_est": est, "eps_rep": rep,
            "surprise": surp, "beat": (1 if surp > 0 else (-1 if surp < 0 else 0)) if surp == surp else np.nan,
            "run20": prev / c[i - 21] - 1, "gap": o[i] / prev - 1, "r0": c[i] / prev - 1, "r5": c[i + 5] / prev - 1,
            "preA": prev / c[i - 8] - 1 if i >= 8 else np.nan, "qqq0": q[1] / q[0] - 1 if not np.isnan(q).any() else np.nan}


def analyst_net(ud, ann_ts, days=ANALYST_DAYS):
    """(رفع − خفض، عدد التغييرات) في days يوماً قبل الإعلان. ud: إطار upgrades_downgrades بفهرس GradeDate."""
    if ud is None or not len(ud):
        return 0, 0
    t1 = pd.Timestamp(ann_ts).tz_localize(None) if pd.Timestamp(ann_ts).tzinfo else pd.Timestamp(ann_ts)
    w = ud[(ud.index < t1) & (ud.index >= t1 - pd.Timedelta(days=days))]
    act = w["Action"].astype(str).str.lower() if "Action" in w else pd.Series(dtype=str)
    return int((act == "up").sum() - (act == "down").sum()), int(len(w))


def parse_rss(xml_text, limit=MAX_HEADS):
    """[(وقت UTC، عنوان)] من RSS أخبار جوجل."""
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        pub = it.findtext("pubDate")
        try:
            t = parsedate_to_datetime(pub).astimezone(timezone.utc) if pub else None
        except Exception:  # noqa: BLE001
            t = None
        if title:
            out.append((t.isoformat() if t else "", title))
        if len(out) >= limit:
            break
    return out


def pre_only(heads, ann_day):
    """منع التسرب: يُبقي عناوين ما قبل الإعلان المؤرخة قبل اليوم السابق للإعلان (تواريخ جوجل تقريبية بيوم)."""
    cut = (pd.Timestamp(ann_day) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    return [(t, h) for t, h in heads if t and t[:10] < cut]


def gnews_url(query, after, before):
    q = f"{query} after:{after} before:{before}"
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})


def boot_diff(a, b, n=N_BOOT, seed=0):
    """فرق المتوسطين (a − b) مع فاصل 95% بإعادة معاينة أيام الإعلان. a، b: إطاران فيهما day وy."""
    u = np.unique(np.r_[a["day"].values, b["day"].values])
    if len(u) < 5 or not len(a) or not len(b):
        return np.nan, (np.nan, np.nan), 1.0
    ba = {d: g["y"].values for d, g in a.groupby("day")}
    bb = {d: g["y"].values for d, g in b.groupby("day")}
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        s = rng.choice(u, len(u))
        xa = np.concatenate([ba.get(d, np.array([])) for d in s])
        xb = np.concatenate([bb.get(d, np.array([])) for d in s])
        if len(xa) and len(xb):
            out.append(xa.mean() - xb.mean())
    out = np.array(out)
    return float(a["y"].mean() - b["y"].mean()), tuple(np.percentile(out, [2.5, 97.5])), float((out <= 0).mean())


def boot_mean(v, days, n=N_BOOT, seed=1):
    v, days = np.asarray(v, float), np.asarray(days)
    u = np.unique(days)
    if len(u) < 5:
        return float(np.mean(v)) if len(v) else np.nan, (np.nan, np.nan)
    by = {d: v[days == d] for d in u}
    rng = np.random.default_rng(seed)
    ms = [np.mean(np.concatenate([by[d] for d in rng.choice(u, len(u))])) for _ in range(n)]
    return float(np.mean(v)), tuple(np.percentile(ms, [2.5, 97.5]))


def bh(ps, q=0.05):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    k = 0
    for r, i in enumerate(order, 1):
        if ps[i] <= q * r / len(ps):
            k = r
    ok = [False] * len(ps)
    for r, i in enumerate(order, 1):
        ok[i] = r <= k
    return ok


def composite(tone, anet, run20, lo, hi):
    """اتجاه P5: إشارة (نبرة الأخبار + إشارة المحللين − 1 لصعود مسبق في الثلث الأعلى + 1 للأدنى)."""
    s = (np.sign(tone) if tone == tone else 0) + np.sign(anet)
    if run20 >= hi:
        s -= 1
    elif run20 <= lo:
        s += 1
    return int(np.sign(s))


# ====================== المراحل ======================
def stage_events(limit=None):
    import yfinance as yf
    syms, _ = universe()
    syms = syms[:limit] if limit else syms
    qqq = yf.Ticker("QQQ").history(start="2018-06-01", auto_adjust=True)[["Open", "Close"]]
    qqq.index = qqq.index.tz_localize(None).date
    rows = []
    for s in syms:
        try:
            t = yf.Ticker(s)
            e = t.get_earnings_dates(limit=40)
            px = t.history(start="2018-06-01", auto_adjust=True)[["Open", "Close"]]
            px.index = px.index.tz_localize(None).date
        except Exception as ex:  # noqa: BLE001
            print("تخطي", s, type(ex).__name__)
            continue
        if e is None or not len(e) or not len(px):
            continue
        for ts, r in e.iterrows():
            tsn = pd.Timestamp(ts).tz_convert("America/New_York").tz_localize(None)
            if not (START <= tsn <= END) or pd.isna(r.get("Reported EPS")):
                continue
            row = event_row(s, tsn, r.get("EPS Estimate"), r.get("Reported EPS"), r.get("Surprise(%)"), px, qqq)
            if row:
                rows.append(row)
        print(s, len(rows), flush=True)
        time.sleep(0.3)
    df = pd.DataFrame(rows)
    os.makedirs(WORK, exist_ok=True)
    df.to_csv(EVENTS, index=False)
    return df


def stage_analysts():
    import yfinance as yf
    ev = pd.read_csv(EVENTS)
    nets, cnts = [], []
    for s, g in ev.groupby("sym", sort=False):
        try:
            ud = yf.Ticker(s).upgrades_downgrades
            if ud is not None and len(ud):
                idx = pd.to_datetime(ud.index)
                ud.index = idx.tz_localize(None) if idx.tz is not None else idx
        except Exception:  # noqa: BLE001
            ud = None
        for i, r in g.iterrows():
            nets.append((i, *analyst_net(ud, r["ts"])))
        time.sleep(0.3)
    for i, n, c in nets:
        ev.at[i, "analyst_net"], ev.at[i, "analyst_n"] = n, c
    ev.to_csv(EVENTS, index=False)
    return ev


def _fetch(url, tries=4):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research)"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", None)
            time.sleep(30 * (k + 1) if code == 429 else 3 * (k + 1))
    return None


def stage_news(names=None):
    ev = pd.read_csv(EVENTS)
    _, nm = universe()
    os.makedirs(NEWS_DIR, exist_ok=True)
    done = fail = 0
    for r in ev.itertuples():
        path = os.path.join(NEWS_DIR, f"{r.sym}_{r.ann_day}.json")
        if os.path.exists(path):
            continue
        d = pd.Timestamp(r.ann_day)
        name = nm.get(r.sym, r.sym)
        q = f'"{r.sym}" OR "{name}" stock'
        pre = _fetch(gnews_url(q, (d - pd.Timedelta(days=PRE_DAYS + 1)).date(), (d - pd.Timedelta(days=1)).date()))
        post = _fetch(gnews_url(q, d.date(), (d + pd.Timedelta(days=POST_DAYS + 1)).date()))
        if pre is None and post is None:
            fail += 1
            continue
        json.dump({"pre": pre_only(parse_rss(pre or ""), r.ann_day), "post": parse_rss(post or "")},
                  open(path, "w", encoding="utf-8"), ensure_ascii=False)
        done += 1
        if done % 50 == 0:
            print("أخبار:", done, "فشل:", fail, flush=True)
        time.sleep(1.2)
    print("أخبار انتهت:", done, "فشل:", fail)


# ---------------- Claude ----------------
SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {
    "type": "object",
    "properties": {"i": {"type": "integer"}, "tone": {"type": "integer"},
                   "expect": {"type": "string", "enum": ["up", "down", "unclear"]},
                   "reason": {"type": "string", "enum": REASONS}, "explain_ar": {"type": "string"}},
    "required": ["i", "tone", "expect", "reason", "explain_ar"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False}


def spent():
    try:
        return float(pd.read_csv(USAGE)["usd"].sum())
    except Exception:  # noqa: BLE001
        return 0.0


def prompt(batch):
    parts = []
    for k, (r, n) in enumerate(batch):
        pre = "\n".join(f"  - {t}" for _, t in n["pre"][:MAX_HEADS]) or "  (لا عناوين)"
        post = "\n".join(f"  - {t}" for _, t in n["post"][:15]) or "  (لا عناوين)"
        beat = "فوق التوقع" if r.beat > 0 else ("تحت التوقع" if r.beat < 0 else "مطابق")
        parts.append(f"### {k}. {r.sym} — إعلان {r.ann_day} (أرباح {beat})، رد فعل السهم {r.r0 * 100:+.1f}%\n"
                     f"عناوين الأسبوع السابق للإعلان:\n{pre}\nعناوين بعد الإعلان:\n{post}")
    return ("أنت محلل أسهم. لكل إعلان أرباح مرقّم: "
            "tone = نبرة عناوين الأسبوع السابق للإعلان فقط تجاه الشركة (−2 سلبية جداً … +2 إيجابية جداً؛ 0 محايدة أو لا عناوين)؛ "
            "expect = الاتجاه المتوقع لرد الفعل بناءً على عناوين ما قبل الإعلان فقط (لا تنظر لما بعده)؛ "
            "reason = السبب الأرجح لرد فعل السهم من عناوين ما بعد الإعلان (اختر من القائمة؛ unclear إن لم يتضح)؛ "
            "explain_ar = جملة عربية قصيرة تشرح السبب. لا تخترع معلومات غير موجودة في العناوين.\n\n" + "\n\n".join(parts))


def call_claude(text, client):
    r = client.messages.create(model=MODEL, max_tokens=4000, messages=[{"role": "user", "content": text}],
                               output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}})
    u_in, u_out = getattr(r.usage, "input_tokens", 0) or 0, getattr(r.usage, "output_tokens", 0) or 0
    usd = (u_in * PRICES[0] + u_out * PRICES[1]) / 1e6
    new = not os.path.exists(USAGE)
    with open(USAGE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "input", "output", "usd", "stop"])
        w.writerow([datetime.now(timezone.utc).isoformat(timespec="seconds"), u_in, u_out, round(usd, 6), r.stop_reason])
    if r.stop_reason != "end_turn":
        return None
    return json.loads(next(b.text for b in r.content if getattr(b, "type", "") == "text"))["items"]


def stage_classify(client=None, batch_size=6):
    ev = pd.read_csv(EVENTS)
    done = set()
    if os.path.exists(CLS):
        done = {json.loads(l)["key"] for l in open(CLS, encoding="utf-8") if l.strip()}
    if client is None:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=120.0)
    todo = []
    for r in ev.itertuples():
        key = f"{r.sym}_{r.ann_day}"
        p = os.path.join(NEWS_DIR, f"{key}.json")
        if key in done or not os.path.exists(p):
            continue
        n = json.load(open(p, encoding="utf-8"))
        if n["pre"] or n["post"]:
            todo.append((r, n))
    print("للقراءة:", len(todo), "المنفق حتى الآن:", round(spent(), 3), flush=True)
    for s in range(0, len(todo), batch_size):
        if spent() >= BUDGET_USD:
            print("بُلغ سقف Claude؛ توقف القراءة.")
            break
        batch = todo[s:s + batch_size]
        try:
            items = call_claude(prompt(batch), client)
        except Exception as e:  # noqa: BLE001
            print("فشل طلب:", type(e).__name__, str(e)[:200])
            time.sleep(5)
            continue
        with open(CLS, "a", encoding="utf-8") as f:
            for it in items or []:
                k = it.get("i", -1)
                if 0 <= k < len(batch):
                    r = batch[k][0]
                    it["tone"] = int(max(-2, min(2, int(it.get("tone", 0)))))
                    f.write(json.dumps({"key": f"{r.sym}_{r.ann_day}", **{x: it[x] for x in ("tone", "expect", "reason", "explain_ar")}},
                                       ensure_ascii=False) + "\n")
        if (s // batch_size) % 25 == 0:
            print("قُرئ:", s + len(batch), "المنفق:", round(spent(), 3), flush=True)


# ---------------- التقرير ----------------
TONE_BLIND = os.path.join(WORK, "tone_blind.jsonl")
BLIND_SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {
    "type": "object", "properties": {"i": {"type": "integer"}, "tone": {"type": "integer"},
                                     "expect": {"type": "string", "enum": ["up", "down", "unclear"]}},
    "required": ["i", "tone", "expect"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False}


def blind_prompt(batch):
    """نبرة ما قبل الإعلان **عمياء**: عناوين الأسبوع السابق فقط، بلا نتيجة الأرباح ولا رد الفعل ولا عناوين ما بعده (منع التسرب)."""
    parts = []
    for k, (r, n) in enumerate(batch):
        pre = "\n".join(f"  - {t}" for _, t in n["pre"][:MAX_HEADS]) or "  (لا عناوين)"
        parts.append(f"### {k}. {r.sym} — إعلان أرباح قادم بتاريخ {r.ann_day}\nعناوين الأسبوع السابق:\n{pre}")
    return ("أنت محلل أسهم قبل إعلان الأرباح؛ لا تعرف النتيجة. لكل شركة مرقّمة: tone = نبرة العناوين تجاه الشركة "
            "(−2 سلبية جداً … +2 إيجابية جداً؛ 0 محايدة أو لا عناوين ذات صلة)؛ expect = الاتجاه الأرجح لرد فعل السهم بعد الإعلان "
            "بناءً على هذه العناوين وحدها (unclear إن لم تكفِ). لا تستعمل أي معرفة عن نتائج هذه الإعلانات.\n\n" + "\n\n".join(parts))


def stage_tone_blind(client=None, batch_size=8):
    ev = pd.read_csv(EVENTS)
    done = set()
    if os.path.exists(TONE_BLIND):
        done = {json.loads(l)["key"] for l in open(TONE_BLIND, encoding="utf-8") if l.strip()}
    if client is None:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=120.0)
    todo = []
    for r in ev.itertuples():
        key = f"{r.sym}_{r.ann_day}"
        p = os.path.join(NEWS_DIR, f"{key}.json")
        if key in done or not os.path.exists(p):
            continue
        n = json.load(open(p, encoding="utf-8"))
        if n["pre"]:
            todo.append((r, n))
    print("نبرة عمياء للقراءة:", len(todo), flush=True)
    for s in range(0, len(todo), batch_size):
        if spent() >= BUDGET_USD:
            print("بُلغ سقف Claude؛ توقف القراءة.")
            break
        batch = todo[s:s + batch_size]
        try:
            r_ = client.messages.create(model=MODEL, max_tokens=2000, messages=[{"role": "user", "content": blind_prompt(batch)}],
                                        output_config={"effort": "low", "format": {"type": "json_schema", "schema": BLIND_SCHEMA}})
        except Exception as e:  # noqa: BLE001
            print("فشل طلب:", type(e).__name__, str(e)[:200])
            time.sleep(5)
            continue
        u_in, u_out = getattr(r_.usage, "input_tokens", 0) or 0, getattr(r_.usage, "output_tokens", 0) or 0
        with open(USAGE, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([datetime.now(timezone.utc).isoformat(timespec="seconds"), u_in, u_out,
                                    round((u_in * PRICES[0] + u_out * PRICES[1]) / 1e6, 6), r_.stop_reason])
        if r_.stop_reason != "end_turn":
            continue
        items = json.loads(next(b.text for b in r_.content if getattr(b, "type", "") == "text"))["items"]
        with open(TONE_BLIND, "a", encoding="utf-8") as f:
            for it in items:
                k = it.get("i", -1)
                if 0 <= k < len(batch):
                    r = batch[k][0]
                    f.write(json.dumps({"key": f"{r.sym}_{r.ann_day}", "tone_b": int(max(-2, min(2, int(it.get("tone", 0))))),
                                        "expect_b": it.get("expect", "unclear")}, ensure_ascii=False) + "\n")


def load_all():
    ev = pd.read_csv(EVENTS)
    ev["key"] = ev["sym"] + "_" + ev["ann_day"]
    if os.path.exists(CLS):
        c = pd.DataFrame([json.loads(l) for l in open(CLS, encoding="utf-8") if l.strip()]).drop_duplicates("key", keep="last")
        ev = ev.merge(c, on="key", how="left")
    if os.path.exists(TONE_BLIND):                 # النبرة العمياء تحل محل نبرة القراءة المشتركة (فيها تسرب: رأى النموذج رد الفعل)
        b = pd.DataFrame([json.loads(l) for l in open(TONE_BLIND, encoding="utf-8") if l.strip()]).drop_duplicates("key", keep="last")
        ev = ev.merge(b, on="key", how="left")
        ev["tone_seen"] = ev.get("tone")
        ev["tone"], ev["expect"] = ev["tone_b"], ev["expect_b"]
    for col in ("tone", "expect", "reason", "explain_ar", "analyst_net"):
        if col not in ev:
            ev[col] = np.nan
    ev["day"] = ev["d0"]
    return ev


def hyp(name, a, b, halves_fn, sign=1):
    """صف فرضية: الفرق (a − b) بالإشارة المطلوبة."""
    d, (lo, hi), p = boot_diff(a, b)
    if sign < 0:
        d, lo, hi, p = -d, -hi, -lo, 1 - p if p == p else p
    h = halves_fn()
    return {"name": name, "d": d, "lo": lo, "hi": hi, "p": p, "h": h, "na": len(a), "nb": len(b)}


def report(ev):
    ev = ev.dropna(subset=["r0"]).copy()
    days = sorted(ev["day"].unique())
    mid = days[len(days) // 2]
    pick = lambda df, cond: df[cond].assign(y=df.loc[cond, "r0"])
    L = ["# H-EARNPRE: توقع رد فعل السهم على الأرباح مما كان معروفاً قبل الإعلان (نتائج آلية)", "",
         f"- {ev['sym'].nunique()} شركة، {len(ev)} إعلاناً ({ev['day'].min()} → {ev['day'].max()}). قُرئت أخبار {ev['tone'].notna().sum()} إعلاناً بـClaude "
         f"(المنفق {spent():.2f}$). نبرة ما قبل الإعلان **عمياء** (لا يرى النموذج النتيجة ولا رد الفعل).", "- r0 = رد فعل السهم يوم D0 منذ آخر إغلاق قبل الإعلان؛ النتائج حركة السهم لا العقد.", "",
         "## صورة عامة", "", "| المجموعة | إعلانات | رد الفعل r0 | صعد | +5 أيام | قبل الإعلان (النسخة أ) |", "|---|---|---|---|---|---|"]
    row = lambda name, g: L.append(f"| {name} | {len(g)} | {g['r0'].mean() * 100:+.2f}% | {(g['r0'] > 0).mean() * 100:.0f}% | "
                                   f"{g['r5'].mean() * 100:+.2f}% | {g['preA'].mean() * 100:+.2f}% |")
    row("كل الإعلانات", ev)
    row("فوق التوقع", ev[ev["beat"] > 0])
    row("تحت التوقع", ev[ev["beat"] < 0])
    bf = ev[(ev["beat"] > 0) & (ev["r0"] < 0)]
    L.append(f"\n- **فوق التوقع وهبط:** {len(bf)} من {int((ev['beat'] > 0).sum())} ({len(bf) / max(1, (ev['beat'] > 0).sum()) * 100:.0f}%). "
             f"**تحت التوقع وصعد:** {int(((ev['beat'] < 0) & (ev['r0'] > 0)).sum())} من {int((ev['beat'] < 0).sum())}.")
    # P1
    b = ev[ev["beat"] > 0]
    lo_t, hi_t = b["run20"].quantile(1 / 3), b["run20"].quantile(2 / 3)
    top, bot = pick(b, b["run20"] >= hi_t), pick(b, b["run20"] <= lo_t)
    h1 = lambda: [x[x["run20"] >= hi_t]["r0"].mean() - x[x["run20"] <= lo_t]["r0"].mean() for x in (b[b["day"] < mid], b[b["day"] >= mid])]
    rows = [hyp("P1 الصعود المسبق (فوق التوقع: الأعلى صعوداً − الأدنى؛ المتوقع سالب)", top, bot, h1, sign=-1)]
    # P2
    t = ev[ev["tone"].notna()]
    pos, neg = pick(t, t["tone"] > 0), pick(t, t["tone"] < 0)
    h2 = lambda: [x[x["tone"] > 0]["r0"].mean() - x[x["tone"] < 0]["r0"].mean() for x in (t[t["day"] < mid], t[t["day"] >= mid])]
    rows.append(hyp("P2 نبرة الأخبار قبل الإعلان (موجبة − سالبة)", pos, neg, h2))
    # P3
    a = ev[ev["analyst_net"].notna()]
    up, dn = pick(a, a["analyst_net"] > 0), pick(a, a["analyst_net"] < 0)
    h3 = lambda: [x[x["analyst_net"] > 0]["r0"].mean() - x[x["analyst_net"] < 0]["r0"].mean() for x in (a[a["day"] < mid], a[a["day"] >= mid])]
    rows.append(hyp("P3 المحللون قبل الإعلان (رفع − خفض)", up, dn, h3))
    ok = bh([r_["p"] for r_ in rows])
    L += ["", "## الفرضيات P1–P3 (BH بمعدل 5%)", "", "| الفرضية | المجموعتان | الفرق | فاصل 95% | النصفان | الحكم |", "|---|---|---|---|---|---|"]
    for r_, o in zip(rows, ok):
        halves_ok = all(x < 0 for x in r_["h"]) if r_["name"].startswith("P1") else all(x > 0 for x in r_["h"])
        good = o and r_["lo"] > 0 and halves_ok
        L.append(f"| {r_['name']} | {r_['na']} / {r_['nb']} | {r_['d'] * 100:+.2f} | [{r_['lo'] * 100:+.2f}، {r_['hi'] * 100:+.2f}] | "
                 f"{r_['h'][0] * 100:+.2f} / {r_['h'][1] * 100:+.2f} | {'تحققت ✅' if good else 'لم تتحقق'} |")
    # P5 خارج العينة
    ev = ev.sort_values("day")
    cut = ev["day"].iloc[int(len(ev) * 0.7)]
    train, test = ev[ev["day"] < cut], ev[ev["day"] >= cut].copy()
    lo5, hi5 = train["run20"].quantile(1 / 3), train["run20"].quantile(2 / 3)
    test["dir"] = [composite(r.tone, r.analyst_net if r.analyst_net == r.analyst_net else 0, r.run20, lo5, hi5) for r in test.itertuples()]
    s5 = test[test["dir"] != 0]
    m5, (l5, u5) = boot_mean(s5["dir"] * s5["r0"], s5["day"]) if len(s5) else (np.nan, (np.nan, np.nan))
    L += ["", f"## P5 المركّب (يُقاس على آخر 30% زمنياً فقط: من {cut})", "",
          f"- إعلانات لها اتجاه: {len(s5)} | متوسط r0 بإشارة الاتجاه: {m5 * 100:+.2f}% [{l5 * 100:+.2f}، {u5 * 100:+.2f}] | "
          f"أصاب الاتجاه: {(np.sign(s5['r0']) == s5['dir']).mean() * 100:.0f}% → **{'تحققت ✅' if l5 > 0 else 'لم تتحقق'}**",
          f"- وصفي بالاتجاه نفسه: النسخة أ (قبل الإعلان) {(s5['dir'] * s5['preA']).mean() * 100:+.2f}% | النسخة ب (+5 أيام) "
          f"{(s5['dir'] * s5['r5']).mean() * 100:+.2f}%"]
    # مكتبة الأسباب
    odd = ev[((ev["beat"] > 0) & (ev["r0"] < 0)) | ((ev["beat"] < 0) & (ev["r0"] > 0))]
    odd = odd[odd["reason"].notna()]
    L += ["", "## مكتبة الأسباب: لماذا خالف السهم الأرباح؟ (من عناوين ما بعد الإعلان؛ وصفي)", "",
          "| السبب | فوق التوقع وهبط | تحت التوقع وصعد |", "|---|---|---|"]
    for k in REASONS:
        L.append(f"| {REASON_AR[k]} | {int(((odd['beat'] > 0) & (odd['reason'] == k)).sum())} | {int(((odd['beat'] < 0) & (odd['reason'] == k)).sum())} |")
    ex = odd[odd["beat"] > 0].reindex(odd[odd["beat"] > 0]["r0"].abs().sort_values(ascending=False).index).head(12)
    if len(ex):
        L += ["", "### أوضح أمثلة «فوق التوقع وهبط»", ""]
        for r in ex.itertuples():
            L.append(f"- **{r.sym} {r.ann_day}** (مفاجأة {r.surprise:+.1f}%، رد الفعل {r.r0 * 100:+.1f}%، صعود مسبق {r.run20 * 100:+.1f}%): "
                     f"{REASON_AR.get(r.reason, r.reason)} — {r.explain_ar}")
    L += ["", "تحفظ: حركة السهم ليست ربح العقد؛ أخبار جوجل التاريخية ناقصة التغطية؛ أسباب ما بعد الإعلان تفسير لاحق لا يُستعمل للتوقع."]
    return "\n".join(L) + "\n"


def stage_report():
    md = report(load_all())
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


def main(argv):
    stage = argv[argv.index("--stage") + 1] if "--stage" in argv else "all"
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    os.makedirs(WORK, exist_ok=True)
    if stage in ("all", "events"):
        stage_events(limit)
    if stage in ("all", "analysts"):
        stage_analysts()
    if stage in ("all", "news"):
        stage_news()
    if stage in ("all", "classify"):
        stage_classify()
    if stage in ("all", "tone_blind"):
        stage_tone_blind()
    if stage in ("all", "report"):
        stage_report()


if __name__ == "__main__":
    main(sys.argv[1:])
