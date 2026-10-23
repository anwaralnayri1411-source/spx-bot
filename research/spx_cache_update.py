#!/usr/bin/env python3
"""يلحق شموع SPX 5د اليومية بمخبأ `research/spx_5m_cache.csv` حتى لا تضيع أيام بعد نافذة ياهو (آخر 60 يوماً فقط).

لماذا: المخبأ توقف عند 12/10/2026 ولا أحد يحدّثه، والدراسات (real_price_check، tail_log، backtest_spx_v2...) تجمع المخبأ مع ياهو؛
فبعد ~20/12 ستختفي أيام 13–23 أكتوبر. هذه المهمة تسدّ الفجوة وتبقيه محدّثاً.
قواعد الأمان (الملف مشترك مع دراسات أخرى فلا نغيّر ما فيه):
  - الصفوف القديمة تبقى كما هي دائماً (نضيف شموعاً ناقصة فقط؛ عند التعارض يفوز القديم) فلا يتغير أي اختبار تاريخي سابق.
  - لا ننقص عدد الصفوف أبداً، ولا نكتب إن لم يضف شيء، ونرفض أي بيانات فيها أخطاء (NaN، أعلى < أدنى، سعر غير منطقي).
  - شموع اليوم الحالي لا تُلحق قبل 16:10 نيويورك (تجنباً لشمعة جارية ناقصة).
  - التنسيق نفسه: time,Open,High,Low,Close بتوقيت نيويورك، من 9:30 إلى 15:55.
المخرج: research/spx_5m_cache.csv (يُحفظ بمهمة `spx_cache` على الخادم). تشغيل: python research/spx_cache_update.py [--period 30d] [--dry-run]
"""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "spx_5m_cache.csv")
NY = ZoneInfo("America/New_York")
COLS = ["Open", "High", "Low", "Close"]
OPEN_M, CLOSE_M = 9 * 60 + 30, 16 * 60          # [09:30، 16:00)
SPX_MIN, SPX_MAX = 1000.0, 50000.0


# ============================ دوال نقية (قابلة للاختبار) ============================
def read_cache(path=CACHE):
    if not os.path.exists(path):
        return pd.DataFrame(columns=COLS)
    d = pd.read_csv(path, index_col=0)
    d.index = pd.to_datetime(d.index, utc=True).tz_convert(NY)
    return d[COLS]


def normalize(df, now=None):
    """شموع ياهو ← فهرس نيويورك، الأعمدة الأربعة، حصة التداول العادية، بلا اليوم الحالي قبل 16:10."""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=COLS)
    d = df.copy()
    d.index = d.index.tz_convert(NY) if d.index.tz is not None else d.index.tz_localize("UTC").tz_convert(NY)
    d = d[COLS].dropna()
    m = d.index.hour * 60 + d.index.minute
    d = d[(m >= OPEN_M) & (m < CLOSE_M)]
    now = now or datetime.now(NY)
    if (now.hour, now.minute) < (16, 10):
        d = d[d.index.date != now.date()]
    return d


def problems(d):
    """أسباب رفض البيانات الجديدة (قائمة فارغة = سليمة)."""
    out = []
    if len(d) == 0:
        return out
    if d[COLS].isna().any().any():
        out.append("قيم ناقصة")
    if (d["High"] < d["Low"]).any():
        out.append("أعلى < أدنى")
    if ((d[COLS] < SPX_MIN) | (d[COLS] > SPX_MAX)).any().any():
        out.append("سعر خارج النطاق المنطقي")
    if (d[["Open", "Close"]].max(axis=1) > d["High"] + 1e-6).any() or (d[["Open", "Close"]].min(axis=1) < d["Low"] - 1e-6).any():
        out.append("افتتاح/إغلاق خارج المدى")
    return out


def merge(old, new):
    """يضيف شموع new الناقصة إلى old. القديم يفوز عند التعارض. يرجع (النتيجة، عدد الشموع المضافة)."""
    added = new[~new.index.isin(old.index)]
    if added.empty:
        return old, 0
    return pd.concat([old, added]).sort_index(), len(added)


def days_report(d, days):
    return ", ".join(f"{x.isoformat()}({int((d.index.date == x).sum())})" for x in days)


# ============================ التنفيذ ============================
def fetch(period):
    import yfinance as yf
    return yf.Ticker("^GSPC").history(period=period, interval="5m", auto_adjust=False)


def main(argv):
    period = argv[argv.index("--period") + 1] if "--period" in argv else "30d"
    dry = "--dry-run" in argv
    old = read_cache()
    try:
        new = normalize(fetch(period))
    except Exception as e:  # noqa: BLE001
        print("تعذر جلب SPX من ياهو:", type(e).__name__)
        return 0                                          # فشل مؤقت لا يكسر شيئاً؛ نعيد غداً
    bad = problems(new)
    if bad:
        print("رُفضت بيانات ياهو:", "، ".join(bad))
        return 1
    merged, added = merge(old, new)
    if len(merged) < len(old):
        print("رُفض: كان سينقص عدد الصفوف.")
        return 1
    if added == 0:
        print(f"المخبأ محدّث ({len(old)} شمعة حتى {old.index.max()}).")
        return 0
    new_days = sorted(set(merged.index.date) - set(old.index.date))
    short = [x for x in new_days if int((merged.index.date == x).sum()) < 70]
    print(f"أُضيفت {added} شمعة في {len(new_days)} يوم: {days_report(merged, new_days)}")
    if short:
        print("تنبيه: أيام ناقصة الشموع (إغلاق مبكر أو بيانات ناقصة):", days_report(merged, short))
    if dry:
        print("تجربة فقط: لم يُكتب شيء.")
        return 0
    merged.to_csv(CACHE, index_label="time")
    print(f"حُفظ المخبأ: {len(merged)} شمعة، {merged.index.min():%Y-%m-%d} → {merged.index.max():%Y-%m-%d}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
