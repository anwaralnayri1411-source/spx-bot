#!/usr/bin/env python3
"""ظل MGT (H-MGTCLONE، مسجّل في CLAUDE.md 5/11/2026 بطلب المستخدم). ورق فقط: لا رسائل توصية ولا تغيير في أي بوت.

ينسخ طريقة MGT كما استنتجناها (research/mgt_reverse_results.md): تصويت فريمات + ADX، زناد إغلاق 5د عبر خط Murrey Math،
عقد SPXW بسعر ≈ 3.50$ ووقف 50%، وسلّم كل 5 نقاط، وفي الجلستين الليلية والرسمية، والاتجاهان معاً كما يفعلون.
يسجّل كل دخول بكل ما كان حوله (القاما والدلتا والزخم…) ثم مسار العقد كل دقيقة، وتقرير نهاية الجلسة.
الملفات في SPXBOT_DATA/mgt_shadow/ (خارج git). تشغيل كل دقيقة: python mgt_shadow.py [--force] [--no-send]
"""
import json
import math
import os
import sys
import urllib.parse
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
NY = ZoneInfo("America/New_York")
DIR = os.path.join(os.getenv("SPXBOT_DATA", "/opt/spxbot/data"), "mgt_shadow")
STATE, ENTRIES, PATHS, TICKS = (os.path.join(DIR, f) for f in ("state.json", "entries.csv", "paths.csv", "es_ticks.csv"))
REPORT_MD = os.path.join(DIR, "reports.md")
PRICE_LO, PRICE_HI, PRICE_T = 2.90, 4.00, 3.50
STOP_FRAC = 0.50
LADDER_PTS, MAX_PER_SIDE = 5.0, 12
SCORE_MIN, ADX_MIN = 3, 15.0
GTH_START, GTH_END = dtime(20, 15), dtime(9, 15)
RTH_START, RTH_LAST_ENTRY, RTH_END = dtime(9, 30), dtime(15, 30), dtime(16, 0)
REPORT_AT = dtime(16, 5)


# ====================== دوال صافية ======================
def session(now):
    """'rth' أو 'gth' أو None. الليلية من الأحد 20:15 حتى الجمعة 09:15."""
    t, wd = now.time(), now.weekday()
    if wd < 5 and RTH_START <= t < RTH_END:
        return "rth"
    if (t >= GTH_START and wd in (6, 0, 1, 2, 3)) or (t < GTH_END and wd in (0, 1, 2, 3, 4)):
        return "gth"
    return None


def trade_date(now):
    """تاريخ عقد 0DTE: اليوم حتى 16:15، وإلا يوم العمل التالي."""
    d = now.date()
    if now.time() >= dtime(16, 15) or now.weekday() >= 5:
        d += timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
    return d


def octave(range5d):
    for o in (78.125, 156.25, 312.5, 625.0, 1250.0):
        if range5d <= o:
            return o
    return 2500.0


def mml_lines(price, octv):
    """خطوط Murrey 1/16 حول السعر: [(السعر، الترتيب k/16 ضمن الأوكتاف)]."""
    base = math.floor(price / octv) * octv
    step = octv / 16
    return [(base + k * step, k) for k in range(-16, 33)]


def crossed_line(prev_c, c, lines, side):
    """خط Murrey عبَره إغلاق الشمعة في اتجاه الجهة، أو None."""
    for lv, k in lines:
        if side == "CALL" and prev_c < lv <= c:
            return lv, k % 16
        if side == "PUT" and prev_c > lv >= c:
            return lv, k % 16
    return None


def ema(s, n=20):
    return s.ewm(span=n, adjust=False).mean()


def tf_vote(close):
    """+1 إن كان الإغلاق فوق EMA20 وهي صاعدة، −1 إن تحتها وهابطة، وإلا 0."""
    if len(close) < 25:
        return 0
    e = ema(close)
    up = close.iloc[-1] > e.iloc[-1] and e.iloc[-1] > e.iloc[-4]
    dn = close.iloc[-1] < e.iloc[-1] and e.iloc[-1] < e.iloc[-4]
    return 1 if up else (-1 if dn else 0)


def adx(df, n=14):
    h, l, c = df["h"], df["l"], df["c"]
    up, dn = h.diff(), -l.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(pdm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    ndi = 100 * pd.Series(ndm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return float(dx.ewm(alpha=1 / n, adjust=False).mean().iloc[-1]) if len(df) > 2 * n else float("nan")


def rsi(c, n=14):
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / n).mean(), (-d.clip(upper=0)).ewm(alpha=1 / n).mean()
    return float((100 - 100 / (1 + up / dn.replace(0, np.nan))).iloc[-1])


def resample(m1, rule):
    return m1.resample(rule, label="left", closed="left").agg({"o": "first", "h": "max", "l": "min", "c": "last", "v": "sum"}).dropna()


def decide(m1, sess_open, sess_vwap, pressure=0, flow=0):
    """(الجهة أو None، المجموع، التفاصيل) من شموع دقيقة SPX المكافئة (o,h,l,c,v)."""
    votes = {f"M{k}": tf_vote(resample(m1, f"{k}min")["c"]) if k > 1 else tf_vote(m1["c"]) for k in (1, 5, 15, 30)}
    S = float(m1["c"].iloc[-1])
    sess = (1 if S > sess_open else -1) if sess_open else 0
    sess += (1 if S > sess_vwap else -1) if sess_vwap else 0
    sess = int(np.sign(sess))
    total = sum(votes.values()) + sess + int(np.sign(pressure)) + int(np.sign(flow))
    m5 = resample(m1, "5min")
    a = adx(m5)
    det = dict(votes, session=sess, pressure=int(np.sign(pressure)), flow=int(np.sign(flow)), score=total, adx=a,
               rsi=rsi(m5["c"]) if len(m5) > 15 else float("nan"))
    side = None
    if a == a and a >= ADX_MIN:
        side = "CALL" if total >= SCORE_MIN else ("PUT" if total <= -SCORE_MIN else None)
    return side, total, det


def pick_contract(chain, side, S):
    """السترايك خارج المال بسعر طلب أقرب إلى 3.50$ ضمن 2.90–4.00$، أو None."""
    ok = [r for r in chain if r["side"] == side and PRICE_LO <= r["ask"] <= PRICE_HI and
          ((side == "CALL" and r["strike"] > S) or (side == "PUT" and r["strike"] < S))]
    return min(ok, key=lambda r: abs(r["ask"] - PRICE_T)) if ok else None


def may_ladder(st, side, S, day):
    """سلّم: أول دخول للجهة، أو تقدّم ≥ 5 نقاط منذ آخر دخول لها، وتحت السقف."""
    mine = [e for e in st["open"] + st["closed"] if e["side"] == side and e["tdate"] == day]
    if len(mine) >= MAX_PER_SIDE:
        return False
    if not mine:
        return True
    last = max(mine, key=lambda e: e["t"])
    return (S - last["S"]) * (1 if side == "CALL" else -1) >= LADDER_PTS


def update_path(e, bid, ask, now):
    """يحدّث القمة والوقف ونصف+متحرك على سعر الوسط/العرض."""
    mid = (bid + ask) / 2 if ask > 0 else bid
    if mid > e["peak"]:
        e["peak"], e["t_peak"] = mid, now.strftime("%H:%M")
    if e.get("t_stop") is None and bid <= e["entry"] * STOP_FRAC:
        e["t_stop"], e["stop_before_peak"] = now.strftime("%H:%M"), e["peak"] < e["entry"] * 1.5
    if e.get("t_tp50") is None and bid >= e["entry"] * 1.5 and e.get("t_stop") is None:
        e["t_tp50"] = now.strftime("%H:%M")
    if e.get("half") is None and e.get("t_stop") is None and bid >= 2 * e["entry"]:
        e["half"], e["trail_peak"] = True, bid
    if e.get("half") and e.get("trail_exit") is None:
        e["trail_peak"] = max(e["trail_peak"], bid)
        if bid <= e["trail_peak"] * 0.5:
            e["trail_exit"] = bid
    e["last_bid"], e["last_mid"] = bid, mid


def results(e):
    """النتائج بالدولار للعقد الواحد بأربع طرق."""
    E = e["entry"]
    peak_gain = (e["peak"] - E) * 100
    win_mgt = peak_gain >= (50 if E * 100 >= 200 else 0.15 * E * 100)
    mgt = peak_gain if win_mgt else -STOP_FRAC * E * 100
    if e.get("t_tp50") is not None:
        real = 0.5 * E * 100
    elif e.get("t_stop") is not None:
        real = -STOP_FRAC * E * 100
    else:
        real = (e.get("last_bid", 0) - E) * 100
    if e.get("half"):
        rest = e.get("trail_exit", e.get("last_bid", 0))
        house = 0.5 * E * 100 + 0.5 * (rest - E) * 100
    elif e.get("t_stop") is not None:
        house = -STOP_FRAC * E * 100
    else:
        house = (e.get("last_bid", 0) - E) * 100
    return {"usd_mgt": round(mgt, 2), "usd_real": round(real, 2), "usd_house": round(house, 2),
            "usd_hold": round((e.get("last_bid", 0) - E) * 100, 2)}


# ====================== الجلب ======================
def es_mark():
    import live_quotes as lq
    tok = lq._tasty_token()
    items = (lq._tasty_req("/instruments/futures?product-code[]=ES", token=tok).get("data") or {}).get("items") or []
    sym = next((i["symbol"] for i in items if i.get("active-month")), None)
    it = ((lq._tasty_req("/market-data/by-type?" + urllib.parse.urlencode({"future": sym}), token=tok).get("data") or {}).get("items") or [{}])[0]
    return float(it.get("mark") or it.get("last") or 0), float(it.get("prev-close") or 0)


def live_m1(hist, ticks):
    """شموع دقيقة لحظية: تاريخ ياهو قبل أول عيّنة لحظية، ثم عيّنات ES من Tastytrade (كل دقيقة، 3 عيّنات للقمة والقاع).
    ticks: DataFrame بفهرس الوقت وعمود mark. فيبقى ياهو للماضي فقط، والذيل الحديث كله لحظي."""
    if ticks is None or ticks.empty:
        return hist
    t = ticks["mark"].resample("1min").agg(["first", "max", "min", "last"]).dropna()
    t.columns = ["o", "h", "l", "c"]
    t["v"] = 0.0
    start = t.index[0]
    h = hist[hist.index < start] if hist is not None and len(hist) else hist
    return pd.concat([h, t]) if h is not None and len(h) else t


def sample_es(n=3, gap=15):
    """عيّنات سعر ES اللحظية (Tastytrade) داخل الدقيقة لقمتها وقاعها؛ تُحفظ في es_ticks.csv."""
    import time
    out = []
    for i in range(n):
        try:
            m, prev = es_mark()
            if m > 0:
                out.append({"time": datetime.now(NY).isoformat(timespec="seconds"), "mark": m})
        except Exception as e:  # noqa: BLE001
            print("عيّنة ES:", e)
        if i < n - 1:
            time.sleep(gap)
    if out:
        os.makedirs(DIR, exist_ok=True)
        pd.DataFrame(out).to_csv(TICKS, mode="a", header=not os.path.exists(TICKS), index=False)
    return out


def load_ticks(days=3):
    try:
        t = pd.read_csv(TICKS)
        t.index = pd.to_datetime(t["time"], utc=True).dt.tz_convert(NY)
        t = t[t.index >= datetime.now(NY) - timedelta(days=days)]
        return t[["mark"]].sort_index()
    except Exception:  # noqa: BLE001
        return None


def es_bars():
    import yfinance as yf
    h = yf.Ticker("ES=F").history(period="5d", interval="1m", auto_adjust=False)
    h.index = h.index.tz_convert(NY)
    return h.rename(columns={"Open": "o", "High": "h", "Low": "l", "Close": "c", "Volume": "v"})[["o", "h", "l", "c", "v"]]


def chain(tdate, S):
    """عقود SPXW لتاريخ tdate ضمن ±3% (عرض/طلب/دلتا/تذبذب/اهتمام/حجم) من Tastytrade."""
    import live_quotes as lq
    tok = lq._tasty_token()
    ch = (lq._tasty_req("/option-chains/SPX/nested", token=tok).get("data") or {}).get("items") or []
    meta = {}
    for r in ch:
        if r.get("root-symbol") != "SPXW":
            continue
        for e in r.get("expirations") or []:
            if e["expiration-date"] == tdate.isoformat():
                for k in e.get("strikes") or []:
                    K = float(k["strike-price"])
                    if abs(K / S - 1) <= 0.03:
                        meta[k["call"]], meta[k["put"]] = ("CALL", K), ("PUT", K)
    return quotes(list(meta), meta)


def quotes(syms, meta=None):
    import live_quotes as lq
    tok = lq._tasty_token()
    out = []
    for i in range(0, len(syms), 50):
        q = urllib.parse.urlencode([("equity-option", s) for s in syms[i:i + 50]])
        for it in (lq._tasty_req(f"/market-data/by-type?{q}", token=tok).get("data") or {}).get("items") or []:
            f = lambda k: float(it.get(k) or 0)
            r = {"symbol": it.get("symbol"), "bid": f("bid"), "ask": f("ask"), "delta": f("delta"), "iv": f("volatility"),
                 "oi": f("open-interest"), "volume": f("volume")}
            if meta and r["symbol"] in meta:
                r["side"], r["strike"] = meta[r["symbol"]]
            out.append(r)
    return out


def flow_now(now):
    """آخر لقطة لمراقب السيولة (الرسمية): القاما والجدران وميل التدفق 30د."""
    try:
        fl = pd.read_csv(os.path.join(ROOT, "spx_flow_log.csv"), dtype={"time": str})
        g = fl[fl["date"] == now.date().isoformat()].sort_values("time")
        if not len(g):
            return {}
        r = g.iloc[-1]
        w = g[g["time"] > (now - timedelta(minutes=30)).strftime("%H:%M")]
        lean = float(w["call_prem"].fillna(0).sum() - w["put_prem"].fillna(0).sum())
        return {"gex_m": r["net_gex_m"], "flip": r["flip"], "call_wall": r["call_wall"], "put_wall": r["put_wall"],
                "flow_lean_30m": lean, "flow_time": r["time"]}
    except Exception:  # noqa: BLE001
        return {}


def spy_pressure(now):
    """ضغط الحجم: حجم شموع SPY الصاعدة − الهابطة آخر 30 دقيقة (Alpaca IEX، الرسمية)."""
    try:
        import live_quotes as lq
        start = (now - timedelta(minutes=40)).astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")
        q = urllib.parse.urlencode({"timeframe": "5Min", "start": start, "feed": "iex", "limit": 20, "adjustment": "raw"})
        rows = (lq._get(f"{lq.DATA}/v2/stocks/SPY/bars?{q}").get("bars") or [])[-6:]
        return float(sum(np.sign(float(r["c"]) - float(r["o"])) * float(r["v"]) for r in rows))
    except Exception:  # noqa: BLE001
        return 0.0


# ====================== التشغيل ======================
def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"open": [], "closed": [], "basis": None, "last_bar": None, "report_day": ""}


def save(st):
    os.makedirs(DIR, exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)


def append(path, row):
    os.makedirs(DIR, exist_ok=True)
    pd.DataFrame([row]).to_csv(path, mode="a", header=not os.path.exists(path), index=False)


def outcome(e):
    """نتيجة الخطة الواقعية للعقد: هدف أو وقف أو للإغلاق."""
    return "هدف" if e.get("t_tp50") is not None else "وقف" if e.get("t_stop") is not None else "إغلاق"


def entry_blocks(rs):
    """يجمّع الدخولات المتتالية من الجلسة والجهة نفسيهما في كتلة واحدة."""
    out = []
    for e in sorted(rs, key=lambda x: x["t"]):
        if out and (out[-1][0]["sess"], out[-1][0]["side"]) == (e["sess"], e["side"]):
            out[-1].append(e)
        else:
            out.append([e])
    return out


def block_line(b):
    a, z = b[0], b[-1]
    t0, t1 = a["t"][11:16], z["t"][11:16]
    strikes = list(dict.fromkeys(f"{e['strike']:g}" for e in b))
    prices = [e["entry"] for e in b]
    n = {k: sum(outcome(e) == k for e in b) for k in ("هدف", "وقف", "إغلاق")}
    res = " · ".join(x for x in (f"✅ {n['هدف']} هدف" if n["هدف"] else "", f"🛑 {n['وقف']} وقف" if n["وقف"] else "",
                                 f"⏳ {n['إغلاق']} للإغلاق" if n["إغلاق"] else "") if x)
    return (f"{'🌙 ليلاً' if a['sess'] == 'gth' else '☀️ الجلسة'} {t0}" + (f"–{t1}" if t1 != t0 else "")
            + f": **{len(b)} {a['side']}** بسترايك {('، '.join(strikes))}\n"
            + f"   السعر {min(prices):.2f}–{max(prices):.2f}$ | SPX {a['S']:,.0f}" + (f" ← {z['S']:,.0f}" if len(b) > 1 else "")
            + f"\n   النتيجة: {res} = **{sum(e['usd_real'] for e in b):+,.0f}$**")


def report(st, day):
    es = [e for e in st["closed"] + st["open"] if e["tdate"] == day]
    if not es:
        return f"🧪 **ظل MGT** {day}: لا دخولات."
    rs = [dict(e, **results(e)) for e in es]
    df = pd.DataFrame(rs)
    L = [f"🧪 **ظل MGT (ورقي)** · عقود {day}", "",
         f"**النتيجة الواقعية: {df.usd_real.sum():+,.0f}$** (بيع عند +50% أو وقف عند −50%، لعقد واحد من كل دخول)",
         f"{len(df)} عقداً: {int((df.usd_real > 0).sum())} رابحة · {int((df.usd_real < 0).sum())} خاسرة | "
         f"التكلفة {df.entry.sum() * 100:,.0f}$", "", "**أين فتح العقود (نيويورك):**"]
    L += [block_line(b) for b in entry_blocks(rs)]
    L += ["", "**لو أدار العقود نفسها بطريقة أخرى:**",
          f"• نصف عند +100% والباقي بوقف متحرك: {df.usd_house.sum():+,.0f}$",
          f"• احتفاظ حتى الإغلاق دون وقف: {df.usd_hold.sum():+,.0f}$",
          f"• سقف نظري بطريقة MGT (البيع عند القمة، حتى بعد ضرب الوقف): {df.usd_mgt.sum():+,.0f}$",
          "", "⚠️ ورقي للدراسة، وليس توصية. يتداول الاتجاهين كما يفعل MGT."]
    return "\n".join(L)


def main(argv):
    now = datetime.now(NY)
    force, no_send = "--force" in argv, "--no-send" in argv
    st = load_state()
    sess = session(now)
    tdate = trade_date(now).isoformat()
    # 1) متابعة المفتوح
    if st["open"]:
        try:
            qs = {q["symbol"]: q for q in quotes([e["symbol"] for e in st["open"]])}
        except Exception as e:  # noqa: BLE001
            print("أسعار المفتوح:", e)
            qs = {}
        keep = []
        for e in st["open"]:
            q = qs.get(e["symbol"])
            if q:
                update_path(e, q["bid"], q["ask"], now)
                append(PATHS, {"time": now.isoformat(timespec="minutes"), "id": e["id"], "bid": q["bid"], "ask": q["ask"],
                               "delta": q["delta"], "iv": q["iv"]})
            expired = now.date().isoformat() >= e["tdate"] and now.time() >= RTH_END
            (st["closed"] if expired else keep).append(e)
        st["open"] = keep
    # 2) تقرير نهاية الجلسة
    today = now.date().isoformat()
    if now.weekday() < 5 and now.time() >= REPORT_AT and st.get("report_day") != today and             any(e["tdate"] == today for e in st["open"] + st["closed"]):
        txt = report(st, today)
        os.makedirs(DIR, exist_ok=True)
        with open(REPORT_MD, "a", encoding="utf-8") as f:
            f.write(txt.replace("**", "") + "\n\n")
        print(txt)
        if not no_send:
            import analyst as an
            an.send(txt, "mgt_shadow")
        st["report_day"] = today
    # 3) دخول جديد
    can_enter = sess == "gth" or (sess == "rth" and now.time() <= RTH_LAST_ENTRY)
    if not (can_enter or force):
        save(st)
        print("خارج نافذة الدخول.", sess)
        return
    try:
        es, _prev = es_mark()
        sample_es()                                 # عيّنات لحظية تبني الشموع الحديثة بدل ياهو المتأخر
        es, _prev = es_mark()
        bars = live_m1(es_bars(), load_ticks())
    except Exception as e:  # noqa: BLE001
        print("بيانات ES:", e)
        save(st)
        return
    if sess == "rth":
        try:
            import live_quotes as lq
            spx, _ = lq.spot("SPX")
            if spx:
                st["basis"] = es - spx
        except Exception:  # noqa: BLE001
            pass
    if st.get("basis") is None:              # أول تشغيل ليلاً: الفرق من إغلاق SPX الرسمي وES عند 16:00
        try:
            import yfinance as yf
            spx_close = float(yf.Ticker("^GSPC").history(period="5d")["Close"].iloc[-1])
            b16 = bars[bars.index.strftime("%H:%M") == "16:00"]
            st["basis"] = float(b16["c"].iloc[-1]) - spx_close if len(b16) else 0.0
        except Exception:  # noqa: BLE001
            st["basis"] = 0.0
    if st.get("vix_day") != today:
        try:
            import yfinance as yf
            st["vix"] = float(yf.Ticker("^VIX").history(period="5d")["Close"].iloc[-1])
            st["vix_day"] = today
        except Exception:  # noqa: BLE001
            pass
    basis = st["basis"]
    m1 = bars.copy()
    m1[["o", "h", "l", "c"]] -= basis
    S = es - basis
    m1.loc[m1.index[-1], "c"] = S
    sess_start = datetime.combine(now.date() if now.time() >= GTH_START or sess == "rth" else now.date() - timedelta(days=1),
                                  RTH_START if sess == "rth" else GTH_START, NY)
    sb = m1[m1.index >= sess_start]
    sess_open = float(sb["o"].iloc[0]) if len(sb) else None
    vw = float((sb["c"] * sb["v"]).sum() / sb["v"].sum()) if len(sb) and sb["v"].sum() > 0 else None
    pressure = spy_pressure(now) if sess == "rth" else 0.0
    fl = flow_now(now) if sess == "rth" else {}
    side, score, det = decide(m1, sess_open, vw, pressure, fl.get("flow_lean_30m", 0) or 0)
    m5 = resample(m1, "5min")
    done5 = m5[m5.index + pd.Timedelta(minutes=5) <= now]
    if len(done5) < 3 or side is None:
        save(st)
        print("لا قرار:", side, score, {k: det[k] for k in ("M1", "M5", "M15", "M30", "session", "adx")})
        return
    last_bar = done5.index[-1].isoformat()
    rng5 = float(m1["h"].max() - m1["l"].min())
    octv = octave(rng5)
    hit = crossed_line(float(done5["c"].iloc[-2]), float(done5["c"].iloc[-1]), mml_lines(S, octv), side) \
        if st.get("last_bar") != last_bar else None
    st["last_bar"] = last_bar
    ladder = may_ladder(st, side, S, tdate) and any(e["side"] == side and e["tdate"] == tdate for e in st["open"] + st["closed"])
    if not (hit or ladder) or not may_ladder(st, side, S, tdate):
        save(st)
        print("لا زناد:", side, score)
        return
    try:
        ch = chain(date.fromisoformat(tdate), S)
    except Exception as e:  # noqa: BLE001
        print("السلسلة:", e)
        save(st)
        return
    k = pick_contract(ch, side, S)
    if not k:
        save(st)
        print("لا عقد بسعر 2.90–4.00$")
        return
    em = (st.get("vix") or 17) / 100 / math.sqrt(252) * S
    c = m1["c"]
    e = {"id": f"{now:%m%d-%H%M}-{side}-{k['strike']:g}", "t": now.isoformat(timespec="minutes"), "sess": sess, "tdate": tdate,
         "side": side, "strike": k["strike"], "symbol": k["symbol"], "entry": k["ask"], "bid0": k["bid"], "S": round(S, 2),
         "es": es, "basis": round(basis, 2), "trigger": "mml" if hit else "ladder", "mml": f"{hit[0]:.2f} ({hit[1]}/16)" if hit else "",
         "octave": octv, "otm_pts": round(abs(k["strike"] - S), 1), "delta": k["delta"], "iv": k["iv"], "oi": k["oi"],
         "volume": k["volume"], "spread": round(k["ask"] - k["bid"], 2), **{kk: det[kk] for kk in det},
         "from_sess_open_em": round((S - sess_open) / em, 3) if sess_open else None,
         "vwap_dist_em": round((S - vw) / em, 3) if vw else None,
         "r15": round(S / float(c.iloc[-16]) - 1, 5) if len(c) > 16 else None,
         "r30": round(S / float(c.iloc[-31]) - 1, 5) if len(c) > 31 else None,
         "r60": round(S / float(c.iloc[-61]) - 1, 5) if len(c) > 61 else None,
         "es_vs_prev_settle": round(es / _prev - 1, 5) if _prev else None, **fl,
         "peak": k["ask"], "t_peak": now.strftime("%H:%M"), "t_stop": None}
    st["open"].append(e)
    append(ENTRIES, {kk: v for kk, v in e.items()})
    print("دخول ظل MGT:", e["id"], e["trigger"], e["mml"], "score", score)
    save(st)


if __name__ == "__main__":
    main(sys.argv[1:])
