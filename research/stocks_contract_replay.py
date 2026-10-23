#!/usr/bin/env python3
"""استعادة تاريخية بأسعار حقيقية: ماذا لو اختير للشركات عقد بقاعدة المحترفين بدل عقد البوت؟ (قراءة فقط، لا تغيّر أي توصية)

لكل توصية سابقة في stocks_positions.json (نفس لحظة التوصية والاتجاه) نعيد تشغيل ثلاثة عقود على شموع Alpaca الحقيقية (5د، صفقات فعلية):
  bot     = عقد البوت نفسه (ضابط: يجب أن تقترب نتيجته من المسجّل فعلاً)
  atm     = دلتا ≈ 0.50 بانتهاء شهري نحو 30–45 يوماً
  itm     = «بديل السهم»: دلتا ≈ 0.70 بالانتهاء نفسه
وبقاعدتي خروج على كل عقد:
  r1 = قاعدة البوت (+50% / −40% بعد 30 دقيقة / الانتهاء)
  r2 = قاعدة المحترفين: +50%، ووقف على السهم 1.5% ضد الاتجاه (بعد 30 دقيقة)، ووقف زمني عند بقاء 7 أيام أو أقل للانتهاء
تسعير الدخول والخروج: سعر افتتاح أول شمعة بعد التوصية + نصف فرق 1.5% وعمولة (كما في tail_log). السترايك البديل يُختار من شبكة مرشحة حول الدلتا المطلوبة
(بتذبذب مُعايَر على عقد البوت وقت الدخول) ثم يُبقى على ما له شموع فعلية فقط.
⚠️ عقود لم تنتهِ بعد تُقيَّم بآخر شمعة متاحة (سبب «end»)، وتُمدَّد النتائج مع مرور الأيام؛ والعينة صغيرة (عشرات التوصيات في أيام قليلة) وشموع العقود البعيدة عن السعر قد تكون متباعدة (آخر صفقة لا سعر عرض)؛ للمراقبة لا للحكم.
تشغيل (على الخادم، يحتاج ALPACA_KEY/ALPACA_SECRET): python research/stocks_contract_replay.py   المخرجات: research/stocks_contract_replay_results.md و..._trades.csv
"""
import json
import math
import os
import sys
import time as _time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

NY = ZoneInfo("America/New_York")
OUT_MD = os.path.join(HERE, "stocks_contract_replay_results.md")
OUT_CSV = os.path.join(HERE, "stocks_contract_replay_trades.csv")
HS, COMM = 0.015, 0.0065                 # نصف فرق العرض والعمولة (كما في tail_log)
TP, STOP, MIN_AGE_MIN = 0.50, 0.40, 30
STOCK_STOP = 0.015
TIME_STOP_DAYS = 7
VARIANTS = {"atm": 0.50, "itm": 0.70}
EXP_MIN_DTE, EXP_MAX_DTE = 28, 50
ENTRY_WINDOW_MIN = 30
MIN_BARS = 5
STEPS = (0.5, 1.0, 2.5, 5.0, 10.0)


# ============================ دوال نقية ============================
def third_friday(y, m):
    d = date(y, m, 15)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def monthly_expiry(day):
    """أول انتهاء شهري (الجمعة الثالثة) بعد 28 يوماً من day وقبل 50 يوماً؛ وإلا أول واحد بعد 28 يوماً."""
    cands = []
    y, m = day.year, day.month
    for k in range(4):
        mm = (m - 1 + k) % 12 + 1
        yy = y + (m - 1 + k) // 12
        cands.append(third_friday(yy, mm))
    ok = [c for c in cands if EXP_MIN_DTE <= (c - day).days <= EXP_MAX_DTE]
    return ok[0] if ok else next(c for c in cands if (c - day).days >= EXP_MIN_DTE)


def occ(ticker, exp, side, strike):
    return f"{ticker}{exp.strftime('%y%m%d')}{'C' if side == 'CALL' else 'P'}{int(round(float(strike) * 1000)):08d}"


def calibrate_iv(S, K, T, price, side, bs_price):
    """تذبذب ضمني من سعر العقد (قسمة ثنائية). None إن لم يقع السعر ضمن المدى."""
    lo, hi = 0.02, 4.0
    if not (bs_price(S, K, T, hi, side) > price > bs_price(S, K, T, lo, side)):
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs_price(S, K, T, mid, side) > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def strike_for_delta(S, T, iv, side, target, abs_delta):
    """السترايك المستمر الذي يعطي دلتا target (دلتا مطلقة تتناقص مع السترايك للكول وتتزايد للبوت)."""
    lo, hi = S * 0.5, S * 1.5
    for _ in range(60):
        mid = (lo + hi) / 2
        d = abs_delta(S, mid, T, iv, side)
        if (side == "CALL" and d > target) or (side == "PUT" and d < target):
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def candidate_strikes(k_star):
    out = set()
    for s in STEPS:
        for k in (math.floor(k_star / s) * s, math.ceil(k_star / s) * s):
            if k > 0:
                out.add(round(k, 3))
    return sorted(out)


def first_bar_after(bars, t0):
    """أول شمعة بدايتها ≥ t0 وخلال ENTRY_WINDOW_MIN دقيقة منه، وإلا None."""
    x = bars[bars.index >= t0]
    if x.empty or (x.index[0] - t0).total_seconds() > ENTRY_WINDOW_MIN * 60:
        return None
    return x.index[0]


def simulate(bars, stock, t0, side, entry_spot, exp, rule):
    """bars: DataFrame(o,h,l,c) فهرسه وقت نيويورك. stock: Series إغلاق السهم بالفهرس نفسه (أو None). يرجع (نتيجة، سبب، وقت) أو None."""
    ts = first_bar_after(bars, t0)
    if ts is None:
        return None
    entry = float(bars.loc[ts, "o"]) * (1 + HS) + COMM
    if entry <= 0:
        return None
    d_exp = exp
    last = None
    for t, r in bars[bars.index >= ts].iterrows():
        bar_end = t + timedelta(minutes=5)
        val = (float(r["c"]) * (1 - HS) - COMM) / entry - 1
        last = (val, "end", bar_end)
        age = (bar_end - ts).total_seconds() / 60
        if val >= TP:
            return TP, "tp", bar_end
        if rule == "r1":
            if age >= MIN_AGE_MIN and val <= -STOP:
                return -STOP, "stop", bar_end
        else:
            if stock is not None and age >= MIN_AGE_MIN:
                px = stock.asof(bar_end)
                if px == px and px > 0:
                    adverse = -(px / entry_spot - 1) if side == "CALL" else (px / entry_spot - 1)
                    if adverse >= STOCK_STOP:
                        return val, "stock_stop", bar_end
            if (d_exp - bar_end.date()).days <= TIME_STOP_DAYS and bar_end.hour * 60 + bar_end.minute >= 15 * 60 + 55:
                return val, "time", bar_end
        if bar_end.date() >= d_exp and bar_end.hour * 60 + bar_end.minute >= 15 * 60 + 55:
            return val, "expiry", bar_end
    return last


# ============================ البيانات ============================
def fetch_bars(symbols, start, end):
    """شموع 5د من Alpaca لعدة رموز وفترة (UTC iso). {رمز: DataFrame(o,h,l,c) بفهرس نيويورك}."""
    out = {}
    syms = list(dict.fromkeys(symbols))
    for i in range(0, len(syms), 50):
        page = None
        while True:
            q = {"symbols": ",".join(syms[i:i + 50]), "timeframe": "5Min", "start": start, "end": end, "limit": 10000}
            if page:
                q["page_token"] = page
            req = urllib.request.Request("https://data.alpaca.markets/v1beta1/options/bars?" + urllib.parse.urlencode(q),
                                         headers={"APCA-API-KEY-ID": os.getenv("ALPACA_KEY", "").strip(),
                                                  "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET", "").strip()})
            j = json.loads(urllib.request.urlopen(req, timeout=30).read())
            for s, b in (j.get("bars") or {}).items():
                out.setdefault(s, []).extend(b)
            page = j.get("next_page_token")
            if not page:
                break
        _time.sleep(0.3)
    res = {}
    for s, b in out.items():
        df = pd.DataFrame(b)
        df.index = pd.to_datetime(df["t"]).dt.tz_convert(NY)
        res[s] = df.sort_index()[["o", "h", "l", "c"]]
    return res


def stock_series(ticker):
    import yfinance as yf
    h = yf.Ticker(ticker).history(period="60d", interval="5m")["Close"].dropna()
    if h.empty:
        return None
    h.index = h.index.tz_convert(NY)
    return h


# ============================ التشغيل ============================
def plan_position(p, abs_delta, bs_price):
    """{variant: (انتهاء، [سترايكات مرشحة])} للتوصية p، أو None إن تعذرت المعايرة."""
    t0 = datetime.fromisoformat(p["time"])
    day = t0.date()
    S = float(p["entry_spot"])
    exp_bot = date.fromisoformat(p["exp"])
    T_bot = max((exp_bot - day).days, 0.5) / 365
    iv = calibrate_iv(S, float(p["strike"]), T_bot, float(p["entry"]), p["side"], bs_price)
    if iv is None:
        return None
    exp = monthly_expiry(day)
    T = (exp - day).days / 365
    return {v: (exp, candidate_strikes(strike_for_delta(S, T, iv, p["side"], d, abs_delta)), iv, T) for v, d in VARIANTS.items()}


def cap_end(expiry, now=None):
    """نهاية نافذة الجلب: إغلاق يوم الانتهاء أو الآن−16 دقيقة أيهما أبكر؛ Alpaca يرفض (403 OPRA) أي نهاية في المستقبل أو أحدث من 15 دقيقة."""
    utc = ZoneInfo("UTC")
    close = datetime.combine(expiry, datetime.min.time(), NY).replace(hour=16).astimezone(utc)
    return min(close, (now or datetime.now(utc)) - timedelta(minutes=16))


def pick_strike(cands, bars_by_sym, ticker, exp, side, S, iv, T, target, abs_delta, t0):
    best = None
    for k in cands:
        b = bars_by_sym.get(occ(ticker, exp, side, k))
        if b is None or len(b) < MIN_BARS or first_bar_after(b, t0) is None:
            continue
        dd = abs(abs_delta(S, k, T, iv, side) - target)
        if best is None or dd < best[0]:
            best = (dd, k)
    return best[1] if best else None


def run(pos, ob, fetch=None, get_stock=None):
    """يعيد تشغيل كل التوصيات في pos. fetch وget_stock قابلتان للاستبدال في الاختبار. يرجع DataFrame بالصفقات."""
    fetch = fetch or fetch_bars
    get_stock = get_stock or stock_series
    rows = []
    for p in pos:
        if not p.get("entry") or not p.get("entry_spot"):
            continue
        plan = plan_position(p, ob.abs_delta, ob.bs_price)
        if plan is None:
            continue
        t0 = datetime.fromisoformat(p["time"])
        S, side, tk = float(p["entry_spot"]), p["side"], p["ticker"]
        exp_bot = date.fromisoformat(p["exp"])
        syms = [occ(tk, exp_bot, side, p["strike"])]
        for v, (exp, ks, _, _) in plan.items():
            syms += [occ(tk, exp, side, k) for k in ks]
        end = max(plan[v][0] for v in plan)
        utc = ZoneInfo("UTC")
        stop_at = cap_end(end)
        if stop_at <= t0.astimezone(utc) + timedelta(minutes=10):     # توصية حديثة جداً: لا شموع بعد (Alpaca يرفض نافذة معكوسة)
            print("تخطي توصية حديثة جداً:", p["id"])
            continue
        try:
            bars = fetch(syms, t0.astimezone(utc).isoformat(), stop_at.isoformat())
        except Exception as e:  # noqa: BLE001
            print("فشل الجلب:", p["id"], getattr(e, "code", type(e).__name__))   # رمز HTTP فقط، لا نص الخطأ (قد يحمل ترويسات)
            continue
        stock = get_stock(tk)
        chosen = {"bot": (exp_bot, float(p["strike"]))}
        for v, (exp, ks, iv, T) in plan.items():
            k = pick_strike(ks, bars, tk, exp, side, S, iv, T, VARIANTS[v], ob.abs_delta, t0)
            if k is not None:
                chosen[v] = (exp, k)
        for v, (exp, k) in chosen.items():
            b = bars.get(occ(tk, exp, side, k))
            if b is None:
                continue
            for rule in ("r1", "r2"):
                res = simulate(b, stock, t0, side, S, exp, rule)
                if res is not None:
                    rows.append({"id": p["id"], "ticker": tk, "side": side, "day": t0.date().isoformat(), "variant": v, "rule": rule,
                                 "exp": exp.isoformat(), "strike": k, "pnl": round(res[0], 4), "why": res[1],
                                 "recorded": (p.get("closed") or {}).get("pnl")})
    return pd.DataFrame(rows)


def main():
    import options_bot as ob
    if not (os.getenv("ALPACA_KEY") and os.getenv("ALPACA_SECRET")):
        print("يحتاج ALPACA_KEY وALPACA_SECRET (يعمل على الخادم).")
        return 0
    pos = json.load(open(os.path.join(ROOT, "stocks_positions.json"), encoding="utf-8"))["positions"]
    df = run(pos, ob)
    df.to_csv(OUT_CSV, index=False, lineterminator="\n")
    lines = report(df)
    with open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


def report(df):
    L = ["# استعادة بأسعار حقيقية: عقد البوت مقابل عقود بقواعد المحترفين (الشركات، نتائج آلية)", "",
         "- شموع Alpaca الحقيقية (5د) بعد التوصية؛ الدخول بافتتاح أول شمعة بعد التوصية + نصف فرق 1.5% وعمولة. لا تغيّر أي توصية.",
         "- bot = عقد البوت نفسه (ضابط). atm = دلتا ≈0.50، itm = دلتا ≈0.70 بانتهاء شهري 28–50 يوماً. r1 = قاعدة البوت، r2 = وقف سهم 1.5% + وقف زمني (7 أيام).",
         "- ⚠️ عينة صغيرة جداً وشموع متباعدة للعقود غير النشطة؛ للمراقبة لا للحكم.", ""]
    if df.empty:
        return L + ["لا نتائج بعد."]
    names = {"bot": "عقد البوت", "atm": "على السعر (0.50)", "itm": "عميق (0.70)"}
    L += ["| العقد | الخروج | صفقات | متوسط | وسيط | رابحة | منتهية بقاعدة | مفتوحة (آخر شمعة) | متوسط المنتهية فقط (متحيز للربح) |", "|---|---|---|---|---|---|---|---|---|"]
    for v in ("bot", "atm", "itm"):
        for r in ("r1", "r2"):
            g = df[(df["variant"] == v) & (df["rule"] == r)]
            x = g["pnl"]
            if len(x):
                fin = g[g["why"] != "end"]["pnl"]
                L.append(f"| {names[v]} | {r} | {len(x)} | {x.mean() * 100:+.1f}% | {x.median() * 100:+.1f}% | {int((x > 0).sum())}/{len(x)} | "
                         f"{len(fin)} | {len(x) - len(fin)} | {'-' if fin.empty else f'{fin.mean() * 100:+.1f}%'} |")
    L.append("")
    L.append("- **تنبيه مقارنة:** العقود الأطول لم تنتهِ بعد، فكثير من نتائجها تقييم بآخر سعر صفقة (يُحسب بعد خصم نصف الفرق) وليس خروجاً فعلياً؛ أما عقد البوت القصير فمعظمه أُغلق بهدف أو وقف. وعمود «المنتهية فقط» متحيز للربح (الصفقات التي لم تبلغ هدفاً ولا وقفاً تبقى مفتوحة وتُستبعد منه)، فالمقارنة العادلة هي «متوسط» كل الصفقات مع تحفّظ التقييم بآخر سعر. الحكم الحقيقي بعد انتهاء العقود وفي الظل الحي.")
    ctl = df[(df["variant"] == "bot") & (df["rule"] == "r1") & df["recorded"].notna()]
    if len(ctl):
        same = int(((ctl["pnl"] - pd.to_numeric(ctl["recorded"])).abs() < 0.05).sum())
        L += ["", f"- ضابط: على {len(ctl)} توصية مغلقة فعلاً، متوسط المسجّل {pd.to_numeric(ctl['recorded']).mean() * 100:+.1f}% مقابل الاستعادة {ctl['pnl'].mean() * 100:+.1f}%؛ "
                  f"النتيجة نفسها في {same} من {len(ctl)} (الباقي اختلف فيه توقيت الوقف/الهدف: الاستعادة تقرأ إغلاق شموع 5د وسعر آخر صفقة، والبوت يقرأ سعر البيع كل 5 دقائق)."]
    piv = df.pivot_table(index="id", columns=["variant", "rule"], values="pnl")
    for v in ("atm", "itm"):
        if ("bot", "r1") in piv.columns and (v, "r1") in piv.columns:
            d = (piv[(v, "r1")] - piv[("bot", "r1")]).dropna()
            if len(d):
                L.append(f"- {names[v]} − عقد البوت (r1، {len(d)} توصية): متوسط {d.mean() * 100:+.1f} نقطة مئوية، وأفضل في {int((d > 0).sum())} من {len(d)}.")
    return L


if __name__ == "__main__":
    sys.exit(main())
