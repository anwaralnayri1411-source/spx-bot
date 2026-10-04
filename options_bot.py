"""بوت إشارات خيارات SPX لنفس اليوم (0DTE): شراء Call أو Put -> تليجرام (لا ينفذ أي أوامر)

الرسالة الأولى: توصية مختصرة (كول أو بوت) مع عقود الدخول ملونة.
الرسالة الثانية: كل التفاصيل (الاتجاه، السيولة، الأخبار، الأحداث، خطة الإدارة).
"""

import argparse
import html
import json
import math
import os
import re
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
WINDOW_START = (9, 45)      # نافذة التشغيل بتوقيت نيويورك
WINDOW_END = (15, 30)
NO_NEW_ENTRY = (15, 0)      # لا دخول جديد بعد هذا الوقت
RISK_FREE = 0.04
WALL_RANGE = 0.03
STATE_FILE = "bot_state.json"
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


def in_window(n):
    return n.weekday() < 5 and WINDOW_START <= (n.hour, n.minute) <= WINDOW_END


def ry_time(h, m):
    """يحوّل وقتاً بتوقيت نيويورك (اليوم) إلى توقيت الرياض للعرض."""
    t = now_ny().replace(hour=h, minute=m, second=0, microsecond=0)
    return t.astimezone(RY).strftime("%I:%M %p").replace("AM", "ص").replace("PM", "م")


# ====================== تليجرام ======================
def send_telegram(text):
    print(text)
    print()
    if not TOKEN or not CHAT_ID:
        print("[تنبيه] لم تُضبط TELEGRAM_TOKEN / TELEGRAM_CHAT_ID، فلم تُرسل الرسالة.")
        return
    parts, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) + 2 > 3800 and cur:
            parts.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    parts.append(cur)
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    for p in parts:
        try:
            r = requests.post(url, data={"chat_id": CHAT_ID, "text": p, "parse_mode": "HTML"}, timeout=15)
            if not r.ok:
                print("خطأ من تليجرام:", r.text)
        except requests.RequestException as e:
            print("تعذر الإرسال:", e)


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

    return sum(r[1] for r in rows), sum(r[2] for r in rows), rows


# ====================== اختيار العقود ======================
def pick_contracts(side, df, S, T, vix):
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
        if oi < MIN_OI and vol < MIN_VOL:
            continue
        if spr is not None and spr > MAX_SPREAD:
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
        liq = 2 if oi >= 1000 else (1.5 if oi >= 300 else (1 if oi >= MIN_OI else 0.5))
        liq += 0.5 if spr is None else (1 if spr <= 0.10 else (0.5 if spr <= 0.25 else 0))
        dp = 3 if delta >= 0.20 else (2 if delta >= 0.12 else (1 if delta >= 0.07 else 0.5))
        rp = 4 if z <= 0.6 else (3 if z <= 1.0 else (2 if z <= 1.5 else 1))
        # الطلب (تدفق): حجم التداول اليوم نسبة إلى المراكز المفتوحة
        flow_ratio = vol / max(oi, 1.0)
        flow = 0.0
        if vol >= MIN_VOL:
            flow = 1.0 if flow_ratio >= 0.5 else 0.0
            flow += 0.5 if flow_ratio >= 1.5 else 0.0
        score = min(10.0, liq + dp + rp + flow)
        if score < MIN_CONTRACT_SCORE:
            continue
        out.append({"strike": K, "price": price, "cost": cost, "be": be, "delta": delta, "prob": prob,
                    "z": z, "oi": oi, "vol": vol, "spr": spr, "iv": iv, "score": score,
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
    row = {"date": n.date().isoformat(), "time": n.strftime("%H:%M"), "symbol": SYMBOL, "expiry": exp,
           "side": side, "strike": ctr["strike"], "price": round(ctr["price"], 2), "spot": round(S, 2),
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
         "gap": None, "above_vwap": True, "vwap": None}
    if len(dates) > 1:
        prev = ix[ix.index.date == dates[-2]]["Close"].dropna()
        if not prev.empty:
            c["gap"] = float(bars.iloc[0] / prev.iloc[-1] - 1)
    try:
        fl = yf.Ticker(FLOW_SYMBOL).history(period="5d", interval="5m")
        fl = fl[fl.index.date == last_date]
        if fl["Volume"].sum() > 0:
            tp = (fl["High"] + fl["Low"] + fl["Close"]) / 3
            vwap = float((tp * fl["Volume"]).cumsum().iloc[-1] / fl["Volume"].cumsum().iloc[-1])
            c["vwap"] = vwap
            c["above_vwap"] = bool(fl["Close"].iloc[-1] > vwap)
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

    bull, bear, rows = direction_points(c)
    edge = abs(bull - bear)
    reasons = []
    if shock["level"] == 2:
        edge = max(edge - 1.0, 0)
    side = "CALL" if bull > bear else "PUT"
    strength = min(10.0, edge * 2.0)

    # ----- شروط المنع -----
    if n.weekday() < 5 and NO_NEW_ENTRY <= (n.hour, n.minute) < (16, 0):
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
        if not contracts:
            reasons.append(f"لا توجد عقود {('Call' if side == 'CALL' else 'Put')} بسعر ${CONTRACT_MIN_USD}-${CONTRACT_MAX_USD} "
                           "مع سيولة كافية الآن.")

    return {"c": c, "shock": shock, "heads": heads, "events": events, "rows": rows, "bull": bull, "bear": bear,
            "edge": edge, "side": side, "strength": strength, "reasons": reasons, "contracts": contracts,
            "exp": exp, "is_test": is_test, "today": today}


# ====================== الرسائل ======================
def signal_message(a):
    c, side, S = a["c"], a["side"], a["c"]["S"]
    call = side == "CALL"
    head = "🟢🟢🟢 <b>CALL (شراء كول)</b>" if call else "🔴🔴🔴 <b>PUT (شراء بوت)</b>"
    word = "Call" if call else "Put"
    dot = "🟢" if call else "🔴"
    L = [head, f"📍 {esc(SYMBOL)}: <b>{S:,.1f}</b> | ⭐ قوة الإشارة <b>{a['strength']:.0f}/10</b>",
         f"⏳ ينتهي: {'اليوم (0DTE)' if not a['is_test'] else a['exp'] + ' (تجريبي، السوق مغلق)'}", "",
         "🎯 <b>عقود الدخول:</b>"]
    for k in a["contracts"]:
        L.append(f"{tier(k['score'])}{dot} <b>{k['strike']:,.0f} {word}</b> ≈ <b>{k['price']:.2f}</b> (${k['cost']:,.0f}) "
                 f"| تعادل {k['be']:,.1f} | احتمال ~{k['prob'] * 100:.0f}%"
                 + (f" | 🔥 طلب عالٍ (حجم {k['vol']:,.0f})" if k.get('flow', 0) >= 1 else ""))
    L += ["", f"🟩 قوي  🟨 متوسط  🟧 مقبول  🔥 طلب عالٍ على العقد", "",
          f"✅ جني الربح: +{TAKE_PROFIT * 100:.0f}% | 🛑 وقف الخسارة: -{STOP_LOSS * 100:.0f}%",
          f"⏰ لا دخول بعد {ry_time(*NO_NEW_ENTRY)} بتوقيتك"]
    if c["age"] >= 5:
        L.append(f"⏱️ البيانات متأخرة ~{c['age']:.0f} دقيقة، تحقق من السعر الحي قبل الدخول.")
    L.append("⚠️ <i>للتعلم فقط وليست توصية مالية. أقصى خسارة هي سعر العقد.</i>")
    return "\n".join(L)


def no_signal_message(a):
    c = a["c"]
    L = [f"⚪ <b>لا صفقة الآن</b> | {esc(SYMBOL)} {c['S']:,.1f}"]
    for r in a["reasons"]:
        L.append(f"• {esc(r)}")
    return "\n".join(L)


def details_message(a, log_line, has_signal):
    c, shock, heads, today = a["c"], a["shock"], a["heads"], a["today"]
    L = [f"📊 <b>تفاصيل التحليل</b> | {today}", LINE,
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

    L += ["", "🛠️ <b>إدارة الصفقة (اقتراح)</b>",
          f"• عقد واحد فقط، وأقصى خسارة = سعر العقد",
          f"• جني الربح عند +{TAKE_PROFIT * 100:.0f}%، ووقف الخسارة عند -{STOP_LOSS * 100:.0f}%",
          "• لا تضاعف الحجم بعد خسارة، ولا تدخل بعد إشارة فاتتك",
          "", log_line, "",
          "⚠️ <i>تنبيه صريح: معظم عقود 0DTE الرخيصة تنتهي بلا قيمة. الاحتمال الظاهر تقدير نظري من نموذج، "
          "والبيانات مجانية ومتأخرة. جرّب بحساب تجريبي أولاً ولا تخاطر بمال لا تتحمل خسارته.</i>"]
    return "\n".join(L)


# ====================== التشغيل ======================
def run_once(force=False):
    n = now_ny()
    if not force and not in_window(n):
        print("خارج وقت التداول، لا شيء للتنفيذ.")
        return
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
    has_signal = bool(a["contracts"])
    log_line = log_summary()

    if force:
        send_telegram(signal_message(a) if has_signal else no_signal_message(a))
        send_telegram(details_message(a, log_line, has_signal))
        return

    last_t = state.get("last_time")
    recent = False
    if has_signal and state.get("last_side") == a["side"] and last_t:
        try:
            recent = (n - datetime.fromisoformat(last_t)).total_seconds() < REPEAT_MINUTES * 60
        except Exception:
            recent = False

    if has_signal and not recent:
        send_telegram(signal_message(a))
        send_telegram(details_message(a, log_line, True))
        log_signal(n, a["exp"], a["side"], a["contracts"][0], a["c"]["S"], a["strength"])
        state.update(last_side=a["side"], last_time=n.isoformat(), date=today_s, report_sent=True)
    elif state.get("date") != today_s or not state.get("report_sent"):
        send_telegram(no_signal_message(a) + "\n\n🔔 سأراقب السوق وأرسل لك إشارة عند توفر الشروط.")
        send_telegram(details_message(a, log_line, False))
        state.update(date=today_s, report_sent=True)
    else:
        print("لا إشارة جديدة، لا رسالة.")
    save_state(state)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="تشغيل فوري حتى خارج وقت التداول")
    args = parser.parse_args()
    run_once(force=args.force or os.getenv("FORCE") == "1")
