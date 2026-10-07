"""دراسة: هل تتنبأ إشارات ما قبل افتتاح السوق الأمريكي باتجاه الجلسة (الافتتاح -> الإغلاق)؟

الهدف (ما يتداوله صاحب عقود 0DTE): حركة SPX من افتتاح 9:30 إلى إغلاق 16:00 بتوقيت نيويورك.
الإشارات المتاحة قبل الافتتاح:
  - الفجوة: افتتاح اليوم / إغلاق أمس (ما سعّرته عقود ES الآجلة وما قبل الافتتاح)
  - نيكاي وهانغ سنغ: تغير يوم التداول نفسه (يُغلقان قبل افتتاح أمريكا)
  - ES: من 16:00 أمس حتى 9:00 صباحاً (بيانات ساعة، آخر ~730 يوماً)
  - DAX: من إغلاقه أمس حتى 9:00 صباحاً نيويورك (بيانات ساعة، آخر ~730 يوماً)
تنبيه: التحليل ارتباط تاريخي وليس ضماناً، والعينات المتداخلة بين الفترات قد تبالغ في الدلالة.
"""
import math
import warnings
import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")
NY = "America/New_York"
OUT = "research/premarket_results.md"


def daily(sym, start="2010-01-01"):
    df = yf.Ticker(sym).history(start=start, auto_adjust=False)
    df.index = pd.to_datetime(df.index.tz_localize(None) if df.index.tz is not None else df.index).normalize()
    return df[~df.index.duplicated()]


def hourly(sym):
    df = yf.Ticker(sym).history(period="730d", interval="1h", auto_adjust=False)
    if df.empty:
        return pd.Series(dtype=float)
    idx = df.index.tz_convert(NY) if df.index.tz is not None else df.index.tz_localize("UTC").tz_convert(NY)
    return pd.Series(df["Close"].values, index=idx)


def at(h, day, hour):
    """إغلاق شمعة الساعة التي تبدأ عند hour بتوقيت نيويورك في اليوم day."""
    ts = pd.Timestamp(day).tz_localize(NY) + pd.Timedelta(hours=hour)
    return h.get(ts, np.nan)


def corr_t(x, y):
    d = pd.concat([x, y], axis=1).dropna()
    n = len(d)
    if n < 30:
        return n, np.nan, np.nan
    r = float(d.iloc[:, 0].corr(d.iloc[:, 1]))
    t = r * math.sqrt((n - 2) / max(1e-12, 1 - r * r))
    return n, r, t


def cond(x, y, thr):
    d = pd.concat([x, y], axis=1).dropna()
    d.columns = ["x", "y"]
    up, dn = d[d.x >= thr], d[d.x <= -thr]
    f = lambda s: (len(s), float((s.y > 0).mean()) if len(s) else np.nan, float(s.y.mean()) if len(s) else np.nan)
    return f(up), f(dn), float((d.y > 0).mean()), len(d)


def main():
    spx = daily("^GSPC")
    spx = spx[(spx["Open"] > 0) & (spx["Close"] > 0)]
    prev_close = spx["Close"].shift(1)
    gap = spx["Open"] / prev_close - 1
    intr = spx["Close"] / spx["Open"] - 1
    sig = {"الفجوة (افتتاح اليوم / إغلاق أمس)": gap}
    for name, sym in (("نيكاي (تغير اليوم)", "^N225"), ("هانغ سنغ (تغير اليوم)", "^HSI")):
        try:
            d = daily(sym)
            ch = (d["Close"] / d["Close"].shift(1) - 1).reindex(spx.index)
            sig[name] = ch
        except Exception as e:
            print("تعذر", sym, e)
    try:
        es, dax = hourly("ES=F"), hourly("^GDAXI")
        days = list(spx.index)
        es_s, dax_s = {}, {}
        for i in range(1, len(days)):
            d, p = days[i], days[i - 1]
            if not es.empty:
                a, b = at(es, p, 15), at(es, d, 8)
                if not (np.isnan(a) or np.isnan(b)):
                    es_s[d] = b / a - 1
            if not dax.empty:
                last = dax[(dax.index.date == p.date())]
                b = at(dax, d, 8)
                if len(last) and not np.isnan(b):
                    dax_s[d] = b / float(last.iloc[-1]) - 1
        if es_s:
            sig["ES (16:00 أمس → 9:00)"] = pd.Series(es_s)
        if dax_s:
            sig["DAX (إغلاقه أمس → 9:00 نيويورك)"] = pd.Series(dax_s)
    except Exception as e:
        print("تعذر بيانات الساعة:", e)

    L = ["# دراسة ما قبل الافتتاح", "",
         f"- بيانات SPX اليومية: {spx.index[0].date()} إلى {spx.index[-1].date()} ({len(spx)} يوماً)",
         f"- متوسط |حركة الافتتاح→الإغلاق| = **{intr.abs().mean() * 100:.2f}%**، "
         f"ونسبة الأيام الصاعدة (افتتاح→إغلاق) = **{(intr > 0).mean() * 100:.1f}%**",
         f"- متوسط |الفجوة| = **{gap.abs().mean() * 100:.2f}%**", "",
         "## 1) هل الإشارة تتنبأ بالفجوة نفسها؟ (ارتباط مع فجوة الافتتاح)", "",
         "| الإشارة | عدد الأيام | الارتباط | t |", "|---|---|---|---|"]
    for k, x in sig.items():
        if k.startswith("الفجوة"):
            continue
        n, r, t = corr_t(x, gap)
        L.append(f"| {k} | {n} | {r:+.2f} | {t:+.1f} |")
    L += ["", "## 2) هل تتنبأ بحركة الجلسة (الافتتاح → الإغلاق)؟ (هذا ما يهم عقود 0DTE)", "",
          "| الإشارة | عدد الأيام | الارتباط | t | ملاحظة |", "|---|---|---|---|---|"]
    for k, x in sig.items():
        n, r, t = corr_t(x, intr)
        note = "دلالة ضعيفة (|t|<2)" if not (abs(t) >= 2) else ("اتجاه يتبع الإشارة" if r > 0 else "عكس الإشارة (ارتداد)")
        L.append(f"| {k} | {n} | {r:+.2f} | {t:+.1f} | {note} |")
    L += ["", "## 3) احتمال أن يغلق السوق صاعداً بعد الافتتاح، بحسب الإشارة", "",
          "| الإشارة | العتبة | صعودية: أيام / نسبة صعود الجلسة / متوسط الحركة | هبوطية: أيام / نسبة صعود الجلسة / متوسط الحركة | الأساس |",
          "|---|---|---|---|---|"]
    for k, x in sig.items():
        for thr in (0.003, 0.006):
            up, dn, base, n = cond(x, intr, thr)
            fmt = lambda t: f"{t[0]} / {t[1] * 100:.0f}% / {t[2] * 100:+.2f}%" if t[0] else "لا أيام"
            L.append(f"| {k} | ±{thr * 100:.1f}% | {fmt(up)} | {fmt(dn)} | {base * 100:.0f}% |")
    L += ["", "## كيف نقرأ الجداول", "",
          "- الجدول 1: إن كان الارتباط عالياً فالإشارة تُفسّر الفجوة (وهي في الغالب كذلك لأن ES يلخص الليل).",
          "- الجدول 2 و3: هنا الاختبار الحقيقي. إن كانت نسبة صعود الجلسة بعد إشارة صعودية قريبة من الأساس، "
          "فمعلومة ما قبل الافتتاح لا تكفي لتحديد اتجاه الجلسة.",
          "- كلما قلّ عدد الأيام ارتفع الضجيج. نسبة 55% من 40 يوماً لا تعني شيئاً.",
          "- هذه ارتباطات تاريخية على بيانات مجانية، وليست اختباراً لاستراتيجية تداول."]
    open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
