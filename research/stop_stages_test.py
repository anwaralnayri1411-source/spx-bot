#!/usr/bin/env python3
"""H-STOPSTAGES: هل يحسّن رفع الوقف بمراحل نتيجة عقود 0DTE؟ (مسجّل مسبقاً في CLAUDE.md 6/11/2026). اختبار فقط.

عيّنتان بشموع SPXW الحقيقية بالدقيقة من Alpaca، وطرق الخروج ثابتة في RULES قبل التشغيل:
- MGT: صفقاتهم المعاد بناؤها بأبكر وأبعد وقت دخول ممكن (`mgt_reverse2_rows.csv`). منتقاة لأنها منشورة ولأن بناءها يشترط بلوغ القمة.
- إشارات: إشارات بوت SPX في H-NOCHASE (`nochase_rows.csv`). غير منتقاة بالنتيجة، فهي العيّنة الحاكمة.
داخل الشمعة نفترض الأسوأ: الوقف قبل الهدف، ولا يرتفع الوقف إلا من الشمعة التالية. تنفيذ الوقف عند الأدنى بين الوقف وافتتاح الشمعة، وكلفة 1.5% على الخروج.
تشغيل (الخادم، ALPACA): SPXBOT_DATA=/opt/spxbot/data python research/stop_stages_test.py
المخرج: research/stop_stages_results.md
"""
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mgt_reverse as mr  # noqa: E402

DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
OUT = os.path.join(HERE, "stop_stages_results.md")
COST, STOP0, N_BOOT = 0.015, 0.5, 3000
WIDE = ((0.5, "fix", 1.0), (1.0, "fix", 1.5), (2.0, "peak", 0.6))
LATE = ((1.0, "fix", 1.0), (2.0, "fix", 1.5), (3.0, "peak", 0.6))
RULES = {
    "tp50": {"name": "هدف +50% (الأساس)", "tp": 0.5},
    "tp100": {"name": "هدف +100%", "tp": 1.0},
    "be_tp100": {"name": "وقف للدخول عند +50% وهدف +100%", "tp": 1.0, "stages": ((0.5, "fix", 1.0),)},
    "wide": {"name": "مراحل واسعة", "stages": WIDE},
    "wide_half": {"name": "مراحل واسعة + نصف عند +100%", "stages": WIDE, "half": True},
    "late": {"name": "مراحل متأخرة", "stages": LATE},
    "late_half": {"name": "مراحل متأخرة + نصف عند +100%", "stages": LATE, "half": True},
    "tight": {"name": "قفل سريع (70% من القمة)", "stages": ((0.5, "fix", 1.0), (1.0, "peak", 0.7))},
    "half_trail": {"name": "نصف عند +100% ووقف 50% من القمة", "stages": ((1.0, "peak", 0.5),), "half": True},
    "hold": {"name": "وقف 50% ثم الإغلاق"},
}


def simulate(b, t0, entry, rule, cost=COST):
    """عائد العقد الواحد (نسبة من الدخول) بقاعدة خروج على شموع الدقيقة، أو None."""
    x = b[(b.index >= t0) & (b.index.strftime("%H:%M") <= "15:55")]
    if x.empty:
        return None
    stop, peak, held, got = entry * (1 - STOP0), entry, 1.0, 0.0
    tp, half = rule.get("tp"), rule.get("half")
    for o, h, lo in zip(x["o"].values, x["h"].values, x["l"].values):
        if lo <= stop:
            return got + held * (min(stop, o) * (1 - cost) / entry - 1)
        if half and held == 1.0 and h >= 2 * entry:
            got, held = 0.5 * (2 * (1 - cost) - 1), 0.5
        if tp and h >= entry * (1 + tp):
            return got + held * ((1 + tp) * (1 - cost) - 1)
        peak = max(peak, h)
        for gain, kind, v in rule.get("stages", ()):
            if peak / entry - 1 >= gain:
                stop = max(stop, entry * v if kind == "fix" else peak * v)
    return got + held * (float(x["c"].values[-1]) * (1 - cost) / entry - 1)


def boot_diff(a, base, days, rng, n=N_BOOT):
    """فاصل 95% لفرق المتوسط عن الأساس بإعادة السحب بالأيام."""
    d = pd.DataFrame({"x": a - base, "day": days}).groupby("day")["x"].agg(["sum", "count"]).values
    if len(d) < 2:
        return np.nan, np.nan
    i = rng.integers(0, len(d), (n, len(d)))
    m = d[i, 0].sum(1) / d[i, 1].sum(1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def table(df, rng):
    """جدول لكل قاعدة: المتوسط والوسيط ونسبة الربح والمتوسط دون أفضل يوم وفرقه عن الأساس."""
    rows = []
    for k, r in RULES.items():
        x = df.dropna(subset=[k])
        best = x.groupby("day")[k].sum().idxmax()
        y = x[x["day"] != best]
        lo, hi = boot_diff(x[k].values, x["tp50"].values, x["day"].values, rng)
        rows.append({"key": k, "name": r["name"], "n": len(x), "mean": x[k].mean(), "median": x[k].median(), "win": (x[k] > 0).mean(),
                     "no_best": y[k].mean(), "base_no_best": y["tp50"].mean(), "lo": lo, "hi": hi})
    return pd.DataFrame(rows)


def rows_for(items):
    """items: [{day, side, strike, entry|None, t}] ← جدول عوائد لكل قاعدة."""
    out = []
    for day, g in pd.DataFrame(items).groupby("day"):
        try:
            bars = mr.opt_bars_1m([mr.occ(day, r.side, r.strike) for r in g.itertuples()], day)
        except Exception as e:  # noqa: BLE001
            print("فشل", day, getattr(e, "code", type(e).__name__))
            continue
        for r in g.itertuples():
            b = bars.get(mr.occ(day, r.side, r.strike))
            if b is None or b.empty:
                continue
            t0 = datetime.fromisoformat(f"{day}T{r.t}:00").replace(tzinfo=mr.NY)
            e = r.entry
            if e != e:
                x = b[b.index >= t0]
                if x.empty or (x.index[0] - t0).total_seconds() > 600:
                    continue
                e = float(x["o"].iloc[0]) * (1 + COST)
            res = {k: simulate(b, t0, e, v) for k, v in RULES.items()}
            if res["tp50"] is not None:
                out.append({"day": day, "side": r.side, "strike": r.strike, "entry": e, **res})
        print(day, len(g), flush=True)
    return pd.DataFrame(out)


def md(title, t):
    L = [f"## {title}", "", "| الطريقة | العدد | المتوسط | الوسيط | نسبة الربح | دون أفضل يوم | الفرق عن الأساس (95%) |", "|---|---|---|---|---|---|---|"]
    for r in t.itertuples():
        ci = "—" if r.key == "tp50" else f"{r.lo:+.1%} إلى {r.hi:+.1%}"
        L.append(f"| {r.name} | {r.n} | {r.mean:+.1%} | {r.median:+.1%} | {r.win:.0%} | {r.no_best:+.1%} | {ci} |")
    return "\n".join(L)


def verdict(tabs):
    """القواعد التي تحقق الشروط المسجلة. tabs: {early, late, sig}."""
    s = tabs["sig"].set_index("key")
    ok = []
    for k in RULES:
        if k == "tp50":
            continue
        beats = all(t.set_index("key").loc[k, "mean"] > t.set_index("key").loc["tp50", "mean"] for t in tabs.values())
        robust = s.loc[k, "no_best"] > s.loc[k, "base_no_best"]
        proven = s.loc[k, "lo"] > 0
        if beats and robust:
            ok.append((k, bool(proven), float(s.loc[k, "no_best"])))
    return sorted(ok, key=lambda x: (x[1], x[2]), reverse=True)


def run():
    rng = np.random.default_rng(7)
    m = pd.read_csv(os.path.join(DATA, "mgt_reverse2_rows.csv"))
    tabs, frames = {}, {}
    for tag in ("early", "late"):
        x = m.dropna(subset=[f"t_{tag}"])
        frames[tag] = rows_for([{"day": r.day, "side": r.side, "strike": r.strike, "entry": r.entry, "t": getattr(r, f"t_{tag}")}
                                for r in x.itertuples()])
    n = pd.read_csv(os.path.join(DATA, "nochase_rows.csv")).dropna(subset=["a_K"])
    frames["sig"] = rows_for([{"day": r.day, "side": r.side, "strike": float(r.a_K), "entry": np.nan, "t": r.time} for r in n.itertuples()])
    tabs = {k: table(v, rng) for k, v in frames.items()}
    v = verdict(tabs)
    days = {k: f.day.nunique() for k, f in frames.items()}
    L = ["# اختبار مراحل رفع الوقف (H-STOPSTAGES)", "",
         f"شموع SPXW بالدقيقة. MGT {days['early']} يوماً، وإشارات البوت {days['sig']} يوماً. الوقف الابتدائي 50%، وكلفة الخروج 1.5%، وداخل الشمعة نفترض الأسوأ.", "",
         md("إشارات البوت (العيّنة الحاكمة، غير منتقاة)", tabs["sig"]), "",
         md("MGT بأبكر وقت دخول", tabs["early"]), "", md("MGT بأبعد وقت دخول", tabs["late"]), "", "## الحكم بالشروط المسجلة", ""]
    if v:
        L += [f"- {RULES[k]['name']}: {'مثبتة (الفاصل فوق الصفر)' if p else 'مرشحة (الفاصل يشمل الصفر)'}، دون أفضل يوم {nb:+.1%}" for k, p, nb in v]
    else:
        L.append("- لا طريقة حققت الشروط؛ يبقى الأساس.")
    open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    run()
