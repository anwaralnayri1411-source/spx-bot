#!/usr/bin/env python3
"""انجراف ما قبل الأرباح (H-PAPER R3، مسجّل في CLAUDE.md 4/11/2026 بطلب المستخدم). توصيات تجريبية، والحكم بالدولار بعد شهر.

مرة يومياً بعد 10:30 نيويورك: لشركات قائمة بوت الشركات التي إعلانها بعد 5–10 أيام تداول، CALL بدلتا ≈ 0.45 **ينتهي قبل الإعلان**
(يوم الإعلان إن كان بعد الإغلاق، وإلا قبله بيوم)، تكلفته 100–600$. حد 3 في اليوم، ومرة واحدة لكل إعلان.
كل تشغيل (كل 30 دقيقة): متابعة المفتوح: هدف +50% أو وقف −40% أو إغلاق في آخر جلسة قبل الإعلان (15:30).
إيقاف آلي: صافٍ −500$ أو 5 خسائر متتالية. الرسائل إلى TELEGRAM_CHAT_ID وحده («📅 فرصة ما قبل الأرباح (تجريبية)»).
الملفات: earnings_runup_log.csv، earnings_runup_state.json، messages_earnings_runup.jsonl.
تشغيل: python earnings_runup.py [--no-send] [--force]
"""
import json
import math
import os
import sys
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
NY = ZoneInfo("America/New_York")
LOG = os.path.join(ROOT, "earnings_runup_log.csv")
STATE = os.path.join(ROOT, "earnings_runup_state.json")
TD_MIN, TD_MAX = 5, 10
COST_MIN, COST_MAX = 100.0, 600.0
DELTA_T, DELTA_MIN, DELTA_MAX = 0.45, 0.30, 0.60
MAX_SPREAD, MIN_OI = 0.30, 100
MAX_PER_DAY = 3
TP, SL = 0.50, -0.40
HALT_USD, HALT_STREAK = -500.0, 5
SCAN_FROM, END = dtime(10, 30), dtime(16, 0)
EXIT_AT = dtime(15, 30)
COLS = ["id", "date", "time", "ticker", "earn_ts", "exit_day", "exp", "strike", "occ", "entry", "spot", "delta", "status",
        "exit_time", "exit_ret", "pnl_usd", "last_ret"]


# ====================== دوال صافية ======================
def trading_days_between(d0, d1):
    """أيام التداول (بلا عطل رسمية) من الغد حتى d1 شاملاً."""
    return int(np.busday_count(d0 + timedelta(days=1), d1 + timedelta(days=1)))


def exit_day(earn_ts):
    """آخر جلسة قبل الإعلان: يوم الإعلان إن كان بعد 9:30 (بعد الإغلاق عادةً)، وإلا يوم العمل السابق."""
    d = earn_ts.date()
    if earn_ts.time() >= dtime(9, 30):
        return d
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def pick_expiry(options, today, last):
    """أبعد انتهاء ≤ last وبعد اليوم بيومين على الأقل، أو None."""
    ok = [e for e in options if today + timedelta(days=2) <= date.fromisoformat(e) <= last]
    return max(ok) if ok else None


def bs_delta(S, K, T, iv):
    if S <= 0 or K <= 0 or T <= 0 or iv <= 0:
        return 0.0
    d1 = (math.log(S / K) + 0.5 * iv * iv * T) / (iv * math.sqrt(T))
    return 0.5 * (1 + math.erf(d1 / math.sqrt(2)))


def pick_call(calls, S, T):
    """أقرب CALL إلى دلتا 0.45 ضمن حدود التكلفة والسيولة والفرق؛ dict أو None."""
    best = None
    for r in calls.itertuples():
        bid, ask = float(r.bid or 0), float(r.ask or 0)
        if bid <= 0 or ask <= 0:
            continue
        mid = (bid + ask) / 2
        spr = (ask - bid) / mid
        cost = ask * 100
        oi = 0 if pd.isna(r.openInterest) else float(r.openInterest)
        iv = float(r.impliedVolatility or 0)
        dl = bs_delta(S, float(r.strike), T, iv)
        if not (COST_MIN <= cost <= COST_MAX and spr <= MAX_SPREAD and oi >= MIN_OI and DELTA_MIN <= dl <= DELTA_MAX):
            continue
        if best is None or abs(dl - DELTA_T) < abs(best["delta"] - DELTA_T):
            best = {"strike": float(r.strike), "ask": ask, "bid": bid, "delta": dl, "cost": cost}
    return best


def halted(log):
    done = log[log["status"].isin(["هدف", "وقف", "إغلاق"])]
    pnl = pd.to_numeric(done["pnl_usd"], errors="coerce").dropna()
    if pnl.sum() <= HALT_USD:
        return True
    streak = 0
    for x in pnl:
        streak = streak + 1 if x < 0 else 0
    return streak >= HALT_STREAK


def entry_message(r):
    return (f"📅 **فرصة ما قبل الأرباح (تجريبية)** | {r['ticker']} 🟢 CALL\n"
            f"الإعلان: {r['earn_ts'][:16].replace('T', ' ')} نيويورك | الخروج: {r['exit_day']} قبل الإغلاق (قبل الإعلان)\n"
            f"العقد: **{r['ticker']} {float(r['strike']):g} CALL** ينتهي {r['exp']} (قبل الإعلان) | دلتا {float(r['delta']):.2f}\n"
            f"دخول ≈ ${float(r['entry']):.2f} (${float(r['entry']) * 100:,.0f}) | 🎯 +50% · 🛑 −40%\n"
            "الفكرة: الأسهم تميل للصعود في الأسبوع الذي يسبق الإعلان (+0.88% في المتوسط، H-EARNPRE)، ونخرج قبل النتيجة فلا نقامر عليها.\n"
            "⚠️ تجريبية (H-PAPER): تُقاس بالدولار وتُلغى إن خسرت. القرار قرارك.")


def exit_message(r, status, ret):
    icon = {"هدف": "✅", "وقف": "🛑", "إغلاق": "⏹️"}[status]
    return (f"{icon} **ما قبل الأرباح** {r['ticker']} {float(r['strike']):g} CALL: {status} {ret * 100:+.0f}% "
            f"(${float(r['entry']) * ret * 100:+,.0f} للعقد)")


# ====================== الحالة والسجل ======================
def read_log():
    return pd.read_csv(LOG, dtype=str, keep_default_na=False) if os.path.exists(LOG) else pd.DataFrame(columns=COLS)


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"done": [], "halted": False, "scan_day": ""}


def send(text, no_send):
    if no_send:
        print(text)
        return
    import analyst as an
    an.send(text, "earnings_runup")


# ====================== الشبكة ======================
def next_earnings(t):
    """(طابع الإعلان القادم بنيويورك) أو None — من get_earnings_dates (فيه الوقت)."""
    try:
        e = t.get_earnings_dates(limit=6)
        fut = [pd.Timestamp(x).tz_convert(NY).tz_localize(None) for x in e.index if pd.Timestamp(x) > pd.Timestamp.now(tz="UTC")]
        return min(fut) if fut else None
    except Exception:  # noqa: BLE001
        return None


def quote_mid(occ):
    import live_quotes as lq
    q = lq.chain_quotes([occ]).get(occ)
    return None if not q else (q[0]["bid"] + q[0]["ask"]) / 2


def scan(n, st, log, no_send):
    import yfinance as yf
    import live_quotes as lq
    import stocks_bot as sb
    today = n.date()
    sent_today = int((log["date"] == today.isoformat()).sum())
    for tk in sb.WATCHLIST:
        if sent_today >= MAX_PER_DAY:
            break
        t = yf.Ticker(tk)
        ets = next_earnings(t)
        if ets is None:
            continue
        key = f"{tk}:{ets.date().isoformat()}"
        if key in st["done"] or not TD_MIN <= trading_days_between(today, ets.date()) <= TD_MAX:
            continue
        last = exit_day(ets)
        exp = pick_expiry(list(t.options or []), today, last)
        if not exp:
            continue
        S, _ = lq.spot(tk)
        if not S:
            S = sb.spot_now(t)
        try:
            calls = t.option_chain(exp).calls
            calls = lq.overlay(calls, S, 0.15)
        except Exception as e:  # noqa: BLE001
            print("سلسلة:", tk, type(e).__name__)
            continue
        T = max((date.fromisoformat(exp) - today).days, 1) / 365
        k = pick_call(calls, S, T)
        st["done"].append(key)                     # مرة لكل إعلان، وجد عقداً أم لا
        if not k:
            print("لا عقد مناسب:", tk, exp)
            continue
        out_day = min(last, date.fromisoformat(exp))   # الخروج في آخر جلسة قبل الإعلان أو يوم انتهاء العقد، أيهما أسبق
        r = {"id": f"{today}:{tk}", "date": today.isoformat(), "time": n.strftime("%H:%M"), "ticker": tk,
             "earn_ts": ets.isoformat(), "exit_day": out_day.isoformat(), "exp": exp, "strike": f"{k['strike']:g}",
             "occ": lq.occ(tk, exp, "CALL", k["strike"]), "entry": f"{k['ask']:.2f}", "spot": f"{S:.2f}",
             "delta": f"{k['delta']:.3f}", "status": "مفتوحة", "exit_time": "", "exit_ret": "", "pnl_usd": "", "last_ret": ""}
        log = pd.concat([log, pd.DataFrame([r])], ignore_index=True)
        sent_today += 1
        send(entry_message(r), no_send)
    return log


def track(n, log, no_send, quote=quote_mid):
    for i, r in log[log["status"] == "مفتوحة"].iterrows():
        mid = quote(r["occ"])
        if mid is None:
            continue
        ret = mid / float(r["entry"]) - 1
        log.at[i, "last_ret"] = f"{ret:.4f}"
        due = n.date().isoformat() >= r["exit_day"] and n.time() >= EXIT_AT
        status = "هدف" if ret >= TP else "وقف" if ret <= SL else "إغلاق" if due else None
        if status:
            log.loc[i, ["status", "exit_time", "exit_ret", "pnl_usd"]] = [status, n.strftime("%Y-%m-%d %H:%M"), f"{ret:.4f}",
                                                                         f"{float(r['entry']) * ret * 100:.2f}"]
            send(exit_message(r, status, ret), no_send)
    return log


def main(argv):
    n = datetime.now(NY)
    no_send = "--no-send" in argv
    if "--force" not in argv and (n.weekday() >= 5 or not dtime(9, 45) <= n.time() <= END):
        print("خارج النافذة.")
        return
    st, log = load_state(), read_log()
    log = track(n, log, no_send)
    if not st.get("halted") and halted(log):
        st["halted"] = True
        send("⏸️ **توقفت فرص ما قبل الأرباح** (H-PAPER: صافٍ −500$ أو 5 خسائر متتالية). لا توصيات جديدة حتى تقرر.", no_send)
    if not st.get("halted") and n.time() >= SCAN_FROM and st.get("scan_day") != n.date().isoformat():
        log = scan(n, st, log, no_send)
        st["scan_day"] = n.date().isoformat()
    log[COLS].to_csv(LOG, index=False)
    st["done"] = st["done"][-300:]
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    main(sys.argv[1:])
