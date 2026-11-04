#!/usr/bin/env python3
"""H-EARNREV: انعكاس يوم رد الفعل بعد الأرباح عبر VWAP ما قبل الافتتاح (مسجّل مسبقاً في CLAUDE.md 4/11/2026). اختبار فقط.

العينة: إعلانات H-EARNPRE (2024-03 → 2026-10)، يوم رد الفعل D0 بفجوة افتتاح ≥ 2%.
الإشارة: أول شمعة 5د تُغلق بين 09:35 و10:30 على الجهة الأخرى من VWAP ما قبل الافتتاح (PMV) مقارنةً بالافتتاح؛
  الدخول بافتتاح الشمعة التالية في اتجاه العبور.
E1: عائد السهم في الاتجاه حتى 15:55. E2: عقد بدلتا ≈0.50، أول جمعة بعد D0 بيومين على الأقل، +50% / −30% / 15:55.
C1 (مقارنة): كل أيام العينة، دخول 10:00 في اتجاه الفجوة، بالقواعد نفسها.
تشغيل (الخادم، ALPACA): SPXBOT_DATA=/opt/spxbot/data python research/earnings_reversal_study.py [--limit N]
المخرج: research/earnings_reversal_results.md و SPXBOT_DATA/earnings_pre/earnings_reversal_rows.csv
"""
import json
import math
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from stocks_contract_replay import cap_end, fetch_bars, first_bar_after, occ, strike_for_delta  # noqa: E402
from stock_contract_test import step_for  # noqa: E402
from exit_plan_test import fridays, path  # noqa: E402

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
EVENTS = os.path.join(DATA, "earnings_pre", "events.csv")
ROWS = os.path.join(DATA, "earnings_pre", "earnings_reversal_rows.csv")
OUT = os.path.join(HERE, "earnings_reversal_results.md")
START, LAST = date(2024, 3, 1), date(2026, 10, 15)
MIN_GAP = 0.02
OPEN_T, SIG_FROM, SIG_TO, C1_T, CUTOFF = dtime(9, 30), dtime(9, 35), dtime(10, 30), dtime(10, 0), dtime(15, 55)
TARGET_D, TP, SL = 0.50, 0.50, -0.30
N_BOOT = 3000


# ====================== دوال صافية ======================
def pm_vwap(m1):
    """VWAP ما قبل الافتتاح (04:00–09:29) من شموع دقيقة (o,h,l,c,v[,vw]) بفهرس نيويورك، أو None."""
    pm = m1[(m1.index.time >= dtime(4, 0)) & (m1.index.time < OPEN_T)]
    if pm.empty or pm["v"].sum() <= 0:
        return None
    px = pm["vw"] if "vw" in pm else (pm["h"] + pm["l"] + pm["c"]) / 3
    return float((px * pm["v"]).sum() / pm["v"].sum())


def regular(m1):
    return m1[(m1.index.time >= OPEN_T) & (m1.index.time < dtime(16, 0))]


def bars5(m1):
    r = regular(m1)
    return r.resample("5min", label="left", closed="left").agg({"o": "first", "h": "max", "l": "min", "c": "last", "v": "sum"}).dropna()


def signal(m1, pmv):
    """(اتجاه +1/−1، وقت الدخول، تصريف؟) أو None. الاتجاه = جهة العبور؛ تصريف = أول 5د ابتعدت عن PMV."""
    b = bars5(m1)
    if b.empty or pmv is None:
        return None
    o0 = float(b["o"].iloc[0])
    if o0 == pmv:
        return None
    below = o0 < pmv
    flush = bool(b["c"].iloc[0] < o0) if below else bool(b["c"].iloc[0] > o0)
    for t, r in b.iterrows():
        end = t + timedelta(minutes=5)
        if end.time() < SIG_FROM:
            continue
        if end.time() > SIG_TO:
            break
        if (below and r["c"] > pmv) or (not below and r["c"] < pmv):
            return (1 if below else -1), end, flush
    return None


def px_at(m1, t, col="o"):
    x = m1[m1.index >= t]
    return float(x[col].iloc[0]) if len(x) and (x.index[0] - t).total_seconds() <= 600 else None


def stock_ret(m1, t0, direction):
    """عائد السهم في الاتجاه من افتتاح أول دقيقة ≥ t0 حتى إغلاق آخر دقيقة قبل 15:55."""
    e = px_at(m1, t0)
    x = m1[(m1.index >= t0) & (m1.index.time < CUTOFF)]
    if e is None or x.empty:
        return None
    return direction * (float(x["c"].iloc[-1]) / e - 1)


def opt_exit(p, cut, tp=TP, sl=SL):
    """أول هدف أو وقف على إغلاق الشمعة، وإلا آخر قيمة قبل cut."""
    last = None
    for t, v, _ in p:
        if t > cut:
            break
        last = v
        if v >= tp:
            return tp
        if v <= sl:
            return sl
    return last


def expiry_for(d0):
    f = fridays(d0 + timedelta(days=2), d0 + timedelta(days=12))
    return f[0] if f else None


# ====================== الجلب ======================
def stock_bars(sym, d0):
    """شموع دقيقة للسهم يوم d0 من 04:00 حتى 16:00 نيويورك (Alpaca sip)."""
    a = datetime.combine(d0, dtime(4, 0), NY).astimezone(UTC).isoformat()
    b = min(datetime.combine(d0, dtime(16, 0), NY).astimezone(UTC), datetime.now(UTC) - timedelta(minutes=16)).isoformat()
    rows, page = [], None
    while True:
        q = {"symbols": sym, "timeframe": "1Min", "start": a, "end": b, "limit": 10000, "feed": "sip", "adjustment": "raw"}
        if page:
            q["page_token"] = page
        req = urllib.request.Request("https://data.alpaca.markets/v2/stocks/bars?" + urllib.parse.urlencode(q),
                                     headers={"APCA-API-KEY-ID": os.getenv("ALPACA_KEY", "").strip(),
                                              "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET", "").strip()})
        j = json.loads(urllib.request.urlopen(req, timeout=30).read())
        rows += (j.get("bars") or {}).get(sym) or []
        page = j.get("next_page_token")
        if not page:
            break
    if not rows:
        return pd.DataFrame(columns=["o", "h", "l", "c", "v", "vw"])
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["t"]).dt.tz_convert(NY)
    return df[["o", "h", "l", "c", "v", "vw"]].astype(float)


def option_trade(sym, d0, t0, direction, S, iv):
    """عائد عقد بدلتا ≈0.50 في الاتجاه من t0، أو None."""
    import options_bot as ob
    exp = expiry_for(d0)
    if exp is None:
        return None
    side = "CALL" if direction > 0 else "PUT"
    T = max((exp - d0).days, 1) / 365
    st = step_for(S)
    k = strike_for_delta(S, T, iv, side, TARGET_D, ob.abs_delta)
    ks = sorted({round(x, 3) for x in (math.floor(k / st) * st, math.ceil(k / st) * st) if x > 0})
    try:
        bars = fetch_bars([occ(sym, exp, side, x) for x in ks], t0.astimezone(UTC).isoformat(), cap_end(d0).isoformat())
    except Exception:  # noqa: BLE001
        return None
    best = None
    for x in ks:
        b = bars.get(occ(sym, exp, side, x))
        if b is None or len(b) < 3 or first_bar_after(b, t0) is None:
            continue
        d = abs(ob.abs_delta(S, x, T, iv, side) - TARGET_D)
        if best is None or d < best[0]:
            best = (d, b)
    if best is None:
        return None
    pr = path(best[1], t0)
    return opt_exit(pr[1], datetime.combine(d0, CUTOFF, tzinfo=NY)) if pr else None


def run(limit=None):
    import yfinance as yf
    ev = pd.read_csv(EVENTS)
    ev = ev[(ev["d0"] >= START.isoformat()) & (ev["d0"] <= LAST.isoformat())]
    syms = sorted(ev["sym"].unique())[:limit] if limit else sorted(ev["sym"].unique())
    rows = []
    for sym in syms:
        try:
            px = yf.Ticker(sym).history(start="2023-12-01", auto_adjust=False)["Close"]
            px.index = px.index.tz_localize(None).date
        except Exception as e:  # noqa: BLE001
            print("تخطي", sym, type(e).__name__)
            continue
        tdays = list(px.index)
        for r in ev[ev["sym"] == sym].itertuples():
            d0 = date.fromisoformat(r.d0)
            if d0 not in tdays or tdays.index(d0) < 22:
                continue
            k = tdays.index(d0)
            prev = float(px.iloc[k - 1])
            iv = float(np.log(px.iloc[k - 22:k]).diff().dropna().std() * math.sqrt(252) * 1.15)
            try:
                m1 = stock_bars(sym, d0)
            except Exception as e:  # noqa: BLE001
                print("فشل السهم:", sym, d0, getattr(e, "code", type(e).__name__))
                continue
            reg = regular(m1)
            if reg.empty:
                continue
            o0 = float(reg["o"].iloc[0])
            gap = o0 / prev - 1
            if abs(gap) < MIN_GAP:
                continue
            pmv = pm_vwap(m1)
            out = {"sym": sym, "day": r.d0, "beat": getattr(r, "beat", np.nan), "gap": round(gap, 4),
                   "pmv": round(pmv, 3) if pmv else np.nan, "open": o0}
            gdir = 1 if gap > 0 else -1
            t_c1 = datetime.combine(d0, C1_T, tzinfo=NY)
            out["c1_stock"] = stock_ret(m1, t_c1, gdir)
            s_c1 = px_at(m1, t_c1) or o0
            out["c1_opt"] = option_trade(sym, d0, t_c1, gdir, s_c1, iv)
            sg = signal(m1, pmv)
            if sg:
                dirn, t0, flush = sg
                out.update(sig=dirn, sig_time=t0.strftime("%H:%M"), flush=flush, with_gap=int(dirn == gdir))
                out["e1"] = stock_ret(m1, t0, dirn)
                out["e2"] = option_trade(sym, d0, t0, dirn, px_at(m1, t0) or o0, iv)
            rows.append(out)
        print(sym, len(rows), flush=True)
    df = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(ROWS), exist_ok=True)
    df.to_csv(ROWS, index=False)
    return df


# ====================== التقرير ======================
def boot_mean(v, n=N_BOOT, seed=0):
    v = np.asarray(pd.Series(v).dropna(), dtype=float)
    if len(v) < 10:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    return tuple(np.percentile([rng.choice(v, len(v)).mean() for _ in range(n)], [2.5, 97.5]))


def verdict(df):
    """('مدعوم'|'جزئي'|'غير مدعوم', تفاصيل)."""
    s = df.dropna(subset=["e1"]) if "e1" in df else df.iloc[0:0]
    if len(s) < 10:
        return "غير مدعوم", {}
    days = sorted(s["day"].unique())
    mid = days[len(days) // 2]
    e1, ci1 = s["e1"].mean(), boot_mean(s["e1"])
    halves = (s[s["day"] < mid]["e1"].mean(), s[s["day"] >= mid]["e1"].mean())
    e2s = s["e2"].dropna() if "e2" in s else pd.Series(dtype=float)
    e2, ci2 = (e2s.mean(), boot_mean(e2s)) if len(e2s) else (np.nan, (np.nan, np.nan))
    ok1 = e1 > 0 and ci1[0] > 0 and min(halves) > 0
    ok2 = bool(len(e2s)) and e2 > 0 and ci2[0] > 0
    v = "مدعوم" if ok1 and ok2 else "جزئي" if ok1 else "غير مدعوم"
    return v, {"n": len(s), "e1": e1, "ci1": ci1, "halves": halves, "n2": len(e2s), "e2": e2, "ci2": ci2}


def _row(name, v, money=False):
    v = pd.Series(v).dropna()
    if not len(v):
        return f"| {name} | 0 | — | — | — |"
    lo, hi = boot_mean(v)
    return f"| {name} | {len(v)} | {v.mean() * 100:+.2f}% | [{lo * 100:+.2f}، {hi * 100:+.2f}] | {(v > 0).mean() * 100:.0f}% |"


def report(df):
    v, d = verdict(df)
    s = df.dropna(subset=["e1"]) if "e1" in df else df.iloc[0:0]
    L = ["# H-EARNREV: انعكاس يوم رد الفعل عبر VWAP ما قبل الافتتاح (نتائج آلية)", "",
         f"- {df['sym'].nunique() if len(df) else 0} شركة، {len(df)} يوم رد فعل بفجوة ≥ 2% ({df['day'].min() if len(df) else ''} → {df['day'].max() if len(df) else ''}).",
         f"- أيام فيها إشارة عبور (09:35–10:30): {len(s)} ({len(s) / max(len(df), 1) * 100:.0f}%).", "",
         "| القياس | العدد | المتوسط | فاصل 95% | رابحة |", "|---|---|---|---|---|",
         _row("E1 السهم في اتجاه العبور حتى 15:55", s.get("e1", [])),
         _row("E2 عقد دلتا 0.50 (+50% / −30% / 15:55)", s.get("e2", [])),
         _row("C1 السهم: 10:00 مع الفجوة (كل الأيام)", df.get("c1_stock", [])),
         _row("C1 العقد: 10:00 مع الفجوة (كل الأيام)", df.get("c1_opt", [])), ""]
    if d:
        L.append(f"نصفا الفترة لـE1: {d['halves'][0] * 100:+.2f}% / {d['halves'][1] * 100:+.2f}%")
    L += ["", f"⚖️ **الحكم: {v}**", "", "## وصفي (ليس جزءاً من الحكم)", "", "| المجموعة | العدد | E1 | E2 |", "|---|---|---|---|"]
    groups = [("عبور مع اتجاه الفجوة", s.get("with_gap") == 1), ("عبور ضد الفجوة (مثل AMD)", s.get("with_gap") == 0),
              ("سبقه تصريف", s.get("flush") == True), ("بلا تصريف", s.get("flush") == False),  # noqa: E712
              ("فوق التوقع", s.get("beat", pd.Series(dtype=float)) > 0), ("تحت التوقع", s.get("beat", pd.Series(dtype=float)) < 0)]
    for name, mask in groups:
        if len(s) and mask is not None and hasattr(mask, "sum"):
            g = s[mask]
            L.append(f"| {name} | {len(g)} | {g['e1'].mean() * 100:+.2f}% | {g['e2'].dropna().mean() * 100 if 'e2' in g and g['e2'].notna().any() else float('nan'):+.1f}% |")
    L += ["", "تحفظ: أسعار آخر صفقة في الشمعة؛ دلتا من التذبذب المحقق؛ جمعات فقط؛ الجدران ونقطة الانقلاب غير مختبرة (لا اهتمام مفتوح تاريخي)."]
    return "\n".join(L) + "\n"


def main(argv):
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    df = run(limit) if "--report" not in argv else pd.read_csv(ROWS)
    md = report(df)
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main(sys.argv[1:])
