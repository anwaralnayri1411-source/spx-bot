#!/usr/bin/env python3
"""المراقب اللحظي (ظل؛ تسجيل فقط — لا يرسل رسائل ولا يعدّل أي ملف من ملفات البوت).

السؤال: لو كان بوت SPX يعمل باستمرار ويفحص كل دقيقة بأسعار لحظية بدل «كل 30 دقيقة + تأخر GitHub + ياهو المتأخر»،
متى كان سيرسل التوصية؟ وبكم كان سعر العقد وقتها مقارنة بوقت إرسال البوت الفعلي؟

يعمل داخل مهمة GitHub طويلة (حتى ~6 ساعات) ويدور كل دقيقة:
  - شموع SPY دقيقة لحظية من Alpaca (IEX) → المدخلات السريعة: المتوسطات 9/21 على شموع 5 دقائق، VWAP، زخم 30 دقيقة،
    فجوة الافتتاح، نطاق أول 30 دقيقة، أعلى/أدنى أمس (كلها على مقياس SPY؛ النسب نفسها لـ SPX).
  - المدخلات البطيئة كل 15 دقيقة من ياهو كما يأخذها البوت: متوسطات 20/50 اليومية، VIX اليوم، ES، القياديات.
  - القرار بدالة البوت نفسها `options_bot.direction_points` وعتبة MIN_EDGE، مع نافذة الدخول وأيام FOMC ومنع تكرار
    نفس الاتجاه خلال REPEAT_MINUTES. (غير متاح هنا: جدران العقود وجاما الصنّاع، نقطة الألم، كول/بوت، صدمة الأخبار.)
  - عند الإشارة: يسجّل الوقت والاتجاه وأسعار عقدين من SPY (على السعر، وعقد «شبيه بالبوت» 100–350$) من Alpaca اللحظي.
التقرير يقارن إشارات المراقب بتوصيات البوت الفعلية (zero_dte_log.csv) وسعر عقد البوت في اللحظتين (شموع ياهو التاريخية).

الملفات (جديدة): live_watcher_state.json، live_watcher_signals.csv، live_watcher_edges.csv، live_watcher_runs.csv،
research/live_watcher_results.md.   يحتاج: ALPACA_KEY، ALPACA_SECRET.
تشغيل: python research/live_watcher.py [--once] [--report]
"""
import csv
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import options_bot as ob  # noqa: E402  قراءة فقط: direction_points والإعدادات والعطل

STATE = os.path.join(ROOT, "live_watcher_state.json")
SIGNALS = os.path.join(ROOT, "live_watcher_signals.csv")
EDGES = os.path.join(ROOT, "live_watcher_edges.csv")
RUNS = os.path.join(ROOT, "live_watcher_runs.csv")
REPORT = os.path.join(HERE, "live_watcher_results.md")
DATA = "https://data.alpaca.markets"
END_DATE = date(2026, 11, 20)        # نهاية التجربة ثم مراجعة مع المستخدم
MAX_RUN_MIN = 345                    # حد مهمة GitHub 6 ساعات (مع هامش للحفظ)
SLOW_EVERY_MIN = 15
MAX_AGE = 120
FOMC = {"2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"}
SIG_COLS = ["date", "time", "side", "edge", "bull", "bear", "spy", "r30", "above_vwap", "orb", "atm", "atm_bid", "atm_ask",
            "botlike", "botlike_bid", "botlike_ask"]


# ============================ Alpaca ============================
def _get(path, params):
    url = f"{DATA}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"APCA-API-KEY-ID": os.getenv("ALPACA_KEY", ""),
                                               "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET", ""),
                                               "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _age(ts):
    try:
        t = datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", ts.replace("Z", "+00:00")))
        return (datetime.now(timezone.utc) - t).total_seconds()
    except Exception:
        return None


def spy_bars(n, days=8):
    """شموع SPY دقيقة (IEX) لآخر أيام التداول، كإطار pandas بتوقيت نيويورك (o,h,l,c,v)."""
    import pandas as pd
    start = (n - timedelta(days=days)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    rows, token = [], None
    for _ in range(20):
        p = {"timeframe": "1Min", "start": start, "feed": "iex", "limit": 10000, "adjustment": "raw"}
        if token:
            p["page_token"] = token
        d = _get("/v2/stocks/SPY/bars", p)
        rows += d.get("bars") or []
        token = d.get("next_page_token")
        if not token:
            break
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["t"], utc=True).dt.tz_convert(ob.NY)
    df = df[["o", "h", "l", "c", "v"]].astype(float)
    m = df.index.hour * 60 + df.index.minute
    return df[(m >= 9 * 60 + 30) & (m < 16 * 60)]


def spy_quotes(exp, side, S):
    """(أقرب عقد للسعر، عقد شبيه بالبوت) من سلسلة SPY اللحظية: كل منهما (رمز، bid، ask) أو None."""
    d = _get("/v1beta1/options/snapshots/SPY", {"feed": "indicative", "expiration_date": exp, "limit": 1000,
                                                "strike_price_gte": f"{S - 12:.0f}", "strike_price_lte": f"{S + 12:.0f}"})
    cand = []
    for sym, s in (d.get("snapshots") or {}).items():
        m = re.match(r"^SPY\d{6}([CP])(\d{8})$", sym)
        q = s.get("latestQuote") or {}
        if not m or (m.group(1) == "C") != (side == "CALL"):
            continue
        bid, ask, age = q.get("bp"), q.get("ap"), _age(q.get("t") or "")
        if bid and ask and bid > 0 and ask > 0 and age is not None and age <= MAX_AGE:
            cand.append((int(m.group(2)) / 1000, sym, float(bid), float(ask)))
    if not cand:
        return None, None
    atm = min(cand, key=lambda x: abs(x[0] - S))
    otm = [x for x in cand if (x[0] > S if side == "CALL" else x[0] < S)
           and ob.CONTRACT_MIN_USD <= x[3] * 100 <= ob.CONTRACT_MAX_USD]
    bl = min(otm, key=lambda x: abs(x[0] - S)) if otm else None
    return atm[1:], (bl[1:] if bl else None)


# ============================ المدخلات ============================
def slow_inputs():
    """ما يأخذه البوت من ياهو ويتغير ببطء: يُحدَّث كل 15 دقيقة."""
    import yfinance as yf
    out = {"daily": None, "vix_day": None, "es": None, "leaders": None}
    try:
        d = yf.Ticker("SPY").history(period="1y")["Close"].dropna()
        out["daily"] = d[d.index.date < ob.now_ny().date()]
    except Exception as e:
        print("slow daily:", e)
    try:
        vx = yf.Ticker("^VIX").history(period="1d", interval="5m")["Close"].dropna()
        if len(vx) >= 3:
            out["vix_day"] = float(vx.iloc[-1] / vx.iloc[0] - 1)
    except Exception as e:
        print("slow vix:", e)
    try:
        es = yf.Ticker("ES=F").history(period="5d")["Close"].dropna()
        if len(es) >= 2:
            out["es"] = float(es.iloc[-1] / es.iloc[-2] - 1)
    except Exception as e:
        print("slow es:", e)
    try:
        import leaders
        out["leaders"] = leaders.compute()
    except Exception as e:
        print("slow leaders:", e)
    return out


def build_c(df, n, slow):
    """نفس مفاتيح c التي يبنيها ob.analyze، على مقياس SPY ومن شموع لحظية."""
    import pandas as pd
    today = df[df.index.date == n.date()]
    if len(today) < 2:
        return None
    prev_dates = sorted({x for x in df.index.date if x < n.date()})
    prev = df[df.index.date == prev_dates[-1]] if prev_dates else df.iloc[0:0]
    agg = {"o": "first", "h": "max", "l": "min", "c": "last", "v": "sum"}
    t5 = today.resample("5min", label="left", closed="left").agg(agg).dropna()
    c5 = df["c"].resample("5min", label="left", closed="left").last().dropna()
    S = float(today["c"].iloc[-1])
    daily = slow.get("daily")
    dser = pd.concat([daily, pd.Series([S])]) if daily is not None and len(daily) >= 50 else None
    tp = (today["h"] + today["l"] + today["c"]) / 3
    vol = today["v"].where(today["v"] > 0, 1.0)
    vwap = float((tp * vol).cumsum().iloc[-1] / vol.cumsum().iloc[-1])
    c = {"n": n, "S": S, "age": 0.0,
         "sma20": float(dser.rolling(20).mean().iloc[-1]) if dser is not None else S,
         "sma50": float(dser.rolling(50).mean().iloc[-1]) if dser is not None else S,
         "ema9": float(c5.ewm(span=9, adjust=False).mean().iloc[-1]),
         "ema21": float(c5.ewm(span=21, adjust=False).mean().iloc[-1]),
         "r30": float(t5["c"].iloc[-1] / t5["c"].iloc[-7] - 1) if len(t5) >= 7 else 0.0,
         "gap": float(t5["c"].iloc[0] / prev["c"].iloc[-1] - 1) if len(prev) else None,
         "prev_close": float(prev["c"].iloc[-1]) if len(prev) else None,
         "above_vwap": S > vwap, "vwap": vwap, "vwap_spx": None,
         "map": None, "max_pain": None, "pc": None,
         "es": slow.get("es"), "leaders": slow.get("leaders"), "vix_day": slow.get("vix_day"),
         "orb": (float(t5["h"].iloc[:6].max()), float(t5["l"].iloc[:6].min())) if len(t5) >= 7 else None,
         "pd_hl": (float(prev["h"].max()), float(prev["l"].min())) if len(prev) else None,
         "day_hl": (float(today["h"].max()), float(today["l"].min())), "pivots": [], "range1h": None}
    return c


def blocked(n):
    """سبب منع الدخول كما في البوت (الوقت والأحداث الكبرى)، أو None."""
    hm = (n.hour, n.minute)
    if not ob.market_day(n) or not ((9, 45) <= hm < ob.no_entry(n)):
        return "خارج نافذة الدخول"
    evs = [nm for d, nm in ob.upcoming_events(n.date()) if d == n.date()]
    if n.date().isoformat() in FOMC:
        evs.append("FOMC")
    for nm in evs:
        if not nm.startswith("تقرير") or hm < (10, 0):
            return nm
    return None


# ============================ الحلقة ============================
def _append(path, cols, row):
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(cols)
        w.writerow([row.get(k, "") for k in cols])


def load_state(today):
    try:
        s = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        s = {}
    if s.get("date") != today:
        s = {"date": today, "last_side": None, "last_t": None, "signals": 0}
    return s


def step(n, df, slow, state):
    """فحص دقيقة واحدة. يرجع صف الإشارة إن وُجدت."""
    c = build_c(df, n, slow)
    if c is None:
        return None
    bull, bear, _ = ob.direction_points(c)
    edge, side = abs(bull - bear), ("CALL" if bull > bear else "PUT")
    hm = n.strftime("%H:%M")
    _append(EDGES, ["date", "time", "spy", "bull", "bear", "side", "edge"],
            {"date": n.date().isoformat(), "time": hm, "spy": round(c["S"], 2), "bull": round(bull, 2),
             "bear": round(bear, 2), "side": side, "edge": round(edge, 2)})
    if edge < ob.MIN_EDGE or blocked(n):
        return None
    if state["last_side"] == side and state["last_t"] is not None:
        last = datetime.fromisoformat(state["last_t"])
        if (n - last).total_seconds() < ob.REPEAT_MINUTES * 60:
            return None
    row = {"date": n.date().isoformat(), "time": hm, "side": side, "edge": round(edge, 2), "bull": round(bull, 2),
           "bear": round(bear, 2), "spy": round(c["S"], 2), "r30": round(c["r30"] * 100, 3),
           "above_vwap": c["above_vwap"], "orb": "" if not c["orb"] else f"{c['orb'][1]:.2f}-{c['orb'][0]:.2f}"}
    try:
        atm, bl = spy_quotes(n.date().isoformat(), side, c["S"])
        if atm:
            row.update(atm=atm[0], atm_bid=atm[1], atm_ask=atm[2])
        if bl:
            row.update(botlike=bl[0], botlike_bid=bl[1], botlike_ask=bl[2])
    except Exception as e:
        print("quotes:", e)
    _append(SIGNALS, SIG_COLS, row)
    state.update(last_side=side, last_t=n.isoformat(), signals=state["signals"] + 1)
    json.dump(state, open(STATE, "w", encoding="utf-8"))
    print(f"[{hm}] إشارة {side} edge {edge:.1f} SPY {c['S']:.2f}")
    return row


def loop(once=False):
    start = ob.now_ny()
    if start.date() > END_DATE or not ob.market_day(start):
        print("لا جلسة اليوم أو انتهت التجربة.")
        return
    state = load_state(start.date().isoformat())
    slow, slow_t, minutes, errors = {}, None, 0, 0
    while True:
        n = ob.now_ny()
        if (n.hour, n.minute) >= (16, 0) or (n - start).total_seconds() / 60 > MAX_RUN_MIN:
            break
        if (n.hour, n.minute) >= (9, 31):
            try:
                if slow_t is None or (n - slow_t).total_seconds() >= SLOW_EVERY_MIN * 60:
                    slow, slow_t = slow_inputs(), n
                df = spy_bars(n)
                if df is not None and len(df):
                    step(n, df, slow, state)
                    minutes += 1
            except Exception as e:
                errors += 1
                print("خطأ في الدقيقة:", type(e).__name__, e)
        if once:
            break
        nxt = (n + timedelta(minutes=1)).replace(second=5, microsecond=0)
        time.sleep(max(1.0, (nxt - ob.now_ny()).total_seconds()))
    _append(RUNS, ["date", "start", "end", "minutes", "errors"],
            {"date": start.date().isoformat(), "start": start.strftime("%H:%M"), "end": ob.now_ny().strftime("%H:%M"),
             "minutes": minutes, "errors": errors})
    print(f"انتهى: {minutes} دقيقة مفحوصة، {errors} أخطاء.")


# ============================ التقرير ============================
def _price_at(contract, t):
    """إغلاق شمعة 5 دقائق التي تنتهي عند t لعقد (من شموع ياهو التاريخية)."""
    import yfinance as yf
    h = yf.Ticker(contract).history(period="5d", interval="5m")
    if not len(h):
        return None
    h = h.tz_convert(ob.NY)
    w = h[(h.index > t - timedelta(minutes=10)) & (h.index <= t)]
    return float(w["Close"].iloc[-1]) if len(w) else None


def report():
    rd = lambda p: list(csv.DictReader(open(p, encoding="utf-8"))) if os.path.exists(p) else []
    sig, runs = rd(SIGNALS), rd(RUNS)
    bot = [r for r in rd(os.path.join(ROOT, "zero_dte_log.csv")) if r["date"] >= "2026-10-20"]
    L = ["# المراقب اللحظي (ظل) — نتائج آلية", "",
         "- يفحص كل دقيقة بأسعار SPY لحظية (Alpaca IEX) وبدالة قرار البوت نفسها. لا يرسل شيئاً.",
         "- ⚠️ أسعار عقود الشموع من ياهو التاريخية (آخر صفقة في الشمعة) للمقارنة فقط.", "",
         "## التغطية", "", "| اليوم | التشغيلات | دقائق مفحوصة | أخطاء |", "|---|---|---|---|"]
    for d in sorted({r["date"] for r in runs}):
        R = [r for r in runs if r["date"] == d]
        L.append(f"| {d} | " + "، ".join(f"{r['start']}–{r['end']}" for r in R) +
                 f" | {sum(int(r['minutes']) for r in R)} | {sum(int(r['errors']) for r in R)} |")
    L += ["", "## توصيات البوت الفعلية مقابل المراقب اللحظي", "",
          "| اليوم | البوت أرسل | المراقب أشار (نفس الاتجاه) | سبق بـ | سعر عقد البوت وقت المراقب | وقت البوت | الفرق |",
          "|---|---|---|---|---|---|---|"]
    for b in bot:
        bt = datetime.fromisoformat(f"{b['date']}T{b['time']}").replace(tzinfo=ob.NY)
        same = [s for s in sig if s["date"] == b["date"] and s["side"] == b["side"]
                and datetime.fromisoformat(f"{s['date']}T{s['time']}").replace(tzinfo=ob.NY) <= bt]
        if not same:
            L.append(f"| {b['date']} | {b['time']} {b['side']} {b['symbol']} {float(b['strike']):g} | — | — | — | — | — |")
            continue
        s = max(same, key=lambda s: s["time"])
        st = datetime.fromisoformat(f"{s['date']}T{s['time']}").replace(tzinfo=ob.NY)
        root = "SPXW" if b["symbol"] == "^SPX" else b["symbol"].lstrip("^")
        cs = f"{root}{b['expiry'].replace('-', '')[2:]}{'C' if b['side'] == 'CALL' else 'P'}{int(float(b['strike']) * 1000):08d}"
        p1, p2 = _price_at(cs, st), _price_at(cs, bt)
        diff = f"{(p2 / p1 - 1) * 100:+.0f}%" if p1 and p2 else "—"
        L.append(f"| {b['date']} | {b['time']} {b['side']} {b['symbol']} {float(b['strike']):g} | {s['time']} | "
                 f"{(bt - st).total_seconds() / 60:.0f} د | {p1 or '—'} | {p2 or '—'} | {diff} |")
    L += ["", "## كل إشارات المراقب", "", "| اليوم | الوقت | الاتجاه | الفرق | SPY | عقد على السعر (طلب) | شبيه بالبوت (طلب) |",
          "|---|---|---|---|---|---|---|"]
    for s in sig[-40:]:
        L.append(f"| {s['date']} | {s['time']} | {s['side']} | {s['edge']} | {s['spy']} | {s['atm'] or '—'} ({s['atm_ask'] or '—'}) | "
                 f"{s['botlike'] or '—'} ({s['botlike_ask'] or '—'}) |")
    L += ["", "- «سبق بـ» = كم دقيقة قبل البوت كان المراقب سيرسل نفس الاتجاه. «الفرق» = كم تغيّر سعر عقد البوت بين اللحظتين.",
          "- هذه إشارات بقرار البوت نفسه لكن كل دقيقة؛ تكرارها أعلى يعني أن قواعد الدخول تحتاج ضبطاً قبل أي تشغيل لحظي حقيقي."]
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    if "--report" in sys.argv:
        report()
    elif not (os.getenv("ALPACA_KEY") and os.getenv("ALPACA_SECRET")):
        print("✗ المفاتيح غير موجودة (ALPACA_KEY / ALPACA_SECRET).")
        sys.exit(1)
    else:
        loop(once="--once" in sys.argv)
