#!/usr/bin/env python3
"""هل يتغير نجاح الزخم باختلاف نظام التضخم؟ (قراءة فقط، استكشافية؛ فرضية H-REGIME مسجّلة مسبقاً في CLAUDE.md 24/10/2026 بطلب المستخدم).

الفرضية: مهارة زخم آخر ساعة (عائد عقد ATM ناقص متوسط الكول والبوت، 1DTE و0DTE، الكلفة الحقيقية المعايَرة) تختلف بين تضخم CPI السنوي ≥3% و<3%،
الفرق ≠ 0 بفاصل 95% يستثني الصفر بعناقيد شهرية. التضخم = القيمة المعروفة وقت الصفقة فقط: CPI شهر m يُنشر نحو منتصف m+1، فنعتمد توفره بعد 45 يوماً من أول الشهر (لا نظرة للمستقبل).
البيانات: CPIAUCSL من FRED (CSV عام). تحفظ مسبق: النظم المستقلة قليلة (تضخم مرتفع 2021–2023 ثم قرب 3% بعدها)، فهذا وصف وليس إثباتاً. لا يُبنى قرار قبل أن يتكرر الفرق في الظل الحي.
تشغيل: python research/regime_study.py [مسار الشموع]   المخرج: research/regime_study_results.md
"""
import io
import os
import sys
import urllib.request
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import levels_study as ls  # noqa: E402
import option_buyer_cost as obc  # noqa: E402
import strategy_sim as sim  # noqa: E402
import strategy_walkforward as wf  # noqa: E402

OUT = os.path.join(HERE, "regime_study_results.md")
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL"
THRESH = 0.03
PUBLISH_LAG_DAYS = 45
MIN_PER_REGIME = 300


# ============================ دوال نقية ============================
def parse_cpi(text):
    """Series شهرية (فهرس أول الشهر) من CSV لـFRED. تتجاهل القيم الناقصة."""
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip() for c in df.columns]
    date_col, val_col = df.columns[0], df.columns[1]
    s = pd.Series(pd.to_numeric(df[val_col], errors="coerce").to_numpy(), index=pd.to_datetime(df[date_col])).dropna()
    return s.sort_index()


def known_yoy(cpi, days):
    """التضخم السنوي المعروف وقت كل يوم: آخر شهر نُشر (بعد PUBLISH_LAG_DAYS من أوله) مقسوماً على قبله بـ12 شهراً. Series بفهرس days (NaN قبل توفر البيانات)."""
    yoy = cpi.pct_change(12).dropna()
    avail = yoy.copy()
    avail.index = avail.index + pd.Timedelta(days=PUBLISH_LAG_DAYS)
    u = pd.DatetimeIndex(pd.to_datetime(days))                     # قد يتكرر اليوم (عدة صفقات في اليوم)
    filled = avail.reindex(avail.index.union(u.unique())).ffill()
    return filled.reindex(u)


def cluster_diff(df, flag_col="high", n=3000, seed=31):
    """فرق متوسط المهارة (مرتفع − منخفض) وفاصله بإعادة معاينة الأشهر. df: day, skill, high(bool). None إن قلّت العينة."""
    d = df.copy()
    d["m"] = pd.to_datetime(d["day"]).dt.to_period("M").astype(str)
    g = d.groupby(["m", flag_col])["skill"].agg(["sum", "count"]).unstack(flag_col).fillna(0.0)
    months = g.index
    if len(months) < 12 or (True not in g["count"].columns) or (False not in g["count"].columns):
        return None
    Sh, Nh = g[("sum", True)].to_numpy(), g[("count", True)].to_numpy()
    Sl, Nl = g[("sum", False)].to_numpy(), g[("count", False)].to_numpy()
    if Nh.sum() < MIN_PER_REGIME or Nl.sum() < MIN_PER_REGIME:
        return None
    idx = np.random.default_rng(seed).integers(0, len(months), (n, len(months)))
    with np.errstate(invalid="ignore", divide="ignore"):
        diff = Sh[idx].sum(1) / Nh[idx].sum(1) - Sl[idx].sum(1) / Nl[idx].sum(1)
    diff = diff[np.isfinite(diff)]
    obs = Sh.sum() / Nh.sum() - Sl.sum() / Nl.sum()
    return {"diff": float(obs), "ci": (float(np.percentile(diff, 2.5)), float(np.percentile(diff, 97.5))), "n_high": int(Nh.sum()), "n_low": int(Nl.sum()),
            "high": float(Sh.sum() / Nh.sum()), "low": float(Sl.sum() / Nl.sum())}


def verdict(r):
    if r is None:
        return "H-REGIME: لا حكم (عينة قليلة أو نظام واحد فقط)"
    lo, hi = r["ci"]
    desc = f"مرتفع {100 * r['high']:+.2f}% مقابل منخفض {100 * r['low']:+.2f}%، الفرق {100 * r['diff']:+.2f}% بفاصل {100 * lo:+.2f}…{100 * hi:+.2f}"
    if lo > 0 or hi < 0:
        return f"H-REGIME: يختلف ({desc}) — استكشافي: يحتاج تكرار الفرق في الظل الحي"
    return f"H-REGIME: لم يثبت اختلاف ({desc} يشمل الصفر)"


def report(per_dte, yearly, meta):
    L = ["# الزخم ونظام التضخم (نتائج آلية، استكشافية)", "", f"- {meta}",
         f"- مهارة زخم آخر ساعة (ATM، خروج بعد 60 دقيقة، كلفة حقيقية معايَرة) مقسومة بحسب التضخم السنوي المعروف وقت الصفقة (عتبة {100 * THRESH:.0f}%، تأخير نشر {PUBLISH_LAG_DAYS} يوماً).",
         "- ⚠️ النظم المستقلة قليلة (2021–2023 مرتفع ثم قرب العتبة)؛ وصف لا إثبات. الحكم على الظل الحي.", ""]
    if yearly is not None and len(yearly):
        L += ["## بحسب السنة (1DTE)", "", "| السنة | صفقات | متوسط المهارة | متوسط التضخم السنوي المعروف |", "|---|---|---|---|"]
        for y, r in yearly.iterrows():
            L.append(f"| {y} | {int(r['n'])} | {100 * r['skill']:+.2f}% | {100 * r['yoy']:.1f}% |")
        L.append("")
    for dte, r in per_dte.items():
        L += [f"## ATM {dte}DTE", ""]
        if r is None:
            L += ["لا حكم (عينة قليلة).", ""]
        else:
            L += [f"- تضخم ≥{100 * THRESH:.0f}%: {r['n_high']} صفقة، مهارة {100 * r['high']:+.2f}% | أقل: {r['n_low']} صفقة، {100 * r['low']:+.2f}% | الفرق {100 * r['diff']:+.2f}% (فاصل {100 * r['ci'][0]:+.2f}…{100 * r['ci'][1]:+.2f})", ""]
    L += ["⚖️ **" + verdict(per_dte.get(1)) + "**"]
    return L


# ============================ التشغيل ============================
def fetch_cpi():
    req = urllib.request.Request(FRED, headers={"User-Agent": "spxbot/1.0"})
    return parse_cpi(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))


def main(argv):
    path = argv[0] if argv else ls.DATA
    if not os.path.exists(path):
        print("لا ملف بيانات:", path)
        return 0
    try:
        cpi = fetch_cpi()
    except Exception as e:  # noqa: BLE001
        msg = f"تعذر تحميل CPI ({type(e).__name__}): لا دراسة هذه المرة."
        print(msg)
        with open(OUT, "w", encoding="utf-8", newline="\n") as f:
            f.write("# الزخم ونظام التضخم\n\n" + msg + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
        return 1
    d = ls.load(path)
    vix = obc.load_vix()
    F = sim.features(d)
    S = obc.samples(d).merge(F, on=["day", "i"], how="inner")
    S = S[np.isfinite(S["day"].map(vix).to_numpy(float))].reset_index(drop=True)
    targets = sim.load_targets()
    per, yearly = {}, None
    for dte in (0, 1):
        mult, _ = sim.calibrate_mult(S, vix, dte, targets[dte])
        call, put, up, days = sim.option_returns(S, vix, dte, mult)
        sk = wf.skill_frame(S, sim.sig_momentum_1h, call, put, up, days)
        yoy = known_yoy(cpi, sk["day"])
        sk["yoy"] = yoy.to_numpy()
        sk = sk.dropna(subset=["yoy"])
        sk["high"] = sk["yoy"] >= THRESH
        per[dte] = cluster_diff(sk)
        if dte == 1:
            yearly = sk.groupby(sk["day"].dt.year).agg(n=("skill", "size"), skill=("skill", "mean"), yoy=("yoy", "mean"))
    meta = f"{S['day'].nunique()} يوماً، CPI حتى {cpi.index.max():%Y-%m}"
    L = report(per, yearly, meta)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
