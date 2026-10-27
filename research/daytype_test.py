#!/usr/bin/env python3
"""H-DAYTYPE: هل يُعرف «نوع اليوم» (اتجاه أم نطاق) عند 10:00 نيويورك؟ ومعه MACD. اختبار فقط (لا يغيّر أي بوت). بطلب المستخدم 27/10/2026.

الفرضيات (مسجّلة هنا وفي CLAUDE.md قبل التشغيل):
  المقاييس عند 10:00 (بعد 6 شموع 5د، بلا نظرة للمستقبل):
    orb_rel   = عرض نطاق أول 30 دقيقة ÷ وسيط عرضه في الـ20 يوماً السابقة.
    drive     = |إغلاق 10:00 − افتتاح 9:30| ÷ عرض النطاق (هل كانت أول 30 دقيقة في اتجاه واحد).
    vwap_side = نسبة الشموع الست التي أغلقت في جهة VWAP نفسها التي أغلقت فيها الأخيرة.
    macd      = |هيستوغرام MACD(12,26,9)| على شموع 5د عند 10:00 ÷ السعر (قوة تسارع الزخم).
    gap_rel   = |الفجوة| ÷ متوسط مدى الأيام الـ14 السابقة (وصفي).
  الدرجة = متوسط الرتب المئوية للمقاييس الأربعة الأولى (rank داخل كل نصف من البيانات، بلا معرفة بالنتائج).
  النتيجة: كفاءة بقية اليوم ER = |إغلاق 15:55 − سعر 10:00| ÷ (أعلى − أدنى من 10:00 إلى 15:55)؛ 1 = اتجاه مستقيم، قرب 0 = نطاق.
  القسمة: النصف الأول من الأيام لتحديد حدود الأثلاث؛ الحكم على النصف الثاني فقط.
  (1) التنبؤ: ارتباط سبيرمان بين الدرجة وER في النصف الثاني > 0 بفاصل 95% (إعادة معاينة الأيام).
  (2) الفائدة: اتباع اتجاه أول 30 دقيقة من 10:00 حتى 15:55 (عائد SPY) في الثلث الأعلى (يوم اتجاه متوقع) > الثلث الأدنى،
      بفاصل 95% لفرق المتوسطين، والثلث الأعلى نفسه > 0 بفاصل 95%.
  وصفي: كل مقياس وحده (ومنه MACD) وارتباطه بـER، وعكس الاتجاه (الارتداد إلى VWAP) في الثلث الأدنى.
تحفظات: SPY بدل SPX؛ بلا VIX والغاما واتساع السوق (لا تاريخ لها في هذا الملف)؛ عائد SPY ليس ربح عقد (تكلفة شراء عقد ATM نحو 3%).
البيانات: SPXBOT_DATA/spy_5m.csv.gz (Alpaca، منذ 2019؛ على الخادم). تشغيل: python research/daytype_test.py
"""
import os
import sys
from datetime import datetime, time as dtime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "daytype_test_results.md")
DATA = os.path.join(os.getenv("SPXBOT_DATA", "/opt/spxbot/data"), "spy_5m.csv.gz")
NY = "America/New_York"


def load(path=DATA):
    d = pd.read_csv(path)
    d.index = pd.DatetimeIndex(pd.to_datetime(d["t"], utc=True)).tz_convert(NY)
    d = d.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})[["Open", "High", "Low", "Close", "Volume"]]
    return d[(d.index.time >= dtime(9, 30)) & (d.index.time < dtime(16, 0))]


def macd_hist(close):
    m = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    return m - m.ewm(span=9, adjust=False).mean()


def day_rows(bars):
    """صف لكل يوم: المقاييس عند 10:00، اتجاه أول 30 دقيقة، وER وعوائد بقية اليوم."""
    hist = macd_hist(bars["Close"])
    rows, prev = [], []
    for day, g in bars.groupby(bars.index.date):
        if len(g) < 70:                                       # يوم ناقص أو إغلاق مبكر
            prev.append(None)
            continue
        first, rest = g[g.index.time < dtime(10, 0)], g[(g.index.time >= dtime(10, 0)) & (g.index.time < dtime(15, 55))]
        if len(first) < 6 or len(rest) < 60:
            continue
        o, p10 = float(first["Open"].iloc[0]), float(first["Close"].iloc[-1])
        width = float(first["High"].max() - first["Low"].min())
        tp = (first["High"] + first["Low"] + first["Close"]) / 3
        vw = (tp * first["Volume"]).cumsum() / first["Volume"].cumsum().replace(0, np.nan)
        side = np.sign(first["Close"] - vw).fillna(0).values
        hi, lo, close = float(rest["High"].max()), float(rest["Low"].min()), float(g[g.index.time < dtime(15, 55)]["Close"].iloc[-1])
        vw_all = ((g["High"] + g["Low"] + g["Close"]) / 3 * g["Volume"]).cumsum() / g["Volume"].cumsum().replace(0, np.nan)
        rows.append({"day": day, "o": o, "p10": p10, "width": width, "drive": abs(p10 - o) / width if width > 0 else 0.0,
                     "vwap_side": float(np.mean(side == side[-1])) if side[-1] != 0 else 0.5,
                     "macd": abs(float(hist.loc[first.index[-1]])) / p10, "dir": 1 if p10 > o else -1,
                     "er": abs(close - p10) / (hi - lo) if hi > lo else 0.0, "ret_rest": close / p10 - 1,
                     "vwap10": float(vw.iloc[-1]) if not np.isnan(vw.iloc[-1]) else p10,
                     "range": float(g["High"].max() - g["Low"].min()), "prev_close": None, "vw_close": float(vw_all.iloc[-1])})
    df = pd.DataFrame(rows).set_index("day")
    df["orb_rel"] = df["width"] / df["width"].shift(1).rolling(20).median()
    closes = bars.groupby(bars.index.date)["Close"].last()
    df["gap_rel"] = (df["o"] / closes.shift(1).reindex(df.index) - 1).abs() / (df["range"] / df["p10"]).shift(1).rolling(14).mean()
    df["follow"] = df["dir"] * df["ret_rest"]                                  # اتباع اتجاه أول 30 دقيقة
    df["fade"] = np.sign(df["vwap10"] - df["p10"]) * df["ret_rest"]            # العودة نحو VWAP
    return df.dropna(subset=["orb_rel"])


FEATS = ("orb_rel", "drive", "vwap_side", "macd")


def score(df):
    return df[list(FEATS)].rank(pct=True).mean(axis=1)


def boot(fn, days, n=3000, seed=19):
    rng = np.random.default_rng(seed)
    vals = [fn(rng.integers(0, days, days)) for _ in range(n)]
    vals = [v for v in vals if v is not None and np.isfinite(v)]
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def spearman(a, b):
    return float(pd.Series(a).rank().corr(pd.Series(b).rank()))


def analyze(df):
    days = sorted(df.index)
    h = len(days) // 2
    A, B = df.loc[days[:h]].copy(), df.loc[days[h:]].copy()
    A["s"], B["s"] = score(A), score(B)
    lo_t, hi_t = A["s"].quantile(1 / 3), A["s"].quantile(2 / 3)
    B["grp"] = np.where(B["s"] >= hi_t, "top", np.where(B["s"] <= lo_t, "bot", "mid"))
    s, er = B["s"].values, B["er"].values
    rho = spearman(s, er)
    rho_ci = boot(lambda i: spearman(s[i], er[i]), len(B))
    top, bot = B[B.grp == "top"]["follow"].values, B[B.grp == "bot"]["follow"].values
    diff = float(top.mean() - bot.mean())
    tb, bb = B["grp"].values, B["follow"].values

    def d_fn(i):
        g, f = tb[i], bb[i]
        return f[g == "top"].mean() - f[g == "bot"].mean() if (g == "top").any() and (g == "bot").any() else None
    diff_ci = boot(d_fn, len(B))
    top_ci = boot(lambda i: top[i % len(top)].mean(), len(top))
    feats = {f: (spearman(B[f].values, er), boot(lambda i, f=f: spearman(B[f].values[i], er[i]), len(B))) for f in FEATS + ("gap_rel",) if B[f].notna().all()}
    fade_bot = B[B.grp == "bot"]["fade"].values
    return {"nA": len(A), "nB": len(B), "span": (days[0], days[h], days[-1]), "rho": rho, "rho_ci": rho_ci,
            "top_mean": float(top.mean()), "top_ci": top_ci, "bot_mean": float(bot.mean()), "diff": diff, "diff_ci": diff_ci,
            "er_top": float(B[B.grp == "top"]["er"].mean()), "er_bot": float(B[B.grp == "bot"]["er"].mean()),
            "feats": feats, "fade_bot": float(fade_bot.mean()), "fade_bot_ci": boot(lambda i: fade_bot[i % len(fade_bot)].mean(), len(fade_bot)),
            "n_top": len(top), "n_bot": len(bot)}


def pc(x, d=3):
    return f"{100 * x:+.{d}f}%"


def ci(c, pct=True):
    return f"{100 * c[0]:+.3f}…{100 * c[1]:+.3f}%" if pct else f"{c[0]:+.3f}…{c[1]:+.3f}"


def report(r):
    a, m, b = r["span"]
    L = ["# H-DAYTYPE: نوع اليوم عند 10:00 (اتجاه أم نطاق) ومعه MACD (اختبار، SPY 5د من Alpaca)", "",
         f"- النصف الأول (تحديد الحدود): {r['nA']} يوماً ({a} → {m})؛ النصف الثاني (الحكم): {r['nB']} يوماً (→ {b}).",
         "- بلا VIX والغاما واتساع السوق؛ عائد SPY ليس ربح عقد (تكلفة شراء عقد ATM نحو 3%).", "",
         "## (1) هل الدرجة عند 10:00 تتنبأ بنوع بقية اليوم؟", "",
         f"- ارتباط سبيرمان بين الدرجة وكفاءة بقية اليوم: {r['rho']:+.3f} بفاصل {ci(r['rho_ci'], False)}",
         f"- متوسط كفاءة بقية اليوم: الثلث الأعلى {r['er_top']:.3f} مقابل الأدنى {r['er_bot']:.3f}", ""]
    L.append(f"⚖️ H-DAYTYPE (1): {'تتنبأ' if r['rho_ci'][0] > 0 else 'لا تتنبأ'} (ارتباط {r['rho']:+.3f})")
    L += ["", "## (2) هل تفيد؟ اتباع اتجاه أول 30 دقيقة من 10:00 حتى 15:55", "",
          "| المجموعة | أيام | متوسط عائد SPY | فاصل 95% |", "|---|---|---|---|",
          f"| الثلث الأعلى (يوم اتجاه متوقع) | {r['n_top']} | {pc(r['top_mean'])} | {ci(r['top_ci'])} |",
          f"| الثلث الأدنى (يوم نطاق متوقع) | {r['n_bot']} | {pc(r['bot_mean'])} | - |", "",
          f"- الفرق (أعلى − أدنى): {pc(r['diff'])} بفاصل {ci(r['diff_ci'])}"]
    ok2 = r["diff_ci"][0] > 0 and r["top_ci"][0] > 0
    L += ["", f"⚖️ H-DAYTYPE (2): {'تفيد' if ok2 else 'لا تفيد بما يكفي'} (اتباع الاتجاه في يوم الاتجاه المتوقع {pc(r['top_mean'])})", "",
          "## وصفي: كل مقياس وحده (ارتباطه بكفاءة بقية اليوم)", "", "| المقياس | الارتباط | فاصل 95% |", "|---|---|---|"]
    names = {"orb_rel": "عرض أول 30 دقيقة", "drive": "اندفاع أول 30 دقيقة", "vwap_side": "الثبات في جهة VWAP", "macd": "قوة هيستوغرام MACD", "gap_rel": "حجم الفجوة"}
    for f, (rho, c) in r["feats"].items():
        L.append(f"| {names[f]} | {rho:+.3f} | {ci(c, False)} |")
    L += ["", f"- وصفي: الارتداد نحو VWAP في يوم النطاق المتوقع: {pc(r['fade_bot'])} بفاصل {ci(r['fade_bot_ci'])}", ""]
    return L


def main(argv):
    df = day_rows(load())
    r = analyze(df)
    L = report(r)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
