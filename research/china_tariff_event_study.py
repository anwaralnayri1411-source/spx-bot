#!/usr/bin/env python3
"""دراسة حدث ضيقة: هل تتحرك NVDA وAAPL وMU مع قرارات الرسوم الأمريكية-الصينية؟ (قراءة فقط، بطلب المستخدم 25/10/2026؛ مسجّلة مسبقاً في CLAUDE.md قبل لمس أي عائد).

القائمة: أحداث من صفحة ويكيبيديا «China–United States trade war» (اطّلعت عليها 25/10/2026)، 2018–2019، بتواريخها المذكورة فيها. قاعدة الاختيار (حُدّدت قبل رؤية العوائد):
  تُدرج: إعلانات قوائم رسوم جديدة أو زيادات أو تهديدات رسمية أو ردود صينية (تصعيد، اتجاه −1)، وتأجيلات وهدنة وصفقة وتراجع (تهدئة، اتجاه +1).
  تُستبعد: رسوم عامة لا تخص الصين (22/1/2018 و1/3/2018)، «يفكر في» (5/4/2018)، أيام «دخلت حيز التنفيذ» (6/7 و23/8 و1/9) لأنها معلنة سابقاً، نشر السجل الفيدرالي لما أُعلن (9/5/2019)، تصريح فول الصويا (11/12/2018)، والمكرر في اليوم نفسه.
  حدث في عطلة الأسبوع يُنسب لأول يوم تداول بعده (يوم رد الفعل d). حدث يقع رد فعله في يوم تداول أو يوم بعد حدث سابق بالاتجاه نفسه يُدمج (يُهمل) لئلا تتداخل النوافذ.
المقياس: العائد المُزال للسهم على نافذة يومين [إغلاق ما قبل d، إغلاق d+1] = r − β·r_SPY، β من انحدار يومي في 2017 (قبل أول حدث). الإشارة = اتجاه الحدث × العائد المُزال (تصعيد → نتوقع هبوطاً للمعرّضين للصين).
**الفرضيات (ثلاث، لكل منها فاصل 95% بإعادة معاينة الأحداث):** متوسط الإشارة > 0 لكل من NVDA وAAPL وMU. فحص سلامة: إشارة SPY الخام (اتجاه × عائد SPY) > 0.
**قاعدة القرار:** نبني طبقة «صافي الأثر والإلغاء» فقط إن تحقق سهمان من ثلاثة عند 95% مع نجاح فحص السلامة؛ نعرض أيضاً الفاصل الأشد (Bonferroni لثلاث فرضيات: 98.3%).
تحفظات: ~25 حدثاً فقط؛ حساسية 2018–2019 قد لا تنطبق على اليوم؛ ساعة الإعلان مجهولة (نافذة يومين تغطي بعد الإغلاق).
تشغيل: python research/china_tariff_event_study.py   المخرج: research/china_tariff_event_study_results.md
"""
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "china_tariff_event_study_results.md")
TICKERS = ("NVDA", "AAPL", "MU")
EST_START, EST_END = "2017-01-03", "2017-12-29"
# (التاريخ، الاتجاه −1 تصعيد / +1 تهدئة، وصف قصير)
EVENTS = [
    ("2018-03-22", -1, "USTR: طلب تحقيق القسم 301 (50–60 مليار)"),
    ("2018-04-02", -1, "الصين: رسوم انتقامية على 128 منتجاً"),
    ("2018-04-03", -1, "USTR: قائمة أولية 1300+ سلعة"),
    ("2018-04-04", -1, "الصين: خطة رسوم 25% على 106 بنود"),
    ("2018-05-20", +1, "منوشين: «نضع الحرب التجارية على الانتظار»"),
    ("2018-05-29", -1, "البيت الأبيض: 25% على 50 مليار"),
    ("2018-06-15", -1, "رسوم 25% على 50 مليار + رد الصين"),
    ("2018-06-19", -1, "الصين: تهديد برسوم على 50 مليار"),
    ("2018-07-10", -1, "قائمة 200 مليار بـ10%"),
    ("2018-08-08", -1, "قائمة نهائية 16 مليار + رد الصين"),
    ("2018-09-17", -1, "10% على 200 مليار"),
    ("2018-09-18", -1, "الصين: 10% على 60 مليار"),
    ("2018-12-01", +1, "تأجيل الزيادات ونافذة تفاوض 90 يوماً"),
    ("2019-05-05", -1, "ترامب: رفع الـ200 مليار إلى 25%"),
    ("2019-06-01", -1, "الصين ترفع رسوم 60 مليار"),
    ("2019-06-29", +1, "ترامب وشي: هدنة"),
    ("2019-08-01", -1, "ترامب: 10% على 300 مليار"),
    ("2019-08-05", -1, "الصين: وقف شراء الزراعة الأمريكية"),
    ("2019-08-13", +1, "تأجيل رسوم 160 مليار إلى 15 ديسمبر"),
    ("2019-08-23", -1, "رسوم انتقامية صينية على 75 مليار + رفع أمريكي"),
    ("2019-09-11", +1, "تأجيل زيادة 1 أكتوبر"),
    ("2019-09-13", +1, "الصين تلغي رسوم الصويا"),
    ("2019-10-11", +1, "صفقة المرحلة الأولى (مبدئية)"),
    ("2019-12-13", +1, "اتفاق أولي: إلغاء رسوم 15 ديسمبر"),
]


# ============================ دوال نقية ============================
def reaction_day(date_str, index):
    """أول يوم تداول ≥ التاريخ في فهرس الأسعار، أو None."""
    d = pd.Timestamp(date_str)
    pos = index.searchsorted(d)
    return None if pos >= len(index) else index[pos]


def merge_events(events, index):
    """يرجع [(يوم رد الفعل، اتجاه، وصف)] بعد إهمال ما تتداخل نافذته (يوم تداول أو يوم بعد الحدث السابق بالاتجاه نفسه)."""
    out, last = [], {}
    for date_str, direction, desc in events:
        d = reaction_day(date_str, index)
        if d is None:
            continue
        pos = index.get_loc(d)
        if direction in last and pos - last[direction] <= 1:
            continue
        last[direction] = pos
        out.append((d, direction, desc))
    return out


def beta(stock, spy, start=EST_START, end=EST_END):
    """بيتا يومية من انحدار عوائد السهم على SPY في نافذة التقدير."""
    r = pd.DataFrame({"s": stock.pct_change(), "m": spy.pct_change()}).loc[start:end].dropna()
    if len(r) < 100:
        return None
    return float(np.cov(r["s"], r["m"])[0, 1] / np.var(r["m"], ddof=1))


def window_return(close, d, days=2):
    """عائد من إغلاق ما قبل d إلى إغلاق d+days−1."""
    i = close.index.get_loc(d)
    if i < 1 or i + days - 1 >= len(close):
        return None
    return float(close.iloc[i + days - 1] / close.iloc[i - 1] - 1)


def signed_ar(close, spy, events, b):
    """إشارة اتجاه × (r − β·r_SPY) لكل حدث. b=None ← عائد خام (لـSPY نفسه)."""
    vals = []
    for d, direction, _ in events:
        if d not in close.index or d not in spy.index:
            continue
        r, m = window_return(close, d), window_return(spy, d)
        if r is None or m is None:
            continue
        vals.append(direction * (r - (b * m if b is not None else 0.0)))
    return np.array(vals)


def boot_ci(v, level=0.95, n=5000, seed=41):
    v = np.asarray(v, dtype=float)
    if len(v) < 8:
        return None
    m = v[np.random.default_rng(seed).integers(0, len(v), (n, len(v)))].mean(1)
    a = (1 - level) / 2
    return float(np.percentile(m, 100 * a)), float(np.percentile(m, 100 * (1 - a)))


def judge(v):
    """('تحققت' | 'لم تتحقق' | 'عكسية' | 'لا حكم'، متوسط، فاصل 95%، فاصل 98.3%)"""
    v = np.asarray(v, dtype=float)
    ci, strict = boot_ci(v), boot_ci(v, 0.9833)
    if ci is None:
        return "لا حكم", float("nan"), None, None
    word = "تحققت" if ci[0] > 0 else "عكسية" if ci[1] < 0 else "لم تتحقق"
    return word, float(v.mean()), ci, strict


def fmt_ci(ci):
    return "-" if ci is None else f"{100 * ci[0]:+.2f}…{100 * ci[1]:+.2f}"


def report(rows, spy_row, n_events, meta):
    L = ["# أحداث الرسوم الأمريكية-الصينية 2018–2019 وحساسية NVDA وAAPL وMU (نتائج آلية)", "", f"- {meta}",
         f"- {n_events} حدثاً بعد الدمج. العائد المُزال على نافذة يومين؛ الإشارة = اتجاه الحدث (تصعيد −1 / تهدئة +1) × العائد المُزال؛ فاصل 95% بإعادة معاينة الأحداث، والأشد 98.3% (Bonferroni لثلاث فرضيات).",
         "- ⚠️ ~25 حدثاً فقط؛ حساسية 2018–2019 قد لا تنطبق على اليوم.", "",
         "| السهم | أحداث | متوسط الإشارة | فاصل 95% (%) | فاصل 98.3% (%) | الحكم |", "|---|---|---|---|---|---|"]
    for name, v in rows:
        w, m, ci, st = judge(v)
        L.append(f"| {name} | {len(v)} | {100 * m:+.2f}% | {fmt_ci(ci)} | {fmt_ci(st)} | {w} |")
    w, m, ci, st = judge(spy_row)
    L += ["", f"- فحص السلامة (SPY الخام): {len(spy_row)} حدثاً، متوسط {100 * m:+.2f}%، فاصل 95% {fmt_ci(ci)} → {w}"]
    ok = sum(1 for _, v in rows if judge(v)[0] == "تحققت")
    sane = judge(spy_row)[0] == "تحققت"
    L += ["", "⚖️ **" + (f"قاعدة القرار: تحقق {ok} من 3 وفحص السلامة {'نجح' if sane else 'لم ينجح'} → "
                         + ("نبني طبقة الصافي والإلغاء" if ok >= 2 and sane else "لا نبني طبقة الصافي والإلغاء على هذه الحساسية")) + "**"]
    return L


def run(prices):
    """prices: {رمز: Series إغلاق}. يرجع (صفوف الأسهم، صف SPY، عدد الأحداث)."""
    spy = prices["SPY"]
    ev = merge_events(EVENTS, spy.index)
    rows = []
    for t in TICKERS:
        b = beta(prices[t], spy)
        rows.append((t, signed_ar(prices[t], spy, ev, b) if b is not None else np.array([])))
    return rows, signed_ar(spy, spy, ev, None), len(ev)


def load_prices():
    import yfinance as yf
    px = yf.download(list(TICKERS) + ["SPY"], start="2016-12-01", end="2020-02-15", group_by="ticker", auto_adjust=True, progress=False)
    out = {}
    for t in list(TICKERS) + ["SPY"]:
        s = px[t]["Close"].dropna()
        s.index = pd.to_datetime(s.index).tz_localize(None)
        out[t] = s
    return out


def main(argv):
    try:
        prices = load_prices()
    except Exception as e:  # noqa: BLE001
        print("تعذر تحميل الأسعار:", type(e).__name__)
        return 1
    rows, spy_row, n = run(prices)
    meta = f"أسعار ياهو المعدلة من 2016-12 إلى 2020-02؛ بيتا من {EST_START} إلى {EST_END}"
    L = report(rows, spy_row, n, meta)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
