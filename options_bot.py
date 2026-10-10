"""بوت إشارات خيارات SPX لنفس اليوم (0DTE): شراء Call أو Put -> تليجرام (لا ينفذ أي أوامر)

الرسالة الأولى: توصية مختصرة (كول أو بوت) مع عقود الدخول ملونة.
الرسالة الثانية: كل التفاصيل (الاتجاه، السيولة، الأخبار، الأحداث، خطة الإدارة).
"""

import argparse
import copy
import html
import json
import math
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import yfinance as yf

# ====================== الإعدادات ======================
SYMBOL = "^SPX"             # يمكنك تغييره إلى "SPY"
NDX_SYMBOL = "^NDX"         # ناسداك 100 (NDXP): نفس اتجاه SPX بعقود أرخص بعيدة عن السعر (اتركها "" لتعطيلها)
NDX_MIN_OI, NDX_MIN_VOL, NDX_MAX_SPREAD = 20, 50, 0.35   # سيولته أقل من SPX فنخفف الشروط
ALT_SYMBOL = "SPY"          # أداة بديلة: خيارات SPY بنفس الاتجاه (اتركها "" لتعطيلها)
FLOW_SYMBOL = "SPY"         # لحساب VWAP (المؤشر نفسه بلا حجم تداول)
NEWS_SYMBOL = "SPY"
CONTRACT_MIN_USD = 100      # أقل سعر للعقد (دولار)
CONTRACT_MAX_USD = 300      # أعلى سعر للعقد (دولار)
MIN_OI = 150                # أقل فائدة مفتوحة (أو حجم تداول 200)
MIN_VOL = 200
MAX_SPREAD = 0.30           # أقصى فرق مقبول بين الشراء والبيع (نسبة من المتوسط)
MIN_EDGE = 3.0              # أقل فرق بين نقاط الصعود والهبوط لإعطاء توصية
MIN_CONTRACT_SCORE = 5.0    # أقل درجة للعقد
MAX_CONTRACTS = 3           # عدد العقود المعروضة
TAKE_PROFIT = 0.50          # جني الربح عند +50% من سعر الدخول
STOP_LOSS = 0.40            # وقف الخسارة عند -40%
REPEAT_MINUTES = 90         # لا نكرر نفس الاتجاه قبل هذه المدة
RISK_BUDGET_USD = 150       # أقصى خسارة تقبلها في الصفقة الواحدة عند الوقف (عدّلها حسب حسابك)
MAX_QTY = 5                 # سقف عدد العقود المقترح
HOLD_MIN = 30               # افتراض مدة الاحتفاظ بالعقد لحساب مستوى هدف المؤشر (دقيقة)
WINDOW_START = (9, 45)      # نافذة التشغيل بتوقيت نيويورك
WINDOW_END = (16, 0)
NO_NEW_ENTRY = (15, 0)      # لا دخول جديد بعد هذا الوقت
RISK_FREE = 0.04
WALL_RANGE = 0.03
STATE_FILE = "bot_state.json"
POS_FILE = "spx_positions.json"      # إشارات اليوم المفتوحة للمتابعة
TRACK_FILE = "spx_track.csv"         # تتبع صامت لكل عقد حتى بعد الوقف
MIN_AGE_MIN = 15                     # لا تنبيه وقف قبل مرور هذه المدة (فرق السعر وحده قد يخدع)
GIVEBACK = 0.50                      # بعد بلوغ الهدف: تنبيه إذا تراجع الربح لنصف أعلى ربح
LOG_FILE = "zero_dte_log.csv"

NY = ZoneInfo("America/New_York")
RY = ZoneInfo("Asia/Riyadh")

# تواريخ قرار الفيدرالي 2026 (تحقق منها). أضف أحداثاً أخرى مثل CPI هنا:
EVENTS = {
    "2026-10-28": "قرار الفيدرالي (FOMC)",
    "2026-12-09": "قرار الفيدرالي (FOMC)",
    # "2026-10-14": "تقرير التضخم CPI",
}
RISK_WORDS = ["crash", "plunge", "selloff", "sell-off", "tumble", "recession", "default",
              "shutdown", "downgrade", "panic", "rate hike", "bank failure", "slump", "rout"]
GEO_RE = re.compile(r"\b(war|missile|missiles|attack|attacks|iran|israel|gaza|ukraine|russia|china|taiwan|"
                    r"sanction|sanctions|tariff|tariffs|embargo|military|nuclear|ceasefire|troops|houthi|"
                    r"red sea|opec|strait of hormuz|geopolitical)\b", re.I)
LEADERS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO"]
MARKET_QUERIES = ["stock market today", "Wall Street stocks S&P 500", "Nvidia Apple Microsoft stocks"]
GEO_QUERY = "geopolitical tensions markets"

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
LINE = "━━━━━━━━━━━━━━━"


def esc(x):
    return html.escape(str(x))


def now_ny():
    return datetime.now(NY)


# عطلات البورصة الأمريكية (تُراجَع سنوياً) وأيام الإغلاق المبكر 1:00 ظهراً بتوقيت نيويورك
HOLIDAYS = {"2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
            "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"}
EARLY_CLOSE = {"2026-11-27": (13, 0), "2026-12-24": (13, 0)}


def market_day(n):
    return n.weekday() < 5 and n.date().isoformat() not in HOLIDAYS


def session_end(n):
    return EARLY_CLOSE.get(n.date().isoformat(), WINDOW_END)


def no_entry(n):
    """آخر وقت للدخول الجديد: قبل الإغلاق بساعة (وفي الإغلاق المبكر قبل 1:00 بساعة)."""
    end = session_end(n)
    return min(NO_NEW_ENTRY, (end[0] - 1, end[1]))


def in_window(n):
    return market_day(n) and WINDOW_START <= (n.hour, n.minute) <= session_end(n)


def ry_time(h, m):
    """يحوّل وقتاً بتوقيت نيويورك (اليوم) إلى توقيت الرياض للعرض."""
    t = now_ny().replace(hour=h, minute=m, second=0, microsecond=0)
    return t.astimezone(RY).strftime("%I:%M %p").replace("AM", "ص").replace("PM", "م")


# ====================== تليجرام ======================
def log_message(text):
    """يحفظ نص كل رسالة في ملف خاص بكل بوت (لتحليل التوصيات لاحقاً). لا يؤثر على الإرسال إن فشل."""
    try:
        src = os.path.basename(sys.argv[0]).replace(".py", "") or "bot"
        rec = {"t": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), "text": text}
        with open(f"messages_{src}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print("تعذر حفظ الرسالة:", e)


def _post(method, data):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/{method}", data=data, timeout=15)
        return r
    except requests.RequestException as e:
        print("تعذر الإرسال:", e)
        return None


def _plain(text):
    return re.sub(r"</?(?:b|i|u|s|code|pre|blockquote)[^>]*>", "", text)


def send_telegram(text, reply_to=None, full_text=None):
    """يرسل رسالة (ويقسمها إن طالت). يرجع رقم أول رسالة (للرد عليها أو تحريرها لاحقاً)."""
    print(text)
    print()
    log_message(full_text or text)
    if not TOKEN or not CHAT_ID:
        print("[تنبيه] لم تُضبط TELEGRAM_TOKEN / TELEGRAM_CHAT_ID، فلم تُرسل الرسالة.")
        return None
    parts, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) + 2 > 3800 and cur:
            parts.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    parts.append(cur)
    first = None
    for idx, p in enumerate(parts):
        data = {"chat_id": CHAT_ID, "text": p, "parse_mode": "HTML"}
        if reply_to and idx == 0:
            data["reply_to_message_id"] = reply_to
            data["allow_sending_without_reply"] = "true"
        r = _post("sendMessage", data)
        if r is not None and not r.ok and "parse" in r.text.lower():
            data.pop("parse_mode")
            data["text"] = _plain(p)                  # لا نخسر الرسالة بسبب خطأ تنسيق
            r = _post("sendMessage", data)
        if r is None or not r.ok:
            print("خطأ من تليجرام:", None if r is None else r.text)
            continue
        if first is None:
            try:
                first = r.json()["result"]["message_id"]
            except Exception:
                pass
    return first


def edit_telegram(mid, text):
    """يحرر رسالة سابقة بدل إرسال جديدة. يرجع True عند النجاح."""
    print("[تحرير رسالة]", text)
    log_message("[تحرير] " + text)
    if not TOKEN or not CHAT_ID or not mid:
        return False
    r = _post("editMessageText", {"chat_id": CHAT_ID, "message_id": mid, "text": text, "parse_mode": "HTML"})
    return bool(r is not None and r.ok)


def send_photo(png, caption="", reply_to=None):
    """يرسل صورة PNG مع تعليق قصير. يرجع رقم الرسالة أو None."""
    print("[صورة]", caption)
    log_message(f"[صورة] {caption}")
    if not TOKEN or not CHAT_ID or not png:
        return None
    data = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}
    if reply_to:
        data["reply_to_message_id"] = reply_to
        data["allow_sending_without_reply"] = "true"
    try:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto", data=data,
                          files={"photo": ("card.png", png, "image/png")}, timeout=40)
        if not r.ok:
            print("خطأ من تليجرام:", r.text)
            return None
        return r.json()["result"]["message_id"]
    except Exception as e:
        print("تعذر إرسال الصورة:", e)
        return None


COLLAPSE_LIMIT = 3300


def collapsed(body):
    """يلف النص في اقتباس قابل للطي (يظهر سطرين ويُفتح بالضغط)."""
    b = body if len(body) <= COLLAPSE_LIMIT else body[:COLLAPSE_LIMIT].rsplit("\n", 1)[0] + "\n…"
    return f"<blockquote expandable>{b}</blockquote>"


def analysis_wrap(body):
    """رسالة التحليل: عنوان واضح وتفاصيل مطوية. السجل يحفظ النص كاملاً."""
    text = "📊 <b>التحليل التفصيلي</b> #تحليل\n⬇️ اضغط على النص لفتحه\n" + collapsed(body)
    return text, "📊 التحليل التفصيلي\n" + body


# ====================== رياضيات ======================
def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _d1(S, K, T, s):
    return (math.log(S / K) + (RISK_FREE + 0.5 * s * s) * T) / (s * math.sqrt(T))


def bs_gamma(S, K, T, s):
    if s <= 0 or T <= 0:
        return 0.0
    return norm_pdf(_d1(S, K, T, s)) / (S * s * math.sqrt(T))


def abs_delta(S, K, T, s, side):
    d1 = _d1(S, K, T, s)
    return norm_cdf(d1) if side == "CALL" else norm_cdf(-d1)


def prob_beyond(S, be, T, s, side):
    d2 = (math.log(S / be) + (RISK_FREE - 0.5 * s * s) * T) / (s * math.sqrt(T))
    return norm_cdf(d2) if side == "CALL" else norm_cdf(-d2)


def bs_price(S, K, T, s, side):
    d1 = _d1(S, K, T, s)
    d2 = d1 - s * math.sqrt(T)
    disc = math.exp(-RISK_FREE * T)
    if side == "CALL":
        return S * norm_cdf(d1) - K * disc * norm_cdf(d2)
    return K * disc * norm_cdf(-d2) - S * norm_cdf(-d1)


# ====================== بيانات السوق ======================
def vix_info():
    try:
        h = yf.Ticker("^VIX").history(period="1y")["Close"].dropna()
        if len(h) < 50:
            return None, None
        last = float(h.iloc[-1])
        return last, float((h < last).mean() * 100)
    except Exception:
        return None, None


def upcoming_events(today, days=7):
    ev = []
    for d, n in EVENTS.items():
        dd = date.fromisoformat(d)
        if 0 <= (dd - today).days <= days:
            ev.append((dd, n))
    for off in (0, 1):  # تقرير الوظائف: أول جمعة في الشهر (تقريبي)
        y = today.year + (today.month - 1 + off) // 12
        m = (today.month - 1 + off) % 12 + 1
        d1 = date(y, m, 1)
        ff = d1 + timedelta(days=(4 - d1.weekday()) % 7)
        if 0 <= (ff - today).days <= days:
            ev.append((ff, "تقرير الوظائف (تقريبي)"))
    return sorted(ev)


def liquidity_map(t, S, exps):
    """جدران السيولة وGEX التقريبي من الفائدة المفتوحة لتواريخ الانتهاء القريبة."""
    calls, puts, gex, used = {}, {}, 0.0, 0
    for e, dte in exps:
        try:
            ch = t.option_chain(e)
        except Exception:
            continue
        used += 1
        T = max(dte, 0.25) / 365
        for side, df in (("c", ch.calls), ("p", ch.puts)):
            for r in df.itertuples():
                K = float(r.strike)
                if abs(K - S) / S > WALL_RANGE:
                    continue
                oi = 0.0 if pd.isna(r.openInterest) else float(r.openInterest)
                if oi <= 0:
                    continue
                book = calls if side == "c" else puts
                book[K] = book.get(K, 0) + oi
                iv = 0.0 if pd.isna(r.impliedVolatility) else float(r.impliedVolatility)
                if iv > 0.03:
                    g = bs_gamma(S, K, T, iv) * oi * 100 * S * S * 0.01
                    gex += g if side == "c" else -g
        time.sleep(0.2)
    if used == 0 or not (calls or puts):
        return None
    pw = max(((k, v) for k, v in puts.items() if k < S), key=lambda x: x[1], default=None)
    cw = max(((k, v) for k, v in calls.items() if k > S), key=lambda x: x[1], default=None)
    return {"put_wall": pw, "call_wall": cw, "gex": gex}


def max_pain(calls, puts):
    strikes = sorted(set(calls["strike"]) | set(puts["strike"]))
    if not strikes:
        return None
    co = calls["openInterest"].fillna(0).values
    po = puts["openInterest"].fillna(0).values
    ck, pk = calls["strike"].values, puts["strike"].values
    best, best_pain = None, None
    for x in strikes:
        pain = (co * np.maximum(x - ck, 0)).sum() + (po * np.maximum(pk - x, 0)).sum()
        if best_pain is None or pain < best_pain:
            best, best_pain = x, pain
    return float(best)


# ====================== الأخبار وصدمات السوق ======================
def fetch_rss(q):
    out = []
    try:
        r = requests.get("https://news.google.com/rss/search",
                         params={"q": q + " when:1d", "hl": "en-US", "gl": "US", "ceid": "US:en"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if not r.ok:
            return out
        root = ET.fromstring(r.content)
    except Exception:
        return out
    cutoff = datetime.now(timezone.utc) - timedelta(hours=30)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        if not title:
            continue
        try:
            if parsedate_to_datetime(it.findtext("pubDate")) < cutoff:
                continue
        except Exception:
            pass
        out.append(title)
    return out


def fetch_headlines():
    market, seen = [], set()
    for q in MARKET_QUERIES:
        for t in fetch_rss(q):
            if t not in seen:
                seen.add(t)
                market.append(t)
    geo = [t for t in fetch_rss(GEO_QUERY) if t not in seen]
    if not market:
        for sym in (SYMBOL, NEWS_SYMBOL):
            try:
                items = yf.Ticker(sym).news or []
            except Exception:
                items = []
            for it in items:
                title = it.get("title") or (it.get("content") or {}).get("title")
                if title and title not in seen:
                    seen.add(title)
                    market.append(title)
    return {"market": market, "geo": geo}


def _chg(sym, n=1):
    h = yf.Ticker(sym).history(period="3mo")["Close"].dropna()
    if len(h) < n + 1:
        return None
    return float(h.iloc[-1] / h.iloc[-1 - n] - 1)


def market_shock(heads):
    """نقاط صدمة السوق ومستواه (1 هادئ، 2 متوسط، 3 مرتفع)."""
    pts, flags = 0, []
    out = {"vix_ch": None, "oil_ch": None, "gold_ch": None, "leaders": [], "avg1": None, "below": 0}
    for key, sym in (("vix_ch", "^VIX"), ("oil_ch", "CL=F"), ("gold_ch", "GC=F")):
        try:
            out[key] = _chg(sym)
        except Exception:
            pass
    v = out["vix_ch"]
    if v is not None and v >= 0.20:
        pts += 2
        flags.append(f"VIX قفز {v * 100:.0f}% في يوم واحد")
    elif v is not None and v >= 0.10:
        pts += 1
        flags.append(f"VIX ارتفع {v * 100:.0f}% في يوم")
    o = out["oil_ch"]
    if o is not None and abs(o) >= 0.04:
        pts += 1
        flags.append(f"النفط تحرك {o * 100:+.1f}% في يوم")
    g = out["gold_ch"]
    if g is not None and g >= 0.025:
        pts += 1
        flags.append(f"الذهب صعد {g * 100:.1f}% (طلب على الملاذ الآمن)")
    for sym in LEADERS:
        try:
            h = yf.Ticker(sym).history(period="3mo")["Close"].dropna()
            ch1 = float(h.iloc[-1] / h.iloc[-2] - 1)
            above = bool(h.iloc[-1] > h.rolling(50).mean().iloc[-1])
            out["leaders"].append((sym, ch1, above))
        except Exception:
            continue
    if out["leaders"]:
        out["avg1"] = sum(x[1] for x in out["leaders"]) / len(out["leaders"])
        out["below"] = sum(1 for x in out["leaders"] if not x[2])
        if out["avg1"] <= -0.035:
            pts += 2
            flags.append(f"الشركات القيادية هبطت بمتوسط {out['avg1'] * 100:.1f}% اليوم")
        elif out["avg1"] <= -0.02:
            pts += 1
            flags.append(f"الشركات القيادية تضغط على السوق ({out['avg1'] * 100:.1f}%)")
        if out["below"] >= 5:
            pts += 1
            flags.append(f"{out['below']} من {len(out['leaders'])} شركات قيادية تحت متوسط 50 يوم")
    geo_driving = [t for t in heads["market"] if GEO_RE.search(t)]
    risk = [t for t in heads["market"] if any(w in t.lower() for w in RISK_WORDS)]
    if len(geo_driving) >= 3:
        pts += 1
        flags.append(f"أخبار السوق متأثرة بعناوين جيوسياسية ({len(geo_driving)})")
    if len(risk) >= 3:
        pts += 1
        flags.append(f"عناوين فيها كلمات مخاطرة ({len(risk)})")
    out.update(pts=pts, flags=flags, geo_driving=geo_driving, level=1 if pts <= 1 else (2 if pts == 2 else 3))
    return out


# ====================== اتجاه اليوم ======================
def direction_points(c):
    """نقاط الصعود والهبوط من الاتجاه اليومي واللحظي والسيولة. يرجع (bull, bear, تفاصيل)."""
    rows = []
    gex = c["map"]["gex"] if c["map"] else 0
    mult = 1.25 if gex < 0 else (0.75 if gex > 0 else 1.0)

    if c["S"] > c["sma50"] and c["sma20"] > c["sma50"]:
        rows.append(("الاتجاه اليومي", 1, 0, "صاعد (فوق المتوسطات)"))
    elif c["S"] < c["sma50"] and c["sma20"] < c["sma50"]:
        rows.append(("الاتجاه اليومي", 0, 1, "هابط (تحت المتوسطات)"))
    else:
        rows.append(("الاتجاه اليومي", 0, 0, "محايد"))

    up = (1 if c["above_vwap"] else -1) + (1 if c["ema9"] > c["ema21"] else -1)
    vw = "فوق" if c["above_vwap"] else "تحت"
    em = "صاعد" if c["ema9"] > c["ema21"] else "هابط"
    if up == 2:
        rows.append(("الاتجاه اللحظي", 2 * mult, 0, f"{vw} VWAP والمتوسط السريع {em}"))
    elif up == -2:
        rows.append(("الاتجاه اللحظي", 0, 2 * mult, f"{vw} VWAP والمتوسط السريع {em}"))
    else:
        rows.append(("الاتجاه اللحظي", 0, 0, f"مختلط ({vw} VWAP والمتوسط {em})"))

    r30 = c["r30"]
    if r30 > 0.001:
        rows.append(("زخم 30 دقيقة", 1 * mult, 0, f"{r30 * 100:+.2f}%"))
    elif r30 < -0.001:
        rows.append(("زخم 30 دقيقة", 0, 1 * mult, f"{r30 * 100:+.2f}%"))
    else:
        rows.append(("زخم 30 دقيقة", 0, 0, f"{r30 * 100:+.2f}% (ضعيف)"))

    m = c["map"]
    if m and m["call_wall"] and (m["call_wall"][0] - c["S"]) / c["S"] <= 0.002:
        rows.append(("الجدران", 0, 1, f"قريب من جدار الكول {m['call_wall'][0]:,.0f} (مقاومة)"))
    elif m and m["put_wall"] and (c["S"] - m["put_wall"][0]) / c["S"] <= 0.002:
        rows.append(("الجدران", 1, 0, f"قريب من جدار البوت {m['put_wall'][0]:,.0f} (دعم)"))
    else:
        rows.append(("الجدران", 0, 0, "لا ملامسة لجدار"))

    gap = c["gap"]
    if gap is not None and gap >= 0.004:
        rows.append(("فجوة الافتتاح", 0.5, 0, f"{gap * 100:+.2f}% عن الإغلاق السابق"))
    elif gap is not None and gap <= -0.004:
        rows.append(("فجوة الافتتاح", 0, 0.5, f"{gap * 100:+.2f}% عن الإغلاق السابق"))

    mp = c["max_pain"]
    if mp and c["n"].hour >= 13 and abs(c["S"] - mp) / c["S"] > 0.002:
        if c["S"] > mp:
            rows.append(("نقطة الألم", 0, 0.5, f"السعر فوق {mp:,.0f} وقد ينجذب إليها"))
        else:
            rows.append(("نقطة الألم", 0.5, 0, f"السعر تحت {mp:,.0f} وقد ينجذب إليها"))

    # ----- مؤشرات إضافية -----
    es = c.get("es")
    if es is not None:
        w = 1.0 if (c["n"].hour, c["n"].minute) < (10, 30) else 0.5   # وزنه أكبر في أول ساعة
        if es >= 0.004:
            rows.append(("عقود SPX الآجلة (ES)", w, 0, f"{es * 100:+.2f}% عن إغلاق أمس"))
        elif es <= -0.004:
            rows.append(("عقود SPX الآجلة (ES)", 0, w, f"{es * 100:+.2f}% عن إغلاق أمس"))
        else:
            rows.append(("عقود SPX الآجلة (ES)", 0, 0, f"{es * 100:+.2f}% (محايد)"))
    orb = c.get("orb")
    if orb:
        if c["S"] > orb[0]:
            rows.append(("نطاق أول 30 دقيقة", 1.5, 0, f"اخترق الأعلى {orb[0]:,.0f}"))
        elif c["S"] < orb[1]:
            rows.append(("نطاق أول 30 دقيقة", 0, 1.5, f"كسر الأدنى {orb[1]:,.0f}"))
        else:
            rows.append(("نطاق أول 30 دقيقة", 0, 0, f"داخل النطاق {orb[1]:,.0f} - {orb[0]:,.0f}"))
    vd = c.get("vix_day")
    if vd is not None:
        if vd <= -0.03:
            rows.append(("VIX اليوم", 1, 0, f"{vd * 100:+.1f}% (الخوف يتراجع)"))
        elif vd >= 0.03:
            rows.append(("VIX اليوم", 0, 1, f"{vd * 100:+.1f}% (الخوف يرتفع)"))
        else:
            rows.append(("VIX اليوم", 0, 0, f"{vd * 100:+.1f}% (هادئ)"))
    pc = c.get("pc")
    if pc is not None:
        if pc >= 1.3:
            rows.append(("حجم الكول/البوت", 0.5, 0, f"كول {pc:.2f}× البوت"))
        elif pc <= 0.77:
            rows.append(("حجم الكول/البوت", 0, 0.5, f"بوت {1 / pc:.2f}× الكول"))
        else:
            rows.append(("حجم الكول/البوت", 0, 0, f"متوازن ({pc:.2f})"))
    pdh = c.get("pd_hl")
    if pdh:
        if c["S"] > pdh[0]:
            rows.append(("أمس", 1, 0, f"فوق أعلى سعر أمس {pdh[0]:,.0f}"))
        elif c["S"] < pdh[1]:
            rows.append(("أمس", 0, 1, f"تحت أدنى سعر أمس {pdh[1]:,.0f}"))
        else:
            rows.append(("أمس", 0, 0, f"داخل نطاق أمس {pdh[1]:,.0f} - {pdh[0]:,.0f}"))

    return sum(r[1] for r in rows), sum(r[2] for r in rows), rows


# ====================== اختيار العقود ======================
def pick_contracts(side, df, S, T, vix, min_oi=None, min_vol=None, max_spr=None):
    min_oi = MIN_OI if min_oi is None else min_oi
    min_vol = MIN_VOL if min_vol is None else min_vol
    max_spr = MAX_SPREAD if max_spr is None else max_spr
    cand = df[(df["strike"] > S) & (df["strike"] <= S * 1.025)] if side == "CALL" \
        else df[(df["strike"] < S) & (df["strike"] >= S * 0.975)]
    out = []
    for _, r in cand.iterrows():
        bid = 0 if pd.isna(r.get("bid")) else float(r["bid"])
        ask = 0 if pd.isna(r.get("ask")) else float(r["ask"])
        last = 0 if pd.isna(r.get("lastPrice")) else float(r["lastPrice"])
        if bid > 0 and ask > 0:
            price, spr = ask, (ask - bid) / ((ask + bid) / 2)
        else:
            price, spr = last, None
        cost = price * 100
        if not (CONTRACT_MIN_USD <= cost <= CONTRACT_MAX_USD):
            continue
        oi = 0 if pd.isna(r.get("openInterest")) else float(r["openInterest"])
        vol = 0 if pd.isna(r.get("volume")) else float(r["volume"])
        if oi < min_oi and vol < min_vol:
            continue
        if spr is not None and spr > max_spr:
            continue
        iv = r.get("impliedVolatility")
        if iv is None or pd.isna(iv) or iv < 0.03:
            iv = (vix or 18) / 100
        iv = float(iv)
        K = float(r["strike"])
        be = K + price if side == "CALL" else K - price
        delta = abs_delta(S, K, T, iv, side)
        prob = prob_beyond(S, be, T, iv, side)
        z = abs(be - S) / (S * iv * math.sqrt(T))
        if z > 2.0 or delta < 0.03:
            continue
        # درجة العقد: سيولة (3) + دلتا (3) + واقعية الوصول للتعادل (4)
        liq = 2 if oi >= 1000 else (1.5 if oi >= 300 else (1 if oi >= min_oi else 0.5))
        liq += 0.5 if spr is None else (1 if spr <= 0.10 else (0.5 if spr <= 0.25 else 0))
        dp = 3 if delta >= 0.20 else (2 if delta >= 0.12 else (1 if delta >= 0.07 else 0.5))
        rp = 4 if z <= 0.6 else (3 if z <= 1.0 else (2 if z <= 1.5 else 1))
        # الطلب (تدفق): حجم التداول اليوم نسبة إلى المراكز المفتوحة
        flow_ratio = vol / max(oi, 1.0)
        flow = 0.0
        if vol >= min_vol:
            flow = 1.0 if flow_ratio >= 0.5 else 0.0
            flow += 0.5 if flow_ratio >= 1.5 else 0.0
        score = min(10.0, liq + dp + rp + flow)
        if score < MIN_CONTRACT_SCORE:
            continue
        out.append({"strike": K, "price": price, "cost": cost, "be": be, "delta": delta, "prob": prob,
                    "z": z, "oi": oi, "vol": vol, "spr": spr, "iv": iv, "T": T, "score": score,
                    "flow": flow, "flow_ratio": flow_ratio})
    out.sort(key=lambda x: -x["score"])
    return out[:MAX_CONTRACTS]


def tier(score):
    return "🟩" if score >= 7.5 else ("🟨" if score >= 6.0 else "🟧")


# ====================== الحالة والسجل ======================
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(s):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False)
    except Exception as e:
        print("تعذر حفظ الحالة:", e)


def log_signal(n, exp, side, ctr, S, setup):
    row = {"date": n.date().isoformat(), "time": n.strftime("%H:%M"), "symbol": ctr.get("sym", SYMBOL), "expiry": exp,
           "side": side, "strike": ctr["strike"], "price": round(ctr["price"], 2), "spot": round(ctr.get("spot", S), 2),
           "setup": round(setup, 1), "contract_score": round(ctr["score"], 1), "result": "", "pnl": ""}
    try:
        if os.path.exists(LOG_FILE):
            old = pd.read_csv(LOG_FILE)
            pd.concat([old, pd.DataFrame([row])], ignore_index=True).to_csv(LOG_FILE, index=False)
        else:
            pd.DataFrame([row]).to_csv(LOG_FILE, index=False)
    except Exception as e:
        print("تعذر حفظ السجل:", e)


def log_summary():
    if not os.path.exists(LOG_FILE):
        return "📒 <b>سجل الإشارات</b>: لا توجد إشارات مسجلة بعد."
    try:
        df = pd.read_csv(LOG_FILE)
        df["result"] = df["result"].astype(object)
        df["pnl"] = pd.to_numeric(df["pnl"], errors="coerce")
        n, changed = now_ny(), False
        for i, r in df.iterrows():
            if pd.notna(r["result"]):
                continue
            exp = date.fromisoformat(str(r["expiry"]))
            if exp > n.date() or (exp == n.date() and (n.hour, n.minute) < (16, 30)):
                continue
            h = yf.Ticker(str(r["symbol"])).history(start=exp.isoformat(), end=(exp + timedelta(days=5)).isoformat())
            if h.empty:
                continue
            close = float(h["Close"].iloc[0])
            intrinsic = max(close - r["strike"], 0) if r["side"] == "CALL" else max(r["strike"] - close, 0)
            pnl = intrinsic - r["price"]
            df.loc[i, "pnl"] = round(float(pnl), 2)
            df.loc[i, "result"] = "ربح" if pnl > 0 else "خسارة"
            changed = True
        if changed:
            df.to_csv(LOG_FILE, index=False)
        closed = df[df["result"].notna() & (df["result"] != "")]
        if closed.empty:
            return f"📒 <b>سجل الإشارات</b>: {len(df)} إشارة مسجلة ولم تُحسب نتيجتها بعد."
        wins = int((closed["result"] == "ربح").sum())
        net = float(closed["pnl"].sum()) * 100
        return (f"📒 <b>سجل الإشارات</b> (لو أبقيت العقد حتى الانتهاء): {len(df)} إشارة | "
                f"انتهت {len(closed)} | رابحة {wins / len(closed):.0%} | الصافي {net:+,.0f}$")
    except Exception as e:
        return f"📒 تعذر قراءة السجل: {esc(e)}"


# ====================== التحليل ======================
def analyze(force):
    n = now_ny()
    today = n.date()
    t = yf.Ticker(SYMBOL)
    daily = t.history(period="1y")["Close"].dropna()
    ix = t.history(period="5d", interval="5m")
    if daily.empty or ix.empty:
        raise RuntimeError("لا توجد بيانات كافية حالياً.")
    closes = ix["Close"].dropna()
    dates = sorted(set(ix.index.date))
    last_date = dates[-1]
    bars = ix[ix.index.date == last_date]["Close"].dropna()
    S = float(closes.iloc[-1])
    age = (n - ix.index[-1].to_pydatetime()).total_seconds() / 60

    c = {"n": n, "S": S, "age": age,
         "sma20": float(daily.rolling(20).mean().iloc[-1]), "sma50": float(daily.rolling(50).mean().iloc[-1]),
         "ema9": float(closes.ewm(span=9, adjust=False).mean().iloc[-1]),
         "ema21": float(closes.ewm(span=21, adjust=False).mean().iloc[-1]),
         "r30": float(bars.iloc[-1] / bars.iloc[-7] - 1) if len(bars) >= 7 else 0.0,
         "gap": None, "above_vwap": True, "vwap": None, "prev_close": None, "vwap_spx": None}
    if len(dates) > 1:
        prev = ix[ix.index.date == dates[-2]]["Close"].dropna()
        if not prev.empty:
            c["gap"] = float(bars.iloc[0] / prev.iloc[-1] - 1)
            c["prev_close"] = float(prev.iloc[-1])
    try:
        fl = yf.Ticker(FLOW_SYMBOL).history(period="5d", interval="5m")
        fl = fl[fl.index.date == last_date]
        if fl["Volume"].sum() > 0:
            tp = (fl["High"] + fl["Low"] + fl["Close"]) / 3
            vwap = float((tp * fl["Volume"]).cumsum().iloc[-1] / fl["Volume"].cumsum().iloc[-1])
            c["vwap"] = vwap
            c["above_vwap"] = bool(fl["Close"].iloc[-1] > vwap)
            c["vwap_spx"] = vwap * S / float(fl["Close"].iloc[-1])   # VWAP بمستوى المؤشر
    except Exception:
        pass

    c["vix"], c["vpct"] = vix_info()
    heads = fetch_headlines()
    shock = market_shock(heads)
    events = upcoming_events(today)

    # ----- تاريخ الانتهاء (نفس اليوم) -----
    opts = list(t.options)
    if not opts:
        raise RuntimeError("لا توجد سلسلة خيارات حالياً.")
    today_s = today.isoformat()
    is_test = today_s not in opts
    exp = today_s if not is_test else next((e for e in opts if e >= today_s), opts[-1])
    dte = (date.fromisoformat(exp) - today).days
    if dte == 0:
        T = max((n.replace(hour=16, minute=0, second=0, microsecond=0) - n).total_seconds() / 3600, 0.25) / (24 * 365)
    else:
        T = max(dte, 1) / 365
    ch = t.option_chain(exp)
    c["max_pain"] = max_pain(ch.calls, ch.puts)
    map_exps = [(e, (date.fromisoformat(e) - today).days) for e in opts
                if 0 <= (date.fromisoformat(e) - today).days <= 2][:3] or [(exp, dte)]
    try:
        c["map"] = liquidity_map(t, S, map_exps)
    except Exception:
        c["map"] = None

    # ----- مؤشرات إضافية (كل واحد اختياري: إن فشل جلبه يُتجاهل) -----
    c["es"] = c["vix_day"] = c["orb"] = c["pd_hl"] = c["pc"] = None
    try:
        es = yf.Ticker("ES=F").history(period="5d")["Close"].dropna()
        if len(es) >= 2:
            c["es"] = float(es.iloc[-1] / es.iloc[-2] - 1)
    except Exception:
        pass
    try:
        vx = yf.Ticker("^VIX").history(period="5d", interval="5m")["Close"].dropna()
        vx = vx[vx.index.date == last_date]
        if len(vx) >= 3:
            c["vix_day"] = float(vx.iloc[-1] / vx.iloc[0] - 1)
    except Exception:
        pass
    try:
        day = ix[ix.index.date == last_date]
        if len(day) >= 7:   # اكتمل نطاق أول 30 دقيقة
            first = day.iloc[:6]
            c["orb"] = (float(first["High"].max()), float(first["Low"].min()))
        if len(dates) > 1:
            pdn = ix[ix.index.date == dates[-2]]
            if not pdn.empty:
                c["pd_hl"] = (float(pdn["High"].max()), float(pdn["Low"].min()))
    except Exception:
        pass
    try:
        cv = float(pd.to_numeric(ch.calls["volume"], errors="coerce").fillna(0).sum())
        pv = float(pd.to_numeric(ch.puts["volume"], errors="coerce").fillna(0).sum())
        if cv + pv >= 2000 and pv > 0:
            c["pc"] = cv / pv
    except Exception:
        pass

    bull, bear, rows = direction_points(c)
    edge = abs(bull - bear)
    reasons = []
    if shock["level"] == 2:
        edge = max(edge - 1.0, 0)
    side = "CALL" if bull > bear else "PUT"
    strength = min(10.0, edge * 1.5)

    # ----- شروط المنع -----
    if market_day(n) and no_entry(n) <= (n.hour, n.minute) < session_end(n):
        reasons.append("بعد وقت آخر دخول (قرب الإغلاق، والزمن يأكل قيمة العقد بسرعة).")
    if any(d == today for d, _ in events):
        nm = next(nm for d, nm in events if d == today)
        reasons.append(f"حدث كبير اليوم: {nm}. التقلب يصعب توقعه.")
    if shock["level"] >= 3:
        reasons.append("مخاطر الأخبار والجيوسياسة مرتفعة: " + "، ".join(shock["flags"][:2]) + ".")
    if edge < MIN_EDGE:
        reasons.append(f"لا اتجاه واضح (صعود {bull:.1f} مقابل هبوط {bear:.1f}).")

    contracts = []
    if not reasons:
        contracts = pick_contracts(side, ch.calls if side == "CALL" else ch.puts, S, T, c["vix"])
        for k in contracts:
            k["sym"], k["spot"] = SYMBOL, S
        if ALT_SYMBOL:
            try:
                ts = yf.Ticker(ALT_SYMBOL)
                if exp in list(ts.options):
                    chs = ts.option_chain(exp)
                    hs = ts.history(period="1d", interval="5m")["Close"].dropna()
                    Ss = float(hs.iloc[-1]) if len(hs) else float(ts.history(period="5d")["Close"].dropna().iloc[-1])
                    alt = pick_contracts(side, chs.calls if side == "CALL" else chs.puts, Ss, T, c["vix"])
                    for k in alt:
                        k["sym"], k["spot"] = ALT_SYMBOL, Ss
                    contracts += alt
            except Exception as e:
                print("تعذر قراءة خيارات", ALT_SYMBOL, e)
        contracts.sort(key=lambda x: -x["score"])
        contracts = contracts[:MAX_CONTRACTS]
        if NDX_SYMBOL and contracts:
            try:
                tn = yf.Ticker(NDX_SYMBOL)
                if exp in list(tn.options):
                    chn = tn.option_chain(exp)
                    hn = tn.history(period="1d", interval="5m")["Close"].dropna()
                    Sn = float(hn.iloc[-1]) if len(hn) else float(tn.history(period="5d")["Close"].dropna().iloc[-1])
                    nd = pick_contracts(side, chn.calls if side == "CALL" else chn.puts, Sn, T, c["vix"],
                                        NDX_MIN_OI, NDX_MIN_VOL, NDX_MAX_SPREAD)
                    for k in nd[:1]:
                        k["sym"], k["spot"] = "NDX", Sn
                        contracts.append(k)
            except Exception as e:
                print("تعذر قراءة خيارات", NDX_SYMBOL, e)
        if not contracts:
            reasons.append(f"لا توجد عقود {('Call' if side == 'CALL' else 'Put')} بسعر ${CONTRACT_MIN_USD}-${CONTRACT_MAX_USD} "
                           "مع سيولة كافية الآن.")

    return {"c": c, "shock": shock, "heads": heads, "events": events, "rows": rows, "bull": bull, "bear": bear,
            "edge": edge, "side": side, "strength": strength, "reasons": reasons, "contracts": contracts,
            "exp": exp, "is_test": is_test, "today": today}


# ====================== الكمية والمستويات والخطة ======================
def qty_for(cost):
    """عدد العقود بحيث لا تتجاوز الخسارة عند الوقف ميزانية المخاطرة (عقد واحد على الأقل)."""
    per_loss = cost * STOP_LOSS
    q = int(RISK_BUDGET_USD // per_loss) if per_loss > 0 else 1
    return max(1, min(MAX_QTY, q)), per_loss


def index_levels(k, side, hold_min=None):
    """مستوى الأصل (SPX أو SPY) الذي يعطي هدف العقد (+TAKE_PROFIT بعد HOLD_MIN دقيقة)
    ومستوى وقفه (-STOP_LOSS فوراً). تقدير من نموذج بلاك-شولز بتذبذب ثابت. يرجع (هدف، وقف) أو None."""
    S0, T0 = k.get("spot"), k.get("T")
    if not S0 or not T0 or not k.get("iv"):
        return None
    K, iv, P = k["strike"], k["iv"], k["price"]
    # التذبذب الضمني المنقول من البيانات قد يكون قديماً في عقود اليوم نفسه، فنعايره على سعر العقد الفعلي
    lo_i, hi_i = 0.02, 4.0
    if bs_price(S0, K, T0, hi_i, side) > P > bs_price(S0, K, T0, lo_i, side):
        for _ in range(60):
            mid_i = (lo_i + hi_i) / 2
            if bs_price(S0, K, T0, mid_i, side) > P:
                hi_i = mid_i
            else:
                lo_i = mid_i
        iv = (lo_i + hi_i) / 2
    T2 = max(T0 - (hold_min or HOLD_MIN) / (365 * 24 * 60), 5 / (365 * 24 * 60))
    sign = 1 if side == "CALL" else -1

    def solve(target, T, lo, hi):
        g = lambda x: sign * (bs_price(x, K, T, iv, side) - target)
        if not (g(lo) < 0 < g(hi)):
            return None
        for _ in range(60):
            mid = (lo + hi) / 2
            if g(mid) < 0:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2
    up = (S0, S0 * 1.05)
    dn = (S0 * 0.95, S0)
    tp = solve(P * (1 + TAKE_PROFIT), T2, *(up if side == "CALL" else dn))
    sl = solve(P * (1 - STOP_LOSS), T0, *(dn if side == "CALL" else up))
    if tp is None or sl is None:
        return None
    return tp, sl


def build_plan(c, bull, bear):
    """خطة اليوم: مستويات الدعم والمقاومة من البيانات المتاحة + سيناريوهات دخول مشروطة بأهداف."""
    S, vix, prev = c["S"], c.get("vix"), c.get("prev_close")
    em = (prev or S) * (vix / 100) / math.sqrt(252) if vix else S * 0.007   # حركة يومية متوقعة (1σ)
    lv = []
    m = c.get("map")
    if m:
        if m.get("call_wall"):
            lv.append((m["call_wall"][0], "جدار الكول"))
        if m.get("put_wall"):
            lv.append((m["put_wall"][0], "جدار البوت"))
    if c.get("pd_hl"):
        lv += [(c["pd_hl"][0], "أعلى أمس"), (c["pd_hl"][1], "أدنى أمس")]
    if c.get("orb"):
        lv += [(c["orb"][0], "أعلى أول 30د"), (c["orb"][1], "أدنى أول 30د")]
    if c.get("vwap_spx"):
        lv.append((c["vwap_spx"], "VWAP"))
    if c.get("max_pain"):
        lv.append((c["max_pain"], "نقطة الألم"))
    if prev and vix:
        lv += [(prev + em, "حد الحركة المتوقعة"), (prev - em, "حد الحركة المتوقعة")]
    lv = sorted((p_, t) for p_, t in lv if p_ and abs(p_ - S) / S <= 0.011)
    merged = []                       # ندمج المستويات المتقاربة في مستوى واحد
    for p_, t in lv:
        if merged and abs(p_ - merged[-1][0]) / S <= 0.0012:
            merged[-1] = ((merged[-1][0] + p_) / 2, merged[-1][1] + ([t] if t not in merged[-1][1] else []))
        else:
            merged.append((p_, [t]))
    res = [x for x in merged if x[0] > S * 1.0004][:3]
    sup = [x for x in merged if x[0] < S * 0.9996][::-1][:3]
    if not res:
        res = [(S + 0.5 * em, ["حركة متوقعة"])]
    if not sup:
        sup = [(S - 0.5 * em, ["حركة متوقعة"])]
    R1 = res[0][0]
    R2 = res[1][0] if len(res) > 1 else R1 + 0.35 * em
    S1 = sup[0][0]
    S2 = sup[1][0] if len(sup) > 1 else S1 - 0.35 * em
    d = bull - bear
    bias = "CALL" if d >= 2 else ("PUT" if d <= -2 else None)
    scen = [
        {"side": "CALL", "txt": f"إغلاق شمعة 5د فوق <b>{R1:,.0f}</b>", "tg": [R2]},
        {"side": "PUT", "txt": f"رفض عند <b>{R1:,.0f}</b> (شمعة 5د حمراء تغلق تحته)", "tg": [S1, S2]},
        {"side": "CALL", "txt": f"ارتداد من <b>{S1:,.0f}</b> (شمعة 5د خضراء تغلق فوقه)", "tg": [R1]},
        {"side": "PUT", "txt": f"إغلاق شمعة 5د تحت <b>{S1:,.0f}</b>", "tg": [S2]},
    ]
    if bias:
        scen.sort(key=lambda x: x["side"] != bias)   # السيناريوهات الموافقة لميل البوت أولاً
    return {"res": res, "sup": sup, "scen": scen, "bias": bias, "has_orb": bool(c.get("orb")), "d": d}


def plan_message(a, plan, update=False, status=None):
    c = a["c"]
    bias = {"CALL": "صعود 🟢", "PUT": "هبوط 🔴", None: "محايد ⚪"}[plan["bias"]]
    L = [f"🗺️ <b>SPX — خطة اليوم</b>{' (تحديث: اكتمل نطاق أول 30 دقيقة)' if update else ''} #خطة",
         f"📍 {c['S']:,.1f} | 🧭 الميل: <b>{bias}</b> (صعود {a['bull']:.1f} / هبوط {a['bear']:.1f})"]
    if status:
        L.append("⚪ لا توصية الآن: " + " • ".join(esc(x) for x in status[:3]))
    L.append(LINE)

    def fmt(lst):
        return " • ".join(f"<b>{p_:,.0f}</b> ({esc(' + '.join(t[:2]))})" for p_, t in lst)
    L.append(f"🧱 مقاومة: {fmt(plan['res'])}")
    L.append(f"🛡️ دعم: {fmt(plan['sup'])}")
    if not plan["has_orb"]:
        L.append("⏳ نطاق أول 30 دقيقة لم يكتمل بعد، وسأرسل تحديثاً عند اكتماله.")
    L.append("")
    for sc in plan["scen"]:
        call = sc["side"] == "CALL"
        tag = ""
        if plan["bias"]:
            tag = " 🔥 يوافق ميل البوت" if sc["side"] == plan["bias"] else " ⚠️ عكس ميل البوت"
        tg = " ثم ".join(f"{x:,.0f}" for x in sc["tg"])
        L.append(f"{'🟢 CALL' if call else '🔴 PUT'} | {sc['txt']} ← هدف <b>{tg}</b>{tag}")
    L += ["", "📡 عند توفر إشارة أرسل لك العقد (السترايك والسعر والكمية) مع مستويات المؤشر.",
          "⚠️ <i>المستويات وشروطها قواعد ثابتة لم تُختبر تاريخياً، والبيانات متأخرة"
          f" ~{c['age']:.0f} دقيقة. الشرط يُراقَب على الشارت الحي، والبوت يفحص كل نصف ساعة فقط.</i>",
          f"🕒 {now_ny().astimezone(RY).strftime('%H:%M')} الرياض | SPX إصدار 17"]
    return "\n".join(L)


# ====================== الرسائل ======================
def root_sym(k):
    """جذر العقد كما يظهر عند الوسيط: SPXW لعقود SPX اليومية، وSPY كما هو."""
    sy = k.get("sym", SYMBOL).lstrip("^")
    return {"SPX": "SPXW", "NDX": "NDXP"}.get(sy, sy)


def instrument_block(sym, ks, side, S_idx):
    """كتلة مستقلة لأداة واحدة (SPX أو SPY): العقد، الدخول، الجني، الوقف، الكمية، أقصى خسارة."""
    call = side == "CALL"
    word = "CALL" if call else "PUT"
    sgn = "+" if call else "-"
    title = "🅰️ <b>SPX المؤشر</b> (تسوية نقدية)" if sym == "SPX" else "🅱️ <b>SPY صندوق ETF</b> (قد يتحول لأسهم)"
    if not ks:
        return [f"{title}", "لا عقد مناسب لميزانيتك وسيولتك الآن."]
    k0 = ks[0]
    q, per = qty_for(k0["cost"])
    sp = k0.get("spot", S_idx)
    need = abs(k0["be"] - sp) / sp * 100
    mark = tier(k0["score"]) + ("🔥" if k0.get("flow", 0) >= 1 else "")
    L = [title,
         f"{mark} <b>{root_sym(k0)} {k0['strike']:,.0f} {word}</b> ×{q} | 💵 ${k0['cost']:,.0f} للعقد",
         f"├ 🟢 <b>دخول</b> بسعر حتى <b>{k0['price']:.2f}</b> | يحتاج {sgn}{need:.2f}% للتعادل"]
    tp_p, sl_p = k0["price"] * (1 + TAKE_PROFIT), k0["price"] * (1 - STOP_LOSS)
    lv = index_levels(k0, side)
    warn = None
    if lv:
        f = (lambda x: f"{x:,.2f}") if sym == "SPY" else (lambda x: f"{x:,.0f}")
        L.append(f"├ 🎯 <b>خروج بربح</b> <b>{tp_p:.2f}</b> (+{TAKE_PROFIT * 100:.0f}%) ← {sym} ≈ {f(lv[0])}")
        L.append(f"├ 🛑 <b>خروج بخسارة</b> <b>{sl_p:.2f}</b> (-{STOP_LOSS * 100:.0f}%) ← {sym} ≈ {f(lv[1])}")
        if max(abs(lv[0] - sp), abs(lv[1] - sp)) / sp < 0.0012:
            warn = "⚠️ الهدف والوقف قريبان جداً (أقل من 0.12%): الضوضاء العادية قد تضرب الوقف أولاً."
    else:
        L.append(f"├ 🎯 <b>خروج بربح</b> <b>{tp_p:.2f}</b> (+{TAKE_PROFIT * 100:.0f}%)")
        L.append(f"├ 🛑 <b>خروج بخسارة</b> <b>{sl_p:.2f}</b> (-{STOP_LOSS * 100:.0f}%)")
    L.append(f"└ 💰 أقصى خسارة ≈ ${q * per:,.0f} | رأس المال ≈ ${q * k0['cost']:,.0f}")
    if warn:
        L.append(warn)
    if per > RISK_BUDGET_USD:
        L.append(f"⚠️ عقد واحد يتجاوز ميزانية مخاطرتك (${RISK_BUDGET_USD}).")
    if len(ks) > 1:
        alts = " | ".join(f"{k['strike']:,.0f} · ${k['cost']:,.0f}" for k in ks[1:])
        L.append(f"↳ بدائل: {alts}")
    return L


def signal_message(a):
    c, side, S = a["c"], a["side"], a["c"]["S"]
    call = side == "CALL"
    dot = "🟢" if call else "🔴"
    word = "CALL" if call else "PUT"
    when = f" | {a['exp']} (تجريبي، السوق مغلق)" if a["is_test"] else ""
    L = [f"🔔 <b>توصية SPX | {word} {dot}</b> | ⭐ {a['strength']:.0f}/10 | 📍 SPX {S:,.1f}{when} #توصية"]
    groups = {}
    for k in a["contracts"]:
        groups.setdefault(k.get("sym", SYMBOL).lstrip("^"), []).append(k)
    order = [SYMBOL.lstrip("^")] + ([ALT_SYMBOL] if ALT_SYMBOL else [])
    for sym in order:
        if sym not in groups and sym != SYMBOL.lstrip("^") and not ALT_SYMBOL:
            continue
        L.append("")
        L += instrument_block(sym, groups.get(sym, []), side, S)
    L.append("")
    L.append("ادخل بسعر العقد المذكور أو أقل، ولا تلاحق السعر إن ارتفع العقد أكثر من 10% قبل دخولك. "
             f"⏰ لا دخول بعد {ry_time(*no_entry(now_ny()))}")
    if c["age"] >= 5:
        L.append(f"⏱️ البيانات متأخرة ~{c['age']:.0f} دقيقة، تحقق من السعر الحي.")
    L.append("⚠️ <i>تعليمي وليست توصية. أقصى خسارة هي سعر العقد.</i>")
    L.append(f"🕒 {now_ny().astimezone(RY).strftime('%H:%M')} الرياض | SPX إصدار 17")
    return "\n".join(L)


def ticket_lines(sym, k0, side):
    """سطور تذكرة الدخول المختصرة لعقد واحد: دخول، تكلفة، هدف، وقف، أقصى خسارة."""
    call = side == "CALL"
    q, per = qty_for(k0["cost"])
    tp_p, sl_p = k0["price"] * (1 + TAKE_PROFIT), k0["price"] * (1 - STOP_LOSS)
    lv = index_levels(k0, side)
    f = (lambda x: f"{x:,.2f}") if sym == "SPY" else (lambda x: f"{x:,.0f}")
    tp_t = f" ← {sym} ≈ {f(lv[0])}" if lv else ""
    sl_t = f" ← {sym} ≈ {f(lv[1])}" if lv else ""
    return [f"✅ <b>الدخول:</b> {root_sym(k0)} {k0['strike']:,.0f} {side} ×{q}",
            f"💵 بسعر حتى <b>{k0['price']:.2f}</b> | تكلفة <b>${k0['cost']:,.0f}</b> للعقد",
            f"🎯 الهدف <b>{tp_p:.2f}</b> (+{TAKE_PROFIT * 100:.0f}%){tp_t}",
            f"🛑 الوقف <b>{sl_p:.2f}</b> (-{STOP_LOSS * 100:.0f}%){sl_t}",
            f"💰 أقصى خسارة ≈ ${q * per:,.0f}"] + (
        ["⚠️ الهدف والوقف قريبان جداً: الضوضاء قد تضرب الوقف أولاً."]
        if lv and max(abs(lv[0] - k0.get("spot", lv[0])), abs(lv[1] - k0.get("spot", lv[1]))) / k0.get("spot", lv[0]) < 0.0012 else [])


def send_signal(a):
    """توصية SPX: صورة بطاقة العقد مع تذكرة مختصرة. وعند تعذر الصورة نرسل النص الكامل."""
    side, S, c = a["side"], a["c"]["S"], a["c"]
    groups = {}
    for k in a["contracts"]:
        groups.setdefault(k.get("sym", SYMBOL).lstrip("^"), []).append(k)
    main_sym = SYMBOL.lstrip("^")
    ks = groups.get(main_sym) or next(iter(groups.values()), [])
    if not ks:
        return send_telegram(signal_message(a))
    k0 = ks[0]
    sym = k0.get("sym", SYMBOL).lstrip("^")
    dot = "🟢" if side == "CALL" else "🔴"
    cap = [f"🔔 <b>توصية SPX | {side} {dot}</b> | ⭐ {a['strength']:.0f}/10 #توصية"] + ticket_lines(sym, k0, side)
    alt = groups.get(ALT_SYMBOL) if ALT_SYMBOL else None
    if alt:
        ka = alt[0]
        cap.append(f"↳ بديل ETF: {root_sym(ka)} {ka['strike']:,.0f} {side} · ${ka['cost']:,.0f}")
    cap.append(f"⏰ لا دخول بعد {ry_time(*no_entry(now_ny()))} | 📍 SPX {S:,.1f}")
    nd = groups.get("NDX")
    if c["age"] >= 5:
        cap.append(f"⏱️ البيانات متأخرة ~{c['age']:.0f} دقيقة، تحقق من السعر الحي.")
    cap.append("⚠️ <i>تعليمي وليست توصية.</i>")
    try:
        import cards
        sub = (f"انتهاء {a['exp']} (تجريبي)" if a["is_test"] else "انتهاء اليوم") + f" · {sym} {k0.get('spot', S):,.0f}"
        png = cards.contract_card("entry", f"{root_sym(k0)} {k0['strike']:,.0f} {side}", sub, k0["price"], None,
                                  k0["price"], k0["price"] * (1 - STOP_LOSS), k0["price"] * (1 + TAKE_PROFIT),
                                  badge=f"{side} {dot}", foot=f"{now_ny().astimezone(RY).strftime('%H:%M')} الرياض | SPX إصدار 17")
        mid = send_photo(png, "\n".join(cap))
        if mid:
            if nd:
                send_ndx_card(a, nd[0], side, dot, reply_to=mid)
            return mid
    except Exception as e:
        print("تعذر إنشاء بطاقة التوصية:", e)
    return send_telegram(signal_message(a))


def send_ndx_card(a, kn, side, dot, reply_to=None):
    """بطاقة NDXP (ناسداك 100): نفس اتجاه SPX بعقد أرخص بعيد عن السعر."""
    try:
        import cards
        cap = [f"🔔 <b>توصية NDXP | {side} {dot}</b> #توصية"] + ticket_lines("NDX", kn, side) + [
            "ℹ️ الاتجاه مأخوذ من تحليل SPX. NDXP أوسع حركة وأقل سيولة، وفرق السعر أكبر.",
            "⚠️ <i>تعليمي وليست توصية.</i>"]
        png = cards.contract_card("entry", f"{root_sym(kn)} {kn['strike']:,.0f} {side}",
                                  f"انتهاء {a['exp']} · NDX {kn.get('spot', 0):,.0f}", kn["price"], None,
                                  kn["price"], kn["price"] * (1 - STOP_LOSS), kn["price"] * (1 + TAKE_PROFIT),
                                  badge=f"NDXP {dot}", foot=f"{now_ny().astimezone(RY).strftime('%H:%M')} الرياض | SPX إصدار 17")
        send_photo(png, "\n".join(cap), reply_to=reply_to)
    except Exception as e:
        print("تعذر إنشاء بطاقة NDXP:", e)


def summary_message(state):
    L = ["🧾 <b>SPX — ملخص اليوم</b> #ملخص", "",
         f"🔎 عدد الفحوصات: <b>{state.get('runs', 0)}</b>"]
    be = state.get("best_edge", 0.0)
    if state.get("best_time"):
        try:
            bt = datetime.fromisoformat(state["best_time"]).astimezone(RY).strftime("%H:%M")
        except Exception:
            bt = "؟"
        side = "صعود" if state.get("best_side") == "CALL" else "هبوط"
        L.append(f"📈 أعلى فرق وصل إليه: <b>{be:.1f}</b> ({side}) الساعة {bt} بتوقيتك، والحد المطلوب {MIN_EDGE}")
    rc = state.get("reason_counts", {})
    if rc:
        L.append("🚫 أكثر أسباب عدم الإشارة:")
        for r, cnt in sorted(rc.items(), key=lambda x: -x[1])[:3]:
            L.append(f"• {esc(r)} ({cnt} مرة)")
    L += ["", "💡 لا إشارة = لا صفقة، والانتظار قرار سليم.",
          f"🕒 {now_ny().astimezone(RY).strftime('%H:%M')} الرياض | SPX إصدار 17"]
    return "\n".join(L)


def no_signal_message(a):
    c = a["c"]
    L = [f"⚪ <b>SPX — لا توصية الآن</b> | {c['S']:,.1f}"]
    for r in a["reasons"]:
        L.append(f"• {esc(r)}")
    return "\n".join(L)


def details_message(a, log_line, has_signal):
    c, shock, heads, today = a["c"], a["shock"], a["heads"], a["today"]
    L = [f"🏛️ <b>تحليل SPX (المؤشر)</b> | {today}", LINE,
         f"💲 {esc(SYMBOL)}: <b>{c['S']:,.2f}</b> | متوسط 20/50 يوم: {c['sma20']:,.0f} / {c['sma50']:,.0f}"]
    if c["vix"] is not None:
        L.append(f"🌡️ VIX: <b>{c['vix']:.1f}</b> (أعلى من {c['vpct']:.0f}% من أيام السنة)")
    if c["vwap"]:
        L.append(f"📐 VWAP ({FLOW_SYMBOL}): السعر {'فوقه' if c['above_vwap'] else 'تحته'}")
    L.append(f"⏱️ عمر آخر بيانات: ~{c['age']:.0f} دقيقة")

    L += ["", f"🧭 <b>اتجاه اليوم</b>: صعود <b>{a['bull']:.1f}</b> مقابل هبوط <b>{a['bear']:.1f}</b>"]
    for label, b, br, note in a["rows"]:
        icon = "🟢" if b > br else ("🔴" if br > b else "⚪")
        L.append(f"{icon} {label}: {esc(note)}")

    m = c["map"]
    L += ["", "🗺️ <b>خريطة السيولة</b> (اليوم وغداً)"]
    if m:
        if m["put_wall"]:
            L.append(f"🛡️ جدار البوت (دعم): <b>{m['put_wall'][0]:,.0f}</b> ({m['put_wall'][1]:,.0f} عقد)")
        if m["call_wall"]:
            L.append(f"🧱 جدار الكول (مقاومة): <b>{m['call_wall'][0]:,.0f}</b> ({m['call_wall'][1]:,.0f} عقد)")
        if c["max_pain"]:
            L.append(f"🎯 نقطة الألم القصوى: {c['max_pain']:,.0f}")
        L.append("⚡ صناع السوق (تقريبي): " + ("موجب، يميل السعر للارتداد" if m["gex"] >= 0 else "سالب، تتسع الحركات"))
    else:
        L.append("غير متاحة حالياً.")

    L.append("")
    icon = {1: "🟢 هادئة", 2: "🟠 متوسطة", 3: "🔴 مرتفعة"}[shock["level"]]
    L.append(f"📰 <b>الأخبار والجيوسياسة</b>: مخاطر {icon}")
    for f in shock["flags"]:
        L.append(f"⚠️ {esc(f)}")
    if not shock["flags"]:
        L.append("لا مؤشرات صدمة في VIX أو النفط أو الذهب أو الشركات القيادية.")
    if shock["leaders"]:
        L.append("🏢 القيادية: " + " | ".join(f"{sy} {ch * 100:+.1f}%" for sy, ch, _ in shock["leaders"]))
    shown = shock["geo_driving"][:2] + [t for t in heads["geo"] if t not in shock["geo_driving"]][:2]
    shown += [t for t in heads["market"] if t not in shown][: max(0, 4 - len(shown))]
    for h in shown[:4]:
        L.append(f"• {esc(h[:95])}")
    if a["events"]:
        for d, nm in a["events"][:3]:
            L.append(f"📅 {nm}: {d} (بعد {(d - today).days} يوم)")
    else:
        L.append("📅 لا أحداث كبرى معروفة خلال 7 أيام.")

    if a["contracts"]:
        L += ["", "🎯 <b>العقود المقترحة (تفصيل)</b>  🟩 قوي  🟨 متوسط  🟧 مقبول  🔥 طلب عالٍ"]
        for k in a["contracts"]:
            L.append(f"{tier(k['score'])} {root_sym(k)} {k['strike']:,.0f} | ${k['cost']:,.0f} | تعادل {k['be']:,.1f} | "
                     f"احتمال نموذجي ~{k['prob'] * 100:.0f}% | دلتا {k['delta']:.2f} | OI {k['oi']:,.0f} | حجم {k['vol']:,.0f}")
    L += ["", "🛠️ <b>إدارة الصفقة (اقتراح)</b>",
          f"• عقد واحد فقط، وأقصى خسارة = سعر العقد",
          f"• جني الربح عند +{TAKE_PROFIT * 100:.0f}%، ووقف الخسارة عند -{STOP_LOSS * 100:.0f}%",
          "• لا تضاعف الحجم بعد خسارة، ولا تدخل بعد إشارة فاتتك",
          "", log_line, "",
          "⚠️ <i>تنبيه صريح: معظم عقود 0DTE الرخيصة تنتهي بلا قيمة. الاحتمال الظاهر تقدير نظري من نموذج، "
          "والبيانات مجانية ومتأخرة. جرّب بحساب تجريبي أولاً ولا تخاطر بمال لا تتحمل خسارته.</i>"]
    return "\n".join(L)


# ====================== التشغيل ======================
def load_positions():
    try:
        with open(POS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("positions", [])
        return d
    except Exception:
        return {"positions": []}


def save_positions(d):
    try:
        with open(POS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception as e:
        print("تعذر حفظ المراكز:", e)


def add_positions(n, a):
    """يسجل العقد الأول من كل أداة (SPX وSPY) في الإشارة للمتابعة."""
    P = load_positions()
    seen = set()
    for k in a["contracts"]:
        sym = k.get("sym", SYMBOL).lstrip("^")
        if sym in seen:
            continue
        seen.add(sym)
        q, _ = qty_for(k["cost"])
        pid = f"{sym}:{a['side']}:{k['strike']:g}:{a['exp']}"
        if any(p["id"] == pid for p in P["positions"]):
            continue
        P["positions"].append({"id": pid, "sym": sym, "root": root_sym(k), "side": a["side"], "strike": float(k["strike"]),
                               "exp": a["exp"], "entry": round(float(k["price"]), 2), "qty": q,
                               "time": n.isoformat(timespec="minutes"), "entry_spot": round(k.get("spot", a["c"]["S"]), 2),
                               "status": "open", "peak": 0.0, "alerts": {}})
    save_positions(P)


def spx_quote(p):
    """سعر العقد الحالي (الوسط) وسعر الأصل. يرجع (قيمة، فرق السعر، سعر الأصل) أو None."""
    try:
        t = yf.Ticker({"SPX": "^SPX", "NDX": "^NDX"}.get(p["sym"], p["sym"]))
        ch = t.option_chain(p["exp"])
        df = ch.calls if p["side"] == "CALL" else ch.puts
        r = df[(df["strike"] - p["strike"]).abs() < 1e-6]
        if r.empty:
            return None
        r = r.iloc[0]
        bid = 0 if pd.isna(r.get("bid")) else float(r["bid"])
        ask = 0 if pd.isna(r.get("ask")) else float(r["ask"])
        last = 0 if pd.isna(r.get("lastPrice")) else float(r["lastPrice"])
        if bid > 0 and ask > 0:
            val, spr = (bid + ask) / 2, (ask - bid) / ((ask + bid) / 2)
        elif last > 0:
            val, spr = last, None
        else:
            return None
        spot = None
        try:
            spot = float(t.history(period="1d", interval="5m")["Close"].dropna().iloc[-1])
        except Exception:
            pass
        return val, spr, spot
    except Exception as e:
        print("تعذر تقييم", p.get("id"), e)
        return None


def eval_position(p, n, val, spot):
    """يرجع (pnl، قائمة التنبيهات الجديدة) ويعدّل p."""
    entry = p["entry"]
    pnl = val / entry - 1
    try:
        age = (n - datetime.fromisoformat(p["time"])).total_seconds() / 60
    except Exception:
        age = 9999
    al, new, hm = p["alerts"], [], (n.hour, n.minute)

    def fire(kind, text):
        if kind not in al:
            al[kind] = n.isoformat(timespec="minutes")
            new.append((kind, text))
    if pnl > p.get("peak", 0.0):
        p["peak"] = round(pnl, 3)
    live = p["status"] != "stopped"
    if live and age >= MIN_AGE_MIN and pnl <= -STOP_LOSS:
        fire("stop", f"وصل وقف الخسارة (-{STOP_LOSS * 100:.0f}%). الخروج يحدّ الخسارة.")
        p["status"] = "stopped"
    if live and pnl >= TAKE_PROFIT:
        fire("tp", f"وصل هدف الربح (+{TAKE_PROFIT * 100:.0f}%). فكّر بجني الربح أو ارفع وقفك إلى سعر دخولك.")
        if p["status"] == "open":
            p["status"] = "tp"
    if live and p["status"] == "tp" and p.get("peak", 0) >= TAKE_PROFIT and pnl <= p["peak"] * GIVEBACK:
        fire("giveback", f"الربح تراجع من +{p['peak'] * 100:.0f}% إلى {pnl * 100:+.0f}%. احمِ ما تبقى.")
    if live and hm >= (15, 30):
        itm = spot is not None and ((spot > p["strike"]) if p["side"] == "CALL" else (spot < p["strike"]))
        if p["sym"] == "SPY" and itm:
            fire("exp_final", "آخر 30 دقيقة: العقد داخل المال. إن تركته قد يتحول إلى 100 سهم. أغلقه قبل الإغلاق.")
        elif itm:
            fire("exp_final", "آخر 30 دقيقة: داخل المال، وSPX تسوية نقدية فلا أسهم. بِعه الآن إن أردت تثبيت الربح.")
        else:
            fire("exp_final", "آخر 30 دقيقة: خارج المال وسينتهي بلا قيمة إن لم يتحرك السعر. بِعه الآن لاسترداد ما بقي.")
    # اختصار: عند الوقف تنبيه واحد فقط
    if any(k == "stop" for k, _ in new):
        new = [x for x in new if x[0] == "stop"]
    return pnl, new


def position_message(p, val, pnl, spot, text, kind):
    icon = {"stop": "🛑", "tp": "🟢", "giveback": "📉", "exp_final": "⏰"}.get(kind, "📈")
    pl = (val - p["entry"]) * 100 * p.get("qty", 1)
    head = {"stop": "وصل الوقف", "tp": "وصل الهدف", "giveback": "الربح يتراجع", "exp_final": "ينتهي اليوم"}.get(kind, "تحرّك سعر العقد")
    L = ["📌 <b>متابعة</b> #متابعة",
         f"{icon} <b>{head}</b>: {p['root']} {p['strike']:,.0f} {p['side']}",
         f"💵 دخول {p['entry']:.2f} ← الآن {val:.2f} | <b>{pnl * 100:+.0f}%</b> ({pl:+,.0f}$ على ×{p.get('qty', 1)})"]
    if spot:
        fmt = f"{spot:,.2f}" if p["sym"] == "SPY" else f"{spot:,.1f}"
        L.append(f"📍 {p['sym']} {fmt}")
    L.append(text)
    L.append(f"🛑 الوقف {p['entry'] * (1 - STOP_LOSS):.2f} | ✅ الجني {p['entry'] * (1 + TAKE_PROFIT):.2f}")
    L.append(f"⚠️ القرار قرارك | 🕒 {now_ny().astimezone(RY).strftime('%H:%M')} الرياض | SPX إصدار 17")
    return "\n".join(L)


def follow_payload(p, val, pnl, spot, text, kind):
    """متابعة العقد: بطاقة صورة وتعليق مختصر (ونص كامل احتياطي)."""
    full = position_message(p, val, pnl, spot, text, kind)
    pl = (val - p["entry"]) * 100 * p.get("qty", 1)
    head = {"stop": "🛑 وصل الوقف", "tp": "🎯 وصل الهدف", "giveback": "📉 الربح يتراجع",
            "exp_final": "⏰ ينتهي اليوم"}.get(kind, "📈 تحرّك سعر العقد")
    name = f"{p['root']} {p['strike']:,.0f} {p['side']}"
    stop_p, tp_p = p["entry"] * (1 - STOP_LOSS), p["entry"] * (1 + TAKE_PROFIT)
    cap = ["📌 <b>متابعة</b> #متابعة", f"{head}: <b>{name}</b>",
           f"💵 دخول <b>{p['entry']:.2f}</b> ← الآن <b>{val:.2f}</b> | <b>{pnl * 100:+.0f}%</b> ({pl:+,.0f}$ على ×{p.get('qty', 1)})",
           f"🛑 الوقف {stop_p:.2f} | 🎯 الهدف {tp_p:.2f}", text,
           f"⚠️ القرار قرارك | 🕒 {now_ny().astimezone(RY).strftime('%H:%M')} الرياض"]
    args = (("follow", name, ("انتهاء اليوم" + (f" · {p['sym']} {spot:,.0f}" if spot else "")), val, pnl,
             p["entry"], stop_p, tp_p), {"now": val, "badge": head,
                                         "foot": f"وقت الرصد {now_ny().astimezone(RY).strftime('%H:%M')} الرياض | SPX إصدار 17"})
    return (args, "\n".join(cap), full)


def daily_report_items(n):
    """مراكز اليوم بنتيجة قاعدة الخروج (هدف +50% / وقف -40% / بلا حسم)."""
    P = load_positions()
    today = n.date().isoformat()
    todays = [p for p in P["positions"] if p["time"][:10] == today]
    last = {}
    try:
        for r in pd.read_csv(TRACK_FILE).itertuples():
            last[r.id] = float(r.val)
    except Exception:
        pass
    items = []
    for p in todays:
        e, q, pk = p["entry"], p.get("qty", 1), p.get("peak", 0.0)
        if pk >= TAKE_PROFIT:
            status, ex, pnl = "tp", e * (1 + TAKE_PROFIT), TAKE_PROFIT * e * 100 * q
        elif p["status"] == "stopped":
            status, ex, pnl = "stop", e * (1 - STOP_LOSS), -STOP_LOSS * e * 100 * q
        else:
            val = last.get(p["id"], e)
            status, ex, pnl = "flat", val, (val - e) * 100 * q
        items.append({"name": f"{p['root']} {p['strike']:,.0f} {p['side']}", "qty": q, "entry": e, "exit": ex,
                      "status": status, "peak": pk, "pnl": pnl})
    return items


def daily_report_png(n):
    import cards
    items = daily_report_items(n)
    if not items:
        return None
    return cards.daily_card(n.strftime("%Y-%m-%d"), items, STOP_LOSS, TAKE_PROFIT)


def monitor_positions(n, force=False):
    """يتابع إشارات اليوم المفتوحة. تنبيه مختصر عند الهدف أو الوقف أو التراجع أو قرب الانتهاء، وصمت بعد الوقف."""
    P = load_positions()
    today = n.date().isoformat()
    keep = [p for p in P["positions"] if p["exp"] >= today]
    dirty = len(keep) != len(P["positions"])
    P["positions"] = keep
    msgs, track = [], []
    for p0 in keep:
        p = copy.deepcopy(p0) if force else p0
        q = spx_quote(p)
        if q is None:
            continue
        val, spr, spot = q
        before = json.dumps(p, sort_keys=True)
        pnl, new = eval_position(p, n, val, spot)
        if json.dumps(p, sort_keys=True) != before:
            dirty = True
        track.append([n.isoformat(timespec="minutes"), p["id"], p["status"], "" if spot is None else round(spot, 2),
                      round(val, 3), round(pnl, 3)])
        if new and not force:
            kind, text = new[0]
            msgs.append(follow_payload(p, val, pnl, spot, text, kind))
    if track and not force:
        import csv
        fresh = not os.path.exists(TRACK_FILE)
        try:
            with open(TRACK_FILE, "a", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                if fresh:
                    w.writerow(["time", "id", "status", "spot", "val", "pnl"])
                w.writerows(track)
        except Exception as e:
            print("تعذر تسجيل التتبع:", e)
    for m in msgs:
        if isinstance(m, tuple):
            png_args, cap, text_msg = m
            mid = None
            try:
                import cards
                mid = send_photo(cards.contract_card(*png_args[0], **png_args[1]), cap)
            except Exception as e:
                print("تعذر إنشاء بطاقة المتابعة:", e)
            if not mid:
                send_telegram(text_msg)
        else:
            send_telegram(m)
    if dirty and not force:
        save_positions(P)


def run_once(force=False):
    n = now_ny()
    if not force and not in_window(n):
        print("خارج وقت التداول، لا شيء للتنفيذ.")
        return
    try:
        monitor_positions(n, force)
    except Exception as e:
        print("خطأ في متابعة المراكز:", e)
    try:
        a = analyze(force)
    except Exception as e:
        if force:
            send_telegram(f"⚠️ حدث خطأ في البوت: {esc(e)}")
        else:
            print("خطأ:", e)
        return

    state = load_state()
    today_s = a["today"].isoformat()
    if state.get("date") != today_s:   # يوم جديد: نصفّر عدادات اليوم
        state = {"date": today_s, "last_side": state.get("last_side"), "last_time": state.get("last_time"),
                 "report_sent": False, "runs": 0, "best_edge": 0.0, "signals": 0, "summary_sent": False,
                 "reason_counts": {}}
    has_signal = bool(a["contracts"])
    log_line = log_summary()

    if force:
        mid = send_signal(a) if has_signal else send_telegram(no_signal_message(a))
        shown, full = analysis_wrap(details_message(a, log_line, has_signal))
        send_telegram(shown, reply_to=mid, full_text=full)
        send_telegram(plan_message(a, build_plan(a["c"], a["bull"], a["bear"])))
        return

    state["runs"] = state.get("runs", 0) + 1
    if a["edge"] >= state.get("best_edge", 0.0):
        state.update(best_edge=round(a["edge"], 2), best_time=n.isoformat(), best_side=a["side"])
    for r in a["reasons"]:
        key = r.split(" (")[0][:60]
        rc = state.setdefault("reason_counts", {})
        rc[key] = rc.get(key, 0) + 1
    last_t = state.get("last_time")
    recent = False
    if has_signal and state.get("last_side") == a["side"] and last_t:
        try:
            recent = (n - datetime.fromisoformat(last_t)).total_seconds() < REPEAT_MINUTES * 60
        except Exception:
            recent = False

    plan = build_plan(a["c"], a["bull"], a["bear"])
    if has_signal and not recent:
        mid = send_signal(a)
        shown, full = analysis_wrap(details_message(a, log_line, True))
        send_telegram(shown, reply_to=mid, full_text=full)
        log_signal(n, a["exp"], a["side"], a["contracts"][0], a["c"]["S"], a["strength"])
        add_positions(n, a)
        state.update(last_side=a["side"], last_time=n.isoformat(), date=today_s, report_sent=True,
                     signals=state.get("signals", 0) + 1)
    elif state.get("date") != today_s or not state.get("report_sent"):
        if (n.hour, n.minute) < no_entry(n) and not state.get("plan_sent"):
            # أول رسالة اليوم: خطة واحدة تحمل سبب عدم التوصية، والتحليل مطوي تحتها
            pmid = send_telegram(plan_message(a, plan, status=a["reasons"]))
            shown, full = analysis_wrap(details_message(a, log_line, False))
            send_telegram(shown, reply_to=pmid, full_text=full)
            state.update(plan_sent=True, plan_orb=plan["has_orb"], plan_mid=pmid)
        else:
            send_telegram(no_signal_message(a) + "\n\n🔔 سأراقب السوق وأرسل لك إشارة عند توفر الشروط.")
        state.update(date=today_s, report_sent=True)
    else:
        print("لا إشارة جديدة، لا رسالة.")
    if (n.hour, n.minute) < no_entry(n) and (not state.get("plan_sent") or (plan["has_orb"] and not state.get("plan_orb"))):
        upd = bool(state.get("plan_sent"))
        txt = plan_message(a, plan, update=upd)
        if upd and state.get("plan_mid") and edit_telegram(state["plan_mid"], txt):
            pass                                  # حدّثنا رسالة الخطة نفسها بدل إرسال جديدة
        else:
            state["plan_mid"] = send_telegram(txt)
        state["plan_sent"] = True
        state["plan_orb"] = state.get("plan_orb", False) or plan["has_orb"]
    if (n.hour, n.minute) >= (12, 0) and not state.get("rep_mid"):
        state["rep_mid"] = True
        try:
            import report
            send_telegram(report.build("mid", n))
        except Exception as e:
            print("تعذر إرسال تقرير منتصف اليوم:", e)
    close_t = (session_end(n)[0], session_end(n)[1] - 30) if session_end(n)[1] >= 30 else (session_end(n)[0] - 1, session_end(n)[1] + 30)
    if (n.hour, n.minute) >= close_t and not state.get("rep_close"):
        state["rep_close"] = True
        try:
            import report
            send_telegram(report.build("close", n))
        except Exception as e:
            print("تعذر إرسال تقرير نهاية اليوم:", e)
    if (n.hour, n.minute) >= close_t and not state.get("report_img"):
        try:
            png = daily_report_png(n)
            if png:
                send_photo(png, "🧾 <b>تقرير SPX اليومي</b> #تقرير")
        except Exception as e:
            print("تعذر إنشاء بطاقة التقرير:", e)
        state["report_img"] = True
    if (n.hour, n.minute) >= no_entry(n) and not state.get("summary_sent") and state.get("signals", 0) == 0:
        send_telegram(summary_message(state))   # ملخص نهاية الجلسة عند عدم وجود أي إشارة
        state["summary_sent"] = True
    save_state(state)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="تشغيل فوري حتى خارج وقت التداول")
    args = parser.parse_args()
    run_once(force=args.force or os.getenv("FORCE") == "1")
