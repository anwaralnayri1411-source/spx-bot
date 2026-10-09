"""ملخص ما قبل الافتتاح: رسالة واحدة قبل افتتاح السوق الأمريكي بنحو 15 دقيقة.
لا يعطي توصية دخول. يجمع ما حدث في الليل ويذكّر بالإحصاء التاريخي للفجوة.
البيانات مجانية ومتأخرة قليلاً (yfinance)."""
import os
import sys
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

import options_bot as ob
import stocks_bot as sb

FORCE = os.getenv("FORCE", "0") == "1"
WINDOW = ((9, 0), (9, 40))                      # بتوقيت نيويورك
HOLIDAYS = {"2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26"}
GAP_T = 0.004
MIN_PM_VOL = 20000                              # أقل حجم تداول قبل الافتتاح لعرض السهم
MOVE_MIN = 0.015                                # أقل تحرك لعرض السهم
LINE = ob.LINE
# من research/overnight_results.md (فجوة SPX الفعلية، 2010-2026)
STATS = {
    "up": "بعد فجوة صاعدة 0.4% أو أكثر أغلقت الجلسة صاعدة في ~66-68% من الأيام (المعدل العام ~54%)",
    "down": "بعد فجوة هابطة 0.4% أو أكثر أغلقت الجلسة هابطة في ~56-58% من الأيام (المعدل العام ~46%)",
    "flat": "الفجوة المحايدة (أقل من 0.4%) لا تعطي أفضلية تُذكر",
}


def chg_series(sym, period="5d", interval="1d"):
    try:
        h = yf.Ticker(sym).history(period=period, interval=interval)["Close"].dropna()
        return h
    except Exception:
        return pd.Series(dtype=float)


def daily_change(sym):
    h = chg_series(sym)
    if len(h) < 2:
        return None
    return float(h.iloc[-1] / h.iloc[-2] - 1)


def es_gap(n):
    """تغير عقود ES منذ إغلاق نيويورك أمس 16:00 (تقدير فجوة الافتتاح)."""
    try:
        h = yf.Ticker("ES=F").history(period="5d", interval="15m")["Close"].dropna()
        h.index = h.index.tz_convert(ob.NY)
        d = n.date() - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        ref = pd.Timestamp(datetime(d.year, d.month, d.day, 16, 0, tzinfo=ob.NY))
        before = h[h.index <= ref]
        if before.empty:
            return None, None
        return float(h.iloc[-1] / before.iloc[-1] - 1), float(h.iloc[-1])
    except Exception:
        return None, None


def movers(n):
    tickers = list(sb.WATCHLIST)
    out = []
    try:
        daily = yf.download(tickers, period="7d", interval="1d", auto_adjust=False, progress=False)
        intra = yf.download(tickers, period="1d", interval="5m", prepost=True, auto_adjust=False, progress=False)
        today = n.date()
        for tk in tickers:
            try:
                dc = daily["Close"][tk].dropna()
                dc = dc[[d.date() < today for d in dc.index]]
                ic = intra["Close"][tk].dropna()
                iv = intra["Volume"][tk].dropna()
                if dc.empty or ic.empty:
                    continue
                ch = float(ic.iloc[-1] / dc.iloc[-1] - 1)
                vol = float(iv.sum())
                if abs(ch) >= MOVE_MIN and vol >= MIN_PM_VOL:
                    out.append((tk, ch, vol, float(ic.iloc[-1])))
            except Exception:
                continue
    except Exception:
        pass
    out.sort(key=lambda x: -abs(x[1]))
    return out


def fmt(x):
    return "؟" if x is None else f"{x * 100:+.2f}%"


def build(n):
    gap, es_px = es_gap(n)
    world = [("نيكاي", "^N225"), ("هانغ سنغ", "^HSI"), ("فوتسي لندن", "^FTSE"), ("داكس", "^GDAXI")]
    other = [("النفط", "CL=F"), ("الذهب", "GC=F"), ("بيتكوين", "BTC-USD")]
    vix, vix_pct = ob.vix_info()
    tnx = chg_series("^TNX", "7d")
    L = ["🌅 <b>ملخص ما قبل الافتتاح</b> #ملخص", f"🕒 {n.astimezone(ob.RY).strftime('%H:%M')} الرياض | الافتتاح بعد ~{max(0, (9 * 60 + 30) - (n.hour * 60 + n.minute))} دقيقة", ""]
    if gap is None:
        L.append("📊 تعذر حساب فجوة ES الآن.")
    else:
        if gap >= GAP_T:
            icon, key = "🟢", "up"
        elif gap <= -GAP_T:
            icon, key = "🔴", "down"
        else:
            icon, key = "⚪", "flat"
        L.append(f"{icon} <b>فجوة الافتتاح المتوقعة (ES): {fmt(gap)}</b>" + (f" | ES {es_px:,.0f}" if es_px else ""))
        L.append(f"📚 {STATS[key]}")
        L.append("ملاحظة: هذا احتمال تاريخي لاتجاه المؤشر، وليس ربح عقود.")
    L += ["", f"🌍 <b>الأسواق</b>"]
    L.append(" | ".join(f"{nm} {fmt(daily_change(sy))}" for nm, sy in world))
    L.append(" | ".join(f"{nm} {fmt(daily_change(sy))}" for nm, sy in other))
    bits = []
    if vix is not None:
        bits.append(f"VIX {vix:.1f} (أعلى من {vix_pct:.0f}% من أيام السنة)")
    if len(tnx) >= 2:
        bits.append(f"عائد 10 سنوات {float(tnx.iloc[-1]):.2f}% ({float(tnx.iloc[-1] - tnx.iloc[-2]):+.2f})")
    if bits:
        L.append(" | ".join(bits))
    try:
        import levels
        d = levels.compute(n)
        levels.log(d)
        L += ["", "📐 <b>مستويات SPX</b>", levels.block(d)]
    except Exception as e:
        print("تعذر حساب المستويات:", e)
    ev = [(d, nm) for d, nm in ob.upcoming_events(n.date(), 1)]
    if ev:
        L += ["", "📅 <b>أحداث قريبة</b>"] + [f"• {'اليوم' if d == n.date() else 'غداً'}: {nm}" for d, nm in ev]
    mv = movers(n)
    if mv:
        L += ["", "🔥 <b>أبرز تحركات الأسهم قبل الافتتاح</b>"]
        for tk, ch, vol, px in mv[:6]:
            L.append(f"{'🟢' if ch > 0 else '🔴'} {tk} {fmt(ch)} | ${px:,.2f} | حجم {vol:,.0f}")
    L += ["", "⚠️ <i>تعليمي وليست توصية. البيانات مجانية ومتأخرة، وبوت SPX يعطي التوصيات بعد الافتتاح.</i>"]
    return "\n".join(L)


def main():
    n = ob.now_ny()
    ok = n.weekday() < 5 and n.date().isoformat() not in HOLIDAYS and WINDOW[0] <= (n.hour, n.minute) <= WINDOW[1]
    if not ok and not FORCE:
        print("خارج نافذة ما قبل الافتتاح أو يوم إجازة.")
        return
    ob.send_telegram(build(n))


if __name__ == "__main__":
    main()
