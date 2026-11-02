#!/usr/bin/env python3
"""H-SREGIME: هل يفيد فلتر اتجاه السوق عقود الشركات؟ (مسجّل مسبقاً في CLAUDE.md 2/11/2026). اختبار فقط، لا يغيّر أي بوت.

البيانات: صفقات H-STKCONTRACT الحقيقية (research/stock_contract_test_trades.csv أو المسار المعطى)، العقد b2 بالخروج r1.
النظام يوم الدخول: إغلاق SPY في اليوم السابق مقابل متوسطه 20 يوماً. متوافق = CALL فوقه أو PUT تحته.
الفرضية: متوسط المتوافق − المعاكس > 0 بفاصل 95% (إعادة معاينة أيام الدخول) يستثني الصفر، وموجب في النصفين.
تشغيل: python research/stocks_regime_test.py [مسار ملف الصفقات]   المخرج: research/stocks_regime_test_results.md
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
TRADES = os.path.join(HERE, "stock_contract_test_trades.csv")
OUT_MD = os.path.join(HERE, "stocks_regime_test_results.md")
VARIANT, RULE = "b2", "r1"
N_BOOT = 10000


def spy_daily(start, end):
    import yfinance as yf
    d = yf.Ticker("SPY").history(start=start, end=end, auto_adjust=True)[["Close"]]
    v = yf.Ticker("^VIX").history(start=start, end=end)[["Close"]].rename(columns={"Close": "vix"})
    d.index, v.index = d.index.tz_localize(None).normalize(), v.index.tz_localize(None).normalize()
    return d.join(v, how="left")


def regime_table(spy):
    """لكل يوم: النظام كما يُرى صباحه (من إغلاق اليوم السابق)."""
    c = spy["Close"]
    f = pd.DataFrame({"above20": c > c.rolling(20).mean(), "above50": c > c.rolling(50).mean(),
                      "r5": c / c.shift(5) - 1, "vix": spy["vix"]}).shift(1)
    return f.dropna()


def label(trades, reg):
    t = trades.copy()
    t["date"] = pd.to_datetime(t["day"])
    t = t.merge(reg, left_on="date", right_index=True, how="inner")
    t["above20"], t["above50"] = t["above20"].astype(bool), t["above50"].astype(bool)   # بعد shift تصير object؛ ~ عليها يعطي أعداداً
    up = np.where(t["side"] == "CALL", 1, -1)
    t["aligned"] = np.where(up * np.where(t["above20"], 1, -1) > 0, "متوافق", "معاكس")
    t["aligned50"] = np.where(up * np.where(t["above50"], 1, -1) > 0, "متوافق", "معاكس")
    t["aligned_r5"] = np.where(up * np.sign(t["r5"]) > 0, "متوافق", "معاكس")
    t["vix_hi"] = np.where(t["vix"] >= 20, "VIX ≥ 20", "VIX < 20")
    return t


def diff_ci(t, col="aligned", n=N_BOOT, seed=0):
    """(فرق المتوسطين، الفاصل 95%) بإعادة معاينة أيام الدخول."""
    days = t["day"].unique()
    by = {d: g for d, g in t.groupby("day")}
    rng = np.random.default_rng(seed)
    a = t[t[col] == "متوافق"]["pnl"].mean() - t[t[col] == "معاكس"]["pnl"].mean()
    bs = []
    for _ in range(n):
        s = pd.concat([by[d] for d in rng.choice(days, len(days))])
        x, y = s[s[col] == "متوافق"]["pnl"], s[s[col] == "معاكس"]["pnl"]
        if len(x) and len(y):
            bs.append(x.mean() - y.mean())
    return a, tuple(np.percentile(bs, [2.5, 97.5]))


def row(name, g):
    return f"| {name} | {len(g)} | {g['pnl'].mean() * 100:+.1f}% | {(g['pnl'] > 0).mean() * 100:.0f}% | {(g['why'] == 'tp').mean() * 100:.0f}% |"


def report(t):
    a, (lo, hi) = diff_ci(t)
    days = sorted(t["day"].unique())
    half = days[len(days) // 2]
    h1, h2 = t[t["day"] < half], t[t["day"] >= half]
    d1 = h1[h1["aligned"] == "متوافق"]["pnl"].mean() - h1[h1["aligned"] == "معاكس"]["pnl"].mean()
    d2 = h2[h2["aligned"] == "متوافق"]["pnl"].mean() - h2[h2["aligned"] == "معاكس"]["pnl"].mean()
    ok = lo > 0 and d1 > 0 and d2 > 0
    L = ["# H-SREGIME: فلتر اتجاه السوق لعقود الشركات (نتائج آلية)", "",
         f"- صفقات H-STKCONTRACT الحقيقية، العقد المعتمد (14–21 يوماً، دلتا 0.45) بقاعدة +50/−40؛ {len(t)} صفقة، "
         f"{t['sym'].nunique()} شركة، {len(days)} يوم دخول ({days[0]} → {days[-1]}).",
         "- النظام من إغلاق SPY في اليوم السابق مقابل متوسط 20 يوماً. الاتجاه مفروض بالجهتين، فالنتيجة تقيس الفلتر وحده.", "",
         "| المجموعة | صفقات | متوسط | رابحة | بلغت الهدف |", "|---|---|---|---|---|",
         row("الكل", t), row("**متوافق** مع السوق (متوسط 20)", t[t["aligned"] == "متوافق"]),
         row("معاكس للسوق (متوسط 20)", t[t["aligned"] == "معاكس"]),
         row("CALL والسوق فوق المتوسط", t[(t["side"] == "CALL") & t["above20"]]),
         row("CALL والسوق تحت المتوسط", t[(t["side"] == "CALL") & ~t["above20"]]),
         row("PUT والسوق تحت المتوسط", t[(t["side"] == "PUT") & ~t["above20"]]),
         row("PUT والسوق فوق المتوسط", t[(t["side"] == "PUT") & t["above20"]]), "",
         f"- **الفرق (متوافق − معاكس):** {a * 100:+.1f} نقطة للصفقة، فاصل 95% [{lo * 100:+.1f}، {hi * 100:+.1f}]",
         f"- النصف الأول: {d1 * 100:+.1f} نقطة | النصف الثاني: {d2 * 100:+.1f} نقطة", "",
         "## وصفي بلا حكم", "", "| التقسيم | صفقات | متوسط | رابحة | بلغت الهدف |", "|---|---|---|---|---|"]
    for col, name in (("aligned50", "متوسط 50"), ("aligned_r5", "حركة SPY 5 أيام")):
        for k in ("متوافق", "معاكس"):
            L.append(row(f"{name}: {k}", t[t[col] == k]))
    for k in ("VIX < 20", "VIX ≥ 20"):
        for al in ("متوافق", "معاكس"):
            L.append(row(f"{k}، {al} (متوسط 20)", t[(t["vix_hi"] == k) & (t["aligned"] == al)]))
    L += ["", f"⚖️ **H-SREGIME: {'تحققت' if ok else 'لم تتحقق'}** (الفرق {a * 100:+.1f} نقطة، فاصل [{lo * 100:+.1f}، {hi * 100:+.1f}]"
          f"، النصفان {d1 * 100:+.1f} / {d2 * 100:+.1f})",
          "", "تحفظ: صفقات مفروضة بالجهتين كل 15 يوماً لا إشارات البوت؛ عائد العقد بعد نصف الفرق؛ العينة 2024–2026 فقط."]
    return "\n".join(L) + "\n", ok


def main(argv):
    path = argv[0] if argv else TRADES
    tr = pd.read_csv(path)
    tr = tr[(tr["variant"] == VARIANT) & (tr["rule"] == RULE)].dropna(subset=["pnl"])
    reg = regime_table(spy_daily("2023-12-01", "2026-11-01"))
    md, _ = report(label(tr, reg))
    open(OUT_MD, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main(sys.argv[1:])
