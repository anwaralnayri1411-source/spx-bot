"""أسعار عقود لحظية من Alpaca (feed=indicative) مع الرجوع لياهو عند الفشل — أداة بيانات فقط، بلا منطق توصيات.

يستخدمها بوت SPX (ولاحقاً بوت الشركات) عبر مفتاح لكل مشروع:
  LIVE_QUOTES_SPX / LIVE_QUOTES_STOCKS = "0" (معطّل) | "shadow" (نجلب ونسجّل فقط؛ البوت يستخدم ياهو كما هو)
  | "1" (السعر اللحظي مصدر أول للمتابعة وياهو احتياط؛ لمشاريع LIVE_READY فقط، وغيرها يُعامل "1" كـ "shadow").
لا ترفع أي استثناء إلى البوت أبداً، وتنتظر 5 ثوانٍ كحد أقصى، وترفض أي تسعيرة أقدم من MAX_AGE.
وTastytrade مصدر ثالث في الظل فقط (tasty_quote): يُسجَّل بجانب Alpaca ولا يُستخدم في أي حساب. يحتاج
TASTY_CLIENT_SECRET وTASTY_REFRESH_TOKEN (منحة OAuth شخصية بصلاحية read فقط).
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
            "entry", "entry_live", "pnl_yahoo", "pnl_live", "source",
            "tasty_bid", "tasty_ask", "tasty_age_s", "tasty_status"]
LIVE_READY = {"SPX"}   # المشاريع التي يُفعَّل فيها "1" فعلياً (الشركات تنتظر نتائج feed_compare)


def mode(project):
    """'0' أو 'shadow' أو '1'. بلا مفاتيح = '0'؛ '1' فقط لمشاريع LIVE_READY، وأي قيمة أخرى = ظل."""
    m = (os.getenv(f"LIVE_QUOTES_{project}") or "0").strip().lower()
    if m in ("", "0", "off", "false") or not (os.getenv("ALPACA_KEY") and os.getenv("ALPACA_SECRET")):
        return "0"
    if m in ("1", "on", "true", "live") and project in LIVE_READY:
        return "1"
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


TASTY = "https://api.tastyworks.com"
TASTY_UA = "spxbot/1.0"            # Tastytrade يرفض أي طلب بلا User-Agent بصيغة product/version
_TASTY_TOK = {}                     # رمز الدخول (15 دقيقة) مرة واحدة لكل تشغيل


def _tasty_req(path, data=None, token=None):
    h = {"User-Agent": TASTY_UA, "Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    body = None
    if data is not None:
        h["Content-Type"] = "application/json"
        body = json.dumps(data).encode("utf-8")
    with urllib.request.urlopen(urllib.request.Request(TASTY + path, data=body, headers=h), timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _tasty_token():
    t = _TASTY_TOK.get("t")
    if t and (datetime.now(timezone.utc) - _TASTY_TOK["at"]).total_seconds() < 600:
        return t
    d = _tasty_req("/oauth/token", {"grant_type": "refresh_token", "refresh_token": os.getenv("TASTY_REFRESH_TOKEN", ""),
                                    "client_secret": os.getenv("TASTY_CLIENT_SECRET", "")})
    _TASTY_TOK.update(t=d["access_token"], at=datetime.now(timezone.utc))
    return d["access_token"]


def tasty_quote(contract):
    """تسعيرة Tastytrade لرمز OCC (نفس رمز quote()). نفس شكل quote()؛ status = off بلا مفاتيح. لا يرفع استثناء."""
    if not (os.getenv("TASTY_CLIENT_SECRET") and os.getenv("TASTY_REFRESH_TOKEN")):
        return {"bid": None, "ask": None, "age": None, "status": "off"}
    try:
        m = re.match(r"^([A-Z]+)(\d{6}[CP]\d{8})$", contract)
        sym = f"{m.group(1):<6}{m.group(2)}"          # Tastytrade: الجذر بعرض 6 خانات بمسافات
        q = urllib.parse.urlencode({"equity-option": sym})
        items = (_tasty_req(f"/market-data/by-type?{q}", token=_tasty_token()).get("data") or {}).get("items") or []
        it = next((x for x in items if x.get("symbol") == sym), None) or {}
        bid, ask = float(it.get("bid") or 0), float(it.get("ask") or 0)
        if bid <= 0 or ask <= 0:
            return {"bid": None, "ask": None, "age": None, "status": "empty"}
        age = _age(it.get("updated-at") or "")
        st = "ok" if age is not None and age <= MAX_AGE else "stale"
        return {"bid": bid, "ask": ask, "age": age, "status": st}
    except Exception as e:  # noqa: BLE001
        return {"bid": None, "ask": None, "age": None, "status": f"error:{type(e).__name__}"}


LOG_STOCKS = "live_quotes_stocks_log.csv"   # ملف منفصل لبوت الشركات: سير عمل مختلف، فلا يتصادم الحفظ


def _upgrade_header(path):
    """سجل قديم بأعمدة أقل: يُعاد كتابته بالأعمدة الحالية (القيم الجديدة فارغة) كي لا تنزاح الصفوف."""
    with open(path, encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        if r.fieldnames is None or list(r.fieldnames) == LOG_COLS:
            return
        rows = list(r)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        w.writeheader()
        w.writerows({k: r.get(k) or "" for k in LOG_COLS} for r in rows)


def log(row, path=LOG):
    try:
        new = not os.path.exists(path)
        if not new:
            _upgrade_header(path)
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
