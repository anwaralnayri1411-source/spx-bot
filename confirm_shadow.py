#!/usr/bin/env python3
"""ظل «تأكيد حركة السوق» (H-CONFIRM، مسجّل مسبقاً في CLAUDE.md 4/11/2026 بطلب المستخدم). تسجيل فقط: لا يرسل ولا يغيّر توصية.

لكل أداة (SPY وQQQ وIWM وDIA و33 شركة قائمة البوت) عند 10:30 نيويورك: حركتها منذ إغلاق أمس واتجاهها، ثم:
  الحجم: حجم الأداة في أول ساعة ÷ متوسط أول ساعة لها لآخر 20 جلسة ≥ 1.2.
  الاتساع: عدد صناديق القطاعات الـ11 المتحركة باتجاهها منذ إغلاق أمس ≥ 8.
  قطاع الشركة (وصفي): صندوق قطاعها (stocks_sector_study.SECTOR) يتحرك باتجاهها.
  العائد (وصفي): ^TNX عكس السوق؟ (صعود SPY مع هبوط العائد أو العكس).
بعد الإغلاق (--eval): النتيجة من 10:30 حتى الإغلاق وحتى إغلاق الغد بإشارة الحركة.
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
INDEXES = ("SPY", "QQQ", "IWM", "DIA")
MOVE_MIN = {"index": 0.002, "stock": 0.005}   # يوم حركة
VOL_X = 1.2
BREADTH_MIN = 8
VOL_DAYS = 20
READ_FROM, READ_TO = dtime(10, 30), dtime(11, 0)
COLS = ["date", "time", "sym", "kind", "prev", "p1030", "move", "side", "move_day", "vol_ratio", "vol_ok", "breadth",
        "n_sectors", "breadth_ok", "group", "sector", "sector_ok", "leaders", "laggards", "tnx_chg", "tnx_opposite", "event",
        "out_close", "out_next"]
GROUP_AR = {"both": "الحجم والاتساع معاً", "vol_only": "الحجم وحده", "breadth_only": "الاتساع وحده", "none": "بلا تأكيد"}


def sector_map():
    try:
        sys.path.insert(0, os.path.join(ROOT, "research"))
        from stocks_sector_study import SECTOR
        return dict(SECTOR)
    except Exception:  # noqa: BLE001
        return {}


def instruments():
    try:
        import stocks_bot as sb
        stocks = list(sb.WATCHLIST)
    except Exception:  # noqa: BLE001
        stocks = list(sector_map())
    return list(INDEXES) + stocks


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


def classify(move, vr, b, nb, kind="index"):
    """قراءة كاملة من الحركة ونسبة الحجم والاتساع."""
    side = "CALL" if move > 0 else "PUT"
    vol_ok = vr is not None and vr >= VOL_X
    breadth_ok = nb >= len(SECTORS) - 1 and b >= BREADTH_MIN
    return {"move": move, "side": side, "move_day": abs(move) >= MOVE_MIN[kind], "vol_ratio": vr, "vol_ok": vol_ok,
            "breadth": b, "n_sectors": nb, "breadth_ok": breadth_ok, "group": group(vol_ok, breadth_ok)}


def outcome(side, p1030, close, next_close=None):
    s = 1 if side == "CALL" else -1
    return (s * (close / p1030 - 1) if close else None, s * (next_close / p1030 - 1) if next_close else None)


def move_at(d5, day, t=dtime(10, 25)):
    """(إغلاق أمس، إغلاق شمعة 10:25 اليوم) أو (None، None)."""
    if d5 is None or not len(d5):
        return None, None
    today = d5[(d5.index.date == day) & (d5.index.time <= t)]
    prev = d5[d5.index.date < day]
    if not len(today) or not len(prev):
        return None, None
    return float(prev["Close"].iloc[-1]), float(today["Close"].iloc[-1])


def read_one(d5, day, sec_moves, kind, own_sector_move=None):
    """صف قراءة أداة واحدة (بلا شبكة) أو None."""
    prev, p = move_at(d5, day)
    if prev is None:
        return None
    move = p / prev - 1
    side = "CALL" if move > 0 else "PUT"
    b, nb = breadth(sec_moves, side)
    r = classify(move, vol_ratio(first_hour_volume(d5), day), b, nb, kind)
    sok = "" if own_sector_move is None else int(own_sector_move * (1 if side == "CALL" else -1) > 0)
    return {"prev": round(prev, 4), "p1030": round(p, 4), **r, "sector_ok": sok}


# ====================== الجلب (ياهو، الظل الحي) ======================
def _download(syms, period):
    import yfinance as yf
    raw = yf.download(list(syms), period=period, interval="5m", group_by="ticker", auto_adjust=False, progress=False, threads=True)
    out = {}
    for s in syms:
        try:
            d = raw[s].dropna(subset=["Close"])
        except Exception:  # noqa: BLE001
            continue
        d.index = d.index.tz_convert(NY).tz_localize(None)
        out[s] = d[(d.index.time >= dtime(9, 30)) & (d.index.time < dtime(16, 0))]
    return out


def _event(day):
    try:
        import options_bot as ob
        return "، ".join(nm for d, nm in ob.upcoming_events(day) if d == day)
    except Exception:  # noqa: BLE001
        return ""


def snapshot(n):
    day = n.date()
    secmap = sector_map()
    syms = instruments()
    extra = sorted(set(SECTORS) | {v for v in secmap.values()} | {"^TNX"})
    bars = _download(syms, "1mo")
    sb_ = _download(extra, "5d")
    mv = {}
    for s, d in sb_.items():
        a, b = move_at(d, day)
        mv[s] = (b / a - 1) if a else None
    sec_moves = {s: mv.get(s) for s in SECTORS}
    ranked = sorted(((m, k) for k, m in sec_moves.items() if m is not None), reverse=True)
    leaders, laggards = " ".join(k for _, k in ranked[:2]), " ".join(k for _, k in ranked[-2:])
    tnx = mv.get("^TNX")
    tp, tn = move_at(sb_.get("^TNX"), day)
    tnx_chg = round(tn - tp, 3) if tp else ""
    ev = _event(day)
    rows = []
    spy_move = None
    for s in syms:
        kind = "index" if s in INDEXES else "stock"
        own = mv.get(secmap.get(s)) if kind == "stock" else None
        r = read_one(bars.get(s), day, sec_moves, kind, own)
        if r is None:
            continue
        if s == "SPY":
            spy_move = r["move"]
        rows.append({"date": day.isoformat(), "time": n.strftime("%H:%M"), "sym": s, "kind": kind,
                     **{k: (round(v, 5) if isinstance(v, float) else v) for k, v in r.items()},
                     "sector": secmap.get(s, "") if kind == "stock" else "", "leaders": leaders, "laggards": laggards,
                     "tnx_chg": tnx_chg, "tnx_opposite": "", "event": ev, "out_close": "", "out_next": ""})
    if tnx is not None and tp and spy_move is not None:
        for r in rows:
            r["tnx_opposite"] = int((tn - tp) * spy_move < 0)
    return rows


def read_log():
    return pd.read_csv(LOG, dtype=str, keep_default_na=False) if os.path.exists(LOG) else pd.DataFrame(columns=COLS)


def append(rows):
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        for row in rows:
            w.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in COLS})


def daily_closes(syms):
    import yfinance as yf
    c = yf.download(list(syms), period="3mo", interval="1d", auto_adjust=False, progress=False)["Close"]
    c.index = pd.Index(c.index.date)
    return c


def evaluate(n, closes=None):
    """يكمل النتائج للصفوف الناقصة من الإغلاقات اليومية ويكتب تقرير النتائج. closes: DataFrame (يوم × رمز)."""
    log = read_log()
    if not len(log):
        return "لا سجل بعد."
    if closes is None:
        closes = daily_closes(sorted(log["sym"].unique()))
    days = list(closes.index)
    for i, r in log.iterrows():
        d = datetime.fromisoformat(r["date"]).date()
        if d not in closes.index or r["sym"] not in closes.columns or not r["p1030"]:
            continue
        k = days.index(d)
        cl = closes[r["sym"]]
        nxt = float(cl.iloc[k + 1]) if k + 1 < len(days) and not np.isnan(cl.iloc[k + 1]) else None
        if np.isnan(cl.iloc[k]):
            continue
        oc, on = outcome(r["side"], float(r["p1030"]), float(cl.iloc[k]), nxt)
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


def report(log):
    L = ["# ظل تأكيد حركة السوق (H-CONFIRM، حي)", "",
         f"الأيام: {log['date'].nunique()} | القراءات: {len(log)} | آخر تحديث: {datetime.now(NY):%Y-%m-%d %H:%M} نيويورك. "
         "النتيجة بإشارة حركة أول ساعة (موجب = استمرت).", "",
         "| الأدوات | المجموعة | قراءات حركة | حتى الإغلاق | حتى إغلاق الغد |", "|---|---|---|---|---|"]
    m = log[log["move_day"].astype(str).isin(["True", "1"])].copy()
    for c in ("out_close", "out_next"):
        m[c] = pd.to_numeric(m[c], errors="coerce")
    for kind, kname in (("index", "المؤشرات"), ("stock", "الشركات")):
        for g, name in GROUP_AR.items():
            x = m[(m["kind"] == kind) & (m["group"] == g)]
            if len(x):
                L.append(f"| {kname} | {name} | {len(x)} | {_pct(x['out_close'].mean())} | {_pct(x['out_next'].mean())} |")
    s = m[(m["kind"] == "stock") & (m["sector_ok"].astype(str) != "")]
    for v, name in (("1", "قطاع الشركة معها"), ("0", "قطاع الشركة ضدها")):
        x = s[s["sector_ok"].astype(str) == v]
        if len(x):
            L.append(f"| الشركات | {name} | {len(x)} | {_pct(x['out_close'].mean())} | {_pct(x['out_next'].mean())} |")
    ev = m[m["event"] != ""]
    if len(ev):
        L.append(f"| الكل | أيام أحداث | {len(ev)} | {_pct(ev['out_close'].mean())} | {_pct(ev['out_next'].mean())} |")
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
    rows = snapshot(n)
    if rows:
        append(rows)
        print(f"{len(rows)} قراءة؛ SPY:", next((r for r in rows if r["sym"] == "SPY"), None))


if __name__ == "__main__":
    main(sys.argv[1:])
