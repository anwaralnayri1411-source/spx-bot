#!/usr/bin/env python3
"""ظل «مستويات كاماريلا» لـ SPX (تسجيل فقط؛ لا يرسل رسائل ولا يقرأ أو يغيّر أي ملف من ملفات البوت).

يطبّق قواعد `camarilla_study.py` (انعكاس عند H3/L3، اختراق H4/L4) على كل يوم جديد بعد START_DATE، بعد إغلاق السوق وتحديث
المخبأ `research/spx_5m_cache.csv` (مهمة spx_cache 22:20 UTC). يكتب صفاً لكل قاعدة ظهرت لها إشارة (مستويات الجلسة السابقة،
الدخول، الوقف، الهدف، R بعد الكلفة، المرجع) في `camarilla_shadow_log.csv`، ولا يكرر يوماً سُجّل. الأسعار على المؤشر (R)، لا عقود خيارات.
الحكم: `--report` بعد 100 صفقة لكل قاعدة، بفاصل 95% وفوق المرجع (H-CAMA)؛ قبلها «لا حكم». يتوقف عن التسجيل بعد END_DATE.
الملفات (جديدة): camarilla_shadow_log.csv، research/camarilla_shadow_results.md.   تشغيل: python research/camarilla_shadow.py [--report]
"""
import csv
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import camarilla_study as cs  # noqa: E402

LOG = os.path.join(ROOT, "camarilla_shadow_log.csv")
REPORT = os.path.join(HERE, "camarilla_shadow_results.md")
START_DATE = date(2026, 11, 2)     # الأمامي فقط: لا نخلط أيام الدراسة التاريخية
END_DATE = date(2026, 12, 18)      # ~7 أسابيع، ثم مراجعة مع المستخدم
COLS = ["date", "strat", "side", "time", "entry", "stop", "target", "r", "kind", "base_r",
        "PP", "H3", "H4", "H5", "L3", "L4", "L5", "bars"]


def read_log(path=LOG):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def new_rows(days, done, start=START_DATE, end=END_DATE):
    """صفوف السجل لأيام لم تُسجَّل بعد. done: مجموعة (تاريخ نصي، قاعدة). اليوم الناقص (أقل من 70 شمعة) يُؤجَّل."""
    rows = []
    for d in sorted(days):
        if d < start or d > end:
            continue
        if len(days[d]["c"]) < 70:
            continue
        lv = cs.prev_levels(days, d)
        if lv is None:
            continue
        for s in cs.STRATS:
            if (d.isoformat(), s) in done:
                continue
            t = cs.trade_for(days[d], lv, s)
            if not t:
                continue
            rows.append({"date": d.isoformat(), "strat": s, "side": "CALL" if t["side"] == 1 else "PUT",
                         "time": t["time"].strftime("%H:%M"), "entry": round(t["entry"], 2),
                         "stop": round(t["stop"], 2), "target": round(t["target"], 2), "r": round(t["r"], 3),
                         "kind": t["kind"], "base_r": round(t["base_r"], 3), "bars": len(days[d]["c"]),
                         **{k: round(lv[k], 2) for k in ("PP", "H3", "H4", "H5", "L3", "L4", "L5")}})
    return rows


def append(rows, path=LOG):
    if not rows:
        return
    fresh = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if fresh:
            w.writeheader()
        w.writerows(rows)


def report_lines(log):
    L = ["# ظل مستويات كاماريلا (أمامي، SPX على المؤشر)", "",
         f"- من {START_DATE} إلى {END_DATE}؛ قراءة فقط. R بعد كلفة 0.002% لكل جهة؛ المرجع = متوسط الاتجاهين بالهندسة نفسها.",
         "", "| القاعدة | صفقات | ربح% | متوسط R | فاصل 95% | المرجع | الحكم |", "|---|---|---|---|---|---|---|"]
    for s, name in cs.STRATS.items():
        tr = [{"r": float(r["r"]), "base_r": float(r["base_r"]), "kind": r["kind"]} for r in log if r["strat"] == s]
        m = cs.summarize(tr)
        if m["n"] == 0:
            L.append(f"| {name} | 0 | - | - | - | - | {m['verdict']} |")
        else:
            L.append(f"| {name} | {m['n']} | {m['win']:.0%} | {m['mean']:+.2f} | [{m['lo']:+.2f}, {m['hi']:+.2f}] | "
                     f"{m['base']:+.2f} | {m['verdict']} |")
    L.append("")
    return L


BOT_LOG = os.path.join(ROOT, "zero_dte_log.csv")
MIN_COMPARE = 30     # أقل من هذا من التوصيات/الإشارات لا نحكم في المقارنة


def read_bot_recs(path=BOT_LOG, start=START_DATE, end=END_DATE):
    """توصيات بوت SPX (date، time، side) من zero_dte_log.csv لفترة الظل فقط."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            try:
                if start <= date.fromisoformat(r["date"]) <= end and r["side"] in ("CALL", "PUT"):
                    out.append({"date": r["date"], "time": r["time"], "side": r["side"]})
            except (KeyError, ValueError):
                continue
    return out


def bot_hit_close(rec, day):
    """هل اتجه SPX في صالح توصية البوت من وقتها حتى الإغلاق؟ (True/False) أو None. السعر عند أول شمعة تبدأ عند الوقت أو بعده."""
    hh, mm = (int(x) for x in rec["time"].split(":"))
    for i, t in enumerate(day["t"]):
        if (t.hour, t.minute) >= (hh, mm):
            move = float(day["c"][-1]) - float(day["o"][i])
            return (move > 0) if rec["side"] == "CALL" else (move < 0)
    return None


def compare(log, recs, days):
    """مقارنة كاماريلا ببوت SPX: اتفاق الاتجاه في اليوم الواحد، ونسبة إصابة اتجاه السوق حتى الإغلاق لكل منهما. قراءة فقط."""
    by_day = {}
    for r in recs:
        by_day.setdefault(r["date"], []).append(r)
    cam = {}
    for r in log:
        cam.setdefault(r["date"], []).append(r)
    agree = disagree = only_cam = 0
    cam_hits, bot_hits = [], []
    r_agree, r_other = [], []
    for d, rows in cam.items():
        day = days.get(date.fromisoformat(d))
        if day is None:
            continue
        for r in rows:
            side = 1 if r["side"] == "CALL" else -1
            cam_hits.append(side * (float(day["c"][-1]) - float(r["entry"])) > 0)
        bots = {b["side"] for b in by_day.get(d, [])}
        sides = {r["side"] for r in rows}
        if not bots:
            only_cam += 1
        elif bots == sides:
            agree += 1
            r_agree += [float(r["r"]) for r in rows]
        else:
            disagree += 1
            r_other += [float(r["r"]) for r in rows]
    for d, rs in by_day.items():
        day = days.get(date.fromisoformat(d))
        if day is None:
            continue
        for b in rs:
            h = bot_hit_close(b, day)
            if h is not None:
                bot_hits.append(h)
    return {"agree": agree, "disagree": disagree, "only_cam": only_cam, "cam_hits": cam_hits, "bot_hits": bot_hits,
            "r_agree": r_agree, "r_other": r_other}


def compare_lines(c):
    def pct(h):
        return f"{sum(h)}/{len(h)} = {sum(h) / len(h):.0%}" if h else "-"

    def mean(x):
        return f"{sum(x) / len(x):+.2f} (n={len(x)})" if x else "-"

    small = len(c["cam_hits"]) < MIN_COMPARE or len(c["bot_hits"]) < MIN_COMPARE
    L = ["", "## مقارنة مع بوت SPX (قراءة فقط)", "",
         "- اتجاه كاماريلا (CALL/PUT) مقابل توصيات البوت في اليوم نفسه، ثم نسبة إصابة اتجاه SPX من وقت الإشارة حتى الإغلاق لكل منهما.",
         "- لا تقارن R بنتائج العقود: هذه حركة المؤشر وليست أسعار خيارات.", "",
         "| البند | القيمة |", "|---|---|",
         f"| أيام اتفق فيها الاتجاه | {c['agree']} |",
         f"| أيام اختلف فيها الاتجاه | {c['disagree']} |",
         f"| أيام إشارة كاماريلا بلا توصية للبوت | {c['only_cam']} |",
         f"| إصابة كاماريلا حتى الإغلاق | {pct(c['cam_hits'])} |",
         f"| إصابة البوت حتى الإغلاق | {pct(c['bot_hits'])} |",
         f"| متوسط R لكاماريلا عند الاتفاق مع البوت | {mean(c['r_agree'])} |",
         f"| متوسط R لكاماريلا عند الاختلاف | {mean(c['r_other'])} |", ""]
    if small:
        L.append(f"⚖️ **لا حكم**: أقل من {MIN_COMPARE} في إحدى العينتين.")
    L.append("")
    return L


def main(argv):
    if "--report" in argv:
        log = read_log()
        lines = report_lines(log)
        try:
            lines += compare_lines(compare(log, read_bot_recs(), cs.load_days()))
        except Exception as e:      # المقارنة إضافية ولا توقف التقرير الأساسي
            lines.append(f"تعذرت المقارنة مع البوت: {e}")
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("\n".join(lines))
        return 0
    days = cs.load_days()
    done = {(r["date"], r["strat"]) for r in read_log()}
    rows = new_rows(days, done)
    append(rows)
    print(f"camarilla_shadow: {len(rows)} صف جديد")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
