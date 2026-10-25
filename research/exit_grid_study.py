#!/usr/bin/env python3
"""شبكة الوقف والهدف: ماذا لو خرجت التوصيات بقاعدة مختلفة؟ (إعادة تشغيل على مسارات التتبع الفعلية؛ قراءة فقط؛ بطلب المستخدم 26/10/2026؛ H-EXIT مسجّلة في CLAUDE.md قبل البيانات الأمامية).

المصدر: مسارات ربح/خسارة كل عقد بعد التوصية — الشركات: stocks_positions.json + stocks_track.csv؛ SPX: spx_track.csv (وقت الدخول = أول سطر متابعة لكل عقد).
القواعد (وقف/هدف كنسبة من سعر الدخول): A=قاعدة البوت (−40%/+50%)، B=(−60%/+100%)، C=(−40%/+100%)، D=(−60%/+50%) مرشحة؛ E=(بلا وقف/+100%) مرجع فقط.
لا وقف قبل مرور min_age (30 دقيقة للشركات، 15 لـSPX). الخروج بسعر الحد (متفائل مقارنة بالتنفيذ الفعلي)؛ إن لم يُصَب أي حد فآخر قيمة متابَعة (عقد انتهى).
الأمامية = دخلت من FORWARD_FROM وانتهى عقدها قبل اليوم؛ ما قبلها استكشافي ويُعرض منفصلاً. الفرضية: متوسط (القاعدة − A) على التوصية نفسها > 0 بفاصل 95% بإعادة معاينة أيام الدخول، بعد 40 توصية (الشركات) / 30 (SPX).
تشغيل: python research/exit_grid_study.py   المخرج: research/exit_grid_study_results.md
"""
import json
import os
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "exit_grid_study_results.md")
FORWARD_FROM = "2026-10-26"
RULES = {"A": (0.40, 0.50), "B": (0.60, 1.00), "C": (0.40, 1.00), "D": (0.60, 0.50), "E": (None, 1.00)}
CANDIDATES = ("B", "C", "D")
MIN_AGE = {"stocks": 30, "spx": 15}
MIN_N = {"stocks": 40, "spx": 30}
NAMES = {"A": "قاعدة البوت −40%/+50%", "B": "−60%/+100%", "C": "−40%/+100%", "D": "−60%/+50%", "E": "بلا وقف/+100% (مرجع)"}


# ============================ دوال نقية ============================
def replay(path, t0, stop, tp, min_age_min):
    """path: قائمة (وقت pandas Timestamp، ربح/خسارة نسبي). يرجع نتيجة القاعدة أو None إن لم يوجد مسار بعد t0."""
    seq = sorted((t, v) for t, v in path if t >= t0)
    if not seq:
        return None
    for t, v in seq:
        if tp is not None and v >= tp:
            return tp
        if stop is not None and (t - t0) >= pd.Timedelta(minutes=min_age_min) and v <= -stop:
            return -stop
    return float(seq[-1][1])


def build_paths(track):
    """{id: [(Timestamp، pnl)]} من DataFrame التتبع (أعمدة time وid وpnl)."""
    out = {}
    for pid, g in track.groupby("id"):
        out[pid] = [(pd.Timestamp(str(t)[:16]), float(v)) for t, v in zip(g["time"], g["pnl"]) if pd.notna(v)]
    return out


def evaluate(entries, paths, min_age):
    """entries: [{id, t0 (Timestamp), exp (date)}] → DataFrame(id, day, A..E) للتوصيات التي لها مسار."""
    rows = []
    for e in entries:
        path = paths.get(e["id"])
        if not path:
            continue
        r = {"id": e["id"], "day": e["t0"].date().isoformat(), "exp": e["exp"].isoformat()}
        res = {k: replay(path, e["t0"], s, tp, min_age) for k, (s, tp) in RULES.items()}
        if any(v is None for v in res.values()):
            continue
        r.update(res)
        rows.append(r)
    return pd.DataFrame(rows)


def split_forward(df, today=None, forward_from=FORWARD_FROM):
    """(أمامية منتهية، سابقة منتهية). المنتهية = تاريخ الانتهاء قبل اليوم."""
    today = (today or date.today()).isoformat()
    done = df[df["exp"] < today] if len(df) else df
    return done[done["day"] >= forward_from], done[done["day"] < forward_from]


def paired_ci(df, rule, base="A", n=3000, seed=43):
    """متوسط (rule − base) لكل توصية وفاصل 95% بإعادة معاينة أيام الدخول. None إن قلّت الأيام عن 8."""
    if len(df) == 0:
        return None
    d = (df[rule] - df[base]).groupby(df["day"]).agg(["sum", "count"])
    if len(d) < 8:
        return {"mean": float((df[rule] - df[base]).mean()), "ci": None, "n": len(df)}
    S, N = d["sum"].to_numpy(), d["count"].to_numpy()
    idx = np.random.default_rng(seed).integers(0, len(S), (n, len(S)))
    m = S[idx].sum(1) / N[idx].sum(1)
    return {"mean": float((df[rule] - df[base]).mean()), "ci": (float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))), "n": len(df)}


def verdict(df, kind):
    """حكم H-EXIT: أي قاعدة مرشحة تتفوق على A بفاصل يستثني الصفر بعد MIN_N توصية."""
    if len(df) < MIN_N[kind]:
        return f"H-EXIT ({kind}): لا حكم بعد ({len(df)} من {MIN_N[kind]} توصية أمامية منتهية)"
    wins = []
    for r in CANDIDATES:
        c = paired_ci(df, r)
        if c and c["ci"] and c["ci"][0] > 0:
            wins.append((c["mean"], r))
    if wins:
        m, r = max(wins)
        return f"H-EXIT ({kind}): تحققت للقاعدة {r} ({NAMES[r]}): تتفوق على A بمتوسط {100 * m:+.1f}% للتوصية — القرار لصاحب المشروع"
    return f"H-EXIT ({kind}): لم تتحقق: لا قاعدة مرشحة تتفوق على A بفاصل يستثني الصفر"


def table(df, title):
    L = [f"### {title} ({len(df)} توصية)", ""]
    if len(df) == 0:
        return L + ["لا توصيات بعد.", ""]
    L += ["| القاعدة | متوسط/توصية | رابحة | الفرق عن A | فاصل 95% (%) |", "|---|---|---|---|---|"]
    for k in RULES:
        v = df[k]
        c = paired_ci(df, k) if k != "A" else None
        diff = "-" if c is None else f"{100 * c['mean']:+.1f}%"
        ci = "-" if c is None or c["ci"] is None else f"{100 * c['ci'][0]:+.1f}…{100 * c['ci'][1]:+.1f}"
        L.append(f"| {k}: {NAMES[k]} | {100 * v.mean():+.1f}% | {100 * (v > 0).mean():.0f}% | {diff} | {ci} |")
    return L + [""]


# ============================ التحميل والتقرير ============================
def load_stocks(root=ROOT):
    pos = json.load(open(os.path.join(root, "stocks_positions.json"), encoding="utf-8")).get("positions", [])
    entries = [{"id": p["id"], "t0": pd.Timestamp(p["time"][:16]), "exp": date.fromisoformat(p["exp"])} for p in pos]
    track = pd.read_csv(os.path.join(root, "stocks_track.csv"))
    return entries, build_paths(track)


def load_spx(root=ROOT):
    track = pd.read_csv(os.path.join(root, "spx_track.csv"))
    paths = build_paths(track)
    entries = []
    for pid, path in paths.items():
        try:
            exp = date.fromisoformat(pid.split(":")[-1].split("@")[0])
        except ValueError:
            continue
        entries.append({"id": pid, "t0": min(t for t, _ in path), "exp": exp})
    return entries, paths


def report(sections, today=None):
    L = ["# شبكة الوقف والهدف على التوصيات الفعلية (نتائج آلية، إعادة تشغيل)", "",
         "- قراءة فقط. الخروج بسعر الحد (متفائل)، والمتابعة متقطعة. الأمامية = دخلت من 26/10/2026 وانتهى عقدها؛ السابقة استكشافية (رُئيت قبل التسجيل).",
         "- **الفرضية H-EXIT المسجّلة مسبقاً:** قاعدة مرشحة (B أو C أو D) تتفوق على A بفاصل 95% بعد 40 توصية (الشركات) / 30 (SPX).", ""]
    for kind, label, df in sections:
        fwd, past = split_forward(df, today)
        L += [f"## {label}", ""] + table(fwd, "أمامية منتهية (الحكم)") + table(past, "سابقة منتهية (استكشاف فقط)")
        L += ["⚖️ **" + verdict(fwd, kind) + "**", ""]
    return L


def main(argv):
    sections = []
    for kind, label, loader in (("stocks", "الشركات", load_stocks), ("spx", "SPX/SPY", load_spx)):
        try:
            entries, paths = loader()
            sections.append((kind, label, evaluate(entries, paths, MIN_AGE[kind])))
        except Exception as e:  # noqa: BLE001
            print(f"تعذر تحميل {label}: {type(e).__name__}")
            sections.append((kind, label, pd.DataFrame(columns=["id", "day", "exp"] + list(RULES))))
    L = report(sections)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
