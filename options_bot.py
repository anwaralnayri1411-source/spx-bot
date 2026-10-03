"""بوت إشارات خيارات SPX -> تليجرام (لا ينفذ أي أوامر تداول)"""

import argparse
import html
import math
import os
import time
from datetime import datetime

import pandas as pd
import requests
import yfinance as yf

# ====== الإعدادات ======
SYMBOL = "^SPX"          # يمكنك تغييره إلى "SPY" إن لم تتوفر بيانات SPX
WIDTH = 25               # عرض السبريد المستهدف بالنقاط (استخدم 5 مع SPY)
MAX_WIDTH_FACTOR = 2     # أقصى انحراف مقبول عن العرض المستهدف
TARGET_DTE = 35          # الأيام المستهدفة حتى الانتهاء
TARGET_DELTA = 0.18      # دلتا البيع المستهدفة
MIN_CREDIT_RATIO = 0.15  # أقل نسبة (العائد / العرض) لقبول الصفقة
RISK_FREE = 0.04         # سعر الفائدة التقريبي
LOOP_HOURS = 6

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

LINE = "━━━━━━━━━━━━━━━"


def send_telegram(text):
    print(text)
    if not TOKEN or not CHAT_ID:
        print("\n[تنبيه] لم تُضبط TELEGRAM_TOKEN / TELEGRAM_CHAT_ID، فلم تُرسل الرسالة.")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
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


def no_trade(S, sma50, reason):
    trend = "صاعد ✅" if S >= sma50 else "غير صاعد ⚠️"
    return (
        f"📊 <b>تقرير {html.escape(SYMBOL)}</b>\n"
        f"{LINE}\n"
        f"💲 السعر الحالي: <b>{S:,.2f}</b>\n"
        f"📈 متوسط 50 يوم: {sma50:,.2f}\n"
        f"🧭 الاتجاه: {trend}\n"
        f"{LINE}\n"
        f"🚫 <b>لا توجد صفقة اليوم</b>\n"
        f"السبب: {reason}\n\n"
        f"💡 الانتظار قرار سليم، فلا يجب الدخول إلا عند توفر الشروط."
    )


def build_message():
    t = yf.Ticker(SYMBOL)

    hist = t.history(period="6mo")
    if hist.empty or len(hist) < 50:
        return "لا توجد بيانات كافية حالياً."
    close = hist["Close"]
    S = float(close.iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1])

    if S < sma50:
        return no_trade(S, sma50, "السعر تحت متوسط 50 يوم، والاتجاه ليس صاعداً، واستراتيجيتنا تدخل مع الاتجاه الصاعد فقط.")

    picked = pick_expiry(t)
    if not picked:
        return no_trade(S, sma50, "لا يوجد تاريخ انتهاء مناسب (بين 21 و60 يوماً).")
    exp, dte = picked

    all_puts = t.option_chain(exp).puts.copy()
    T = dte / 365
    puts = all_puts[(all_puts["strike"] < S) & (all_puts["impliedVolatility"] > 0.01)].copy()
    if puts.empty:
        return no_trade(S, sma50, "لا توجد عقود مناسبة في سلسلة الخيارات.")

    puts["delta"] = puts.apply(
        lambda r: abs(put_delta(S, r["strike"], T, r["impliedVolatility"])), axis=1
    )
    puts = puts.dropna(subset=["delta"])
    if puts.empty:
        return no_trade(S, sma50, "تعذر حساب الدلتا للعقود المتاحة.")
    short = puts.iloc[(puts["delta"] - TARGET_DELTA).abs().argsort().iloc[0]]

    # اختيار أقرب سعر تنفيذ متاح للشراء (أدنى من سعر البيع بمقدار العرض المستهدف)
    target_long = short["strike"] - WIDTH
    candidates = all_puts[all_puts["strike"] < short["strike"]]
    if candidates.empty:
        return no_trade(S, sma50, "لا يوجد عقد أدنى لإكمال السبريد.")
    long = candidates.iloc[(candidates["strike"] - target_long).abs().argsort().iloc[0]]
    long_strike = float(long["strike"])
    short_strike = float(short["strike"])
    width = short_strike - long_strike

    if width <= 0 or width > WIDTH * MAX_WIDTH_FACTOR:
        return no_trade(
            S, sma50,
            f"لا يوجد سعر تنفيذ قريب من {target_long:,.0f} لإكمال السبريد (أقرب متاح {long_strike:,.0f}).",
        )

    credit = mid_price(short) - mid_price(long)
    if credit <= 0:
        return no_trade(S, sma50, "الأسعار الحالية غير صالحة (قد يكون السوق مغلقاً).")

    max_loss = width - credit
    ratio = credit / width
    pop = (1 - float(short["delta"])) * 100

    if ratio < MIN_CREDIT_RATIO:
        return no_trade(
            S, sma50,
            f"أفضل سبريد متاح يعطي عائداً ضعيفاً ({ratio:.0%} من عرض السبريد)، والمخاطرة لا تستحق.",
        )

    dist_pct = (S - short_strike) / S * 100
    breakeven = short_strike - credit
    take_profit = credit * 0.5
    stop_price = min(credit * 2, width * 0.8)

    return (
        f"📊 <b>إشارة خيارات {html.escape(SYMBOL)}</b>\n"
        f"{LINE}\n"
        f"💲 السعر الحالي: <b>{S:,.2f}</b>\n"
        f"📈 متوسط 50 يوم: {sma50:,.2f}\n"
        f"{LINE}\n\n"
        f"🎯 <b>هدف الدخول</b>\n"
        f"نتوقع أن المؤشر سيبقى <b>فوق {short_strike:,.0f}</b> حتى {exp}. "
        f"نستلم مبلغاً مقدماً (علاوة)، ونحتفظ به كاملاً إذا لم ينزل المؤشر تحت هذا المستوى.\n\n"
        f"🧭 <b>لماذا هذه الصفقة الآن؟</b>\n"
        f"• الاتجاه صاعد: السعر فوق متوسط 50 يوم\n"
        f"• مسافة أمان: سعر البيع أقل من السعر الحالي بنحو {dist_pct:.1f}%\n"
        f"• احتمال الربح التقريبي: <b>{pop:.0f}%</b>\n\n"
        f"🛒 <b>التنفيذ (أمر واحد: Bull Put Credit Spread)</b>\n"
        f"1️⃣ بيع Put بسعر تنفيذ <b>{short_strike:,.0f}</b>\n"
        f"2️⃣ شراء Put بسعر تنفيذ <b>{long_strike:,.0f}</b> (حماية من الخسارة الكبيرة)\n"
        f"📆 الانتهاء: {exp} (بعد {dte} يوم)\n"
        f"💵 سعر الأمر (Limit): استلام ≈ <b>{credit:.2f}</b> نقطة\n\n"
        f"💰 <b>الأرقام لكل عقد</b>\n"
        f"✅ أقصى ربح: <b>{credit * 100:,.0f}$</b>\n"
        f"❌ أقصى خسارة: <b>{max_loss * 100:,.0f}$</b>\n"
        f"⚖️ نقطة التعادل: {breakeven:,.0f}\n\n"
        f"🚪 <b>خطة الخروج (اقتراح)</b>\n"
        f"• جني الربح: أغلق السبريد عندما يصل سعره إلى ≈ {take_profit:.2f} (ربح نصف العلاوة)\n"
        f"• وقف الخسارة: أغلقه إذا ارتفع سعره إلى ≈ {stop_price:.2f}\n"
        f"• لا تنتظر الأيام الأخيرة: أغلقه قبل الانتهاء بنحو 7 أيام\n\n"
        f"⚠️ <i>للتعلم فقط وليست توصية مالية. الأسعار تقريبية، فتحقق من الأسعار الحية في منصتك قبل أي قرار، "
        f"ولا تخاطر بأكثر مما تتحمل خسارته.</i>"
    )


def run_once():
    try:
        send_telegram(build_message())
    except Exception as e:
        send_telegram(f"⚠️ حدث خطأ في البوت: {html.escape(str(e))}")


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
