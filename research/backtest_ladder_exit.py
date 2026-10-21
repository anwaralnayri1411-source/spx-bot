#!/usr/bin/env python3
"""اختبار تاريخي: خروج بوت SPX بأهداف متدرجة مع رفع الوقف (طريقة MGT) مقابل +50% الحالية.

الإشارات وأوقاتها وعقودها من research/backtest_spx_v2.py حرفياً (direction_points + فلاتر البوت، فحص كل 30د،
بيانات متأخرة 15د، تسعير بلاك-شولز نظري، فرق سعر ±5% وعمولة). الذي يتغير هو الخروج فقط:
  v2      : خروج البوت كما يعمل الآن (التنبيه من بيانات متأخرة 15د، والبيع بالسعر الحالي لحظتها) — مرجع v2 نفسه.
  fixed   : +50% / −40% بعد 15د / 15:30 على السعر الحالي (متابعة لحظية، كما سيكون مع Alpaca).
  ladder  : أهداف +50%، +100% ثم كل +100%؛ الوقف −40% حتى الهدف الأول، ثم الدخول، ثم الهدف السابق.
  thirds  : ثلث عند +50%، ثلث عند +100%، والباقي يتبع الوقف المرفوع.
  mgt     : هدف كل +1.00$ من سعر العقد (+100$ للعقد)، وقف أولي −50%، ثم الدخول، ثم الهدف السابق.
  hold    : حتى 15:30.
السياسات اللحظية تُقيَّم على مسار قيمة العقد كل 5 دقائق (السعر الحالي بعد خصم فرق البيع والعمولة)، فقمة بين قراءتين
لا تُرى. الإشارات نفسها في كل السياسات (حد الوقفين اليومي يبقى محسوباً على خروج v2) لتكون المقارنة على الصفقات ذاتها.
المقارنة الرئيسية: ladder مقابل fixed (كلاهما لحظي)، بفاصل ثقة حسب الأيام، مع فصل البيانات المحجوزة كما في v2.
تشغيل (يحتاج ياهو، فيعمل على GitHub): python research/backtest_ladder_exit.py → research/backtest_ladder_exit_results.md
"""
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import backtest_spx_v2 as v2          # noqa: E402  قراءة فقط: الإشارات والتسعير
import ladder_exit_shadow as lx       # noqa: E402  سياسات الخروج نفسها المستخدمة في الظل

OUT = os.path.join(HERE, "backtest_ladder_exit_results.md")
PATHS = []                            # (معرّف الصفقة، المسار) يملؤه follow المغلَّف
_orig_follow = v2.follow


def follow_with_path(g, m, side, K, run_m, rec_entry, real_entry, iv0, open_px, variant):
    """نفس follow في v2، ويحفظ مسار القيمة الحالية القابلة للبيع كل 5 دقائق حتى 15:30."""
    path, tau = [], run_m + 5
    d = g.index[0].date()
    while tau <= v2.FINAL_M:
        S_now = v2.s_at(g, m, tau, False)
        if S_now is not None:
            val = v2.price(S_now, K, tau, side, iv0, open_px, variant) * (1 - v2.HAIRCUT) - v2.COMMISSION
            path.append((datetime(d.year, d.month, d.day, tau // 60, tau % 60), val / real_entry - 1))
        tau += 5
    PATHS.append(path)
    return _orig_follow(g, m, side, K, run_m, rec_entry, real_entry, iv0, open_px, variant)


def run_policies(tr, paths):
    rows = []
    for (_, t), path in zip(tr.iterrows(), paths):
        if not path:
            continue
        start = datetime(t["day"].year, t["day"].month, t["day"].day, t["run_m"] // 60, t["run_m"] % 60)
        can = lambda x, s=start: (x - s).total_seconds() / 60 >= v2.ob.MIN_AGE_MIN
        e = t["entry"]
        r = {"day": t["day"], "role": t["role"], "side": t["side"], "entry": e, "v2": t["pnl"],
             "peak": max(p for _, p in path),
             "fixed": lx.run_fixed(path, can)[0], "ladder": lx.run_ladder(path, e, can)[0],
             "thirds": lx.run_ladder(path, e, can, thirds=True)[0], "mgt": lx.run_mgt(path, e, can)[0],
             "hold": path[-1][1]}
        rows.append(r)
    return pd.DataFrame(rows)


def boot_diff(df, a, b, rng):
    days = sorted(df["day"].unique())
    by = {d: (df[df["day"] == d][a] - df[df["day"] == d][b]).values for d in days}
    m = []
    for _ in range(v2.BOOT):
        pick = rng.choice(len(days), len(days))
        m.append(np.concatenate([by[days[i]] for i in pick]).mean())
    return np.percentile(m, [2.5, 97.5])


POL = ("v2", "fixed", "ladder", "thirds", "mgt", "hold")
NAMES = {"v2": "البوت الآن (تنبيه متأخر)", "fixed": "+50% لحظي", "ladder": "أهداف متدرجة", "thirds": "أثلاث",
         "mgt": "طريقة MGT (+100$)", "hold": "حتى 15:30"}


def table(df, title, rng):
    L = [f"### {title} — {len(df)} صفقة، {df['day'].nunique()} يوماً", "",
         "| الخروج | متوسط الصفقة | بالدولار للعقد | رابحة | أفضل | أسوأ |", "|---|---|---|---|---|---|"]
    for p in POL:
        usd = (df[p] * df["entry"] * 100).mean()
        L.append(f"| {NAMES[p]} | {df[p].mean() * 100:+.1f}% | {usd:+.0f}$ | {(df[p] > 0).mean() * 100:.0f}% | "
                 f"{df[p].max() * 100:+.0f}% | {df[p].min() * 100:+.0f}% |")
    if df["day"].nunique() >= 5:
        lo, hi = boot_diff(df, "ladder", "fixed", rng)
        L.append(f"\n**أهداف متدرجة − +50% لحظي:** {(df['ladder'] - df['fixed']).mean() * 100:+.1f}% للصفقة "
                 f"(فاصل 95% حسب الأيام: {lo * 100:+.1f}% … {hi * 100:+.1f}%)")
    pk = df["peak"]
    L.append(f"\nقمة الصفقات: بلغت +50% {(pk >= .5).mean() * 100:.0f}%، +100% {(pk >= 1).mean() * 100:.0f}%، "
             f"+200% {(pk >= 2).mean() * 100:.0f}%، +500% {(pk >= 5).mean() * 100:.0f}%.")
    return L + [""]


def main():
    data = v2.load()
    v2.follow = follow_with_path
    tr, diag = v2.simulate(data, v2.HEADLINE)
    v2.follow = _orig_follow
    df = run_policies(tr, PATHS)
    rng = np.random.default_rng(11)
    bot = df[df["role"] == "bot"]
    ho = pd.Timestamp(v2.HOLDOUT_FROM).date()
    L = ["# اختبار تاريخي: الأهداف المتدرجة مقابل +50% لإشارات بوت SPX", "",
         f"- الإشارات من backtest_spx_v2 (التسعير الأساسي): {diag['signals']} توصية. أسعار العقود نظرية (بلاك-شولز) مع فرق ±5% وعمولة.",
         "- السياسات اللحظية تُقرأ كل 5 دقائق على السعر الحالي؛ «البوت الآن» = خروج v2 بالتنبيه المتأخر 15 دقيقة.",
         "- ⚠️ نظري: لا يعكس قفزات تذبذب 0DTE الحقيقية؛ النتيجة مؤشر مبكر لا حكم. الحكم من ظل الأهداف المتدرجة الحي.", ""]
    L += table(bot[bot["day"] < ho], "استكشاف — توصيات البوت", rng)
    L += table(bot[bot["day"] >= ho], "البيانات المحجوزة — توصيات البوت", rng) if (bot["day"] >= ho).any() else []
    L += table(bot, "كل الفترة — توصيات البوت", rng)
    for s in ("CALL", "PUT"):
        L += table(bot[bot["side"] == s], f"كل الفترة — {s}", rng)
    L += table(df[df["role"] == "opp"], "مرجع: نفس الأوقات بالاتجاه المعاكس", rng)
    L.append("القرار: إن تفوّقت الأهداف المتدرجة على +50% اللحظي بوضوح (فاصل فوق الصفر) هنا وفي الظل الحي → نعرضها على المستخدم.")
    txt = "\n".join(L) + "\n"
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(txt)
    print(txt)


if __name__ == "__main__":
    main()
