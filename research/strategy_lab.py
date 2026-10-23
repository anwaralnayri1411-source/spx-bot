#!/usr/bin/env python3
"""مختبر الاستراتيجيات (قراءة فقط، لا يغيّر أي توصية): هل تتوقع قراءة البوت وبدائلها الشائعة الاتجاه على بيانات سابقة أطول من بياناتنا؟

يُعاد تشغيله شهرياً (وأي فكرة جديدة من فيديو أو غيره تُضاف هنا وتُختبر بالطريقة نفسها قبل أن تدخل ظلاً).
(1) الشركات: قائمة البوت، بيانات ياهو اليومية 7 سنوات. الإشارة عند إغلاق t، الدخول افتتاح t+1، الخروج إغلاق t+H (H=5، عينة كل H أيام بلا تداخل).
    العائد = عائد السهم − متوسط القائمة في النافذة نفسها (يزيل انحياز البقاء: القائمة مختارة اليوم). القيمة = إشارة الاتجاه × العائد المُزال متوسطه.
    القواعد: نقاط البوت الفنية `stocks_bot.tech_points` (بلا الأخبار وتدفق الخيارات فلا تُعاد تاريخياً)، زخم 12-1 و20 يوماً، انعكاس 5 أيام،
    RSI(2)، اختراق 20 يوماً، متوسط 50/200، وعشوائي كمرجع. وفرق أعلى/أدنى 20% من نقاط البوت لكل تاريخ.
(2) المؤشر SPY: بيانات ياهو الساعية (730 يوماً، غير معدّلة) واليومية غير المعدّلة لتطابق الأسعار؛ الإشارة عند إغلاق شمعة 10:30 والعائد حتى
    إغلاق اليوم وإغلاق الغد: أول ساعة، فجوة الافتتاح، أمس، متوسط 50، VIX، وعشوائي.
فاصل 95% بإعادة معاينة التواريخ/الأيام (العينة الفعلية = التواريخ). تحذير: لا قاعدة هنا تتبع مسار العقد ولا تكلفته؛ الاختبار على اتجاه السهم/المؤشر فقط.
تشغيل: python research/strategy_lab.py [--no-fetch]   المخرج: research/strategy_lab_results.md
"""
import os
import sys
import time
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUT = os.path.join(HERE, "strategy_lab_results.md")
H = 5
YEARS = 7
MIN_N = 30


# ============================ دوال نقية (قابلة للاختبار) ============================
def boot_ci(values_by_group, n=2000, seed=3):
    """فاصل 95% لمتوسط القيم بإعادة معاينة المجموعات (التواريخ). values_by_group: {مجموعة: [قيم]}. None إن قلّت المجموعات عن 3."""
    keys = list(values_by_group)
    if len(keys) < 3:
        return None
    rng = np.random.default_rng(seed)
    means = [np.mean([v for k in rng.choice(len(keys), len(keys)) for v in values_by_group[keys[k]]]) for _ in range(n)]
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def evaluate(rows, sig_col, thresh=0.0):
    """rows: DataFrame فيه date وar (عائد مُزال) وعمود الإشارة. يرجع dict بالإحصاء أو None إن قلّت الحالات."""
    x = rows[rows[sig_col].abs() > thresh]
    if len(x) < MIN_N:
        return None
    v = np.sign(x[sig_col]) * x["ar"]
    by = {d: g.tolist() for d, g in v.groupby(x["date"])}
    return {"n": len(x), "hit": float((v > 0).mean()), "mean": float(v.mean()), "ci": boot_ci(by)}


def quintile_spread(rows, col="edge"):
    """فرق أعلى 20% وأدنى 20% من col داخل كل تاريخ: (متوسط الفرق، فاصل 95%، نسبة التواريخ الرابحة، متوسط النصف الأول، متوسط النصف الثاني)."""
    r = rows.copy()
    r["q"] = r.groupby("date")[col].rank(pct=True)
    sp = r.groupby("date").apply(lambda g: g[g["q"] >= 0.8]["ar"].mean() - g[g["q"] <= 0.2]["ar"].mean()).dropna()
    if len(sp) < 10:
        return None
    rng = np.random.default_rng(5)
    bm = [sp.iloc[rng.integers(0, len(sp), len(sp))].mean() for _ in range(3000)]
    half = len(sp) // 2
    return {"mean": float(sp.mean()), "ci": (float(np.percentile(bm, 2.5)), float(np.percentile(bm, 97.5))),
            "win": float((sp > 0).mean()), "n": len(sp), "first": float(sp.iloc[:half].mean()), "second": float(sp.iloc[half:].mean())}


def stock_features(df):
    """ميزات بدائل الشركات من شموع يومية حتى اليوم (آخر صف = اليوم)."""
    c = df["Close"].astype(float)
    ret5 = c.iloc[-1] / c.iloc[-6] - 1
    d = c.diff()
    up, dn = d.clip(lower=0).tail(2).mean(), (-d.clip(upper=0)).tail(2).mean()
    rsi2 = 100 - 100 / (1 + up / dn) if dn > 0 else 100.0
    hi20, lo20 = df["High"].iloc[-21:-1].max(), df["Low"].iloc[-21:-1].min()
    sma50 = c.rolling(50).mean().iloc[-1]
    sma200 = c.rolling(200).mean().iloc[-1] if len(c) >= 200 else np.nan
    return {"mom12_1": c.iloc[-22] / c.iloc[-253] - 1 if len(c) > 253 else np.nan,
            "mom20": c.iloc[-1] / c.iloc[-21] - 1, "rev5": -ret5, "rsi2": 50 - rsi2,
            "brk20": 1 if c.iloc[-1] > hi20 else (-1 if c.iloc[-1] < lo20 else 0),
            "trend": (1 if c.iloc[-1] > sma50 else -1) + (1 if c.iloc[-1] > sma200 else (-1 if sma200 == sma200 else 0))}


def stock_rows(D, tick, tech_points, horizon=H):
    """D: {رمز: DataFrame يومي} ويحتوي SPY. يرجع DataFrame بكل (تاريخ، سهم) مع نقاط البوت والميزات والعائد المُزال."""
    dates = D["SPY"].index
    close = pd.DataFrame({t: D[t]["Close"] for t in tick}).reindex(dates)
    openp = pd.DataFrame({t: D[t]["Open"] for t in tick}).reindex(dates)
    spyc = D["SPY"]["Close"].reindex(dates)
    rows = []
    for i in range(260, len(dates) - horizon - 2, horizon):
        spy5 = float(spyc.iloc[i] / spyc.iloc[i - 5] - 1)
        spy_up = float(spyc.iloc[i]) > float(spyc.iloc[i - 19:i + 1].mean())
        r = close.iloc[i + horizon] / openp.iloc[i + 1] - 1
        r = r - r.mean()
        for t in tick:
            if pd.isna(r.get(t)):
                continue
            df = D[t].iloc[: i + 1]
            try:
                bull, bear, _, _ = tech_points(df, spy5, None, float(df["Volume"].iloc[-1]))
            except Exception:  # noqa: BLE001
                continue
            bull, bear = (bull + 0.5, bear) if spy_up else (bull, bear + 0.5)
            rows.append({"date": dates[i], "t": t, "edge": bull - bear, "ar": float(r[t]), **stock_features(df)})
    R = pd.DataFrame(rows)
    if len(R):
        R["rand"] = np.random.default_rng(1).choice([-1, 1], len(R))
    return R


STOCK_RULES = (("بوتنا: فرق النقاط ≥ 3.5 (عتبة البوت)", "edge", 3.4), ("بوتنا: فرق النقاط ≥ 2", "edge", 1.9), ("بوتنا: كل الإشارات", "edge", 0.01),
               ("زخم 12 شهراً (استبعاد شهر)", "mom12_1", 0), ("زخم 20 يوماً", "mom20", 0), ("انعكاس 5 أيام", "rev5", 0),
               ("RSI(2) انعكاس", "rsi2", 20), ("اختراق 20 يوماً", "brk20", 0), ("متوسط 50/200", "trend", 0), ("عشوائي (مرجع)", "rand", 0))


def index_frame(h, daily):
    """h: شموع ساعية غير معدّلة بفهرس نيويورك. daily: يومي غير معدّل (SPY وVIX Close). يرجع DataFrame بالإشارات والعوائد."""
    rows = []
    for day, g in h.groupby(h.index.date):
        g = g.sort_index()
        if len(g) < 6 or g.index[0].hour != 9:
            continue
        rows.append({"date": pd.Timestamp(day), "o": float(g["Open"].iloc[0]), "c1030": float(g["Close"].iloc[0]), "cl": float(g["Close"].iloc[-1])})
    X = pd.DataFrame(rows).set_index("date")
    spy, vix = daily["spy"].copy(), daily["vix"].copy()
    spy.index, vix.index = pd.to_datetime(spy.index).tz_localize(None), pd.to_datetime(vix.index).tz_localize(None)
    X["prev_close"] = spy.shift(1).reindex(X.index)
    X["next_close"] = spy.shift(-1).reindex(X.index)
    X["prev_ret"] = (spy / spy.shift(1) - 1).shift(1).reindex(X.index)
    X["sma50"] = spy.rolling(50).mean().shift(1).reindex(X.index)
    X["vix_chg"] = (vix / vix.shift(1) - 1).shift(1).reindex(X.index)
    X = X.dropna()
    X["gap"], X["fh"] = X["o"] / X["prev_close"] - 1, X["c1030"] / X["o"] - 1
    X["rest"], X["next"] = X["cl"] / X["c1030"] - 1, X["next_close"] / X["cl"] - 1
    return X


def index_signals(X):
    sg = lambda s, th: np.sign(s).where(s.abs() > th, 0)
    rng = np.random.default_rng(2)
    return {"أول ساعة: استمرار": sg(X["fh"], 0.002), "أول ساعة: انعكاس": -sg(X["fh"], 0.002),
            "فجوة الافتتاح: استمرار": sg(X["gap"], 0.003), "فجوة الافتتاح: ملء": -sg(X["gap"], 0.003),
            "أمس: استمرار": sg(X["prev_ret"], 0.005), "أمس: انعكاس": -sg(X["prev_ret"], 0.005),
            "فوق/تحت متوسط 50": np.sign(X["o"] - X["sma50"]), "VIX صاعد: بيع": -sg(X["vix_chg"], 0.05),
            "عشوائي (مرجع)": pd.Series(rng.choice([-1, 1], len(X)), index=X.index)}


def index_stat(sig, ret):
    v = (sig * ret)[sig != 0]
    if len(v) < MIN_N:
        return None
    by = {k: [x] for k, x in v.items()}
    return {"n": len(v), "hit": float((v > 0).mean()), "mean": float(v.mean()), "ci": boot_ci(by)}


def fmt(s, sep=" | "):
    if s is None:
        return "حالات قليلة"
    ci = "أيام قليلة" if s["ci"] is None else f"{100 * s['ci'][0]:+.2f}…{100 * s['ci'][1]:+.2f}"
    return sep.join([str(s["n"]), f"{100 * s['hit']:.1f}%", f"{100 * s['mean']:+.3f}%", ci])


def report(R, X, notes):
    L = ["# مختبر الاستراتيجيات (نتائج آلية)", "",
         f"- آخر تشغيل: {datetime.now():%Y-%m-%d}. قراءة فقط، ولا يغيّر أي توصية.",
         "- الاختبار على **اتجاه** السهم/المؤشر فقط (لا مسار العقد ولا تكلفته). فاصل 95% بإعادة معاينة التواريخ؛ فاصل يشمل الصفر = لا ميزة مثبتة.",
         "- ⚠️ ما يظهر «ميزة» عند اختبار عدة قواعد قد يكون صدفة؛ لا ندخل قاعدة في ظل إلا بفرضية مسجّلة مسبقاً.", ""] + notes + [""]
    L += [f"## الشركات (أفق {H} أيام، عائد مُزال من متوسط القائمة)", ""]
    if R is None or len(R) == 0:
        L.append("لا بيانات.")
    else:
        L += [f"- ملاحظات: {len(R)} في {R['date'].nunique()} تاريخ، {R['t'].nunique()} سهماً.", "",
              "| القاعدة | حالات | إصابة | متوسط الاتجاه | فاصل 95% (%) |", "|---|---|---|---|---|"]
        for name, col, th in STOCK_RULES:
            L.append(f"| {name} | " + fmt(evaluate(R, col, th)) + " |")
        sp = quintile_spread(R)
        if sp:
            L += ["", f"- فرق أعلى 20% وأدنى 20% من نقاط البوت لكل تاريخ: {100 * sp['mean']:+.3f}% (فاصل 95%: {100 * sp['ci'][0]:+.2f}…{100 * sp['ci'][1]:+.2f})، "
                  f"تواريخ رابحة {100 * sp['win']:.0f}% من {sp['n']}؛ النصف الأول {100 * sp['first']:+.3f}% والثاني {100 * sp['second']:+.3f}%."]
    L += ["", "## المؤشر SPY (من إغلاق 10:30)", ""]
    if X is None or len(X) == 0:
        L.append("لا بيانات.")
    else:
        L += [f"- أيام: {len(X)} من {X.index.min().date()} إلى {X.index.max().date()}.", "",
              "| القاعدة | حتى الإغلاق: حالات / إصابة / متوسط / فاصل | إغلاق الغد: حالات / إصابة / متوسط / فاصل |", "|---|---|---|"]
        for name, sig in index_signals(X).items():
            L.append(f"| {name} | " + fmt(index_stat(sig, X["rest"]), " / ") + " | " + fmt(index_stat(sig, X["next"]), " / ") + " |")
    return L


# ============================ البيانات والتشغيل ============================
def load_stocks():
    import yfinance as yf
    import stocks_bot as sb
    tick = list(sb.WATCHLIST)
    raw = yf.download(tick + ["SPY"], period=f"{YEARS}y", interval="1d", group_by="ticker", auto_adjust=True, progress=False, threads=True)
    D = {}
    for t in tick + ["SPY"]:
        try:
            d = raw[t].dropna(subset=["Close"])
            if len(d) > 300:
                D[t] = d
        except Exception:  # noqa: BLE001
            pass
    return D, [t for t in tick if t in D], sb.tech_points


def load_index():
    import yfinance as yf
    h = yf.download("SPY", period="730d", interval="1h", auto_adjust=False, progress=False)
    h.columns = [c[0] if isinstance(c, tuple) else c for c in h.columns]
    h.index = h.index.tz_convert("America/New_York")
    d = yf.download(["SPY", "^VIX"], period="15y", interval="1d", auto_adjust=False, progress=False, group_by="ticker")
    return h, {"spy": d["SPY"]["Close"].dropna(), "vix": d["^VIX"]["Close"].dropna()}


def main(argv):
    notes, R, X = [], None, None
    if "--no-fetch" not in argv:
        try:
            D, tick, tp = load_stocks()
            R = stock_rows(D, tick, tp)
        except Exception as e:  # noqa: BLE001
            notes.append(f"- تعذر قسم الشركات: {type(e).__name__}")
        for attempt in (1, 2):       # ياهو يرجع أحياناً بيانات ناقصة عند الضغط: نعيد المحاولة مرة
            try:
                h, daily = load_index()
                X = index_frame(h, daily)
            except Exception as e:  # noqa: BLE001
                X = None
                notes.append(f"- تعذر قسم المؤشر (محاولة {attempt}): {type(e).__name__}")
            if X is not None and len(X):
                break
            time.sleep(5)
        if X is None or len(X) == 0:
            notes.append("- قسم المؤشر بلا أيام بعد دمج ياهو الساعي واليومي هذه المرة (بيانات ناقصة)؛ يُعاد في التشغيل التالي.")
    L = report(R, X, notes)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
