#!/usr/bin/env python3
"""مقارنة درجة الأخبار: تصنيف Claude (من ذاكرة السوابق) مقابل عدّ الكلمات في بوت الشركات (قراءة فقط، لا تغيّر أي توصية).

السؤال: عند لحظة قرار البوت (10:15 نيويورك)، هل درجة أخبار الشركة بتصنيف Claude تتوقع حركة السهم بعدها أفضل من درجة الكلمات الحالية (`stocks_bot.news_points`)؟
بلا استدعاء جديد لـ Claude: تصنيفات news_memory_log.csv (expect/importance/new) موجودة أصلاً (Haiku كل 30 دقيقة).

الأخبار المحسوبة لكل (يوم، شركة): العناوين المنشورة بين إغلاق آخر يوم تداول و10:15 من اليوم فقط (لا نظرة لاحقة).
  - درجة Claude = مجموع الأخبار «الجديدة» بـ expect صعود/هبوط، بوزن الأهمية (منخفض 0.5، متوسط 1، عال 2)، موجبة للصعود وسالبة للهبوط.
  - درجة الكلمات = bull − bear من news_points على العناوين نفسها (±2 كحد أقصى).
القياس: حركة السهم فوق السوق (r − بيتا × SPY؛ بيتا من 60 يوماً قبل اليوم) من سعر 10:15 حتى إغلاق اليوم (ar_day) وحتى إغلاق اليوم التالي (ar_next).
الإحصاء: لكل طريقة ولكل أفق، على الحالات ذات الدرجة ≠ 0: نسبة إصابة الاتجاه ومتوسط الحركة بإشارة الدرجة وفاصل 95% بإعادة معاينة الأيام (العينة الفعلية = الأيام)،
مع مرجع عشوائي (خلط الإشارات)، وقسم لحالات الاختلاف (Claude يخالف الكلمات).
الفرضية المسجّلة مسبقاً (CLAUDE.md، 23/10): المقياس الأساسي = متوسط ar_next بإشارة درجة Claude على الأيام من JUDGE_FROM؛ لا حكم قبل MIN_SCORED حالة، وتتحقق حين لا يشمل فاصل 95% الصفر.
⚠️ وقت نشر Google News قد يتأخر عن الحدث (عنوان بعد الحركة): لذلك لا نقيس إلا بعد 10:15، ولا نحسب شيئاً سبق القرار في الحركة. لا قرار على news_points قبل ظل ثانٍ وموافقة المستخدم.
تشغيل: python research/news_score_compare.py [--no-fetch]   المخرجات: news_score_compare.csv، research/news_score_compare_results.md
"""
import os
import random
import sys
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

NY = ZoneInfo("America/New_York")
NEWS_LOG = os.path.join(ROOT, "news_memory_log.csv")
DATA = os.path.join(ROOT, "news_score_compare.csv")
OUT = os.path.join(HERE, "news_score_compare_results.md")
COLS = ["day", "ticker", "n_heads", "n_scored", "claude", "words", "anchor", "ar_day", "ar_next", "partial"]
ANCHOR = time(10, 15)                      # لحظة القرار: أول فحص للبوت تقريباً
ANCHOR_BAR_START = time(10, 10)            # الشمعة 5د التي يُغلق سعرها عند 10:15
WEIGHT = {"low": 0.5, "medium": 1.0, "high": 2.0}
JUDGE_FROM = "2026-10-24"                  # الحكم على الأيام الجديدة فقط
MIN_SCORED = 100
FIRST_FULL_DAY = "2026-10-23"              # الذاكرة تبدأ 22/10 05:10 UTC: نافذة 22/10 ناقصة (partial)
BETA_DAYS = 60


# ============================ دوال نقية ============================
def trading_holidays():
    try:
        import options_bot as ob
        return set(ob.HOLIDAYS)
    except Exception:  # noqa: BLE001
        return set()


def is_trading_day(d, holidays=None):
    return d.weekday() < 5 and d.isoformat() not in (holidays if holidays is not None else trading_holidays())


def prev_trading_day(d, holidays=None):
    p = d - timedelta(days=1)
    while not is_trading_day(p, holidays):
        p -= timedelta(days=1)
    return p


def window(d, holidays=None):
    """(بداية، نهاية) نافذة أخبار يوم d: من 16:00 لآخر يوم تداول قبله إلى 10:15 من d (بتوقيت نيويورك)."""
    p = prev_trading_day(d, holidays)
    return datetime.combine(p, time(16, 0), NY), datetime.combine(d, ANCHOR, NY)


def claude_score(rows):
    """rows: DataFrame فيه new/importance/expect. موجبة للصعود وسالبة للهبوط، بوزن الأهمية، للأخبار الجديدة فقط."""
    s = 0.0
    for r in rows.itertuples():
        if int(r.new) != 1 or r.expect not in ("up", "down"):
            continue
        s += WEIGHT.get(str(r.importance), 0.5) * (1 if r.expect == "up" else -1)
    return s


def words_score(titles, news_points):
    bull, bear, _ = news_points(list(titles))
    return bull - bear


def sign(x):
    return 0 if x == 0 or pd.isna(x) else (1 if x > 0 else -1)


def beta_of(stock_close, spy_close, day, n=BETA_DAYS):
    """بيتا من عوائد آخر n يوماً قبل day (Series بفهرس تاريخ)."""
    r = pd.concat([stock_close.pct_change(), spy_close.pct_change()], axis=1, keys=["s", "m"]).dropna()
    r = r[r.index < pd.Timestamp(day).tz_localize(NY)].tail(n)
    if len(r) < 20 or r["m"].var() == 0:
        return 1.0
    return float(r["s"].cov(r["m"]) / r["m"].var())


def anchor_price(five, day):
    """سعر إغلاق شمعة 5د التي بدأت 10:10 في اليوم day (ثم أقرب شمعة سابقة في اليوم نفسه). None إن لم توجد."""
    if five is None or five.empty:
        return None
    d = five[five.index.date == day]
    d = d[d.index.time <= ANCHOR_BAR_START]
    return float(d["Close"].iloc[-1]) if len(d) else None


def abnormal(px_a, px_end, spy_a, spy_end, beta):
    if None in (px_a, px_end, spy_a, spy_end) or px_a <= 0 or spy_a <= 0:
        return None
    return (px_end / px_a - 1) - beta * (spy_end / spy_a - 1)


def close_on(daily, day):
    if daily is None or daily.empty:
        return None
    x = daily[daily.index.date == day]
    return float(x["Close"].iloc[-1]) if len(x) else None


def next_close(daily, day, holidays=None):
    d = day + timedelta(days=1)
    for _ in range(6):
        if is_trading_day(d, holidays):
            return close_on(daily, d)
        d += timedelta(days=1)
    return None


# ============================ بناء الصفوف ============================
def day_rows(news, day, tickers, news_points, prices, now=None, holidays=None):
    """صفوف يوم day: لكل شركة لها خبر في النافذة. prices = {ticker: (five, daily)} مع SPY. now: لتجنب إغلاق لم يحدث بعد."""
    now = now or datetime.now(NY)
    t0, t1 = window(day, holidays)
    inwin = news[(news["t"] > t0) & (news["t"] <= t1)]
    out = []
    spy_five, spy_daily = prices.get("SPY", (None, None))
    closed_day = now >= datetime.combine(day, time(16, 10), NY)
    for tk in tickers:
        g = inwin[inwin["ticker"] == tk].drop_duplicates("key")
        if g.empty:
            continue
        c, w = claude_score(g), words_score(g["title"], news_points)
        if c == 0 and w == 0:
            continue
        five, daily = prices.get(tk, (None, None))
        a, sa = anchor_price(five, day), anchor_price(spy_five, day)
        ar_day = ar_next = None
        if a is not None and sa is not None and daily is not None and spy_daily is not None:
            beta = beta_of(daily["Close"], spy_daily["Close"], day)
            if closed_day:
                ar_day = abnormal(a, close_on(daily, day), sa, close_on(spy_daily, day), beta)
                ar_next = abnormal(a, next_close(daily, day, holidays), sa, next_close(spy_daily, day, holidays), beta)
        out.append({"day": day.isoformat(), "ticker": tk, "n_heads": len(g), "n_scored": int(((g["new"] == 1) & g["expect"].isin(["up", "down"])).sum()),
                    "claude": round(c, 2), "words": round(w, 2), "anchor": a if a is None else round(a, 2),
                    "ar_day": ar_day if ar_day is None else round(ar_day, 5), "ar_next": ar_next if ar_next is None else round(ar_next, 5),
                    "partial": int(day.isoformat() < FIRST_FULL_DAY)})
    return out


def merge_rows(old, new):
    """القديم يبقى إلا ما كان ناقص ar_next/ar_day فيُستبدل بالأحدث (عند توفر البيانات)."""
    key = lambda r: (str(r["day"]), str(r["ticker"]))
    cur = {key(r): r for r in old}
    for r in new:
        k = key(r)
        o = cur.get(k)
        if o is None or any(pd.isna(o.get(c)) and not pd.isna(r.get(c)) for c in ("ar_day", "ar_next", "anchor")):
            cur[k] = r
    return sorted(cur.values(), key=key)


# ============================ الإحصاء ============================
def signed(df, method, horizon):
    d = df[(df[method] != 0) & df[f"ar_{horizon}"].notna()].copy()
    d["sgn"] = d[method].map(sign)
    d["val"] = d["sgn"] * d[f"ar_{horizon}"].astype(float)
    return d


def boot_ci(d, n_boot=2000, seed=11):
    """فاصل 95% لمتوسط val بإعادة معاينة الأيام. None إن قلّت الأيام عن 3."""
    by = {k: g["val"].tolist() for k, g in d.groupby("day")}
    days = list(by)
    if len(days) < 3:
        return None
    rng = random.Random(seed)
    m = []
    for _ in range(n_boot):
        vals = [v for k in (rng.choice(days) for _ in days) for v in by[k]]
        m.append(sum(vals) / len(vals))
    m.sort()
    return m[int(0.025 * n_boot)], m[int(0.975 * n_boot) - 1]


def null_mean(d, n=1000, seed=3):
    """مرجع عشوائي: نفس القيم المطلقة مع إشارات مخلوطة؛ يرجع (متوسط، انحراف) توزيع المتوسط."""
    rng = np.random.default_rng(seed)
    a, s = d["ar_val"].to_numpy(), d["sgn"].to_numpy()
    ms = [float((rng.permutation(s) * a).mean()) for _ in range(n)]
    return float(np.mean(ms)), float(np.std(ms))


def summary(d):
    if d.empty:
        return None
    d = d.assign(ar_val=d["val"] * d["sgn"])        # الحركة نفسها بلا إشارة الدرجة
    ci = boot_ci(d)
    nm, ns = null_mean(d)
    return {"n": len(d), "days": d["day"].nunique(), "hit": float((d["val"] > 0).mean()), "mean": float(d["val"].mean()), "ci": ci,
            "null": (nm, ns)}


def cases(n):
    return f"{n} حالة" if n == 1 or n > 10 else f"{n} حالات"


def fmt(s):
    if s is None:
        return "لا حالات"
    ci = "فاصل: أيام قليلة" if s["ci"] is None else f"فاصل 95%: {s['ci'][0] * 100:+.2f}…{s['ci'][1] * 100:+.2f}%"
    return (f"{cases(s['n'])}/{s['days']} يوماً، إصابة {s['hit']:.0%}، متوسط {s['mean'] * 100:+.2f}% ({ci}؛ عشوائي {s['null'][0] * 100:+.2f}±{s['null'][1] * 100:.2f}%)")


def verdict(s):
    if s is None or s["n"] < MIN_SCORED:
        return f"لا حكم بعد ({0 if s is None else s['n']} من {MIN_SCORED})"
    if s["ci"] is None:
        return "لا حكم بعد (أيام قليلة)"
    lo, hi = s["ci"]
    if lo > 0:
        return "تحققت الفرضية: درجة Claude تتوقع الحركة (الفاصل فوق الصفر)"
    if hi < 0:
        return "عكسية: درجة Claude تعاكس الحركة (الفاصل تحت الصفر)"
    return "لم تتحقق بعد: الفاصل يشمل الصفر"


def disagreement(df, horizon):
    """أيام يختلف فيها اتجاه Claude عن الكلمات (كلاهما ≠ 0): أيهما أصاب؟"""
    d = df[(df["claude"] != 0) & (df["words"] != 0) & df[f"ar_{horizon}"].notna()]
    d = d[d["claude"].map(sign) != d["words"].map(sign)]
    if d.empty:
        return None
    ar = d[f"ar_{horizon}"].astype(float)
    return {"n": len(d), "claude": int((d["claude"].map(sign) * ar > 0).sum()), "words": int((d["words"].map(sign) * ar > 0).sum())}


def report(rows):
    df = pd.DataFrame(rows, columns=COLS)
    for c in ("claude", "words", "ar_day", "ar_next"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    L = ["# مقارنة درجة الأخبار: Claude مقابل عدّ الكلمات (نتائج آلية)", "",
         "- الأخبار المنشورة قبل 10:15 نيويورك فقط؛ الحركة فوق السوق من سعر 10:15 (r − بيتا × SPY). لا تغيّر هذه الدراسة أي توصية.",
         f"- **الفرضية المسجّلة مسبقاً:** متوسط ar_next بإشارة درجة Claude على الأيام من {JUDGE_FROM} > 0 بفاصل 95% لا يشمل الصفر، بعد {MIN_SCORED} حالة على الأقل.",
         "- ⚠️ العنوان قد يُنشر بعد الحركة (تأخر Google News)؛ لهذا لا نقيس إلا بعد 10:15. العينة صغيرة في البداية والحكم بالأيام لا بالحالات.",
         f"- صفوف: {len(df)} (يوم-شركة) | أيام: {df['day'].nunique() if len(df) else 0}", ""]
    if df.empty:
        return L + ["لا بيانات بعد."]
    judged, early = df[df["day"] >= JUDGE_FROM], df[df["day"] < JUDGE_FROM]
    for title, part in ((f"الحكم (من {JUDGE_FROM})", judged), (f"استكشاف (قبل {JUDGE_FROM}، لا يُحكم عليه)", early)):
        L += [f"## {title}", ""]
        for horizon, hn in (("next", "حتى إغلاق اليوم التالي (الأساسي)"), ("day", "حتى إغلاق اليوم")):
            L.append(f"**{hn}:**")
            for method, mn in (("claude", "Claude"), ("words", "الكلمات")):
                s = summary(signed(part, method, horizon))
                L.append(f"- {mn}: {fmt(s)}")
            dis = disagreement(part, horizon)
            L.append("- عند الاختلاف: " + ("لا حالات" if dis is None else f"{cases(dis['n'])}؛ أصاب Claude في {dis['claude']} والكلمات في {dis['words']}"))
            L.append("")
        if part is judged:
            L.append(f"⚖️ **الحكم على الفرضية:** {verdict(summary(signed(judged, 'claude', 'next')))}")
            L.append("")
    return L


# ============================ التنفيذ ============================
def load_news():
    d = pd.read_csv(NEWS_LOG)
    d = d[d["ticker"] != "MARKET"].copy()
    d["t"] = pd.to_datetime(d["time_utc"], utc=True).dt.tz_convert(NY)
    d["new"] = pd.to_numeric(d["new"], errors="coerce").fillna(0).astype(int)
    return d


def fetch_prices(tickers):
    import yfinance as yf
    syms = sorted(set(tickers) | {"SPY"})
    five = yf.download(syms, period="60d", interval="5m", group_by="ticker", auto_adjust=True, progress=False, threads=True)
    daily = yf.download(syms, period="6mo", interval="1d", group_by="ticker", auto_adjust=True, progress=False, threads=True)

    def frame(raw, tk, intraday):
        try:
            d = raw[tk].dropna(subset=["Close"]).copy()
            d.index = d.index.tz_convert(NY) if d.index.tz is not None else d.index.tz_localize(NY)
            return d
        except Exception:  # noqa: BLE001
            return None
    return {tk: (frame(five, tk, True), frame(daily, tk, False)) for tk in syms}


def main(argv):
    from stocks_bot import WATCHLIST, news_points
    news = load_news()
    tickers = [t for t in news["ticker"].unique() if t in WATCHLIST]
    first = news["t"].min().date()
    today = datetime.now(NY).date()
    days = [first + timedelta(days=i) for i in range((today - first).days + 1)]
    days = [d for d in days if is_trading_day(d) and d >= first]
    old = pd.read_csv(DATA).to_dict("records") if os.path.exists(DATA) else []
    prices = {} if "--no-fetch" in argv else fetch_prices(tickers)
    new = []
    for d in days:
        new += day_rows(news, d, tickers, news_points, prices)
    rows = merge_rows(old, new)
    pd.DataFrame(rows, columns=COLS).to_csv(DATA, index=False, lineterminator="\n")
    L = report(rows)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
