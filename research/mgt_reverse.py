#!/usr/bin/env python3
"""هندسة عكسية لصفقات MGT على SPX (بطلب المستخدم 5/11/2026). قراءة وتحليل فقط، لا يغيّر أي بوت.

لكل صفقة SPXW في mgt_recs_log.csv (دخول وقمة ووقف معلنة، والوقت غالباً غير مذكور):
1) نعيد بناء وقت الدخول من شموع العقد الحقيقية (Alpaca، دقيقة): أول دقيقة تداول فيها العقد بسعر الدخول
   ثم بلغ بعدها القمة المعلنة (±5%). هذا يحوّل «لا وقت» إلى وقت دخول موثّق بالسعر.
2) نقرأ حالة المؤشر لحظتها: الحركة منذ الافتتاح وبوحدات الحركة المتوقعة، VWAP، نطاق أول 30د، الزخم 15/30/60د،
   RSI، المتوسطات، موقع السعر من قمة/قاع اليوم، بُعد السترايك، دلتا تقريبية، الوقت من الافتتاح.
3) نقارن بخط أساس: كل شموع 5د في الأيام نفسها، ونقارن توقيتهم بتوقيت عشوائي على العقد نفسه بالسعر نفسه.
4) نحسب نتيجتهم الواقعية (لا من القمة): وقفهم المعلن، و+50%، والاحتفاظ للإغلاق.
تشغيل (الخادم، ALPACA): SPXBOT_DATA=/opt/spxbot/data python research/mgt_reverse.py
المخرج: SPXBOT_DATA/mgt_reverse_rows.csv و research/mgt_reverse_results.md
"""
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
RECS = os.path.join(ROOT, "mgt_recs_log.csv")
ROWS = os.path.join(DATA, "mgt_reverse_rows.csv")
BASE = os.path.join(DATA, "mgt_reverse_base.csv")
OUT = os.path.join(HERE, "mgt_reverse_results.md")
CACHE5 = os.path.join(ROOT, "research", "spx_5m_cache.csv")
SPY_FILE = os.path.join(DATA, "spy_5m.csv.gz")
PEAK_TOL, PX_TOL = 0.05, 0.03


# ====================== قراءة صفقاتهم ======================
def parse_trades(path=RECS):
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    out = []
    for r in df.itertuples():
        m = re.match(r"SPXW (\d+) (CALL|PUT)", r.contract)
        if not m or r.kind not in ("rec", "rec_report", "rec_update"):
            continue
        try:
            e, pk = float(r.entry), float(r.peak)
        except ValueError:
            continue
        st = re.search(r"وقف ([\d.]+)", r.note)
        out.append({"day": r.date_ny, "strike": float(m.group(1)), "side": m.group(2), "entry": e, "peak": pk,
                    "stop": float(st.group(1)) if st and float(st.group(1)) < e else round(e / 2, 2), "kind": r.kind, "t_riyadh": r.time_riyadh})
    t = pd.DataFrame(out)
    return t.drop_duplicates(["day", "strike", "side", "entry", "peak"]).reset_index(drop=True)


# ====================== دوال صافية ======================
def match_entry(b, entry, peak, px_tol=PX_TOL, peak_tol=PEAK_TOL):
    """b: شموع دقيقة (o,h,l,c) مرتبة. أول دقيقة يقع فيها سعر الدخول (±px_tol) ثم تبلغ القمة بعدها (±peak_tol).
    يرجع (وقت الدخول، وقت القمة، أعلى بعد الدخول) أو None."""
    if b is None or b.empty:
        return None
    lo, hi = b["l"].values, b["h"].values
    hi_after = np.maximum.accumulate(hi[::-1])[::-1]          # أعلى سعر من هذه الدقيقة فصاعداً
    tol = max(px_tol * entry, 0.05)
    for i in range(len(b)):
        if lo[i] - tol <= entry <= hi[i] + tol and abs(hi_after[i] / peak - 1) <= peak_tol:
            j = i + int(np.argmax(hi[i:] >= hi_after[i] * 0.999))
            return b.index[i], b.index[j], float(hi_after[i])
    return None


def outcomes(b, t0, entry, stop, tp=0.5, cut="15:55"):
    """النتيجة الواقعية من t0: وقفهم (أول دقيقة low ≤ وقف)، +50% (أول high ≥ 1.5×)، والإغلاق 15:55؛ وأيهما أولاً."""
    x = b[b.index >= t0]
    x = x[x.index.strftime("%H:%M") <= cut]
    if x.empty:
        return {}
    t_stop = next((t for t, r in x.iterrows() if r["l"] <= stop), None)
    t_tp = next((t for t, r in x.iterrows() if r["h"] >= entry * (1 + tp)), None)
    last = float(x["c"].iloc[-1])
    first = "tp" if t_tp is not None and (t_stop is None or t_tp <= t_stop) else ("stop" if t_stop is not None else "close")
    rule = {"tp": tp, "stop": stop / entry - 1, "close": last / entry - 1}[first]
    return {"first": first, "rule_ret": rule, "hold_ret": last / entry - 1, "t_stop": t_stop, "t_tp": t_tp,
            "min_before_peak": None}


def bs_delta(S, K, T, iv, side):
    if T <= 0 or iv <= 0:
        return (1.0 if S > K else 0.0) if side == "CALL" else (1.0 if S < K else 0.0)
    d1 = (math.log(S / K) + 0.5 * iv * iv * T) / (iv * math.sqrt(T))
    nd = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
    return nd if side == "CALL" else 1 - nd


def rsi(s, n=14):
    d = s.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / n).mean(), (-d.clip(upper=0)).ewm(alpha=1 / n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def index_state(spx5, spy5, t, prev_close, vix_prev):
    """حالة المؤشر عند الوقت t (من الشموع المنتهية قبله)."""
    day = spx5[spx5.index.date == t.date()]
    done = day[day.index + pd.Timedelta(minutes=5) <= t]
    if len(done) < 1:
        return None
    S, o = float(done["Close"].iloc[-1]), float(day["Open"].iloc[0])
    em = vix_prev / 100 / math.sqrt(252)
    f = {"S": S, "open": o, "gap": o / prev_close - 1 if prev_close else np.nan, "from_open": S / o - 1,
         "from_open_em": (S / o - 1) / em, "from_prev_em": (S / prev_close - 1) / em if prev_close else np.nan,
         "mins": (t.hour * 60 + t.minute) - 570, "day_hi": float(done["High"].max()), "day_lo": float(done["Low"].min())}
    rng = f["day_hi"] - f["day_lo"]
    f["pos_in_range"] = (S - f["day_lo"]) / rng if rng > 0 else 0.5
    for k, n in (("r15", 3), ("r30", 6), ("r60", 12)):
        f[k] = S / float(done["Close"].iloc[-1 - n]) - 1 if len(done) > n else np.nan
    allc = spx5[spx5.index <= done.index[-1]]["Close"].tail(120)
    f["rsi"] = float(rsi(allc).iloc[-1]) if len(allc) > 15 else np.nan
    f["ema9_gt_21"] = bool(allc.ewm(span=9).mean().iloc[-1] > allc.ewm(span=21).mean().iloc[-1])
    orb = day[day.index.strftime("%H:%M") < "10:00"]
    if len(orb) >= 6 and t.strftime("%H:%M") >= "10:00":
        f["orb_hi"], f["orb_lo"] = float(orb["High"].max()), float(orb["Low"].min())
        f["orb_pos"] = "فوق" if S > f["orb_hi"] else ("تحت" if S < f["orb_lo"] else "داخل")
    else:
        f["orb_pos"] = "قبل اكتمال النطاق"
    sp = spy5[spy5.index.date == t.date()] if spy5 is not None else None
    if sp is not None and len(sp):
        sp = sp[(sp.index + pd.Timedelta(minutes=5) <= t) & (sp.index.strftime("%H:%M") >= "09:30")]
        if len(sp) and sp["Volume"].sum() > 0:
            tp = (sp["High"] + sp["Low"] + sp["Close"]) / 3
            vw = float((tp * sp["Volume"]).sum() / sp["Volume"].sum())
            f["vwap_dist_em"] = (float(sp["Close"].iloc[-1]) / vw - 1) / em
    # آخر قمة/قاع محلي: ارتداد من طرف اليوم
    f["from_hi_em"] = (S / f["day_hi"] - 1) / em
    f["from_lo_em"] = (S / f["day_lo"] - 1) / em
    return f


# ====================== الجلب ======================
def opt_bars_1m(syms, day):
    a = datetime.fromisoformat(day).replace(tzinfo=NY)
    start, end = (a + timedelta(hours=4)).astimezone(UTC).isoformat(), (a + timedelta(hours=16, minutes=15)).astimezone(UTC).isoformat()
    out = {}
    syms = list(dict.fromkeys(syms))
    for i in range(0, len(syms), 25):
        page = None
        while True:
            q = {"symbols": ",".join(syms[i:i + 25]), "timeframe": "1Min", "start": start, "end": end, "limit": 10000}
            if page:
                q["page_token"] = page
            req = urllib.request.Request("https://data.alpaca.markets/v1beta1/options/bars?" + urllib.parse.urlencode(q),
                                         headers={"APCA-API-KEY-ID": os.getenv("ALPACA_KEY", "").strip(),
                                                  "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET", "").strip()})
            j = json.loads(urllib.request.urlopen(req, timeout=60).read())
            for s, bb in (j.get("bars") or {}).items():
                df = pd.DataFrame(bb)
                df.index = pd.to_datetime(df["t"]).dt.tz_convert(NY)
                out[s] = pd.concat([out.get(s, pd.DataFrame()), df[["o", "h", "l", "c", "v"]].astype(float)])
            page = j.get("next_page_token")
            if not page:
                break
    return {k: v.sort_index() for k, v in out.items()}


def occ(day, side, K):
    d = datetime.fromisoformat(day)
    return f"SPXW{d:%y%m%d}{'C' if side == 'CALL' else 'P'}{int(round(K * 1000)):08d}"


def load_index():
    import yfinance as yf
    spx = pd.read_csv(CACHE5, index_col=0)
    spx.index = pd.to_datetime(spx.index, utc=True).tz_convert(NY)
    try:
        y = yf.Ticker("^GSPC").history(period="60d", interval="5m", auto_adjust=False)[["Open", "High", "Low", "Close"]]
        y.index = y.index.tz_convert(NY)
        spx = pd.concat([spx, y])
    except Exception as e:  # noqa: BLE001
        print("ياهو SPX:", e)
    spx = spx[~spx.index.duplicated(keep="last")].sort_index()
    spx = spx[(spx.index.strftime("%H:%M") >= "09:30") & (spx.index.strftime("%H:%M") < "16:00")]
    spy = None
    try:
        f = pd.read_csv(SPY_FILE)
        f.index = pd.DatetimeIndex(pd.to_datetime(f["t"], utc=True)).tz_convert(NY)
        spy = f.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})[["Open", "High", "Low", "Close", "Volume"]]
        y = yf.Ticker("SPY").history(period="60d", interval="5m", auto_adjust=False)[["Open", "High", "Low", "Close", "Volume"]]
        y.index = y.index.tz_convert(NY)
        spy = pd.concat([spy[spy.index >= spx.index.min() - pd.Timedelta(days=3)], y])
        spy = spy[~spy.index.duplicated(keep="last")].sort_index()
    except Exception as e:  # noqa: BLE001
        print("SPY:", e)
    daily = yf.Ticker("^GSPC").history(period="1y", auto_adjust=False)["Close"]
    daily.index = daily.index.tz_localize(None).date
    vix = yf.Ticker("^VIX").history(period="1y", auto_adjust=False)["Close"]
    vix.index = vix.index.tz_localize(None).date
    return spx, spy, daily, vix


def prev_of(series, day):
    d = datetime.fromisoformat(day).date()
    s = series[[x < d for x in series.index]]
    return float(s.iloc[-1]) if len(s) else np.nan


def run():
    trades = parse_trades()
    print(len(trades), "صفقة SPXW")
    spx, spy, daily, vix = load_index()
    rows, base = [], []
    for day, g in trades.groupby("day"):
        try:
            bars = opt_bars_1m([occ(day, r.side, r.strike) for r in g.itertuples()], day)
        except Exception as e:  # noqa: BLE001
            print("فشل جلب", day, getattr(e, "code", type(e).__name__))
            continue
        pc, vx = prev_of(daily, day), prev_of(vix, day)
        for r in g.itertuples():
            b = bars.get(occ(day, r.side, r.strike))
            row = {"day": day, "side": r.side, "strike": r.strike, "entry": r.entry, "peak": r.peak, "stop": r.stop,
                   "claimed_pct": r.peak / r.entry - 1, "bars": 0 if b is None else len(b)}
            m = match_entry(b, r.entry, r.peak)
            if m is None:
                row["matched"] = False
                if b is not None and len(b):
                    row["day_hi"] = float(b["h"].max())
                    row["entry_in_range"] = bool(b["l"].min() <= r.entry <= b["h"].max())
                rows.append(row)
                continue
            t0, tpk, hiaft = m
            row.update(matched=True, t_entry=t0.strftime("%H:%M"), t_peak=tpk.strftime("%H:%M"),
                       mins_to_peak=(tpk - t0).total_seconds() / 60)
            oc = outcomes(b, t0, r.entry, r.stop)
            row.update({k: v for k, v in oc.items() if k in ("first", "rule_ret", "hold_ret")})
            row["stop_before_peak"] = bool(oc.get("t_stop") is not None and oc["t_stop"] < tpk)
            # عشوائي على العقد نفسه: كل دقيقة كان سعره فيها ضمن ±25% من دخولهم
            band = b[(b["c"] >= r.entry * 0.75) & (b["c"] <= r.entry * 1.25) & (b.index.strftime("%H:%M") <= "15:30")]
            if len(band) >= 5:
                rr = []
                for t in band.index[::3]:
                    o2 = outcomes(b, t, float(b.loc[t, "c"]), float(b.loc[t, "c"]) * r.stop / r.entry)
                    if o2:
                        rr.append(o2["rule_ret"])
                row["rand_rule_ret"] = float(np.mean(rr)) if rr else np.nan
            st = index_state(spx, spy, t0.to_pydatetime(), pc, vx) if not np.isnan(vx) else None
            if st:
                row.update(st)
                T = max((16 * 60 - (t0.hour * 60 + t0.minute)) / (365 * 1440), 1 / (365 * 1440))
                row["otm_pts"] = (r.strike - st["S"]) * (1 if r.side == "CALL" else -1)
                row["delta"] = bs_delta(st["S"], r.strike, T, vx / 100 * 1.1, r.side)
            rows.append(row)
        # خط الأساس: حالة المؤشر في كل شمعة 5د من 09:35 إلى 15:30 في اليوم نفسه
        d = spx[spx.index.date == datetime.fromisoformat(day).date()]
        for t in d.index:
            if not ("09:35" <= t.strftime("%H:%M") <= "15:30"):
                continue
            st = index_state(spx, spy, (t + pd.Timedelta(minutes=5)).to_pydatetime(), pc, vx) if not np.isnan(vx) else None
            if st:
                base.append(dict(st, day=day, t=t.strftime("%H:%M")))
        print(day, sum(1 for x in rows if x["day"] == day and x.get("matched")), "/", len(g), flush=True)
    df, bs = pd.DataFrame(rows), pd.DataFrame(base)
    df.to_csv(ROWS, index=False)
    bs.to_csv(BASE, index=False)
    return df, bs


if __name__ == "__main__":
    run()
