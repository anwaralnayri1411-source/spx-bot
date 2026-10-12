"""اختبار تاريخي لسيناريوهات «خطة اليوم» لـ SPX (مشروع SPX فقط، لا علاقة له بالشركات).

ماذا يختبر: السيناريوهات الأربعة (اختراق المقاومة، رفض عندها، ارتداد من الدعم، كسر الدعم)
على شموع 5 دقائق لمؤشر SPX، ثم يقيس نتيجة عقد 0DTE نظري بقاعدة البوت: جني +50% / وقف -40%.

حدود صريحة (مهمة):
- شموع 5 دقائق المجانية متاحة لآخر ~60 يوماً فقط، فالعينة صغيرة. السكربت يحفظ الشموع في
  research/spx_5m_cache.csv ويضيف إليها كل تشغيل فتكبر العينة مع الوقت.
- أسعار العقود نظرية (بلاك-شولز، تذبذب = VIX إغلاق أمس، زمن التداول)، مع خصم 5% عند الشراء و5% عند البيع بدل فروق السعر الحقيقية.
- المستويات المستخدمة: أعلى/أدنى أمس، نطاق أول 30 دقيقة، أعلى/أدنى اليوم، الأرقام الدائرية (25). لا جدران عقود ولا VWAP ولا نقطة الألم (غير متاحة تاريخياً).
- إذا لمست الشمعة الوقف والجني معاً نفترض الوقف أولاً (تحفظ).
- مقارنة مرجعية: دخول عشوائي بنفس القاعدة، لنعرف هل السيناريوهات أفضل من العشوائية.
"""
import math
import os
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
NY = "America/New_York"
OUT = "research/backtest_spx_results.md"
CACHE = "research/spx_5m_cache.csv"

TAKE_PROFIT, STOP_LOSS = 0.50, 0.40
TARGET_PRICE = 2.0            # سعر العقد المستهدف عند الدخول (= 200$ للعقد الواحد)
HAIRCUT = 0.05                # فرق السعر عند الشراء (+) والبيع (-)
ENTRY_FROM, ENTRY_TO = 10 * 60, 15 * 60    # نافذة الدخول بالدقائق من منتصف الليل (بتوقيت نيويورك)
OPEN_M, CLOSE_M = 9 * 60 + 30, 16 * 60
DELAYS = [1, 4]               # تأخير التنفيذ بالشموع: 1 = افتتاح الشمعة التالية، 4 = بعد ~15 دقيقة (بيانات متأخرة)
HOLDS = [30, 60]              # أقصى مدة احتفاظ بالدقائق
HEADLINE = (1, 60)
SCEN = {
    "call_break": "CALL اختراق المقاومة",
    "put_reject": "PUT رفض عند المقاومة",
    "call_bounce": "CALL ارتداد من الدعم",
    "put_break": "PUT كسر الدعم",
}


# ---------------------------- تسعير نظري ----------------------------
def _cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs(S, K, minutes_left, iv, side):
    """سعر عقد بلاك-شولز (فائدة 0) بزمن التداول: دقائق متبقية / (252 يوماً × 390 دقيقة)."""
    T = max(minutes_left, 1.0) / (252 * 390)
    sd = iv * math.sqrt(T)
    if sd <= 0:
        return max(0.0, (S - K) if side == "CALL" else (K - S))
    d1 = (math.log(S / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    if side == "CALL":
        return S * _cdf(d1) - K * _cdf(d2)
    return K * _cdf(-d2) - S * _cdf(-d1)


def pick_strike(S, minutes_left, iv, side):
    """السترايك الذي سعر عقده (مع خصم الشراء) أقرب إلى TARGET_PRICE، بشرط أن يكون بين 1 و3 دولار."""
    base = round(S / 5) * 5
    sgn = 1 if side == "CALL" else -1
    best = None
    for step in range(0, 40):
        K = base + sgn * 5 * step
        p = bs(S, K, minutes_left, iv, side) * (1 + HAIRCUT)
        if 1.0 <= p <= 3.0 and (best is None or abs(p - TARGET_PRICE) < abs(best[1] - TARGET_PRICE)):
            best = (K, p)
    return best


# ---------------------------- البيانات ----------------------------
def load_bars():
    import yfinance as yf
    df = yf.Ticker("^GSPC").history(period="60d", interval="5m", auto_adjust=False)
    idx = df.index.tz_convert(NY) if df.index.tz is not None else df.index.tz_localize("UTC").tz_convert(NY)
    new = pd.DataFrame({"Open": df["Open"].values, "High": df["High"].values,
                        "Low": df["Low"].values, "Close": df["Close"].values}, index=idx)
    if os.path.exists(CACHE):
        old = pd.read_csv(CACHE, index_col=0)
        old.index = pd.to_datetime(old.index, utc=True).tz_convert(NY)
        new = pd.concat([old, new])
    new = new[~new.index.duplicated(keep="last")].sort_index()
    new.index.name = "time"
    new.to_csv(CACHE)
    return new


def load_vix():
    import yfinance as yf
    v = yf.Ticker("^VIX").history(period="2y", auto_adjust=False)["Close"]
    v.index = pd.to_datetime(v.index.tz_localize(None) if v.index.tz is not None else v.index).normalize()
    return v


def session_days(bars):
    """يقسم الشموع إلى أيام جلسة كاملة تقريباً (9:30-15:55)."""
    m = bars.index.hour * 60 + bars.index.minute
    bars = bars[(m >= OPEN_M) & (m < CLOSE_M)].copy()
    days = {}
    for d, g in bars.groupby(bars.index.date):
        if len(g) >= 70:           # يتجاوز الأيام الناقصة وأنصاف الأيام
            days[pd.Timestamp(d)] = g
    return days


# ---------------------------- المستويات والسيناريوهات ----------------------------
def levels_at(S, prev_hl, orb, day_hl):
    lv = []
    if prev_hl:
        lv += [prev_hl[0], prev_hl[1]]
    if orb:
        lv += [orb[0], orb[1]]
    if day_hl:
        lv += [day_hl[0], day_hl[1]]
    k25 = int(S // 25)
    lv += [float(k * 25) for k in (k25 - 1, k25, k25 + 1, k25 + 2)]
    lv = sorted(p for p in lv if abs(p - S) / S <= 0.011)
    merged = []
    for p in lv:
        if merged and abs(p - merged[-1]) / S <= 0.0012:
            merged[-1] = (merged[-1] + p) / 2
        else:
            merged.append(p)
    res = [p for p in merged if p > S * 1.0004]
    sup = [p for p in merged if p < S * 0.9996]
    return (res[0] if res else None), (sup[-1] if sup else None)


def simulate(days, vix, delay, hold, null_stride=None):
    """يمر على كل يوم ويرجع قائمة صفقات. null_stride: دخول مرجعي كل N شمعة (CALL وPUT) بدلاً من السيناريوهات."""
    keys = sorted(days)
    trades = []
    for di, d in enumerate(keys):
        g = days[d]
        o, h, l, c = (g[x].values for x in ("Open", "High", "Low", "Close"))
        mins = (g.index.hour * 60 + g.index.minute).values
        prev_hl = (days[keys[di - 1]]["High"].max(), days[keys[di - 1]]["Low"].min()) if di > 0 else None
        vprev = vix[vix.index < d]
        iv = float(vprev.iloc[-1]) / 100 if len(vprev) else 0.16
        done = set()
        n = len(g)
        for i in range(6, n - delay - 2):
            if not (ENTRY_FROM <= mins[i] <= ENTRY_TO):
                continue
            S0 = c[i - 1]
            orb = (h[:6].max(), l[:6].min())
            day_hl = (h[:i].max(), l[:i].min())
            if null_stride:
                if (i - 6) % null_stride:
                    continue
                fired = [("null_call", "CALL"), ("null_put", "PUT")]
            else:
                R1, S1 = levels_at(S0, prev_hl, orb, day_hl)
                fired = []
                if R1 and c[i] > R1:
                    fired.append(("call_break", "CALL"))
                if R1 and h[i] >= R1 and c[i] < R1 and c[i] < o[i]:
                    fired.append(("put_reject", "PUT"))
                if S1 and l[i] <= S1 and c[i] > S1 and c[i] > o[i]:
                    fired.append(("call_bounce", "CALL"))
                if S1 and c[i] < S1:
                    fired.append(("put_break", "PUT"))
            for name, side in fired:
                if name in done and not null_stride:
                    continue
                j = i + delay
                left = CLOSE_M - mins[j]
                pk = pick_strike(o[j], left, iv, side)
                if pk is None:
                    continue
                done.add(name)
                K, entry = pk
                stop_v, tp_v = entry * (1 - STOP_LOSS), entry * (1 + TAKE_PROFIT)
                last = min(n - 1, j + hold // 5 - 1)
                out, exit_v = "time", None
                for k in range(j, last + 1):
                    left_end = CLOSE_M - (mins[k] + 5)
                    adv = l[k] if side == "CALL" else h[k]
                    fav = h[k] if side == "CALL" else l[k]
                    if bs(adv, K, left_end, iv, side) * (1 - HAIRCUT) <= stop_v:
                        out, exit_v = "stop", stop_v
                        break
                    if bs(fav, K, left_end, iv, side) * (1 - HAIRCUT) >= tp_v:
                        out, exit_v = "tp", tp_v
                        break
                if exit_v is None:
                    exit_v = bs(c[last], K, CLOSE_M - (mins[last] + 5), iv, side) * (1 - HAIRCUT)
                trades.append({"day": d.date(), "scen": name, "side": side, "entry": entry,
                               "out": out, "pnl": exit_v / entry - 1})
    return pd.DataFrame(trades)


# ---------------------------- التقرير ----------------------------
def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, mid - half), min(1.0, mid + half))


def summarize(df):
    n = len(df)
    if n == 0:
        return None
    wins = int((df["pnl"] > 0).sum())
    lo, hi = wilson(wins, n)
    return {"n": n, "tp": 100 * (df["out"] == "tp").mean(), "sl": 100 * (df["out"] == "stop").mean(),
            "tm": 100 * (df["out"] == "time").mean(), "avg": 100 * df["pnl"].mean(),
            "usd": df["pnl"].mean() * df["entry"].mean() * 100, "win": 100 * wins / n,
            "wlo": 100 * lo, "whi": 100 * hi}


def fmt_row(label, s):
    if not s:
        return f"| {label} | 0 | - | - | - | - | - |"
    return (f"| {label} | {s['n']} | {s['tp']:.0f}% | {s['sl']:.0f}% | {s['tm']:.0f}% | "
            f"{s['avg']:+.1f}% (≈{s['usd']:+.0f}$) | {s['win']:.0f}% ({s['wlo']:.0f}–{s['whi']:.0f}) |")


def report(days, vix):
    keys = sorted(days)
    L = ["# اختبار تاريخي لخطة SPX (نتائج آلية)", "",
         f"- الفترة: {keys[0].date()} → {keys[-1].date()} | أيام جلسة كاملة: {len(keys)}",
         "- ⚠️ عينة صغيرة (≈ شهرين من شموع 5 دقائق المجانية) وأسعار عقود نظرية. النتيجة ليست إثباتاً لربحية ولا لخسارة.",
         "- قاعدة الخروج: جني +50% / وقف −40% / خروج زمني. خصم 5% عند الشراء و5% عند البيع. الوقف أولاً إذا تزامنا في شمعة.",
         "- المستويات: أعلى/أدنى أمس، نطاق أول 30د، أعلى/أدنى اليوم، أرقام 25 الدائرية (بدون جدران العقود/VWAP).",
         "- أول إشارة فقط لكل سيناريو في اليوم. الدخول بين 10:00 و15:00 نيويورك.", ""]
    for delay, hold in [(d, h) for d in DELAYS for h in HOLDS]:
        tag = " ← الأساسي" if (delay, hold) == HEADLINE else ""
        wait = "افتتاح الشمعة التالية" if delay == 1 else f"بعد {delay * 5} دقيقة (محاكاة تأخر البيانات)"
        L += [f"## تنفيذ: {wait} | احتفاظ أقصى {hold} دقيقة{tag}", "",
              "| السيناريو | العدد | جني | وقف | زمني | متوسط الربح (≈ لكل عقد) | رابحة (فاصل 95%) |",
              "|---|---|---|---|---|---|---|"]
        tr = simulate(days, vix, delay, hold)
        for key, name in SCEN.items():
            L.append(fmt_row(name, summarize(tr[tr["scen"] == key]) if len(tr) else None))
        L.append(fmt_row("**كل السيناريوهات**", summarize(tr) if len(tr) else None))
        nl = simulate(days, vix, delay, hold, null_stride=6)
        if len(nl):
            L.append(fmt_row("مرجعي: دخول عشوائي (كل 30د، CALL وPUT)", summarize(nl)))
        L.append("")
    L += ["## كيف تقرأ النتيجة", "",
          "- إن كان متوسط الربح قريباً من المرجعي العشوائي أو أقل منه، فالسيناريوهات لا تضيف ميزة تُذكر في هذه العينة.",
          "- فاصل الثقة العريض يعني أن العينة لا تكفي لحكم. أعد التشغيل مع تراكم الشموع في الملف المحفوظ.",
          "- متوسط الربح الحقيقي سيكون أسوأ غالباً بسبب فروق السعر الفعلية وتأخر البيانات وانزلاق التنفيذ."]
    return "\n".join(L)


def main():
    bars = load_bars()
    vix = load_vix()
    days = session_days(bars)
    if len(days) < 5:
        raise SystemExit("بيانات غير كافية")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(report(days, vix))
    print(open(OUT, encoding="utf-8").read())


if __name__ == "__main__":
    main()
