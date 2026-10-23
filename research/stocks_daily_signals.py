#!/usr/bin/env python3
"""دراسات يومية لإشارات شركات البوت (قراءة فقط، لا تغيّر أي توصية). بطلب المستخدم 24/10/2026: «نختبرها كلها».

H-SGAP — فجوة السهم: لأسهم قائمة البوت 7 سنوات يومية، فجوة |≥2%| (افتتاح اليوم ÷ إغلاق أمس − 1) × عائد الافتتاح→الإغلاق مطروحاً منه عائد SPY في اليوم نفسه.
  **مسجّلة مسبقاً (24/10/2026):** الأثر ≠ 0 بفاصل 95% يستثني الصفر (ثنائي الاتجاه: نسجّل إشارته، استمرار أو انعكاس). عناقيد = اليوم (الفجوات تتجمع في أيام السوق).
  الحد الأدنى للأحداث 200 (يُحدَّد هنا قبل التشغيل). الأسعار غير معدلة (الفجوة تتضمن هبوط توزيع الأرباح؛ صغير مقابل عتبة 2%).
H-OIL — النفط يقود XOM/CVX؟ حركة نفط CL=F خلال 5 أيام |≥4%| (أحداث مستقلة بفارق 5 أيام فأكثر) × عائد XOM وCVX المُزال منه SPY في الأيام الخمسة التالية (من إغلاق يوم الإشارة).
  **مسجّلة مسبقاً:** الأثر > 0 بفاصل يستثني الصفر (يبرر نقطة «المحرك» في البوت). عناقيد شهرية. الحد الأدنى 30 حدثاً مستقلاً (يُحدَّد هنا قبل التشغيل).
تدفق الخيارات (استكشافي بلا حكم): نسبة call/put المسجّلة حياً في stocks_flow_log.csv × عائد الإغلاق التالي المُزال؛ العينة صغيرة (أسابيع) فلا يُعتمد.
تشغيل: python research/stocks_daily_signals.py   المخرج: research/stocks_daily_signals_results.md
"""
import os
import sys
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
import pead_study as ps  # noqa: E402  (cluster_ci)

OUT = os.path.join(HERE, "stocks_daily_signals_results.md")
FLOW = os.path.join(ROOT, "stocks_flow_log.csv")
GAP_MIN = 0.02
MIN_GAP_EVENTS = 200
OIL_MOVE = 0.04
OIL_DAYS = 5
MIN_OIL_EVENTS = 30
OIL_TICKERS = ("XOM", "CVX")
HORIZON = 5


# ============================ دوال نقية ============================
def gap_events(ticker, o, c, spy_o, spy_c, thr=GAP_MIN):
    """أحداث الفجوة لسهم: عائد الافتتاح→الإغلاق مطروحاً SPY، بعد فجوة |≥thr|. كل المدخلات Series بفهرس تاريخ."""
    df = pd.DataFrame({"o": o, "c": c, "so": spy_o, "sc": spy_c}).dropna()
    df["gap"] = df["o"] / df["c"].shift(1) - 1
    df["ar"] = (df["c"] / df["o"] - 1) - (df["sc"] / df["so"] - 1)
    ev = df[df["gap"].abs() >= thr].dropna(subset=["gap", "ar"])
    return [{"ticker": ticker, "date": d, "gap": float(r.gap), "ar": float(r.ar), "cluster": str(d.date())} for d, r in ev.iterrows()]


def independent(flags, gap=OIL_DAYS):
    keep, last = np.zeros(len(flags), dtype=bool), -10 ** 9
    for i, f in enumerate(np.asarray(flags)):
        if f and i - last >= gap:
            keep[i] = True
            last = i
    return keep


def oil_events(oil, stocks, spy):
    """أحداث النفط المستقلة: dict(date، oil5، ar لكل سهم موجود). oil/spy: Series إغلاق؛ stocks: {رمز: Series إغلاق}."""
    ok = oil.where(oil > 5)
    o5 = ok.pct_change(OIL_DAYS)
    o5 = o5.where(o5.abs() <= 0.5)
    flag = (o5.abs() >= OIL_MOVE).fillna(False).to_numpy()
    keep = independent(flag)
    out = []
    spy_f = spy.shift(-HORIZON) / spy - 1
    for i in np.flatnonzero(keep):
        d = o5.index[i]
        if d not in spy_f.index or not np.isfinite(spy_f.loc[d]):
            continue
        for t, s in stocks.items():
            if d not in s.index:
                continue
            f = s.shift(-HORIZON).loc[d] / s.loc[d] - 1
            if np.isfinite(f):
                out.append({"ticker": t, "date": d, "oil5": float(o5.iloc[i]), "ar": float(f - spy_f.loc[d]), "cluster": f"{d.year}-{d.month:02d}"})
    return out


def stat(sign, ar, clusters):
    v = np.sign(np.asarray(sign, dtype=float)) * np.asarray(ar, dtype=float)
    m = np.isfinite(v) & (np.sign(np.asarray(sign, dtype=float)) != 0)
    v, cl = v[m], np.asarray(clusters)[m]
    if len(v) == 0:
        return None
    return {"n": len(v), "hit": float((v > 0).mean()), "mean": float(v.mean()), "ci": ps.cluster_ci(v, cl)}


def fmt(s):
    if s is None:
        return "- | - | - | -"
    ci = "-" if s["ci"] is None else f"{100 * s['ci'][0]:+.3f}…{100 * s['ci'][1]:+.3f}"
    return f"{s['n']} | {100 * s['hit']:.0f}% | {100 * s['mean']:+.3f}% | {ci}"


def judge(name, s, min_n, two_sided):
    if s is None or s["n"] < min_n:
        return f"{name}: لا حكم بعد ({0 if s is None else s['n']} من {min_n} حدثاً)"
    if s["ci"] is None:
        return f"{name}: لا حكم بعد (عناقيد قليلة)"
    lo, hi = s["ci"]
    desc = f"متوسط {100 * s['mean']:+.3f}%، فاصل {100 * lo:+.3f}…{100 * hi:+.3f}"
    if lo > 0:
        return f"{name}: تحققت ({desc})" if not two_sided else f"{name}: تحققت، الفجوة تستمر ({desc})"
    if hi < 0:
        return f"{name}: عكسية ({desc})" if not two_sided else f"{name}: تحققت، الفجوة تنعكس ({desc})"
    return f"{name}: لم تتحقق ({desc} يشمل الصفر)"


def flow_section(flow, close):
    """استكشافي: لوغاريتم نسبة call/put × عائد الإغلاق التالي المُزال. flow: DataFrame (time, ticker, ratio)؛ close: {رمز: Series} وفيه SPY."""
    if flow is None or len(flow) == 0 or "SPY" not in close:
        return ["- لا سجل تدفق كافٍ بعد."]
    f = flow.copy()
    f["date"] = pd.to_datetime(f["time"].str[:10])
    f = f.groupby(["date", "ticker"], as_index=False)["ratio"].last()
    rows = []
    spy_n = close["SPY"].shift(-1) / close["SPY"] - 1
    for _, r in f.iterrows():
        s = close.get(r["ticker"])
        if s is None or r["date"] not in s.index or r["ratio"] <= 0:
            continue
        n = s.shift(-1).loc[r["date"]] / s.loc[r["date"]] - 1
        if np.isfinite(n) and np.isfinite(spy_n.loc[r["date"]]):
            rows.append((np.log(r["ratio"]), n - spy_n.loc[r["date"]], str(r["date"].date())))
    if len(rows) < 30:
        return [f"- {len(rows)} ملاحظة فقط (يلزم 30 على الأقل): لا يُقرأ منها شيء بعد."]
    x, y, cl = (np.array(z) for z in zip(*rows))
    hi = stat(np.where(x > np.log(1.5), 1, np.where(x < np.log(1 / 1.5), -1, 0)), y, cl)
    corr = float(np.corrcoef(x, y)[0, 1])
    return [f"- {len(rows)} ملاحظة: ارتباط لوغاريتم النسبة بعائد الغد المُزال {corr:+.3f}",
            f"- إشارة (نسبة > 1.5 صعود، < 0.67 هبوط) × عائد الغد المُزال: {fmt(hi)}"]


def report(gaps, oil, flow_lines, meta):
    L = ["# إشارات يومية لشركات البوت: الفجوة والنفط (نتائج آلية)", "", f"- {meta}",
         "- قراءة فقط ولا تغيّر أي توصية. العوائد مُزالاً منها SPY. فاصل 95% بإعادة معاينة العناقيد.",
         f"- **الفرضيات المسجّلة مسبقاً:** H-SGAP: أثر الفجوة ≠ 0 بعد {MIN_GAP_EVENTS} حدثاً؛ H-OIL: النفط × عائد XOM/CVX التالي > 0 بعد {MIN_OIL_EVENTS} حدثاً مستقلاً.", ""]
    G = pd.DataFrame(gaps)
    O = pd.DataFrame(oil)
    L += ["| الإشارة × العائد | أحداث | ربح الإشارة | متوسط | فاصل 95% (%) |", "|---|---|---|---|---|"]
    if len(G):
        L.append(f"| **H-SGAP** فجوة ≥2% × عائد الافتتاح→الإغلاق | {fmt(stat(G['gap'], G['ar'], G['cluster']))} |")
        big = G[G["gap"].abs() >= 0.05]
        L.append(f"| (استكشافي) فجوة ≥5% | {fmt(stat(big['gap'], big['ar'], big['cluster']))} |")
        L.append(f"| (استكشافي) فجوة هابطة فقط | {fmt(stat(-G[G['gap'] < 0]['gap'].abs(), G[G['gap'] < 0]['ar'] * -1, G[G['gap'] < 0]['cluster']))} |")
        h = G["date"].median()
        for name, part in (("النصف الأول", G[G["date"] <= h]), ("النصف الثاني", G[G["date"] > h])):
            L.append(f"| {name} | {fmt(stat(part['gap'], part['ar'], part['cluster']))} |")
    if len(O):
        L.append(f"| **H-OIL** حركة النفط 5 أيام × عائد XOM+CVX التالي | {fmt(stat(O['oil5'], O['ar'], O['cluster']))} |")
        for t in OIL_TICKERS:
            p = O[O["ticker"] == t]
            L.append(f"| (استكشافي) {t} وحده | {fmt(stat(p['oil5'], p['ar'], p['cluster']))} |")
    L += ["", "## تدفق الخيارات (استكشافي)", ""] + flow_lines + [""]
    gs = stat(G["gap"], G["ar"], G["cluster"]) if len(G) else None
    os_ = stat(O["oil5"], O["ar"], O["cluster"]) if len(O) else None
    L += ["⚖️ **" + judge("H-SGAP", gs, MIN_GAP_EVENTS, True) + "**", "", "⚖️ **" + judge("H-OIL", os_, MIN_OIL_EVENTS, False) + "**"]
    return L


# ============================ التشغيل ============================
def load(tickers):
    import yfinance as yf
    names = list(dict.fromkeys(list(tickers) + ["SPY", "CL=F"]))
    px = yf.download(names, period="7y", interval="1d", group_by="ticker", auto_adjust=False, progress=False, threads=True)
    out = {}
    for t in names:
        if t in px.columns.get_level_values(0):
            d = px[t].dropna(subset=["Close"]).copy()
            d.index = pd.to_datetime(d.index).tz_localize(None)
            out[t] = d
    return out


def main(argv):
    import stocks_bot as sb
    try:
        data = load(list(sb.WATCHLIST))
    except Exception as e:  # noqa: BLE001
        print("تعذر تحميل البيانات:", type(e).__name__)
        return 0
    spy = data.get("SPY")
    if spy is None:
        print("لا بيانات SPY")
        return 0
    gaps = []
    for t, d in data.items():
        if t in ("SPY", "CL=F"):
            continue
        gaps += gap_events(t, d["Open"], d["Close"], spy["Open"], spy["Close"])
    oil = oil_events(data["CL=F"]["Close"], {t: data[t]["Close"] for t in OIL_TICKERS if t in data}, spy["Close"]) if "CL=F" in data else []
    flow = pd.read_csv(FLOW) if os.path.exists(FLOW) else None
    flow_lines = flow_section(flow, {t: d["Close"] for t, d in data.items()})
    meta = f"{len(data) - 2} سهماً، فجوات {len(gaps)}، أحداث نفط {len(oil)}"
    L = report(gaps, oil, flow_lines, meta)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
