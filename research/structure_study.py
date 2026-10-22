#!/usr/bin/env python3
"""هيكل صفقة SPX: إشارات أقل وأقوى، وعقود وخروج مختلفة — كلها مقابل الاتجاه المعاكس في اللحظة نفسها (بطلب المستخدم 23/10).

يعيد استعمال research/backtest_spx_v2.py (البيانات، نقاط الاتجاه من دالة البوت، التسعير النظري، الفحص عند :05 و:35).
الواقع الحالي على الخادم: الإشارة من شموع ياهو المتأخرة 15د، والدخول والمتابعة والخروج بأسعار لحظية، بلا تأخر تشغيل.
لكل إشارة نشتري الهيكل نفسه في اتجاه البوت وفي الاتجاه المعاكس (مرجع)، ونقيس:
  - متوسط الصفقة (مع التكاليف) وفاصل ثقة بالأيام؛
  - قيمة الاتجاه = البوت − المعاكس (متوسط يومي) — هذا هو السؤال: هل الإشارة تعرف الاتجاه؟
المرشحات: الحد الأدنى للفرق (MIN_EDGE 3 كالبوت، 5، 7)، وأول إشارة في اليوم فقط، ومع اتجاه 20 يوماً.
العقود: خارج المال بقواعد البوت (0DTE)، على السعر 0DTE، على السعر 1DTE، خارج المال 1DTE (~0.30 دلتا).
الخروج: +50/−40 (البوت)، +100/−50، +30/−30، بلا وقف حتى 15:30، خروج بعد 60 دقيقة.
لا حد وقفين يومي (يعتمد على الهيكل)؛ منع تكرار الاتجاه خلال 90د باقٍ. كل الصفقات تُغلق في يومها (15:30)، لا مبيت.
⚠️ أسعار نظرية (بلاك-شولز بتذبذب VIX أمس): متوسط الهيكل نفسه يعكس فرق التذبذب المحقق/الضمني والتكاليف المفترضة،
فالحكم على «قيمة الاتجاه» لا على متوسط الصفقة. اختبارات كثيرة: الاستكشاف قبل 13/10، والحكم على الأيام المحجوزة فقط.
تشغيل: python research/structure_study.py   ← research/structure_study_results.md
"""
import math
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import backtest_spx_v2 as bt   # noqa: E402
ob = bt.ob

OUT = os.path.join(HERE, "structure_study_results.md")
SIG_DELAY = 15                      # الإشارة من شموع ياهو المتأخرة
VARIANT = "base"
FILTERS = {"edge3": "الفرق ≥ 3 (البوت)", "edge5": "الفرق ≥ 5", "edge7": "الفرق ≥ 7",
           "first": "أول إشارة في اليوم (≥ 3)", "trend": "≥ 3 ومع اتجاه 20 يوماً"}
CONTRACTS = {"otm0": "خارج المال 0DTE (البوت)", "atm0": "على السعر 0DTE", "atm1": "على السعر 1DTE", "d30_1": "دلتا ~0.30 1DTE"}
EXITS = {"50_40": (0.50, 0.40, None), "100_50": (1.00, 0.50, None), "30_30": (0.30, 0.30, None),
         "hold": (None, None, None), "t60": (None, None, 60)}
EXIT_AR = {"50_40": "+50/−40 (البوت)", "100_50": "+100/−50", "30_30": "+30/−30", "hold": "بلا وقف حتى 15:30", "t60": "خروج بعد 60د"}


def price_d(S, K, minute, side, iv0, open_px, days):
    """قيمة العقد مع أيام إضافية حتى الانتهاء (days=1 → 1DTE)."""
    left = max(bt.CLOSE_M - minute, 1.0) + 390 * days
    iv = bt.model_iv(S, K, iv0, open_px, VARIANT)
    sd = iv * math.sqrt(left / (252 * 390))
    d1 = (math.log(S / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    return S * bt._cdf(d1) - K * bt._cdf(d2) if side == "CALL" else K * bt._cdf(-d2) - S * bt._cdf(-d1)


def half_spread(p):
    return 0.05 + 0.01 * p           # ~3% لعقد 2.5$، ~1.3% لعقد 15$ (عرض/طلب SPXW النموذجي)


def choose(kind, side, S, minute, iv0, open_px):
    """(السترايك، أيام الانتهاء) أو None."""
    if kind == "otm0":
        pk = bt.pick_contract(side, S, minute, iv0, open_px, VARIANT)
        return None if pk is None else (pk[0], 0)
    if kind in ("atm0", "atm1"):
        return float(round(S / 5) * 5), (0 if kind == "atm0" else 1)
    sg = 1 if side == "CALL" else -1                       # d30_1: أول سترايك دلتاه ≤ 0.30
    T = (bt.CLOSE_M - minute + 390) / (252 * 390)
    iv = bt.model_iv(S, S, iv0, open_px, VARIANT)
    for i in range(1, 80):
        K = float(round(S / 5) * 5 + sg * 5 * i)
        if ob.abs_delta(S, K, T, iv, side) <= 0.30:
            return K, 1
    return None


def follow(g, m, side, K, days, run_m, entry, iv0, open_px, ex):
    tp, sl, tmax = EXITS[ex]
    tau = run_m + 5
    while tau <= bt.FINAL_M:
        S = bt.s_at(g, m, tau, False)
        if S is not None:
            mid = price_d(S, K, tau, side, iv0, open_px, days)
            val = mid - half_spread(mid) - bt.COMMISSION
            pnl = val / entry - 1
            if tmax and tau - run_m >= tmax:
                return "time", pnl
            if sl and tau - run_m >= ob.MIN_AGE_MIN and pnl <= -sl:
                return "stop", pnl
            if tp and pnl >= tp:
                return "tp", pnl
            if tau >= bt.FINAL_M:
                return "final", pnl
        tau += 5
    S = bt.s_at(g, m, bt.FINAL_M, False)
    mid = price_d(S, K, bt.FINAL_M, side, iv0, open_px, days)
    return "final", (mid - half_spread(mid) - bt.COMMISSION) / entry - 1


def signals(data):
    """كل الفحوص التي تجاوزت شروط البوت (الحدث، الحد الأدنى 3، منع التكرار 90د): (يوم، دقيقة، اتجاه، فرق، فوق متوسط 20)."""
    spx = data["spx"]
    dates = sorted(set(spx.index.date))
    out = []
    for di, d in enumerate(dates):
        g, m = bt.day_bars(spx, d)
        if len(g) < 70 or di == 0:
            continue
        last = {}
        for hh in range(9, 16):
            for mm_ in bt.SCAN_MINUTES:
                run_m = hh * 60 + mm_
                if not ((9 * 60 + 45) <= run_m < 15 * 60):
                    continue
                c = bt.build_c(data, d, dates[di - 1], run_m - SIG_DELAY, run_m)
                if c is None or bt.events_block(d, run_m):
                    continue
                bull, bear, _ = ob.direction_points(c)
                edge, side = abs(bull - bear), ("CALL" if bull > bear else "PUT")
                if edge < ob.MIN_EDGE or (side in last and run_m - last[side] < ob.REPEAT_MINUTES):
                    continue
                last[side] = run_m
                out.append({"day": d, "run_m": run_m, "side": side, "edge": edge,
                            "trend": (c["S"] > c["sma20"]) == (side == "CALL")})
    return pd.DataFrame(out)


def keep(sig, f):
    if f == "edge3":
        return sig
    if f in ("edge5", "edge7"):
        return sig[sig["edge"] >= float(f[-1])]
    if f == "first":
        return sig.sort_values("run_m").groupby("day", as_index=False).first()
    return sig[sig["trend"]]


def run(data, sig):
    spx = data["spx"]
    rows = []
    for _, s in sig.iterrows():
        d, run_m = s["day"], int(s["run_m"])
        g, m = bt.day_bars(spx, d)
        vprev = data["vixd"][data["vixd"].index < pd.Timestamp(d)]
        iv0 = float(vprev.iloc[-1]) / 100 if len(vprev) else 0.16
        open_px = float(g["Open"].values[0])
        S = bt.s_at(g, m, run_m, False)                    # سعر لحظي عند الدخول (الوضع 1)
        if S is None:
            continue
        for kind in CONTRACTS:
            for role, side in (("bot", s["side"]), ("opp", "PUT" if s["side"] == "CALL" else "CALL")):
                ch = choose(kind, side, S, run_m, iv0, open_px)
                if ch is None:
                    continue
                K, days = ch
                mid = price_d(S, K, run_m, side, iv0, open_px, days)
                entry = mid + half_spread(mid) + bt.COMMISSION
                for ex in EXITS:
                    out, pnl = follow(g, m, side, K, days, run_m, entry, iv0, open_px, ex)
                    rows.append({"day": d, "run_m": run_m, "edge": s["edge"], "trend": s["trend"], "kind": kind,
                                 "exit": ex, "role": role, "out": out, "pnl": pnl, "entry": entry})
    return pd.DataFrame(rows)


def stats(tr):
    bot, opp = tr[tr["role"] == "bot"], tr[tr["role"] == "opp"]
    if bot.empty:
        return None
    lo, hi = bt.boot_mean(bot)
    pdf = bt.paired_diff(bot, opp)
    return {"n": len(bot), "days": bot["day"].nunique(), "mean": bot["pnl"].mean(), "lo": lo, "hi": hi,
            "usd": (bot["pnl"] * bot["entry"] * 100).mean(), "win": (bot["pnl"] > 0).mean(),
            "opp": opp["pnl"].mean() if len(opp) else float("nan"),
            "dv": pdf[0] if pdf else float("nan"), "dlo": pdf[1] if pdf else float("nan"), "dhi": pdf[2] if pdf else float("nan")}


def main():
    data = bt.load()
    sig = signals(data)
    trades = run(data, sig)
    days = sorted(set(data["spx"].index.date))
    combos = []
    for f in FILTERS:
        fs = keep(sig, f)
        key = set(zip(fs["day"], fs["run_m"]))
        tf = trades[[k in key for k in zip(trades["day"], trades["run_m"])]]
        for kind in CONTRACTS:
            for ex in EXITS:
                t = tf[(tf["kind"] == kind) & (tf["exit"] == ex)]
                e = stats(t[t["day"] < bt.HOLDOUT_FROM])
                h = stats(t[t["day"] >= bt.HOLDOUT_FROM])
                if e:
                    combos.append((f, kind, ex, e, h))
    combos.sort(key=lambda x: -x[3]["dv"])
    pct = lambda v: "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{100 * v:+.1f}%"
    L = ["# هيكل صفقة SPX: إشارات أقوى، عقود وخروج مختلفة (نتائج آلية)", "",
         f"- الفترة: {days[0]} → {days[-1]}. الاستكشاف قبل {bt.HOLDOUT_FROM}، والمحجوز بعده (للحكم فقط).",
         f"- الإشارة من دالة البوت على شموع متأخرة {SIG_DELAY}د؛ الدخول والمتابعة بالسعر اللحظي؛ تكلفة: نصف فرق 0.05$+1% من السعر + عمولة.",
         f"- إشارات (بعد شروط البوت): {len(sig)} في {sig['day'].nunique()} يوماً. تركيبات مختبرة: {len(combos)} "
         f"(المتوقع بالصدفة أن ~{max(1, round(0.05 * len(combos)))} منها تبدو «دالة» عند 5%).",
         "- **قيمة الاتجاه** = متوسط يومي (البوت − المعاكس بالهيكل نفسه). الحكم عليها لا على متوسط الصفقة (التسعير نظري).", "",
         "## الاستكشاف: كل التركيبات مرتبة بقيمة الاتجاه", "",
         "| المرشح | العقد | الخروج | صفقات | أيام | متوسط البوت | بالدولار | رابحة | متوسط المعاكس | قيمة الاتجاه (فاصل 95%) |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for f, kind, ex, e, _ in combos:
        L.append(f"| {FILTERS[f]} | {CONTRACTS[kind]} | {EXIT_AR[ex]} | {e['n']} | {e['days']} | {pct(e['mean'])} | "
                 f"≈{e['usd']:+.0f}$ | {100 * e['win']:.0f}% | {pct(e['opp'])} | {pct(e['dv'])} ({pct(e['dlo'])} … {pct(e['dhi'])}) |")
    L += ["", "## المحجوز: أفضل 5 تركيبات من الاستكشاف + هيكل البوت الحالي", "",
          "| المرشح | العقد | الخروج | صفقات | أيام | متوسط البوت | متوسط المعاكس | قيمة الاتجاه (فاصل 95%) |", "|---|---|---|---|---|---|---|---|"]
    pick = combos[:5] + [c for c in combos if c[:3] == ("edge3", "otm0", "50_40")]
    for f, kind, ex, _, h in pick:
        if h:
            L.append(f"| {FILTERS[f]} | {CONTRACTS[kind]} | {EXIT_AR[ex]} | {h['n']} | {h['days']} | {pct(h['mean'])} | "
                     f"{pct(h['opp'])} | {pct(h['dv'])} ({pct(h['dlo'])} … {pct(h['dhi'])}) |")
    sig_e = sum(1 for c in combos if c[3]["dlo"] > 0)
    L += ["", f"تركيبات قيمة اتجاهها موجبة بثقة في الاستكشاف: **{sig_e}** من {len(combos)}.",
          "", "## كيف تقرأ النتيجة",
          "- تركيبة تستحق الظل فقط إن كانت قيمة اتجاهها موجبة بثقة في الاستكشاف **وبقيت موجبة في المحجوز**.",
          "- المحجوز 8 أيام فقط: يكبر أسبوعياً؛ لا اعتماد قبل 20 يوماً محجوزاً.",
          "- متوسط الصفقة نفسه نظري: الأرقام بالدولار للمقارنة بين الهياكل لا للتوقع."]
    open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
