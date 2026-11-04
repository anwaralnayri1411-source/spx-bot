#!/usr/bin/env python3
"""ظل «تأكيد حركة السوق» (H-CONFIRM، مسجّل مسبقاً في CLAUDE.md 4/11/2026 بطلب المستخدم). تسجيل فقط: لا يرسل ولا يغيّر توصية.

عند 10:30 نيويورك: حركة SPY منذ إغلاق أمس واتجاهها D، ثم ثلاثة تأكيدات:
  الحجم: حجم SPY في أول ساعة ÷ متوسط أول ساعة لآخر 20 جلسة ≥ 1.2.
  الاتساع: عدد صناديق القطاعات الـ11 المتحركة باتجاه D منذ إغلاق أمس ≥ 8.
  العائد (وصفي): ^TNX عكس الأسهم؟ (صعود أسهم مع هبوط العائد أو العكس).
بعد الإغلاق (--eval): نتيجة SPY من 10:30 حتى الإغلاق وحتى إغلاق الغد بإشارة D.
الدوال الصافية (بلا شبكة) يستعملها أيضاً الاختبار التاريخي research/confirm_study.py.
الملفات: confirm_log.csv، research/confirm_results.md. تشغيل: python confirm_shadow.py [--eval] [--force]
"""
import csv
import os
import sys
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
NY = ZoneInfo("America/New_York")
LOG = os.path.join(ROOT, "confirm_log.csv")
RESULTS = os.path.join(ROOT, "research", "confirm_results.md")
SECTORS = ("XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE", "XLC")
MOVE_MIN = 0.002            # يوم حركة: |SPY منذ إغلاق أمس حتى 10:30| ≥ 0.2%
VOL_X = 1.2                 # تأكيد الحجم
BREADTH_MIN = 8             # تأكيد الاتساع: 8 من 11
VOL_DAYS = 20
READ_FROM, READ_TO = dtime(10, 30), dtime(11, 0)
COLS = ["date", "time", "spy_prev", "spy_1030", "move", "side", "move_day", "vol_ratio", "vol_ok", "breadth", "n_sectors",
        "breadth_ok", "group", "leaders", "laggards", "tnx_chg", "tnx_opposite", "event", "out_close", "out_next"]


# ====================== الدوال الصافية ======================
def first_hour_volume(d5):
    """{يوم: حجم أول ساعة (9:30–10:25)} من شموع 5د بفهرس نيويورك بلا منطقة."""
    fh = d5[(d5.index.time >= dtime(9, 30)) & (d5.index.time < dtime(10, 30))]
    return fh.groupby(fh.index.date)["Volume"].sum()


def vol_ratio(fh_vol, day):
    """حجم أول ساعة اليوم ÷ متوسط آخر VOL_DAYS جلسة قبله (None إن نقصت البيانات)."""
    if day not in fh_vol.index:
        return None
    prev = fh_vol[fh_vol.index < day].tail(VOL_DAYS)
    if len(prev) < VOL_DAYS // 2 or prev.mean() <= 0:
        return None
    return float(fh_vol[day] / prev.mean())


def breadth(moves, side):
    """(عدد القطاعات باتجاه الحركة، عدد القطاعات المقروءة). moves: {رمز: حركة منذ إغلاق أمس}."""
    s = 1 if side == "CALL" else -1
    vals = [m for m in moves.values() if m is not None and not np.isnan(m)]
    return sum(1 for m in vals if m * s > 0), len(vals)


def group(vol_ok, breadth_ok):
    return "both" if vol_ok and breadth_ok else ("none" if not vol_ok and not breadth_ok else ("vol_only" if vol_ok else "breadth_only"))


def classify(move, vr, b, nb):
    """قراءة كاملة من الحركة ونسبة الحجم والاتساع."""
    side = "CALL" if move > 0 else "PUT"
    vol_ok = vr is not None and vr >= VOL_X
    breadth_ok = nb >= len(SECTORS) - 1 and b >= BREADTH_MIN
    return {"move": move, "side": side, "move_day": abs(move) >= MOVE_MIN, "vol_ratio": vr, "vol_ok": vol_ok,
            "breadth": b, "n_sectors": nb, "breadth_ok": breadth_ok, "group": group(vol_ok, breadth_ok)}


def outcome(side, p1030, close, next_close=None):
    s = 1 if side == "CALL" else -1
    return (s * (close / p1030 - 1) if close else None, s * (next_close / p1030 - 1) if next_close else None)


# ====================== الجلب (ياهو، الظل الحي) ======================
def _bars(sym, period="1mo"):
    import yfinance as yf
    d = yf.Ticker(sym).history(period=period, interval="5m", auto_adjust=False)
    if d is None or d.empty:
        return None
    d.index = d.index.tz_convert(NY).tz_localize(None)
    return d[(d.index.time >= dtime(9, 30)) & (d.index.time < dtime(16, 0))]


def _move_at(d5, day, t=dtime(10, 25)):
    """(إغلاق أمس، سعر إغلاق شمعة 10:25 اليوم) أو (None، None)."""
    if d5 is None:
        return None, None
    today = d5[(d5.index.date == day) & (d5.index.time <= t)]
    prev = d5[d5.index.date < day]
    if not len(today) or not len(prev):
        return None, None
    return float(prev["Close"].iloc[-1]), float(today["Close"].iloc[-1])


def _event(day):
    try:
        import options_bot as ob
        return "، ".join(nm for d, nm in ob.upcoming_events(day) if d == day)
    except Exception:  # noqa: BLE001
        return ""


def snapshot(n):
    day = n.date()
    spy = _bars("SPY")
    prev, p = _move_at(spy, day)
    if prev is None:
        print("لا بيانات SPY.")
        return None
    move = p / prev - 1
    vr = vol_ratio(first_hour_volume(spy), day)
    moves = {}
    for s in SECTORS:
        a, b = _move_at(_bars(s, "5d"), day)
        moves[s] = (b / a - 1) if a else None
    side = "CALL" if move > 0 else "PUT"
    bc, nb = breadth(moves, side)
    r = classify(move, vr, bc, nb)
    ranked = sorted(((m, k) for k, m in moves.items() if m is not None), reverse=True)
    tnx_chg = tnx_opp = ""
    tp, tn = _move_at(_bars("^TNX", "5d"), day)
    if tp:
        tnx_chg = round(tn - tp, 3)
        tnx_opp = int((tn - tp) * move < 0)
    row = {"date": day.isoformat(), "time": n.strftime("%H:%M"), "spy_prev": round(prev, 2), "spy_1030": round(p, 2),
           **{k: (round(v, 5) if isinstance(v, float) else v) for k, v in r.items()},
           "leaders": " ".join(k for _, k in ranked[:2]), "laggards": " ".join(k for _, k in ranked[-2:]),
           "tnx_chg": tnx_chg, "tnx_opposite": tnx_opp, "event": _event(day), "out_close": "", "out_next": ""}
    return row


def read_log():
    return pd.read_csv(LOG, dtype=str, keep_default_na=False) if os.path.exists(LOG) else pd.DataFrame(columns=COLS)


def append(row):
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        w.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in COLS})


def evaluate(n, closes=None):
    """يكمل النتائج للصفوف الناقصة من إغلاقات SPY اليومية ويكتب تقرير النتائج."""
    log = read_log()
    if not len(log):
        return "لا سجل بعد."
    if closes is None:
        import yfinance as yf
        c = yf.Ticker("SPY").history(period="3mo", auto_adjust=False)["Close"]
        c.index = c.index.tz_convert(NY).tz_localize(None).date
        closes = c
    days = list(closes.index)
    for i, r in log.iterrows():
        d = datetime.fromisoformat(r["date"]).date()
        if d not in closes.index or not r["spy_1030"]:
            continue
        k = days.index(d)
        nxt = float(closes.iloc[k + 1]) if k + 1 < len(days) else None
        oc, on = outcome(r["side"], float(r["spy_1030"]), float(closes.iloc[k]), nxt)
        if not r["out_close"] and oc is not None:
            log.at[i, "out_close"] = f"{oc:.5f}"
        if not r["out_next"] and on is not None:
            log.at[i, "out_next"] = f"{on:.5f}"
    log[COLS].to_csv(LOG, index=False)
    md = report(log)
    os.makedirs(os.path.dirname(RESULTS), exist_ok=True)
    open(RESULTS, "w", encoding="utf-8").write(md)
    return md


def _pct(v):
    return "-" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v * 100:+.2f}%"


GROUP_AR = {"both": "الحجم والاتساع معاً", "vol_only": "الحجم وحده", "breadth_only": "الاتساع وحده", "none": "بلا تأكيد"}


def report(log):
    L = ["# ظل تأكيد حركة السوق (H-CONFIRM، حي)", "",
         f"الأيام المسجّلة: {len(log)} | آخر تحديث: {datetime.now(NY):%Y-%m-%d %H:%M} نيويورك. النتيجة بإشارة حركة أول ساعة (موجب = استمرت).", "",
         "| المجموعة | أيام حركة | حتى الإغلاق | حتى إغلاق الغد |", "|---|---|---|---|"]
    m = log[log["move_day"].astype(str).isin(["True", "1"])].copy()
    for c in ("out_close", "out_next"):
        m[c] = pd.to_numeric(m[c], errors="coerce")
    for g, name in GROUP_AR.items():
        x = m[m["group"] == g]
        if len(x):
            L.append(f"| {name} | {len(x)} | {_pct(x['out_close'].mean())} | {_pct(x['out_next'].mean())} |")
    ev = m[m["event"] != ""]
    if len(ev):
        L.append(f"| أيام أحداث (كلها) | {len(ev)} | {_pct(ev['out_close'].mean())} | {_pct(ev['out_next'].mean())} |")
    L += ["", "وصفي حي؛ الحكم من الاختبار التاريخي (research/confirm_study_results.md)."]
    return "\n".join(L) + "\n"


def main(argv):
    n = datetime.now(NY)
    if "--eval" in argv:
        print(evaluate(n))
        return
    if "--force" not in argv:
        if n.weekday() >= 5 or not READ_FROM <= n.time() <= READ_TO:
            print("خارج نافذة القراءة.")
            return
        if (read_log()["date"] == n.date().isoformat()).any():
            print("قراءة اليوم مسجّلة.")
            return
    row = snapshot(n)
    if row:
        append(row)
        print(row)


if __name__ == "__main__":
    main(sys.argv[1:])
