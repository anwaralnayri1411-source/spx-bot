"""H-CRYPTO-GRID: بوت شبكة سبوت على BTC-USDT مقابل محفظة 50/50 بنفس التعرّض.
التعريف والفرضية مسجّلان مسبقاً في crypto/README.md. قراءة فقط: لا يتداول.
"""
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trend_study as ts  # noqa: E402

FEE = 0.001
WINDOW_H = 30 * 24
SPLIT = pd.Timestamp("2022-07-01")
CONFIGS = [(r, n) for r in (0.10, 0.20, 0.30) for n in (10, 20, 40)]
COINS = ["BTC", "ETH"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "grid_study_results.md")


def okx_hourly(coin, get=ts._get):
    rows, after = [], ""
    for _ in range(1200):
        url = (f"https://www.okx.com/api/v5/market/history-candles?instId={coin}-USDT&bar=1H&limit=100"
               + (f"&after={after}" if after else ""))
        d = get(url)
        if d.get("code") != "0":
            raise RuntimeError(d.get("msg"))
        if not d["data"]:
            break
        rows += d["data"]
        after = d["data"][-1][0]
        time.sleep(0.11)
    df = pd.DataFrame([(int(r[0]), *map(float, r[1:5]), int(r[8])) for r in rows],
                      columns=["ts", "o", "h", "l", "c", "confirm"])
    df = df[df.confirm == 1].drop_duplicates("ts").sort_values("ts")
    df.index = pd.to_datetime(df.ts, unit="ms")
    return df[["o", "h", "l", "c"]]


def path_points(bars):
    """مسار مقدّر لكل شمعة: خضراء O→L→H→C، حمراء O→H→L→C."""
    o, h, l, c = (bars[k].values for k in "ohlc")
    green = c >= o
    p2 = np.where(green, l, h)
    p3 = np.where(green, h, l)
    return np.column_stack([o, p2, p3, c]).ravel()


def grid_window(pts, equity, btc_val_prev, r, n):
    """يعيد (قيمة نهاية النافذة، قيمة BTC في النهاية، نسبة BTC الابتدائية، خارج النطاق؟)."""
    p0 = pts[0]
    lv = np.geomspace(p0 * (1 - r), p0 * (1 + r), n + 1)
    lo, up = lv[:-1], lv[1:]
    hold = lo >= p0                               # خلايا فوق السعر تبدأ بـ BTC
    w = hold.mean()
    equity -= FEE * abs(w * equity - btc_val_prev)  # كلفة إعادة البناء
    u = np.full(n, equity / n)
    q = np.where(hold, u / p0, 0.0)
    u = np.where(hold, 0.0, u)
    state = hold.copy()
    for a, b in zip(pts[:-1], pts[1:]):
        if b < a:
            m = (~state) & (lo >= b) & (lo < a)
            if m.any():
                q[m] = u[m] * (1 - FEE) / lo[m]
                u[m] = 0.0
                state[m] = True
        elif b > a:
            m = state & (up > a) & (up <= b)
            if m.any():
                u[m] = q[m] * up[m] * (1 - FEE)
                q[m] = 0.0
                state[m] = False
    pend = pts[-1]
    btc = q.sum() * pend
    return u.sum() + btc, btc, w


def run_grid(bars, r, n):
    """نتائج كل نافذة: عائد الشبكة وعائد 50/50 (بنفس النسبة الابتدائية)."""
    out = []
    eq_g, btc_g = 1.0, 0.0
    eq_b, btc_b = 1.0, 0.0
    for s in range(0, len(bars) - WINDOW_H + 1, WINDOW_H):
        win = bars.iloc[s:s + WINDOW_H]
        pts = path_points(win)
        p0, pend = pts[0], pts[-1]
        new_g, btc_g_end, w = grid_window(pts, eq_g, btc_g, r, n)
        # المرجع: نسبة w من BTC تُعاد موازنتها في بداية النافذة
        eb = eq_b - FEE * abs(w * eq_b - btc_b)
        bt = w * eb * pend / p0
        new_b = (1 - w) * eb + bt
        lo, hi = p0 * (1 - r), p0 * (1 + r)
        outside = float(((win.c < lo) | (win.c > hi)).mean())
        out.append(dict(start=win.index[0], g=new_g / eq_g - 1, b=new_b / eq_b - 1, w=w, out=outside,
                        bh=pend / p0 - 1))
        eq_g, btc_g, eq_b, btc_b = new_g, btc_g_end, new_b, bt
    return pd.DataFrame(out)


def boot_ci(x, n=5000, seed=1):
    rng = np.random.default_rng(seed)
    x = np.asarray(x)
    means = rng.choice(x, size=(n, len(x)), replace=True).mean(axis=1)
    return np.percentile(means, [2.5, 97.5])


def tot(r):
    return float(np.prod(1 + np.asarray(r)) - 1)


def pct(x):
    return "—" if pd.isna(x) else f"{x*100:+.1f}%"


def main(load=okx_hourly):
    lines = ["# نتائج H-CRYPTO-GRID", "",
             f"تشغيل {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. نوافذ 30 يوماً، عمولة 0.1% لكل تنفيذ، "
             f"الاكتشاف قبل {SPLIT.date()}.", ""]
    verdict_done = False
    for coin in COINS:
        try:
            bars = load(coin)
        except Exception as e:  # noqa: BLE001
            lines.append(f"- {coin}: فشل الجلب ({type(e).__name__})")
            continue
        lines += [f"## {coin}: {bars.index[0]:%Y-%m-%d} → {bars.index[-1]:%Y-%m-%d} ({len(bars):,} ساعة)", "",
                  "| النطاق | الخلايا | النصف | نوافذ | الشبكة الكلي | 50/50 الكلي | متوسط الفرق/نافذة | خارج النطاق |",
                  "|---|---|---|---|---|---|---|---|"]
        res, disc = {}, {}
        for r, n in CONFIGS:
            df = run_grid(bars, r, n)
            res[(r, n)] = df
            for half, m in (("اكتشاف", df.start < SPLIT), ("حكم", df.start >= SPLIT)):
                d = df[m]
                diff = (d.g - d.b).mean()
                if half == "اكتشاف":
                    disc[(r, n)] = diff
                lines.append(f"| ±{r*100:.0f}% | {n} | {half} | {len(d)} | {pct(tot(d.g))} | {pct(tot(d.b))} | "
                             f"{pct(diff)} | {d.out.mean()*100:.0f}% |")
        best = max(disc, key=lambda k: disc[k])
        d = res[best][res[best].start >= SPLIT]
        diff = (d.g - d.b).values
        lo, hi = boot_ci(diff)
        bh = tot(d.bh)
        lines += ["", f"**الإعداد المختار من الاكتشاف: ±{best[0]*100:.0f}% و{best[1]} خلية** "
                  f"(متوسط الفرق فيه {pct(disc[best])})", ""]
        if coin == "BTC" and not verdict_done:
            c1 = lo > 0
            c2 = tot(d.g) > 0
            v = "**مدعومة ✅**" if (c1 and c2) else "**غير مدعومة ❌**"
            lines += ["### الحكم (BTC، نوافذ الحكم)", "",
                      f"1. متوسط (الشبكة − 50/50) لكل نافذة {pct(diff.mean())}، فاصل 95% [{pct(lo)}، {pct(hi)}] "
                      f"→ {'✓' if c1 else '✗'}",
                      f"2. عائد الشبكة الكلي {pct(tot(d.g))} > 0 → {'✓' if c2 else '✗'}",
                      "", f"الفرضية: {v}", ""]
            verdict_done = True
        else:
            lines.append(f"وصفي: الفرق {pct(diff.mean())} [{pct(lo)}، {pct(hi)}]، الشبكة {pct(tot(d.g))}.")
        # مقارنة وصفية بالاحتفاظ وT50 في نصف الحكم (نفس الفترة تقريباً)
        daily = bars.c.resample("1D").last().dropna()
        P = ts.positions(daily)["T50"]
        m = daily.index >= d.start.iloc[0]
        t50 = ts.stats(ts.daily_returns(daily[m], P[m]))
        g_eq = np.cumprod(1 + d.g.values)
        g_mdd = float((g_eq / np.maximum.accumulate(g_eq) - 1).min())
        lines += ["", f"مقارنة وصفية (نصف الحكم): الشبكة {pct(tot(d.g))} (أعمق تراجع بين النوافذ {pct(g_mdd)}، "
                  f"أسوأ نافذة {pct(d.g.min())}) · 50/50 {pct(tot(d.b))} · الاحتفاظ {pct(bh)} · "
                  f"T50 {pct(t50['total'])} (تراجع {pct(t50['mdd'])})", ""]
    lines += ["تحفظات: شموع الساعة تفوّت تقلبات داخلها؛ مسار الشمعة افتراضي؛ الماضي لا يضمن المستقبل."]
    text = "\n".join(lines)
    open(OUT, "w").write(text)
    print(text)


if __name__ == "__main__":
    main()
