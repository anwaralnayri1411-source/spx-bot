"""ظل فلتر الأخبار (لا يؤثر على التوصيات): يسجّل كل فحص SPX حجبه فلتر الأخبار أو أضعفه، مع اتجاه البوت وقوته،
في news_shadow_log.csv. النتيجة (حركة SPX بعد الحجب) يحسبها research/news_shadow_eval.py بعد الإغلاق.
- blocked: صدمة مستوى 3 أوقفت الإشارة، والفرق كان ≥ MIN_EDGE (أي كانت ستُرسل لولا الأخبار والأسباب الأخرى).
- weakened: صدمة مستوى 2 خصمت نقطة فنزل الفرق تحت MIN_EDGE.
other_reasons = أسباب منع أخرى في الفحص نفسه (لو وُجدت فالحجب ليس بسبب الأخبار وحدها)."""
import csv
import os

import options_bot as ob

LOG = "news_shadow_log.csv"
COLS = ["time", "spot", "side", "bull", "bear", "edge", "kind", "level", "flags", "other_reasons"]
NEWS_PREFIX = "مخاطر الأخبار"


def snapshot(n, a):
    shock = a["shock"]
    raw = abs(a["bull"] - a["bear"])
    news = [r for r in a["reasons"] if r.startswith(NEWS_PREFIX)]
    if a.get("news_half") and a.get("contracts"):
        kind = "allowed_half"       # منذ 31/10/2026: مستوى 3 لا يمنع، التوصية بنصف الكمية (H-NEWSHALF)
    elif news and raw >= ob.MIN_EDGE:
        kind = "blocked"
    elif shock.get("level") == 2 and raw >= ob.MIN_EDGE > a["edge"]:
        kind = "weakened"
    else:
        return
    other = [r for r in a["reasons"] if not r.startswith(NEWS_PREFIX) and not r.startswith("لا اتجاه واضح")]
    row = {"time": n.strftime("%Y-%m-%d %H:%M"), "spot": round(float(a["c"]["S"]), 2), "side": a["side"],
           "bull": round(a["bull"], 2), "bear": round(a["bear"], 2), "edge": round(raw, 2), "kind": kind,
           "level": shock.get("level"), "flags": " | ".join(shock.get("flags", [])[:4]),
           "other_reasons": " | ".join(r[:60] for r in other)}
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        w.writerow(row)
    print("ظل فلتر الأخبار:", row["kind"], row["side"], row["edge"])
