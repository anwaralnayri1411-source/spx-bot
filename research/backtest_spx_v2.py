"""اختبار تاريخي v2 لبوت SPX: يحاكي توصيات البوت الحي نفسه، لا سيناريوهات الخطة (انظر backtest_spx.py للقديم).

الفرق عن v1 (مراجعة 18 أكتوبر 2026):
1. قرار الدخول من دالة البوت نفسها `options_bot.direction_points` (عتبة MIN_EDGE)، مع أيام FOMC، وحد الوقفين اليومي،
   ومنع تكرار نفس الاتجاه خلال REPEAT_MINUTES، ولا دخول بعد no_entry. واختيار العقد بنفس شروط البوت (سعر 100-350$،
   خارج المال ضمن 2.5%، z ≤ 2، دلتا ≥ 0.03، درجة ≥ 5) على أسعار نموذجية.
2. التوقيت كما في الواقع: فحص كل 30 دقيقة (:05 و:35 + تأخر GitHub)، والبيانات متأخرة DATA_DELAY دقيقة.
   المتابعة كل 5 دقائق على بيانات متأخرة، والوقف لا يُفعَّل قبل 15 دقيقة، والخروج بقيمة العقد لحظة التنبيه (لا عند -40% بالضبط).
3. فاصل الثقة بإعادة المعاينة حسب الأيام (الصفقات في اليوم نفسه مترابطة).
4. حساسية التسعير: تذبذب ×0.8 و×1.2، وانحراف السترايكات مع تفاعل التذبذب مع الهبوط.
5. مرجع عادل: نفس أوقات توصيات البوت لكن بالاتجاه المعاكس (يقيس قيمة الاتجاه وحده).
6. فصل بيانات الاستكشاف عن بيانات الاختبار المحجوزة (HOLDOUT_FROM) مع فرضية مسجّلة مسبقاً.

ما لا يمكن إعادة بنائه تاريخياً (يُحذف من نقاط الاتجاه، فالنتيجة تقريبية): جدران العقود وجاما الصنّاع، نقطة الألم،
حجم الكول/البوت، صدمة الأخبار. أسعار العقود نظرية (بلاك-شولز). لا يكتب إلا ملف النتائج، ولا يغيّر البوت ولا الكاش.
تشغيل: python research/backtest_spx_v2.py
"""
import math
import os
import sys
import warnings
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import options_bot as ob  # noqa: E402  (قراءة فقط: دوال القرار والإعدادات)

OUT = os.path.join(ROOT, "research", "backtest_spx_v2_results.md")
CACHE = os.path.join(ROOT, "research", "spx_5m_cache.csv")   # يُقرأ فقط
HOLDOUT_FROM = date(2026, 10, 13)      # بعد آخر يوم في اختبار v1: بيانات لم تُستخدم في أي قرار سابق
DATA_DELAY = 15        # تأخر بيانات ياهو المجانية (دقيقة)
RUN_LAG = 5            # تأخر تشغيل GitHub Actions عن موعد cron (دقيقة، تقريبي)
SCAN_MINUTES = (5, 35)  # cron "5,35 13-21"
OPEN_M, CLOSE_M = 9 * 60 + 30, 16 * 60
FINAL_M = 15 * 60 + 30  # تنبيه «آخر 30 دقيقة»: نفترض البيع عنده
HAIRCUT = 0.05          # نصف فرق الشراء/البيع التقريبي (+ عند الشراء، - عند البيع)
# المقاس فعلياً (Alpaca 19–22/10، research/backtest_audit.md): 1.5% لعقود SPXW بسعر 1–3.5$. HAIRCUT يبقى 5% (سيناريو متشائم)
# لأن research/preopen_shadow.py الحي يقرؤه وتغييره يقسم سجله؛ الدراسات الجديدة تستعمل HAIRCUT_REAL.
HAIRCUT_REAL = 0.015
COMMISSION = 0.0065     # 0.65$ للعقد في كل اتجاه (بوحدة سعر العقد)
LIQ_ASSUMED = 2.5       # جزء السيولة من درجة العقد (غير متاح تاريخياً): افتراض عقد SPX سائل
SKEW_PTS = 1.5          # نقاط تذبذب لكل 1% تحت السعر (انحراف السترايكات)
SPOTVOL = 2.0           # كل هبوط 1% منذ الافتتاح يرفع التذبذب نقطتين
BOOT = 2000
FOMC_HIST = {"2026-07-29": "قرار الفيدرالي (FOMC)", "2026-09-16": "قرار الفيدرالي (FOMC)"}
VARIANTS = {
    "base": ("أساسي: VIX أمس، بلا انحراف", 1.0, False),
    "iv08": ("تذبذب ×0.8", 0.8, False),
    "iv12": ("تذبذب ×1.2", 1.2, False),
    "skew": ("انحراف السترايكات + ارتفاع التذبذب مع الهبوط", 1.0, True),
}
HEADLINE = "base"


# ============================ التسعير ============================
def _cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def model_iv(S, K, iv0, open_px, variant):
    _, scale, skew = VARIANTS[variant]
    iv = iv0 * scale
    if skew:
        iv += SKEW_PTS / 100 * ((S - K) / S * 100)       # سترايك أدنى = تذبذب أعلى
        iv += SPOTVOL / 100 * (-(S / open_px - 1) * 100)  # الهبوط يرفع التذبذب
    return max(iv, 0.05)


def price(S, K, minute, side, iv0, open_px, variant):
    """قيمة العقد عند دقيقة اليوم `minute` (بزمن التداول كما في v1)."""
    left = max(CLOSE_M - minute, 1.0)
    iv = model_iv(S, K, iv0, open_px, variant)
    sd = iv * math.sqrt(left / (252 * 390))
    d1 = (math.log(S / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    if side == "CALL":
        return S * _cdf(d1) - K * _cdf(d2)
    return K * _cdf(-d2) - S * _cdf(-d1)


def pick_contract(side, S, minute, iv0, open_px, variant):
    """نفس شروط ob.pick_contracts على أسعار نموذجية: يرجع (السترايك، سعر الطلب) أو None."""
    T = max((CLOSE_M - minute) / 60, 0.25) / (24 * 365)   # زمن البوت الحي (تقويمي) لحساب الدلتا وz
    sg = 1 if side == "CALL" else -1
    best = None
    k0 = math.floor(S / 5) * 5 + (5 if side == "CALL" else 0)
    for i in range(0, 60):
        K = float(k0 + sg * 5 * i)
        if (side == "CALL" and K > S * 1.025) or (side == "PUT" and K < S * 0.975):
            break
        if (side == "CALL" and K <= S) or (side == "PUT" and K >= S):
            continue
        ask = price(S, K, minute, side, iv0, open_px, variant) * (1 + HAIRCUT)
        if not (ob.CONTRACT_MIN_USD <= ask * 100 <= ob.CONTRACT_MAX_USD):
            continue
        # التذبذب الذي يقرؤه البوت من ياهو مشتق من سعر السوق بزمن تقويمي؛ نحوّل تذبذب النموذج (بزمن التداول) إليه
        # حتى تكون دلتا وz متسقتين مع السعر كما في الواقع.
        T_tt = max(CLOSE_M - minute, 1.0) / (252 * 390)
        iv = model_iv(S, K, iv0, open_px, variant) * math.sqrt(T_tt / T)
        be = K + ask if side == "CALL" else K - ask
        delta = ob.abs_delta(S, K, T, iv, side)
        z = abs(be - S) / (S * iv * math.sqrt(T))
        if z > 2.0 or delta < 0.03:
            continue
        dp = 3 if delta >= 0.20 else (2 if delta >= 0.12 else (1 if delta >= 0.07 else 0.5))
        rp = 4 if z <= 0.6 else (3 if z <= 1.0 else (2 if z <= 1.5 else 1))
        score = min(10.0, LIQ_ASSUMED + dp + rp)
        if score < ob.MIN_CONTRACT_SCORE:
            continue
        if best is None or score > best[0]:
            best = (score, K, ask)
    return None if best is None else (best[1], best[2])


# ============================ البيانات ============================
def _ny(df):
    idx = df.index
    idx = idx.tz_convert(ob.NY) if idx.tz is not None else idx.tz_localize("UTC").tz_convert(ob.NY)
    df = df.copy()
    df.index = idx
    return df


def load():
    import yfinance as yf
    spx = _ny(yf.Ticker("^GSPC").history(period="60d", interval="5m", auto_adjust=False))[["Open", "High", "Low", "Close"]]
    if os.path.exists(CACHE):
        old = pd.read_csv(CACHE, index_col=0)
        old.index = pd.to_datetime(old.index, utc=True).tz_convert(ob.NY)
        spx = pd.concat([old, spx])
    spx = spx[~spx.index.duplicated(keep="last")].sort_index()
    m = spx.index.hour * 60 + spx.index.minute
    spx = spx[(m >= OPEN_M) & (m < CLOSE_M)]

    def intraday(sym):
        try:
            return _ny(yf.Ticker(sym).history(period="60d", interval="5m", auto_adjust=False))
        except Exception as e:
            print("تعذر", sym, e)
            return pd.DataFrame()

    data = {"spx": spx, "spy": intraday("SPY"), "vix5": intraday("^VIX"), "es5": intraday("ES=F"),
            "leaders": {t: intraday(t)["Close"].dropna() for t in __import__("leaders").WEIGHTS}}
    daily = yf.Ticker("^GSPC").history(period="2y", auto_adjust=False)["Close"].dropna()
    daily.index = pd.to_datetime(daily.index.date)
    vixd = yf.Ticker("^VIX").history(period="2y", auto_adjust=False)["Close"].dropna()
    vixd.index = pd.to_datetime(vixd.index.date)
    esd = yf.Ticker("ES=F").history(period="2y", auto_adjust=False)["Close"].dropna()
    esd.index = pd.to_datetime(esd.index.date)
    data.update(daily=daily, vixd=vixd, esd=esd)
    return data


def _upto(df, d, cutoff):
    """شموع اليوم d التي انتهت قبل الدقيقة cutoff (شمعة 5 دقائق تنتهي عند البداية + 5)."""
    if df is None or len(df) == 0:
        return df
    day = df[df.index.date == d]
    m = day.index.hour * 60 + day.index.minute
    return day[(m + 5) <= cutoff]


# ============================ إعادة بناء c كما يبنيه analyze ============================
def build_c(data, d, prev_d, cutoff, run_m):
    spx = data["spx"]
    hist = spx[spx.index.date < d].iloc[-5 * 78:]
    today = _upto(spx, d, cutoff)
    if len(today) < 2:
        return None
    closes = pd.concat([hist["Close"], today["Close"]])
    S = float(today["Close"].iloc[-1])
    daily = data["daily"][data["daily"].index < pd.Timestamp(d)]
    dser = pd.concat([daily, pd.Series([S])])
    prev = spx[spx.index.date == prev_d]
    n = datetime(d.year, d.month, d.day, run_m // 60, run_m % 60, tzinfo=ob.NY)
    c = {"n": n, "S": S, "age": float(DATA_DELAY),
         "sma20": float(dser.rolling(20).mean().iloc[-1]), "sma50": float(dser.rolling(50).mean().iloc[-1]),
         "ema9": float(closes.ewm(span=9, adjust=False).mean().iloc[-1]),
         "ema21": float(closes.ewm(span=21, adjust=False).mean().iloc[-1]),
         "r30": float(today["Close"].iloc[-1] / today["Close"].iloc[-7] - 1) if len(today) >= 7 else 0.0,
         "gap": float(today["Close"].iloc[0] / prev["Close"].iloc[-1] - 1) if len(prev) else None,
         "prev_close": float(prev["Close"].iloc[-1]) if len(prev) else None,
         "map": None, "max_pain": None, "pc": None, "es": None, "leaders": None, "vix_day": None,
         "orb": None, "pd_hl": None}
    spy = _upto(data["spy"], d, cutoff)
    if spy is None or len(spy) == 0 or spy["Volume"].sum() <= 0:
        return None                                   # بلا VWAP يختل الاتجاه اللحظي: نتجاهل الفحص بدل افتراض «فوق»
    tp = (spy["High"] + spy["Low"] + spy["Close"]) / 3
    vwap = float((tp * spy["Volume"]).cumsum().iloc[-1] / spy["Volume"].cumsum().iloc[-1])
    c["above_vwap"] = bool(spy["Close"].iloc[-1] > vwap)
    vx = _upto(data["vix5"], d, cutoff)
    if vx is not None and len(vx) >= 3:
        c["vix_day"] = float(vx["Close"].iloc[-1] / vx["Close"].iloc[0] - 1)
    if len(today) >= 7:
        c["orb"] = (float(today["High"].iloc[:6].max()), float(today["Low"].iloc[:6].min()))
    if len(prev):
        c["pd_hl"] = (float(prev["High"].max()), float(prev["Low"].min()))
    try:
        es = _upto(data["es5"], d, cutoff)
        esd = data["esd"][data["esd"].index < pd.Timestamp(d)]
        if len(es) and len(esd):
            c["es"] = float(es["Close"].iloc[-1] / esd.iloc[-1] - 1)
    except Exception:
        pass
    try:
        day = m30 = 0.0
        up = k = 0
        for t, w in __import__("leaders").WEIGHTS.items():
            cl = data["leaders"].get(t)
            if cl is None or not len(cl):
                continue
            mm = cl.index.hour * 60 + cl.index.minute
            cl = cl[(cl.index.date < d) | ((cl.index.date == d) & ((mm + 5) <= cutoff))]
            p0s = cl[cl.index.date < d]
            if not len(p0s) or len(cl) < 8 or cl.index[-1].date() != d:
                continue
            p0, p1, p30 = float(p0s.iloc[-1]), float(cl.iloc[-1]), float(cl.iloc[-7])
            day += w * (p1 / p0 - 1)
            m30 += w * (p1 / p30 - 1)
            up += 1 if p1 > p0 else 0
            k += 1
        if k >= 6:
            c["leaders"] = {"day": day, "m30": m30, "up": up, "n": k, "top": []}
    except Exception:
        pass
    return c


# ============================ المحاكاة ============================
def day_bars(spx, d):
    g = spx[spx.index.date == d]
    m = (g.index.hour * 60 + g.index.minute).values
    return g, m


def s_at(g, m, minute, delayed):
    """سعر SPX عند الدقيقة: الحالي = افتتاح الشمعة التي تبدأ عندها، والمتأخر = إغلاق آخر شمعة انتهت قبل (الدقيقة - التأخر)."""
    if delayed:
        idx = np.searchsorted(m + 5, minute - DATA_DELAY, side="right") - 1
        return None if idx < 0 else float(g["Close"].values[idx])
    idx = np.searchsorted(m, minute, side="right") - 1
    if idx < 0:
        return None
    return float(g["Open"].values[idx]) if m[idx] == minute else float(g["Close"].values[idx])


def follow(g, m, side, K, run_m, rec_entry, real_entry, iv0, open_px, variant):
    """متابعة كل 5 دقائق كما في spx_monitor: التنبيه من بيانات متأخرة، والخروج بقيمة العقد الحالية لحظة التنبيه."""
    tau = run_m + 5
    while tau <= CLOSE_M - 5:
        S_obs = s_at(g, m, tau, True)
        S_now = s_at(g, m, tau, False)
        if S_obs is None or S_now is None:
            tau += 5
            continue
        bot_val = price(S_obs, K, tau - DATA_DELAY, side, iv0, open_px, variant) * (1 - HAIRCUT)
        pnl_bot = bot_val / rec_entry - 1
        kind = None
        if tau - run_m >= ob.MIN_AGE_MIN and pnl_bot <= -ob.STOP_LOSS:
            kind = "stop"
        elif pnl_bot >= ob.TAKE_PROFIT:
            kind = "tp"
        elif tau >= FINAL_M:
            kind = "final"
        if kind:
            out = price(S_now, K, tau, side, iv0, open_px, variant) * (1 - HAIRCUT) - COMMISSION
            return kind, tau, out / real_entry - 1
        tau += 5
    S_now = s_at(g, m, CLOSE_M - 5, False)
    out = price(S_now, K, CLOSE_M - 5, side, iv0, open_px, variant) * (1 - HAIRCUT) - COMMISSION
    return "final", CLOSE_M - 5, out / real_entry - 1


def events_block(d, run_m):
    evs = [nm for dd, nm in ob.upcoming_events(d) if dd == d]
    if d.isoformat() in FOMC_HIST:
        evs.append(FOMC_HIST[d.isoformat()])
    for nm in evs:
        if nm.startswith("تقرير"):
            if run_m < 10 * 60:
                return nm
        else:
            return nm
    return None


def simulate(data, variant):
    spx = data["spx"]
    dates = sorted(set(spx.index.date))
    trades, diag = [], {"scans": 0, "no_data": 0, "weak": 0, "event": 0, "repeat": 0, "daily_stop": 0,
                        "no_contract": 0, "signals": 0}
    for di, d in enumerate(dates):
        g, m = day_bars(spx, d)
        if len(g) < 70 or di == 0:
            continue                                       # أيام ناقصة/مبكرة الإغلاق تُتجاهل؛ «أمس» = آخر يوم فيه شموع
        prev_d = dates[di - 1]
        vprev = data["vixd"][data["vixd"].index < pd.Timestamp(d)]
        iv0 = float(vprev.iloc[-1]) / 100 if len(vprev) else 0.16
        open_px = float(g["Open"].values[0])
        last_side, last_t, day_tr = None, None, []
        for hh in range(9, 16):
            for mm_ in SCAN_MINUTES:
                run_m = hh * 60 + mm_ + RUN_LAG
                if not ((9 * 60 + 45) <= run_m < 15 * 60):      # in_window و no_entry
                    continue
                diag["scans"] += 1
                cutoff = run_m - DATA_DELAY
                c = build_c(data, d, prev_d, cutoff, run_m)
                if c is None:
                    diag["no_data"] += 1
                    continue
                bull, bear, _ = ob.direction_points(c)
                edge = abs(bull - bear)
                side = "CALL" if bull > bear else "PUT"
                if events_block(d, run_m):
                    diag["event"] += 1
                    continue
                if edge < ob.MIN_EDGE:
                    diag["weak"] += 1
                    continue
                stops = sum(1 for t in day_tr if t["out"] == "stop" and t["exit_m"] <= run_m)
                if stops >= ob.MAX_DAILY_STOPS:
                    diag["daily_stop"] += 1
                    continue
                if last_side == side and last_t is not None and run_m - last_t < ob.REPEAT_MINUTES:
                    diag["repeat"] += 1
                    continue
                for s_, role in ((side, "bot"), ("PUT" if side == "CALL" else "CALL", "opp")):
                    pk = pick_contract(s_, c["S"], cutoff, iv0, open_px, variant)
                    if pk is None:
                        if role == "bot":
                            diag["no_contract"] += 1
                            break
                        continue
                    K, rec_entry = pk
                    S_now = s_at(g, m, run_m, False)
                    real_entry = price(S_now, K, run_m, s_, iv0, open_px, variant) * (1 + HAIRCUT) + COMMISSION
                    out, exit_m, pnl = follow(g, m, s_, K, run_m, rec_entry, real_entry, iv0, open_px, variant)
                    t = {"day": d, "role": role, "side": s_, "edge": edge, "run_m": run_m, "K": K,
                         "entry": real_entry, "out": out, "exit_m": exit_m, "pnl": pnl}
                    trades.append(t)
                    if role == "bot":
                        day_tr.append(t)
                        diag["signals"] += 1
                        last_side, last_t = side, run_m
                else:
                    continue
    return pd.DataFrame(trades), diag


# ============================ الإحصاء والتقرير ============================
def boot_mean(df, col="pnl", rng=None):
    """فاصل 95% لمتوسط الصفقة بإعادة معاينة الأيام (لا الصفقات)."""
    if len(df) == 0:
        return (float("nan"), float("nan"))
    rng = rng or np.random.default_rng(7)
    groups = [g[col].values for _, g in df.groupby("day")]
    k = len(groups)
    means = []
    for _ in range(BOOT):
        pick = rng.integers(0, k, k)
        v = np.concatenate([groups[i] for i in pick])
        means.append(v.mean())
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def paired_diff(bot, opp):
    """فرق (البوت − المعاكس) لكل يوم ثم فاصل بإعادة معاينة الأيام."""
    j = bot.groupby("day")["pnl"].mean().to_frame("b").join(opp.groupby("day")["pnl"].mean().to_frame("o"), how="inner")
    if len(j) == 0:
        return None
    d = (j["b"] - j["o"]).values
    rng = np.random.default_rng(11)
    bs_ = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(BOOT)]
    return float(d.mean()), float(np.percentile(bs_, 2.5)), float(np.percentile(bs_, 97.5)), len(d)


def row(label, df):
    if len(df) == 0:
        return f"| {label} | 0 | 0 | - | - | - | - | - |"
    lo, hi = boot_mean(df)
    usd = (df["pnl"] * df["entry"] * 100).mean()
    return (f"| {label} | {len(df)} | {df['day'].nunique()} | {100 * (df['out'] == 'tp').mean():.0f}% / "
            f"{100 * (df['out'] == 'stop').mean():.0f}% / {100 * (df['out'] == 'final').mean():.0f}% | "
            f"{100 * df['pnl'].mean():+.1f}% | ≈{usd:+.0f}$ | {100 * lo:+.0f}% … {100 * hi:+.0f}% | "
            f"{100 * (df['pnl'] > 0).mean():.0f}% |")


def section(title, tr):
    L = [f"### {title}", "", "| المجموعة | صفقات | أيام | هدف / وقف / نهاية اليوم | متوسط الصفقة | بالدولار للعقد | فاصل 95% (حسب الأيام) | رابحة |",
         "|---|---|---|---|---|---|---|---|"]
    if len(tr) == 0:
        return L + ["| لا صفقات في هذه الفترة | 0 | 0 | - | - | - | - | - |", ""]
    bot, opp = tr[tr["role"] == "bot"], tr[tr["role"] == "opp"]
    L.append(row("**توصيات البوت**", bot))
    for s_ in ("CALL", "PUT"):
        L.append(row(f"  منها {s_}", bot[bot["side"] == s_]))
    L.append(row("مرجع: نفس الأوقات بالاتجاه المعاكس", opp))
    pdf = paired_diff(bot, opp)
    if pdf:
        mean, lo, hi, nd = pdf
        L += ["", f"**قيمة الاتجاه** (البوت − المعاكس، متوسط يومي على {nd} يوماً): {100 * mean:+.1f}% "
                  f"(فاصل 95%: {100 * lo:+.1f}% … {100 * hi:+.1f}%)" +
              (" ← أفضل من المعاكس بثقة." if lo > 0 else (" ← أسوأ من المعاكس بثقة." if hi < 0 else " ← لا يمكن تمييزه عن المعاكس."))]
    return L + [""]


def delay_section(delays):
    """حساسية التأخير (التسعير الأساسي، كل الفترة، استكشافي): كم يكلّف تأخر البيانات؟"""
    L = ["## حساسية تأخر البيانات (استكشافي، كل الفترة، التسعير الأساسي)", "",
         "| تأخر البيانات | توصيات | متوسط الصفقة | هدف / وقف | قيمة الاتجاه (البوت − المعاكس) |", "|---|---|---|---|---|"]
    for dl, tr in delays:
        bot, opp = tr[tr["role"] == "bot"], tr[tr["role"] == "opp"]
        pdf = paired_diff(bot, opp)
        dv = "-" if not pdf else f"{100 * pdf[0]:+.1f}% ({100 * pdf[1]:+.1f}% … {100 * pdf[2]:+.1f}%)"
        L.append(f"| {dl} دقيقة | {len(bot)} | {100 * bot['pnl'].mean():+.1f}% | "
                 f"{100 * (bot['out'] == 'tp').mean():.0f}% / {100 * (bot['out'] == 'stop').mean():.0f}% | {dv} |")
    base = dict(delays).get(DATA_DELAY)
    if base is not None and len(base):
        b = base[base["role"] == "bot"]
        L += ["", f"- تنبيهات «وقف» بينما صفقتك الفعلية رابحة: {int(((b['out'] == 'stop') & (b['pnl'] > 0)).sum())} من "
                  f"{int((b['out'] == 'stop').sum())}؛ وتنبيهات «هدف» بينما صفقتك خاسرة: "
                  f"{int(((b['out'] == 'tp') & (b['pnl'] < 0)).sum())} من {int((b['out'] == 'tp').sum())}. "
                  "السبب: البوت يسجّل سعر الدخول ويحسب الربح من بيانات متأخرة، وأنت تدخل وتخرج بالسعر الحالي.",
              f"- متوسط سعر العقد عند الدخول ≈ {b['entry'].mean() * 100:.0f}$، ومتوسط مدة الصفقة "
              f"{(b['exit_m'] - b['run_m']).median():.0f} دقيقة."]
    L += ["- هذا القسم استكشافي (عدة مقارنات)، فلا يُبنى عليه قرار قبل تأكيده على البيانات المحجوزة.", ""]
    return L


def report(data, results, delays=None):
    spx = data["spx"]
    days = sorted(set(spx.index.date))
    base_tr, diag = results[HEADLINE]
    L = ["# اختبار تاريخي v2: توصيات بوت SPX كما تعمل فعلياً (نتائج آلية)", "",
         f"- الفترة المتاحة: {days[0]} → {days[-1]}. البيانات المحجوزة للاختبار: من {HOLDOUT_FROM} فصاعداً.",
         f"- التوقيت: فحص عند :05 و:35 + {RUN_LAG} دقائق تأخر تشغيل، وبيانات متأخرة {DATA_DELAY} دقيقة. "
         f"المتابعة كل 5 دقائق، والوقف بعد {ob.MIN_AGE_MIN} دقيقة على الأقل، والخروج بقيمة العقد لحظة التنبيه، "
         "وبيع إلزامي عند 15:30.",
         f"- التكاليف: ±{HAIRCUT * 100:.0f}% فرق سعر عند الشراء والبيع + عمولة 0.65$ للعقد في كل اتجاه.",
         "- نقاط الاتجاه من دالة البوت نفسها، لكن بدون: جدران العقود وجاما الصنّاع، نقطة الألم، حجم الكول/البوت، صدمة الأخبار "
         "(غير متاحة تاريخياً). أسعار العقود نظرية.",
         "- فاصل الثقة بإعادة معاينة **الأيام** (الصفقات في اليوم نفسه مترابطة).", "",
         "## الفرضية المسجّلة مسبقاً (تُحكم على البيانات المحجوزة فقط)",
         "",
         "> توصيات البوت (التسعير الأساسي) متوسطها أعلى من صفقات نفس الأوقات بالاتجاه المعاكس، "
         "والحد الأدنى لفاصل الثقة فوق الصفر. لا تُعدَّل أي قاعدة في البوت بناءً على قسم الاستكشاف وحده.", "",
         "## تشخيص الفحوص (التسعير الأساسي، كل الفترة)", "",
         f"فحوص: {diag['scans']} | بلا بيانات كافية: {diag['no_data']} | اتجاه ضعيف (< {ob.MIN_EDGE:g}): {diag['weak']} | "
         f"يوم حدث كبير: {diag['event']} | تكرار نفس الاتجاه: {diag['repeat']} | حد الوقفين: {diag['daily_stop']} | "
         f"لا عقد مناسب: {diag['no_contract']} | **توصيات: {diag['signals']}**", ""]
    for part, holdout in (("استكشاف (قبل البيانات المحجوزة)", False), ("البيانات المحجوزة (الحكم)", True)):
        L += [f"## {part}", ""]
        for key, (label, _, _) in VARIANTS.items():
            tr, _ = results[key]
            if len(tr):
                mask = (tr["day"] >= HOLDOUT_FROM) if holdout else (tr["day"] < HOLDOUT_FROM)
                tr = tr[mask]
            L += section(label + (" ← الأساسي" if key == HEADLINE else ""), tr)
    if delays:
        L += delay_section(delays)
    L += ["## كيف تقرأ النتيجة", "",
          "- **قيمة الاتجاه** هي السؤال الأهم: هل اختيار البوت للاتجاه أفضل من عكسه في نفس اللحظة؟ إن لم يكن، فالربح أو الخسارة "
          "سببهما التوقيت والتكاليف لا الاتجاه.",
          "- إذا تغيّر الحكم بين صفوف التسعير (×0.8، ×1.2، الانحراف)، فالنموذج النظري لا يكفي للحكم ونحتاج أسعار عقود حقيقية.",
          "- البيانات المحجوزة قليلة الآن وتكبر مع الوقت: أعد التشغيل أسبوعياً، ولا تحكم قبل 20 يوماً محجوزاً على الأقل.",
          "- العينة الفعلية = عدد الأيام لا عدد الصفقات."]
    return "\n".join(L)


def main():
    data = load()
    global DATA_DELAY
    results = {k: simulate(data, k) for k in VARIANTS}
    delays, keep = [], DATA_DELAY
    for dl in (keep, 5, 0):
        DATA_DELAY = dl
        delays.append((dl, results[HEADLINE][0] if dl == keep else simulate(data, HEADLINE)[0]))
    DATA_DELAY = keep
    txt = report(data, results, delays)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(txt)
    print(txt)


if __name__ == "__main__":
    main()
