#!/usr/bin/env python3
"""مراقب سيولة SPX اللحظي (H-SPXFLOW، مسجّل مسبقاً في CLAUDE.md 2/11/2026 بطلب المستخدم). تسجيل فقط: لا يرسل ولا يغيّر توصية.

كل 5 دقائق 9:35–16:00 نيويورك: عقود SPXW 0DTE ضمن ±1.5% من SPX من Tastytrade (OI، الحجم، الإغريقيات، العرض/الطلب)،
ومنها جدارا OI والحجم، وصافي GEX ونقطة الانقلاب، والتدفق المصنّف (فرق الحجم منذ اللقطة السابقة بجهة آخر سعر من المنتصف).
بعد الإغلاق (--eval): نتائج الفرضيات F1/F2/F3 في research/spx_flow_results.md.
الملفات: spx_flow_log.csv (ملخص، يُحفظ في git)، spx_flow_raw/YYYY-MM-DD.csv.gz (خام، على الخادم فقط لحجمه).
تشغيل: python spx_flow_watch.py [--eval] [--force]
"""
import csv
import math
import os
import sys
import urllib.parse
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
NY = ZoneInfo("America/New_York")
LOG = os.path.join(ROOT, "spx_flow_log.csv")
RAW_DIR = os.path.join(ROOT, "spx_flow_raw")
RESULTS = os.path.join(ROOT, "research", "spx_flow_results.md")
BAND = 0.015                 # ±1.5% من السعر
START, END = dtime(9, 35), dtime(16, 0)
TOUCH = 0.001                # F1: اقتراب ضمن 0.1% من الجدار
BARRIER = 0.0003             # F3: حاجز 0.03% لحركة 30 دقيقة
MIN_F1, MIN_F2, MIN_F3 = 40, 15, 300
COLS = ["date", "time", "spot", "call_wall", "call_wall_oi", "put_wall", "put_wall_oi", "vol_call_wall", "vol_put_wall",
        "net_gex_m", "flip", "call_prem", "put_prem", "net_delta", "contracts"]
RAW_COLS = ["time", "symbol", "side", "strike", "oi", "volume", "gamma", "delta", "iv", "bid", "ask", "last"]


# ====================== الحسابات (بلا شبكة) ======================
def gamma(S, K, T, iv):
    if S <= 0 or K <= 0 or T <= 0 or iv <= 0:
        return 0.0
    sd = iv * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sd * sd) / sd
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (S * sd)


def years_left(now):
    """الزمن المتبقي حتى إغلاق 16:00 بالسنوات (حد أدنى 15 دقيقة كي لا تنفجر الغاما)."""
    mins = (datetime.combine(now.date(), END, NY) - now).total_seconds() / 60
    return max(mins, 15) / (365 * 1440)


def gex_at(df, S, T):
    """صافي GEX بالملايين عند سعر S: صانع السوق طويل الكول قصير البوت."""
    tot = 0.0
    for r in df.itertuples():
        g = gamma(S, r.strike, T, r.iv) * r.oi * 100 * S * S * 0.01
        tot += g if r.side == "C" else -g
    return tot / 1e6


def flip_point(df, S, T):
    """أقرب سعر لـ S في شبكة ±1.5% (خطوة 0.1%) تتغير عنده إشارة GEX؛ None إن لم تتغير."""
    grid = [S * (1 + i / 1000) for i in range(-15, 16)]
    vals = [gex_at(df, x, T) for x in grid]
    cross = [(grid[i] + grid[i + 1]) / 2 for i in range(len(grid) - 1) if vals[i] * vals[i + 1] < 0]
    return min(cross, key=lambda x: abs(x - S)) if cross else None


def walls(df, S):
    c, p = df[(df.side == "C") & (df.strike > S)], df[(df.side == "P") & (df.strike < S)]
    pick = lambda d, col: (float(d.loc[d[col].idxmax(), "strike"]), float(d[col].max())) if len(d) and d[col].max() > 0 else (None, 0.0)
    cw, pw = pick(c, "oi"), pick(p, "oi")
    return {"call_wall": cw[0], "call_wall_oi": cw[1], "put_wall": pw[0], "put_wall_oi": pw[1],
            "vol_call_wall": pick(c, "volume")[0], "vol_put_wall": pick(p, "volume")[0]}


def flow(prev, cur):
    """صافي العلاوة (شراء − بيع) للكول والبوت وصافي الدلتا منذ اللقطة السابقة. الجهة: آخر سعر ≥ المنتصف = شراء."""
    if prev is None or not len(prev):
        return {"call_prem": 0.0, "put_prem": 0.0, "net_delta": 0.0}
    m = cur.merge(prev[["symbol", "volume"]], on="symbol", how="left", suffixes=("", "_prev"))
    m["dv"] = (m.volume - m.volume_prev.fillna(m.volume)).clip(lower=0)
    mid = (m.bid + m.ask) / 2
    sign = np.where((m["last"] > 0) & (m["last"] >= mid), 1.0, -1.0)
    m["prem"] = m.dv * m["last"] * 100 * sign
    m["dlt"] = m.dv * m.delta * 100 * sign
    return {"call_prem": float(m.loc[m.side == "C", "prem"].sum()), "put_prem": float(m.loc[m.side == "P", "prem"].sum()),
            "net_delta": float(m.dlt.sum())}


def summarize(df, prev, S, now):
    T = years_left(now)
    fp = flip_point(df, S, T)
    row = {"date": now.date().isoformat(), "time": now.strftime("%H:%M"), "spot": round(S, 2), **walls(df, S),
           "net_gex_m": round(gex_at(df, S, T), 2), "flip": round(fp, 1) if fp else "", **flow(prev, df), "contracts": len(df)}
    return {k: (round(v, 0) if k in ("call_prem", "put_prem", "net_delta") else v) for k, v in row.items()}


# ====================== الجلب (Tastytrade) ======================
def fetch(now):
    """(سعر SPX، جدول عقود SPXW 0DTE ضمن النطاق) أو (None، None)."""
    import live_quotes as lq
    S, _ = lq.spot("SPX")
    if not S:
        return None, None
    tok = lq._tasty_token()
    ch = (lq._tasty_req("/option-chains/SPX/nested", token=tok).get("data") or {}).get("items") or []
    day = now.date().isoformat()
    syms = []
    for root in ch:
        if root.get("root-symbol") != "SPXW":
            continue
        for e in root.get("expirations") or []:
            if e.get("expiration-date") == day:
                for k in e.get("strikes") or []:
                    K = float(k["strike-price"])
                    if abs(K / S - 1) <= BAND:
                        syms += [(k["call"], "C", K), (k["put"], "P", K)]
    rows, meta = [], {s: (side, K) for s, side, K in syms}
    keys = list(meta)
    for i in range(0, len(keys), 50):
        q = urllib.parse.urlencode([("equity-option", s) for s in keys[i:i + 50]])
        for it in (lq._tasty_req(f"/market-data/by-type?{q}", token=tok).get("data") or {}).get("items") or []:
            s = it.get("symbol")
            if s not in meta:
                continue
            f = lambda k: float(it.get(k) or 0)
            rows.append({"time": now.strftime("%H:%M"), "symbol": s, "side": meta[s][0], "strike": meta[s][1],
                         "oi": f("open-interest"), "volume": f("volume"), "gamma": f("gamma"), "delta": f("delta"),
                         "iv": f("volatility"), "bid": f("bid"), "ask": f("ask"), "last": f("last")})
    return S, (pd.DataFrame(rows, columns=RAW_COLS) if rows else None)


def raw_path(d):
    return os.path.join(RAW_DIR, f"{d}.csv.gz")


def last_snapshot(d):
    p = raw_path(d)
    if not os.path.exists(p):
        return None, None
    r = pd.read_csv(p)
    return r, (r[r.time == r.time.iloc[-1]] if len(r) else None)


def snap(now):
    S, df = fetch(now)
    if df is None:
        print("لا بيانات (السعر أو السلسلة).")
        return None
    os.makedirs(RAW_DIR, exist_ok=True)
    allr, prev = last_snapshot(now.date().isoformat())
    if prev is not None and len(prev) and prev.time.iloc[0] == now.strftime("%H:%M"):
        print("لقطة هذه الدقيقة مسجّلة.")
        return None
    row = summarize(df, prev, S, now)
    (df if allr is None else pd.concat([allr, df])).to_csv(raw_path(now.date().isoformat()), index=False)
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        w.writerow(row)
    print(row)
    return row


# ====================== التقييم ======================
def outcomes(log):
    """لكل لقطة: عائد SPX بعد 30 و60 دقيقة (من لقطات اليوم نفسه)."""
    out = []
    for d, g in log.groupby("date"):
        g = g.sort_values("time").copy()
        t = pd.to_datetime(d + " " + g.time)
        s = pd.Series(g.spot.values, index=t)
        for h in (30, 60):
            fut = [s[s.index >= x + pd.Timedelta(minutes=h)] for x in t]
            g[f"r{h}"] = [(f.iloc[0] / v - 1) if len(f) and (f.index[0] - x).total_seconds() <= (h + 10) * 60 else np.nan
                          for f, v, x in zip(fut, s.values, t)]
        out.append(g)
    return pd.concat(out) if out else log


def boot_ci(vals, days, n=4000, seed=0):
    vals, days = np.asarray(vals, float), np.asarray(days)
    u = np.unique(days)
    if len(u) < 2:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    by = {d: vals[days == d] for d in u}
    ms = [np.mean(np.concatenate([by[d] for d in rng.choice(u, len(u))])) for _ in range(n)]
    return tuple(np.percentile(ms, [2.5, 97.5]))


def boot_p(vals, days, n=4000, seed=1):
    """قيمة p أحادية تقريبية: نسبة المعاينات التي متوسطها ≤ 0."""
    vals, days = np.asarray(vals, float), np.asarray(days)
    u = np.unique(days)
    if len(u) < 2:
        return np.nan
    rng = np.random.default_rng(seed)
    by = {d: vals[days == d] for d in u}
    ms = np.array([np.mean(np.concatenate([by[d] for d in rng.choice(u, len(u))])) for _ in range(n)])
    return float((ms <= 0).mean())


def f1_touches(o):
    """أول اقتراب في اليوم لكل جدار (ضمن 0.1%) → عائد 30د في جهة الارتداد."""
    ev = []
    for d, g in o.groupby("date"):
        seen = set()
        for r in g.sort_values("time").itertuples():
            for kind, lvl, sgn in (("call", r.call_wall, -1), ("put", r.put_wall, 1)):
                if pd.isna(lvl) or lvl == "" or (kind, lvl) in seen:
                    continue
                if abs(r.spot / float(lvl) - 1) <= TOUCH:
                    seen.add((kind, lvl))
                    if not pd.isna(r.r30):
                        ev.append({"date": d, "kind": kind, "y": sgn * r.r30})
    return pd.DataFrame(ev, columns=["date", "kind", "y"])


def f2_days(o):
    rows = []
    for d, g in o.groupby("date"):
        g = g.sort_values("time")
        at = g[g.time >= "10:00"]
        if len(at) < 12:
            continue
        rows.append({"date": d, "gex": float(at.net_gex_m.iloc[0]), "range": (at.spot.max() - at.spot.min()) / at.spot.iloc[0]})
    return pd.DataFrame(rows, columns=["date", "gex", "range"])


def f3_obs(o):
    rows = []
    for d, g in o.groupby("date"):
        g = g.sort_values("time").reset_index(drop=True)
        for i, r in g.iterrows():
            if r.time < "10:00" or r.time > "15:30" or r.time[-2:] not in ("00", "30") or pd.isna(r.r30):
                continue
            w = g[(g.time > (pd.Timestamp(d + " " + r.time) - pd.Timedelta(minutes=30)).strftime("%H:%M")) & (g.time <= r.time)]
            lean = float(w.call_prem.sum() - w.put_prem.sum())
            if lean != 0:
                rows.append({"date": d, "time": r.time, "y": np.sign(lean) * r.r30})
    return pd.DataFrame(rows, columns=["date", "time", "y"])


def bh(ps, q=0.05):
    """Benjamini-Hochberg: قائمة نجاح/فشل بالترتيب نفسه."""
    idx = [i for i, p in enumerate(ps) if not np.isnan(p)]
    order = sorted(idx, key=lambda i: ps[i])
    ok, k = [False] * len(ps), 0
    for rank, i in enumerate(order, 1):
        if ps[i] <= q * rank / len(order):
            k = rank
    for rank, i in enumerate(order, 1):
        ok[i] = rank <= k
    return ok


def evaluate():
    if not os.path.exists(LOG):
        return "لا سجل بعد."
    o = outcomes(pd.read_csv(LOG, dtype={"time": str}))
    f1, f2, f3 = f1_touches(o), f2_days(o), f3_obs(o)
    pos, neg = f2[f2.gex > 0], f2[f2.gex < 0]
    L = ["# نتائج H-SPXFLOW (مراقب سيولة SPX اللحظي)", "",
         f"الأيام: {o.date.nunique()} | اللقطات: {len(o)} | آخر تحديث: {datetime.now(NY):%Y-%m-%d %H:%M} نيويورك", ""]
    ps, ready = [np.nan] * 3, [len(f1) >= MIN_F1, len(pos) >= MIN_F2 and len(neg) >= MIN_F2, len(f3) >= MIN_F3]
    # F1
    if len(f1):
        lo, hi = boot_ci(f1.y, f1.date)
        ps[0] = boot_p(f1.y, f1.date)
        L.append(f"- **F1 الجدران:** {len(f1)} اقتراباً (الحد {MIN_F1}) | متوسط عائد الارتداد 30د {f1.y.mean():+.3%} "
                 f"[{lo:+.3%}، {hi:+.3%}] | ارتد في {(f1.y > 0).mean():.0%}")
    else:
        L.append(f"- **F1 الجدران:** لا اقتراب بعد (الحد {MIN_F1}).")
    # F2
    if len(pos) and len(neg):
        ratio = pos.range.mean() / neg.range.mean()
        rng = np.random.default_rng(2)
        rs = [rng.choice(pos.range, len(pos)).mean() / rng.choice(neg.range, len(neg)).mean() for _ in range(4000)]
        ps[1] = float((np.array(rs) > 0.8).mean())
        L.append(f"- **F2 نظام الغاما:** أيام موجب {len(pos)} / سالب {len(neg)} (الحد {MIN_F2} لكل) | نسبة المدى {ratio:.2f} "
                 f"[{np.percentile(rs, 2.5):.2f}، {np.percentile(rs, 97.5):.2f}] (الفرضية ≤ 0.8)")
    else:
        L.append(f"- **F2 نظام الغاما:** أيام موجب {len(pos)} / سالب {len(neg)} — لا مقارنة بعد.")
    # F3
    if len(f3):
        lo, hi = boot_ci(f3.y, f3.date)
        ps[2] = boot_p(np.asarray(f3.y) - BARRIER, f3.date)
        L.append(f"- **F3 ميل التدفق:** {len(f3)} ملاحظة (الحد {MIN_F3}) | متوسط العائد في جهة التدفق 30د {f3.y.mean():+.3%} "
                 f"[{lo:+.3%}، {hi:+.3%}] (الحاجز {BARRIER:+.2%}) | صحيح الجهة {(f3.y > 0).mean():.0%}")
    else:
        L.append(f"- **F3 ميل التدفق:** لا ملاحظات بعد (الحد {MIN_F3}).")
    ok = bh(ps)
    L += ["", "**الحكم:** " + (" | ".join(f"F{i + 1}: " + ("ثبت" if ok[i] else "لم يثبت") for i in range(3)) if all(ready)
                              else "قبل اكتمال العينة — وصفي فقط، لا حكم."), "",
          "تحفظ: تصنيف الشراء/البيع تقريبي (لقطة كل 5 دقائق)، وافتراض موقف صانع السوق قد يكون خاطئاً، وحركة المؤشر ليست ربح العقد."]
    os.makedirs(os.path.dirname(RESULTS), exist_ok=True)
    open(RESULTS, "w", encoding="utf-8").write("\n".join(L) + "\n")
    return "\n".join(L)


def main(argv):
    now = datetime.now(NY)
    if "--eval" in argv:
        print(evaluate())
        return
    if "--force" not in argv and (now.weekday() >= 5 or not START <= now.time() <= END):
        print("خارج نافذة الجلسة.")
        return
    snap(now)


if __name__ == "__main__":
    main(sys.argv[1:])
