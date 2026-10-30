#!/usr/bin/env python3
"""H-CALM: الكوندور الحديدي على SPY في الأيام الهادئة فقط (أسعار Alpaca الحقيقية؛ اختبار فقط، لا يغيّر أي بوت). بطلب المستخدم 30/10/2026.

الأصل: H-SELL (بيع الأقساط كل يوم) كان قرب التعادل: −0.023R. الفكرة: السوق يدفع للبائع أكثر حين يكون الخوف مسعّراً (VIX ليس منخفضاً)
والحركة الفعلية هادئة (مدى أمس ضيق). الصفقة نفسها حرفياً من premium_sell_study: كل يوم 10:30 كوندور SPY لليوم التالي، البيع على 0.8 ×
الحركة الضمنية وجناحان بعرض 3$، حتى الانتهاء بالقيمة الجوهرية. التنفيذ الأساسي: أمر محدد (نصف فرق 1.5%)، والسوقي (3%) وصفي.
الفرضية (مسجّلة هنا وفي CLAUDE.md قبل التشغيل)، بفئات H-CONTEXT نفسها المحددة مسبقاً:
  اليوم الهادئ = VIX إغلاق أمس ≥ 15 (متوسط أو مرتفع) **و** مدى SPY أمس ÷ متوسط مداه 20 يوماً قبله < 0.8 (ضيق).
  (1) متوسط R للكوندور في الأيام الهادئة > 0 بفاصل 95% (إعادة معاينة الأيام). (2) وصفي: الأيام الأخرى، وكل شرط وحده.
  تقويم الأحداث (يوم حدث) غير متاح تاريخياً فلا يدخل في التعريف.
تحفظات: الأيام الهادئة قليلة (قد تكون عشرات فقط)؛ الكوندور يربح قليلاً كثيراً ويخسر كثيراً نادراً، فالمتوسط حساس ليوم واحد سيئ.
تشغيل (الخادم، ALPACA وملف SPY 5د): python research/condor_calm_test.py   المخرج: research/condor_calm_test_results.md
"""
import os
import sys
import time as _time
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import premium_sell_study as ps  # noqa: E402

OUT = os.path.join(HERE, "condor_calm_test_results.md")
VIX_MIN, NARROW = 15.0, 0.8
PRIMARY_H, MARKET_H = 0.015, 0.03


def condor_rows(df, h):
    """R الكوندور الأساسي (base) لكل يوم بنصف فرق h: DataFrame(day, val)."""
    sub = df[(df["kind"] == "base") & df["p_short"].notna() & df["p_long"].notna()]
    out = []
    for day, g in sub.groupby("day"):
        if set(g["side"]) != {"put", "call"}:
            continue
        p, c = g[g["side"] == "put"].iloc[0], g[g["side"] == "call"].iloc[0]
        cp, cc = ps.credit_of(float(p["p_short"]), float(p["p_long"]), h), ps.credit_of(float(c["p_short"]), float(c["p_long"]), h)
        if cp is None or cc is None:
            continue
        r = ps.condor_r(cp, cc, ps.spread_value("put", p["short"], p["settle"]), ps.spread_value("call", c["short"], c["settle"]))
        if r is not None:
            out.append({"day": day, "val": r})
    return pd.DataFrame(out, columns=["day", "val"])


def day_context(d, vix):
    """{يوم: (vix_prev, rel_range_prev)}. d: شموع SPY 5د (t,o,h,l,c)؛ vix: إغلاق أمس بفهرس اليوم."""
    g = d.groupby(d["t"].dt.date)
    rng = (g["h"].max() - g["l"].min()) / g["c"].last()
    rel = rng / rng.shift(1).rolling(20).mean()
    out = {}
    days = list(rng.index)
    for i, day in enumerate(days):
        if i == 0:
            continue
        prev_rel = rel.iloc[i - 1]
        v = vix.get(pd.Timestamp(day))
        out[pd.Timestamp(day)] = (None if v is None or not np.isfinite(v) else float(v), None if not np.isfinite(prev_rel) else float(prev_rel))
    return out


def classify(ctx):
    v, r = ctx
    if v is None or r is None:
        return None
    return {"vix_ok": v >= VIX_MIN, "narrow": r < NARROW, "calm": v >= VIX_MIN and r < NARROW}


def stats(rows):
    if len(rows) < 10:
        return None
    days = sorted(set(rows["day"]))
    vals = rows["val"].values
    idx = np.random.default_rng(53).integers(0, len(vals), (3000, len(vals)))
    m = vals[idx].mean(1)
    win = float((vals > 0).mean())
    return {"n": len(vals), "days": len(days), "mean": float(vals.mean()), "lo": float(np.percentile(m, 2.5)), "hi": float(np.percentile(m, 97.5)),
            "win": win, "worst": float(vals.min()), "dd": ps.drawdown(vals)}


def report(rows_p, rows_m, ctx, meta):
    L = ["# H-CALM: الكوندور الحديدي في الأيام الهادئة فقط (SPY، أسعار Alpaca الحقيقية)", "", f"- {meta}",
         f"- اليوم الهادئ: VIX أمس ≥ {VIX_MIN:g} ومدى أمس < {NARROW} × متوسط 20 يوماً. R = الربح ÷ أقصى خسارة. التنفيذ الأساسي محدد (1.5%).", "",
         "| المجموعة | التنفيذ | أيام | متوسط R | فاصل 95% | رابحة | أسوأ يوم | أعمق تراجع (R) |", "|---|---|---|---|---|---|---|---|"]
    verdict = None
    for exec_name, rows in (("محدد 1.5%", rows_p), ("سوقي 3%", rows_m)):
        tag = rows["day"].map(lambda d: classify(ctx.get(d, (None, None))))
        groups = {"الأيام الهادئة": tag.map(lambda t: bool(t and t["calm"])), "الأيام الأخرى": tag.map(lambda t: bool(t and not t["calm"])),
                  "VIX ≥ 15 فقط": tag.map(lambda t: bool(t and t["vix_ok"])), "مدى أمس ضيق فقط": tag.map(lambda t: bool(t and t["narrow"])),
                  "كل الأيام": tag.map(lambda t: t is not None)}
        for gname, mask in groups.items():
            s = stats(rows[mask.values])
            if s is None:
                L.append(f"| {gname} | {exec_name} | {int(mask.sum())} | - | - | - | - | - |")
                continue
            L.append(f"| {gname} | {exec_name} | {s['n']} | {s['mean']:+.3f} | {s['lo']:+.3f}…{s['hi']:+.3f} | {100 * s['win']:.0f}% | {s['worst']:+.2f} | {s['dd']:.1f} |")
            if exec_name == "محدد 1.5%" and gname == "الأيام الهادئة":
                verdict = "تحققت" if s["lo"] > 0 else "عكسية" if s["hi"] < 0 else "لم تتحقق"
                vtxt = f"متوسط {s['mean']:+.3f}R بفاصل {s['lo']:+.3f}…{s['hi']:+.3f} على {s['n']} يوماً"
    L += ["", f"⚖️ H-CALM: {verdict or 'لا حكم (أيام قليلة)'}" + (f" ({vtxt})" if verdict else "")]
    return L


def main(argv):
    import option_buyer_cost as obc
    import options_bot as ob
    import stocks_contract_replay as scr
    if not os.path.exists(ps.ls.DATA) or not os.getenv("ALPACA_KEY"):
        print("يلزم ملف SPY 5 دقائق ومفاتيح Alpaca (الخادم).")
        return 0
    d = ps.ls.load(ps.ls.DATA)
    vix = obc.load_vix()
    plans = ps.build_plans(d, vix, ob.HOLIDAYS)
    plans = [{**p, "strikes": {k: v for k, v in p["strikes"].items() if k[0] == "base"}} for p in plans]
    bars = {}
    for k in range(0, len(plans), ps.DAYS_PER_CALL):
        chunk = plans[k:k + ps.DAYS_PER_CALL]
        syms = [s for p in chunk for s in ps.plan_symbols(p)]
        start = (chunk[0]["day"].tz_localize("America/New_York") + pd.Timedelta(hours=9)).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
        end = (chunk[-1]["day"].tz_localize("America/New_York") + pd.Timedelta(hours=17)).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            bars.update(scr.fetch_bars(syms, start, end))
        except Exception as e:  # noqa: BLE001
            print("تعذر جلب دفعة:", type(e).__name__)
        _time.sleep(0.2)
    df = ps.evaluate(plans, bars)
    ctx = day_context(d, vix)
    L = report(condor_rows(df, PRIMARY_H), condor_rows(df, MARKET_H), ctx, f"{len(plans)} يوماً من {ps.START}")
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
