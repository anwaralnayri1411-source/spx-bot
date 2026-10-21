"""أسعار عقود لحظية من Alpaca (feed=indicative) مع الرجوع لياهو عند الفشل — أداة بيانات فقط، بلا منطق توصيات.

يستخدمها بوت SPX (ولاحقاً بوت الشركات) عبر مفتاح لكل مشروع:
  LIVE_QUOTES_SPX / LIVE_QUOTES_STOCKS = "0" (معطّل) | "shadow" (نجلب ونسجّل فقط؛ البوت يستخدم ياهو كما هو)
التفعيل الفعلي ("1") مرحلة لاحقة بموافقة المستخدم؛ حالياً تُعامل "1" كـ "shadow".
لا ترفع أي استثناء إلى البوت أبداً، وتنتظر 5 ثوانٍ كحد أقصى، وترفض أي تسعيرة أقدم من MAX_AGE.
المخرج: live_quotes_log.csv (للمقارنة).   تقرير: python live_quotes.py --report
"""
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DATA = "https://data.alpaca.markets"
FEED = "indicative"
TIMEOUT = 5
MAX_AGE = 120
LOG = "live_quotes_log.csv"
LOG_COLS = ["time", "project", "id", "contract", "yahoo_val", "alpaca_bid", "alpaca_ask", "age_s", "status",
            "entry", "entry_live", "pnl_yahoo", "pnl_live"]


def mode(project):
    """'0' أو 'shadow'. أي قيمة أخرى غير '0' تُعامل كظل في هذه المرحلة. بلا مفاتيح = '0'."""
    m = (os.getenv(f"LIVE_QUOTES_{project}") or "0").strip().lower()
    if m in ("", "0", "off", "false") or not (os.getenv("ALPACA_KEY") and os.getenv("ALPACA_SECRET")):
        return "0"
    return "shadow"


def occ(root, exp, side, strike):
    """رمز OCC: SPXW261019C07700000 (الجذر + YYMMDD + C/P + السترايك × 1000 بثمانية أرقام)."""
    d = exp.replace("-", "")[2:]
    return f"{root}{d}{'C' if side == 'CALL' else 'P'}{int(round(float(strike) * 1000)):08d}"


def _age(ts):
    try:
        t = datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", ts.replace("Z", "+00:00")))
        return (datetime.now(timezone.utc) - t).total_seconds()
    except Exception:
        return None


def _get(url):
    req = urllib.request.Request(url, headers={"APCA-API-KEY-ID": os.getenv("ALPACA_KEY", ""),
                                               "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET", ""),
                                               "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def quote(contract):
    """{'bid','ask','age','status'}؛ status = ok | stale | empty | error. لا يرفع استثناء."""
    try:
        q = urllib.parse.urlencode({"symbols": contract, "feed": FEED})
        d = _get(f"{DATA}/v1beta1/options/snapshots?{q}")
        s = (d.get("snapshots") or {}).get(contract) or {}
        lq = s.get("latestQuote") or {}
        bid, ask = lq.get("bp"), lq.get("ap")
        if not bid or not ask or bid <= 0 or ask <= 0:
            return {"bid": None, "ask": None, "age": None, "status": "empty"}
        age = _age(lq.get("t") or "")
        st = "ok" if age is not None and age <= MAX_AGE else "stale"
        return {"bid": float(bid), "ask": float(ask), "age": age, "status": st}
    except Exception as e:  # noqa: BLE001  أي خطأ = رجوع لياهو
        return {"bid": None, "ask": None, "age": None, "status": f"error:{type(e).__name__}"}


LOG_STOCKS = "live_quotes_stocks_log.csv"   # ملف منفصل لبوت الشركات: سير عمل مختلف، فلا يتصادم الحفظ


def log(row, path=LOG):
    try:
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=LOG_COLS)
            if new:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in LOG_COLS})
    except Exception as e:  # noqa: BLE001
        print("تعذر حفظ سجل الأسعار اللحظية:", e)


def report(path=LOG):
    """ملخص الظل: التوفر والحداثة، ومتى كانت قاعدة +50% / −40% ستختلف بين المصدرين."""
    if not os.path.exists(path):
        print("لا سجل بعد.")
        return
    R = list(csv.DictReader(open(path, encoding="utf-8")))
    ok = [r for r in R if r["status"] == "ok"]
    print(f"قراءات: {len(R)} | حديثة (≤{MAX_AGE}ث): {len(ok)} ({100 * len(ok) / max(len(R), 1):.0f}%) | "
          f"أخرى: {', '.join(sorted({r['status'] for r in R if r['status'] != 'ok'})) or '—'}")

    def first(rows, col, cond):
        for r in rows:
            try:
                if cond(float(r[col])):
                    return r["time"][11:16]
            except (TypeError, ValueError):
                continue
        return "—"
    for pid in sorted({r["id"] for r in R}):
        rows = [r for r in R if r["id"] == pid]
        print(f"- {pid}: وقف ياهو {first(rows, 'pnl_yahoo', lambda x: x <= -0.4)} / لحظي {first(rows, 'pnl_live', lambda x: x <= -0.4)}"
              f" | هدف ياهو {first(rows, 'pnl_yahoo', lambda x: x >= 0.5)} / لحظي {first(rows, 'pnl_live', lambda x: x >= 0.5)}")


if __name__ == "__main__":
    if "--report" in sys.argv:
        report()
