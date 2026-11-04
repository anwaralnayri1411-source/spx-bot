#!/usr/bin/env python3
"""انجراف ما قبل الأرباح (H-PAPER R3، مسجّل في CLAUDE.md 4/11/2026 بطلب المستخدم). توصيات تجريبية، والحكم بالدولار بعد شهر.

طريقة «مال السوق» (H-EXITPLAN، بطلب المستخدم 4/11/2026): مرة يومياً بعد 10:30 نيويورك، لشركات قائمة بوت الشركات التي إعلانها
بعد 5–10 أيام تداول: **عقدان** CALL بدلتا ≈ 0.45 (100–600$ للعقد) ينتهيان **بعد يوم رد الفعل D0**. حد 3 في اليوم، ومرة لكل إعلان.
كل تشغيل (كل 30 دقيقة): قبل الإعلان −40% للاثنين؛ عند +100% يُباع الأول (يسترد رأس المال) ويبقى الثاني حتى إغلاق D0؛
وإن لم يتضاعف حتى آخر جلسة قبل الإعلان (15:30) يُباع الاثنان.
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
TP2, SL, QTY = 1.00, -0.40, 2
HALT_USD, HALT_STREAK = -500.0, 5
SCAN_FROM, END = dtime(10, 30), dtime(16, 0)
EXIT_AT = dtime(15, 30)
COLS = ["id", "date", "time", "ticker", "earn_ts", "exit_day", "d0", "exp", "strike", "occ", "entry", "spot", "delta", "status",
        "exit_time", "exit_ret", "pnl_usd", "last_ret"]
DONE = ("مال السوق", "وقف", "إغلاق")


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


def reaction_day(exit_d):
    """يوم رد الفعل D0 = أول يوم عمل بعد آخر جلسة قبل الإعلان."""
    return pd.Timestamp(np.busday_offset(np.datetime64(exit_d), 1, roll="forward")).date()


def pick_expiry(options, d0, max_days=10):
    """أول انتهاء ≥ D0 وخلال max_days يوماً منه، أو None (العقد يعبر الإعلان)."""
    ok = [e for e in options if d0 <= date.fromisoformat(e) <= d0 + timedelta(days=max_days)]
    return min(ok) if ok else None


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
    done = log[log["status"].isin(DONE)]
    pnl = pd.to_numeric(done["pnl_usd"], errors="coerce").dropna()
    if pnl.sum() <= HALT_USD:
        return True
    streak = 0
    for x in pnl:
        streak = streak + 1 if x < 0 else 0
    return streak >= HALT_STREAK


def entry_message(r):
    e = float(r["entry"])
    return (f"📅 **فرصة ما قبل الأرباح — مال السوق (تجريبية)** | {r['ticker']} 🟢 CALL ×{QTY}\n"
            f"الإعلان: {r['earn_ts'][:16].replace('T', ' ')} نيويورك | يوم رد الفعل: {r['d0']}\n"
            f"العقد: **{r['ticker']} {float(r['strike']):g} CALL** ينتهي {r['exp']} | دلتا {float(r['delta']):.2f}\n"
            f"دخول ≈ ${e:.2f} للعقد (${e * 100 * QTY:,.0f} للعقدين)\n"
            f"الخطة: 🛑 −40% للاثنين قبل الإعلان (${e * 0.6:.2f}) · عند **+100%** (${e * 2:.2f}) بِع عقداً واحداً فتسترد رأس مالك، "
            f"وأكمل بالثاني حتى إغلاق يوم رد الفعل · إن لم يتضاعف حتى {r['exit_day']} قبل الإغلاق فبِع الاثنين قبل الإعلان.\n"
            "الأساس: اختبار 848 إعلاناً بأسعار حقيقية (H-EXITPLAN): +3% للصفقة، و36% رابحة.\n"
            "⚠️ تجريبية (H-PAPER): تُقاس بالدولار وتُلغى إن خسرت. القرار قرارك.")


def exit_message(r, status, usd, note=""):
    icon = {"نصف": "💰", "مال السوق": "✅", "وقف": "🛑", "إغلاق": "⏹️"}[status]
    head = {"نصف": "تضاعف العقد: بِع عقداً واحداً الآن (استرددت رأس المال)، وأكمل بالثاني حتى إغلاق يوم رد الفعل",
            "مال السوق": "أُغلق العقد الثاني بعد الإعلان", "وقف": "بلغ الوقف −40%: بِع العقدين", "إغلاق": "لم يتضاعف قبل الإعلان: بِع العقدين"}[status]
    return f"{icon} **ما قبل الأرباح** {r['ticker']} {float(r['strike']):g} CALL: {head}{note} | الحصيلة ≈ ${usd:+,.0f} للعقدين"


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
        d0 = reaction_day(last)
        exp = pick_expiry(list(t.options or []), d0)
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
        r = {"id": f"{today}:{tk}", "date": today.isoformat(), "time": n.strftime("%H:%M"), "ticker": tk,
             "earn_ts": ets.isoformat(), "exit_day": last.isoformat(), "d0": d0.isoformat(), "exp": exp, "strike": f"{k['strike']:g}",
             "occ": lq.occ(tk, exp, "CALL", k["strike"]), "entry": f"{k['ask']:.2f}", "spot": f"{S:.2f}",
             "delta": f"{k['delta']:.3f}", "status": "مفتوحة", "exit_time": "", "exit_ret": "", "pnl_usd": "", "last_ret": ""}
        log = pd.concat([log, pd.DataFrame([r])], ignore_index=True)
        sent_today += 1
        send(entry_message(r), no_send)
    return log


def track(n, log, no_send, quote=quote_mid):
    """مال السوق: «مفتوحة» = العقدان؛ «نصف» = بيع الأول عند +100% وبقي الثاني حتى إغلاق D0."""
    day, now_t = n.date().isoformat(), n.time()
    for i, r in log[log["status"].isin(["مفتوحة", "نصف"])].iterrows():
        mid = quote(r["occ"])
        if mid is None:
            continue
        e = float(r["entry"])
        ret = mid / e - 1
        log.at[i, "last_ret"] = f"{ret:.4f}"
        stamp = n.strftime("%Y-%m-%d %H:%M")
        if r["status"] == "مفتوحة":
            if ret >= TP2:
                log.at[i, "status"] = "نصف"
                send(exit_message(r, "نصف", e * 100 * TP2), no_send)
            elif ret <= SL:
                usd = QTY * e * 100 * ret
                log.loc[i, ["status", "exit_time", "exit_ret", "pnl_usd"]] = ["وقف", stamp, f"{ret:.4f}", f"{usd:.2f}"]
                send(exit_message(r, "وقف", usd), no_send)
            elif day >= r["exit_day"] and now_t >= EXIT_AT:
                usd = QTY * e * 100 * ret
                log.loc[i, ["status", "exit_time", "exit_ret", "pnl_usd"]] = ["إغلاق", stamp, f"{ret:.4f}", f"{usd:.2f}"]
                send(exit_message(r, "إغلاق", usd), no_send)
        elif day >= r["d0"] and now_t >= EXIT_AT:
            usd = e * 100 * (TP2 + ret)                    # الأول بيع عند +100%، والثاني الآن
            log.loc[i, ["status", "exit_time", "exit_ret", "pnl_usd"]] = ["مال السوق", stamp, f"{(TP2 + ret) / 2:.4f}", f"{usd:.2f}"]
            send(exit_message(r, "مال السوق", usd, f" ({ret * 100:+.0f}% للثاني)"), no_send)
    return log


def main(argv):
    n = datetime.now(NY)
    no_send = "--no-send" in argv
    if "--force" not in argv and (n.weekday() >= 5 or not dtime(9, 45) <= n.time() <= END):
        print("خارج النافذة.")
        return
    st, log = load_state(), read_log().reindex(columns=COLS, fill_value="")
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
