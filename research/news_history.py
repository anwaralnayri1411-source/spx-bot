#!/usr/bin/env python3
"""اختبار تاريخي يومي لذاكرة الأخبار (يدوي؛ دراسة فقط، لا يلمس البوتات). بطلب المستخدم 22/10.

1) خريطة ترامب/السياسة: عناوين Google News الأسبوعية (after:/before:) لاستعلامات POLICY_Q منذ تنصيب 20/1/2025،
   يصنّفها Claude (news_memory.classify). «يوم حدث» لموضوع = يوم فيه قرار/تصريح جديد من ترامب أو إدارته بأهمية متوسطة+،
   ولا يتبع يوم حدث آخر للموضوع نفسه مباشرة. الحركة = عائد اليوم فوق السوق (r − بيتا×SPY، يومي) لكل شركة وصندوق قطاع.
   الوهمي: 2000 سحب عشوائي لنفس عدد الأيام من أيام بلا حدث لهذا الموضوع ← p لمتوسط الحركة.
2) سوابق الشركات: عناوين «<الشركة> stock» آخر COMPANY_DAYS يوماً، نفس التصنيف، والحركة اليومية فوق السوق؛
   ثم اختبار news_memory.precedent_test (اتجاه السوابق مقابل نوع آخر عشوائي وتوقع Claude).
⚠️ تاريخ Google News القديم يومي فقط (بلا ساعة)، فقرار بعد الإغلاق قد يُنسب ليوم ردة الفعل أو قبله: نقيس يوم النشر
ونعرض أيضاً يومين (أمس + اليوم). عينات صغيرة واختبارات كثيرة: نعرض كم نتيجة p<0.05 متوقعة بالصدفة.
التصنيفات تُحفظ في research/news_history_heads.csv فلا يُعاد تصنيف عنوان (يوفر كلفة Claude).
تشغيل: python research/news_history.py [--policy] [--company]  ← research/news_history_results.md
"""
import csv
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

import numpy as np
import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import news_memory as nm   # noqa: E402

HEADS = os.path.join(HERE, "news_history_heads.csv")
EVENTS_OUT = os.path.join(HERE, "news_history_policy_days.csv")
MD = os.path.join(HERE, "news_history_results.md")
START = date(2025, 1, 20)
COMPANY_DAYS = 180
PER_DAY = 3            # أقصى عناوين لكل استعلام في اليوم (يقلل كلفة Claude)
DRAWS = 2000
ACTION = re.compile(r"\b(says|said|announc|sign|order|impos|threat|plan|paus|delay|rais|cut|ban|slap|hike|exempt|"
                    r"approv|block|deal|agree|halt|fire|nominat|launch|beats?|miss|guidance|upgrad|downgrad|sue|probe)",
                    re.I)
HCOLS = ["date", "ticker", "query", "title", "source", "key", "event", "theme", "new", "importance", "expect", "affected"]


def fetch(query, a, b):
    try:
        r = requests.get("https://news.google.com/rss/search",
                         params={"q": f"{query} after:{a} before:{b}", "hl": "en-US", "gl": "US", "ceid": "US:en"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        root = ET.fromstring(r.content)
    except Exception as e:
        print("أخبار:", e)
        return []
    out = []
    for it in root.iter("item"):
        try:
            d = parsedate_to_datetime(it.findtext("pubDate")).date()
        except Exception:
            continue
        out.append((d, (it.findtext("title") or "").strip(), (it.findtext("source") or "").strip()))
    return out


def gather(queries, start, end):
    """queries: [(ticker, query)] ← عناوين جديدة غير مصنفة بعد، بحد PER_DAY لكل استعلام ويوم (الأفضلية لأفعال الحدث)."""
    known = set()
    if os.path.exists(HEADS):
        known = {r["key"] for r in csv.DictReader(open(HEADS, encoding="utf-8"))}
    import stocks_watcher as sw
    picked = []
    for tk, q in queries:
        a = start
        while a < end:
            b = min(a + timedelta(days=7), end)
            by_day = {}
            for d, title, src in fetch(q, a, b):
                k = sw.news_key(title)
                if k and k not in known and a <= d < b:
                    by_day.setdefault(d, []).append((not ACTION.search(title), d, title, src, k))
            for d, xs in by_day.items():
                for _, d_, title, src, k in sorted(xs)[:PER_DAY]:
                    known.add(k)
                    picked.append({"date": d_.isoformat(), "ticker": tk, "query": q, "title": title, "source": src, "key": k})
            a = b
            time.sleep(0.5)
        print(f"{q}: {len(picked)} حتى الآن")
    return picked


def classify_and_save(picked):
    for s in range(0, len(picked), 400):
        chunk = picked[s:s + 400]
        cls = nm.classify([(r["ticker"], r["title"]) for r in chunk])
        rows = []
        for r, c in zip(chunk, cls):
            if c:
                rows.append({**r, "event": c["event"], "theme": c["theme"], "new": int(c["new"]),
                             "importance": c["importance"], "expect": c["expect"], "affected": " ".join(c["affected"])})
        nm.append(HEADS, HCOLS, rows)
        print(f"صُنّف {s + len(chunk)}/{len(picked)}؛ Claude حتى الآن: {nm.USAGE['in']} دخل، {nm.USAGE['out']} خرج")


def daily(start):
    import yfinance as yf
    syms = sorted(nm.SECTOR) + sorted(set(nm.SECTOR.values())) + ["SPY"]
    c = yf.download(syms, start=(start - timedelta(days=200)).isoformat(), interval="1d", auto_adjust=True,
                    progress=False, threads=True)["Close"]
    r = c.pct_change()
    ar = pd.DataFrame(index=r.index)
    for s in syms:
        if s == "SPY":
            continue
        m = r[s].notna() & r["SPY"].notna()
        b = np.polyfit(r["SPY"][m], r[s][m], 1)[0]
        ar[s] = (r[s] - b * r["SPY"]) * 100
    return ar[ar.index.date >= start]


def trade_day(idx, d):
    """أول يوم تداول ≥ d."""
    i = idx.searchsorted(pd.Timestamp(d))
    return idx[i] if i < len(idx) else None


def policy_map(h, ar):
    p = h[(h["ticker"] == "MARKET") & (h["theme"] != "none") & (h["new"] == 1) & h["importance"].isin(["medium", "high"])]
    idx = ar.index
    rng = np.random.default_rng(7)
    days_rows, res = [], []
    for th, g in p.groupby("theme"):
        ds = sorted({trade_day(idx, d) for d in pd.to_datetime(g["date"]).dt.date} - {None})
        ev, last = [], None
        for d in ds:                            # يوم حدث لا يتبع يوم حدث سابق مباشرة (نفس الموضوع)
            if last is None or idx.get_loc(d) - idx.get_loc(last) > 1:
                ev.append(d)
            last = d
        for d in ev:
            days_rows.append({"theme": th, "day": d.date().isoformat(),
                              "titles": " | ".join(g[pd.to_datetime(g["date"]).dt.date.map(lambda x: trade_day(idx, x)) == d]["title"].head(3))})
        if len(ev) < 4:
            continue
        pos = [idx.get_loc(d) for d in ev]
        two = ar.rolling(2).sum()
        quiet = np.setdiff1d(np.arange(1, len(idx)), sorted(set(pos) | {q + 1 for q in pos}))
        for s in ar.columns:
            x = ar[s].iloc[pos].dropna()
            x2 = two[s].iloc[pos].dropna()
            pool = ar[s].iloc[quiet].dropna().values
            if len(x) < 4 or len(pool) < 50:
                continue
            null = rng.choice(pool, size=(DRAWS, len(x))).mean(axis=1)
            pv = (np.sum(np.abs(null) >= abs(x.mean())) + 1) / (DRAWS + 1)
            res.append({"theme": th, "sym": s, "n": len(x), "mean": x.mean(), "up": (x > 0).mean(), "mean2": x2.mean(), "p": pv})
    pd.DataFrame(days_rows).to_csv(EVENTS_OUT, index=False)
    return pd.DataFrame(res)


def company_test(h, ar):
    c = h[(h["ticker"] != "MARKET")].copy()
    c["day"] = pd.to_datetime(c["date"]).dt.date.map(lambda d: trade_day(ar.index, d))
    c = c.dropna(subset=["day"])
    c["total"] = [ar.at[d, tk] if tk in ar.columns else np.nan for d, tk in zip(c["day"], c["ticker"])]
    c["t0"] = c["day"]
    c["day"] = c["day"].map(lambda x: x.date())
    c["src_ticker"] = c["ticker"]
    cl = nm.clusters(c)
    return cl, nm.precedent_test(cl)


def main():
    a = set(sys.argv[1:]) or {"--policy", "--company"}
    end = date.today()
    if "--policy" in a:
        classify_and_save(gather([("MARKET", q) for q in nm.POLICY_Q.values()], START, end))
    if "--company" in a:
        classify_and_save(gather([(tk, f"{n} stock") for tk, n in nm.names().items()], end - timedelta(days=COMPANY_DAYS), end))
    h = pd.read_csv(HEADS)
    ar = daily(START)
    L = ["# اختبار تاريخي يومي: أخبار ترامب وسوابق الشركات", "",
         f"آخر تحديث: {datetime.now():%Y-%m-%d}. العناوين المصنفة: {len(h)}. الحركة = عائد اليوم فوق السوق (بيتا يومية) بالنسبة المئوية. "
         "تاريخ Google News القديم يومي فقط، فالتوقيت تقريبي.", ""]
    pm = policy_map(h, ar)
    if not pm.empty:
        sig = pm[pm["p"] < 0.05].sort_values("p")
        L += ["## 1) قرارات وتصريحات ترامب: من يتحرك؟",
              f"اختبارات: {len(pm)}؛ p<0.05: **{len(sig)}** (المتوقع بالصدفة ~{0.05 * len(pm):.0f}).", "",
              "| الموضوع | الرمز | أيام | صعود | متوسط اليوم | متوسط يومين | p |", "|---|---|---|---|---|---|---|"]
        for _, r in sig.head(40).iterrows():
            L.append(f"| {r['theme']} | {r['sym']} | {r['n']} | {100 * r['up']:.0f}% | {r['mean']:+.2f}% | {r['mean2']:+.2f}% | {r['p']:.3f} |")
        L += ["", "عدد أيام الحدث لكل موضوع: " + ", ".join(f"{t} {int(n)}" for t, n in pm.groupby("theme")["n"].max().items()),
              f"قائمة الأيام وعناوينها: `research/{os.path.basename(EVENTS_OUT)}`", ""]
    cl, (hit, base, clx) = company_test(h, ar)
    if len(cl):
        L += ["## 2) سوابق الشركات (يومي)", f"أخبار مجمّعة (شركة + نوع + يوم، جديدة، أهمية متوسطة+): {len(cl)}", "",
              "| النوع | العدد | صعود | متوسط |", "|---|---|---|---|"]
        for e, g in cl.groupby("event"):
            t = g["total"].dropna()
            L.append(f"| {nm.EVENT_AR.get(e, e)} | {len(t)} | {nm.pct(list(t > 0))} | {t.mean():+.2f}% |")
        L += ["", f"هل اتجاه السوابق (≥{nm.MIN_PRIOR}) يتنبأ باتجاه يوم الخبر؟",
              f"- السوابق: {nm.pct(hit)}", f"- نوع آخر عشوائي (وهمي): {nm.pct(base)}", f"- توقع Claude من العنوان: {nm.pct(clx)}"]
    L += ["", f"Claude في هذا التشغيل: {nm.USAGE['calls']} طلب، {nm.USAGE['in']} رمز دخل، {nm.USAGE['out']} رمز خرج."]
    open(MD, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
