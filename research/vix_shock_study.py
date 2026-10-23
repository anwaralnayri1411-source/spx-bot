#!/usr/bin/env python3
"""دراسة ارتداد SPY بعد «صدمة التقلب» على 15 سنة يومية (قراءة فقط، لا تغيّر أي توصية). بطلب المستخدم 24/10/2026: «نختبرها كلها».

السؤال: بعد يوم يقفز فيه VIX ≥ 15% أو يهبط فيه SPY ≥ 2%، هل يرتد SPY خلال الأيام الخمسة التالية أكثر من المعتاد؟ (فكرة شائعة: «الخوف فرصة شراء».)
العائد الأمامي = إغلاق بعد 5 أيام تداول ÷ إغلاق يوم الصدمة − 1 (دخول ممكن عند ذلك الإغلاق). الأثر = متوسط العائد بعد الصدمات − متوسط العائد الأمامي لكل الأيام.
**الفرضية المسجّلة مسبقاً (24/10/2026، CLAUDE.md، قبل أي تشغيل):** H-SHOCK: متوسط العائد الأمامي لخمسة أيام بعد الصدمة يتجاوز متوسط كل الأيام بفاصل 95% يستثني الصفر
(إعادة معاينة عناقيد شهرية لأن الصدمات تتجمع)، بعد 20 حدثاً على الأقل (يوم صدمة = يوم مستقل عن غيره بفارق 5 أيام فأكثر؛ الأيام المتتالية تُحسب مرة واحدة).
استكشافي: كل شرط على حدة، آفاق 1 و10 أيام، ومستوى VIX المرتفع (>30).
تحفظ: التحقق على بيانات أزمات قليلة (2008 خارج 15 سنة؛ 2020 و2022 تقود النتائج)، والارتداد على المؤشر لا يعني أن خيار الشراء يربح (الثيتا والانتشار؛ انظر option_buyer_cost).
تشغيل: python research/vix_shock_study.py   المخرج: research/vix_shock_study_results.md
"""
import os
import sys
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import pead_study as ps  # noqa: E402  (cluster_ci)

OUT = os.path.join(HERE, "vix_shock_study_results.md")
VIX_JUMP = 0.15
SPY_DROP = -0.02
HORIZON = 5
MIN_EVENTS = 20
GAP_DAYS = 5


# ============================ دوال نقية ============================
def build(spy, vix, horizon=HORIZON):
    """DataFrame يومي: عائد اليوم، قفزة VIX، العائد الأمامي fwd، صدمة SPY/VIX، ومستوى VIX. spy وvix: Series بفهرس تاريخ."""
    df = pd.DataFrame({"spy": spy, "vix": vix}).dropna()
    df["ret"] = df["spy"].pct_change()
    df["vjump"] = df["vix"].pct_change()
    df["fwd"] = df["spy"].shift(-horizon) / df["spy"] - 1
    df["shock_spy"] = df["ret"] <= SPY_DROP
    df["shock_vix"] = df["vjump"] >= VIX_JUMP
    df["shock"] = df["shock_spy"] | df["shock_vix"]
    return df.dropna(subset=["ret", "vjump"])


def independent(flags, gap=GAP_DAYS):
    """يبقي أول يوم من كل مجموعة صدمات تفصل بينها أقل من gap أيام تداول (قناع بوليني)."""
    keep, last = np.zeros(len(flags), dtype=bool), -10 ** 9
    for i, f in enumerate(np.asarray(flags)):
        if f and i - last >= gap:
            keep[i] = True
            last = i
    return keep


def excess(df, mask, col="fwd"):
    """(قيم الأثر لأيام mask، العناقيد الشهرية). الأثر = العائد الأمامي − متوسط كل الأيام ذات العائد المتاح."""
    base = df[col].mean()
    sub = df[mask & df[col].notna()]
    return (sub[col] - base).to_numpy(), np.asarray([f"{d.year}-{d.month:02d}" for d in sub.index])


def stat(df, mask, col="fwd"):
    v, cl = excess(df, mask, col)
    if len(v) == 0:
        return None
    return {"n": len(v), "hit": float((v > 0).mean()), "mean": float(v.mean()), "ci": ps.cluster_ci(v, cl)}


def fmt(s):
    if s is None:
        return "- | - | - | -"
    ci = "-" if s["ci"] is None else f"{100 * s['ci'][0]:+.2f}…{100 * s['ci'][1]:+.2f}"
    return f"{s['n']} | {100 * s['hit']:.0f}% | {100 * s['mean']:+.2f}% | {ci}"


def verdict(df):
    mask = pd.Series(independent(df["shock"].to_numpy()), index=df.index)
    s = stat(df, mask)
    if s is None or s["n"] < MIN_EVENTS:
        return f"H-SHOCK: لا حكم بعد ({0 if s is None else s['n']} من {MIN_EVENTS} حدثاً)"
    if s["ci"] is None:
        return "H-SHOCK: لا حكم بعد (عناقيد قليلة)"
    lo, hi = s["ci"]
    if lo > 0:
        return f"H-SHOCK: تحققت (الأثر {100 * s['mean']:+.2f}% فوق المعتاد، فاصل {100 * lo:+.2f}…{100 * hi:+.2f})"
    if hi < 0:
        return f"H-SHOCK: عكسية (يتبع الهبوط ضعف: {100 * s['mean']:+.2f}%)"
    return f"H-SHOCK: لم تتحقق (الأثر {100 * s['mean']:+.2f}% وفاصله {100 * lo:+.2f}…{100 * hi:+.2f} يشمل الصفر)"


def report(df, meta):
    L = ["# ارتداد SPY بعد صدمة التقلب (نتائج آلية)", "", f"- {meta}",
         f"- قراءة فقط ولا تغيّر أي توصية. الصدمة: VIX ≥ +{100 * VIX_JUMP:.0f}% في يوم أو SPY ≤ {100 * SPY_DROP:.0f}%. العائد الأمامي {HORIZON} أيام من إغلاق يوم الصدمة، والأثر مطروحاً منه متوسط كل الأيام. فاصل 95% بعناقيد شهرية.",
         f"- **الفرضية المسجّلة مسبقاً:** H-SHOCK: الأثر > 0 بفاصل يستثني الصفر بعد {MIN_EVENTS} حدثاً مستقلاً على الأقل.", ""]
    if len(df) == 0:
        return L + ["لا بيانات."]
    ind = pd.Series(independent(df["shock"].to_numpy()), index=df.index)
    L += ["| الشرط | أحداث | أعلى من المعتاد | متوسط الأثر | فاصل 95% (%) |", "|---|---|---|---|---|",
          f"| **الأساسي** صدمة (أي شرط، أحداث مستقلة) | {fmt(stat(df, ind))} |",
          f"| (استكشافي) كل أيام الصدمة بلا فصل (متداخلة) | {fmt(stat(df, df['shock']))} |",
          f"| (استكشافي) هبوط SPY ≤ 2% فقط | {fmt(stat(df, df['shock_spy'] & ~df['shock_vix']))} |",
          f"| (استكشافي) قفزة VIX ≥ 15% فقط | {fmt(stat(df, df['shock_vix'] & ~df['shock_spy']))} |",
          f"| (استكشافي) الشرطان معاً | {fmt(stat(df, df['shock_vix'] & df['shock_spy']))} |",
          f"| (استكشافي) صدمة وVIX > 30 | {fmt(stat(df, df['shock'] & (df['vix'] > 30)))} |",
          f"| (استكشافي) صدمة وVIX ≤ 30 | {fmt(stat(df, df['shock'] & (df['vix'] <= 30)))} |"]
    for h in (1, 10):
        d2 = df.copy()
        d2["fwd"] = d2["spy"].shift(-h) / d2["spy"] - 1
        L.append(f"| (استكشافي) أفق {h} أيام، أحداث مستقلة | {fmt(stat(d2, ind))} |")
    half = df.index[len(df) // 2]
    for name, part in (("النصف الأول", df[df.index <= half]), ("النصف الثاني", df[df.index > half])):
        pi = pd.Series(independent(part["shock"].to_numpy()), index=part.index)
        L.append(f"| {name} ({part.index.min():%Y}–{part.index.max():%Y}) | {fmt(stat(part, pi))} |")
    L += ["", "⚖️ **" + verdict(df) + "**"]
    return L


# ============================ التشغيل ============================
def load():
    import yfinance as yf
    px = yf.download(["SPY", "^VIX"], period="15y", interval="1d", auto_adjust=True, progress=False, group_by="ticker")
    spy, vix = px["SPY"]["Close"].dropna(), px["^VIX"]["Close"].dropna()
    for s in (spy, vix):
        s.index = pd.to_datetime(s.index).tz_localize(None)
    return spy, vix


def main(argv):
    try:
        spy, vix = load()
    except Exception as e:  # noqa: BLE001
        print("تعذر تحميل البيانات:", type(e).__name__)
        return 0
    df = build(spy, vix)
    meta = f"{len(df)} يوماً من {df.index.min():%Y-%m-%d} إلى {df.index.max():%Y-%m-%d}" if len(df) else "بلا بيانات"
    L = report(df, meta)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
