"""اختبار «السلّم» في الظل (تسجيل وقياس فقط، لا يرسل ولا يغيّر أي توصية).
الفكرة (مستوحاة من تقارير MGT): عند اتجاه مؤكد، عقد رخيص جديد (2.5–4$) كل خطوة 5 نقاط، بدل عقد واحد.
القواعد المختبرة: اتجاه هابط = إغلاق دون افتتاح اليوم ودون إغلاق قبل 15 دقيقة (3 قراءات متتالية أدنى)،
وصاعد عكس ذلك؛ دخول عند قاع/قمة جديدة بخطوة ≥5 نقاط عن آخر دخول؛ حد أقصى 10 عقود يومياً؛ لا دخول بعد 14:30.
التسعير: Black-Scholes نظري بتقلب ثابت (حساسية 3 قيم) — ليس أسعاراً حقيقية! الخروج: +50% أو -40% (الوقف أولاً داخل الشمعة تحفظاً)، وإلا قيمة الإغلاق.
المخرج: ladder_shadow_log.csv. العينة صغيرة والتسعير نظري، للمراقبة وليس للحكم."""
import csv
import math
import os
import sys

import pandas as pd

TP, SL, MAXN, LASTH = 0.50, 0.40, 10, (14, 30)
OUT = "ladder_shadow_log.csv"


def _cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def price(S, K, T_min, iv, put):
    T = max(T_min, 0.5) / (390 * 252)
    sd = iv * math.sqrt(T)
    if sd <= 0:
        return max(0.0, (K - S) if put else (S - K))
    d1 = (math.log(S / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    return (K * _cdf(-d2) - S * _cdf(-d1)) if put else (S * _cdf(d1) - K * _cdf(d2))


def pick_strike(S, put, iv, tmin):
    """أبعد سترايك (مضاعف 5) يعطي سعراً 2.5–4$ (عقد 250–400$)؛ None إن لم يوجد."""
    best = None
    for off in range(0, 80, 5):
        K = round((S - off) / 5) * 5 if put else round((S + off) / 5) * 5
        p = price(S, K, tmin, iv, put)
        if 2.5 <= p <= 4.0:
            best = (K, p)
    return best


def simulate_day(g, iv):
    g = g.reset_index()
    n, rows, last_ref, o = len(g), [], None, g.open[0]
    for i in range(3, n - 2):
        t = g.iloc[i, 0]
        if (t.hour, t.minute) >= LASTH or len(rows) >= MAXN:
            break
        c = g.close[i]
        down = c < o and c < g.close[i - 3] and g.close[i] <= g.close[i - 1:i].min() + 0 and g.low[i] <= g.low[i - 3:i].min()
        up = c > o and c > g.close[i - 3] and g.high[i] >= g.high[i - 3:i].max()
        if not (down or up):
            continue
        put = down
        if last_ref is not None and (abs(c - last_ref) < 5 or ((c > last_ref) if put else (c < last_ref))):
            continue
        tmin = (16 * 60) - (t.hour * 60 + t.minute)
        pk = pick_strike(c, put, iv, tmin)
        if not pk:
            continue
        K, ent = pk
        last_ref, status, exitp = c, "open", None
        for j in range(i + 1, n):
            tj = (16 * 60) - (g.iloc[j, 0].hour * 60 + g.iloc[j, 0].minute)
            worst = price(g.high[j] if put else g.low[j], K, tj, iv, put)
            best = price(g.low[j] if put else g.high[j], K, tj, iv, put)
            if worst <= ent * (1 - SL):
                status, exitp = "stop", ent * (1 - SL); break
            if best >= ent * (1 + TP):
                status, exitp = "tp", ent * (1 + TP); break
        held = max(0.0, (K - g.close[n - 1]) if put else (g.close[n - 1] - K))
        if exitp is None:
            exitp = held
        rows.append((str(t)[:16], "PUT" if put else "CALL", K, round(c, 2), round(ent, 2), status,
                     round(exitp / ent - 1, 3), round(held / ent - 1, 3)))
    return rows


def summarize(rows):
    if not rows:
        return "لا حالات"
    rule = sum(r[6] for r in rows) / len(rows)
    held = sum(r[7] for r in rows) / len(rows)
    tp = sum(r[5] == "tp" for r in rows)
    st = sum(r[5] == "stop" for r in rows)
    return f"n={len(rows)} هدف={tp} وقف={st} متوسط العائد بالقاعدة={rule:+.0%} لو تُرك للإغلاق={held:+.0%}"


def load_cache(path="research/spx_5m_cache.csv"):
    d = pd.read_csv(path)
    d = d.set_index(pd.to_datetime(d["time"], utc=True).dt.tz_convert("America/New_York")).drop(columns="time")
    d.columns = [c.lower() for c in d.columns]
    d = d[(d.index.time >= pd.Timestamp("09:30").time()) & (d.index.time <= pd.Timestamp("16:00").time())]
    d["day"] = d.index.date
    return d


def daily(period="10d", iv=0.14):
    import yfinance as yf
    h = yf.Ticker("^SPX").history(period=period, interval="5m").reset_index()
    h.columns = [str(c).lower() for c in h.columns]
    h = h.rename(columns={h.columns[0]: "time"})
    h = h.set_index(pd.to_datetime(h["time"], utc=True).dt.tz_convert("America/New_York"))[["open", "high", "low", "close"]]
    h = h[(h.index.time >= pd.Timestamp("09:30").time()) & (h.index.time <= pd.Timestamp("16:00").time())]
    h["day"] = h.index.date
    done = set(open("ladder_days.txt").read().split()) if os.path.exists("ladder_days.txt") else set()
    for day, g in h.groupby("day"):
        k = day.isoformat()
        if k in done or k < "2026-10-14" or len(g) < 70:
            continue
        rows = simulate_day(g.drop(columns="day"), iv)
        fresh = not os.path.exists(OUT)
        with open(OUT, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if fresh:
                w.writerow(["time_ny", "side", "strike", "spot", "theo_entry", "status", "ret_rule", "ret_held"])
            w.writerows(rows)
        open("ladder_days.txt", "a").write(k + "\n")
        print(k, summarize(rows))


if __name__ == "__main__":
    if sys.argv[1:] == ["--daily"]:
        daily()
    else:                                  # اختبار رجعي على الكاش، لا يكتب ملفات
        d = load_cache()
        for iv in (0.10, 0.14, 0.18):
            allr, perday = [], []
            for day, g in d.groupby("day"):
                r = simulate_day(g.drop(columns="day"), iv)
                allr += r; perday.append(len(r))
            print(f"IV={iv:.0%}:", summarize(allr), f"| أيام بلا دخول={sum(x == 0 for x in perday)}/{len(perday)}")
            if iv == 0.14:
                for s in ("PUT", "CALL"):
                    print("   ", s, summarize([r for r in allr if r[1] == s]))
