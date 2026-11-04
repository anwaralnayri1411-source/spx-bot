#!/usr/bin/env python3
"""ظل القاعدة C (H-NOCHASE، مراقبة للأمام، مسجّل في CLAUDE.md 4/11/2026 بطلب المستخدم). تسجيل فقط، لا رسائل ولا تغيير في البوت.

بعد كل إغلاق: لكل توصية SPX/SPY في zero_dte_log.csv (منذ START) لم تُسجَّل بعد: ماذا لو انتظر البوت إغلاق شمعة 5د
فوق أعلى أول 30 دقيقة (CALL) أو تحت أدناها (PUT)؟ الدخول بعد 10 دقائق من إغلاق الشمعة وحتى 15:00، بأقرب سترايك
خارج المال (≤ 2.5%) سعره الحقيقي ضمن ميزانية البوت، والخروج +50% أو −40% بعد 15 دقيقة أو 15:30. بلا كسر = 0$.
تشغيل: python rule_c_shadow.py   (ALPACA_KEY/ALPACA_SECRET)
"""
import math
import os
import sys
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
RECS = os.path.join(ROOT, "zero_dte_log.csv")
LOG = os.path.join(ROOT, "rule_c_log.csv")
START = "2026-11-05"
ORB_END = dtime(10, 0)
LAST_ENTRY = dtime(15, 0)
FINAL = dtime(15, 30)
ENTRY_LAG_MIN = 10
HS, COMM = 0.015, 0.0065
TP, SL, MIN_AGE = 0.50, 0.40, 15
COST_MIN, COST_MAX = 100.0, 400.0
COLS = ["id", "day", "time", "sym", "side", "rec_strike", "c_time", "c_strike", "c_entry", "c_kind", "c_ret", "c_usd"]


# ====================== دوال صافية ======================
def orb(b5):
    """(أعلى، أدنى) أول 30 دقيقة من شموع 5د بفهرس نيويورك، أو None."""
    x = b5[(b5.index.time >= dtime(9, 30)) & (b5.index.time < ORB_END)]
    return (float(x["h"].max()), float(x["l"].min())) if len(x) >= 6 else None


def c_entry_time(b5, rec_t, side):
    """وقت دخول القاعدة C: أول شمعة 5د تنتهي ≥ max(وقت التوصية، 10:00) ويُغلق خارج النطاق في الاتجاه، + 10 دقائق."""
    rng = orb(b5)
    if rng is None:
        return None
    for t, r in b5.iterrows():
        end = t + timedelta(minutes=5)
        if end.time() < ORB_END or end < rec_t:
            continue
        ent = end + timedelta(minutes=ENTRY_LAG_MIN)
        ent = max(ent, rec_t)
        if ent.time() > LAST_ENTRY:
            return None
        if (side == "CALL" and r["c"] > rng[0]) or (side == "PUT" and r["c"] < rng[1]):
            return ent
    return None


def otm_strikes(S, side, step):
    k0 = math.floor(S / step) * step + (step if side == "CALL" else 0)
    sg = 1 if side == "CALL" else -1
    out = []
    for i in range(80):
        K = round(k0 + sg * step * i, 3)
        if (side == "CALL" and K > S * 1.025) or (side == "PUT" and K < S * 0.975):
            break
        if (side == "CALL" and K > S) or (side == "PUT" and K < S):
            out.append(K)
    return out


def first_open(b, t0):
    x = b[b.index >= t0] if b is not None else None
    if x is None or x.empty or (x.index[0] - t0).total_seconds() > 600:
        return None
    return float(x["o"].iloc[0])


def pick_real(bars_by_k, S, t0):
    best = None
    for K, b in bars_by_k.items():
        o = first_open(b, t0)
        if o is None:
            continue
        if not (COST_MIN <= o * (1 + HS) * 100 <= COST_MAX):
            continue
        if best is None or abs(K - S) < best[0]:
            best = (abs(K - S), K)
    return None if best is None else best[1]


def follow(b, t0):
    """(دخول، عائد، نوع) بقواعد البوت على شموع حقيقية، أو None."""
    o = first_open(b, t0)
    if o is None or o <= 0:
        return None
    entry = o * (1 + HS) + COMM
    ret = 0.0
    for t, r in b[b.index >= t0].iterrows():
        end = t + timedelta(minutes=5)
        ret = (float(r["c"]) * (1 - HS) - COMM) / entry - 1
        if ret >= TP:
            return entry, ret, "هدف"
        if (end - t0).total_seconds() / 60 >= MIN_AGE and ret <= -SL:
            return entry, ret, "وقف"
        if end.time() >= FINAL:
            return entry, ret, "إغلاق"
    return entry, ret, "إغلاق"


# ====================== الجلب ======================
def index_bars(sym, d):
    """شموع 5د للمؤشر أو SPY ليوم d من ياهو (o,h,l,c)."""
    import yfinance as yf
    t = "^GSPC" if sym in ("^SPX", "SPX") else "SPY"
    h = yf.Ticker(t).history(start=d.isoformat(), end=(d + timedelta(days=1)).isoformat(), interval="5m", auto_adjust=False)
    if h.empty:
        return h
    h.index = h.index.tz_convert(NY)
    return h.rename(columns={"Open": "o", "High": "h", "Low": "l", "Close": "c"})[["o", "h", "l", "c"]]


def option_bars(occs, d):
    sys.path.insert(0, os.path.join(ROOT, "research"))
    from stocks_contract_replay import fetch_bars
    a = datetime.combine(d, dtime(9, 30), NY).astimezone(UTC).isoformat()
    b = min(datetime.combine(d, dtime(16, 0), NY).astimezone(UTC), datetime.now(UTC) - timedelta(minutes=16)).isoformat()
    return fetch_bars(occs, a, b)      # Alpaca يرفض (403) نهايةً أحدث من 15 دقيقة


def occ(root, d, side, K):
    return f"{root}{d.strftime('%y%m%d')}{'C' if side == 'CALL' else 'P'}{int(round(float(K) * 1000)):08d}"


def evaluate(r, idx_bars=index_bars, opt_bars=option_bars):
    d = date.fromisoformat(r["date"])
    sym, side = r["symbol"], r["side"]
    rec_t = datetime.combine(d, dtime(*map(int, r["time"].split(":"))), NY)
    out = {"id": f"{r['date']}:{r['time']}:{sym}:{side}", "day": r["date"], "time": r["time"], "sym": sym, "side": side,
           "rec_strike": r["strike"], "c_time": "", "c_strike": "", "c_entry": "", "c_kind": "لا دخول", "c_ret": "", "c_usd": 0.0}
    b5 = idx_bars(sym, d)
    if b5 is None or b5.empty:
        return None
    t0 = c_entry_time(b5, rec_t, side)
    if t0 is None:
        return out
    S = float(b5[b5.index < t0]["c"].iloc[-1])
    spx = sym in ("^SPX", "SPX")
    root, step = ("SPXW", 5.0) if spx else ("SPY", 1.0)
    ks = otm_strikes(S, side, step)
    got = opt_bars([occ(root, d, side, K) for K in ks], d)
    by_k = {K: got.get(occ(root, d, side, K)) for K in ks}
    K = pick_real(by_k, S, t0)
    if K is None:
        out["c_kind"] = "لا عقد"
        return out
    res = follow(by_k[K], t0)
    if res is None:
        out["c_kind"] = "لا عقد"
        return out
    e, ret, kind = res
    out.update(c_time=t0.strftime("%H:%M"), c_strike=K, c_entry=round(e, 3), c_kind=kind, c_ret=round(ret, 4),
               c_usd=round(e * ret * 100, 2))
    return out


def main(argv):
    if not os.path.exists(RECS):
        print("لا سجل توصيات.")
        return
    recs = pd.read_csv(RECS, dtype=str, keep_default_na=False)
    log = pd.read_csv(LOG, dtype=str, keep_default_na=False) if os.path.exists(LOG) else pd.DataFrame(columns=COLS)
    done = set(log["id"])
    now = datetime.now(NY)
    rows = []
    for r in recs.to_dict("records"):
        if r["date"] < START or (r["date"] == now.date().isoformat() and now.time() < dtime(16, 20)):
            continue
        rid = f"{r['date']}:{r['time']}:{r['symbol']}:{r['side']}"
        if rid in done:
            continue
        try:
            o = evaluate(r)
        except Exception as e:  # noqa: BLE001
            print("تخطي", rid, type(e).__name__, e)
            continue
        if o:
            rows.append(o)
            print(o)
    if rows:
        log = pd.concat([log, pd.DataFrame(rows, columns=COLS)], ignore_index=True)
    log[COLS].to_csv(LOG, index=False)
    print(f"سُجّل {len(rows)}، المجموع {len(log)}")


if __name__ == "__main__":
    main(sys.argv[1:])
