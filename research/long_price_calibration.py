#!/usr/bin/env python3
"""معايرة طويلة: ثمن مشتري ATM على SPY بأسعار عقود **حقيقية** (شموع Alpaca) على نحو 600 يوم، مقابل التسعير النظري (قراءة فقط). بطلب المستخدم 24/10/2026.

لماذا: نتائج option_buyer_cost نظرية (بلاك-شولز بتذبذب VIX)، وreal_price_check غطّى 68 يوماً فقط (حد ياهو 60 يوماً لشموع SPX). شموع عقود SPY الحقيقية متاحة من Alpaca منذ فبراير 2024،
وشموع SPY نفسها (8 سنوات) عندنا على الخادم، فنقيس **كلفة الشراء الحقيقية** بلا توقيع إشارات: كل يوم، 4 أوقات دخول (10:30 و11:30 و12:30 و13:30)، عقد ATM كول وبوت،
انتهاء اليوم (0DTE) وغداً (1DTE)، خروج بعد 60 دقيقة. الدخول = افتتاح أول شمعة عقد بعد وقت الدخول × (1 + h)، والخروج = افتتاح أول شمعة بعد 60 دقيقة × (1 − h)، h = 1.5%.
شمعة العقد = صفقات فعلية؛ عقد لم يُتداول عند الدخول أو الخروج (خلال 10 دقائق) يُستبعد (نعرض نسبة الاستبعاد: ATM على SPY سيولته عالية فالنسبة صغيرة).
النظري على العينات نفسها: بلاك-شولز بزمن التداول وتذبذب VIX إغلاق أمس × 1.0. السؤال: كم يبعد الحقيقي عن النظري؟ وأيهما يُعتمد لنسبة التعادل؟
تحفظات: لا إشارة ولا اتجاه (متوسط الكول والبوت = كلفة بلا ميزة)؛ سعر "الصفقة الأخيرة" في الشمعة لا يساوي وسط العرض/الطلب بدقة؛ ثابت h يفترض نصف الفرق الذي قسناه.
تشغيل (الخادم؛ يحتاج ALPACA_KEY/SECRET وملف /opt/spxbot/data/spy_5m.csv.gz): python research/long_price_calibration.py   المخرج: research/long_price_calibration_results.md
"""
import json
import os
import sys
import time as _time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import levels_study as ls  # noqa: E402
import option_buyer_cost as obc  # noqa: E402
import live_quotes as lq  # noqa: E402

OUT = os.path.join(HERE, "long_price_calibration_results.md")
WL_JSON = os.path.join(HERE, "long_price_calibration_wl.json")
START = "2024-03-01"
ENTRIES = obc.ENTRIES
HOLD_BARS = 12
H = 0.015
MAX_GAP_MIN = 10
DAYS_PER_CALL = 5


# ============================ دوال نقية ============================
def next_day(d, holidays=()):
    n = d + timedelta(days=1)
    while n.weekday() >= 5 or n.isoformat() in holidays:
        n += timedelta(days=1)
    return n


def plan_day(day, c, t, vix_prev, holidays=()):
    """عينات يوم: قائمة dict(day, i, dte, side, strike, sym, t0, t1, s0, s1, mins_to_close, vix). c: إغلاقات 5د (78)، t: أوقاتها (نيويورك)."""
    out = []
    for i in ENTRIES:
        if i + HOLD_BARS >= len(c):
            continue
        strike = float(round(c[i]))
        t0 = t[i] + pd.Timedelta(minutes=5)           # بداية الشمعة التالية لإغلاق شمعة الدخول
        t1 = t0 + pd.Timedelta(minutes=60)
        for dte in (0, 1):
            exp = day if dte == 0 else next_day(day, holidays)
            for side in ("CALL", "PUT"):
                out.append({"day": pd.Timestamp(day), "i": i, "dte": dte, "side": side, "strike": strike,
                            "sym": lq.occ("SPY", exp.isoformat(), side, strike), "t0": t0, "t1": t1, "s0": float(c[i]), "s1": float(c[i + HOLD_BARS]),
                            "mins_to_close": 16 * 60 - (9 * 60 + 35 + 5 * i), "vix": vix_prev})
    return out


def first_open_after(bars, t, max_gap_min=MAX_GAP_MIN):
    """افتتاح أول شمعة بدايتها ≥ t وخلال max_gap_min، أو None. bars: DataFrame فهرسه أوقات نيويورك وعمود o."""
    if bars is None or len(bars) == 0:
        return None
    sub = bars[(bars.index >= t) & (bars.index <= t + pd.Timedelta(minutes=max_gap_min))]
    return float(sub["o"].iloc[0]) if len(sub) else None


def real_return(sample, bars):
    p0, p1 = first_open_after(bars, sample["t0"]), first_open_after(bars, sample["t1"])
    if not p0 or not p1 or p0 <= 0:
        return None
    return (p1 * (1 - H)) / (p0 * (1 + H)) - 1.0


def theory_return(sample):
    T0 = (sample["mins_to_close"] + sample["dte"] * obc.SESSION_MIN) / obc.TRADING_MIN_YEAR
    T1 = max(T0 - 60.0 / obc.TRADING_MIN_YEAR, 1e-7)
    sig = sample["vix"] / 100.0
    is_call = sample["side"] == "CALL"
    p0 = max(float(obc.bs(sample["s0"], sample["strike"], T0, sig, is_call)), obc.MIN_PRICE)
    p1 = max(float(obc.bs(sample["s1"], sample["strike"], T1, sig, is_call)), 0.0)
    return (p1 * (1 - H)) / (p0 * (1 + H)) - 1.0


def pair_up(rows):
    """يجمع الكول والبوت لنفس (يوم، وقت، دفعة) إلى عينة عشوائية = متوسطهما. rows: dict(day, i, dte, side, real, theory, up)."""
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    wide = df.pivot_table(index=["day", "i", "dte"], columns="side", values=["real", "theory"], aggfunc="first").dropna()
    out = pd.DataFrame({"real_call": wide[("real", "CALL")], "real_put": wide[("real", "PUT")],
                        "th_call": wide[("theory", "CALL")], "th_put": wide[("theory", "PUT")]}).reset_index()
    ups = df.drop_duplicates(["day", "i", "dte"]).set_index(["day", "i", "dte"])["up"]
    out["up"] = [bool(ups.loc[(r.day, r.i, r.dte)]) for r in out.itertuples()]
    out["real"] = (out["real_call"] + out["real_put"]) / 2
    out["theory"] = (out["th_call"] + out["th_put"]) / 2
    return out


def boot_mean(by_day, n=1500, seed=3):
    df = pd.DataFrame({"day": by_day.index, "v": by_day.to_numpy()}).groupby("day")["v"].agg(["sum", "count"])
    if len(df) < 10:
        return None
    S, N = df["sum"].to_numpy(), df["count"].to_numpy()
    idx = np.random.default_rng(seed).integers(0, len(S), (n, len(S)))
    m = S[idx].sum(1) / N[idx].sum(1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def summarize(P):
    right = np.where(P["up"], P["real_call"], P["real_put"])
    wrong = np.where(P["up"], P["real_put"], P["real_call"])
    ci = boot_mean(pd.Series(P["real"].to_numpy(), index=P["day"].to_numpy()))
    return {"n": len(P), "days": int(P["day"].nunique()), "real": float(P["real"].mean()), "theory": float(P["theory"].mean()), "ci": ci,
            "W": float(right.mean()), "L": float(wrong.mean()), "be": obc.breakeven(float(right.mean()), float(wrong.mean()))}


def wl_table(P):
    """{"ATM 0DTE": {"W":..,"L":..,"be":..,"random":..,"n":..}, ...} لكل الأوقات، تقرؤه account_erosion_sim وstrategy_sim."""
    out = {}
    for dte in (0, 1):
        sub = P[P["dte"] == dte] if len(P) else P
        if len(sub) >= 100:
            s = summarize(sub)
            out[f"ATM {dte}DTE"] = {"W": s["W"], "L": s["L"], "be": s["be"], "random": s["real"], "n": s["n"]}
    return out


def report(P, stats, meta):
    L = ["# معايرة طويلة: كلفة المشتري بأسعار عقود SPY الحقيقية (نتائج آلية)", "", f"- {meta}",
         "- قراءة فقط. ATM بلا إشارة: متوسط الكول والبوت = كلفة الشراء بلا ميزة. الدخول بالصفقة الأولى بعد الوقت + نصف فرق 1.5%، الخروج بعد 60 دقيقة − 1.5%.",
         f"- عينات مستبعدة لغياب صفقات: {stats.get('dropped', 0)} من {stats.get('total', 0)} (عقد لم يُتداول عند الدخول/الخروج).", ""]
    if len(P) == 0:
        return L + ["لا عينات."]
    L += ["| الانتهاء | وقت الدخول | عينات | أيام | حقيقي: متوسط عشوائي | فاصل 95% | نظري | حقيقي − نظري | عند الإصابة W | عند الخطأ L | نسبة التعادل الحقيقية |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for dte in (0, 1):
        for label, sub in [("كل الأوقات", P[P["dte"] == dte])] + [(f"{(9 * 60 + 35 + 5 * i) // 60}:{(9 * 60 + 35 + 5 * i) % 60:02d}", P[(P["dte"] == dte) & (P["i"] == i)]) for i in ENTRIES]:
            if len(sub) < 30:
                continue
            s = summarize(sub)
            ci = "-" if s["ci"] is None else f"{100 * s['ci'][0]:+.1f}…{100 * s['ci'][1]:+.1f}"
            be = "-" if s["be"] is None else f"{100 * s['be']:.1f}%"
            L.append(f"| {dte}DTE | {label} | {s['n']} | {s['days']} | {100 * s['real']:+.1f}% | {ci} | {100 * s['theory']:+.1f}% | {100 * (s['real'] - s['theory']):+.1f} نقطة | {100 * s['W']:+.1f}% | {100 * s['L']:+.1f}% | {be} |")
    L += ["", "- القراءة: إن كان «حقيقي − نظري» موجباً فالنظري يبالغ في كلفة الشراء (VIX أعلى من التذبذب الفعلي الضمني أو أن الفرق أقل)، وتُعتمد نسبة التعادل الحقيقية العمود الأخير."]
    return L


# ============================ التشغيل ============================
def main(argv):
    path = ls.DATA
    if not os.path.exists(path) or not os.getenv("ALPACA_KEY"):
        print("يلزم ملف SPY 5د ومفاتيح Alpaca (الخادم).")
        return 0
    import stocks_contract_replay as scr
    import options_bot as ob
    d = ls.load(path)
    vix = obc.load_vix()
    samples = []
    for day, g in d.groupby(d["t"].dt.date):
        if str(day) < START or ls.trim_half_day(g)[1] or len(g) < 78:
            continue
        v = vix.get(pd.Timestamp(day))
        if v is None or not np.isfinite(v):
            continue
        samples += plan_day(day, g["c"].to_numpy(float), list(g["t"]), float(v), ob.HOLIDAYS)
    total, rows, dropped = len(samples), [], 0
    by_day = {}
    for s in samples:
        by_day.setdefault(s["day"], []).append(s)
    days = sorted(by_day)
    for k in range(0, len(days), DAYS_PER_CALL):
        chunk = days[k:k + DAYS_PER_CALL]
        syms = [s["sym"] for dd in chunk for s in by_day[dd]]
        start = (chunk[0].tz_localize("America/New_York") + pd.Timedelta(hours=9)).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
        end = (chunk[-1].tz_localize("America/New_York") + pd.Timedelta(hours=17)).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            bars = scr.fetch_bars(syms, start, end)
        except Exception as e:  # noqa: BLE001
            print("تعذر جلب دفعة:", type(e).__name__)
            dropped += len(syms)
            continue
        for dd in chunk:
            for s in by_day[dd]:
                r = real_return(s, bars.get(s["sym"]))
                if r is None:
                    dropped += 1
                    continue
                rows.append({"day": s["day"], "i": s["i"], "dte": s["dte"], "side": s["side"], "real": r, "theory": theory_return(s), "up": s["s1"] > s["s0"]})
        _time.sleep(0.2)
    P = pair_up(rows)
    meta = f"{len(days)} يوماً مرشحاً من {START}، {len(P)} عينة مزدوجة (كول+بوت)"
    L = report(P, {"dropped": dropped, "total": total}, meta)
    with open(WL_JSON, "w", encoding="utf-8", newline="\n") as f:
        json.dump(wl_table(P), f, ensure_ascii=False, indent=1)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
