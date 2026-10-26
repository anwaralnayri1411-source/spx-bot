#!/usr/bin/env python3
"""تقرير H-COMBO المشترك (قراءة فقط؛ الفرضية مسجّلة في CLAUDE.md قبل كتابته؛ بطلب المستخدم 26/10/2026 عبر جلسة الخادم).

الفكرة: هل تتحسن إشارة البوت القوية (edge7: فرق نقاط ≥ 7) حين يتفق معها الزخم وأول 30 دقيقة (candidates_shadow: agree)؟
الربط: لكل إشارة edge7 نأخذ آخر موعد من 10:30/11:30/12:30/13:30 يسبق وقتها في اليوم نفسه (معلومة معروفة وقتها). combo = اتجاه الإشارة يوافق agree في ذلك الموعد (كول ↔ +1، بوت ↔ −1).
المقياس: عائد عقد ATM اليوم التالي في اتجاه الإشارة (شراء بالطلب، خروج بعد 60 دقيقة بالبيع) = pnl في edge7؛ المهارة = عائد الاتجاه − متوسط عائد الاتجاهين (العقد المعاكس في السجل نفسه).
إشارة واحدة لكل وقت دخول: نفضّل جذر SPY على SPXW إن وُجد (متطابقان تقريباً فلا يُعدّان مرتين).
الفرضية: متوسط مهارة combo > 0 بفاصل 95% بإعادة معاينة الأيام بعد 40 حدثاً على 25 يوماً مختلفاً على الأقل. الباقي وصفي بلا حكم.
تشغيل: python research/combo_report.py   المخرج: research/combo_report_results.md
"""
import csv
import os
import re
import sys
from datetime import datetime

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EDGE7 = os.path.join(ROOT, "edge7_shadow_log.csv")
CAND = os.path.join(ROOT, "candidates_shadow_log.csv")
OUT = os.path.join(HERE, "combo_report_results.md")
SLOTS = ("10:30", "11:30", "12:30", "13:30")
MIN_EVENTS, MIN_DAYS = 40, 25


# ============================ دوال نقية ============================
def root_of(contract):
    m = re.match(r"^([A-Z]+)\d{6}", contract or "")
    return m.group(1) if m else ""


def signals(rows):
    """إشارات edge7: [{time، day، side، pnl، skill}] بإشارة واحدة لكل وقت دخول (SPY قبل SPXW). rows: صفوف CSV (نصوص)."""
    by = {}
    for r in rows:
        try:
            pnl = float(r["pnl"])
        except (KeyError, TypeError, ValueError):
            continue
        by.setdefault((r["entry_time"], root_of(r["contract"])), {})[r["role"]] = (r, pnl)
    chosen = {}
    for (t, root), d in by.items():
        if "bot" not in d or "opp" not in d:
            continue
        cur = chosen.get(t)
        if cur is None or (root == "SPY" and cur["root"] != "SPY"):
            (rb, pb), (_, po) = d["bot"], d["opp"]
            chosen[t] = {"time": t, "day": t[:10], "side": rb["side"], "pnl": pb, "skill": pb - (pb + po) / 2, "root": root}
    return sorted(chosen.values(), key=lambda x: x["time"])


def slot_for(time_iso):
    """آخر موعد من SLOTS يسبق أو يساوي وقت الإشارة (HH:MM نيويورك) أو None."""
    hhmm = time_iso[11:16]
    ok = [s for s in SLOTS if s <= hhmm]
    return ok[-1] if ok else None


def join(sigs, cand_rows):
    """يضيف agree لكل إشارة من candidates في اليوم والموعد المطابقين: (إشارات مرتبطة، عدد بلا موعد مطابق)."""
    idx = {}
    for r in cand_rows:
        if r.get("status") == "closed":
            try:
                idx[(r["day"], r["slot"])] = int(float(r["agree"]))
            except (KeyError, ValueError):
                continue
    out, missing = [], 0
    for s in sigs:
        sl = slot_for(s["time"])
        a = idx.get((s["day"], sl)) if sl else None
        if a is None:
            missing += 1
            continue
        want = 1 if s["side"] == "CALL" else -1
        out.append(dict(s, slot=sl, agree=a, combo=(a == want)))
    return out, missing


def boot_mean(vals_by_day, n=3000, seed=71):
    days = list(vals_by_day)
    if len(days) < 8:
        return None
    S = np.array([sum(vals_by_day[d]) for d in days])
    N = np.array([len(vals_by_day[d]) for d in days])
    idx = np.random.default_rng(seed).integers(0, len(days), (n, len(days)))
    m = S[idx].sum(1) / N[idx].sum(1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def stat(events, key):
    if not events:
        return None
    by = {}
    for e in events:
        by.setdefault(e["day"], []).append(e[key])
    vals = [e[key] for e in events]
    return {"n": len(vals), "days": len(by), "mean": float(np.mean(vals)), "ci": boot_mean(by)}


def fmt(s, pct=True):
    if s is None:
        return "0 | 0 | - | -"
    ci = "-" if s["ci"] is None else f"{100 * s['ci'][0]:+.1f}…{100 * s['ci'][1]:+.1f}"
    return f"{s['n']} | {s['days']} | {100 * s['mean']:+.1f}% | {ci}"


def verdict(combo_events):
    s = stat(combo_events, "skill")
    if s is None or s["n"] < MIN_EVENTS or s["days"] < MIN_DAYS:
        return f"H-COMBO: لا حكم بعد ({0 if s is None else s['n']} من {MIN_EVENTS} حدثاً، {0 if s is None else s['days']} من {MIN_DAYS} يوماً)"
    if s["ci"] is None:
        return "H-COMBO: لا حكم بعد (أيام قليلة)"
    lo, hi = s["ci"]
    desc = f"مهارة {100 * s['mean']:+.1f}% بفاصل {100 * lo:+.1f}…{100 * hi:+.1f}"
    return "H-COMBO: " + ("تحققت (" if lo > 0 else "عكسية (" if hi < 0 else "لم تتحقق (") + desc + (")" if lo > 0 or hi < 0 else " يشمل الصفر)")


def report(joined, missing, total):
    combo = [e for e in joined if e["combo"]]
    rest = [e for e in joined if not e["combo"]]
    L = ["# H-COMBO: الإشارة القوية للبوت مع اتفاق الزخم وأول 30 دقيقة (نتائج آلية، ظل)", "",
         f"- إشارات edge7 (إشارة واحدة لكل وقت دخول): {total}، مرتبطة بموعد candidates: {len(joined)}، بلا موعد مطابق (مستبعدة): {missing}.",
         "- العائد = عقد ATM اليوم التالي في اتجاه الإشارة، شراء بالطلب وخروج بعد 60 دقيقة بالبيع. المهارة = عائد الاتجاه − متوسط الاتجاهين.",
         f"- **الفرضية المسجّلة مسبقاً:** متوسط مهارة combo > 0 بفاصل 95% بعد {MIN_EVENTS} حدثاً على {MIN_DAYS} يوماً. الباقي وصفي بلا حكم.", ""]
    if not joined:
        return L + ["لا إشارات مرتبطة بعد."]
    L += ["| المجموعة | أحداث | أيام | متوسط العائد الصافي | فاصل 95% (%) | متوسط المهارة | فاصل 95% (%) |", "|---|---|---|---|---|---|---|"]
    for name, ev in (("**combo** (إشارة قوية + اتفاق)", combo), ("إشارة قوية بلا اتفاق", rest), ("كل الإشارات القوية (edge7 وحده)", joined)):
        a, b = stat(ev, "pnl"), stat(ev, "skill")
        L.append(f"| {name} | {fmt(a)} | {fmt(b).split(' | ', 2)[2] if b else '- | -'} |")
    L += ["", "⚖️ **" + verdict(combo) + "**"]
    return L


def read(path):
    return list(csv.DictReader(open(path, encoding="utf-8"))) if os.path.exists(path) else []


def main(argv):
    sigs = signals(read(EDGE7))
    joined, missing = join(sigs, read(CAND))
    L = report(joined, missing, len(sigs))
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
