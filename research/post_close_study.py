#!/usr/bin/env python3
"""ماذا فعلت العقود بعد إقفالها بالقاعدة؟ (دراسة وصفية قراءة فقط؛ بطلب المستخدم 26/10/2026: «لو ترك العقد بعد الهدف فين أعلى سعر؟ والمحجوب بالوقف يرتد؟»).

المصدر: stocks_positions.json (الشركات: peak_after وlast_pnl وpeak_after_at) وspx_track.csv (SPX: المتابعة الصامتة بعد الإقفال).
نعرض لكل مصدر: بعد الوقف نسبة من ارتد فوق سعر الدخول ومن بلغ +50%؛ وبعد الهدف نسبة من واصل أعلى من الهدف؛ مع الأرقام الخام.
وصفية فقط: بلا فرضية ولا حكم. الحكم على تغيير الوقف/الهدف في exit_grid_study (H-EXIT) على أيام جديدة.
قيود: المتابعة كل 15–30 دقيقة وبسعر الوسط، والقمم المرصودة أدنى من القمم الحقيقية؛ وعقد لم ينتهِ بعد قد يرتفع أو ينخفض أكثر.
تشغيل: python research/post_close_study.py   المخرج: research/post_close_study_results.md
"""
import json
import os
import sys
from datetime import date, datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import post_close as pc  # noqa: E402

OUT = os.path.join(HERE, "post_close_study_results.md")
STOCK_STOP = 0.40


def stock_rows(positions, today=None):
    """[{kind, peak, last, expired, days_to_peak}] لعقود الشركات المقفلة (peak_after مسجّل للموجب فقط)."""
    today = (today or date.today()).isoformat()
    out = []
    for p in positions:
        c = p.get("closed")
        kind = c["kind"] if c else ("stop" if p.get("status") == "stopped" else None)
        if kind not in ("tp", "stop"):
            continue
        d = None
        try:
            if p.get("peak_after_at") and c and c.get("time"):
                d = (datetime.fromisoformat(p["peak_after_at"]).date() - datetime.fromisoformat(c["time"]).date()).days
        except (ValueError, TypeError):
            d = None
        out.append({"kind": kind, "peak": p.get("peak_after"), "last": p.get("last_pnl"), "expired": p.get("exp", "9999") < today, "days_to_peak": d, "id": p["id"]})
    return out


def spx_rows(track, today=None):
    """[{kind, peak, last, expired}] من spx_track.csv: ما بعد أول صف حالته tp أو stopped."""
    today = (today or date.today()).isoformat()
    out = []
    for pid, g in track.groupby("id"):
        g = g.sort_values("time")
        idx = next((k for k, st in enumerate(g["status"]) if st in ("tp", "stopped")), None)
        if idx is None:
            continue
        pk, lst = pc.from_path(g["pnl"].iloc[idx + 1:])
        try:
            exp = pid.split(":")[-1]
        except Exception:  # noqa: BLE001
            exp = "9999"
        out.append({"kind": "tp" if g["status"].iloc[idx] == "tp" else "stop", "peak": pk, "last": lst, "expired": exp < today, "id": pid, "days_to_peak": None})
    return out


def pct(x):
    return "-" if x is None else f"{100 * x:.0f}%"


def section(title, rows):
    s = pc.summarize(rows)
    done = pc.summarize([r for r in rows if r["expired"]])
    L = [f"## {title}", "", f"- مقفل بالوقف: {s['n_stop']} | مقفل بالهدف: {s['n_tp']} (منها منتهية العقد: {done['n_stop']} وقف، {done['n_tp']} هدف)", "",
         "| المقياس | كل المقفل | المنتهي عقده فقط |", "|---|---|---|",
         f"| بعد الوقف: ارتد فوق سعر الدخول | {pct(s['stop_back_above_entry'])} | {pct(done['stop_back_above_entry'])} |",
         f"| بعد الوقف: بلغ +50% (كان الهدف سيتحقق) | {pct(s['stop_reached_tp'])} | {pct(done['stop_reached_tp'])} |",
         f"| بعد الهدف: واصل أعلى من الهدف | {pct(s['tp_went_higher'])} | {pct(done['tp_went_higher'])} |", ""]
    stops = [r for r in rows if r["kind"] == "stop" and r["peak"] is not None and r["peak"] > 0]
    if stops:
        L += ["أمثلة ارتد فيها العقد بعد الوقف:", ""] + [f"- {r['id']}: أعلى {100 * r['peak']:+.0f}% بعد الوقف، آخر {100 * (r['last'] or 0):+.0f}%" + (f"، بعد {r['days_to_peak']} يوم" if r.get("days_to_peak") is not None else "") for r in stops[:8]] + [""]
    return L


def report(stock, spx, meta):
    L = ["# ماذا فعلت العقود بعد الإقفال بالقاعدة؟ (وصفي، نتائج آلية)", "", f"- {meta}",
         "- قراءة فقط ولا تغيّر أي قاعدة. القيم نسب من سعر دخول البوت، بفحوص دورية وسعر الوسط (القمم أدنى من الحقيقية). لا فرضية ولا حكم هنا؛ الحكم على الوقف والهدف في H-EXIT على أيام جديدة.", ""]
    L += section("الشركات", stock) + section("SPX/SPY", spx)
    return L


def main(argv):
    try:
        positions = json.load(open(os.path.join(ROOT, "stocks_positions.json"), encoding="utf-8")).get("positions", [])
    except Exception:  # noqa: BLE001
        positions = []
    try:
        track = pd.read_csv(os.path.join(ROOT, "spx_track.csv"))
    except Exception:  # noqa: BLE001
        track = pd.DataFrame(columns=["time", "id", "status", "pnl"])
    stock, spx = stock_rows(positions), spx_rows(track)
    L = report(stock, spx, f"{len(stock)} عقد شركات مقفل، {len(spx)} عقد SPX/SPY مقفل")
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
