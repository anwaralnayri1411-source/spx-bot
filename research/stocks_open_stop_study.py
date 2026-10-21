#!/usr/bin/env python3
"""بوت الشركات: هل وقف −40% في أول دقائق الجلسة «ضجيج افتتاح»؟ (قراءة فقط؛ لا يغيّر البوت).

لكل توصية في stocks_track.csv (البوت يتابع كل عقد حتى بعد الوقف) نجد أول قراءة وصل فيها العقد −40% بعد مرور
MIN_AGE دقيقة من التوصية (كما يفعل البوت)، ونصنّفها: «افتتاح» إن كانت قبل OPEN_END في يوم غير يوم التوصية، وإلا «أثناء الجلسة».
ثم ماذا حدث بعد الوقف: هل عاد العقد لنقطة الدخول؟ هل بلغ +50% (هدف البوت)؟ وما قيمته في آخر قراءة؟
ونقارن بقاعدة بديلة: لا وقف قبل OPEN_END في أيام ما بعد التوصية (الوقف يُقيَّم من OPEN_END).
⚠️ القراءات كل ~30–60 دقيقة (جدولة GitHub)، وقيمة العقد من ياهو (قد تكون «آخر صفقة» قديمة عند الافتتاح).
تشغيل: python research/stocks_open_stop_study.py   ← النتائج في research/stocks_open_stop_results.md
"""
import csv
import os
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TRACK = os.path.join(ROOT, "stocks_track.csv")
REPORT = os.path.join(HERE, "stocks_open_stop_results.md")
SL, TP, MIN_AGE = 0.40, 0.50, 30           # stocks_bot.STOP_LOSS / TAKE_PROFIT / MIN_AGE_MIN
OPEN_END = (10, 15)


def load():
    paths = defaultdict(list)
    for r in csv.DictReader(open(TRACK, encoding="utf-8")):
        try:
            paths[r["id"]].append((datetime.fromisoformat(r["time"]).replace(tzinfo=None), float(r["pnl"]), r))
        except Exception:
            continue
    return paths


def entry_time(pid, pts):
    # المعرف لا يحوي وقت التوصية؛ أول قراءة تقريب جيد (البوت يبدأ التتبع من التوصية)
    return pts[0][0]


def first_stop(pts, t0, skip_open):
    for t, p, _ in pts:
        if (t - t0).total_seconds() / 60 < MIN_AGE:
            continue
        if skip_open and t.date() != t0.date() and (t.hour, t.minute) < OPEN_END:
            continue
        if p <= -SL:
            return t, p
    return None


def study():
    rows = []
    for pid, pts in load().items():
        pts.sort(key=lambda x: x[0])
        t0 = entry_time(pid, pts)
        st = first_stop(pts, t0, False)
        if not st:
            continue
        ts, ps = st
        after = [p for t, p, _ in pts if t > ts]
        kind = "افتتاح" if ts.date() != t0.date() and (ts.hour, ts.minute) < OPEN_END else "أثناء الجلسة"
        alt = first_stop(pts, t0, True)
        # القاعدة البديلة: تتبّع حتى أول هدف أو وقف بعد OPEN_END
        alt_res = None
        for t, p, _ in pts:
            if (t - t0).total_seconds() / 60 < MIN_AGE:
                continue
            if p >= TP:
                alt_res = TP
                break
            if alt and t >= alt[0]:
                alt_res = alt[1]
                break
        if alt_res is None:
            alt_res = pts[-1][1]
        rows.append({"id": pid, "entry_t": t0, "stop_t": ts, "stop_pnl": ps, "kind": kind,
                     "back_even": any(p >= 0 for p in after), "back_tp": any(p >= TP for p in after),
                     "max_after": max(after) if after else ps, "last": pts[-1][1], "alt": alt_res,
                     "stock_move": pts[[t for t, _, _ in pts].index(ts)][2].get("stock_move", "")})
    return rows


def report(rows):
    L = ["# بوت الشركات: وقف الافتتاح — نتائج", "",
         f"المصدر: stocks_track.csv. «افتتاح» = أول وصول −40% قبل {OPEN_END[0]}:{OPEN_END[1]:02d} في يوم بعد يوم التوصية.",
         "القاعدة البديلة = لا يُقيَّم الوقف قبل ذلك الوقت في الأيام التالية (الهدف +50% يبقى). ⚠️ قراءات متباعدة وأسعار ياهو.", "",
         "| النوع | وقف | عاد للدخول | بلغ +50% بعده | قيمته عند الوقف (متوسط) | آخر قراءة (متوسط) | القاعدة البديلة (متوسط) |",
         "|---|---|---|---|---|---|---|"]
    for k in ("افتتاح", "أثناء الجلسة"):
        x = [r for r in rows if r["kind"] == k]
        if not x:
            continue
        m = lambda key: sum(r[key] for r in x) / len(x) * 100
        L.append(f"| {k} | {len(x)} | {sum(r['back_even'] for r in x)} ({sum(r['back_even'] for r in x) / len(x) * 100:.0f}%) | "
                 f"{sum(r['back_tp'] for r in x)} ({sum(r['back_tp'] for r in x) / len(x) * 100:.0f}%) | {m('stop_pnl'):+.0f}% | "
                 f"{m('last'):+.0f}% | {m('alt'):+.0f}% |")
    L += ["", "## كل حالات «افتتاح»", "", "| العقد | التوصية | الوقف | العقد عند الوقف | حركة السهم | أعلى بعده | البديلة |", "|---|---|---|---|---|---|---|"]
    for r in sorted((r for r in rows if r["kind"] == "افتتاح"), key=lambda r: r["stop_t"]):
        sm = r["stock_move"]
        sm = f"{float(sm) * 100:+.2f}%" if sm not in ("", None) else "—"
        L.append(f"| {r['id']} | {r['entry_t']:%m-%d %H:%M} | {r['stop_t']:%m-%d %H:%M} | {r['stop_pnl'] * 100:+.0f}% | {sm} | "
                 f"{r['max_after'] * 100:+.0f}% | {r['alt'] * 100:+.0f}% |")
    L += ["", "حركة السهم = منذ التوصية باتجاه العقد كما سجّلها البوت (سالب = ضدك).",
          "الحكم: إن كانت وقوف الافتتاح تعود كثيراً والقاعدة البديلة أفضل بوضوح على عينة كافية → نعرض تأجيل تقييم الوقف على المستخدم."]
    open(REPORT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    report(study())
