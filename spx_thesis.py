"""اختبار «وقف الفكرة» لصفقات SPX في الظل (لا يرسل ولا يغيّر أي توصية ولا قواعد الوقف الحالية).
يقارن: القاعدة الحالية (وقف -40% على سعر العقد) مقابل وقف على حركة SPX نفسه ضد الصفقة بنسب مختلفة.
المدخل: spx_positions.json + spx_track.csv.  المخرج: spx_thesis_log.csv (يُعاد بناؤه كل مرة).
تنبيه: القراءات متأخرة ~15 دقيقة والأسعار نظرية، والعينة صغيرة جداً — للمراقبة وليس للحكم."""
import csv
import json
from collections import defaultdict

POS, TRACK, OUT = "spx_positions.json", "spx_track.csv", "spx_thesis_log.csv"
THRESH = (0.0015, 0.0025, 0.0035)   # حركة SPX ضد الصفقة: 0.15% / 0.25% / 0.35%


def build():
    P = {p["id"]: p for p in json.load(open(POS, encoding="utf-8")).get("positions", [])}
    rows = defaultdict(list)
    with open(TRACK, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["id"] in P and r["spot"]:
                rows[r["id"]].append((r["time"], float(r["spot"]), float(r["val"])))
    out = []
    for pid, pts in rows.items():
        p = P[pid]
        e_spot, entry, put = p.get("entry_spot"), p["entry"], p["side"] == "PUT"
        if not e_spot or not pts:
            continue
        pts = [x for x in pts if x[0] >= p["time"]]
        if not pts:
            continue
        # أسوأ حركة ضد الصفقة (PUT: ارتفاع SPX، CALL: هبوط)
        adv = [((s - e_spot) if put else (e_spot - s)) / e_spot for _, s, _ in pts]
        vals = [v for _, _, v in pts]
        stale = len(vals) > 3 and len(set(vals)) == 1 and abs(pts[-1][1] - e_spot) / e_spot > 0.003
        best = max(v / entry - 1 for v in vals)
        base = {"id": pid, "entry": entry, "entry_spot": e_spot, "max_adverse_pct": round(max(adv) * 100, 3),
                "peak_pnl": round(best, 3), "final_pnl": round(vals[-1] / entry - 1, 3),
                "current_rule": "stop " + p["alerts"]["stop"][11:16] if "stop" in p.get("alerts", {}) else "no stop",
                "suspect_stale_price": stale}
        for t in THRESH:
            k = next((i for i, a in enumerate(adv) if a >= t), None)
            base[f"thesis_{t * 100:.2f}"] = ("لم يُفعَّل" if k is None else
                                              f"stop {pts[k][0][11:16]} pnl {pts[k][2] / entry - 1:+.0%}")
        out.append(base)
    return out


def write():
    res = build()
    if res:
        with open(OUT, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(res[0].keys()))
            w.writeheader()
            w.writerows(res)
    return res


if __name__ == "__main__":
    for r in write():
        print(r)
