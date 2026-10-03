"""بوت إشارات خيارات SPX -> تليجرام (لا ينفذ أي أوامر تداول)

يجمع: الاتجاه + VIX + غلاء العلاوة + سيولة العقود + خريطة السيولة (جدران البوت والكول)
      + الأخبار + الأحداث الكبرى، ويعطي درجة من 10 وتوصية بصفقة Bull Put Credit Spread.
يسجّل كل إشارة في signals_log.csv ويتابع نتيجتها عند الانتهاء.
"""

import argparse
import html
import math
import os
import time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import requests
import yfinance as yf

# ====================== الإعدادات ======================
SYMBOL = "^SPX"            # يمكنك تغييره إلى "SPY" (وغيّر WIDTH إلى 5)
NEWS_SYMBOL = "SPY"        # لجلب الأخبار إن لم تتوفر لـ SPX
WIDTH = 25                 # عرض السبريد المستهدف (نقاط)
MAX_WIDTH_FACTOR = 2       # أقصى انحراف مقبول عن العرض المستهدف
TARGET_DTE = 35            # الأيام المستهدفة حتى الانتهاء
DTE_MIN, DTE_MAX = 21, 60
TARGET_DELTA = 0.18        # دلتا البيع المستهدفة
DELTA_MIN, DELTA_MAX = 0.10, 0.25
MIN_CREDIT_RATIO = 0.15    # أقل نسبة (العائد / العرض)
MIN_SCORE = 7.0            # أقل درجة للتوصية (من 10)
MIN_OI = 100               # أقل فائدة مفتوحة لكل عقد
VIX_MAX = 30               # فوقه لا ندخل
EVENT_BLOCK_DAYS = 1       # لا ندخل إذا كان حدث كبير خلال هذا العدد من الأيام
RISK_FREE = 0.04
WALL_RANGE = 0.08          # نطاق خريطة السيولة حول السعر (±8%)
MAP_MAX_DAYS = 45          # تواريخ الانتهاء المستخدمة في الخريطة
MAP_MAX_EXPIRIES = 20
LOG_FILE = "signals_log.csv"
LOOP_HOURS = 6

# تواريخ قرار الفيدرالي 2026 (تحقق منها). يمكنك إضافة أحداث أخرى (مثل CPI) هنا:
EVENTS = {
    "2026-10-28": "قرار الفيدرالي (FOMC)",
    "2026-12-09": "قرار الفيدرالي (FOMC)",
    # "2026-10-14": "تقرير التضخم CPI",
}

RISK_WORDS = ["crash", "plunge", "selloff", "sell-off", "tumble", "tariff", "war ",
              "recession", "default", "shutdown", "downgrade", "panic", "rate hike",
              "bank failure", "sanction"]

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
LINE = "━━━━━━━━━━━━━━━"


def esc(x):
    return html.escape(str(x))


# ====================== تليجرام ======================
def send_telegram(text):
    print(text)
    if not TOKEN or not CHAT_ID:
        print("\n[تنبيه] لم تُضبط TELEGRAM_TOKEN / TELEGRAM_CHAT_ID، فلم تُرسل الرسالة.")
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


# ====================== دوال رياضية ======================
def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _d1(S, K, T, s):
    return (math.log(S / K) + (RISK_FREE + 0.5 * s * s) * T) / (s * math.sqrt(T))


def put_delta(S, K, T, s):
    if s <= 0 or T <= 0:
        return float("nan")
    return norm_cdf(_d1(S, K, T, s)) - 1


def bs_gamma(S, K, T, s):
    if s <= 0 or T <= 0:
        return 0.0
    return norm_pdf(_d1(S, K, T, s)) / (S * s * math.sqrt(T))


def mid_price(row):
    bid, ask = row.get("bid", 0), row.get("ask", 0)
    if bid and ask and bid > 0 and ask > 0:
        return (bid + ask) / 2
    lp = row.get("lastPrice", 0)
    return 0 if pd.isna(lp) else lp


def spread_ratio(row):
    """فرق السعر بين الشراء والبيع كنسبة من المتوسط (None إذا لا توجد أسعار)."""
    bid, ask = row.get("bid", 0), row.get("ask", 0)
    if bid and ask and bid > 0 and ask > 0:
        return (ask - bid) / ((ask + bid) / 2)
    return None


# ====================== بيانات السوق ======================
def pick_expiry(t):
    today = datetime.now().date()
    best, best_diff = None, None
    for exp in t.options:
        dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        if dte < DTE_MIN or dte > DTE_MAX:
            continue
        diff = abs(dte - TARGET_DTE)
        if best is None or diff < best_diff:
            best, best_diff = (exp, dte), diff
    return best


def vix_info():
    try:
        h = yf.Ticker("^VIX").history(period="1y")["Close"].dropna()
        if len(h) < 50:
            return None, None
        last = float(h.iloc[-1])
        return last, float((h < last).mean() * 100)
    except Exception:
        return None, None


def get_news():
    out = []
    for sym in (SYMBOL, NEWS_SYMBOL):
        try:
            items = yf.Ticker(sym).news or []
        except Exception:
            items = []
        for it in items:
            title = it.get("title") or (it.get("content") or {}).get("title")
            if title and title not in out:
                out.append(title)
        if len(out) >= 3:
            break
    return out[:3]


def upcoming_events(today, days=14):
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


def liquidity_map(t, S):
    """خريطة السيولة من الفائدة المفتوحة: جدار البوت، جدار الكول، وGEX تقريبي."""
    today = datetime.now().date()
    calls, puts, gex, used = {}, {}, 0.0, 0
    exps = []
    for e in t.options:
        dte = (datetime.strptime(e, "%Y-%m-%d").date() - today).days
        if 1 <= dte <= MAP_MAX_DAYS:
            exps.append((e, dte))
    for e, dte in exps[:MAP_MAX_EXPIRIES]:
        try:
            ch = t.option_chain(e)
        except Exception:
            continue
        used += 1
        T = max(dte, 1) / 365
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
                if iv > 0.01:
                    g = bs_gamma(S, K, T, iv) * oi * 100 * S * S * 0.01
                    gex += g if side == "c" else -g
        time.sleep(0.2)
    if used == 0 or not (calls or puts):
        return None
    pw = max(((k, v) for k, v in puts.items() if k < S), key=lambda x: x[1], default=None)
    cw = max(((k, v) for k, v in calls.items() if k > S), key=lambda x: x[1], default=None)
    return {"put_wall": pw, "call_wall": cw, "gex": gex, "used": used}


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


# ====================== اختيار الصفقة ======================
def pick_spread(S, dte, puts, put_wall):
    T = dte / 365
    p = puts[(puts["strike"] < S) & (puts["impliedVolatility"] > 0.01)].copy()
    if p.empty:
        return None, "لا توجد عقود بيع مناسبة في السلسلة."
    p["delta"] = p.apply(lambda r: abs(put_delta(S, r["strike"], T, r["impliedVolatility"])), axis=1)
    p = p.dropna(subset=["delta"])
    p["oi"] = p["openInterest"].fillna(0)
    c = p[(p["delta"] >= DELTA_MIN) & (p["delta"] <= DELTA_MAX) & (p["oi"] >= MIN_OI)].copy()
    if c.empty:
        return None, "لا يوجد عقد بيع بدلتا مناسبة وسيولة كافية."
    c["below"] = (c["strike"] <= put_wall) if put_wall else False
    c["dd"] = (c["delta"] - TARGET_DELTA).abs()
    short = c.sort_values(["below", "dd"], ascending=[False, True]).iloc[0]
    ss = float(short["strike"])

    lower = puts[puts["strike"] < ss]
    liquid = lower[lower["openInterest"].fillna(0) >= MIN_OI]
    pool = liquid if not liquid.empty else lower
    if pool.empty:
        return None, "لا يوجد عقد أدنى لإكمال السبريد."
    long = pool.iloc[(pool["strike"] - (ss - WIDTH)).abs().argsort().iloc[0]]
    ls = float(long["strike"])
    width = ss - ls
    if width <= 0 or width > WIDTH * MAX_WIDTH_FACTOR:
        return None, f"لا يوجد سعر تنفيذ قريب من {ss - WIDTH:,.0f} لإكمال السبريد (أقرب متاح {ls:,.0f})."

    credit = mid_price(short) - mid_price(long)
    if credit <= 0:
        return None, "الأسعار الحالية غير صالحة (قد يكون السوق مغلقاً)."
    return {
        "short": ss, "long": ls, "width": width, "credit": credit,
        "delta": float(short["delta"]), "iv": float(short["impliedVolatility"]),
        "oi_s": float(short["oi"]), "oi_l": float(long["openInterest"]) if not pd.isna(long["openInterest"]) else 0.0,
        "spr_s": spread_ratio(short),
    }, None


# ====================== الدرجة ======================
def mark(pts, mx):
    return "✅" if pts >= mx * 0.75 else ("⚠️" if pts >= mx * 0.4 else "❌")


def compute_score(c):
    comps = []
    # 1) الاتجاه
    p = 0.0
    if c["S"] > c["sma50"]:
        p += 1
    if c["sma20"] > c["sma50"]:
        p += 0.5
    if c["sma50"] > c["sma50_10"]:
        p += 0.5
    comps.append(("الاتجاه", p, 2, "السعر فوق المتوسطات والمتوسط صاعد" if p >= 1.5 else "الاتجاه غير قوي"))
    # 2) حالة التقلب
    v, vp = c["vix"], c["vpct"]
    if v is None:
        p, note = 1.0, "VIX غير متاح"
    elif v >= VIX_MAX:
        p, note = 0.0, f"VIX مرتفع ({v:.0f}) والسوق متوتر"
    elif vp >= 35:
        p, note = 2.0, f"VIX {v:.0f}، تقلب كافٍ لعلاوة جيدة"
    else:
        p, note = 1.0, f"VIX {v:.0f} منخفض نسبياً، علاوة ضعيفة"
    if c["map"] and c["map"]["gex"] < 0 and p > 0:
        p = max(p - 0.5, 0)
        note += "، وتأثير صناع السوق سالب"
    comps.append(("حالة التقلب", p, 2, note))
    # 3) غلاء العلاوة
    sp = c["spread"]
    ratio = sp["iv"] / c["rv"] if c["rv"] > 0 else 1
    if ratio >= 1.2:
        p = 2.0
    elif ratio >= 1.0:
        p = 1.5
    elif ratio >= 0.85:
        p = 1.0
    else:
        p = 0.0
    comps.append(("غلاء العلاوة", p, 2, f"التقلب الضمني {sp['iv'] * 100:.0f}% مقابل الفعلي {c['rv'] * 100:.0f}%"))
    # 4) قوة العقد (السيولة)
    p = 0.0
    if sp["oi_s"] >= MIN_OI * 5 and sp["oi_l"] >= MIN_OI:
        p += 1
    elif sp["oi_s"] >= MIN_OI and sp["oi_l"] >= MIN_OI:
        p += 0.5
    r = sp["spr_s"]
    if r is None:
        p += 0.5
        extra = "لا أسعار حية"
    elif r <= 0.10:
        p += 1
        extra = "فرق السعر ضيق"
    elif r <= 0.25:
        p += 0.5
        extra = "فرق السعر متوسط"
    else:
        extra = "فرق السعر واسع"
    comps.append(("قوة العقد", p, 2, f"فائدة مفتوحة {sp['oi_s']:,.0f}/{sp['oi_l']:,.0f}، {extra}"))
    # 5) دعم جدار البوت
    pw = c["map"]["put_wall"] if c["map"] else None
    if pw is None:
        p, note = 1.0, "خريطة السيولة غير متاحة"
    elif sp["short"] <= pw[0]:
        p, note = 2.0, f"سعر البيع تحت جدار البوت {pw[0]:,.0f}"
    elif (sp["short"] - pw[0]) / c["S"] <= 0.01:
        p, note = 1.0, f"قريب من جدار البوت {pw[0]:,.0f} لكن فوقه"
    else:
        p, note = 0.0, f"سعر البيع أعلى من جدار البوت {pw[0]:,.0f}"
    comps.append(("دعم السيولة", p, 2, note))
    return comps


# ====================== السجل ======================
def update_log_summary():
    if not os.path.exists(LOG_FILE):
        return "📒 <b>سجل الإشارات</b>: لا توجد إشارات مسجلة بعد."
    try:
        df = pd.read_csv(LOG_FILE)
        for col in ("result", "pnl"):
            if col not in df.columns:
                df[col] = np.nan
        df["result"] = df["result"].astype(object)
        df["pnl"] = pd.to_numeric(df["pnl"], errors="coerce")
        today, changed = datetime.now().date(), False
        for i, r in df.iterrows():
            if pd.notna(r["result"]):
                continue
            exp = date.fromisoformat(str(r["expiry"]))
            if exp >= today:
                continue
            h = yf.Ticker(str(r["symbol"])).history(start=exp.isoformat(), end=(exp + timedelta(days=5)).isoformat())
            if h.empty:
                continue
            close = float(h["Close"].iloc[0])
            if close >= r["short"]:
                pnl = r["credit"]
            elif close <= r["long"]:
                pnl = r["credit"] - (r["short"] - r["long"])
            else:
                pnl = r["credit"] - (r["short"] - close)
            df.loc[i, "pnl"] = round(float(pnl), 2)
            df.loc[i, "result"] = "ربح" if pnl > 0 else "خسارة"
            changed = True
        if changed:
            df.to_csv(LOG_FILE, index=False)
        total = len(df)
        closed = df[df["result"].notna()]
        if closed.empty:
            return f"📒 <b>سجل الإشارات</b>: {total} إشارة مسجلة، ولم تنتهِ أي منها بعد."
        wins = int((closed["result"] == "ربح").sum())
        pnl_sum = float(closed["pnl"].sum()) * 100
        return (f"📒 <b>سجل الإشارات</b>: {total} إشارة | انتهت {len(closed)} | "
                f"نسبة الربح {wins / len(closed):.0%} | الصافي {pnl_sum:+,.0f}$ لكل عقد")
    except Exception as e:
        return f"📒 تعذر قراءة السجل: {esc(e)}"


def log_signal(today, exp, c, score):
    sp = c["spread"]
    row = {"date": today.isoformat(), "symbol": SYMBOL, "expiry": exp, "short": sp["short"],
           "long": sp["long"], "credit": round(sp["credit"], 2), "width": sp["width"],
           "spot": round(c["S"], 2), "score": round(score, 1), "result": "", "pnl": ""}
    try:
        if os.path.exists(LOG_FILE):
            old = pd.read_csv(LOG_FILE)
            if ((old["date"] == row["date"]) & (old["expiry"] == exp) & (old["symbol"] == SYMBOL)).any():
                return
            pd.concat([old, pd.DataFrame([row])], ignore_index=True).to_csv(LOG_FILE, index=False)
        else:
            pd.DataFrame([row]).to_csv(LOG_FILE, index=False)
    except Exception as e:
        print("تعذر حفظ السجل:", e)


# ====================== بناء الرسالة ======================
def build_message():
    today = datetime.now().date()
    log_line = update_log_summary()

    t = yf.Ticker(SYMBOL)
    hist = t.history(period="1y")
    if hist.empty or len(hist) < 60:
        return "لا توجد بيانات كافية حالياً."
    close = hist["Close"].dropna()
    c = {
        "S": float(close.iloc[-1]),
        "sma20": float(close.rolling(20).mean().iloc[-1]),
        "sma50": float(close.rolling(50).mean().iloc[-1]),
        "sma50_10": float(close.rolling(50).mean().iloc[-11]),
        "rv": float(np.log(close / close.shift(1)).dropna().tail(20).std() * math.sqrt(252)),
    }
    c["vix"], c["vpct"] = vix_info()
    S = c["S"]
    uptrend = S > c["sma50"]

    news = get_news()
    events = upcoming_events(today)
    block_ev = [(d, n) for d, n in events if (d - today).days <= EVENT_BLOCK_DAYS]

    try:
        c["map"] = liquidity_map(t, S)
    except Exception:
        c["map"] = None

    # ----- اختيار الصفقة -----
    reasons, exp, dte, mp, c["spread"] = [], None, None, None, None
    picked = pick_expiry(t)
    if not picked:
        reasons.append("لا يوجد تاريخ انتهاء مناسب (21-60 يوماً).")
    else:
        exp, dte = picked
        ch = t.option_chain(exp)
        mp = max_pain(ch.calls, ch.puts)
        pw = c["map"]["put_wall"][0] if c["map"] and c["map"]["put_wall"] else None
        c["spread"], err = pick_spread(S, dte, ch.puts, pw)
        if err:
            reasons.append(err)

    comps, total = [], None
    if c["spread"]:
        comps = compute_score(c)
        total = sum(x[1] for x in comps)

    # ----- شروط المنع -----
    if not uptrend:
        reasons.append("السعر تحت متوسط 50 يوم، واستراتيجيتنا تدخل مع الاتجاه الصاعد فقط.")
    if block_ev:
        reasons.append(f"حدث كبير قريب: {block_ev[0][1]} بتاريخ {block_ev[0][0]}، والأفضل الانتظار.")
    if c["vix"] is not None and c["vix"] >= VIX_MAX:
        reasons.append(f"VIX مرتفع ({c['vix']:.0f})، والمخاطرة عالية.")
    sp = c["spread"]
    if sp:
        ratio = sp["credit"] / sp["width"]
        if ratio < MIN_CREDIT_RATIO:
            reasons.append(f"العائد ضعيف ({ratio * 100:.1f}% من عرض السبريد، والحد الأدنى {MIN_CREDIT_RATIO * 100:.0f}%).")
        if total is not None and total < MIN_SCORE:
            reasons.append(f"الدرجة {total:.1f}/10 أقل من الحد الأدنى {MIN_SCORE:.0f}.")
    trade = bool(sp) and not reasons

    # ----- الرسالة -----
    L = [f"📊 <b>تقرير {esc(SYMBOL)}</b> | {today}", LINE,
         f"💲 السعر: <b>{S:,.2f}</b> | متوسط 50 يوم: {c['sma50']:,.2f}",
         f"🧭 الاتجاه: {'صاعد ✅' if uptrend else 'غير صاعد ⚠️'}"]
    if c["vix"] is not None:
        L.append(f"🌡️ VIX: <b>{c['vix']:.1f}</b> (أعلى من {c['vpct']:.0f}% من أيام السنة)")
    L.append(f"📉 التقلب الفعلي (20 يوم): {c['rv'] * 100:.1f}%")

    m = c["map"]
    L.append("")
    L.append("🗺️ <b>خريطة السيولة</b> (الفائدة المفتوحة)")
    if m:
        if m["put_wall"]:
            L.append(f"🛡️ جدار البوت (دعم محتمل): <b>{m['put_wall'][0]:,.0f}</b> ({m['put_wall'][1]:,.0f} عقد)")
        if m["call_wall"]:
            L.append(f"🧱 جدار الكول (مقاومة محتملة): <b>{m['call_wall'][0]:,.0f}</b> ({m['call_wall'][1]:,.0f} عقد)")
        if mp:
            L.append(f"🎯 نقطة الألم القصوى ({exp}): {mp:,.0f}")
        L.append("⚡ تأثير صناع السوق (تقريبي): " + ("موجب، حركة أهدأ غالباً" if m["gex"] >= 0 else "سالب، تقلب أعلى غالباً"))
    else:
        L.append("غير متاحة حالياً.")

    L.append("")
    L.append("📰 <b>الأخبار والأحداث</b>")
    for n in news:
        L.append(f"• {esc(n[:90])}")
    if news:
        flag = any(w in " ".join(news).lower() for w in RISK_WORDS)
        L.append("⚠️ بعض العناوين فيها كلمات مخاطرة" if flag else "لا كلمات مخاطرة حادة في العناوين (مؤشر ضعيف)")
    else:
        L.append("لا أخبار متاحة.")
    if events:
        for d, n in events[:3]:
            L.append(f"📅 {n}: {d} (بعد {(d - today).days} يوم)")
    else:
        L.append("📅 لا أحداث كبرى معروفة خلال 14 يوماً.")

    if comps:
        L.append("")
        L.append(f"⭐ <b>الدرجة: {total:.1f}/10</b> (الحد الأدنى {MIN_SCORE:.0f})")
        for label, pts, mx, note in comps:
            L.append(f"{mark(pts, mx)} {label} {pts:.1f}/{mx}: {esc(note)}")

    L.append(LINE)
    if trade:
        credit, width = sp["credit"], sp["width"]
        pw = m["put_wall"][0] if m and m["put_wall"] else None
        max_loss = width - credit
        dist = (S - sp["short"]) / S * 100
        L += [
            "✅ <b>توصية: Bull Put Credit Spread</b>", "",
            "🎯 <b>هدف الدخول</b>",
            f"نتوقع أن المؤشر سيبقى <b>فوق {sp['short']:,.0f}</b> حتى {exp}. نستلم علاوة مقدماً ونحتفظ بها كاملة إذا لم ينزل تحت هذا المستوى.", "",
            "🧭 <b>لماذا الآن؟</b>",
            f"• الدرجة {total:.1f}/10 والاتجاه صاعد",
            f"• سعر البيع أقل من السعر الحالي بنحو {dist:.1f}%"
            + (f"، وتحت جدار البوت {pw:,.0f}" if pw and sp["short"] <= pw else ""),
            f"• احتمال الربح التقريبي: <b>{(1 - sp['delta']) * 100:.0f}%</b>", "",
            "🛒 <b>التنفيذ (أمر واحد)</b>",
            f"1️⃣ بيع Put بسعر تنفيذ <b>{sp['short']:,.0f}</b>",
            f"2️⃣ شراء Put بسعر تنفيذ <b>{sp['long']:,.0f}</b> (حماية، عرض {width:.0f} نقطة)",
            f"📆 الانتهاء: {exp} (بعد {dte} يوم)",
            f"💵 سعر الأمر (Limit): استلام ≈ <b>{credit:.2f}</b> نقطة", "",
            "💰 <b>الأرقام لكل عقد</b>",
            f"✅ أقصى ربح: <b>{credit * 100:,.0f}$</b>",
            f"❌ أقصى خسارة: <b>{max_loss * 100:,.0f}$</b>",
            f"⚖️ نقطة التعادل: {sp['short'] - credit:,.0f}", "",
            "🚪 <b>خطة الخروج (اقتراح)</b>",
            f"• جني الربح: أغلق عندما يصل سعر السبريد إلى ≈ {credit * 0.5:.2f} (نصف العلاوة)",
            f"• وقف الخسارة: أغلق إذا ارتفع إلى ≈ {min(credit * 2, width * 0.8):.2f}",
            "• لا تنتظر الأيام الأخيرة: أغلق قبل الانتهاء بنحو 7 أيام",
        ]
        log_signal(today, exp, c, total)
    else:
        L.append("🚫 <b>لا دخول اليوم</b>")
        for r in reasons:
            L.append(f"• {esc(r)}")
        L.append("")
        L.append("💡 الانتظار قرار سليم، فلا ندخل إلا عند توفر الشروط.")

    L += ["", log_line, "",
          "⚠️ <i>للتعلم فقط وليست توصية مالية. الأسعار تقريبية وبيانات مجانية متأخرة، فتحقق من الأسعار الحية في منصتك، ولا تخاطر بأكثر مما تتحمل خسارته.</i>"]
    return "\n".join(L)


def run_once():
    try:
        send_telegram(build_message())
    except Exception as e:
        send_telegram(f"⚠️ حدث خطأ في البوت: {esc(e)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    if args.loop:
        while True:
            run_once()
            time.sleep(LOOP_HOURS * 3600)
    else:
        run_once()
