#!/usr/bin/env python3
"""H-FLOWH: هل يسبق شذوذ حجم الخيارات حركة السهم؟ اختبار تاريخي بأحجام Alpaca الحقيقية (لا يغيّر أي بوت). بطلب المستخدم 30/10/2026.

الدافع: ظل التدفق الحي (H-FLOW) بطيء (20 لقطة قبل أول حدث و100 حدث للحكم). هنا التعريفات نفسها على تاريخ منذ 2024:
  لكل سهم من قائمة البوت ولكل يوم: مجموع حجم الكول والبوت اليومي لعقود أقرب انتهاءين (جمعة) بين 5 و35 يوماً، وسترايكات ضمن ±8% من إغلاق اليوم.
  z = (الحجم الكلي − متوسط 20 يوماً سابقة) ÷ انحرافها؛ حصة الكول = كول ÷ (كول + بوت).
  الحدث: z ≥ 2، والاتجاه +1 إن كانت حصة الكول ≥ 65%، و−1 إن كانت ≤ 35% (وإلا بلا اتجاه).
  النتيجة: عائد السهم ناقص عائد SPY من افتتاح اليوم التالي (الحجم يُعرف بعد الإغلاق) حتى إغلاق اليوم 1 و3 و5 بعده، في جهة الحدث.
  الفرضية: متوسط العائد المُزال في جهة الحدث بعد 5 أيام > 0، أعلى من 95% من 10,000 اختيار عشوائي للجهة، على **النصف الثاني** من الأيام
  (النصف الأول للاكتشاف والوصف فقط)، وموجب في النصف الأول أيضاً.
  وصفي: 1 و3 أيام، كول مقابل بوت، شدة z (2–3 مقابل ≥ 3).
تحفظ: الحجم لا يميز الشراء من البيع ولا الفتح من الإغلاق؛ عقود بلا صفقات في يوم ما = حجم صفر؛ عائد السهم ليس ربح العقد.
تشغيل (الخادم، ALPACA): SPXBOT_DATA=/opt/spxbot/data python research/flow_history_test.py   المخرج: research/flow_history_test_results.md و..._events.csv
"""
import json
import os
import sys
import time as _time
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
from stocks_contract_replay import occ  # noqa: E402

NY = ZoneInfo("America/New_York")
DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
OUT_MD = os.path.join(HERE, "flow_history_test_results.md")
OUT_CSV = os.path.join(HERE, "flow_history_test_events.csv")
START = date(2024, 3, 1)
BAND, DTE_MIN, DTE_MAX = 0.08, 5, 35
Z_MIN, LOOKBACK = 2.0, 20
SHARE_UP, SHARE_DN = 0.65, 0.35
HORIZONS = (1, 3, 5)


# ============================ دوال نقية ============================
def fridays(a, b):
    d, out = a, []
    while d <= b:
        if d.weekday() == 4:
            out.append(d)
        d += timedelta(days=1)
    return out


def steps_for(S):
    return (0.5, 1.0) if S < 50 else (1.0, 2.5, 5.0) if S < 200 else (2.5, 5.0, 10.0)


def grid(lo, hi, S):
    out = set()
    for st in steps_for(S):
        k = np.floor(lo / st) * st
        while k <= hi + 1e-9:
            if k > 0:
                out.add(round(float(k), 3))
            k += st
    return sorted(out)


def plan_symbols(sym, closes):
    """{رمز OCC: (انتهاء، جهة، سترايك)} لكل الجمعات التي تقع في نافذة 5–35 يوماً لأي يوم، بسترايكات تغطي ±8% من مدى السعر في عمرها."""
    out = {}
    days = list(closes.index)
    for exp in fridays(days[0] + timedelta(days=DTE_MIN), days[-1] + timedelta(days=DTE_MAX)):
        life = closes[(closes.index >= exp - timedelta(days=DTE_MAX)) & (closes.index <= exp - timedelta(days=DTE_MIN))]
        if life.empty:
            continue
        lo, hi = float(life.min()) * (1 - BAND), float(life.max()) * (1 + BAND)
        for k in grid(lo, hi, float(life.mean())):
            for side in ("CALL", "PUT"):
                out[occ(sym, exp, side, k)] = (exp, side, k)
    return out


def daily_flow(vol, meta, closes):
    """vol: {رمز: Series حجم يومي بفهرس تاريخ}. يرجع DataFrame بالفهرس تاريخ وأعمدة call, put (أقرب انتهاءين 5–35 وسترايكات ±8%)."""
    rows = []
    by_exp = {}
    for s, (exp, side, k) in meta.items():
        by_exp.setdefault(exp, []).append((s, side, k))
    exps = sorted(by_exp)
    for d, S in closes.items():
        near = [e for e in exps if DTE_MIN <= (e - d).days <= DTE_MAX][:2]
        c = p = 0.0
        for e in near:
            for s, side, k in by_exp[e]:
                if abs(k / S - 1) > BAND:
                    continue
                v = vol.get(s)
                x = float(v.get(d, 0.0)) if v is not None else 0.0
                if side == "CALL":
                    c += x
                else:
                    p += x
        rows.append({"day": d, "call": c, "put": p})
    return pd.DataFrame(rows).set_index("day")


def events(flow):
    """أيام الشذوذ: z ≥ 2 مقابل 20 يوماً سابقة، والاتجاه من حصة الكول."""
    tot = flow["call"] + flow["put"]
    mu = tot.shift(1).rolling(LOOKBACK).mean()
    sd = tot.shift(1).rolling(LOOKBACK).std()
    z = (tot - mu) / sd.replace(0, np.nan)
    share = flow["call"] / tot.replace(0, np.nan)
    d = np.where(share >= SHARE_UP, 1, np.where(share <= SHARE_DN, -1, 0))
    out = pd.DataFrame({"z": z, "share": share, "dir": d, "tot": tot})
    return out[(out["z"] >= Z_MIN) & (out["dir"] != 0)]


def outcome(ev, opens, closes, spy_o, spy_c):
    """عائد السهم − عائد SPY من افتتاح اليوم التالي حتى إغلاق اليوم h بعده، في جهة الحدث."""
    days = list(closes.index)
    pos = {d: i for i, d in enumerate(days)}
    rows = []
    for d, r in ev.iterrows():
        i = pos.get(d)
        if i is None or i + 1 >= len(days):
            continue
        d1 = days[i + 1]
        row = {"day": d, "dir": int(r["dir"]), "z": float(r["z"]), "share": float(r["share"])}
        for h in HORIZONS:
            if i + h >= len(days):
                row[f"r{h}"] = None
                continue
            dh = days[i + h]
            try:
                rs = closes[dh] / opens[d1] - 1
                rm = spy_c[dh] / spy_o[d1] - 1
                row[f"r{h}"] = int(r["dir"]) * (rs - rm)
            except KeyError:
                row[f"r{h}"] = None
        rows.append(row)
    return rows


def vs_random(v, sims=10000, seed=43):
    v = np.array(v)
    rand = (np.abs(v)[None, :] * np.random.default_rng(seed).choice([-1, 1], (sims, len(v)))).mean(1)
    return float(v.mean()), float((rand >= v.mean()).mean()), float((v > 0).mean())


# ============================ البيانات ============================
def read_daily(sym):
    if sym == "SPY":
        p = os.path.join(DATA, "spy_5m.csv.gz")
    else:
        p = os.path.join(DATA, "bars_5m", f"{sym}.csv.gz")
    d = pd.read_csv(p)
    d.index = pd.DatetimeIndex(pd.to_datetime(d["t"], utc=True)).tz_convert(NY)
    d = d[(d.index.time >= dtime(9, 30)) & (d.index.time < dtime(16, 0))]
    g = d.groupby(d.index.date)
    return g["o"].first().astype(float), g["c"].last().astype(float)


def fetch_daily_volume(symbols, start, end):
    """{رمز: Series حجم يومي بفهرس تاريخ} من شموع Alpaca اليومية للعقود."""
    out = {}
    syms = list(symbols)
    for i in range(0, len(syms), 100):
        page = None
        while True:
            q = {"symbols": ",".join(syms[i:i + 100]), "timeframe": "1Day", "start": start, "end": end, "limit": 10000}
            if page:
                q["page_token"] = page
            req = urllib.request.Request("https://data.alpaca.markets/v1beta1/options/bars?" + urllib.parse.urlencode(q),
                                         headers={"APCA-API-KEY-ID": os.getenv("ALPACA_KEY", "").strip(),
                                                  "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET", "").strip()})
            for attempt in range(3):
                try:
                    j = json.loads(urllib.request.urlopen(req, timeout=60).read())
                    break
                except Exception:  # noqa: BLE001
                    if attempt == 2:
                        raise
                    _time.sleep(5 * (attempt + 1))
            for s, bars in (j.get("bars") or {}).items():
                for b in bars:
                    out.setdefault(s, {})[pd.Timestamp(b["t"]).tz_convert(NY).date()] = float(b.get("v") or 0)
            page = j.get("next_page_token")
            if not page:
                break
        _time.sleep(0.3)
    return {s: pd.Series(v) for s, v in out.items()}


# ============================ التشغيل والتقرير ============================
def run(symbols, fetch=None, read=None, today=None):
    fetch = fetch or fetch_daily_volume
    read = read or read_daily
    today = today or date.today()
    spy_o, spy_c = read("SPY")
    allrows = []
    for sym in symbols:
        try:
            o, c = read(sym)
        except Exception as e:  # noqa: BLE001
            print("تخطي", sym, type(e).__name__)
            continue
        c = c[(c.index >= START) & (c.index <= today - timedelta(days=8))]
        o = o.reindex(c.index)
        if len(c) < LOOKBACK + 10:
            continue
        meta = plan_symbols(sym, c)
        end = min(datetime.combine(today, dtime(0, 0)), datetime.combine(c.index[-1] + timedelta(days=DTE_MAX + 1), dtime(0, 0)))
        try:
            vol = fetch(list(meta), f"{c.index[0] - timedelta(days=1)}T00:00:00Z", end.strftime("%Y-%m-%dT00:00:00Z"))
        except Exception as e:  # noqa: BLE001
            print("فشل الجلب:", sym, getattr(e, "code", type(e).__name__))
            continue
        flow = daily_flow(vol, meta, c)
        ev = events(flow)
        rows = outcome(ev, o, c, spy_o, spy_c)
        for r in rows:
            r["sym"] = sym
        allrows += rows
        print("انتهى", sym, "أحداث", len(rows))
    return pd.DataFrame(allrows)


def report(df, meta):
    L = ["# H-FLOWH: شذوذ حجم الخيارات وحركة السهم (تاريخي، أحجام Alpaca الحقيقية)", "", f"- {meta}",
         "- الحدث: z ≥ 2 لحجم الخيارات القريبة (±8%، أقرب انتهاءين 5–35 يوماً) مقابل 20 يوماً، والاتجاه من حصة الكول (≥65% صعود، ≤35% هبوط).",
         "- النتيجة: عائد السهم − SPY من افتتاح اليوم التالي، في جهة الحدث. الحجم لا يميز الشراء من البيع. عائد السهم ليس ربح العقد.", ""]
    if df.empty or len(df) < 20:
        return L + [f"⚖️ H-FLOWH: لا حكم (أحداث {len(df)})"]
    days = sorted(df["day"].unique())
    mid = days[len(days) // 2]
    A, B = df[df["day"] < mid], df[df["day"] >= mid]
    L += ["| الجزء | المدة | أحداث | متوسط العائد في الجهة | في الجهة الصحيحة | عشوائي أفضل منه |", "|---|---|---|---|---|---|"]
    res = {}
    for name, part in (("الاكتشاف (النصف الأول)", A), ("الحكم (النصف الثاني)", B)):
        for h in HORIZONS:
            v = part[f"r{h}"].dropna().values
            if len(v) < 10:
                continue
            m, p, hit = vs_random(v)
            res[(name, h)] = (m, p)
            L.append(f"| {name} | {h} يوم | {len(v)} | {100 * m:+.2f}% | {100 * hit:.0f}% | {100 * p:.1f}% |")
    mB, pB = res.get(("الحكم (النصف الثاني)", 5), (None, None))
    mA, _ = res.get(("الاكتشاف (النصف الأول)", 5), (None, None))
    ok = mB is not None and pB < 0.05 and mB > 0 and mA is not None and mA > 0
    L += ["", f"⚖️ H-FLOWH: {'تحققت' if ok else 'لم تتحقق'} (الحكم 5 أيام: {('-' if mB is None else f'{100 * mB:+.2f}%')}، "
              f"عشوائي أفضل منه {('-' if pB is None else f'{100 * pB:.1f}%')}؛ الاكتشاف {('-' if mA is None else f'{100 * mA:+.2f}%')})", "",
          "## وصفي (كل الأيام، 5 أيام)", "", "| المجموعة | أحداث | متوسط | في الجهة الصحيحة |", "|---|---|---|---|"]
    for name, g in (("كول غالب (صعود)", df[df["dir"] == 1]), ("بوت غالب (هبوط)", df[df["dir"] == -1]),
                    ("z بين 2 و3", df[df["z"] < 3]), ("z ≥ 3", df[df["z"] >= 3])):
        v = g["r5"].dropna()
        if len(v):
            L.append(f"| {name} | {len(v)} | {100 * v.mean():+.2f}% | {100 * (v > 0).mean():.0f}% |")
    return L


def main():
    import stocks_bot as sb
    if not (os.getenv("ALPACA_KEY") and os.getenv("ALPACA_SECRET")):
        print("يحتاج ALPACA_KEY وALPACA_SECRET (يعمل على الخادم).")
        return 0
    syms = [s for s in sb.WATCHLIST if os.path.exists(os.path.join(DATA, "bars_5m", f"{s}.csv.gz"))]
    df = run(syms)
    df.to_csv(OUT_CSV, index=False, lineterminator="\n")
    L = report(df, f"{df['sym'].nunique() if len(df) else 0} شركة، {len(df)} حدثاً، {START} → قبل 8 أيام من التشغيل")
    with open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
