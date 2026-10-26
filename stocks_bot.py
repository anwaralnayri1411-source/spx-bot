"""بوت فرص خيارات الشركات (يومي/أسبوعي): شراء Call أو Put -> تليجرام (لا ينفذ أي أوامر)

يعتمد على ملف options_bot.py الموجود في نفس المستودع (دوال التليجرام والرياضيات والأخبار).
الرسالة الأولى: أقوى الفرص (الشركة، الاتجاه، العقد، تاريخ الانتهاء، سعر التنفيذ).
الرسالة الثانية: أسباب كل فرصة، الأخبار، الأرباح، حالة السوق، سجل النتائج.
"""

import argparse
import copy
import json
import math
import os
import re
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

import data_gaps   # تسجيل فجوات البيانات فقط (لا يغيّر أي قرار)
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
    "INTC": ("Intel", None), "SOFI": ("SoFi", None), "F": ("Ford", None), "PFE": ("Pfizer", None),
    "SNAP": ("Snap", None), "MU": ("Micron", None), "RIVN": ("Rivian", None), "T": ("AT&T", None),
    "BA": ("Boeing", None), "XOM": ("Exxon Mobil", "oil"), "CVX": ("Chevron", "oil"),
    "COIN": ("Coinbase", "btc"), "MSTR": ("MicroStrategy", "btc"), "HOOD": ("Robinhood", "btc"),
    "LMT": ("Lockheed Martin", "geo"),
}
CONTRACT_MIN_USD = 80
CONTRACT_MAX_USD = 350   # كان 200 حتى 25/10/2026؛ رُفع بطلب المستخدم (الحد الأدنى 80 باقٍ)
MIN_OI = 500                # أقل مراكز مفتوحة (أو حجم تداول MIN_VOL)
MIN_VOL = 150
MAX_SPREAD = 0.30
MIN_EDGE = 3.5              # أقل فرق بين نقاط الصعود والهبوط لإعطاء فرصة
MIN_CONTRACT_SCORE = 5.0
MAX_IDEAS = 3
MAX_SAME_SIDE = 2           # لا أكثر من فرصتين في نفس الاتجاه (الأسهم مترابطة)
FINALISTS = 12              # عدد الشركات التي تُفحص بعمق (أخبار + سلسلة الخيارات)
MIN_TDAYS = 3               # أقل مدة للعقد بأيام التداول: العقود الأقصر تأكلها سرعة التآكل قبل أن يتحرك السهم
MAX_DTE = 14                # أبعد انتهاء (أيام تقويمية)
MAX_Z = 1.0                 # أقصى بُعد للتعادل عن السعر بوحدات الحركة المتوقعة (كان 1.8)
MIN_HIST_PROB = 0.10        # أقل احتمال تاريخي لبلوغ التعادل خلال مدة العقد
TAKE_PROFIT = 0.50
STOP_LOSS = 0.40
WINDOW_START = (10, 0)      # بتوقيت نيويورك
WINDOW_END = (15, 30)
STATE_FILE = "stocks_state.json"
LOG_FILE = "stocks_log.csv"
TRACK_FILE = "stocks_track.csv"   # تتبع كل عقد بعد التوصية (وبعد الوقف أيضاً)

# ---- متابعة العقود المفتوحة (تنبيهات بعد التوصية) ----
POS_FILE = "stocks_positions.json"
MINE_FILE = "stocks_mine.json"   # اختياراتك من الأزرار (مراقبة/اشتريت)؛ يكتبه بوت الأوامر وحده ويقرؤه هذا البوت
MONITOR_START = (9, 45)     # بتوقيت نيويورك
MONITOR_END = (16, 0)
WARN_LOSS = 0.25            # تحذير مبكر عند نزول العقد 25%
GIVEBACK = 0.50             # بعد بلوغ الهدف: تنبيه إذا تراجع الربح إلى نصف أعلى ربح
CHANCE_MIN = 0.05           # احتمال الوصول للتعادل أقل من 5% = الفرصة ضعيفة جداً
MIN_AGE_MIN = 30            # لا تنبيهات خسارة في أول 30 دقيقة (فرق السعر وحده قد يخدع)
ADVERSE_SHORT = 0.010       # حركة السهم ضدك (عقود تنتهي خلال يومين)
ADVERSE_LONG = 0.015        # حركة السهم ضدك (عقود أطول)
MARKET_ADVERSE = 0.010      # حركة SPY ضدك منذ التوصية
SUMMARY_AT = (15, 20)       # ملخص العقود المفتوحة آخر الجلسة

POS_WORDS = ["upgrade", "upgrades", "beat", "beats", "surge", "surges", "soar", "soars", "rally", "rallies",
             "record", "launch", "launches", "unveil", "unveils", "partnership", "approval", "approved",
             "raises", "buyback", "bullish", "outperform", "jump", "jumps", "wins", "boost"]
NEG_WORDS = ["downgrade", "downgrades", "miss", "misses", "plunge", "plunges", "slump", "falls", "drops",
             "probe", "lawsuit", "sues", "recall", "cuts", "layoffs", "investigation", "ban", "tariff",
             "bearish", "underperform", "warning", "halt", "delay", "delays", "fraud", "crash"]


def in_window(n):
    end = session_end_stocks(n)
    return ob.market_day(n) and WINDOW_START <= (n.hour, n.minute) <= end


def session_end_stocks(n):
    """آخر وقت لفحص الشركات: 30 دقيقة قبل الإغلاق (يتكيف مع الإغلاق المبكر)."""
    e = ob.session_end(n)
    m = e[0] * 60 + e[1] - 30
    return min(WINDOW_END, (m // 60, m % 60))


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
    ext = S / sma20 - 1
    if ext >= 0.08 and pts["bull"] > 0:      # مطاردة سهم ممتد: احتمال ارتداد يضرب العقد القصير
        pts["bull"] = max(0.0, pts["bull"] - 1)
        why.append(("bear", f"السعر ممتد {ext * 100:.0f}% فوق متوسط 20 يوماً، خطر مطاردة القمة"))
    if ext <= -0.08 and pts["bear"] > 0:
        pts["bear"] = max(0.0, pts["bear"] - 1)
        why.append(("bull", f"السعر ممتد {abs(ext) * 100:.0f}% تحت متوسط 20 يوماً، خطر مطاردة القاع"))
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
        if dte <= MAX_DTE and int(np.busday_count(today, date.fromisoformat(e))) >= MIN_TDAYS:
            exps.append((e, dte))
    if not exps:
        for e in (t.options or []):
            dte = (date.fromisoformat(e) - today).days
            if int(np.busday_count(today, date.fromisoformat(e))) >= MIN_TDAYS:
                exps = [(e, dte)]
                break
    exps = exps[:3]
    out = []
    for e, dte in exps:
        try:
            ch = t.option_chain(e)
            out.append((e, dte, ch.calls.copy(), ch.puts.copy()))
        except Exception as ex:
            data_gaps.note("stocks", "option_chain", ex, e)
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


def _blocked_idea(tag, i):
    """فكرة محجوبة بسبب سقف أو مركز مفتوح: ما يحتاجه ظل الحجب (blocked_shadow) فقط."""
    return {"tag": tag, "ticker": i["ticker"], "side": i["side"], "edge": i["edge"], "strength": i["strength"],
            "S": i.get("S"), "contracts": i["contracts"]}


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
            if z > MAX_Z or delta < 0.12:
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
            if hs is not None and hs["prob"] < MIN_HIST_PROB:
                continue
            if hs is not None:
                adj += 0.5 if hs["prob"] >= 0.25 else (-1.0 if hs["prob"] < 0.08 else 0.0)
            score = max(0.0, min(10.0, liq + dp + rp + flow + adj))
            if score < MIN_CONTRACT_SCORE:
                continue
            out.append({"exp": exp, "dte": dte, "strike": K, "price": price, "cost": cost, "be": be,
                        "delta": delta, "prob": prob, "z": z, "oi": oi, "vol": vol, "iv": iv,
                        "score": score, "flow": flow, "rich": richness, "need": need, "hist": hs, "spr": spr,
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


LOG_COLS = ["time", "ticker", "side", "exp", "strike", "price", "strength", "result", "pnl", "rule", "rule_pnl", "peak"]


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
    if "rule" in df.columns:
        rd = df[df["rule"].isin(["tp", "stop", "none"])]
        if len(rd):
            tps, sts = (rd["rule"] == "tp").sum(), (rd["rule"] == "stop").sum()
            net_r = pd.to_numeric(rd["rule_pnl"], errors="coerce").sum()
            s += f"\n🎯 بقاعدة الهدف والوقف: {tps} هدف | {sts} وقف | {len(rd) - tps - sts} بلا حسم | صافي ≈ {net_r:+,.0f}$ (تقريبي)\n"
    if len(done):
        wins = (done["result"] == "win").sum()
        net = pd.to_numeric(done["pnl"], errors="coerce").sum()
        s += f" | رابحة {wins / len(done) * 100:.0f}% | الصافي {net:+,.0f}$ (لو أبقيت العقد حتى الانتهاء)"
    return s


# ====================== متابعة العقود المفتوحة ======================
def monitor_window(n):
    return n.weekday() < 5 and MONITOR_START <= (n.hour, n.minute) <= MONITOR_END


def load_positions():
    """يرجع None إذا لم يُنشأ الملف بعد (فنبنيه من السجل)."""
    try:
        with open(POS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("positions", [])
        d.setdefault("summary_date", "")
        for p in d["positions"]:      # مراكز قديمة: ما بلغ الهدف أو الوقف يُعتبر مقفلاً بنتيجة القاعدة
            al = p.get("alerts", {})
            if p.get("closed"):
                continue
            if p.get("status") == "tp":
                p["closed"] = {"kind": "tp", "time": al.get("tp") or p.get("time"), "pnl": TAKE_PROFIT}
                d["dirty"] = True
            elif p.get("status") == "stopped":
                p["closed"] = {"kind": "stop", "time": al.get("stop") or p.get("time"), "pnl": -STOP_LOSS}
                d["dirty"] = True
        return d
    except Exception:
        return None


def save_positions(d):
    d = {k: v for k, v in d.items() if k != "dirty"}
    try:
        with open(POS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print("تعذر حفظ المراكز:", e)


def is_closed(p):
    """العقد مقفل إذا بلغ الهدف أو الوقف (نتيجته محفوظة ولا يتأرجح بعدها)."""
    return bool(p.get("closed")) or p.get("status") == "stopped"


def closed_info(p):
    """(النوع 'tp'|'stop'، وقت الإقفال، الربح المقفل كنسبة من سعر الدخول) أو None."""
    c = p.get("closed")
    if c:
        return c["kind"], c.get("time"), c["pnl"]
    if p.get("status") == "stopped":
        return "stop", p.get("alerts", {}).get("stop") or p.get("time"), -STOP_LOSS
    return None


def _on_date(iso, d):
    try:
        return datetime.fromisoformat(iso).date() == d
    except Exception:
        return False


def closed_on(p, d):
    ci = closed_info(p)
    return bool(ci and ci[1] and _on_date(ci[1], d))


def blocked_map(P, n):
    """{(سهم، اتجاه): السبب} للتوصيات الجديدة الممنوعة: عقد مفتوح بنفس الاتجاه، أو أُغلق عقد السهم اليوم."""
    today, out = n.date(), {}
    for p in active_positions(P, n):
        key = (p["ticker"], p["side"])
        if not is_closed(p):
            out[key] = "لديك عقد مفتوح بنفس الاتجاه، لا نكرر التوصية"
        elif closed_on(p, today):
            out.setdefault(key, "أُغلق عقد هذا السهم اليوم، توصية جديدة من الغد")
    return out


def pos_id(tk, side, exp, strike):
    return f"{tk}:{side}:{exp}:{float(strike):g}"


def mine_key(base, day):
    """مفتاح اختيارك: معرّف العقد الأساسي + يوم التوصية (نيويورك). ثابت حتى لو أُعيدت توصية نفس العقد لاحقاً."""
    return f"{base}|{day}"


def load_mine():
    """{مفتاح: {mode: 'watch'|'bought'، price، qty، ...}}. فشل القراءة = لا اختيارات."""
    try:
        with open(MINE_FILE, encoding="utf-8") as f:
            return json.load(f).get("items", {})
    except Exception:
        return {}


def save_mine(items):
    with open(MINE_FILE, "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, ensure_ascii=False, indent=1)


def mine_for(p, items):
    """اختيارك لهذا المركز أو None."""
    try:
        return items.get(mine_key(p["id"].split("@")[0], p["time"][:10]))
    except Exception:
        return None


def make_pos(tk, side, exp, strike, entry, t_iso, entry_spot=None, entry_spy=None, strength=None, earn=None):
    return {"id": pos_id(tk, side, exp, strike), "ticker": tk, "side": side, "exp": exp,
            "strike": float(strike), "entry": round(float(entry), 2), "time": t_iso,
            "entry_spot": entry_spot, "entry_spy": entry_spy, "strength": strength, "earn": earn,
            "status": "open", "peak": 0.0, "alerts": {}}


def spot_at(tk, t_iso):
    """سعر السهم وقت التوصية (للعقود المسجلة قبل بدء المتابعة). فاشل = None."""
    try:
        t0 = datetime.fromisoformat(t_iso)
        h = yf.Ticker(tk).history(period="7d", interval="5m")["Close"].dropna()
        h = h[h.index >= t0]
        if len(h):
            return float(h.iloc[0])
    except Exception:
        pass
    return None


def bootstrap_positions(n):
    """أول تشغيل: نبني قائمة العقود المفتوحة من سجل التوصيات (العقود التي لم تنتهِ بعد)."""
    P = {"positions": [], "summary_date": "", "dirty": True}
    try:
        df = pd.read_csv(LOG_FILE, dtype=str).fillna("") if os.path.exists(LOG_FILE) else None
    except Exception:
        df = None
    if df is None:
        return P
    for _, r in df.iterrows():
        try:
            if r["result"] != "" or date.fromisoformat(r["exp"]) < n.date():
                continue
            p = make_pos(r["ticker"], r["side"], r["exp"], float(r["strike"]), float(r["price"]), r["time"],
                         spot_at(r["ticker"], r["time"]), None, float(r["strength"] or 0))
            if all(x["id"] != p["id"] for x in P["positions"]):
                P["positions"].append(p)
        except Exception as e:
            print("تخطي سطر من السجل:", e)
    return P


def active_positions(P, n):
    return [p for p in P["positions"] if date.fromisoformat(p["exp"]) >= n.date()]


def record_outcomes(done):
    """يسجل في السجل نتيجة قاعدة الهدف والوقف لكل عقد انتهت متابعته:
    tp = لمس +50% قبل الوقف، stop = بلغ الوقف، none = لم يبلغ أياً منهما.
    القياس كل نصف ساعة فقط، فهو تقريب وليس سعر التنفيذ الفعلي."""
    if not done or not os.path.exists(LOG_FILE):
        return
    try:
        df = pd.read_csv(LOG_FILE, dtype=str).fillna("")
        for c in ("rule", "rule_pnl", "peak"):
            if c not in df.columns:
                df[c] = ""
        for p in done:
            m = (df["time"] == p["time"]) & (df["ticker"] == p["ticker"]) & (df["strike"].astype(float) == float(p["strike"]))
            if not m.any():
                continue
            peak = float(p.get("peak", 0.0))
            ci = closed_info(p)
            if ci:
                rule, rp = ci[0], ci[2]
            else:
                rule, rp = "none", None
            df.loc[m, "rule"] = rule
            df.loc[m, "rule_pnl"] = "" if rp is None else f"{rp * p['entry'] * 100:.0f}"
            df.loc[m, "peak"] = f"{peak * 100:.0f}"
        df.to_csv(LOG_FILE, index=False)
    except Exception as e:
        print("تعذر تسجيل نتيجة القاعدة:", e)


def prune_positions(P, n):
    keep = active_positions(P, n)
    if len(keep) != len(P["positions"]):
        record_outcomes([p for p in P["positions"] if p not in keep])
        P["positions"] = keep
        P["dirty"] = True


def _entry_ctx(idea, k, n):
    """سياق لحظة التوصية للسجل فقط (لتفسير الخسارة لاحقاً: أخبار، أرباح، توزيعات، مدة، دلتا...). لا يغيّر أي قرار ولا يرفع استثناء."""
    try:
        exp = date.fromisoformat(k["exp"])
        today = n.date()
        d = idea.get("dates") or {}
        earn, exd = d.get("earn"), d.get("exdiv")
        c = {"news_b": idea.get("news_b"), "news_e": idea.get("news_e"), "flow_b": idea.get("flow_b"), "flow_e": idea.get("flow_e"),
             "bull": round(idea["bull"], 1) if idea.get("bull") is not None else None, "bear": round(idea["bear"], 1) if idea.get("bear") is not None else None,
             "dte": k.get("dte"), "delta": round(float(k["delta"]), 2) if k.get("delta") is not None else None,
             "z": round(float(k["z"]), 2) if k.get("z") is not None else None, "score": k.get("score"),
             "earn_in_life": bool(earn and today <= earn <= exp), "exdiv": exd.isoformat() if exd else None,
             "exdiv_in_life": bool(exd and today <= exd <= exp)}
        return {key: v for key, v in c.items() if v is not None}
    except Exception:  # noqa: BLE001
        return {}


def add_position(P, n, idea, spy_px):
    k = idea["contracts"][0]
    earn = idea["dates"]["earn"]
    p = make_pos(idea["ticker"], idea["side"], k["exp"], k["strike"], k["price"], n.isoformat(timespec="minutes"),
                 round(idea["S"], 2), round(spy_px, 2) if spy_px else None, round(idea["strength"], 1),
                 earn.isoformat() if earn else None)
    same = [x for x in P["positions"] if x["id"] == p["id"] or x["id"].startswith(p["id"] + "@")]
    if any(not is_closed(x) for x in same):
        return                                   # لا نضاعف عقداً ما زال مفتوحاً
    if same:
        p["id"] = f"{p['id']}@{n.date().isoformat()}"   # نفس العقد بعد إقفاله: صفقة جديدة
    if k.get("price_src"):
        p.update(entry_src=k["price_src"], entry_yahoo=round(float(k.get("yahoo_price", k["price"])), 2))
    if k.get("spr") is not None:
        p["entry_spr"] = round(float(k["spr"]), 3)    # فرق الشراء/البيع عند الدخول: لدراسة أثره على النتيجة (لا يغيّر قراراً)
    ctx = _entry_ctx(idea, k, n)
    if ctx:
        p["ctx"] = ctx          # سجل فقط: لا يدخل في أي قرار
    _live_entry_stock(p)
    P["positions"].append(p)
    P["dirty"] = True


def _live_prices_stock(ideas):
    """الوضع 1: سعر الدخول من ask اللحظي (Alpaca ثم Tastytrade)؛ اختيار العقد ما زال من سلسلة ياهو.
    يحدّث price/cost/be/need/prob ويضع price_src = alpaca | tasty | yahoo_fallback ويحفظ yahoo_price. خارج الوضع 1 لا يغيّر شيئاً."""
    try:
        import live_quotes as lq
        if lq.mode("STOCKS") != "1":
            return
        for i in ideas:
            for k in i["contracts"]:
                z, src = lq.best_quote(lq.occ(i["ticker"], k["exp"], i["side"], k["strike"]))
                if not z:
                    k["price_src"] = "yahoo_fallback"
                    continue
                ask, K, S = z["ask"], k["strike"], i["S"]
                be = K + ask if i["side"] == "CALL" else K - ask
                T = max(k["dte"], 0.5) / 365
                k.update(yahoo_price=k["price"], price=ask, cost=ask * 100, be=be, need=abs(be / S - 1),
                         prob=ob.prob_beyond(S, be, T, k["iv"], i["side"]), price_src=src, bid=z["bid"],
                         spr=(ask - z["bid"]) / ((ask + z["bid"]) / 2) if z.get("bid") and z["bid"] > 0 else k.get("spr"))
    except Exception as e:
        print("تعذر تحديث أسعار الدخول اللحظية:", e)


# ---- البيانات اللحظية لبوت الشركات: الوضع 1 يعتمدها (contract_quote)، والظل يسجّل المقارنة. المفتاح LIVE_QUOTES_STOCKS ----
def _live_entry_stock(p):
    """يحفظ سعر الطلب اللحظي لحظة التوصية في entry_live (للمقارنة فقط؛ الحسابات ما زالت على entry)."""
    try:
        import live_quotes as lq
        if lq.mode("STOCKS") == "0":
            return
        q, _ = lq.best_quote(lq.occ(p["ticker"], p["exp"], p["side"], p["strike"]))
        if q:
            p["entry_live"] = round(q["ask"], 2)
    except Exception as e:
        print("تعذر جلب سعر الدخول اللحظي:", e)


def _live_shadow_stock(p, q):
    """يسجّل سعر Alpaca بجانب سعر ياهو لكل متابعة في ملف خاص ببوت الشركات. لا يرجع شيئاً ولا يغيّر p."""
    try:
        import live_quotes as lq
        if lq.mode("STOCKS") == "0":
            return
        c = lq.occ(p["ticker"], p["exp"], p["side"], p["strike"])
        z = lq.quote(c)
        t = lq.tasty_quote(c)           # مصدر ثانٍ مستقل: مقارنة دائمة مع Alpaca (تسجيل فقط، لا يغيّر شيئاً)
        yv, el = (q.get("yahoo_val") if q else None), p.get("entry_live")
        lq.log({"time": ob.now_ny().isoformat(timespec="minutes"), "project": "STOCKS", "id": p["id"], "contract": c,
                "yahoo_val": yv if yv is not None else "", "alpaca_bid": z["bid"] or "", "alpaca_ask": z["ask"] or "",
                "age_s": "" if z["age"] is None else round(z["age"]), "status": z["status"],
                "entry": p["entry"], "entry_live": el or "",
                "pnl_yahoo": round(yv / p["entry"] - 1, 3) if yv else "",
                "pnl_live": round(z["bid"] / el - 1, 3) if (el and z["bid"] and z["status"] == "ok") else "",
                "source": q["src"] if q else "", "tasty_bid": t["bid"] or "", "tasty_ask": t["ask"] or "",
                "tasty_age_s": "" if t["age"] is None else round(t["age"]), "tasty_status": t["status"]},
               path=lq.LOG_STOCKS)
    except Exception as e:
        print("تعذر تسجيل السعر اللحظي:", e)


def _live_on():
    try:
        import live_quotes as lq
        return lq.mode("STOCKS") == "1"
    except Exception:
        return False


def _live_px(t):
    """الوضع 1: سعر السهم اللحظي (Tastytrade ثم Alpaca IEX) أو None فيرجع المستدعي إلى ياهو."""
    if not _live_on():
        return None
    import live_quotes as lq
    return lq.spot(t.ticker)[0]


def _live_chains(chains, side, S):
    """الوضع 1: سلاسل ياهو بعرض/طلب لحظي لجهة العقد فقط وضمن ±12% من السعر؛ وإلا كما هي."""
    if not _live_on():
        return chains
    import live_quotes as lq
    return [(e, dte, lq.overlay(c, S, 0.12) if side == "CALL" else c, lq.overlay(p, S, 0.12) if side == "PUT" else p)
            for e, dte, c, p in chains]


def spot_now(t):
    px = _live_px(t)
    if px:
        return px
    try:
        h = t.history(period="1d", interval="5m")["Close"].dropna()
        if len(h):
            return float(h.iloc[-1])
    except Exception as ex:
        data_gaps.note("stocks", "spot_5m", ex, getattr(t, "ticker", ""))
    try:
        return float(t.history(period="5d")["Close"].dropna().iloc[-1])
    except Exception as ex:
        data_gaps.note("stocks", "spot_5d", ex, getattr(t, "ticker", ""))
        return None


def contract_quote(t, p, cache):
    """سعر العقد: {'val','spr','iv','src','yahoo_val'}. في الوضع 1 (LIVE_QUOTES_STOCKS=1) bid اللحظي من Alpaca ثم Tastytrade
    (≤120ث) وياهو احتياطاً؛ غير ذلك ياهو كما هو. src: alpaca | tasty | yahoo_fallback | yahoo."""
    yq = _yahoo_quote(t, p, cache)
    try:
        import live_quotes as lq
        if lq.mode("STOCKS") != "1":
            return yq and dict(yq, src="yahoo", yahoo_val=yq["val"])
        z, src = lq.best_quote(lq.occ(p["ticker"], p["exp"], p["side"], p["strike"]))
    except Exception as e:
        print("تعذر جلب السعر اللحظي:", e)
        z = None
    if not z:
        return yq and dict(yq, src="yahoo_fallback", yahoo_val=yq["val"])
    bid, ask = z["bid"], z["ask"]
    return {"val": bid if ob.EXIT_AT_BID else (bid + ask) / 2, "spr": (ask - bid) / ((ask + bid) / 2),
            "iv": yq["iv"] if yq else None, "src": src, "yahoo_val": yq["val"] if yq else None}


def _yahoo_quote(t, p, cache):
    key = (p["ticker"], p["exp"])
    try:
        if key not in cache:
            cache[key] = t.option_chain(p["exp"])
        ch = cache[key]
    except Exception as e:
        print("تعذر جلب سلسلة الخيارات", key, e)
        return None
    df = ch.calls if p["side"] == "CALL" else ch.puts
    r = df[(df["strike"] - p["strike"]).abs() < 1e-6]
    if r.empty:
        return None
    r = r.iloc[0]

    def num(k):
        v = r.get(k)
        return 0.0 if v is None or pd.isna(v) else float(v)
    bid, ask, last = num("bid"), num("ask"), num("lastPrice")
    if bid > 0 and ask > 0:
        val, spr = (bid if ob.EXIT_AT_BID else (bid + ask) / 2), (ask - bid) / ((ask + bid) / 2)
    elif last > 0:
        val, spr = last, None
    else:
        return None
    iv = num("impliedVolatility")
    return {"val": val, "spr": spr, "iv": iv if iv >= 0.05 else None}


SEVERITY = ["stop", "exp_final", "broken", "market", "chance", "giveback", "tp", "warn", "exp_today", "overnight"]
ALERT_ICON = {"stop": "🛑", "exp_final": "⏰", "broken": "⚠️", "market": "🌐", "chance": "📉",
              "giveback": "🔔", "tp": "✅", "warn": "🟠", "exp_today": "⏳", "overnight": "⏳"}


DROP_KINDS = {"warn", "broken", "market", "chance"}   # تنبيهات الهبوط والضعف


def evaluate(p, n, q, spot, spy_spot):
    """يقيّم عقداً مفتوحاً. يرجع (info, alerts) حيث alerts تنبيهات جديدة لم تُرسل من قبل.
    يعدّل p (الحالة وسجل التنبيهات) فيجب تمرير نسخة عند التجربة."""
    exp = date.fromisoformat(p["exp"])
    dte = (exp - n.date()).days
    call = p["side"] == "CALL"
    d = 1 if call else -1
    entry, val = p["entry"], q["val"]
    pnl = val / entry - 1
    secs = max((datetime(exp.year, exp.month, exp.day, 16, 0, tzinfo=n.tzinfo) - n).total_seconds(), 1800)
    iv = min(2.0, max(0.1, q["iv"] or 0.4))
    be = p["strike"] + entry if call else p["strike"] - entry
    prob = need = None
    if spot:
        prob = ob.prob_beyond(spot, be, secs / (365 * 86400), iv, p["side"])
        need = d * (be / spot - 1)        # موجب: ما زال يحتاج هذه الحركة للتعادل
    mv = (spot / p["entry_spot"] - 1) * d if (spot and p.get("entry_spot")) else None   # موجب = لصالحك
    try:
        age = (n - datetime.fromisoformat(p["time"])).total_seconds() / 60
    except Exception:
        age = 9999
    info = {"val": val, "pnl": pnl, "spot": spot, "mv": mv, "need": need, "prob": prob, "spr": q["spr"],
            "dte": dte}
    new, al, hm = [], p["alerts"], (n.hour, n.minute)
    changed = False
    prior_drop = any(k in al for k in DROP_KINDS)     # سبق تنبيه هبوط لهذا العقد

    def fire(kind, text):
        nonlocal changed
        if kind not in al:
            al[kind] = n.isoformat(timespec="minutes")
            new.append((kind, text))
            changed = True

    live = not is_closed(p)
    pk_key = "peak" if live else "peak_after"      # بعد الإقفال لا نلوّث قمة القاعدة
    if pnl > p.get(pk_key, 0.0) + 1e-9:
        p[pk_key] = round(pnl, 3)
        if pk_key == "peak_after":
            p["peak_after_at"] = n.isoformat(timespec="minutes")      # للدراسة: متى بلغ العقد قمته بعد الإقفال (تسجيل فقط)
        changed = True
    mature = age >= MIN_AGE_MIN
    # دراسة صامتة: آخر ربح/خسارة مرصود (للمقارنة بين «بالقاعدة» و«لو تركته حتى الانتهاء»)
    if p.get("last_pnl") != round(pnl, 3):
        p["last_pnl"] = round(pnl, 3)
        p["last_at"] = n.isoformat(timespec="minutes")
        changed = True

    if live and mature:
        if pnl <= -STOP_LOSS:
            fire("stop", f"وصل وقف الخسارة (-{STOP_LOSS * 100:.0f}%). الخروج يحدّ الخسارة. لا تعزّز عقداً خاسراً.")
            p["status"] = "stopped"
            p["closed"] = {"kind": "stop", "time": n.isoformat(timespec="minutes"), "pnl": -STOP_LOSS}
            changed = True
        elif pnl <= -WARN_LOSS:
            fire("warn", f"العقد نزل {abs(pnl) * 100:.0f}%. وقف الخسارة عند -{STOP_LOSS * 100:.0f}%.")
    if live and pnl >= TAKE_PROFIT:
        lock = TAKE_PROFIT * entry * 100
        fire("tp", f"وصل هدف الربح (+{TAKE_PROFIT * 100:.0f}%). الصفقة مقفلة بالقاعدة: ربح ≈ +${lock:,.0f} للعقد. "
                   "أنهِ صفقتك، وسأواصل رصد العقد بصمت للدراسة.")
        p["status"] = "tp"
        p["closed"] = {"kind": "tp", "time": n.isoformat(timespec="minutes"), "pnl": TAKE_PROFIT}
        changed = True
    if live and mature and mv is not None:
        thr = ADVERSE_SHORT if dte <= 2 else ADVERSE_LONG
        if mv <= -thr:
            fire("broken", f"الفكرة تضعف: السهم تحرك {abs(mv) * 100:.1f}% ضد اتجاهك منذ التوصية.")
    if live and mature and p.get("entry_spy") and spy_spot:
        sm = (spy_spot / p["entry_spy"] - 1) * d
        if sm <= -MARKET_ADVERSE:
            fire("market", f"السوق (SPY) تحرك {abs(sm) * 100:.1f}% ضدك منذ التوصية.")
    if live and mature and prob is not None and prob < CHANCE_MIN and pnl < 0:
        fire("chance", f"الفرصة ضعيفة جداً: احتمال وصول السهم للتعادل في الوقت المتبقي ≈ {prob * 100:.0f}% فقط.")
    if dte == 0 and live and hm >= (14, 30):
        fire("exp_today", f"ينتهي اليوم والتآكل الزمني يتسارع. قيمته الآن ≈ ${val * 100:,.0f}.")
    if dte == 0 and live and spot and hm >= (15, 30):
        itm = spot > p["strike"] if call else spot < p["strike"]
        if itm:
            fire("exp_final", "آخر 30 دقيقة: العقد داخل المال. إن تركته حتى الانتهاء قد يُنفَّذ تلقائياً ويتحول إلى "
                              f"100 سهم (≈ ${spot * 100:,.0f}). أغلقه قبل الإغلاق ما لم تقصد ذلك.")
        else:
            fire("exp_final", f"آخر 30 دقيقة: العقد خارج المال وسينتهي بلا قيمة إن لم يتحرك السعر. "
                              f"إن أردت استرداد ما بقي (≈ ${val * 100:,.0f}) فبِعه قبل الإغلاق.")
    if dte == 1 and live and hm >= (15, 30) and pnl < TAKE_PROFIT:
        fire("overnight", "ينتهي غداً: إن لم يتحرك السهم لصالحك سيفقد جزءاً من قيمته الليلة.")
    # اختصار التنبيهات: عند بلوغ الوقف تنبيه واحد فقط، وبعده صمت.
    # وقبل الوقف لا يصلك أكثر من تنبيه هبوط واحد للعقد (الأشد فقط).
    if any(k == "stop" for k, _ in new):
        new = [x for x in new if x[0] == "stop"]
    else:
        drops = [x for x in new if x[0] in DROP_KINDS]
        if drops:
            keep = None if prior_drop else next((x for k in SEVERITY for x in drops if x[0] == k), None)
            new = [x for x in new if x[0] not in DROP_KINDS or x is keep]
    info["changed"] = changed
    return info, new


def reason_hint(info):
    mv = info["mv"]
    if mv is None:
        return None
    if mv >= 0.003:
        return "🔎 السهم تحرك لصالحك لكن التآكل الزمني وهبوط التذبذب الضمني أكلا أكثر من مكسبك."
    if mv > -0.003:
        return "🔎 السبب الأرجح: تآكل زمني وهبوط التذبذب الضمني؛ السهم نفسه لم يتحرك تقريباً."
    return f"🔎 السبب الأرجح: السهم تحرك {abs(mv) * 100:.1f}% ضد اتجاهك."


def pos_header(p):
    return f"<b>{ob.esc(p['ticker'])} {p['side']}</b> | 🎯 {p['strike']:,.1f} | 📅 {date_ar(p['exp'])}"


def stock_line(info):
    if not info["spot"]:
        return None
    s = f"📈 السهم {info['spot']:,.2f}"
    if info["mv"] is not None:
        s += f" ({'لصالحك' if info['mv'] >= 0 else 'ضدك'} {abs(info['mv']) * 100:.1f}%)"
    if info["need"] is not None:
        s += " | تجاوز نقطة التعادل ✅" if info["need"] <= 0 else f" | يحتاج {info['need'] * 100:.1f}% للتعادل"
    if info["prob"] is not None:
        s += f" | احتمال ≈ {info['prob'] * 100:.0f}%"
    return s


def alert_block(p, info, alerts):
    """تنبيه مختصر: العقد، القيمة والربح بالدولار، حركة السهم، وسبب واحد."""
    kinds = [k for k, _ in alerts]
    top = next(k for k in SEVERITY if k in kinds)
    pl = (info["val"] - p["entry"]) * 100
    L = [f"{ALERT_ICON[top]} {pos_header(p)}",
         f"💵 ${p['entry'] * 100:,.0f} ← ${info['val'] * 100:,.0f} | <b>{info['pnl'] * 100:+.0f}%</b> ({pl:+,.0f}$)"]
    sl = stock_line(info)
    if sl:
        L.append(sl)
    text = next(t for k in SEVERITY for kind, t in alerts if kind == k)
    L.append(ob.esc(text))
    if info["spr"] is not None and info["spr"] > 0.40:
        L.append("⚠️ فرق السعر واسع، فالقيمة تقريبية.")
    return "\n".join(L)


def send_alerts(blocks):
    """كل تنبيه بطاقة صورة مع تعليق مختصر (حتى 4 صور) والبقية نص مدموج. الفشل يرجع للنص."""
    texts = []
    for idx, (p, info, new) in enumerate(blocks):
        body = alert_block(p, info, new)
        if idx >= 4:
            texts.append(body)
            continue
        top = next(k for k in SEVERITY if k in [x for x, _ in new])
        name = f"{p['ticker']} {p['strike']:,.1f} {p['side']}"
        stop_p, tp_p = p["entry"] * (1 - STOP_LOSS), p["entry"] * (1 + TAKE_PROFIT)
        mid = None
        try:
            import cards
            png = cards.contract_card("follow", name, f"انتهاء {date_ar(p['exp'])}", info["val"], info["pnl"],
                                      p["entry"], stop_p, tp_p, now=info["val"], badge=ALERT_ICON[top],
                                      foot=f"وقت الرصد {ob.now_ny().astimezone(ob.RY).strftime('%H:%M')} الرياض | الشركات إصدار 16")
            mid = ob.send_photo(png, "📌 <b>متابعة</b> #متابعة\n" + body + "\n" + short_footer())
        except Exception as e:
            print("تعذر إنشاء بطاقة المتابعة:", e)
        if not mid:
            texts.append(body)
    if texts:
        ob.send_telegram("\n\n".join(["📌 <b>متابعة العقود</b> #متابعة"] + texts + [short_footer()]))


def short_footer():
    return f"⚠️ القرار قرارك | 🕒 {ob.now_ny().astimezone(ob.RY).strftime('%H:%M')} الرياض | إصدار 16"


def footer_line():
    return ("⚠️ <i>تعليمي وليست توصية. البيانات متأخرة ~15 دقيقة، وقد يختلف سعرك الحي.</i>\n"
            f"🕒 {ob.now_ny().astimezone(ob.RY).strftime('%H:%M')} الرياض | إصدار 16")


def live_rows(rows):
    """ما يظهر في اللوحة: العقود المفتوحة + ما أُقفل اليوم. المقفل في أيام سابقة يخرج (وتبقى بياناته للدراسة)."""
    today = ob.now_ny().date()
    return [(p, i) for p, i in rows if not is_closed(p) or closed_on(p, today)]


def post_text(p):
    """ماذا فعل العقد بعد إقفاله (تعرضه اللوحة والتقرير): ما بعد الهدف أو ما بعد الوقف. فارغ لغير المقفل."""
    ci = closed_info(p)
    if not ci:
        return ""
    import post_close
    peak = p.get("peak_after")
    return post_close.after_text("tp" if ci[0] == "tp" else "stop", p["entry"], peak, p.get("last_pnl"), TAKE_PROFIT)


def board_png(rows):
    import cards
    rows = live_rows(rows)
    items = []
    for p, info in rows:
        ci = closed_info(p)
        if ci:                                    # مقفل اليوم: القيمة المقفلة بالقاعدة وليس السعر الحي
            lp = ci[2]
            lab = "🔒 مقفل بالهدف" if ci[0] == "tp" else "🔒 مقفل بالوقف"
            items.append({"name": f"{p['ticker']} {p['side']} {p['strike']:g}", "sub": f"{lab} · {date_ar(p['exp'])}",
                          "entry": p["entry"], "now": round(p["entry"] * (1 + lp), 2), "pnl": lp,
                          "status": "tp" if ci[0] == "tp" else "stopped", "closed": True, "post": post_text(p)})
        else:
            items.append({"name": f"{p['ticker']} {p['side']} {p['strike']:g}", "sub": date_ar(p["exp"]),
                          "entry": p["entry"], "now": info["val"], "pnl": info["pnl"], "status": "open"})
    items.sort(key=lambda x: bool(x.get("closed")))
    return cards.board_card("لوحة عقود الشركات", ob.now_ny().strftime("%Y-%m-%d %H:%M") + " نيويورك", items,
                            STOP_LOSS, TAKE_PROFIT)


def _split_rows(rows, n):
    today = n.date()
    closed = [(p, i) for p, i in rows if closed_on(p, today)]
    opened = [(p, i) for p, i in rows if not is_closed(p)]
    locked = sum(closed_info(p)[2] * p["entry"] * 100 for p, _ in closed)
    floating = sum((i["val"] - p["entry"]) * 100 for p, i in opened)
    return closed, opened, locked, floating


def send_board(rows, n):
    """لوحة صورة: المفتوح (عائم) وما أُقفل اليوم (مقفل بنتيجة القاعدة). يرجع 'photo' أو 'text'."""
    rows = live_rows(rows)
    if not rows:
        ob.send_telegram("📋 <b>حالة العقود</b> #حالة\nلا عقود نشطة الآن.")
        return "text"
    closed, opened, locked, floating = _split_rows(rows, n)
    cap = (f"📋 <b>حالة العقود</b> #حالة | مقفل اليوم: <b>{locked:+,.0f}$</b> ({len(closed)}) "
           f"| عائم: <b>{floating:+,.0f}$</b> ({len(opened)})")
    try:
        if ob.send_photo(board_png(rows), cap):
            return "photo"
    except Exception as e:
        print("تعذر إنشاء اللوحة:", e)
    ob.send_telegram(status_message(rows, n))
    return "text"


def highest_pnl(p):
    """أعلى ربح (نسبة من سعر دخول البوت) وصله العقد قبل الإقفال أو بعده. يُقاس بفحوص نصف ساعة فقط."""
    return max(float(p.get("peak", 0.0) or 0.0), float(p.get("peak_after", 0.0) or 0.0))


def _closed_line(p):
    kind, _, lp = closed_info(p)
    hi = highest_pnl(p)
    top = f" | أعلى ربح وصله <b>{hi * 100:+.0f}%</b>" if hi > 0.01 else ""
    post = post_text(p)
    return (f"{'🎯' if kind == 'tp' else '🛑'} <b>{ob.esc(p['ticker'])} {p['side']}</b> {p['strike']:,.1f} | "
            f"{date_ar(p['exp'])}: ${p['entry'] * 100:,.0f} ← <b>{lp * p['entry'] * 100:+,.0f}$</b> ({lp * 100:+.0f}%){top}"
            + (f"\n   ↳ <i>{post}</i>" if post else ""))


def _open_line(p, info):
    pn = info["pnl"]
    ico = "🟩" if pn >= 0.2 else ("🟢" if pn >= 0 else ("🟠" if pn > -WARN_LOSS else "🔴"))
    return (f"{ico} <b>{ob.esc(p['ticker'])} {p['side']}</b> {p['strike']:,.1f} | {date_ar(p['exp'])}: "
            f"${p['entry'] * 100:,.0f} ← ${info['val'] * 100:,.0f} (<b>{pn * 100:+.0f}%</b>) | دخلت {p['time'][5:10]} "
            f"| أعلى {p.get('peak', 0) * 100:+.0f}% | يتبقى {info['dte']} يوم | "
            f"هدف ${p['entry'] * (1 + TAKE_PROFIT) * 100:,.0f} · وقف ${p['entry'] * (1 - STOP_LOSS) * 100:,.0f}")


def _my_line(p, info, m):
    """سطر عقد اشتريته: الربح محسوب على سعر دخولك أنت (وإن لم تذكره فسعر البوت). يرجع (نص، ربح بالدولار)."""
    my_px = float(m.get("price") or p["entry"])
    qty = int(m.get("qty") or 1)
    ci = closed_info(p)
    if ci:
        px = p["entry"] * (1 + ci[2])               # سعر الخروج بقاعدة البوت
        head, now = ("🎯🔒" if ci[0] == "tp" else "🛑🔒"), f"مقفل ${px * 100:,.0f}"
    else:
        px = info["val"]
        head, now = ("🟢" if px >= my_px else "🔴"), f"الآن ${px * 100:,.0f}"
    usd, pct = (px - my_px) * 100 * qty, px / my_px - 1
    src = "" if m.get("price") else " (بسعر البوت)"
    hi_px = p["entry"] * (1 + highest_pnl(p))
    top = f" | أعلى ربح وصله <b>{(hi_px / my_px - 1) * 100:+.0f}%</b>" if hi_px / my_px - 1 > 0.01 else ""
    return (f"{head} <b>{ob.esc(p['ticker'])} {p['side']}</b> {p['strike']:,.1f} | {date_ar(p['exp'])}: "
            f"دخلت ${my_px * 100:,.0f}{src}{f' ×{qty}' if qty > 1 else ''} ← {now} "
            f"(<b>{pct * 100:+.0f}%</b> ≈ {usd:+,.0f}$){top}"), usd


def day_report(rows, n, title="🌇 <b>الشركات — تقرير اليوم</b> #ملخص", mine=None):
    """عقودك المشتراة (على سعرك)، ثم مراقبتك، ثم توصيات البوت التي لم تختر فيها:
    المقفل اليوم بنتيجته، وما لم يُقفل: توصيات اليوم، ثم المنتقلة بصورة كاملة.
    إن لم تختر شيئاً بالأزرار يبقى التقرير بشكله المعتاد."""
    today = n.date()
    mine = load_mine() if mine is None else mine
    closed_all, opened_all, _, _ = _split_rows(rows, n)
    pairs = closed_all + opened_all
    bought = [(p, i, mine_for(p, mine)) for p, i in pairs if (mine_for(p, mine) or {}).get("mode") == "bought"]
    watched = [(p, i) for p, i in pairs if (mine_for(p, mine) or {}).get("mode") == "watch"]
    chosen = {id(p) for p, *_ in bought} | {id(p) for p, _ in watched}
    rest = [(p, i) for p, i in rows if id(p) not in chosen]
    closed, opened, locked, floating = _split_rows(rest, n)
    L = [title, ""]
    if bought:
        L.append("🛒 <b>عقودي المشتراة</b>")
        net = 0.0
        for p, i, m in sorted(bought, key=lambda x: x[0]["time"]):
            line, usd = _my_line(p, i, m)
            L.append(line)
            net += usd
        L += [f"   صافيها على سعر دخولك: <b>{net:+,.0f}$</b>", ""]
    if watched:
        L.append("👀 <b>مراقبتي</b>")
        for p, i in watched:
            L.append(_closed_line(p) if is_closed(p) else _open_line(p, i))
        L.append("")
    if (bought or watched) and (closed or opened):
        L += ["🤖 <b>باقي توصيات البوت</b> (لم تختر فيها)", ""]
    if closed:
        L.append("🔒 <b>مقفلة اليوم</b>")
        for p, _ in sorted(closed, key=lambda x: -closed_info(x[0])[2]):
            L.append(_closed_line(p))
        L += [f"   الصافي المقفل: <b>{locked:+,.0f}$</b>", ""]
    fresh = [(p, i) for p, i in opened if _on_date(p["time"], today)]
    carry = [(p, i) for p, i in opened if not _on_date(p["time"], today)]
    for head, grp in (("🆕 <b>مفتوحة (توصية اليوم)</b>", fresh), ("↪️ <b>منتقلة، لم تُقفل بعد</b>", carry)):
        if not grp:
            continue
        L.append(head)
        for p, info in sorted(grp, key=lambda x: -x[1]["pnl"]):
            L.append(_open_line(p, info))
        L.append("")
    if not pairs:
        L.append("لا عقود نشطة الآن.")
    if opened:
        L.append(f"العائم على الورق: <b>{floating:+,.0f}$</b> (بسعر الوسط، لو بعت الآن)")
    L += ["", f"⚠️ <i>المقفل بقاعدة الهدف +{TAKE_PROFIT * 100:.0f}% / الوقف −{STOP_LOSS * 100:.0f}%: قيمة نظرية، "
              "فالفحص كل نصف ساعة وقد يفوتنا أعلى سعر حقيقي ويختلف سعر تنفيذك.</i>", short_footer()]
    return "\n".join(L)


def status_message(rows, n):
    return day_report(rows, n, title="📋 <b>الشركات — حالة العقود</b> #حالة")


def track_rows(rows, n, spy_spot, vix):
    """يسجل كل تقييم في stocks_track.csv (حتى بعد الوقف) لدراسة الارتداد حسب السوق والأخبار لاحقاً."""
    import csv
    new = not os.path.exists(TRACK_FILE)
    try:
        with open(TRACK_FILE, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "id", "ticker", "side", "status", "spot", "val", "pnl", "stock_move", "prob", "spy", "vix"])
            for p, info in rows:
                w.writerow([n.isoformat(timespec="minutes"), p["id"], p["ticker"], p["side"], p["status"],
                            "" if info["spot"] is None else round(info["spot"], 2), round(info["val"], 3),
                            round(info["pnl"], 3), "" if info["mv"] is None else round(info["mv"], 4),
                            "" if info["prob"] is None else round(info["prob"], 3),
                            "" if not spy_spot else round(spy_spot, 2), "" if vix is None else round(vix, 1)])
    except Exception as e:
        print("تعذر تسجيل التتبع:", e)


def do_monitor(n, force, P):
    prune_positions(P, n)
    act = active_positions(P, n)
    today_s = n.date().isoformat()
    state = load_state()
    sent_today = state.get("date") == today_s and bool(state.get("sent"))
    rows, blocks = [], []
    cache, tickers = {}, {}
    spy_spot = spot_now(yf.Ticker("SPY")) if act else None
    for p0 in act:
        p = copy.deepcopy(p0) if force else p0      # التجربة اليدوية لا تغيّر الحالة المحفوظة
        tk = p["ticker"]
        try:
            if tk not in tickers:
                t = yf.Ticker(tk)
                tickers[tk] = (t, spot_now(t))
            t, spot = tickers[tk]
            q = contract_quote(t, p, cache)
            if not force:
                _live_shadow_stock(p, q)      # ظل لحظي: تسجيل فقط
            if q is None:
                print("لا سعر حالياً للعقد", p["id"])
                continue
            info, new = evaluate(p, n, q, spot, spy_spot)
        except Exception as e:
            print("تعذر تقييم", p0.get("id"), e)
            continue
        if info["changed"] and not force:
            P["dirty"] = True
        rows.append((p, info))
        if new and not force:
            blocks.append((p, info, new))
    if rows and not force:
        try:
            vix_now = ob.vix_info()[0]
        except Exception:
            vix_now = None
        track_rows(rows, n, spy_spot, vix_now)
    if blocks:
        send_alerts(blocks)
    hm = (n.hour, n.minute)
    if force or (hm >= min(SUMMARY_AT, session_end_stocks(n)) and P.get("summary_date") != today_s):
        if rows:
            how = send_board(rows, n)
            if how == "photo" and not force:
                ob.send_telegram(day_report(rows, n))
        elif force:
            ob.send_telegram("📋 <b>الشركات — حالة العقود المفتوحة</b>\nلا توجد عقود مفتوحة الآن.")
        elif not sent_today:
            ob.send_telegram("⚪ <b>الشركات — ملخص اليوم</b>\nلا فرص ولا عقود مفتوحة اليوم.")
        if not force:
            P["summary_date"] = today_s
            P["dirty"] = True


# ====================== التحليل الرئيسي ======================
def completed_volume(df, n):
    """آخر حجم تداول مكتمل: نتجنب شمعة اليوم الجزئية أثناء الجلسة."""
    v = df["Volume"].astype(float)
    last_day = df.index[-1].date() if hasattr(df.index[-1], "date") else None
    if last_day == n.date() and n.hour < 16 and len(v) > 1:
        return float(v.iloc[-2])
    return float(v.iloc[-1])


def intraday_price(t, fallback):
    px = _live_px(t)
    if px:
        return px
    try:
        h = t.history(period="1d", interval="5m")["Close"].dropna()
        if len(h):
            return float(h.iloc[-1])
    except Exception as ex:
        data_gaps.note("stocks", "price_5m", ex, getattr(t, "ticker", ""))
    return fallback


# شركات إضافية للدراسة الصامتة فقط (لا توصيات منها بعد): نرى هل كانت ستظهر فرصاً أفضل من القائمة الحالية
EXTRA = ("ORCL CRM ADBE QCOM TXN AMAT LRCX KLAC CSCO IBM NOW SHOP PYPL ABNB CRWD PANW SNOW DDOG NET ZS ROKU DKNG "
         "CCL AAL DAL UAL GM NKE SBUX MCD KO PEP PG COST HD LOW TGT CVS UNH JNJ MRK ABBV LLY BMY GILD MRNA GS MS C WFC "
         "SCHW AXP COF COP OXY SLB HAL DVN MPC GE CAT DE RTX HON UPS FDX MMM VZ TMUS CMCSA WBD EBAY LULU SMCI ARM ANET "
         "MRVL ON WDC GME AMC BABA PDD JD NIO TSM ASML SPOT RBLX U AFRM UPST MARA RIOT IONQ RGTI HIMS APP").split()
SHADOW_FILE = "stocks_shadow_log.csv"
FLOW_FILE = "stocks_flow_log.csv"


def _append_csv(path, header, rows):
    import csv
    if not rows:
        return
    fresh = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if fresh:
            w.writerow(header)
        w.writerows(rows)


def shadow_log(n, scored, finalists_flow, spy5, spy_up):
    """دراسة صامتة (لا تُرسل شيئاً ولا تغيّر التوصيات): (1) أفضل مرشحين من قائمة موسّعة مقابل القائمة الحالية،
    (2) نسبة حجم الكول/البوت لكل شركة لقياس انحياز الكول البنيوي."""
    t = n.isoformat(timespec="minutes")
    _append_csv(FLOW_FILE, ["time", "ticker", "call_vol", "put_vol", "ratio"],
                [[t, tk, round(cv), round(pv), round(cv / max(pv, 1.0), 2)] for tk, cv, pv in finalists_flow])
    try:
        raw2 = yf.download(EXTRA, period="6mo", group_by="ticker", auto_adjust=True, progress=False, threads=True)
    except Exception as e:
        print("تعذر تنزيل القائمة الموسّعة:", e)
        return
    rows = []
    main_names = {s["ticker"] for s in scored}
    cands = [(s["ticker"], s["bull"], s["bear"], s["info"], True) for s in scored]
    for tk in EXTRA:
        try:
            df = raw2[tk].dropna(subset=["Close"])
            if len(df) < 55:
                continue
            bull, bear, _why, info = tech_points(df, spy5, None, completed_volume(df, n))
        except Exception:
            continue
        bull, bear = (bull + 0.5, bear) if spy_up else (bull, bear + 0.5)
        cands.append((tk, bull, bear, info, False))
    cands.sort(key=lambda x: -abs(x[1] - x[2]))
    for tk, bull, bear, info, inmain in cands[:25]:
        edge = bull - bear
        rows.append([t, tk, int(inmain), round(bull, 1), round(bear, 1), round(edge, 1), "CALL" if edge > 0 else "PUT",
                     round(info["S"], 2), round(info["ret5"] * 100, 1), round(info["vr"], 2), round(info["rsi"])])
    _append_csv(SHADOW_FILE, ["time", "ticker", "in_main", "bull", "bear", "edge", "side", "price", "ret5_pct",
                              "vol_ratio", "rsi"], rows)


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

    ideas, watch, flow_rows = [], [], []
    blocked = []   # blocked_shadow: ideas blocked by rules, log only
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
        flow_rows.append((tk, cv, pv))
        s["bull"] += fb
        s["bear"] += fe
        if fwhy:
            s["why"].append(fwhy)
        edge = s["bull"] - s["bear"]
        side = "CALL" if edge > 0 else "PUT"
        strength = min(10.0, abs(edge) * 1.6)
        dates = get_dates(t)
        s.update(heads=heads[:3], dates=dates, edge=edge, side=side, strength=strength, cv=cv, pv=pv,
                 news_b=nb, news_e=ne, flow_b=fb, flow_e=fe)
        if abs(edge) < MIN_EDGE:
            watch.append((tk, f"الفرق {abs(edge):.1f} أقل من {MIN_EDGE}"))
            continue
        if shock and side == "CALL":
            watch.append((tk, "تحذير سوق: لا نقترح Call الآن"))
            try:   # ظل الحجب (تسجيل فقط): العقد الذي كان سيُقترح لولا تحذير السوق
                Sx = intraday_price(t, s["info"]["S"])
                cx = pick_contracts(side, _live_chains(chains, side, Sx), Sx, s["info"]["rv"], s["info"]["closes"])
                if cx:
                    blocked.append({"tag": "market_warning", "ticker": tk, "side": side, "edge": edge,
                                    "strength": min(10.0, abs(edge) * 1.1), "S": Sx, "contracts": cx})
            except Exception as e:
                print("تعذر التقاط عقد ظل الحجب:", tk, e)
            continue
        S = intraday_price(t, s["info"]["S"])
        contracts = pick_contracts(side, _live_chains(chains, side, S), S, s["info"]["rv"], s["info"]["closes"])
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
    kept, cnt = [], {"CALL": 0, "PUT": 0}
    for i in ideas:
        if cnt[i["side"]] < MAX_SAME_SIDE:
            kept.append(i)
            cnt[i["side"]] += 1
        else:
            blocked.append(_blocked_idea("same_side_cap", i))   # لظل الحجب (تسجيل فقط)
    ideas = kept
    blocked += [_blocked_idea("max_ideas_cap", i) for i in ideas[MAX_IDEAS:]]
    if in_window(n):      # دراسة صامتة، وأي خطأ فيها لا يؤثر على التوصيات
        try:
            shadow_log(n, scored, flow_rows, spy5, spy_up)
        except Exception as e:
            print("تعذر تسجيل الدراسة الصامتة:", e)
    spy_px = intraday_price(yf.Ticker("SPY"), float(spyc.iloc[-1]))
    return {"n": n, "today": today, "ideas": ideas[:MAX_IDEAS], "watch": watch, "vix": vix, "vix_pct": vix_pct,
            "spy5": spy5, "spy_up": spy_up, "shock": shock, "is_test": not in_window(n), "scanned": len(scored),
            "spy_px": spy_px, "blocked": blocked}


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
        f"💵 <b>${k['cost']:,.0f}</b> ({k['price']:.2f}) | يحتاج {'+' if call else '-'}{k['need'] * 100:.1f}% | {mark}",
        f"├ 🟢 <b>دخول</b> بسعر حتى <b>{k['price']:.2f}</b> (لا تلاحقه فوق +10%)"
        + (f" · 📡 {ob.SRC_NOTE[k['price_src']]}" if k.get("price_src") in ob.SRC_NOTE else ""),
    ] + stock_levels(i, k))


def stock_levels(i, k):
    """سطرا الخروج مع مستوى السهم التقريبي (نموذج بلاك-شولز، افتراض احتفاظ يوم واحد)."""
    tp, sl = f"+${k['cost'] * TAKE_PROFIT:,.0f}", f"-${k['cost'] * STOP_LOSS:,.0f}"
    try:
        kk = {"strike": k["strike"], "price": k["price"], "spot": i["S"], "iv": k["iv"],
              "T": max(k.get("dte", 1), 0.5) / 365}
        lv = ob.index_levels(kk, i["side"], hold_min=1440)
        if lv:
            return [f"├ 🎯 <b>خروج بربح</b> {tp} ← السهم ≈ {lv[0]:,.2f}",
                    f"└ 🛑 <b>خروج بخسارة</b> {sl} ← السهم ≈ {lv[1]:,.2f}"]
    except Exception:
        pass
    return [f"├ 🎯 <b>خروج بربح</b> {tp}", f"└ 🛑 <b>خروج بخسارة</b> {sl}"]


def quote_line(k, now=None):
    """سطر مصدر السعر ووقته وفرق الشراء/البيع في التوصية: ما يظهر في وسيطك قد يختلف بعد دقائق، فنُظهر ما عُرض لحظة الإرسال."""
    when = (now or ob.now_ny()).astimezone(ob.RY).strftime("%H:%M")
    if k.get("price_src") in ("alpaca", "tasty") and k.get("bid"):
        ask, bid = k["price"], k["bid"]
        spr = (ask - bid) / ((ask + bid) / 2)
        line = f"📡 لحظي {when} الرياض: طلب <b>{ask:.2f}</b> | بيع <b>{bid:.2f}</b> | فرق {spr * 100:.0f}%"
        if spr >= 0.08:
            line += f"\n📝 الفرق كبير: ضع أمراً محدداً (Limit) قرب المنتصف {(ask + bid) / 2:.2f}، ولا تشترِ بسعر السوق"
        return line.rstrip("\n")
    return f"⏱️ سعر ياهو متأخر ~15د ({when} الرياض) ولا عرض/طلب لحظي؛ تحقق من السعر الحي"


def idea_caption(i):
    call = i["side"] == "CALL"
    k = i["contracts"][0]
    side = i["side"]
    flags = ""
    ed = i["dates"]["earn"]
    if ed is not None and ed <= date.fromisoformat(k["exp"]):
        flags = " 🚨أرباح"
    lv = [x.replace("├ ", "").replace("└ ", "") for x in stock_levels(i, k)]
    return "\n".join([
        f"🔔 <b>توصية الشركات | {ob.esc(i['ticker'])} {side} {'🟢' if call else '🔴'}</b> | ⭐ {i['strength']:.1f}{flags} #توصية",
        f"✅ <b>الدخول:</b> {ob.esc(i['ticker'])} {k['strike']:,.1f} {side} | 📅 {date_ar(k['exp'])}",
        f"💵 بسعر حتى <b>{k['price']:.2f}</b> | تكلفة <b>${k['cost']:,.0f}</b> للعقد | يحتاج {'+' if call else '-'}{k['need'] * 100:.1f}%",
        quote_line(k),
        lv[0], lv[1],
        f"💰 أقصى خسارة ≈ ${k['cost'] * STOP_LOSS:,.0f}",
        "⚠️ <i>تعليمي وليست توصية. لا تلاحق السعر فوق +10%.</i>"])


def idea_keyboard(i):
    """زرّان تحت كل توصية: مراقبة أو اشتريت. يعالجهما بوت الأوامر (قد يتأخر حتى ~15 دقيقة)."""
    k = i["contracts"][0]
    base = pos_id(i["ticker"], i["side"], k["exp"], k["strike"])
    day = ob.now_ny().date().isoformat()
    return {"inline_keyboard": [[{"text": "👀 مراقبة", "callback_data": f"w|{base}|{day}"},
                                 {"text": "🛒 اشتريت", "callback_data": f"b|{base}|{day}"}]]}


def send_photo_kb(png, caption, markup):
    """مثل ob.send_photo لكن مع أزرار. (دالة خاصة بالشركات حتى لا نلمس ملف SPX المشترك.) يرجع رقم الرسالة أو None."""
    import requests
    print("[صورة]", caption)
    ob.log_message(f"[صورة] {caption}")
    if not ob.TOKEN or not ob.CHAT_ID or not png:
        return None
    data = {"chat_id": ob.CHAT_ID, "caption": caption, "parse_mode": "HTML", "reply_markup": json.dumps(markup)}
    try:
        r = requests.post(f"https://api.telegram.org/bot{ob.TOKEN}/sendPhoto", data=data,
                          files={"photo": ("card.png", png, "image/png")}, timeout=40)
        ob.send_photo_copies(png, caption)          # نسخة للمشاهدين بلا أزرار
        if not r.ok:
            print("خطأ من تليجرام:", r.text)
            return None
        return r.json()["result"]["message_id"]
    except Exception as e:
        print("تعذر إرسال الصورة:", e)
        return None


def send_ideas(a, ideas):
    """كل فكرة بطاقة صورة وتذكرة مختصرة. يرجع رقم أول رسالة، وعند تعذر الصور نرسل النص الكامل."""
    first, ok = None, 0
    for i in ideas:
        k = i["contracts"][0]
        side = i["side"]
        mid = None
        try:
            import cards
            stop_p, tp_p = k["price"] * (1 - STOP_LOSS), k["price"] * (1 + TAKE_PROFIT)
            png = cards.contract_card("entry", f"{i['ticker']} {k['strike']:,.1f} {side}",
                                      f"انتهاء {date_ar(k['exp'])} · السهم {i['S']:,.2f}", k["price"], None,
                                      k["price"], stop_p, tp_p, badge=f"{side} {'🟢' if side == 'CALL' else '🔴'}",
                                      foot=f"{ob.now_ny().astimezone(ob.RY).strftime('%H:%M')} الرياض | الشركات إصدار 16")
            mid = send_photo_kb(png, idea_caption(i), idea_keyboard(i))
        except Exception as e:
            print("تعذر إنشاء بطاقة الفكرة:", e)
        if mid:
            ok += 1
            first = first or mid
    if ok == 0:
        return ob.send_telegram(signal_message(a, ideas))
    return first


def signal_message(a, ideas):
    head = "🔔 <b>توصية الشركات — فرص الأسبوع</b>" + (" (تجريبي، السوق مغلق)" if a["is_test"] else "") + " #توصية"
    L = [head, ""]
    for i in ideas:
        L += [idea_line(i), ""]
    L += ["🟩 قوي  🟨 متوسط  🟧 مقبول  🔥 طلب عالٍ  🚨 أرباح",
          "📡 سأتابع هذه العقود وأنبّهك عند الوقف أو الهدف أو ضعف الفكرة.",
          "⚠️ <i>تعليمي وليست توصية. تحقق من السعر الحي، وأقصى خسارة هي سعر العقد.</i>",
          f"🕒 {ob.now_ny().astimezone(ob.RY).strftime('%H:%M')} الرياض | إصدار 16"]
    return "\n".join(L)


def no_idea_message(a):
    L = ["⚪ <b>الشركات — لا فرص قوية الآن</b> #خطة", f"تم فحص {a['scanned']} شركة."]
    if a["shock"]:
        L.append("🔴 تحذير: السوق في حالة صدمة (VIX أو S&P).")
    if a["watch"]:
        L.append("")
        L.append("👀 قيد المراقبة:")
        for tk, why in a["watch"][:4]:
            L.append(f"• {ob.esc(tk)}: {ob.esc(why)}")
    return "\n".join(L)


def details_message(a, ideas, log_line):
    L = [f"🏢 <b>تحليل الشركات</b> | {a['today'].isoformat()}", ob.LINE]
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
def do_scan(n, force, P):
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

    _live_prices_stock(a["ideas"])
    if force:
        if a["ideas"]:
            mid = send_ideas(a, a["ideas"])
            shown, full = ob.analysis_wrap(details_message(a, a["ideas"], log_line))
        else:
            mid = ob.send_telegram(no_idea_message(a))
            shown, full = ob.analysis_wrap(details_message(a, [], log_line))
        ob.send_telegram(shown, reply_to=mid, full_text=full)
        return

    # لا نكرر التوصية على سهم لديك فيه عقد مفتوح بنفس الاتجاه (منعاً لمضاعفة المخاطرة)
    blocked = blocked_map(P, n)
    new, held = [], []
    for i in a["ideas"]:
        if f"{i['ticker']}:{i['side']}" in state["sent"]:
            continue
        if (i["ticker"], i["side"]) in blocked:
            a["watch"].insert(0, (i["ticker"], blocked[(i["ticker"], i["side"])]))
            held.append(_blocked_idea("open_same_dir", i))
            continue
        new.append(i)
    try:   # ظل الحجب: أفكار حجبتها الشروط تُتابَع بلا إرسال لنعرف هل كان الحجب في صالحنا (تسجيل فقط)
        import blocked_shadow
        blocked_shadow.log_stocks(n, a.get("blocked", []) + held)
    except Exception as e:
        print("تعذر تسجيل ظل الحجب:", e)
    try:   # ظل قواعد المحترفين: عقدان شهريان بدلتا 0.50 و0.70 بجانب عقد كل فكرة، تُتابَع بلا إرسال (تسجيل فقط)
        import pro_rules_shadow
        pro_rules_shadow.log_ideas(n, [dict(i, tag="sent") for i in new] + a.get("blocked", []) + held, ob.abs_delta)
    except Exception as e:
        print("تعذر تسجيل ظل قواعد المحترفين:", e)
    if new:
        mid = send_ideas(a, new)
        shown, full = ob.analysis_wrap(details_message(a, new, log_line))
        ob.send_telegram(shown, reply_to=mid, full_text=full)
        for i in new:
            state["sent"][f"{i['ticker']}:{i['side']}"] = n.isoformat()
            log_idea(n, i)
            add_position(P, n, i, a.get("spy_px"))
        try:   # ظل فلتر القطاع: تسجيل فقط (لا يغيّر التوصية)
            import stocks_sector_shadow
            stocks_sector_shadow.snapshot(n, new)
        except Exception as e:
            print("تعذر تسجيل ظل القطاع:", e)
        state["report_sent"] = True
    elif not state.get("report_sent"):
        ob.send_telegram(no_idea_message(a) + "\n\n🔔 سأواصل الفحص وأرسل لك عند ظهور فرصة.")
        state["report_sent"] = True
    else:
        print("لا فرص جديدة، لا رسالة.")
    save_state(state)


def run_once(force=False, mode="scan"):
    n = ob.now_ny()
    scan_ok = force or (mode != "monitor" and in_window(n))
    mon_ok = force or monitor_window(n)
    if not (scan_ok or mon_ok):
        print("خارج وقت التداول، لا شيء للتنفيذ.")
        return
    P = load_positions()
    if P is None:
        P = bootstrap_positions(n)
    if scan_ok:
        do_scan(n, force, P)
    if mon_ok:
        try:
            do_monitor(n, force, P)
        except Exception as e:
            print("خطأ في المتابعة:", e)
            if force:
                ob.send_telegram(f"⚠️ خطأ في متابعة العقود: {ob.esc(e)}")
    if P.get("dirty"):
        save_positions(P)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="تشغيل فوري حتى خارج وقت التداول")
    args = parser.parse_args()
    run_once(force=args.force or os.getenv("FORCE") == "1", mode=os.getenv("BOT_MODE", "scan"))
