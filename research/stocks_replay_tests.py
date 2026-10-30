#!/usr/bin/env python3
"""H-STKREPLAY: إعادة الاستراتيجيات التي اختُبرت على SPY وحده، على شركات قائمة البوت مجمّعة (اختبار فقط). بطلب المستخدم 30/10/2026:
«لا نضع SPY للاختبار فقط، هذا ممكن يُفشل لنا خططاً واستراتيجيات».

الاختبارات نفسها حرفياً بدوالها ومعاييرها المسجّلة، وتغيير واحد: البيانات = شموع 5د لكل شركة من قائمة البوت (bars_5m)، والنتائج مجمّعة عبر الشركات:
  H-EDGE7H  الإشارات القوية (edge7_history_test.run/report)
  H-DAYTYPE نوع اليوم ومعه MACD (daytype_test.day_rows/analyze/report)
  H-DISSENTH المخالف للأغلبية (dissent_test.slot_rows/classify/report)
  H-POC     نقطة التحكم: المغناطيس وقاعدة 80% والارتداد (poc_test.analyze_symbol/section)
  H-VPAV    بقية Volume Profile وAnchored VWAP (vp_avwap_test.analyze/evaluate/report)
الحكم بمعايير كل اختبار المسجّلة كما هي، على التجميع (الأيام نفسها مشتركة بين الشركات فالمعاينة بالأيام تأخذ الترابط في الحسبان).
تحفظ: خمسة اختبارات × شركات = مقارنات كثيرة؛ النجاح المعتبر = ما يجتاز معاييره الأصلية (التي فيها تصحيح BH حيث وُجد).
تشغيل (الخادم): SPXBOT_DATA=/opt/spxbot/data python research/stocks_replay_tests.py [--only edge7,daytype,dissent,poc,vpav]
المخرج: research/stocks_replay_tests_results.md
"""
import os
import sys
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
OUT = os.path.join(HERE, "stocks_replay_tests_results.md")


def symbols():
    import stocks_bot as sb
    return [s for s in sb.WATCHLIST if os.path.exists(os.path.join(DATA, "bars_5m", f"{s}.csv.gz"))]


def path(sym):
    return os.path.join(DATA, "bars_5m", f"{sym}.csv.gz")


def run_edge7(syms):
    import daytype_test as dt
    import edge7_history_test as eh
    ev = []
    for s in syms:
        try:
            ev += eh.run(dt.load(path(s)))
        except Exception as e:  # noqa: BLE001
            print("edge7 تخطي", s, type(e).__name__)
    ev.sort(key=lambda e: (e["day"], e["t"]))
    return ["## H-EDGE7H على الشركات", ""] + eh.report(ev, f"{len(syms)} شركة، {len(ev)} إشارة (فرق ≥ 3)")[2:]


def run_daytype(syms):
    import daytype_test as dt
    parts = []
    for s in syms:
        try:
            x = dt.day_rows(dt.load(path(s)))
            x.index = pd.MultiIndex.from_arrays([[s] * len(x), x.index])
            parts.append(x.droplevel(0))
        except Exception as e:  # noqa: BLE001
            print("daytype تخطي", s, type(e).__name__)
    df = pd.concat(parts).sort_index()
    return ["## H-DAYTYPE على الشركات", ""] + dt.report(dt.analyze(df))[2:]


def run_dissent(syms):
    import daytype_test as dt
    import dissent_test as ds
    rows = []
    for s in syms:
        try:
            rows += ds.classify(ds.slot_rows(dt.load(path(s))))
        except Exception as e:  # noqa: BLE001
            print("dissent تخطي", s, type(e).__name__)
    days = sorted({r["day"] for r in rows})
    return ["## H-DISSENTH على الشركات", ""] + ds.report(rows, f"{len(syms)} شركة، {len(days)} يوماً")[2:]


def run_poc(syms):
    import poc_test as pt
    from tournament import read_bars
    parts = []
    for s in syms:
        try:
            x = pt.analyze_symbol(read_bars(path(s)))
            x["sym"] = s
            parts.append(x)
        except Exception as e:  # noqa: BLE001
            print("poc تخطي", s, type(e).__name__)
    df = pd.concat(parts)
    rows, verdicts, mid = pt.section(df, "الشركات", True)
    return ["## H-POC على الشركات", "", f"- الاكتشاف قبل {mid}.", "", "| الرمز | السؤال | الجزء | العينة | النتيجة | المتوسط وفاصل 95% |", "|---|---|---|---|---|---|"] + rows + [""] + verdicts


def run_vpav(syms):
    import vp_avwap_test as vt
    from tournament import read_bars
    parts = []
    for s in syms:
        try:
            x = vt.analyze(read_bars(path(s)))
            x["sym"] = s
            parts.append(x)
        except Exception as e:  # noqa: BLE001
            print("vpav تخطي", s, type(e).__name__)
    res = vt.evaluate(pd.concat(parts))
    L = vt.report({"SPY": res}, "الشركات مجمّعة (العمود «الرمز» = SPY اسم داخلي للتجميع)")
    return ["## H-VPAV على الشركات", ""] + [x.replace("| SPY |", "| الشركات |").replace("الحكم (SPY)", "الحكم (الشركات)") for x in L[2:]]


TESTS = {"edge7": run_edge7, "daytype": run_daytype, "dissent": run_dissent, "poc": run_poc, "vpav": run_vpav}


def main(argv):
    only = argv[argv.index("--only") + 1].split(",") if "--only" in argv else list(TESTS)
    syms = symbols()
    L = ["# H-STKREPLAY: اختبارات SPY السابقة على شركات قائمة البوت (اختبار، Alpaca 5د منذ 2019)", "",
         f"- {len(syms)} شركة: {'، '.join(syms)}. الدوال والمعايير نفسها المسجّلة لكل اختبار؛ النتائج مجمّعة عبر الشركات.", ""]
    for k in only:
        try:
            part = TESTS[k](syms)
        except Exception as e:  # noqa: BLE001
            part = [f"## {k}: فشل ({type(e).__name__}: {e})"]
        L += part + [""]
        with open(OUT, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
        print("انتهى", k)
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
