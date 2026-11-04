"""H-CRYPTO-TREND: اتبع الاتجاه أو اجلس نقداً، على شموع يومية (سبوت فقط).
التعريف والفرضية مسجّلان مسبقاً في crypto/README.md. قراءة فقط: لا يتداول.
"""
import sys
import time
import json
import urllib.request
from datetime import datetime, timezone

import numpy as np
import pandas as pd

COINS = ["BTC", "ETH", "SOL", "XRP", "LTC", "ADA", "DOGE"]
MAIN = "BTC"
SPLIT = pd.Timestamp("2022-07-01")
COST = 0.0015
RULES = ["T200", "T50", "X50_200", "DON", "TSM"]
OUT = "crypto/trend_study_results.md"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "research-script"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def okx_daily(coin):
    rows, after = [], ""
    for _ in range(60):
        url = (f"https://www.okx.com/api/v5/market/history-candles?instId={coin}-USDT"
               f"&bar=1Dutc&limit=100" + (f"&after={after}" if after else ""))
        d = _get(url)
        if d.get("code") != "0":
            raise RuntimeError(d.get("msg"))
        data = d["data"]
        if not data:
            break
        rows += data
        after = data[-1][0]
        time.sleep(0.15)
    if not rows:
        raise RuntimeError("no data")
    df = pd.DataFrame([(int(r[0]), float(r[4]), int(r[8])) for r in rows],
                      columns=["ts", "close", "confirm"])
    df = df[df.confirm == 1]
    df["date"] = pd.to_datetime(df.ts, unit="ms").dt.normalize()
    return df.drop_duplicates("date").set_index("date").sort_index()["close"]


def coinbase_daily(coin):
    out, end = [], datetime.now(timezone.utc)
    for _ in range(40):
        start = end - pd.Timedelta(days=299)
        url = (f"https://api.exchange.coinbase.com/products/{coin}-USD/candles?granularity=86400"
               f"&start={start.strftime('%Y-%m-%dT%H:%M:%SZ')}&end={end.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        d = _get(url)
        if not d:
            break
        out += d
        end = start
        time.sleep(0.3)
    df = pd.DataFrame(out, columns=["ts", "low", "high", "open", "close", "vol"])
    df["date"] = pd.to_datetime(df.ts, unit="s").dt.normalize()
    s = df.drop_duplicates("date").set_index("date").sort_index()["close"].astype(float)
    return s[s.index < pd.Timestamp(datetime.now(timezone.utc).date())]


def load(coin):
    for name, fn in (("OKX", okx_daily), ("Coinbase", coinbase_daily)):
        try:
            s = fn(coin)
            if len(s) > 400:
                return s, name
        except Exception as e:  # noqa: BLE001
            print(f"{coin} {name} failed: {type(e).__name__} {e}", file=sys.stderr)
    return None, None


def positions(c):
    sma50, sma200 = c.rolling(50).mean(), c.rolling(200).mean()
    p = {
        "T200": (c > sma200).astype(float).where(sma200.notna()),
        "T50": (c > sma50).astype(float).where(sma50.notna()),
        "X50_200": (sma50 > sma200).astype(float).where(sma200.notna()),
        "TSM": (c.pct_change(28) > 0).astype(float).where(c.shift(28).notna()),
    }
    hi20 = c.rolling(20).max()
    lo10 = c.rolling(10).min()
    don, state = [], 0.0
    for i in range(len(c)):
        if np.isnan(hi20.iloc[i]) or np.isnan(lo10.iloc[i]):
            don.append(np.nan)
            continue
        if state == 0 and c.iloc[i] >= hi20.iloc[i]:
            state = 1.0
        elif state == 1 and c.iloc[i] <= lo10.iloc[i]:
            state = 0.0
        don.append(state)
    p["DON"] = pd.Series(don, index=c.index)
    return p


def daily_returns(c, pos):
    """pos يُقرَّر عند إغلاق اليوم t ويكسب عائد t→t+1."""
    r = c.pct_change().shift(-1)
    pos = pos.fillna(0.0)
    turn = pos.diff().abs()
    turn.iloc[0] = pos.iloc[0]
    return (pos * r - turn * COST).dropna()


def stats(ret):
    if len(ret) < 30:
        return dict(total=np.nan, cagr=np.nan, sharpe=np.nan, mdd=np.nan)
    eq = (1 + ret).cumprod()
    yrs = len(ret) / 365
    sd = ret.std()
    return dict(total=eq.iloc[-1] - 1,
                cagr=eq.iloc[-1] ** (1 / yrs) - 1,
                sharpe=ret.mean() / sd * np.sqrt(365) if sd > 0 else np.nan,
                mdd=(eq / eq.cummax() - 1).min())


def shift_pvalue(c, pos, start, end):
    """نسبة الإزاحات الدائرية (≥30 يوماً) التي يتجاوز شاربها الأصلي داخل [start, end)."""
    mask = (c.index >= start) & (c.index < end)
    cs, ps = c[mask], pos[mask].fillna(0.0)
    actual = stats(daily_returns(cs, ps))["sharpe"]
    n = len(ps)
    vals = []
    for k in range(30, n - 30, 3):
        sh = pd.Series(np.roll(ps.values, k), index=ps.index)
        vals.append(stats(daily_returns(cs, sh))["sharpe"])
    vals = np.array(vals)
    return actual, float((vals >= actual).mean()), len(vals)


def pct(x):
    return "—" if pd.isna(x) else f"{x*100:+.1f}%"


def main():
    lines = ["# نتائج H-CRYPTO-TREND", "",
             f"تشغيل {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. الكلفة 0.15% لكل تغيير مركز. "
             f"الاكتشاف قبل {SPLIT.date()}، والحكم بعده.", ""]
    data = {}
    for coin in COINS:
        s, src = load(coin)
        if s is not None:
            data[coin] = s
            lines.append(f"- {coin}: {src}، {s.index[0].date()} → {s.index[-1].date()} ({len(s)} يوماً)")
    lines.append("")
    if MAIN not in data:
        lines.append("**فشل جلب BTC — لا نتيجة.**")
        open(OUT, "w").write("\n".join(lines))
        print("\n".join(lines))
        return

    # الاختيار والحكم على BTC
    c = data[MAIN]
    P = positions(c)
    first = c.index[0]
    end = c.index[-1] + pd.Timedelta(days=1)
    lines += [f"## {MAIN}: كل القواعد", "",
              "| القاعدة | النصف | العائد الكلي | سنوي | شارب | أقصى تراجع | في السوق |",
              "|---|---|---|---|---|---|---|"]
    disc_sharpe = {}
    for name in ["BH"] + RULES:
        pos = pd.Series(1.0, index=c.index) if name == "BH" else P[name]
        for half, (a, b) in (("اكتشاف", (first, SPLIT)), ("حكم", (SPLIT, end))):
            m = (c.index >= a) & (c.index < b)
            st = stats(daily_returns(c[m], pos[m]))
            expo = pos[m].fillna(0).mean()
            if half == "اكتشاف" and name != "BH":
                disc_sharpe[name] = st["sharpe"]
            lines.append(f"| {name} | {half} | {pct(st['total'])} | {pct(st['cagr'])} | "
                         f"{st['sharpe']:.2f} | {pct(st['mdd'])} | {expo*100:.0f}% |")
    chosen = max(disc_sharpe, key=lambda k: disc_sharpe[k])
    lines += ["", f"**القاعدة المختارة من نصف الاكتشاف (أعلى شارب): {chosen}** "
              f"({disc_sharpe[chosen]:.2f})", ""]

    mt = c.index >= SPLIT
    st_c = stats(daily_returns(c[mt], P[chosen][mt]))
    st_b = stats(daily_returns(c[mt], pd.Series(1.0, index=c.index)[mt]))
    actual, p, nsh = shift_pvalue(c, P[chosen], SPLIT, end)
    c1 = st_c["total"] > 0
    c2 = p < 0.05
    c3 = st_c["mdd"] > st_b["mdd"]
    verdict = "**مدعومة ✅**" if (c1 and c2 and c3) else "**غير مدعومة ❌**"
    lines += ["## الحكم (BTC، نصف الحكم)", "",
              f"1. العائد الكلي {pct(st_c['total'])} > 0 → {'✓' if c1 else '✗'}",
              f"2. شارب {actual:.2f}، نسبة الإزاحات العشوائية الأفضل منه p = {p:.3f} ({nsh} إزاحة) "
              f"→ {'✓' if c2 else '✗'}",
              f"3. أقصى تراجع {pct(st_c['mdd'])} مقابل الاحتفاظ {pct(st_b['mdd'])} → {'✓' if c3 else '✗'}",
              "", f"الفرضية: {verdict}", ""]

    # تكرار وصفي على العملات الأخرى بالقاعدة المختارة
    lines += [f"## تكرار وصفي: {chosen} مقابل الاحتفاظ (نصف الحكم)", "",
              "| العملة | عائد القاعدة | تراجعها | عائد الاحتفاظ | تراجعه | شارب القاعدة | p الإزاحة |",
              "|---|---|---|---|---|---|---|"]
    for coin, s in data.items():
        Ps = positions(s)[chosen]
        m = s.index >= SPLIT
        if m.sum() < 200:
            continue
        a = stats(daily_returns(s[m], Ps[m]))
        b = stats(daily_returns(s[m], pd.Series(1.0, index=s.index)[m]))
        _, pp, _ = shift_pvalue(s, Ps, SPLIT, s.index[-1] + pd.Timedelta(days=1))
        lines.append(f"| {coin} | {pct(a['total'])} | {pct(a['mdd'])} | {pct(b['total'])} | "
                     f"{pct(b['mdd'])} | {a['sharpe']:.2f} | {pp:.3f} |")

    # الإشارة الحالية
    lines += ["", "## الإشارة الحالية (إغلاق آخر يوم)", "",
              "| العملة | آخر إغلاق | فوق متوسط 50؟ | فوق متوسط 200؟ | عائد 28 يوماً | " + chosen + " |",
              "|---|---|---|---|---|---|"]
    for coin, s in data.items():
        Ps = positions(s)
        last = s.iloc[-1]
        s50, s200 = s.rolling(50).mean().iloc[-1], s.rolling(200).mean().iloc[-1]
        lines.append(f"| {coin} | {last:,.4g} | {'نعم' if last > s50 else 'لا'} | "
                     f"{'نعم' if last > s200 else 'لا'} | {pct(s.pct_change(28).iloc[-1])} | "
                     f"{'داخل السوق' if Ps[chosen].iloc[-1] == 1 else 'نقد'} |")
    lines += ["", "تحفظ: الماضي لا يضمن المستقبل؛ العملات غير BTC ناجية (انحياز بقاء)؛ "
              "الأرقام وصفية عدا الحكم المسجّل على BTC."]
    text = "\n".join(lines)
    open(OUT, "w").write(text)
    print(text)


if __name__ == "__main__":
    main()
