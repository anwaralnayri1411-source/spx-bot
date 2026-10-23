#!/usr/bin/env python3
"""سجل «الأيام النادرة» (ظل، تسجيل فقط؛ لا يرسل ولا يغيّر أي ملف للبوت) — بطلب المستخدم 23/10/2026.

السؤال: ما العقود الرخيصة خارج المال التي تقفز +100% أو أكثر في SPX (0DTE)؟ ومتى (يوم الأسبوع، قرب الإغلاق...)؟ وما حال السوق
في تلك الأيام (الحركة اليومية، الفجوة، VIX، قرارات وتقارير، انتهاء الخيارات الشهري)؟ ولنختبر بالبيانات ملاحظة المستخدم أن
هذه القفزات تكثر في آخر يوم تداول من الأسبوع.

لكل يوم تداول بعد الإغلاق، ونطاق سترايكات ±2.5% حول سعر الافتتاح (خارج المال فقط عند كل وقت دخول):
  - أوقات دخول افتراضية: 10:00 و11:00 و12:00 و13:00 و14:00 نيويورك؛ نقبل العقد إن كان سعر دخوله بين 0.50 و3.50 (50–350$).
  - الأسعار الحقيقية: شموع صفقات Alpaca 5د لعقد SPXW نفسه (نفس مصدر real_price_check). الدخول بافتتاح أول شمعة ≥ وقت الدخول
    بزيادة نصف فرق 1.5% (المقاس في 19–22/10) + عمولة؛ والبيع بإغلاق الشمعة ناقص نصف الفرق. شموع الصفقات ليست عرض/طلب، فالأرقام تقريبية.
  - لكل (عقد، وقت دخول): أعلى عائد بلغه حتى 15:55 (بإغلاق الشموع، وبأعلى الشموع كحد أعلى)، وعائد بعد 60 دقيقة، وعائد آخر شمعة.
  - «حدث نادر» = أعلى عائد ≥ +100%.
المخرجات (ملفات جديدة): tail_log_days.csv (صف لكل يوم: سياق السوق وأعداد)، tail_log_events.csv (صف لكل حدث نادر).
تنبيه: القمة أعلى عائد لحظي لا يُلتقط عادة؛ المقياس الأهم متوسط العوائد وحجم العينة. أيام قليلة = لا حكم.
يحتاج: ALPACA_KEY/ALPACA_SECRET. تشغيل: python research/tail_log.py [--backfill N] [--catchup N] [--days YYYY-MM-DD,...]
  بلا خيارات: يومان أخيران (اليوم والأمس) إن لم يكونا مسجَّلين. --backfill 60: كل الأيام المتاحة لياهو (للبدء مرة واحدة).
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NY = ZoneInfo("America/New_York")
DAYS = os.path.join(ROOT, "tail_log_days.csv")
EVENTS = os.path.join(ROOT, "tail_log_events.csv")
CACHE = os.path.join(HERE, "spx_5m_cache.csv")
ENTRY_TIMES = (10 * 60, 11 * 60, 12 * 60, 13 * 60, 14 * 60)     # دقائق من منتصف الليل (نيويورك)
LAST_EXIT_M = 15 * 60 + 55
PRICE_MIN, PRICE_MAX = 0.50, 3.50
STRIKE_BAND = 0.025
HS = 0.015                    # نصف فرق العرض/الطلب (قيس من Alpaca 19–22/10)
COMMISSION = 0.0065
TAIL = 1.0                    # +100%
DAY_COLS = ["date", "dow", "opex_month", "spx_open", "spx_close", "spx_high", "spx_low", "range_pct", "gap_pct", "vix_close",
            "vix_chg_pct", "events", "contracts", "entries", "ge100", "ge300", "max_peak", "mean_ret60", "mean_close"]
EVENT_COLS = ["date", "dow", "entry_time", "side", "strike", "entry", "dist_pts", "peak_ret", "peak_ret_high", "peak_time",
              "mins_to_peak", "ret60", "ret_close"]
EVENT_DAYS = {                # قرارات وتقارير معروفة (للسياق فقط)
    "2026-07-29": "FOMC", "2026-09-16": "FOMC", "2026-10-28": "FOMC", "2026-12-09": "FOMC",
    "2026-10-14": "CPI", "2026-11-10": "CPI", "2026-12-10": "CPI", "2026-10-15": "PPI",
}


# ============================ دوال نقية (قابلة للاختبار) ============================
def occ(exp, side, strike):
    return f"SPXW{exp.strftime('%y%m%d')}{'C' if side == 'CALL' else 'P'}{int(round(strike * 1000)):08d}"


def is_monthly_opex(d):
    return d.weekday() == 4 and 15 <= d.day <= 21


def strike_grid(open_px):
    lo, hi = open_px * (1 - STRIKE_BAND), open_px * (1 + STRIKE_BAND)
    return [float(k) for k in range(int(lo // 5 * 5), int(hi // 5 * 5) + 6, 5)]


def spot_at(spx, m):
    """سعر SPX عند الدقيقة m: افتتاح أول شمعة تبدأ عند m أو بعدها (أول 10 دقائق)."""
    x = spx[(spx["m"] >= m) & (spx["m"] <= m + 10)]
    return None if x.empty else float(x["Open"].iloc[0])


def entry_price(bars, m):
    """سعر دخول عقد: افتتاح أول شمعة صفقات ≥ m (حتى 10 دقائق) + نصف الفرق + عمولة. None إن لم يُتداول حول الدخول."""
    first = bars[(bars.index >= m) & (bars.index <= m + 10)]
    if first.empty:
        return None
    return float(first["o"].iloc[0]) * (1 + HS) + COMMISSION


def follow(bars, m, entry):
    """بعد الدخول عند m: (أعلى عائد بالإغلاق، أعلى عائد بأعلى الشمعة، دقيقة القمة، عائد بعد 60د، عائد آخر شمعة)."""
    after = bars[(bars.index >= m) & (bars.index + 5 <= LAST_EXIT_M)]
    if after.empty:
        return None
    sell = lambda p: (float(p) * (1 - HS) - COMMISSION) / entry - 1
    closes = after["c"].apply(sell)
    peak_i = closes.idxmax()
    highs = after["h"].apply(sell) if "h" in after else closes
    t60 = after[after.index + 5 >= m + 60]
    ret60 = sell(t60["c"].iloc[0]) if not t60.empty else float(closes.iloc[-1])
    return float(closes.max()), float(highs.max()), int(peak_i + 5), float(ret60), float(closes.iloc[-1])


def candidate_legs(open_px, spots):
    """كل (وقت دخول، اتجاه، سترايك) خارج المال عند ذلك الوقت. spots: {m: سعر SPX}."""
    legs = []
    for m in ENTRY_TIMES:
        S = spots.get(m)
        if S is None:
            continue
        for K in strike_grid(open_px):
            if K > S:
                legs.append((m, "CALL", K))
            elif K < S:
                legs.append((m, "PUT", K))
    return legs


def analyze_day(d, spx, bars_by_sym, ctx):
    """يرجع (صف اليوم، قائمة الأحداث النادرة). spx: شموع اليوم (m، Open/High/Low/Close)."""
    open_px = float(spx["Open"].iloc[0])
    spots = {m: spot_at(spx, m) for m in ENTRY_TIMES}
    legs = candidate_legs(open_px, spots)
    events, rets60, retsc, peaks = [], [], [], []
    syms_seen = set()
    for m, side, K in legs:
        sym = occ(d, side, K)
        bars = bars_by_sym.get(sym)
        if bars is None or bars.empty:
            continue
        e = entry_price(bars, m)
        if e is None or not (PRICE_MIN <= e <= PRICE_MAX):
            continue
        f = follow(bars, m, e)
        if f is None:
            continue
        peak, peak_hi, peak_m, r60, rc = f
        syms_seen.add(sym)
        peaks.append(peak); rets60.append(r60); retsc.append(rc)
        if peak >= TAIL:
            events.append({"date": d.isoformat(), "dow": d.weekday(), "entry_time": f"{m // 60:02d}:{m % 60:02d}",
                           "side": side, "strike": K, "entry": round(e, 2), "dist_pts": round(abs(K - spots[m]), 1),
                           "peak_ret": round(peak, 3), "peak_ret_high": round(peak_hi, 3),
                           "peak_time": f"{peak_m // 60:02d}:{peak_m % 60:02d}", "mins_to_peak": peak_m - m,
                           "ret60": round(r60, 3), "ret_close": round(rc, 3)})
    pk = np.array(peaks) if peaks else np.array([])
    day = {"date": d.isoformat(), "dow": d.weekday(), "opex_month": int(is_monthly_opex(d)),
           "spx_open": round(open_px, 2), "spx_close": round(float(spx["Close"].iloc[-1]), 2),
           "spx_high": round(float(spx["High"].max()), 2), "spx_low": round(float(spx["Low"].min()), 2),
           "range_pct": round((float(spx["High"].max()) - float(spx["Low"].min())) / open_px * 100, 3),
           "gap_pct": ctx.get("gap_pct", ""), "vix_close": ctx.get("vix_close", ""), "vix_chg_pct": ctx.get("vix_chg_pct", ""),
           "events": ctx.get("events", ""), "contracts": len(syms_seen), "entries": len(pk),
           "ge100": int((pk >= 1.0).sum()) if len(pk) else 0, "ge300": int((pk >= 3.0).sum()) if len(pk) else 0,
           "max_peak": round(float(pk.max()), 3) if len(pk) else "",
           "mean_ret60": round(float(np.mean(rets60)), 3) if rets60 else "",
           "mean_close": round(float(np.mean(retsc)), 3) if retsc else ""}
    return day, events


# ============================ البيانات ============================
def load_spx():
    """شموع SPX 5د: المخبأ المحفوظ + ياهو (آخر 60 يوماً). فهرس نيويورك، مع عمود m (دقائق اليوم)."""
    import yfinance as yf
    frames = []
    if os.path.exists(CACHE):
        old = pd.read_csv(CACHE, index_col=0)
        old.index = pd.to_datetime(old.index, utc=True).tz_convert(NY)
        frames.append(old)
    try:
        new = yf.Ticker("^GSPC").history(period="60d", interval="5m", auto_adjust=False)[["Open", "High", "Low", "Close"]]
        new.index = new.index.tz_convert(NY) if new.index.tz is not None else new.index.tz_localize("UTC").tz_convert(NY)
        frames.append(new)
    except Exception as e:  # noqa: BLE001
        print("تعذر جلب SPX من ياهو:", type(e).__name__)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df["m"] = df.index.hour * 60 + df.index.minute
    return df[(df["m"] >= 570) & (df["m"] < 960)]


def load_context():
    """إغلاق ^GSPC اليومي وVIX: {تاريخ: (إغلاق، إغلاق السابق)} لحساب الفجوة وتغير VIX."""
    import yfinance as yf
    out = {}
    for name, sym in (("spx", "^GSPC"), ("vix", "^VIX")):
        try:
            s = yf.Ticker(sym).history(period="6mo", auto_adjust=False)["Close"].dropna()
            s.index = [x.date() for x in s.index]
            out[name] = s
        except Exception as e:  # noqa: BLE001
            print("تعذر جلب", sym, type(e).__name__)
            out[name] = pd.Series(dtype=float)
    return out


def day_ctx(d, spx_day, daily):
    c = {}
    ev = EVENT_DAYS.get(d.isoformat(), "")
    if is_monthly_opex(d):
        ev = (ev + "+" if ev else "") + "OPEX"
    c["events"] = ev
    s = daily.get("spx", pd.Series(dtype=float))
    prev = s[[i for i in s.index if i < d]]
    if len(prev):
        c["gap_pct"] = round((float(spx_day["Open"].iloc[0]) / float(prev.iloc[-1]) - 1) * 100, 3)
    v = daily.get("vix", pd.Series(dtype=float))
    if d in v.index:
        pv = v[[i for i in v.index if i < d]]
        c["vix_close"] = round(float(v[d]), 2)
        if len(pv):
            c["vix_chg_pct"] = round((float(v[d]) / float(pv.iloc[-1]) - 1) * 100, 2)
    return c


def fetch_bars(symbols, d):
    """{رمز: DataFrame(فهرس=دقيقة اليوم، o,h,l,c)} لشموع 5د لليوم d من Alpaca. أخطاء الشبكة ترفع استثناءً للمستدعي."""
    start = pd.Timestamp(datetime(d.year, d.month, d.day, 9, 30), tz=NY).tz_convert("UTC")
    end = pd.Timestamp(datetime(d.year, d.month, d.day, 16, 0), tz=NY).tz_convert("UTC")
    out = {}
    for i in range(0, len(symbols), 50):
        page = None
        while True:
            q = {"symbols": ",".join(symbols[i:i + 50]), "timeframe": "5Min", "start": start.isoformat(),
                 "end": end.isoformat(), "limit": 10000}
            if page:
                q["page_token"] = page
            req = urllib.request.Request("https://data.alpaca.markets/v1beta1/options/bars?" + urllib.parse.urlencode(q),
                                         headers={"APCA-API-KEY-ID": os.getenv("ALPACA_KEY", "").strip(),
                                                  "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET", "").strip()})
            j = json.loads(urllib.request.urlopen(req, timeout=30).read())
            for s, b in (j.get("bars") or {}).items():
                out.setdefault(s, []).extend(b)
            page = j.get("next_page_token")
            if not page:
                break
        time.sleep(0.3)
    res = {}
    for s, b in out.items():
        df = pd.DataFrame(b)
        t = pd.to_datetime(df["t"]).dt.tz_convert(NY)
        df["m"] = t.dt.hour * 60 + t.dt.minute
        res[s] = df.set_index("m")[["o", "h", "l", "c"]]
    return res


# ============================ التشغيل ============================
def logged_days():
    if not os.path.exists(DAYS):
        return set()
    return set(pd.read_csv(DAYS, usecols=["date"])["date"].astype(str))


def append_rows(path, cols, rows):
    if not rows:
        return
    new = not os.path.exists(path)
    pd.DataFrame(rows, columns=cols).to_csv(path, mode="a", header=new, index=False)


def process_day(d, spx_all, daily):
    sd = spx_all[spx_all.index.date == d]
    if len(sd) < 60:                      # يوم ناقص أو إغلاق مبكر
        print(d, "تخطي: شموع غير كافية", len(sd))
        return False
    open_px = float(sd["Open"].iloc[0])
    spots = {m: spot_at(sd, m) for m in ENTRY_TIMES}
    syms = sorted({occ(d, side, K) for _, side, K in candidate_legs(open_px, spots)})
    try:
        bars = fetch_bars(syms, d)
    except Exception as e:  # noqa: BLE001
        print(d, "تعذر جلب الشموع:", type(e).__name__)
        return False
    day, events = analyze_day(d, sd, bars, day_ctx(d, sd, daily))
    append_rows(EVENTS, EVENT_COLS, [[e[c] for c in EVENT_COLS] for e in events])
    append_rows(DAYS, DAY_COLS, [[day[c] for c in DAY_COLS]])
    print(f"{d} ({['الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة'][d.weekday()]}): عقود {day['contracts']} | "
          f"دخول {day['entries']} | ≥+100%: {day['ge100']} | أعلى {day['max_peak']}")
    return True


def main(argv):
    if not (os.getenv("ALPACA_KEY") and os.getenv("ALPACA_SECRET")):
        print("لا مفاتيح Alpaca.")
        return 0
    spx = load_spx()
    if spx.empty:
        print("لا بيانات SPX.")
        return 0
    have = logged_days()
    all_days = sorted(set(spx.index.date))
    if "--days" in argv:
        want = [date.fromisoformat(x) for x in argv[argv.index("--days") + 1].split(",")]
    elif "--backfill" in argv:
        n = int(argv[argv.index("--backfill") + 1])
        want = all_days[-n:]
    else:
        n = int(argv[argv.index("--catchup") + 1]) if "--catchup" in argv else 2
        want = all_days[-n:]
    todo = [d for d in want if d.isoformat() not in have and d in all_days]
    now = datetime.now(NY)
    todo = [d for d in todo if d < now.date() or (d == now.date() and (now.hour, now.minute) >= (16, 10))]   # اليوم بعد الإغلاق فقط
    if not todo:
        print("لا أيام جديدة للتسجيل.")
        return 0
    daily = load_context()
    done = 0
    for d in todo:
        done += bool(process_day(d, spx, daily))
    print(f"سُجّل {done} يوماً.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
