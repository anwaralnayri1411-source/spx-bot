#!/usr/bin/env python3
"""دراسة الانجراف بعد الأرباح (PEAD) على شركات البوت (قراءة فقط، بطلب المستخدم 24/10/2026: «هل الخبر ينزل السهم أم لا؟»).

السؤال: بعد إعلان أرباح بمفاجأة واضحة، هل يستمر السهم في اتجاه المفاجأة خلال الأيام التالية (تأثير موثّق في الأدبيات يضعف مع الزمن)؟ إن كان فالخبر «يدفع السهم» وتتأخر تسعيرته.
البيانات (ياهو، مجانية): تواريخ الأرباح وEPS المقدّر والمعلن لشركات قائمة البوت (`get_earnings_dates`)، وأسعار يومية 12 سنة. يوم رد الفعل = يوم الإعلان إن كان قبل الافتتاح، وإلا اليوم التالي.
العائد = حركة السهم − حركة SPY في النافذة نفسها. الانجراف = من إغلاق يوم رد الفعل إلى إغلاق بعد 5 أيام تداول (دخول ممكن عند ذلك الإغلاق: رد الفعل معروف).
**الفرضية المسجّلة مسبقاً (24/10/2026، قبل رؤية النتائج):** إشارة المفاجأة (|المفاجأة| ≥ 2%) × الانجراف الـ5 أيام لها متوسط > 0 بفاصل 95% يستثني الصفر (إعادة معاينة العناقيد = ربع السنة، لأن الأرباح تتجمع في مواسم)، بعد 200 حدث على الأقل.
ثانوي (استكشافي): إشارة رد الفعل في يوم الإعلان × الانجراف؛ ورد الفعل مقابل إشارة المفاجأة (سلامة: يجب أن تتفقا غالباً).
تحفظات: أوقات الإعلان من ياهو تقريبية (نستبعد ما بلا وقت)، وقائمة الشركات مختارة اليوم (انحياز بقاء)، وزمن العقد يخسر الانجراف إن كان صغيراً.
تشغيل: python research/pead_study.py   المخرج: research/pead_study_results.md
"""
import os
import sys
import time
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUT = os.path.join(HERE, "pead_study_results.md")
MIN_SURPRISE = 2.0
DRIFT_DAYS = 5
MIN_EVENTS = 200


# ============================ دوال نقية ============================
def reaction_day(ts, trading_days):
    """يوم رد الفعل لإعلان في الوقت ts (بتوقيت نيويورك): قبل الافتتاح = اليوم نفسه، بعد 16:00 = اليوم التالي، منتصف الليل بالضبط = مجهول (None)."""
    if ts.hour == 0 and ts.minute == 0:
        return None
    day = pd.Timestamp(ts.date())
    if (ts.hour, ts.minute) >= (16, 0):
        nxt = trading_days[trading_days > day]
        return nxt[0] if len(nxt) else None
    cand = trading_days[trading_days >= day]
    return cand[0] if len(cand) and cand[0] == day else None


def build_events(ticker, earn, close, spy, drift_days=DRIFT_DAYS):
    """earn: DataFrame فهرسه وقت الإعلان وأعمدته Surprise(%) وReported EPS. close/spy: Series يومية بفهرس تاريخ. يرجع قائمة dict."""
    out = []
    td = close.index
    for ts, r in earn.iterrows():
        sur = r.get("Surprise(%)")
        if pd.isna(sur) or pd.isna(r.get("Reported EPS")) or abs(float(sur)) < MIN_SURPRISE:
            continue
        rd = reaction_day(ts.tz_convert("America/New_York") if ts.tzinfo else ts, td)
        if rd is None or rd not in td:
            continue
        i = td.get_loc(rd)
        if i < 1 or i + drift_days >= len(td):
            continue
        s0, s1, s2 = spy.reindex(td).iloc[[i - 1, i, i + drift_days]]
        if not np.isfinite([s0, s1, s2]).all():
            continue
        c0, c1, c2 = close.iloc[[i - 1, i, i + drift_days]]
        out.append({"ticker": ticker, "date": rd, "surprise": float(sur), "ar_r": float(c1 / c0 - 1 - (s1 / s0 - 1)),
                    "ar_d": float(c2 / c1 - 1 - (s2 / s1 - 1)), "q": f"{rd.year}Q{(rd.month - 1) // 3 + 1}"})
    return out


def cluster_ci(v, clusters, n=3000, seed=13):
    """فاصل 95% لمتوسط v بإعادة معاينة العناقيد (مجموع/عدد لكل عنقود). None إن قلّت العناقيد عن 8."""
    df = pd.DataFrame({"v": np.asarray(v, dtype=float), "c": np.asarray(clusters)}).groupby("c")["v"].agg(["sum", "count"])
    if len(df) < 8:
        return None
    S, N = df["sum"].to_numpy(), df["count"].to_numpy()
    idx = np.random.default_rng(seed).integers(0, len(S), (n, len(S)))
    m = S[idx].sum(1) / N[idx].sum(1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def stat(sign, ar, clusters):
    v = np.sign(sign) * np.asarray(ar, dtype=float)
    m = np.isfinite(v) & (np.sign(sign) != 0)
    v, cl = v[m], np.asarray(clusters)[m]
    if len(v) == 0:
        return None
    return {"n": len(v), "hit": float((v > 0).mean()), "mean": float(v.mean()), "ci": cluster_ci(v, cl)}


def fmt(s):
    if s is None:
        return "-"
    ci = "-" if s["ci"] is None else f"{100 * s['ci'][0]:+.2f}…{100 * s['ci'][1]:+.2f}"
    return f"{s['n']} | {100 * s['hit']:.0f}% | {100 * s['mean']:+.3f}% | {ci}"


def verdict(E):
    s = stat(E["surprise"], E["ar_d"], E["q"])
    if s is None or s["n"] < MIN_EVENTS:
        return f"لا حكم بعد ({0 if s is None else s['n']} من {MIN_EVENTS} حدثاً)"
    if s["ci"] is None:
        return "لا حكم بعد (عناقيد قليلة)"
    if s["ci"][0] > 0:
        return f"تحققت الفرضية: الانجراف يتبع المفاجأة بمتوسط {100 * s['mean']:+.2f}% (فاصل {100 * s['ci'][0]:+.2f}…{100 * s['ci'][1]:+.2f})"
    if s["ci"][1] < 0:
        return "عكسية: ينعكس السهم بعد المفاجأة"
    return f"لم تتحقق: متوسط {100 * s['mean']:+.2f}% وفاصله {100 * s['ci'][0]:+.2f}…{100 * s['ci'][1]:+.2f} يشمل الصفر"


def report(E, meta):
    L = ["# الانجراف بعد الأرباح على شركات البوت (نتائج آلية)", "", f"- {meta}",
         "- قراءة فقط ولا تغيّر أي توصية. العائد مطروحاً منه SPY. الانجراف من إغلاق يوم رد الفعل إلى إغلاق بعد 5 أيام تداول. فاصل 95% بإعادة معاينة أرباع السنة.",
         "- **الفرضية المسجّلة مسبقاً:** إشارة المفاجأة (|المفاجأة| ≥ 2%) × الانجراف > 0 بفاصل يستثني الصفر بعد 200 حدث على الأقل.", ""]
    if len(E) == 0:
        return L + ["لا أحداث."]
    L += ["| الإشارة × العائد | أحداث | استمرار | متوسط | فاصل 95% (%) |", "|---|---|---|---|---|",
          f"| **المفاجأة × الانجراف (الأساسي)** | {fmt(stat(E['surprise'], E['ar_d'], E['q']))} |",
          f"| رد الفعل في يوم الإعلان × الانجراف (استكشافي) | {fmt(stat(E['ar_r'], E['ar_d'], E['q']))} |",
          f"| المفاجأة × رد الفعل (سلامة: يجب أن تتفقا) | {fmt(stat(E['surprise'], E['ar_r'], E['q']))} |", ""]
    big = E[E["surprise"].abs() >= 10]
    if len(big) >= 30:
        L.append(f"| مفاجآت كبيرة (≥10%) × الانجراف | {fmt(stat(big['surprise'], big['ar_d'], big['q']))} |")
        L.append("")
    half = E["date"].median()
    for name, part in (("النصف الأول", E[E["date"] <= half]), ("النصف الثاني", E[E["date"] > half])):
        L.append(f"- {name} ({len(part)} حدثاً): المفاجأة × الانجراف {fmt(stat(part['surprise'], part['ar_d'], part['q']))}")
    L += ["", "⚖️ **الحكم على الفرضية:** " + verdict(E)]
    return L


# ============================ الشبكة والتشغيل ============================
def load(tickers):
    import yfinance as yf
    px = yf.download(list(tickers) + ["SPY"], period="12y", interval="1d", group_by="ticker", auto_adjust=True, progress=False, threads=True)
    close = {t: px[t]["Close"].dropna() for t in list(tickers) + ["SPY"] if t in px.columns.get_level_values(0)}
    earn = {}
    for t in tickers:
        for attempt in (1, 2):
            try:
                earn[t] = yf.Ticker(t).get_earnings_dates(limit=60)
                break
            except Exception:  # noqa: BLE001
                time.sleep(2)
    return close, earn


def main(argv):
    import stocks_bot as sb
    tickers = [t for t in sb.WATCHLIST]
    close, earn = load(tickers)
    spy = close["SPY"]
    spy.index = pd.to_datetime(spy.index).tz_localize(None)
    rows = []
    for t in tickers:
        if t not in close or t not in earn or earn[t] is None:
            continue
        c = close[t].copy()
        c.index = pd.to_datetime(c.index).tz_localize(None)
        rows += build_events(t, earn[t], c, spy)
    E = pd.DataFrame(rows)
    meta = f"{len(E)} حدثاً في {E['ticker'].nunique() if len(E) else 0} شركة" + (f"، من {E['date'].min():%Y-%m-%d} إلى {E['date'].max():%Y-%m-%d}" if len(E) else "")
    L = report(E, meta)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
