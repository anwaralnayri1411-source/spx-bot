#!/usr/bin/env python3
"""H-CONFIRM (تاريخياً): هل تستمر حركة أول ساعة أكثر حين يؤكدها الحجم واتساع القطاعات؟
(مسجّل مسبقاً في CLAUDE.md 4/11/2026؛ C1 لـSPY وC2 لكل السوق). اختبار فقط. يستعمل دوال confirm_shadow الصافية نفسها.

البيانات: SPXBOT_DATA/spy_5m.csv.gz وbars_5m/<رمز>.csv.gz (SIP، ساعات السوق)، 2019-01 → 2026-10-23.
C1: SPY. C2: QQQ وIWM وDIA و33 شركة. لكل منهما: متوسط (10:30 → الإغلاق بإشارة الحركة) لـ«الحجم والاتساع معاً»
− «بلا تأكيد» > 0 بفاصل 95% (إعادة معاينة الأيام) يستثني الصفر، وموجب في النصفين الزمنيين.
تشغيل (الخادم): SPXBOT_DATA=/opt/spxbot/data python research/confirm_study.py   المخرج: research/confirm_study_results.md
"""
import os
import sys
from datetime import time as dtime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import confirm_shadow as cs  # noqa: E402

DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
OUT = os.path.join(HERE, "confirm_study_results.md")
END = pd.Timestamp("2026-10-24")
N_BOOT = 3000


def read_bars(sym):
    from tournament import read_bars as rb
    p = os.path.join(DATA, "spy_5m.csv.gz") if sym == "SPY" else os.path.join(DATA, "bars_5m", f"{sym}.csv.gz")
    if not os.path.exists(p):
        return None
    d = rb(p)
    return d[d.index < END]


def daily_points(d5):
    """لكل يوم: إغلاق أمس، إغلاق شمعة 10:25، إغلاق اليوم والغد."""
    at = d5[d5.index.time == dtime(10, 25)]["Close"]
    at.index = at.index.date
    g = d5.groupby(d5.index.date)["Close"].last()
    t = pd.DataFrame({"close": g, "p1030": at}).dropna()
    t["prev"] = g.shift(1).reindex(t.index)
    t["next"] = g.shift(-1).reindex(t.index)
    return t.dropna(subset=["prev"])


def instrument_rows(sym, kind, sec, secmap):
    d5 = read_bars(sym)
    if d5 is None:
        return []
    fh = cs.first_hour_volume(d5)
    pts = daily_points(d5)
    own = sec.get(secmap.get(sym)) if kind == "stock" else None
    rows = []
    for day, r in pts.iterrows():
        move = r["p1030"] / r["prev"] - 1
        side = "CALL" if move > 0 else "PUT"
        moves = {s: (t.at[day, "p1030"] / t.at[day, "prev"] - 1) if day in t.index else None
                 for s, t in sec.items() if s in cs.SECTORS}
        b, nb = cs.breadth(moves, side)
        c = cs.classify(move, cs.vol_ratio(fh, day), b, nb, kind)
        sok = None
        if own is not None and day in own.index:
            om = own.at[day, "p1030"] / own.at[day, "prev"] - 1
            sok = int(om * (1 if side == "CALL" else -1) > 0)
        oc, on = cs.outcome(side, r["p1030"], r["close"], r["next"] if not np.isnan(r["next"]) else None)
        rows.append({"sym": sym, "kind": kind, "day": str(day), **c, "sector_ok": sok, "out_close": oc, "out_next": on})
    return rows


def build():
    secmap = cs.sector_map()
    need = sorted(set(cs.SECTORS) | set(secmap.values()))
    sec = {}
    for s in need:
        d = read_bars(s)
        if d is not None:
            sec[s] = daily_points(d)
    rows = []
    syms = list(cs.INDEXES) + sorted(secmap)
    for s in syms:
        r = instrument_rows(s, "index" if s in cs.INDEXES else "stock", sec, secmap)
        rows += r
        print(s, len(r), flush=True)
    return pd.DataFrame(rows), sorted(sec)


def boot_diff(a, b, col, n=N_BOOT, seed=0):
    u = np.unique(np.r_[a["day"].values, b["day"].values])
    ba = {d: g[col].values for d, g in a.groupby("day")}
    bb = {d: g[col].values for d, g in b.groupby("day")}
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        s = rng.choice(u, len(u))
        xa = np.concatenate([ba.get(d, np.array([])) for d in s])
        xb = np.concatenate([bb.get(d, np.array([])) for d in s])
        if len(xa) and len(xb):
            out.append(xa.mean() - xb.mean())
    return float(a[col].mean() - b[col].mean()), tuple(np.percentile(out, [2.5, 97.5]))


def hypothesis(m, name):
    a, b = m[m["group"] == "both"], m[m["group"] == "none"]
    d, (lo, hi) = boot_diff(a, b, "out_close")
    days = sorted(m["day"].unique())
    mid = days[len(days) // 2]
    halves = [x[x["group"] == "both"]["out_close"].mean() - x[x["group"] == "none"]["out_close"].mean()
              for x in (m[m["day"] < mid], m[m["day"] >= mid])]
    ok = lo > 0 and min(halves) > 0
    return [f"- **{name}:** الفرق حتى الإغلاق (الاثنان معاً − بلا تأكيد) {d * 100:+.3f} نقطة، فاصل 95% [{lo * 100:+.3f}، {hi * 100:+.3f}]؛ "
            f"النصفان {halves[0] * 100:+.3f} / {halves[1] * 100:+.3f} → **{'تحققت' if ok else 'لم تتحقق'}**"]


def table(m, title):
    L = [f"### {title}", "", "| المجموعة | قراءات | حتى الإغلاق | استمرت | حتى إغلاق الغد |", "|---|---|---|---|---|"]
    for g, name in cs.GROUP_AR.items():
        x = m[m["group"] == g]
        if len(x):
            L.append(f"| {name} | {len(x)} | {x['out_close'].mean() * 100:+.3f}% | {(x['out_close'] > 0).mean() * 100:.0f}% | "
                     f"{x['out_next'].mean() * 100:+.3f}% |")
    return L + [""]


def report(df, secs):
    full = df[df["move_day"] & (df["n_sectors"] >= len(cs.SECTORS) - 1)]
    spy, rest = full[full["sym"] == "SPY"], full[full["sym"] != "SPY"]
    L = ["# H-CONFIRM: تأكيد حركة أول ساعة بالحجم واتساع القطاعات — كل السوق (نتائج آلية)", "",
         f"- {df['sym'].nunique()} أداة (المؤشرات SPY وQQQ وIWM وDIA و{(df['kind'] == 'stock').groupby(df['sym']).any().sum()} شركة)، "
         f"صناديق {len(secs)}، شموع 5د SIP، {df['day'].min()} → {df['day'].max()}.",
         f"- يوم حركة: ≥{cs.MOVE_MIN['index'] * 100:.1f}% للمؤشرات و≥{cs.MOVE_MIN['stock'] * 100:.1f}% للشركات حتى 10:30. "
         f"الحجم: أول ساعة للأداة ≥ {cs.VOL_X}× متوسطها لـ20 جلسة. الاتساع: ≥ {cs.BREADTH_MIN} من 11 قطاعاً باتجاه الأداة. "
         "النتيجة بإشارة الحركة (موجب = استمرت).", ""]
    L += table(spy, "SPY (C1)")
    L += table(rest[rest["kind"] == "index"], "QQQ وIWM وDIA")
    L += table(rest[rest["kind"] == "stock"], "الشركات")
    s = rest[(rest["kind"] == "stock") & rest["sector_ok"].notna()]
    L += ["### الشركات حسب صندوق قطاعها (وصفي)", "", "| قطاع الشركة | قراءات | حتى الإغلاق | استمرت |", "|---|---|---|---|"]
    for v, name in ((1, "معها"), (0, "ضدها")):
        x = s[s["sector_ok"] == v]
        if len(x):
            L.append(f"| {name} | {len(x)} | {x['out_close'].mean() * 100:+.3f}% | {(x['out_close'] > 0).mean() * 100:.0f}% |")
    L += ["", "## الفرضيات", ""] + hypothesis(spy, "C1 (SPY)") + hypothesis(rest, "C2 (QQQ وIWM وDIA والشركات)")
    L += ["", "وصفي لـC2 منفصلاً:"] + hypothesis(rest[rest["kind"] == "index"], "المؤشرات الثلاثة") + \
         hypothesis(rest[rest["kind"] == "stock"], "الشركات")
    L += ["", "تحفظ: SPY بدل ES؛ الحجم يرتفع آلياً في أيام الحركة الكبيرة؛ حركة الأداة ليست ربح العقد."]
    return "\n".join(L) + "\n"


def main():
    df, secs = build()
    md = report(df, secs)
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main()
