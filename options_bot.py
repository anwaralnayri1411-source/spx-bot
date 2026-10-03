"""بوت إشارات خيارات SPX -> تليجرام (لا ينفذ أي أوامر تداول)"""

import argparse
import math
import os
import time
from datetime import datetime

import pandas as pd
import requests
import yfinance as yf

# ====== الإعدادات ======
SYMBOL = "^SPX"          # يمكنك تغييره إلى "SPY" إن لم تتوفر بيانات SPX
WIDTH = 25               # عرض السبريد بالنقاط (استخدم 5 مع SPY)
TARGET_DTE = 35          # الأيام المستهدفة حتى الانتهاء
TARGET_DELTA = 0.18      # دلتا البيع المستهدفة
MIN_CREDIT_RATIO = 0.15  # أقل نسبة (العائد / العرض) لقبول الصفقة
RISK_FREE = 0.04         # سعر الفائدة التقريبي
LOOP_HOURS = 6

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_telegram(text):
    print(text)
    if not TOKEN or not CHAT_ID:
        print("\n[تنبيه] لم تُضبط TELEGRAM_TOKEN / TELEGRAM_CHAT_ID، فلم تُرسل الرسالة.")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=15)
        if not r.ok:
            print("خطأ من تليجرام:", r.text)
    except requests.RequestException as e:
        print("تعذر الإرسال:", e)


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def put_delta(S, K, T, sigma):
    if sigma <= 0 or T <= 0:
        return float("nan")
    d1 = (math.log(S / K) + (RISK_FREE + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    return norm_cdf(d1) - 1


def mid_price(row):
    bid, ask = row.get("bid", 0), row.get("ask", 0)
    if bid and ask and bid > 0 and ask > 0:
        return (bid + ask) / 2
    return row.get("lastPrice", 0) or 0


def pick_expiry(ticker):
    today = datetime.now().date()
    best, best_diff = None, None
    for exp in ticker.options:
        dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        if dte < 21 or dte > 60:
            continue
        diff = abs(dte - TARGET_DTE)
        if best is None or diff < best_diff:
            best, best_diff = (exp, dte), diff
    return best


def build_message():
    t = yf.Ticker(SYMBOL)

    hist = t.history(period="6mo")
    if hist.empty or len(hist) < 50:
        return "لا توجد بيانات كافية حالياً."
    close = hist["Close"]
    S = float(close.iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1])

    header = f"📊 {SYMBOL}\nالسعر: {S:.2f} | متوسط 50 يوم: {sma50:.2f}\n"

    if S < sma50:
        return header + "\nالاتجاه ليس صاعداً (السعر تحت المتوسط). لا توجد صفقة مقترحة اليوم."

    picked = pick_expiry(t)
    if not picked:
        return header + "\nلا يوجد تاريخ انتهاء مناسب (21-60 يوماً)."
    exp, dte = picked

    puts = t.option_chain(exp).puts.copy()
    T = dte / 365
    puts = puts[(puts["strike"] < S) & (puts["impliedVolatility"] > 0.01)]
    if puts.empty:
        return header + "\nلا توجد عقود مناسبة في السلسلة."

    puts["delta"] = puts.apply(
        lambda r: abs(put_delta(S, r["strike"], T, r["impliedVolatility"])), axis=1
    )
    puts = puts.dropna(subset=["delta"])
    short = puts.iloc[(puts["delta"] - TARGET_DELTA).abs().argsort().iloc[0]]

    long_strike = short["strike"] - WIDTH
    long_rows = t.option_chain(exp).puts
    long_rows = long_rows[long_rows["strike"] == long_strike]
    if long_rows.empty:
        return header + f"\nلا يوجد عقد بسعر تنفيذ {long_strike:.0f} لإكمال السبريد."
    long = long_rows.iloc[0]

    credit = mid_price(short) - mid_price(long)
    if credit <= 0:
        return header + "\nالأسعار الحالية غير صالحة (قد يكون السوق مغلقاً)."

    max_loss = WIDTH - credit
    ratio = credit / WIDTH
    pop = (1 - short["delta"]) * 100

    if ratio < MIN_CREDIT_RATIO:
        return header + (
            f"\nأفضل سبريد متاح يعطي عائداً ضعيفاً ({ratio:.0%} من العرض). لا صفقة اليوم."
        )

    return header + (
        f"\n✅ اقتراح: Bull Put Credit Spread\n"
        f"الانتهاء: {exp} ({dte} يوم)\n"
        f"بيع Put بسعر تنفيذ {short['strike']:.0f}\n"
        f"شراء Put بسعر تنفيذ {long_strike:.0f}\n"
        f"العائد التقريبي: {credit:.2f} نقطة (≈ {credit * 100:.0f}$ للعقد)\n"
        f"أقصى خسارة: {max_loss:.2f} نقطة (≈ {max_loss * 100:.0f}$ للعقد)\n"
        f"احتمال الربح التقريبي: {pop:.0f}%\n\n"
        f"⚠️ تعليمي فقط. تحقق من الأسعار الحية في منصتك قبل أي قرار."
    )


def run_once():
    try:
        send_telegram(build_message())
    except Exception as e:
        send_telegram(f"حدث خطأ في البوت: {e}")


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
