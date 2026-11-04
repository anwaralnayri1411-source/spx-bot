#!/usr/bin/env python3
"""H-CTXSIG: هل تعمل إشارات بوت الشركات أفضل حين توافق سياق السوق؟ (مسجّل مسبقاً في CLAUDE.md 4/11/2026). اختبار فقط.

الإشارات: نقاط البوت التاريخية (src=bot في SPXBOT_DATA/research/chart_levels_trades.csv، دخول 10:30)، النتيجة r5 بإشارة الصفقة.
السياق عند 10:30 (حركة منذ إغلاق أمس): E1 الاتساع، E2 القيادة (هجومي − دفاعي)، E3 العائد (TLT)، E4 محرك الشركة
(IBIT/USO)، E5 الذهب (GLD)، E6 قطاع الشركة؛ وصفي: حجم SPY في أول ساعة، والرقائق مع الذهب. E7 مركّب.
لكل عنصر: موافق − مخالف > 0 بفاصل 95% (إعادة معاينة الأيام) وموجب في النصفين، وتصحيح BH بمعدل 5% على E1–E6.
تشغيل (الخادم): SPXBOT_DATA=/opt/spxbot/data python research/context_signals_study.py   المخرج: research/context_signals_results.md
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import confirm_shadow as cs  # noqa: E402
from confirm_study import daily_points, read_bars  # noqa: E402

DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
OUT = os.path.join(HERE, "context_signals_results.md")
SIGNALS = os.path.join(DATA, "research", "chart_levels_trades.csv")
RISK_ON, DEFENSIVE = ("XLK", "XLY", "XLC"), ("XLU", "XLP", "XLV")
DRIVER = {"COIN": "IBIT", "MSTR": "IBIT", "HOOD": "IBIT", "SOFI": "IBIT", "TSLA": "IBIT", "XOM": "USO", "CVX": "USO"}
LEAD_TH, TLT_TH, DRV_TH, GLD_TH = 0.003, 0.002, 0.005, 0.003
ELEMENTS = {"E1": "اتساع القطاعات", "E2": "القيادة (هجومي/دفاعي)", "E3": "العائد (TLT)", "E4": "محرك الشركة (بتكوين/نفط)",
            "E5": "الذهب", "E6": "قطاع الشركة"}
LAB = {1: "موافق", -1: "مخالف", 0: "محايد"}
N_BOOT = 3000


def mv(pts, sym, day):
    t = pts.get(sym)
    if t is None or day not in t.index:
        return None
    return float(t.at[day, "p1030"] / t.at[day, "prev"] - 1)


def sgn_th(x, th):
    return 0 if x is None or abs(x) < th else (1 if x > 0 else -1)


def context(row, pts, vol, secmap):
    """عناصر السياق لصفقة: +1 موافق، −1 مخالف، 0 محايد (None = لا بيانات)."""
    day, s = row["dayd"], (1 if row["side"] == "CALL" else -1)
    moves = {k: mv(pts, k, day) for k in cs.SECTORS}
    b, nb = cs.breadth(moves, row["side"])
    e = {"E1": None if nb < len(cs.SECTORS) - 1 else (1 if b >= cs.BREADTH_MIN else (-1 if b <= 3 else 0))}
    ro, de = [moves.get(k) for k in RISK_ON], [moves.get(k) for k in DEFENSIVE]
    if None in ro or None in de:
        e["E2"] = None
    else:
        e["E2"] = sgn_th(np.mean(ro) - np.mean(de), LEAD_TH) * s
    t = mv(pts, "TLT", day)
    e["E3"] = None if t is None else sgn_th(t, TLT_TH) * s
    drv = DRIVER.get(row["sym"])
    dm = mv(pts, drv, day) if drv else None
    e["E4"] = None if dm is None else sgn_th(dm, DRV_TH) * s
    g = mv(pts, "GLD", day)
    e["E5"] = None if g is None else -sgn_th(g, GLD_TH) * s
    om = mv(pts, secmap.get(row["sym"]), day) if secmap.get(row["sym"]) else None
    e["E6"] = None if om is None else (1 if om * s > 0 else -1)
    vr = cs.vol_ratio(vol, day)
    e["vol"] = None if vr is None else ("نشط" if vr >= cs.VOL_X else ("هادئ" if vr <= 0.8 else "عادي"))
    e["gold_up"] = None if g is None else int(g >= GLD_TH)
    known = [v for k, v in e.items() if k in ELEMENTS and v is not None]
    e["E7"] = int(np.sum(known)) if known else None
    return e


def boot_diff(a, b, n=N_BOOT, seed=0):
    """(فرق المتوسطين، فاصل 95%، p أحادي ≈ نسبة الفروق ≤ 0) بإعادة معاينة الأيام."""
    u = np.unique(np.r_[a["day"].values, b["day"].values])
    ba = {d: g["r5"].values for d, g in a.groupby("day")}
    bb = {d: g["r5"].values for d, g in b.groupby("day")}
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        s = rng.choice(u, len(u))
        xa = np.concatenate([ba.get(d, np.array([])) for d in s])
        xb = np.concatenate([bb.get(d, np.array([])) for d in s])
        if len(xa) and len(xb):
            out.append(xa.mean() - xb.mean())
    out = np.array(out)
    return float(a["r5"].mean() - b["r5"].mean()), tuple(np.percentile(out, [2.5, 97.5])), float((out <= 0).mean())


def bh(ps, q=0.05):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    k = 0
    for r, i in enumerate(order, 1):
        if ps[i] <= q * r / len(ps):
            k = r
    ok = [False] * len(ps)
    for r, i in enumerate(order, 1):
        ok[i] = r <= k
    return ok


def build():
    sig = pd.read_csv(SIGNALS)
    sig = sig[sig["src"] == "bot"].copy()
    sig["dayd"] = pd.to_datetime(sig["day"]).dt.date
    secmap = cs.sector_map()
    need = set(cs.SECTORS) | set(RISK_ON) | set(DEFENSIVE) | {"TLT", "GLD", "USO", "IBIT"} | set(secmap.values())
    pts = {}
    for s in sorted(need):
        d = read_bars(s)
        if d is not None:
            pts[s] = daily_points(d)
    vol = cs.first_hour_volume(read_bars("SPY"))
    rows = []
    for r in sig.to_dict("records"):
        rows.append({**{k: r[k] for k in ("sym", "day", "side", "r1", "r5")}, **context(r, pts, vol, secmap)})
    return pd.DataFrame(rows), sorted(pts)


def report(df, have):
    L = ["# H-CTXSIG: إشارات بوت الشركات مقابل سياق السوق (نتائج آلية)", "",
         f"- {len(df)} إشارة من نقاط البوت (دخول 10:30)، {df['sym'].nunique()} شركة، {df['day'].min()} → {df['day'].max()}. "
         f"الصناديق المتاحة: {' '.join(have)}.",
         "- النتيجة: حركة السهم بإشارة الصفقة حتى إغلاق +5 أيام (والغد). موافق = السياق في اتجاه الصفقة.", "",
         "| العنصر | موافق | مخالف | محايد | فرق (موافق − مخالف) | فاصل 95% | النصفان |", "|---|---|---|---|---|---|---|"]
    days = sorted(df["day"].unique())
    mid = days[len(days) // 2]
    ps, extra, lines = [], [], []
    for k, name in ELEMENTS.items():
        x = df[df[k].notna()]
        a, b = x[x[k] == 1], x[x[k] == -1]
        if len(a) < 30 or len(b) < 30:
            ps.append(1.0)
            extra.append(False)
            L.append(f"| {name} | {len(a)} | {len(b)} | {(x[k] == 0).sum()} | عينة صغيرة | - | - |")
            continue
        d, (lo, hi), p = boot_diff(a, b)
        h = [(y[y[k] == 1]["r5"].mean() - y[y[k] == -1]["r5"].mean()) for y in (x[x["day"] < mid], x[x["day"] >= mid])]
        ps.append(p)
        extra.append(lo > 0 and min(h) > 0)
        cell = lambda g: f"{len(g)} ({g['r5'].mean() * 100:+.2f}%)"
        L.append(f"| {name} | {cell(a)} | {cell(b)} | {cell(x[x[k] == 0])} | **{d * 100:+.2f}** | [{lo * 100:+.2f}، {hi * 100:+.2f}] | "
                 f"{h[0] * 100:+.2f} / {h[1] * 100:+.2f} |")
    ok = bh(ps)
    L += ["", "## الحكم (تصحيح BH بمعدل 5% على E1–E6)", ""]
    for (k, name), o, e in zip(ELEMENTS.items(), ok, extra):
        L.append(f"- **{k} {name}:** {'تحققت ✅' if (o and e) else 'لم تتحقق'}")
    x = df[df["E7"].notna()]
    q1, q2 = x["E7"].quantile(1 / 3), x["E7"].quantile(2 / 3)
    top, bot = x[x["E7"] > q2], x[x["E7"] < q1]
    if len(top) >= 30 and len(bot) >= 30:
        d, (lo, hi), _ = boot_diff(top, bot)
        h = [(y[y["E7"] > q2]["r5"].mean() - y[y["E7"] < q1]["r5"].mean()) for y in (x[x["day"] < mid], x[x["day"] >= mid])]
        L += ["", f"- **E7 المركّب** (الثلث الأعلى {len(top)} − الأدنى {len(bot)}): {d * 100:+.2f} نقطة [{lo * 100:+.2f}، {hi * 100:+.2f}]، "
                  f"النصفان {h[0] * 100:+.2f} / {h[1] * 100:+.2f} → **{'تحققت ✅' if lo > 0 and min(h) > 0 else 'لم تتحقق'}**"]
    L += ["", "## وصفي", "", "| المجموعة | إشارات | +5 أيام | الغد | رابحة (+5) |", "|---|---|---|---|---|"]
    row = lambda name, g: L.append(f"| {name} | {len(g)} | {g['r5'].mean() * 100:+.2f}% | {g['r1'].mean() * 100:+.2f}% | {(g['r5'] > 0).mean() * 100:.0f}% |")
    row("كل الإشارات", df)
    for v in ("نشط", "عادي", "هادئ"):
        row(f"حجم SPY أول ساعة: {v}", df[df["vol"] == v])
    for side in ("CALL", "PUT"):
        row(f"{side}", df[df["side"] == side])
    chips = df[df["sym"].isin(["NVDA", "AMD", "AVGO", "MU", "INTC"]) & df["gold_up"].notna()]
    for gu, name in ((1, "الرقائق والذهب صاعد"), (0, "الرقائق والذهب غير صاعد")):
        g = chips[chips["gold_up"] == gu]
        if len(g):
            row(f"{name} (CALL)", g[g["side"] == "CALL"])
    for sym in ("COIN", "MSTR", "HOOD", "XOM", "CVX"):
        g = df[(df["sym"] == sym) & df["E4"].notna()]
        for v in (1, -1):
            gg = g[g["E4"] == v]
            if len(gg) >= 5:
                row(f"{sym}: المحرك {LAB[v]}", gg)
    L += ["", "تحفظ: إشارات نقاط البوت بلا أخبار وتدفق (ضابط H-CHART)؛ حركة السهم ليست ربح العقد؛ IBIT منذ 2024 فقط؛ TLT بديل للعائد."]
    return "\n".join(L) + "\n"


def main():
    df, have = build()
    md = report(df, have)
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main()
