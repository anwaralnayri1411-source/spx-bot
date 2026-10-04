"""بوت فرص خيارات الشركات (يومي/أسبوعي): شراء Call أو Put -> تليجرام (لا ينفذ أي أوامر)

يعتمد على ملف options_bot.py الموجود في نفس المستودع (دوال التليجرام والرياضيات والأخبار).
الرسالة الأولى: أقوى الفرص (الشركة، الاتجاه، العقد، تاريخ الانتهاء، سعر التنفيذ).
الرسالة الثانية: أسباب كل فرصة، الأخبار، الأرباح، حالة السوق، سجل النتائج.
"""

import argparse
import json
import math
import os
import re
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

import options_bot as ob

# ====================== الإعدادات ======================
# الرمز: (اسم للبحث في الأخبار، محرك خاص: btc / oil / geo / None)
WATCHLIST = {
    "AAPL": ("Apple", None), "MSFT": ("Microsoft", None), "NVDA": ("Nvidia", None),
    "AMZN": ("Amazon", None), "GOOGL": ("Alphabet Google", None), "META": ("Meta Platforms", None),
    "TSLA": ("Tesla", None), "AVGO": ("Broadcom", None), "AMD": ("AMD", None),
    "NFLX": ("Netflix", None), "PLTR": ("Palantir", None), "UBER": ("Uber", None),
    "MA": ("Mastercard", None), "V": ("Visa", None), "JPM": ("JPMorgan", None),
    "BAC": ("Bank of America", None), "DIS": ("Disney", None), "WMT": ("Walmart", None),
    "BA": ("Boeing", None), "XOM": ("Exxon Mobil", "oil"), "CVX": ("Chevron", "oil"),
    "COIN": ("Coinbase", "btc"), "MSTR": ("MicroStrategy", "btc"), "HOOD": ("Robinhood", "btc"),
    "LMT": ("Lockheed Martin", "geo"),
}
CONTRACT_MIN_USD = 80
CONTRACT_MAX_USD = 200
MIN_OI = 500                # أقل مراكز مفتوحة (أو حجم تداول MIN_VOL)
MIN_VOL = 150
MAX_SPREAD = 0.30
MIN_EDGE = 3.5              # أقل فرق بين نقاط الصعود والهبوط لإعطاء فرصة
MIN_CONTRACT_SCORE = 5.0
MAX_IDEAS = 3
FINALISTS = 8               # عدد الشركات التي تُفحص بعمق (أخبار + سلسلة الخيارات)
MIN_DTE, MAX_DTE = 1, 9     # عقود من يوم إلى أسبوع وزيادة
TAKE_PROFIT = 0.50
STOP_LOSS = 0.40
WINDOW_START = (10, 0)      # بتوقيت نيويورك
WINDOW_END = (15, 30)
STATE_FILE = "stocks_state.json"
LOG_FILE = "stocks_log.csv"

POS_WORDS = ["upgrade", "upgrades", "beat", "beats", "surge", "surges", "soar", "soars", "rally", "rallies",
             "record", "launch", "launches", "unveil", "unveils", "partnership", "approval", "approved",
             "raises", "buyback", "bullish", "outperform", "jump", "jumps", "wins", "boost"]
NEG_WORDS = ["downgrade", "downgrades", "miss", "misses", "plunge", "plunges", "slump", "falls", "drops",
             "probe", "lawsuit", "sues", "recall", "cuts", "layoffs", "investigation", "ban", "tariff",
             "bearish", "underperform", "warning", "halt", "delay", "delays", "fraud", "crash"]


def in_window(n):
    return n.weekday() < 5 and WINDOW_START <= (n.hour, n.minute) <= WINDOW_END


# ====================== التحليل الفني ======================
def rsi14(c):
    d = c.diff().dropna()
    if len(d) < 15:
        return 50.0
    up = d.clip(lower=0).rolling(14).mean().iloc[-1]
    dn = (-d.clip(upper=0)).rolling(14).mean().iloc[-1]
    if dn == 0:
        return 100.0
    return float(100 - 100 / (1 + up / dn))


def tech_points(df, spy5, drv, completed_vol):
    c = df["Close"].astype(float)
    S = float(c.iloc[-1])
    sma20 = float(c.rolling(20).mean().iloc[-1])
    sma50 = float(c.rolling(50).mean().iloc[-1])
    ret5 = float(c.iloc[-1] / c.iloc[-6] - 1)
    day = float(c.iloc[-1] / c.iloc[-2] - 1)
    rsi = rsi14(c)
    rv = float(np.log(c).diff().tail(20).std() * math.sqrt(252))
    hi20 = float(df["High"].iloc[-21:-1].max())
    lo20 = float(df["Low"].iloc[-21:-1].min())
    vol = df["Volume"].astype(float)
    vbase = float(vol.iloc[-22:-2].mean()) if len(vol) >= 22 else float(vol.mean())
    vlast = float(completed_vol)
    vr = vlast / vbase if vbase > 0 else 1.0

    pts = {"bull": 0.0, "bear": 0.0}
    why = []

    def add(side, p, txt):
        pts[side] += p
        why.append((side, txt))

    if S > sma20 > sma50:
        add("bull", 2, "فوق المتوسطين 20 و50 والاتجاه صاعد")
    elif S > sma20 and S > sma50:
        add("bull", 1, "فوق المتوسطين")
    elif S < sma20 < sma50:
        add("bear", 2, "تحت المتوسطين 20 و50 والاتجاه هابط")
    elif S < sma20 and S < sma50:
        add("bear", 1, "تحت المتوسطين")
    if ret5 >= 0.03:
        add("bull", 1, f"زخم 5 أيام {ret5 * 100:+.1f}%")
    elif ret5 <= -0.03:
        add("bear", 1, f"زخم 5 أيام {ret5 * 100:+.1f}%")
    rs = ret5 - spy5
    if rs >= 0.02:
        add("bull", 1, f"أقوى من السوق بـ {rs * 100:.1f}% (5 أيام)")
    elif rs <= -0.02:
        add("bear", 1, f"أضعف من السوق بـ {abs(rs) * 100:.1f}% (5 أيام)")
    if S > hi20:
        add("bull", 1.5, "اختراق أعلى سعر في 20 يوماً")
    elif S < lo20:
        add("bear", 1.5, "كسر أدنى سعر في 20 يوماً")
    if day >= 0.015:
        add("bull", 1, f"حركة اليوم {day * 100:+.1f}%")
    elif day <= -0.015:
        add("bear", 1, f"حركة اليوم {day * 100:+.1f}%")
    if vr >= 1.5:
        lead = "bull" if pts["bull"] >= pts["bear"] else "bear"
        add(lead, 1, f"حجم تداول مرتفع ({vr:.1f}× المعتاد)")
    if drv is not None:
        label, chg = drv
        if chg >= 0.04:
            add("bull", 1, f"{label} صاعد ({chg * 100:+.1f}% خلال 5 أيام)")
        elif chg <= -0.04:
            add("bear", 1, f"{label} هابط ({chg * 100:+.1f}% خلال 5 أيام)")
    if rsi >= 75 and pts["bull"] > 0:
        pts["bull"] = max(0.0, pts["bull"] - 1)
        why.append(("bear", f"تشبع شراء (RSI {rsi:.0f}) يخفض الثقة بالصعود"))
    if rsi <= 25 and pts["bear"] > 0:
        pts["bear"] = max(0.0, pts["bear"] - 1)
        why.append(("bull", f"تشبع بيع (RSI {rsi:.0f}) يخفض الثقة بالهبوط"))
    info = {"S": S, "sma20": sma20, "sma50": sma50, "rsi": rsi, "ret5": ret5, "rv": rv, "vr": vr,
            "hi20": hi20, "lo20": lo20, "closes": c}
    return pts["bull"], pts["bear"], why, info


def news_points(heads):
    pos = neg = 0
    for h in heads:
        w = set(re.findall(r"[a-z\-]+", h.lower()))
        pos += len(w & set(POS_WORDS)) > 0
        neg += len(w & set(NEG_WORDS)) > 0
    net = pos - neg
    if net == 0:
        return 0.0, 0.0, None
    p = min(2.0, abs(net) * 0.5)
    if net > 0:
        return p, 0.0, ("bull", f"أخبار إيجابية ({pos} إيجابي مقابل {neg} سلبي)")
    return 0.0, p, ("bear", f"أخبار سلبية ({neg} سلبي مقابل {pos} إيجابي)")


def get_dates(t):
    out = {"earn": None, "exdiv": None}
    try:
        cal = t.calendar
        if isinstance(cal, dict):
            today = ob.now_ny().date()

            def nxt(v):
                if v is None:
                    return None
                vals = v if isinstance(v, (list, tuple)) else [v]
                ds = []
                for x in vals:
                    try:
                        d = x.date() if hasattr(x, "date") and not isinstance(x, date) else x
                        if isinstance(d, date) and d >= today:
                            ds.append(d)
                    except Exception:
                        pass
                return min(ds) if ds else None
            out["earn"] = nxt(cal.get("Earnings Date"))
            out["exdiv"] = nxt(cal.get("Ex-Dividend Date"))
    except Exception:
        pass
    return out


# ====================== الخيارات ======================
def get_chains(t, today):
    exps = []
    for e in (t.options or []):
        try:
            dte = (date.fromisoformat(e) - today).days
        except Exception:
            continue
        if MIN_DTE <= dte <= MAX_DTE:
            exps.append((e, dte))
    if not exps:
        for e in (t.options or []):
            dte = (date.fromisoformat(e) - today).days
            if dte >= MIN_DTE:
                exps = [(e, dte)]
                break
    exps = exps[:3]
    out = []
    for e, dte in exps:
        try:
            ch = t.option_chain(e)
            out.append((e, dte, ch.calls.copy(), ch.puts.copy()))
        except Exception:
            continue
    return out


def flow_points(chains):
    cv = pv = 0.0
    for _, _, c, p in chains:
        cv += float(pd.to_numeric(c.get("volume"), errors="coerce").fillna(0).sum())
        pv += float(pd.to_numeric(p.get("volume"), errors="coerce").fillna(0).sum())
    if cv + pv < 500:
        return 0.0, 0.0, None, cv, pv
    ratio = cv / max(pv, 1.0)
    if ratio >= 1.5:
        return 1.0, 0.0, ("bull", f"تدفق خيارات: حجم الكول {ratio:.1f}× حجم البوت"), cv, pv
    if ratio <= 1 / 1.5:
        return 0.0, 1.0, ("bear", f"تدفق خيارات: حجم البوت {1 / ratio:.1f}× حجم الكول"), cv, pv
    return 0.0, 0.0, None, cv, pv


def hist_stats(closes, tdays, need, side):
    """نسبة الفترات السابقة (بنفس عدد أيام التداول) التي تحرك فيها السهم بالاتجاه المطلوب بمقدار التعادل أو أكثر."""
    c = np.asarray(closes, dtype=float)
    k = max(1, int(tdays))
    if len(c) <= k + 10:
        return None
    r = c[k:] / c[:-k] - 1
    prob = float((r >= need).mean()) if side == "CALL" else float((r <= -need).mean())
    up, dn = r[r > 0], r[r < 0]
    return {"prob": prob, "up": float(up.mean()) if len(up) else 0.0,
            "dn": float(dn.mean()) if len(dn) else 0.0, "n": len(r)}


def pick_contracts(side, chains, S, rv, closes=None):
    out = []
    today = ob.now_ny().date()
    for exp, dte, calls, puts in chains:
        df = calls if side == "CALL" else puts
        cand = df[(df["strike"] > S) & (df["strike"] <= S * 1.10)] if side == "CALL" \
            else df[(df["strike"] < S) & (df["strike"] >= S * 0.90)]
        T = max(dte, 0.5) / 365
        for _, r in cand.iterrows():
            bid = 0 if pd.isna(r.get("bid")) else float(r["bid"])
            ask = 0 if pd.isna(r.get("ask")) else float(r["ask"])
            last = 0 if pd.isna(r.get("lastPrice")) else float(r["lastPrice"])
            if bid > 0 and ask > 0:
                price, spr = ask, (ask - bid) / ((ask + bid) / 2)
            else:
                price, spr = last, None
            cost = price * 100
            if not (CONTRACT_MIN_USD <= cost <= CONTRACT_MAX_USD):
                continue
            oi = 0 if pd.isna(r.get("openInterest")) else float(r["openInterest"])
            vol = 0 if pd.isna(r.get("volume")) else float(r["volume"])
            if oi < MIN_OI and vol < MIN_VOL:
                continue
            if spr is not None and spr > MAX_SPREAD:
                continue
            iv = r.get("impliedVolatility")
            if iv is None or pd.isna(iv) or iv < 0.05:
                iv = max(rv, 0.2)
            iv = float(iv)
            K = float(r["strike"])
            be = K + price if side == "CALL" else K - price
            delta = ob.abs_delta(S, K, T, iv, side)
            prob = ob.prob_beyond(S, be, T, iv, side)
            z = abs(be - S) / (S * iv * math.sqrt(T))
            if z > 1.8 or delta < 0.12:
                continue
            liq = 2 if oi >= 1000 else (1.5 if oi >= 300 else (1 if oi >= 150 else 0.5))
            liq += 0.5 if spr is None else (1 if spr <= 0.10 else (0.5 if spr <= 0.20 else 0))
            dp = 3 if delta >= 0.35 else (2 if delta >= 0.25 else (1 if delta >= 0.15 else 0.5))
            rp = 4 if z <= 0.5 else (3 if z <= 0.9 else (2 if z <= 1.3 else 1))
            fr = vol / max(oi, 1.0)
            flow = 0.0
            if vol >= MIN_VOL and fr >= 0.5:
                flow = 1.0 + (0.5 if fr >= 1.5 else 0.0)
            richness = iv / rv if rv > 0 else 1.0
            adj = -1.0 if richness >= 1.6 else (0.5 if richness <= 1.0 else 0.0)
            tdays = max(1, int(np.busday_count(today, date.fromisoformat(exp))))
            need = abs(be / S - 1)
            hs = hist_stats(closes, tdays, need, side) if closes is not None else None
            if hs is not None:
                adj += 0.5 if hs["prob"] >= 0.25 else (-1.0 if hs["prob"] < 0.08 else 0.0)
            score = max(0.0, min(10.0, liq + dp + rp + flow + adj))
            if score < MIN_CONTRACT_SCORE:
                continue
            out.append({"exp": exp, "dte": dte, "strike": K, "price": price, "cost": cost, "be": be,
                        "delta": delta, "prob": prob, "z": z, "oi": oi, "vol": vol, "iv": iv,
                        "score": score, "flow": flow, "rich": richness, "need": need, "hist": hs,
                        "em_iv": iv * math.sqrt(max(dte, 0.5) / 365), "em_rv": rv * math.sqrt(tdays / 252)})
    out.sort(key=lambda x: -(x["score"] + 0.15 * min(x["dte"], 5) + (0.5 if x["flow"] >= 1 else 0.0)))
    picked = []
    for x in out:   # نفضّل سترايكين مختلفين بدل تكرار نفس السترايك بتاريخين
        if all(abs(x["strike"] - p["strike"]) > 1e-9 for p in picked):
            picked.append(x)
        if len(picked) == 2:
            break
    for x in out:
        if len(picked) == 2:
            break
        if x not in picked:
            picked.append(x)
    return picked


def tier(score):
    return "🟩" if score >= 7.5 else ("🟨" if score >= 6.0 else "🟧")


# ====================== الحالة والسجل ======================
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(s):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False)
    except Exception as e:
        print("تعذر حفظ الحالة:", e)


LOG_COLS = ["time", "ticker", "side", "exp", "strike", "price", "strength", "result", "pnl"]


def log_idea(n, idea):
    k = idea["contracts"][0]
    row = {"time": n.isoformat(timespec="minutes"), "ticker": idea["ticker"], "side": idea["side"],
           "exp": k["exp"], "strike": k["strike"], "price": round(k["price"], 2),
           "strength": round(idea["strength"], 1), "result": "", "pnl": ""}
    try:
        df = pd.read_csv(LOG_FILE, dtype=str) if os.path.exists(LOG_FILE) else pd.DataFrame(columns=LOG_COLS)
        df = pd.concat([df, pd.DataFrame([row]).astype(str)], ignore_index=True)
        df.to_csv(LOG_FILE, index=False)
    except Exception as e:
        print("تعذر تسجيل الفرصة:", e)


def log_summary():
    if not os.path.exists(LOG_FILE):
        return "لا توجد فرص مسجلة بعد."
    try:
        df = pd.read_csv(LOG_FILE, dtype=str).fillna("")
    except Exception:
        return "تعذر قراءة السجل."
    if df.empty:
        return "لا توجد فرص مسجلة بعد."
    today = ob.now_ny().date()
    changed = False
    for i, r in df.iterrows():
        if r["result"] != "":
            continue
        try:
            exp = date.fromisoformat(r["exp"])
        except Exception:
            continue
        if exp >= today:
            continue
        try:
            h = yf.Ticker(r["ticker"]).history(start=exp.isoformat(), end=(exp + timedelta(days=4)).isoformat())["Close"].dropna()
            if h.empty:
                continue
            px, K, entry = float(h.iloc[0]), float(r["strike"]), float(r["price"])
            intr = max(0.0, px - K) if r["side"] == "CALL" else max(0.0, K - px)
            df.loc[i, "pnl"] = f"{(intr - entry) * 100:.0f}"
            df.loc[i, "result"] = "win" if intr > entry else "loss"
            changed = True
        except Exception:
            continue
    if changed:
        df.to_csv(LOG_FILE, index=False)
    done = df[df["result"] != ""]
    s = f"{len(df)} فرصة | انتهت {len(done)}"
    if len(done):
        wins = (done["result"] == "win").sum()
        net = pd.to_numeric(done["pnl"], errors="coerce").sum()
        s += f" | رابحة {wins / len(done) * 100:.0f}% | الصافي {net:+,.0f}$ (لو أبقيت العقد حتى الانتهاء)"
    return s


# ====================== التحليل الرئيسي ======================
def completed_volume(df, n):
    """آخر حجم تداول مكتمل: نتجنب شمعة اليوم الجزئية أثناء الجلسة."""
    v = df["Volume"].astype(float)
    last_day = df.index[-1].date() if hasattr(df.index[-1], "date") else None
    if last_day == n.date() and n.hour < 16 and len(v) > 1:
        return float(v.iloc[-2])
    return float(v.iloc[-1])


def intraday_price(t, fallback):
    try:
        h = t.history(period="1d", interval="5m")["Close"].dropna()
        if len(h):
            return float(h.iloc[-1])
    except Exception:
        pass
    return fallback


def analyze(force):
    n = ob.now_ny()
    today = n.date()
    tickers = list(WATCHLIST) + ["SPY"]
    raw = yf.download(tickers, period="6mo", group_by="ticker", auto_adjust=True, progress=False, threads=True)

    def bars(tk):
        try:
            d = raw[tk].dropna(subset=["Close"])
            return d if len(d) >= 55 else None
        except Exception:
            return None

    spy = bars("SPY")
    if spy is None:
        raise RuntimeError("تعذر قراءة بيانات السوق (SPY)")
    spyc = spy["Close"].astype(float)
    spy5 = float(spyc.iloc[-1] / spyc.iloc[-6] - 1)
    spy_up = float(spyc.iloc[-1]) > float(spyc.rolling(20).mean().iloc[-1])
    vix, vix_pct = ob.vix_info()
    try:
        vix_chg = ob._chg("^VIX", 1)
        spy_chg = ob._chg("SPY", 1)
    except Exception:
        vix_chg = spy_chg = None
    shock = bool((vix_chg is not None and vix_chg >= 0.15) or (spy_chg is not None and spy_chg <= -0.02))

    drv = {}
    for label, sym, key in (("البتكوين", "BTC-USD", "btc"), ("النفط", "CL=F", "oil")):
        try:
            drv[key] = (label, ob._chg(sym, 5) or 0.0)
        except Exception:
            drv[key] = None

    scored = []
    for tk, (name, dkey) in WATCHLIST.items():
        df = bars(tk)
        if df is None:
            continue
        try:
            bull, bear, why, info = tech_points(df, spy5, drv.get(dkey) if dkey in ("btc", "oil") else None,
                                                completed_volume(df, n))
        except Exception as e:
            print("تخطي", tk, e)
            continue
        if spy_up:
            bull += 0.5
        else:
            bear += 0.5
        scored.append({"ticker": tk, "name": name, "bull": bull, "bear": bear, "why": why, "info": info,
                       "dkey": dkey})
    scored.sort(key=lambda x: -abs(x["bull"] - x["bear"]))
    finalists = [s for s in scored if abs(s["bull"] - s["bear"]) >= 2.0][:FINALISTS]

    ideas, watch = [], []
    geo_heads = ob.fetch_rss("geopolitical tensions markets") if any(s["dkey"] == "geo" for s in finalists) else []
    for s in finalists:
        tk = s["ticker"]
        t = yf.Ticker(tk)
        heads = ob.fetch_rss(f"{s['name']} stock")
        if s["dkey"] == "geo":
            heads = heads + geo_heads
        nb, ne, nwhy = news_points(heads)
        s["bull"] += nb
        s["bear"] += ne
        if nwhy:
            s["why"].append(nwhy)
        chains = get_chains(t, today)
        fb, fe, fwhy, cv, pv = flow_points(chains)
        s["bull"] += fb
        s["bear"] += fe
        if fwhy:
            s["why"].append(fwhy)
        edge = s["bull"] - s["bear"]
        side = "CALL" if edge > 0 else "PUT"
        strength = min(10.0, abs(edge) * 1.6)
        dates = get_dates(t)
        s.update(heads=heads[:3], dates=dates, edge=edge, side=side, strength=strength, cv=cv, pv=pv)
        if abs(edge) < MIN_EDGE:
            watch.append((tk, f"الفرق {abs(edge):.1f} أقل من {MIN_EDGE}"))
            continue
        if shock and side == "CALL":
            watch.append((tk, "تحذير سوق: لا نقترح Call الآن"))
            continue
        S = intraday_price(t, s["info"]["S"])
        contracts = pick_contracts(side, chains, S, s["info"]["rv"], s["info"]["closes"])
        if contracts:   # القوة = نصف قوة الاتجاه + نصف درجة أفضل عقد (حتى لا تتشابه كل الفرص)
            s["strength"] = round(0.5 * min(10.0, abs(edge) * 1.1) + 0.5 * contracts[0]["score"], 1)
        if contracts and dates["earn"] is not None and any(
                dates["earn"] <= date.fromisoformat(k["exp"]) for k in contracts):
            s["strength"] = max(0.0, s["strength"] - 2)   # إعلان أرباح قبل الانتهاء يرفع عدم اليقين
        if not contracts:
            watch.append((tk, f"اتجاه {side} لكن لا عقد مناسب بسعر ${CONTRACT_MIN_USD}-${CONTRACT_MAX_USD}"))
            continue
        s.update(S=S, contracts=contracts)
        ideas.append(s)
    ideas.sort(key=lambda x: -x["strength"])
    return {"n": n, "today": today, "ideas": ideas[:MAX_IDEAS], "watch": watch, "vix": vix, "vix_pct": vix_pct,
            "spy5": spy5, "spy_up": spy_up, "shock": shock, "is_test": not in_window(n), "scanned": len(scored)}


# ====================== الرسائل ======================
AR_DAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
AR_MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر",
             "نوفمبر", "ديسمبر"]


def date_ar(iso):
    d = date.fromisoformat(iso)
    return f"{AR_DAYS[d.weekday()]} {d.day} {AR_MONTHS[d.month - 1]}"


def idea_line(i):
    """سطور مختصرة: أفضل عقد فقط (البديل في الرسالة الثانية)."""
    call = i["side"] == "CALL"
    k = i["contracts"][0]
    flags = ""
    ed = i["dates"]["earn"]
    if ed is not None and ed <= date.fromisoformat(k["exp"]):
        flags = " 🚨أرباح"
    mark = tier(k["score"]) + (" 🔥" if k["flow"] >= 1 else "")
    return "\n".join([
        f"{'🟢' if call else '🔴'} <b>{ob.esc(i['ticker'])}</b> — <b>{'CALL' if call else 'PUT'}</b> | ⭐ {i['strength']:.1f}{flags}",
        f"🎯 Strike <b>{k['strike']:,.1f}</b> | 📅 <b>{date_ar(k['exp'])}</b>",
        f"💵 <b>${k['cost']:,.0f}</b> | يحتاج {'+' if call else '-'}{k['need'] * 100:.1f}% | {mark}",
        f"✅ +${k['cost'] * TAKE_PROFIT:,.0f}  🛑 -${k['cost'] * STOP_LOSS:,.0f}",
    ])


def signal_message(a, ideas):
    head = "📌 <b>فرص الشركات</b>" + (" (تجريبي، السوق مغلق)" if a["is_test"] else "")
    L = [head, ""]
    for i in ideas:
        L += [idea_line(i), ""]
    L += ["🟩 قوي  🟨 متوسط  🟧 مقبول  🔥 طلب عالٍ  🚨 أرباح",
          "⚠️ <i>تعليمي وليست توصية. تحقق من السعر الحي، وأقصى خسارة هي سعر العقد.</i>"]
    return "\n".join(L)


def no_idea_message(a):
    L = ["⚪ <b>لا فرص قوية في الشركات الآن</b>", f"تم فحص {a['scanned']} شركة."]
    if a["shock"]:
        L.append("🔴 تحذير: السوق في حالة صدمة (VIX أو S&P).")
    if a["watch"]:
        L.append("")
        L.append("👀 قيد المراقبة:")
        for tk, why in a["watch"][:4]:
            L.append(f"• {ob.esc(tk)}: {ob.esc(why)}")
    return "\n".join(L)


def details_message(a, ideas, log_line):
    L = [f"📊 <b>تفاصيل فرص الشركات</b> | {a['today'].isoformat()}", ob.LINE]
    vix = f"{a['vix']:.1f}" if a["vix"] else "غير متاح"
    L.append(f"🌡️ VIX: <b>{vix}</b> | السوق (SPY) 5 أيام: {a['spy5'] * 100:+.1f}% "
             f"({'فوق' if a['spy_up'] else 'تحت'} متوسط 20 يوم)")
    if a["shock"]:
        L.append("🔴 حالة صدمة: لا نقترح Call.")
    for i in ideas:
        inf = i["info"]
        L += ["", ob.LINE, f"{'🟢' if i['side'] == 'CALL' else '🔴'} <b>{ob.esc(i['ticker'])}</b> ({ob.esc(i['name'])}) — "
              f"{i['side']} | السعر ≈ {i['S']:,.2f}",
              f"صعود <b>{i['bull']:.1f}</b> مقابل هبوط <b>{i['bear']:.1f}</b>",
              f"متوسط 20/50: {inf['sma20']:,.1f} / {inf['sma50']:,.1f} | RSI {inf['rsi']:.0f} | "
              f"تذبذب 20 يوم {inf['rv'] * 100:.0f}%",
              "<b>الأسباب:</b>"]
        for side, txt in i["why"]:
            L.append(f"{'🟢' if side == 'bull' else '🔴'} {ob.esc(txt)}")
        for k in i["contracts"]:
            rich = "مضخم (IV أعلى من التذبذب الفعلي)" if k["rich"] >= 1.6 else (
                "غير مضخم" if k["rich"] <= 1.0 else "عادي")
            L.append(f"🎯 {k['strike']:,.1f} ({date_ar(k['exp'])}) ≈ {k['price']:.2f} (${k['cost']:,.0f}) | تعادل {k['be']:,.2f} | "
                     f"نموذج ~{k['prob'] * 100:.0f}%"
                     + (f" | تاريخياً {k['hist']['prob'] * 100:.0f}%" if k["hist"] else "")
                     + f" | دلتا {k['delta']:.2f} | OI {k['oi']:,.0f} | حجم {k['vol']:,.0f} | سعر العقد: {rich}")
            mv = f"📏 الحركة المتوقعة حتى الانتهاء: ±{k['em_rv'] * 100:.1f}% (تذبذب فعلي) / ±{k['em_iv'] * 100:.1f}% (ضمني)"
            if k["hist"]:
                mv += (f" | في نفس المدة تاريخياً: متوسط الصعود +{k['hist']['up'] * 100:.1f}% "
                       f"ومتوسط الهبوط {k['hist']['dn'] * 100:.1f}%")
            L.append(mv)
        d = i["dates"]
        if d["earn"]:
            L.append(f"🚨 إعلان الأرباح: {d['earn'].isoformat()}"
                     + (" (قبل انتهاء العقد: تذبذب كبير واحتمال انهيار IV بعد الإعلان)"
                        if any(d['earn'] <= date.fromisoformat(k['exp']) for k in i['contracts']) else ""))
        if d["exdiv"]:
            L.append(f"💵 تاريخ توزيع الأرباح (Ex-Div): {d['exdiv'].isoformat()}")
        if i["heads"]:
            L.append("📰 " + " | ".join(ob.esc(h[:90]) for h in i["heads"][:2]))
    if a["watch"]:
        L += ["", "👀 <b>قيد المراقبة (لم تكتمل الشروط):</b>"]
        for tk, why in a["watch"][:5]:
            L.append(f"• {ob.esc(tk)}: {ob.esc(why)}")
    L += ["", "🛠️ <b>إدارة الصفقة (اقتراح)</b>",
          "• عقد واحد لكل فكرة، وأقصى خسارة = سعر العقد",
          f"• جني الربح +{TAKE_PROFIT * 100:.0f}% ووقف الخسارة -{STOP_LOSS * 100:.0f}%",
          "• لا تدخل قبل إعلان أرباح إلا إذا كنت تقبل الخسارة الكاملة",
          "", f"📒 <b>السجل</b>: {ob.esc(log_line)}", "",
          "⚠️ <i>الأخبار والتدفق مقاييس تقريبية من بيانات مجانية متأخرة. أسهم الشركات قد تتحرك بخبر مفاجئ ضد توقعك، "
          "وأغلب العقود الرخيصة تنتهي بلا قيمة. جرّب ورقياً أولاً.</i>"]
    return "\n".join(L)


# ====================== التشغيل ======================
def run_once(force=False):
    n = ob.now_ny()
    if not force and not in_window(n):
        print("خارج وقت التداول، لا شيء للتنفيذ.")
        return
    try:
        a = analyze(force)
    except Exception as e:
        if force:
            ob.send_telegram(f"⚠️ خطأ في بوت الشركات: {ob.esc(e)}")
        else:
            print("خطأ:", e)
        return
    state = load_state()
    today_s = a["today"].isoformat()
    if state.get("date") != today_s:
        state = {"date": today_s, "sent": {}, "report_sent": False}
    log_line = log_summary()

    if force:
        if a["ideas"]:
            ob.send_telegram(signal_message(a, a["ideas"]))
            ob.send_telegram(details_message(a, a["ideas"], log_line))
        else:
            ob.send_telegram(no_idea_message(a))
            ob.send_telegram(details_message(a, [], log_line))
        return

    new = [i for i in a["ideas"] if f"{i['ticker']}:{i['side']}" not in state["sent"]]
    if new:
        ob.send_telegram(signal_message(a, new))
        ob.send_telegram(details_message(a, new, log_line))
        for i in new:
            state["sent"][f"{i['ticker']}:{i['side']}"] = n.isoformat()
            log_idea(n, i)
        state["report_sent"] = True
    elif not state.get("report_sent"):
        ob.send_telegram(no_idea_message(a) + "\n\n🔔 سأواصل الفحص وأرسل لك عند ظهور فرصة.")
        state["report_sent"] = True
    else:
        print("لا فرص جديدة، لا رسالة.")
    save_state(state)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="تشغيل فوري حتى خارج وقت التداول")
    args = parser.parse_args()
    run_once(force=args.force or os.getenv("FORCE") == "1")
