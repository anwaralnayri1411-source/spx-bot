"""شموع SPX 5 دقائق بذيل لحظي (أداة بيانات؛ لا ترسل ولا تقرر). بطلب المستخدم 27/10/2026؛ مسجّلة في CLAUDE.md قبل الكود.

مشكلة ياهو: شموع ^SPX 5د متأخرة (نحو 15 دقيقة)، فإشارات الاتجاه تُبنى على حركة انتهت. الحل:
  - تبقى شموع ياهو لما سبق (الماضي لا يتأخر).
  - يُستكمل ذيل اليوم المفقود بشموع SPY 5د من Alpaca (مصدر IEX اللحظي) × نسبة SPX/SPY:
    وسيط آخر 6 شموع متداخلة اليوم (ياهو SPX ÷ Alpaca SPY)، وإلا نسبة السعرين اللحظيين.
  - الشمعة الجارية تُضبط على سعر SPX اللحظي من Tastytrade إن وُجد.
  - أي فشل → شموع ياهو كما هي (status يشرح السبب).
المفتاح LIVE_BARS_SPX: 0 إيقاف، shadow (الافتراضي) حساب وتسجيل فقط، 1 القرار من الشموع المستكملة.
لاحقاً يغذي جامع الأسعار المدخلات نفسها (شموع SPY والسعر اللحظي) من لقطته بدل الطلب المباشر.
"""
import csv
import os
import urllib.parse
from datetime import timedelta, timezone

import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(ROOT, "live_bars_log.csv")
RATIO_BARS = 6
BAR_MIN = 5
LOG_COLS = ["time", "mode", "status", "ratio", "ratio_src", "added", "yahoo_last", "yahoo_age_min", "live_age_min",
            "S_yahoo", "S_live", "r30_yahoo", "r30_live", "bull_yahoo", "bear_yahoo", "bull_live", "bear_live", "side_yahoo", "side_live"]


def mode():
    m = (os.getenv("LIVE_BARS_SPX") or "shadow").strip().lower()
    if m in ("0", "off", "false"):
        return "0"
    if m in ("1", "on", "true", "live"):
        return "1"
    return "shadow"


def spy_bars(n, days=1):
    """شموع SPY 5د من Alpaca (IEX، لحظية) منذ افتتاح اليوم: DataFrame بفهرس نيويورك وأعمدة Open/High/Low/Close."""
    import live_quotes as lq
    start = (n - timedelta(days=days - 1)).replace(hour=9, minute=30, second=0, microsecond=0).astimezone(timezone.utc)
    q = urllib.parse.urlencode({"timeframe": "5Min", "start": start.isoformat().replace("+00:00", "Z"), "feed": "iex",
                                "limit": 1000, "adjustment": "raw"})
    rows = lq._get(f"{lq.DATA}/v2/stocks/SPY/bars?{q}").get("bars") or []
    if not rows:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close"])
    idx = pd.DatetimeIndex([pd.Timestamp(b["t"]) for b in rows]).tz_convert(n.tzinfo)
    return pd.DataFrame({"Open": [float(b["o"]) for b in rows], "High": [float(b["h"]) for b in rows],
                         "Low": [float(b["l"]) for b in rows], "Close": [float(b["c"]) for b in rows]}, index=idx)


def extend(ix, spy, n, spx_live=None, spy_live=None, complete_only=True):
    """دالة نقية: (شموع مستكملة، معلومات). ix: شموع ياهو SPX بفهرس نيويورك. spy: شموع SPY اللحظية لليوم.
    آخر شمعة من ياهو قد تكون ناقصة (متأخرة وجارية عنده)، فتُستبدل بنسخة SPY. complete_only: القرار على الشموع
    المكتملة فقط (بدايتها + 5 دقائق ≤ الآن) كما اختُبر في H-LIVE5؛ وإلا تبقى الجارية وتُضبط على سعر SPX اللحظي."""
    if ix is None or ix.empty:
        return ix, {"status": "no_yahoo"}
    if spy is None or spy.empty:
        return ix, {"status": "no_spy"}
    today = spy.index[-1].date()
    if today != n.date():
        return ix, {"status": "spy_not_today"}
    last = ix.index[-1]
    ix_t = ix[(ix.index.date == today) & (ix.index < last)]
    common = ix_t.index.intersection(spy.index)
    if len(common):
        r = (ix_t.loc[common, "Close"] / spy.loc[common, "Close"]).iloc[-RATIO_BARS:]
        ratio, src = float(r.median()), "overlap"
    elif spx_live and spy_live:
        ratio, src = float(spx_live) / float(spy_live), "live"
    else:
        return ix, {"status": "no_ratio"}
    if not 9.0 < ratio < 11.0:                     # SPX/SPY ≈ 10؛ غير ذلك خطأ بيانات
        return ix, {"status": "bad_ratio", "ratio": ratio}
    add = spy[spy.index >= last][["Open", "High", "Low", "Close"]] * ratio
    add["Volume"] = 0.0
    base = ix[ix.index < last] if len(add) and add.index[0] == last else ix
    out = pd.concat([base, add]) if len(add) else ix.copy()
    if complete_only:
        out = out[out.index <= n - timedelta(minutes=BAR_MIN)]
        if out.empty:
            return ix, {"status": "no_complete_bar"}
    elif spx_live and out.index[-1] >= n - timedelta(minutes=BAR_MIN) and out.index[-1].date() == today:
        k = out.index[-1]
        out.loc[k, "Close"] = float(spx_live)
        out.loc[k, "High"] = max(float(out.loc[k, "High"]), float(spx_live))
        out.loc[k, "Low"] = min(float(out.loc[k, "Low"]), float(spx_live))
    return out, {"status": "ok", "ratio": round(ratio, 5), "ratio_src": src, "added": int(out.index.isin(add.index).sum()), "yahoo_last": last,
                 "yahoo_age_min": round((n - last.to_pydatetime()).total_seconds() / 60, 1),
                 "live_age_min": round((n - out.index[-1].to_pydatetime()).total_seconds() / 60, 1)}


def build(ix, n):
    """يجلب SPY والسعرين اللحظيين ثم extend. لا يرفع استثناء."""
    try:
        import live_quotes as lq
        spy = spy_bars(n)
        spx_live, _ = lq.spot("SPX")
        spy_live, _ = lq.spot("SPY")
        return extend(ix, spy, n, spx_live, spy_live)
    except Exception as e:  # noqa: BLE001
        return ix, {"status": f"error:{type(e).__name__}"}


def log(row):
    try:
        new = not os.path.exists(LOG)
        with open(LOG, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=LOG_COLS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in LOG_COLS})
    except Exception as e:  # noqa: BLE001
        print("live_bars log:", type(e).__name__)


def report(path=LOG):
    """ملخص المقارنة لعرضه على المستخدم قبل التحويل."""
    try:
        d = pd.read_csv(path)
    except Exception:  # noqa: BLE001
        return "لا سجل بعد."
    ok = d[d["status"] == "ok"]
    if ok.empty:
        return f"{len(d)} فحصاً، لا مقارنة ناجحة. الحالات: {d['status'].value_counts().to_dict()}"
    same = (ok["side_yahoo"] == ok["side_live"]).mean()
    return (f"{len(d)} فحصاً، ناجح {len(ok)}. عمر آخر شمعة ياهو: وسيط {ok['yahoo_age_min'].median():.0f} دقيقة (أقصى {ok['yahoo_age_min'].max():.0f})؛ "
            f"شموع مضافة وسيط {ok['added'].median():.0f}. فرق السعر وسيط {((ok['S_live'] / ok['S_yahoo'] - 1).abs() * 100).median():.2f}%. "
            f"الاتجاه نفسه في {100 * same:.0f}% من الفحوص.")


if __name__ == "__main__":
    print(report())
