"""دراسة إعادة تشغيل: ماذا لو خرج بوت الشركات بقاعدة وقف مختلفة؟ (قراءة فقط؛ لا تغيّر أي توصية)

المصدر: stocks_positions.json (سعر الدخول وسهمه) وstocks_track.csv (متابعة كل عقد: نسبة حركة السهم عن الدخول وربح/خسارة العقد).
القواعد المقارَنة على التوصيات نفسها:
  base  = قاعدة البوت الفعلية (النتيجة المسجّلة: هدف +50% أو وقف −40% أو آخر قيمة مسجّلة)
  s15   = وقف على حركة السهم: يخرج عند أول متابعة يكون فيها السهم ضد الاتجاه بـ1.5% فأكثر، بشرط ألا يكون ذلك في أول 30 دقيقة بعد
          افتتاح اليوم التالي للدخول، مع إبقاء هدف +50% كما هو
  s15n  = مثل s15 لكن بلا استثناء أول 30 دقيقة
وتقسيم حسب المدة: التوصيات التي مدتها 9 أيام تقويمية فأكثر مقابل الأقصر.
⚠️ العينة صغيرة جداً (عشرات التوصيات في أيام قليلة) والمتابعة كل ~15-30 دقيقة لا شمعة كل دقيقة: للمراقبة لا للحكم.
تشغيل: python research/stocks_exit_study.py   المخرج: research/stocks_exit_study_results.md
"""
import json
import os
import sys
from datetime import date, datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "stocks_exit_study_results.md")
TP, STOP = 0.50, -0.40
STOCK_STOP = 0.015
OPEN_GRACE_MIN = 30
MIN_AGE_MIN = 30
LONG_DTE = 9


def adverse(side, move):
    return -move if side == "CALL" else move


def replay(rows, side, entry_day, rule, entry_time, tp=TP):
    """rows: DataFrame مرتب بالزمن (time كنص ISO، stock_move، pnl). يرجع pnl الخروج حسب القاعدة."""
    last = None
    for r in rows.itertuples():
        last = r.pnl
        t = datetime.fromisoformat(r.time)
        young = (t - datetime.fromisoformat(entry_time)).total_seconds() / 60 < MIN_AGE_MIN   # كالبوت: لا وقف في أول 30 دقيقة
        if r.pnl >= tp:
            return tp                      # كما يسجّل البوت: الهدف يُحسب عند +50% لا عند القفزة الفعلية
        if rule == "base":
            if r.pnl <= STOP and not young:
                return STOP                # كما يسجّل البوت: الوقف عند −40%
            continue
        grace = rule == "s15" and t.date().isoformat() > entry_day and t.hour * 60 + t.minute < 9 * 60 + 30 + OPEN_GRACE_MIN
        if adverse(side, r.stock_move) >= STOCK_STOP and not grace and not young:
            return r.pnl
    return last


def load():
    pos = json.load(open(os.path.join(ROOT, "stocks_positions.json"), encoding="utf-8"))["positions"]
    trk = pd.read_csv(os.path.join(ROOT, "stocks_track.csv"))
    return pos, trk


def build(pos, trk):
    res = []
    for p in pos:
        rows = trk[trk["id"] == p["id"]].sort_values("time")
        if rows.empty or not p.get("entry"):
            continue
        # نبدأ من لحظة الدخول فقط
        rows = rows[rows["time"] >= p["time"]]
        if rows.empty:
            continue
        entry_day = p["time"][:10]
        dte = (date.fromisoformat(p["exp"]) - date.fromisoformat(entry_day)).days
        res.append({"id": p["id"], "side": p["side"], "dte": dte, "entry_day": entry_day, "actual": (p.get("closed") or {}).get("pnl"),
                    **{r: replay(rows, p["side"], entry_day, r, p["time"]) for r in ("base", "s15", "s15n")}})
    return pd.DataFrame(res)


def line(name, s):
    s = s.dropna()
    return f"| {name} | {len(s)} | {100 * s.mean():+.1f}% | {100 * s.median():+.1f}% | {int((s > 0).sum())}/{len(s)} |" if len(s) else f"| {name} | 0 | - | - | - |"


def main():
    pos, trk = load()
    d = build(pos, trk)
    L = ["# دراسة إعادة تشغيل: وقف على حركة السهم لبوت الشركات (آلية)", "",
         "- قراءة فقط من stocks_positions.json وstocks_track.csv، ولا تغيّر أي توصية.",
         "- ⚠️ عينة صغيرة جداً ومتابعة متقطعة؛ للمراقبة لا للحكم، ولا قرار قبل عينة أكبر بكثير ثم تجربة ظل حيّة.",
         f"- التوصيات المشمولة: {len(d)} في {d['entry_day'].nunique() if len(d) else 0} يوم دخول.", ""]
    if d.empty:
        L.append("لا بيانات كافية بعد.")
    else:
        both = d.dropna(subset=["actual"])
        L += [f"- فحص صحة: على {len(both)} توصية مغلقة فعلياً، متوسط النتيجة المسجّلة {100 * both['actual'].mean():+.1f}% "
              f"مقابل إعادة التشغيل بقاعدة البوت {100 * both['base'].mean():+.1f}% (يجب أن يتقاربا).", "",
              "| القاعدة | صفقات | متوسط | وسيط | رابحة |", "|---|---|---|---|---|",
              line("قاعدة البوت (−40% على العقد)", d["base"]),
              line("وقف السهم 1.5% (مع استثناء أول 30د بعد الافتتاح)", d["s15"]),
              line("وقف السهم 1.5% (بلا استثناء)", d["s15n"]), "",
              f"**حسب المدة (قاعدة البوت):**", "", "| المدة | صفقات | متوسط | وسيط | رابحة |", "|---|---|---|---|---|",
              line(f"{LONG_DTE} أيام فأكثر", d[d["dte"] >= LONG_DTE]["base"]),
              line(f"أقل من {LONG_DTE} أيام", d[d["dte"] < LONG_DTE]["base"])]
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
