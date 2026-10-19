#!/usr/bin/env python3
"""ظل «زخم 30 دقيقة» لـ SPX (تسجيل فقط؛ لا يرسل رسائل ولا يقرأ أو يغيّر أي ملف من ملفات البوت).

الفكرة (من اختبار 60 يوماً في 19/10/2026): إذا تحرك المؤشر 0.25% أو أكثر خلال 30 دقيقة، يميل لإكمال الحركة
خلال الساعة التالية. تاريخياً: +7.7 نقطة بعد 60 دقيقة، صحيحة 68%، 37 صفقة في 29 يوماً، والعقد النظري ≈ −3%
مقابل −23% للاتجاه العشوائي. اكتُشفت بعد النظر في البيانات (عكس فكرة فشلت)، لذلك تُختبر هنا على أسعار حقيقية قبل أي قرار.

تعريفان للإشارة، كل منهما يُسجَّل وحده:
  live    : حركة SPY آخر 30 دقيقة من شموع Alpaca اللحظية (IEX).
  delayed : حركة ^GSPC آخر 30 دقيقة من ياهو (متأخرة ~15 دقيقة، كما يراها البوت وكما في الاختبار التاريخي).
لكل إشارة نشتري على الورق عقد SPY ينتهي اليوم على السعر (أقرب سترايك)، بسعر الطلب اللحظي من Alpaca (indicative)،
ومعه مرجع عادل: العقد المعاكس بنفس السترايك في نفس اللحظة. الخروج بسعر البيع (bid):
  +50% أو −40% (بعد 15 دقيقة) أو بعد 60 دقيقة أو عند 15:30.
النافذة 10:00–15:00 نيويورك، صفقة واحدة كل 60 دقيقة لكل تعريف، و3 كحد أقصى يومياً. يتوقف تلقائياً بعد END_DATE.
تنبيه: GitHub قد يؤخر التشغيل (كل ~30 دقيقة أحياناً)، فوقت الخروج الفعلي يُسجَّل كما حدث.

الملفات (كلها جديدة): momentum_shadow_state.json، momentum_shadow_log.csv، research/momentum_shadow_results.md
يحتاج: ALPACA_KEY، ALPACA_SECRET.   تشغيل: python research/momentum_shadow.py [--report]
"""
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATE = os.path.join(ROOT, "momentum_shadow_state.json")
LOG = os.path.join(ROOT, "momentum_shadow_log.csv")
REPORT = os.path.join(HERE, "momentum_shadow_results.md")
NY = ZoneInfo("America/New_York")
DATA = "https://data.alpaca.markets"
END_DATE = date(2026, 11, 20)     # نهاية التجربة (~4 أسابيع)، ثم مراجعة مع المستخدم
THRESH = 0.0025                   # حركة 30 دقيقة
WINDOW = ((10, 0), (15, 0))       # دخول جديد
FINAL = (15, 30)
TP, SL, MIN_AGE, HOLD = 0.50, 0.40, 15, 60
GAP_MIN, MAX_PER_DAY = 60, 3
MAX_AGE = 120                     # ثانية: تسعيرة أقدم لا تُستخدم
MAX_BAR_AGE = 180                 # ثانية: شمعة SPY أقدم = لا إشارة لحظية
HOLIDAYS = {"2026-11-26", "2026-12-25", "2027-01-01"}
EARLY_CLOSE = {"2026-11-27", "2026-12-24"}
LOG_COLS = ["date", "defn", "role", "side", "strike", "sig_time", "r30", "entry_time", "entry_spy", "entry", "exit",
            "exit_time", "pnl", "peak", "spy_after_60", "spy_move_pts", "checks", "stale"]


# ============================ البيانات ============================
def _get(path, params):
    url = f"{DATA}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"APCA-API-KEY-ID": os.getenv("ALPACA_KEY", ""),
                                               "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET", ""),
                                               "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _age(ts, now):
    try:
        t = datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", ts.replace("Z", "+00:00")))
        return (now.astimezone(timezone.utc) - t).total_seconds()
    except Exception:
        return None


def spy_bars(n):
    """شموع SPY دقيقة اليوم من Alpaca (IEX، لحظية): [(وقت نيويورك، إغلاق)]."""
    start = n.replace(hour=9, minute=30, second=0, microsecond=0).astimezone(timezone.utc)
    d = _get("/v2/stocks/SPY/bars", {"timeframe": "1Min", "start": start.isoformat().replace("+00:00", "Z"),
                                     "feed": "iex", "limit": 1000})
    return [(datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(NY), float(b["c"])) for b in d.get("bars") or []]


def gspc_r30():
    """حركة آخر 30 دقيقة لـ ^GSPC كما يراها البوت من ياهو (شموع 5 دقائق متأخرة)."""
    import yfinance as yf
    b = yf.Ticker("^GSPC").history(period="1d", interval="5m")["Close"].dropna()
    return float(b.iloc[-1] / b.iloc[-7] - 1) if len(b) >= 7 else None


def occ(exp, side, strike):
    return f"SPY{exp.replace('-', '')[2:]}{'C' if side == 'CALL' else 'P'}{int(round(strike * 1000)):08d}"


def quotes(contracts, n):
    """{رمز: (bid, ask)} للتسعيرات الحديثة فقط (indicative؛ لا نستخدم آخر صفقة أبداً)."""
    if not contracts:
        return {}
    d = _get("/v1beta1/options/snapshots", {"symbols": ",".join(contracts), "feed": "indicative"})
    out = {}
    for sym, s in (d.get("snapshots") or {}).items():
        q = s.get("latestQuote") or {}
        bid, ask, age = q.get("bp"), q.get("ap"), _age(q.get("t") or "", n)
        if bid and ask and bid > 0 and ask > 0 and age is not None and age <= MAX_AGE:
            out[sym] = (float(bid), float(ask))
    return out


# ============================ الحالة ============================
def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {"date": None, "open": [], "await": [], "last": {}, "count": {}}


def save_state(s):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


def write_closed(items):
    if not items:
        return
    new = not os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        if new:
            w.writeheader()
        for it in items:
            w.writerow({k: it.get(k, "") for k in LOG_COLS})


def _mins(a, b):
    return (b - datetime.fromisoformat(a)).total_seconds() / 60


# ============================ التشغيل ============================
def run(n=None, bars=None, r30_delayed=None, book=None):
    """bars / r30_delayed (دالة) / book للاختبار المحلي فقط (بدونها تُجلب من Alpaca وياهو)."""
    n = n or datetime.now(NY)
    today = n.date().isoformat()
    s = load_state()
    if s.get("date") != today:                       # يوم جديد: ما بقي مفتوحاً من أمس يُغلق بآخر تقييم
        left = s.get("open", []) + s.get("await", [])
        for it in left:
            it["exit"] = it.get("exit") or "final"
        write_closed(left)
        s = {"date": today, "open": [], "await": [], "last": {}, "count": {}}
    hm = (n.hour, n.minute)
    if (n.weekday() >= 5 or today in HOLIDAYS or n.date() > END_DATE or hm < (9, 45) or hm > (16, 0)
            or (today in EARLY_CLOSE and hm > (13, 0))):
        save_state(s)
        print("خارج نافذة التجربة.")
        return
    if not (os.getenv("ALPACA_KEY") and os.getenv("ALPACA_SECRET")) and bars is None:
        print("لا مفاتيح Alpaca.")
        return
    try:
        bars = bars if bars is not None else spy_bars(n)
    except Exception as e:
        print("تعذّر جلب شموع SPY:", e); save_state(s); return
    if not bars:
        print("لا شموع SPY."); save_state(s); return
    t_last, spy = bars[-1]
    fresh = (n - t_last).total_seconds() <= MAX_BAR_AGE
    final_hm = (12, 30) if today in EARLY_CLOSE else FINAL

    # ---- إشارات جديدة ----
    new = []
    if WINDOW[0] <= hm < WINDOW[1] and fresh:
        past = [c for t, c in bars if t <= t_last - timedelta(minutes=30)]
        sig = {"live": (spy / past[-1] - 1) if past else None}
        try:
            sig["delayed"] = r30_delayed() if r30_delayed else gspc_r30()
        except Exception as e:
            print("تعذّر ياهو:", e); sig["delayed"] = None
        for defn, r in sig.items():
            if r is None or abs(r) < THRESH:
                continue
            last = s["last"].get(defn)
            if s["count"].get(defn, 0) >= MAX_PER_DAY or (last and _mins(last, n) < GAP_MIN):
                continue
            side = "CALL" if r > 0 else "PUT"
            K = float(round(spy))
            new.append([{"date": today, "defn": defn, "role": role, "side": sd, "strike": K,
                         "sig_time": n.isoformat(timespec="minutes"), "r30": round(r, 5),
                         "entry_time": n.isoformat(timespec="minutes"), "entry_spy": round(spy, 2), "entry": None,
                         "exit": "", "exit_time": "", "pnl": "", "peak": 0.0, "spy_after_60": "", "spy_move_pts": "",
                         "checks": 0, "stale": 0, "occ": occ(today, sd, K)}
                        for role, sd in (("signal", side), ("opposite", "PUT" if side == "CALL" else "CALL"))])

    # ---- التسعير ----
    want = sorted({it["occ"] for it in s["open"]} | {it["occ"] for pair in new for it in pair})
    try:
        q = book if book is not None else quotes(want, n)
    except Exception as e:
        print("تعذّر جلب أسعار العقود:", e); q = {}
    added = []
    for pair in new:                                  # الزوج (الإشارة + المعاكس) كاملاً أو لا شيء
        if not all(it["occ"] in q for it in pair):
            print("لا سعر حديث لـ", [it["occ"] for it in pair], "— تُتجاهل الإشارة هذه المرة")
            continue
        for it in pair:
            it["entry"] = round(q[it["occ"]][1], 2)
        defn = pair[0]["defn"]
        s["last"][defn] = n.isoformat(timespec="minutes")
        s["count"][defn] = s["count"].get(defn, 0) + 1
        added += pair
    new = added

    closed = []
    for it in s["open"]:
        sg = 1 if it["side"] == "CALL" else -1
        age = _mins(it["entry_time"], n)
        if it["spy_after_60"] == "" and age >= HOLD:   # مقياس بلا نموذج: حركة SPY بعد ~60 دقيقة باتجاه العقد
            it["spy_after_60"] = round(spy, 2)
            it["spy_move_pts"] = round((spy - it["entry_spy"]) * sg * 10, 1)   # بنقاط SPX تقريباً
        b = q.get(it["occ"])
        if not b:
            it["stale"] += 1
            if hm >= final_hm or age >= HOLD + 30:     # لا سعر طويلاً: نغلق بآخر تقييم
                it.update(exit=it["exit"] or "no_quote", exit_time=n.strftime("%H:%M"))
                closed.append(it)
            continue
        pnl = b[0] / it["entry"] - 1
        it["checks"] += 1
        it["peak"] = round(max(it["peak"], pnl), 3)
        it["pnl"] = round(pnl, 3)
        out = ("tp" if pnl >= TP else "stop" if age >= MIN_AGE and pnl <= -SL else
               "time" if age >= HOLD else "final" if hm >= final_hm else None)
        if out:
            it.update(exit=out, exit_time=n.strftime("%H:%M"))
            closed.append(it)
    s["open"] = [it for it in s["open"] if it not in closed] + new
    # صفقة أُغلقت قبل 60 دقيقة: ننتظر قياس حركة SPY بعد 60 دقيقة ثم نكتبها في السجل
    wait = s.get("await", []) + closed
    done = []
    for it in wait:
        if it["spy_after_60"] == "" and _mins(it["entry_time"], n) >= HOLD:
            sg = 1 if it["side"] == "CALL" else -1
            it["spy_after_60"] = round(spy, 2)
            it["spy_move_pts"] = round((spy - it["entry_spy"]) * sg * 10, 1)
        if it["spy_after_60"] != "" or hm >= final_hm:
            done.append(it)
    s["await"] = [it for it in wait if it not in done]
    write_closed(done)
    save_state(s)
    print(f"SPY {spy:.2f} | جديد {len(new)} | مغلق {len(closed)} | مفتوح {len(s['open'])} | بانتظار قياس 60د {len(s['await'])}")


# ============================ التقرير ============================
def report():
    if not os.path.exists(LOG):
        print("لا صفقات مغلقة بعد."); return
    rows = list(csv.DictReader(open(LOG, encoding="utf-8")))
    L = ["# ظل زخم 30 دقيقة (SPX) — نتائج على أسعار Alpaca اللحظية", "",
         f"آخر تحديث: {datetime.now(NY):%Y-%m-%d %H:%M} نيويورك. تجربة حتى {END_DATE}. تسجيل فقط، بلا رسائل.", "",
         "المرجع التاريخي (60 يوماً، نظري): الإشارة +7.7 نقطة بعد 60د، صحيحة 68%، العقد ≈ −3% مقابل −23% للعشوائي.", "",
         "| التعريف | الدور | صفقات | أيام | متوسط العقد | رابحة | هدف/وقف/وقت/نهاية | حركة SPX باتجاهنا بعد ~60د |",
         "|---|---|---|---|---|---|---|---|"]
    for defn in ("live", "delayed"):
        for role in ("signal", "opposite"):
            x = [r for r in rows if r["defn"] == defn and r["role"] == role and r["pnl"] not in ("", None)]
            if not x:
                continue
            p = [float(r["pnl"]) for r in x]
            mv = [float(r["spy_move_pts"]) for r in x if r["spy_move_pts"] not in ("", None)]
            k = {e: sum(1 for r in x if r["exit"] == e) for e in ("tp", "stop", "time", "final")}
            L.append(f"| {'لحظي' if defn == 'live' else 'متأخر (ياهو)'} | {'الإشارة' if role == 'signal' else 'المعاكس (مرجع)'} | "
                     f"{len(x)} | {len({r['date'] for r in x})} | {sum(p) / len(p) * 100:+.1f}% | "
                     f"{sum(1 for v in p if v > 0) / len(p) * 100:.0f}% | {k['tp']}/{k['stop']}/{k['time']}/{k['final']} | "
                     f"{(f'{sum(mv) / len(mv):+.1f}') if mv else '—'} |")
    L += ["", "⚠️ العينة صغيرة حتى 20 يوماً على الأقل. الحكم: الإشارة أفضل من المعاكس بفارق واضح في الحالتين، "
              "ومتوسطها فوق الصفر، قبل أي اقتراح لتغيير البوت."]
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    if "--report" in sys.argv:
        report()
    else:
        run()
