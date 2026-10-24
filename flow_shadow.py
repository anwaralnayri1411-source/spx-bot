"""ظل التدفق (تسجيل فقط؛ لا يرسل ولا يغيّر أي توصية). بطلب المستخدم 24/10/2026، فرضيته H-FLOW مسجّلة في CLAUDE.md قبل البناء.

السؤال: هل شذوذ حجم الخيارات في سهم (مقارنة بمعتاده) باتجاه الكول أو البوت يسبق حركة السهم؟
كل يوم عمل عند 15:45 نيويورك (نافذة 15:40–15:59)، لكل سهم من قائمة البوت وSPY: أقرب انتهاءين بين 5 و35 يوماً، سترايكات ضمن ±8% من السعر (قائمة العقود من ياهو، والحجم والعقود المفتوحة من Tastytrade لحظياً).
نسجّل سطراً لكل سهم: حجم الكول والبوت، OI الكول والبوت، وحجم «المراكز الجديدة» (عقود حجمها اليوم > OI). الشذوذ والاتجاه والعوائد تُحسب في التقرير (ملف السجل يبقى خاماً).
تحفظ: الحجم الإجمالي لا يميز شراء بالطلب من بيع بالعرض ولا فتحاً من إغلاق؛ تغير OI بين لقطتين متتاليتين يعطي تقديراً للفتح (استكشافي).
الملفات: flow_shadow_log.csv، research/flow_shadow_results.md. تشغيل: python flow_shadow.py [--report]
"""
import csv
import math
import os
import sys
import urllib.parse
from datetime import date, datetime, timedelta

import numpy as np

import data_gaps
import live_quotes as lq
import options_bot as ob   # قراءة فقط: التوقيت والعطلات

LOG = "flow_shadow_log.csv"
REPORT = os.path.join("research", "flow_shadow_results.md")
LOG_COLS = ["date", "ticker", "spot", "n_contracts", "n_expiries", "call_vol", "put_vol", "call_oi", "put_oi", "call_fresh", "put_fresh", "status"]
EXP_MIN_DAYS, EXP_MAX_DAYS, EXP_COUNT = 5, 35, 2
STRIKE_BAND = 0.08
SNAP_WINDOW = (15 * 60 + 40, 15 * 60 + 59)
Z_MIN, Z_LOOKBACK = 2.0, 20
CALL_SHARE_UP, CALL_SHARE_DOWN = 0.65, 0.35
HORIZON = 5
MIN_EVENTS = 100


# ============================ دوال نقية ============================
def choose_expiries(exps, today):
    """أقرب EXP_COUNT انتهاءات بين EXP_MIN_DAYS وEXP_MAX_DAYS يوماً من today. exps: قائمة 'YYYY-MM-DD'."""
    ok = []
    for e in sorted(exps):
        d = (date.fromisoformat(e) - today).days
        if EXP_MIN_DAYS <= d <= EXP_MAX_DAYS:
            ok.append(e)
    return ok[:EXP_COUNT]


def band_contracts(rows, spot, band=STRIKE_BAND):
    """رموز العقود (contractSymbol) بسترايك ضمن ±band من السعر. rows: قائمة dict(contractSymbol, strike)."""
    return [r["contractSymbol"] for r in rows if spot and abs(float(r["strike"]) / spot - 1) <= band]


def occ_side(occ):
    """'C' أو 'P' من رمز OCC (…YYMMDDC00250000)."""
    return occ[-9]


def aggregate(items):
    """items: {occ: {'volume','oi'}}. يجمع حجم/OI الكول والبوت وحجم المراكز الجديدة (حجم > OI)."""
    a = {"call_vol": 0.0, "put_vol": 0.0, "call_oi": 0.0, "put_oi": 0.0, "call_fresh": 0.0, "put_fresh": 0.0}
    for occ, z in items.items():
        side = "call" if occ_side(occ) == "C" else "put"
        v, oi = float(z.get("volume") or 0), float(z.get("oi") or 0)
        a[side + "_vol"] += v
        a[side + "_oi"] += oi
        if v > oi:
            a[side + "_fresh"] += v
    return {k: round(v, 1) for k, v in a.items()}


def direction(call_vol, put_vol):
    tot = call_vol + put_vol
    if tot <= 0:
        return 0
    share = call_vol / tot
    return 1 if share >= CALL_SHARE_UP else (-1 if share <= CALL_SHARE_DOWN else 0)


def in_snapshot_window(n):
    m = n.hour * 60 + n.minute
    return ob.market_day(n) and SNAP_WINDOW[0] <= m <= SNAP_WINDOW[1]


# ============================ الشبكة ============================
def yahoo_chain(ticker, today):
    """(spot، قائمة dict(contractSymbol, strike) للانتهاءات المختارة، عدد الانتهاءات). يرفع عند الفشل."""
    import yfinance as yf
    t = yf.Ticker(ticker)
    spot, _ = lq.spot(ticker)
    if not spot:
        spot = float(t.history(period="2d")["Close"].dropna().iloc[-1])
    rows, exps = [], choose_expiries(list(t.options), today)
    for e in exps:
        ch = t.option_chain(e)
        for df in (ch.calls, ch.puts):
            rows += [{"contractSymbol": str(r["contractSymbol"]), "strike": float(r["strike"])} for _, r in df.iterrows()]
    return spot, rows, len(exps)


def tasty_volume_oi(occs):
    """{occ: {'volume','oi'}} من Tastytrade (دفعات 50). عقد بلا بيانات يغيب. لا يرفع استثناء واحداً لدفعة فاشلة."""
    out, back = {}, {}
    for c in occs:
        t = lq._tasty_occ(c)
        if t:
            back[t] = c
    keys = list(back)
    token = lq._tasty_token()
    for i in range(0, len(keys), 50):
        try:
            q = urllib.parse.urlencode([("equity-option", k) for k in keys[i:i + 50]])
            items = (lq._tasty_req(f"/market-data/by-type?{q}", token=token).get("data") or {}).get("items") or []
        except Exception as e:  # noqa: BLE001
            data_gaps.note("flow_shadow", "tasty_batch", e)
            continue
        for it in items:
            sym = it.get("symbol")
            if sym in back:
                out[back[sym]] = {"volume": float(it.get("volume") or 0), "oi": float(it.get("open-interest") or 0)}
    return out


# ============================ التشغيل ============================
def existing_keys():
    if not os.path.exists(LOG):
        return set()
    return {(r["date"], r["ticker"]) for r in csv.DictReader(open(LOG, encoding="utf-8"))}


def append_rows(rows):
    if not rows:
        return
    new = not os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in LOG_COLS})


def run(n=None, tickers=None, get_chain=None, get_items=None):
    n = n or ob.now_ny()
    if not in_snapshot_window(n):
        print("خارج نافذة اللقطة (15:40–15:59 نيويورك)؛ لا شيء.")
        return []
    if tickers is None:
        import stocks_bot as sb
        tickers = list(sb.WATCHLIST) + ["SPY"]
    get_chain = get_chain or yahoo_chain
    get_items = get_items or tasty_volume_oi
    today = n.date()
    done = existing_keys()
    rows = []
    for tk in tickers:
        if (today.isoformat(), tk) in done:
            continue
        try:
            spot, chain, n_exp = get_chain(tk, today)
            occs = band_contracts(chain, spot)
            items = get_items(occs) if occs else {}
        except Exception as e:  # noqa: BLE001
            data_gaps.note("flow_shadow", "ticker", e, tk)
            continue
        if not items:
            data_gaps.note("flow_shadow", "no_items", None, tk)
            continue
        rows.append(dict(date=today.isoformat(), ticker=tk, spot=round(float(spot), 2), n_contracts=len(items), n_expiries=n_exp, status="ok", **aggregate(items)))
    append_rows(rows)
    print(f"ظل التدفق: {len(rows)} سهماً سُجّل من {len(tickers)}.")
    return rows


# ============================ التقرير ============================
def events_from_log(rows):
    """يبني الأحداث من سجل الخام: لكل (سهم، يوم) z لوغاريتم الحجم الكلي مقابل آخر Z_LOOKBACK لقطات سابقة، والاتجاه. rows: قائمة dict (نصوص). يرجع قائمة dict(date, ticker, z, dir, share)."""
    by = {}
    for r in rows:
        try:
            tot = float(r["call_vol"]) + float(r["put_vol"])
            by.setdefault(r["ticker"], []).append((r["date"], tot, float(r["call_vol"]), float(r["put_vol"])))
        except (KeyError, ValueError):
            continue
    ev = []
    for tk, seq in by.items():
        seq.sort()
        for i, (d, tot, cv, pv) in enumerate(seq):
            hist = [math.log(x[1]) for x in seq[max(0, i - Z_LOOKBACK):i] if x[1] > 0]
            if len(hist) < Z_LOOKBACK or tot <= 0:
                continue
            sd = float(np.std(hist, ddof=1))
            if sd <= 0:
                continue
            ev.append({"date": d, "ticker": tk, "z": (math.log(tot) - float(np.mean(hist))) / sd, "dir": direction(cv, pv), "share": cv / (cv + pv)})
    return ev


def attach_returns(ev, closes, horizon=HORIZON):
    """يضيف ar: عائد السهم − SPY من إغلاق يوم اللقطة إلى إغلاق بعد horizon أيام تداول. closes: {رمز: Series إغلاق بفهرس تاريخ}."""
    out = []
    spy = closes.get("SPY")
    for e in ev:
        s = closes.get(e["ticker"])
        if s is None or spy is None:
            continue
        d = np.datetime64(e["date"])
        idx = s.index.values.astype("datetime64[D]")
        if d not in idx:
            continue
        i = int(np.where(idx == d)[0][0])
        if i + horizon >= len(s):
            continue
        sp = spy.index.values.astype("datetime64[D]")
        if d not in sp:
            continue
        j = int(np.where(sp == d)[0][0])
        if j + horizon >= len(spy):
            continue
        ar = float(s.iloc[i + horizon] / s.iloc[i] - 1) - float(spy.iloc[j + horizon] / spy.iloc[j] - 1)
        out.append(dict(e, ar=ar))
    return out


def boot(by_day, n=2000, seed=37):
    days = list(by_day)
    if len(days) < 10:
        return None
    S = np.array([sum(by_day[d]) for d in days])
    N = np.array([len(by_day[d]) for d in days])
    idx = np.random.default_rng(seed).integers(0, len(days), (n, len(days)))
    m = S[idx].sum(1) / N[idx].sum(1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def stat(events):
    if not events:
        return None
    by = {}
    for e in events:
        by.setdefault(e["date"], []).append(e["dir"] * e["ar"])
    vals = [v for vs in by.values() for v in vs]
    return {"n": len(vals), "days": len(by), "mean": float(np.mean(vals)), "hit": float(np.mean([v > 0 for v in vals])), "ci": boot(by)}


def verdict(s):
    if s is None or s["n"] < MIN_EVENTS:
        return f"H-FLOW: لا حكم بعد ({0 if s is None else s['n']} من {MIN_EVENTS} حدثاً)"
    if s["ci"] is None:
        return "H-FLOW: لا حكم بعد (أيام قليلة)"
    lo, hi = s["ci"]
    desc = f"متوسط {100 * s['mean']:+.2f}%، فاصل {100 * lo:+.2f}…{100 * hi:+.2f}"
    return "H-FLOW: " + ("تحققت (" if lo > 0 else "عكسية (" if hi < 0 else "لم تتحقق (") + desc + (")" if lo > 0 or hi < 0 else " يشمل الصفر)")


def load_closes(tickers, first, last):
    import yfinance as yf
    px = yf.download(list(tickers), start=str(first - timedelta(days=5)), end=str(last + timedelta(days=15)), group_by="ticker", auto_adjust=True, progress=False)
    out = {}
    for t in tickers:
        try:
            out[t] = px[t]["Close"].dropna()
        except Exception:  # noqa: BLE001
            continue
    return out


def report(closes=None):
    rows = list(csv.DictReader(open(LOG, encoding="utf-8"))) if os.path.exists(LOG) else []
    L = ["# ظل التدفق: شذوذ حجم الخيارات (نتائج آلية، ورقي)", "",
         "- حجم الكول والبوت من Tastytrade لحظياً عند 15:45 نيويورك؛ الشذوذ z مقابل آخر 20 لقطة للسهم نفسه؛ الاتجاه من حصة الكول (≥65% صعود، ≤35% هبوط).",
         f"- **الفرضية المسجّلة مسبقاً H-FLOW:** z≥{Z_MIN:g} باتجاه ≠ 0، إشارة الاتجاه × عائد السهم المُزال SPY خلال {HORIZON} أيام > 0 بفاصل 95% بإعادة معاينة الأيام، بعد {MIN_EVENTS} حدثاً. الحجم الإجمالي لا يميز الشراء بالطلب من البيع بالعرض.", ""]
    if not rows:
        return _write(L + ["لا لقطات بعد."])
    days = sorted({r["date"] for r in rows})
    L += [f"- لقطات: {len(rows)} سطراً في {len(days)} يوماً ({days[0]} → {days[-1]}) لـ{len({r['ticker'] for r in rows})} سهماً.", ""]
    ev = events_from_log(rows)
    big = [e for e in ev if e["z"] >= Z_MIN and e["dir"] != 0]
    L += [f"- أحداث شاذة باتجاه: {len(big)} من {len(ev)} (سهم-يوم بعد اكتمال 20 لقطة سابقة).", ""]
    if closes is None and ev:
        try:
            closes = load_closes(sorted({e["ticker"] for e in big} | {"SPY"}), date.fromisoformat(days[0]), date.fromisoformat(days[-1]))
        except Exception as e:  # noqa: BLE001
            L += [f"- تعذر تحميل الأسعار ({type(e).__name__})."]
            closes = None
    scored = attach_returns(big, closes) if closes else []
    s = stat(scored)
    L += ["| الحدث | أحداث | أيام | متوسط (إشارة × عائد مُزال) | ربح | فاصل 95% (%) |", "|---|---|---|---|---|---|"]
    if s:
        ci = "-" if s["ci"] is None else f"{100 * s['ci'][0]:+.2f}…{100 * s['ci'][1]:+.2f}"
        L.append(f"| شذوذ z≥{Z_MIN:g} باتجاه | {s['n']} | {s['days']} | {100 * s['mean']:+.2f}% | {100 * s['hit']:.0f}% | {ci} |")
    L += ["", "⚖️ **" + verdict(s) + "**"]
    return _write(L)


def _write(L):
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    if "--report" in sys.argv:
        report()
    else:
        run()
