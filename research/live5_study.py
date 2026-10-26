#!/usr/bin/env python3
"""H-LIVE5: هل نحوّل بوت SPX إلى شموع لحظية وقرار كل 5 دقائق؟ (اختبار تاريخي؛ مسجّل في CLAUDE.md قبل الكود، 27/10/2026).

ثلاث نسخ من نقاط اتجاه البوت (options_bot.direction_points نفسها، بمكوّنات الشموع فقط):
  A القديم:  فحص :05/:35، كل الشموع متأخرة 15 دقيقة (كما يراها البوت من ياهو).
  C ضابط:    فحص :05/:35، شموع حديثة (أثر التأخير وحده = C − A).
  B الجديد:  فحص كل 5 دقائق، شموع SPX بالذيل المستكمل (live_bars.extend: ياهو حتى t−15 + SPY × النسبة)،
             وVWAP وVIX ما زالا من ياهو المتأخر (كما في الوضع 1 الحقيقي).
الدخول بسعر SPX الحقيقي لحظة القرار؛ المقياس حركة SPX في اتجاه الإشارة بعد 30 و60 دقيقة.
(a) دقة الإعادة: اتجاه النقاط بالذيل المستكمل مقابل الشموع الحقيقية (بالمدخلات الأخرى نفسها). (b) لا ضرر: B − A ليس سالباً بفاصل 95%.
بوت جزئي: بلا الجدران وGEX ونقطة الألم وES والقياديات والكول/البوت (لا تاريخ لها). حركة المؤشر ليست ربح العقد.
تشغيل: python research/live5_study.py   المخرج: research/live5_study_results.md
"""
import os
import sys
from datetime import datetime, time as dtime, timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import live_bars  # noqa: E402
import options_bot as ob  # noqa: E402

OUT = os.path.join(HERE, "live5_study_results.md")
DELAY = timedelta(minutes=15)
BAR = timedelta(minutes=5)
START, END = ob.WINDOW_START, ob.NO_NEW_ENTRY
REPEAT = timedelta(minutes=ob.REPEAT_MINUTES)
AGREE_MIN, PRICE_ERR_MAX = 0.95, 0.0005


# ============================ بناء نقاط البوت عند لحظة ============================
def upto(df, cut):
    """الشموع المكتملة قبل cut (فهرس الشمعة = بدايتها)."""
    return df[df.index < cut]


def last5d(ix, day):
    days = sorted(set(ix.index.date))
    keep = [d for d in days if d <= day][-5:]
    return ix[np.isin(ix.index.date, keep)]


def vwap_info(spy, day, S_ref):
    fl = spy[spy.index.date == day]
    if fl.empty or fl["Volume"].sum() <= 0:
        return None, None
    tp = (fl["High"] + fl["Low"] + fl["Close"]) / 3
    vwap = float((tp * fl["Volume"]).cumsum().iloc[-1] / fl["Volume"].cumsum().iloc[-1])
    return vwap * S_ref / float(fl["Close"].iloc[-1]), float(fl["Close"].iloc[-1]) > vwap


def vix_day(vix, day):
    v = vix[vix.index.date == day]["Close"].dropna()
    return float(v.iloc[-1] / v.iloc[0] - 1) if len(v) >= 3 else None


def points(n, ix_spx, spy_for_vwap, vix_bars, daily_prev, S_vwap_ref=None, above_from_S=False):
    """(bull, bear, S) من شموع حتى لحظة القرار. daily_prev: إغلاقات الأيام السابقة (يُضاف إليها السعر الحالي كما في البوت)."""
    f = ob.bar_fields(ix_spx, n)
    S = f["S"]
    d = pd.concat([daily_prev, pd.Series([S])], ignore_index=True)
    vwap_spx, above = vwap_info(spy_for_vwap, n.date(), S_vwap_ref if S_vwap_ref is not None else S)
    if above_from_S and vwap_spx is not None:
        above = S > vwap_spx
    c = {"n": n, "S": S, "sma20": float(d.rolling(20).mean().iloc[-1]), "sma50": float(d.rolling(50).mean().iloc[-1]),
         "above_vwap": True if above is None else bool(above), "vwap_spx": vwap_spx, "map": None, "max_pain": None,
         "es": None, "leaders": None, "pc": None, "vix_day": vix_day(vix_bars, n.date())}
    c.update({k: f[k] for k in ob.BAR_KEYS if k != "S"})
    bull, bear, _ = ob.direction_points(c)
    return bull, bear, S


def evaluate_day(day, spx, spy, vix, daily):
    """لكل لحظة قرار في اليوم: نقاط النسخ الأربع (A، C، B، والحقيقية بمدخلات B) والسعر الحقيقي."""
    tz = spx.index.tz
    prev_close = daily[daily.index.date < day]
    rows = []
    t = datetime.combine(day, dtime(*START), tzinfo=tz) + BAR
    t_end = datetime.combine(day, dtime(*END), tzinfo=tz)
    sx, sy, vx = last5d(spx, day), last5d(spy, day), last5d(vix, day)
    while t <= t_end:
        fresh, late = upto(sx, t), upto(sx, t - DELAY)
        if fresh.empty or fresh.index[-1].date() != day or late.empty:
            t += BAR
            continue
        spy_f, spy_l, vix_f, vix_l = upto(sy, t), upto(sy, t - DELAY), upto(vx, t), upto(vx, t - DELAY)
        recon, info = live_bars.extend(late, spy_f[spy_f.index.date == day], t)
        S_late = float(late["Close"].iloc[-1])
        A = points(t, late, spy_l, vix_l, prev_close)
        C = points(t, fresh, spy_f, vix_f, prev_close)
        B = points(t, recon, spy_l, vix_l, prev_close, S_vwap_ref=S_late, above_from_S=True) if info["status"] == "ok" else None
        T = points(t, fresh, spy_l, vix_l, prev_close, S_vwap_ref=S_late, above_from_S=True)      # الحقيقة بمدخلات B نفسها
        rows.append({"t": t, "day": day, "P": float(fresh["Close"].iloc[-1]), "A": A, "C": C, "B": B, "T": T, "status": info["status"]})
        t += BAR
    return rows


# ============================ الإشارات والنتائج ============================
def signals(rows, key, cadence30):
    """إشارات نسخة بمنع تكرار الاتجاه 90 دقيقة. cadence30: فحص :05/:35 فقط."""
    out, last = [], {}
    for r in rows:
        if cadence30 and r["t"].minute not in (5, 35):
            continue
        v = r[key]
        if v is None:
            continue
        bull, bear, _ = v
        if abs(bull - bear) < ob.MIN_EDGE:
            continue
        side = 1 if bull > bear else -1
        if side in last and r["t"] - last[side] < REPEAT and last.get("day") == r["day"]:
            continue
        if last.get("day") != r["day"]:
            last = {"day": r["day"]}
        last[side] = r["t"]
        out.append({"t": r["t"], "day": r["day"], "side": side, "P": r["P"]})
    return out


def forward(events, price):
    """يضيف حركة 30 و60 دقيقة في اتجاه الإشارة من السعر الحقيقي (إغلاق الشمعة المنتهية عند t+30 / t+60)."""
    for e in events:
        for m in (30, 60):
            k = e["t"] + timedelta(minutes=m) - BAR
            p = price.get(k)
            e[f"f{m}"] = None if p is None else e["side"] * (p / e["P"] - 1)
    return events


def boot_mean(by_day, n=3000, seed=11):
    days = sorted(by_day)
    if len(days) < 8:
        return None
    S = np.array([sum(by_day[d]) for d in days])
    N = np.array([len(by_day[d]) for d in days])
    idx = np.random.default_rng(seed).integers(0, len(days), (n, len(days)))
    m = S[idx].sum(1) / np.maximum(N[idx].sum(1), 1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def stat(events, key):
    by = {}
    for e in events:
        if e.get(key) is not None:
            by.setdefault(e["day"], []).append(e[key])
    vals = [x for v in by.values() for x in v]
    return {"n": len(vals), "days": len(by), "mean": float(np.mean(vals)) if vals else None, "ci": boot_mean(by)}


def diff_ci(ev_b, ev_a, key, n=3000, seed=13):
    """فاصل 95% لفرق المتوسطين (B − A) بإعادة معاينة الأيام نفسها للنسختين."""
    days = sorted({e["day"] for e in ev_a + ev_b})
    if len(days) < 8:
        return None
    def sums(ev):
        s, c = {d: 0.0 for d in days}, {d: 0 for d in days}
        for e in ev:
            if e.get(key) is not None:
                s[e["day"]] += e[key]
                c[e["day"]] += 1
        return np.array([s[d] for d in days]), np.array([c[d] for d in days])
    sb, cb = sums(ev_b)
    sa, ca = sums(ev_a)
    idx = np.random.default_rng(seed).integers(0, len(days), (n, len(days)))
    d = sb[idx].sum(1) / np.maximum(cb[idx].sum(1), 1) - sa[idx].sum(1) / np.maximum(ca[idx].sum(1), 1)
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5)), float(d.mean())


def accuracy(rows):
    """(a): اتفاق اتجاه النقاط بين الذيل المستكمل والحقيقة في الفحوص التي فيها إشارة لأي منهما، ووسيط خطأ السعر."""
    both = [r for r in rows if r["B"] is not None]
    sig = [r for r in both if abs(r["B"][0] - r["B"][1]) >= ob.MIN_EDGE or abs(r["T"][0] - r["T"][1]) >= ob.MIN_EDGE]
    agree = np.mean([(r["B"][0] > r["B"][1]) == (r["T"][0] > r["T"][1]) and
                     (abs(r["B"][0] - r["B"][1]) >= ob.MIN_EDGE) == (abs(r["T"][0] - r["T"][1]) >= ob.MIN_EDGE) for r in sig]) if sig else None
    err = float(np.median([abs(r["B"][2] / r["T"][2] - 1) for r in both])) if both else None
    return {"checks": len(both), "sig_checks": len(sig), "agree": agree, "price_err": err,
            "fallback": sum(1 for r in rows if r["status"] != "ok"), "total": len(rows)}


def pct(x, d=2):
    return "-" if x is None else f"{100 * x:+.{d}f}%"


def ci_txt(ci):
    return "-" if not ci else f"{100 * ci[0]:+.3f}…{100 * ci[1]:+.3f}%"


def report(rows, ev, acc, meta):
    L = ["# H-LIVE5: شموع لحظية وقرار كل 5 دقائق لبوت SPX (اختبار تاريخي، نتائج آلية)", "", f"- {meta}",
         "- بوت جزئي: نقاط الاتجاه من مكوّنات الشموع فقط (بلا الجدران وGEX ونقطة الألم وES والقياديات والكول/البوت). حركة المؤشر ليست ربح العقد (تكلفة ATM 0DTE الفعلية نحو 3.6% للصفقة).", "",
         "## (a) دقة الذيل المستكمل (SPY × النسبة) مقابل شموع SPX الحقيقية", "",
         f"- فحوص: {acc['checks']}، منها بإشارة لأي منهما: {acc['sig_checks']}؛ رجوع لياهو: {acc['fallback']} من {acc['total']}.",
         f"- اتفاق الاتجاه وقرار الإشارة: {pct(acc['agree'], 1).lstrip('+')}؛ وسيط خطأ السعر: {100 * acc['price_err']:.3f}%." if acc["agree"] is not None else "- لا فحوص كافية.", ""]
    ok_a = acc["agree"] is not None and acc["agree"] >= AGREE_MIN and acc["price_err"] is not None and acc["price_err"] <= PRICE_ERR_MAX
    L.append(f"⚖️ H-LIVE5a: {'تحققت' if ok_a else 'لم تتحقق'} (الحد: اتفاق ≥ {AGREE_MIN:.0%} وخطأ سعر ≤ {100 * PRICE_ERR_MAX:.2f}%)")
    L += ["", "## النسخ الثلاث: حركة SPX في اتجاه الإشارة", "",
          "| النسخة | إشارات | أيام | إشارات/يوم | حركة 30د | فاصل 95% | حركة 60د | فاصل 95% |", "|---|---|---|---|---|---|---|---|"]
    ndays = len({r["day"] for r in rows}) or 1
    names = {"A": "A القديم (:05/:35، متأخر 15د)", "C": "C ضابط (:05/:35، حديث)", "B": "B الجديد (كل 5د، ذيل لحظي)"}
    for k in ("A", "C", "B"):
        s30, s60 = stat(ev[k], "f30"), stat(ev[k], "f60")
        L.append(f"| {names[k]} | {len(ev[k])} | {s30['days']} | {len(ev[k]) / ndays:.1f} | {pct(s30['mean'], 3)} | {ci_txt(s30['ci'])} | {pct(s60['mean'], 3)} | {ci_txt(s60['ci'])} |")
    d = diff_ci(ev["B"], ev["A"], "f30")
    dc = diff_ci(ev["C"], ev["A"], "f30")
    L += ["", f"- الفرق B − A (حركة 30د): {'-' if not d else pct(d[2], 3)} بفاصل {ci_txt(d)}",
          f"- أثر التأخير وحده C − A (حركة 30د): {'-' if not dc else pct(dc[2], 3)} بفاصل {ci_txt(dc)}", ""]
    harm = d is not None and d[1] < 0
    L.append(f"⚖️ H-LIVE5b: {'مرفوضة: النسخة الجديدة أسوأ' if harm else 'لا ضرر (الفرق ليس سالباً بفاصل 95%)' if d else 'لا حكم (أيام قليلة)'}")
    build = ok_a and d is not None and not harm
    L += ["", f"⚖️ القرار المسجّل مسبقاً: {'يُبنى القرار كل 5 دقائق بالشموع اللحظية' if build else 'لا يُبنى؛ يُعرض على المستخدم'}", ""]
    return L, build


def load():
    import yfinance as yf
    spx = yf.Ticker("^SPX").history(period="60d", interval="5m")
    spy = yf.Ticker("SPY").history(period="60d", interval="5m")
    vix = yf.Ticker("^VIX").history(period="60d", interval="5m")
    daily = yf.Ticker("^SPX").history(period="2y")["Close"].dropna()
    return spx, spy, vix, daily


def run(spx, spy, vix, daily):
    days = sorted(set(spx.index.date))[5:]          # أول 5 أيام للإحماء (نافذة البوت 5 أيام)
    rows = []
    for d in days:
        rows += evaluate_day(d, spx, spy, vix, daily)
    price = {r["t"] - BAR: r["P"] for r in rows}
    # أسعار كل الشموع (للنتائج بعد 15:00)
    price.update({k: float(v) for k, v in spx["Close"].items()})
    ev = {"A": forward(signals(rows, "A", True), price), "C": forward(signals(rows, "C", True), price),
          "B": forward(signals(rows, "B", False), price)}
    return rows, ev, days


def main(argv):
    spx, spy, vix, daily = load()
    rows, ev, days = run(spx, spy, vix, daily)
    acc = accuracy(rows)
    L, build = report(rows, ev, acc, f"{len(days)} يوم تداول ({days[0]} → {days[-1]})، {len(rows)} لحظة قرار")
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
