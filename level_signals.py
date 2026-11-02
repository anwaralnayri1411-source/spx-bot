#!/usr/bin/env python3
"""توصيات المستويات التجريبية (H-LEVELSIG، مسجّلة مسبقاً في CLAUDE.md 2/11/2026 بطلب المستخدم). منفصلة عن قرار البوت.

كل 5 دقائق 9:50–16:00 نيويورك:
  - على آخر شمعة SPX 5د مكتملة (شموع البوت اللحظية): ارتداد من دعم (لمس + إغلاق أخضر فوقه) → CALL،
    أو رفض عند مقاومة (لمس + إغلاق أحمر تحتها) → PUT. المستويات من options_bot.build_plan والجدران من مراقب السيولة.
  - الفلتر: غاما موجبة في آخر لقطة للمراقب (≤15د) وميل التدفق آخر 30د ليس ضد الجهة.
  - العقد: options_bot.pick_contracts نفسه؛ هدف +50%، وقف −40%، إغلاق 15:55. رسالة عند الدخول والهدف والوقف.
  - إلى TELEGRAM_CHAT_ID وحده. إيقاف مبكر بعد 15 توصية بمتوسط ≤ −15% أو 5 خسائر متتالية.
الملفات: level_signals_log.csv، level_signals_state.json، messages_level_signals.jsonl.
تشغيل: python level_signals.py [--no-send] [--force]
"""
import csv
import json
import os
import sys
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
NY = ZoneInfo("America/New_York")
LOG = os.path.join(ROOT, "level_signals_log.csv")
STATE = os.path.join(ROOT, "level_signals_state.json")
FLOW_LOG = os.path.join(ROOT, "spx_flow_log.csv")
BOT_LOG = os.path.join(ROOT, "zero_dte_log.csv")
START, LAST_ENTRY, CLOSE_AT, END = dtime(9, 50), dtime(15, 0), dtime(15, 55), dtime(16, 0)
TOUCH = 0.0005            # لمس المستوى ضمن 0.05%
MAX_PER_DAY = 3
SAME_SIDE_MIN = 30
FLOW_MAX_AGE = 15         # دقائق
TP, SL = 0.50, -0.40
HALT_N, HALT_MEAN, HALT_STREAK = 15, -0.15, 5
COLS = ["id", "date", "time", "side", "kind", "level", "tags", "strike", "exp", "occ", "entry", "spot", "gex", "lean",
        "status", "exit_time", "exit_ret", "last_ret"]


# ====================== الحالة والسجل ======================
def load_state(day):
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        st = {}
    if st.get("date") != day:
        st = {"date": day, "sent": [], "halted": st.get("halted", False)}
    return st


def save_state(st):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def read_log():
    if not os.path.exists(LOG):
        return pd.DataFrame(columns=COLS)
    return pd.read_csv(LOG, dtype=str, keep_default_na=False)


def write_log(df):
    df[COLS].to_csv(LOG, index=False)


# ====================== القراءات (بلا شبكة) ======================
def flow_state(fl, n):
    """(الغاما، صافي ميل التدفق آخر 30د) من آخر لقطة للمراقب اليوم بعمر ≤ FLOW_MAX_AGE؛ وإلا None."""
    day, hm = n.date().isoformat(), n.strftime("%H:%M")
    if fl is None or not len(fl):
        return None
    g = fl[(fl["date"] == day) & (fl["time"] <= hm)].sort_values("time")
    if not len(g):
        return None
    last = g.iloc[-1]
    t_last = datetime.combine(n.date(), dtime.fromisoformat(last["time"]), NY)
    if (n - t_last).total_seconds() > FLOW_MAX_AGE * 60:
        return None
    t30 = (n - timedelta(minutes=30)).strftime("%H:%M")
    w = g[g["time"] > t30]
    lean = float(pd.to_numeric(w["call_prem"], errors="coerce").fillna(0).sum() - pd.to_numeric(w["put_prem"], errors="coerce").fillna(0).sum())
    return {"gex": float(last["net_gex_m"]), "lean": lean, "call_wall": pd.to_numeric(last["call_wall"], errors="coerce"),
            "put_wall": pd.to_numeric(last["put_wall"], errors="coerce")}


def last_complete_bar(ix, n):
    """آخر شمعة 5د اكتملت قبل n في جلسة اليوم (سلسلة OHLC) أو None."""
    day = ix[ix.index.date == n.date()]
    done = day[[(t + timedelta(minutes=5)).to_pydatetime() <= n for t in day.index]]
    return done.iloc[-1] if len(done) else None


def detect(bar, res, sup):
    """[(الجهة، النوع، المستوى، الوسوم)] من شمعة واحدة ومستويات (سعر، [وسوم])."""
    o, h, l, c = (float(bar[k]) for k in ("Open", "High", "Low", "Close"))
    out = []
    for lvl, tags in sup:
        if l <= lvl * (1 + TOUCH) and c > lvl and c > o:
            out.append(("CALL", "ارتداد من دعم", lvl, tags))
    for lvl, tags in res:
        if h >= lvl * (1 - TOUCH) and c < lvl and c < o:
            out.append(("PUT", "رفض عند مقاومة", lvl, tags))
    return out


def allowed(sig, st, fs, n, halted):
    """سبب الرفض أو None."""
    side, _, lvl, _ = sig
    if halted:
        return "متوقفة (إيقاف مبكر)"
    if not START <= n.time() <= LAST_ENTRY:
        return "خارج نافذة الدخول"
    if len(st["sent"]) >= MAX_PER_DAY:
        return "الحد اليومي"
    if any(abs(float(s["level"]) - lvl) < 0.5 for s in st["sent"]):
        return "المستوى استُخدم"
    for s in st["sent"]:
        if s["side"] == side and (n - datetime.fromisoformat(s["time"])).total_seconds() < SAME_SIDE_MIN * 60:
            return "الجهة نفسها خلال 30د"
    if fs is None:
        return "لا قراءة حديثة للمراقب"
    if fs["gex"] <= 0:
        return "الغاما ليست موجبة"
    if fs["lean"] * (1 if side == "CALL" else -1) < 0:
        return "التدفق ضد الجهة"
    return None


def halt_check(log):
    """True إن تحقق شرط الإيقاف المبكر على التوصيات المحسومة."""
    done = log[log["status"].isin(["هدف", "وقف", "إغلاق"])]
    r = pd.to_numeric(done["exit_ret"], errors="coerce").dropna().tolist()
    if len(r) >= HALT_N and np.mean(r) <= HALT_MEAN:
        return True
    streak = 0
    for x in r:
        streak = streak + 1 if x < 0 else 0
    return streak >= HALT_STREAK


def opposite_open(side, day, bot):
    """توصية البوت المعاكسة المفتوحة اليوم (وقت، سترايك) أو None — لملاحظة الشريعة الإعلامية."""
    if bot is None or not len(bot):
        return None
    opp = "PUT" if side == "CALL" else "CALL"
    g = bot[(bot["date"] == day) & (bot["side"] == opp) & (bot["result"] == "")]
    return (g.iloc[-1]["time"], g.iloc[-1]["strike"]) if len(g) else None


def entry_message(r, k, fs, opp):
    sd = "🟢 CALL" if r["side"] == "CALL" else "🔴 PUT"
    L = [f"🧪 **فرصة مستوى (تجريبية)** | {sd}",
         f"{r['kind']} **{float(r['level']):,.0f}** ({r['tags']}) | SPX {float(r['spot']):,.2f}",
         f"العقد: **SPXW {float(r['strike']):,.0f} {r['side']}** اليوم | دخول ≈ ${float(r['entry']):.2f} (${float(r['entry']) * 100:,.0f})",
         f"🎯 هدف +50% (${float(r['entry']) * 1.5:.2f}) · 🛑 وقف −40% (${float(r['entry']) * 0.6:.2f}) · إغلاق 3:55",
         f"القراءة: غاما موجبة (يوم ارتداد) · ميل التدفق 30د: {'مع الجهة' if fs['lean'] else 'محايد'}"]
    if opp:
        L.append(f"ℹ️ للبوت توصية {('PUT' if r['side'] == 'CALL' else 'CALL')} مفتوحة على SPX ({opp[0]}، سترايك {opp[1]}). القرار لك.")
    L.append("⚠️ تجريبية (H-LEVELSIG): تُقاس منفصلة عن البوت، ولم تُختبر تاريخياً بهذا الفلتر. القرار قرارك.")
    return "\n".join(L)


def exit_message(r, status, ret):
    icon = {"هدف": "✅", "وقف": "🛑", "إغلاق": "⏹️"}[status]
    return (f"{icon} **فرصة المستوى {r['time']}** ({r['side']} {float(r['strike']):,.0f}): {status} "
            f"{ret * 100:+.0f}% | ${float(r['entry']):.2f} ← ${float(r['entry']) * (1 + ret):.2f}")


# ====================== الشبكة ======================
def market(n):
    """(شموع القرار، حقول الشموع، VIX، VWAP بمستوى SPX أو None)."""
    import yfinance as yf
    import options_bot as ob
    ix = yf.Ticker(ob.SYMBOL).history(period="5d", interval="5m")
    if ix.empty:
        return None
    ix_y = ix
    ix, _, _, _ = ob._live_bars(ix, n)
    bf = ob.bar_fields(ix, n)
    vwap = None
    try:
        fl = yf.Ticker(ob.FLOW_SYMBOL).history(period="1d", interval="5m")
        if fl["Volume"].sum() > 0:
            tp = (fl["High"] + fl["Low"] + fl["Close"]) / 3
            v = float((tp * fl["Volume"]).cumsum().iloc[-1] / fl["Volume"].cumsum().iloc[-1])
            vwap = v * float(ix_y["Close"].dropna().iloc[-1]) / float(fl["Close"].iloc[-1])
    except Exception:  # noqa: BLE001
        pass
    vix, _ = ob.vix_info()
    return ix, bf, vix, vwap


def plan_levels(bf, vix, vwap, fs):
    import options_bot as ob
    c = {"S": bf["S"], "vix": vix, "prev_close": bf["prev_close"], "pd_hl": bf["pd_hl"], "orb": bf["orb"], "vwap_spx": vwap,
         "day_hl": bf["day_hl"], "pivots": bf["pivots"], "range1h": bf["range1h"], "max_pain": None, "map": None}
    if fs and not pd.isna(fs["call_wall"]) and not pd.isna(fs["put_wall"]):
        c["map"] = {"call_wall": (float(fs["call_wall"]), 0), "put_wall": (float(fs["put_wall"]), 0)}
    p = ob.build_plan(c, 0, 0)
    return p["res"], p["sup"]


def pick(side, n, vix):
    import yfinance as yf
    import options_bot as ob
    import live_quotes as lq
    S, _ = lq.spot("SPX")
    if not S:
        return None, None
    day = n.date().isoformat()
    t = yf.Ticker(ob.SYMBOL)
    if day not in list(t.options):
        return None, S
    ch = t.option_chain(day)
    T = max((n.replace(hour=16, minute=0, second=0, microsecond=0) - n).total_seconds() / 3600, 0.25) / (24 * 365)
    df = ob._live_chain(ob.spxw_rows(ch.calls if side == "CALL" else ch.puts), S)
    ks = ob.pick_contracts(side, df, S, T, vix)
    return (ks[0] if ks else None), S


def quote_mid(occ):
    import live_quotes as lq
    q = lq.chain_quotes([occ]).get(occ)
    if not q:
        return None
    z = q[0]
    return (z["bid"] + z["ask"]) / 2


def send(text, no_send):
    if no_send:
        print(text)
        return
    import analyst as an
    an.send(text, src="level_signals")


# ====================== التشغيل ======================
def track(n, log, no_send, quote=quote_mid):
    """يقيّم التوصيات المفتوحة اليوم: هدف/وقف، وإغلاق عند 15:55."""
    day = n.date().isoformat()
    for i, r in log[(log["date"] == day) & (log["status"] == "مفتوحة")].iterrows():
        mid = quote(r["occ"])
        if mid is None:
            continue
        ret = mid / float(r["entry"]) - 1
        log.at[i, "last_ret"] = f"{ret:.4f}"
        status = "هدف" if ret >= TP else "وقف" if ret <= SL else "إغلاق" if n.time() >= CLOSE_AT else None
        if status:
            log.loc[i, ["status", "exit_time", "exit_ret"]] = [status, n.strftime("%H:%M"), f"{ret:.4f}"]
            send(exit_message(r, status, ret), no_send)
    return log


def scan(n, st, log, no_send):
    fl = pd.read_csv(FLOW_LOG, dtype=str, keep_default_na=False) if os.path.exists(FLOW_LOG) else None
    fs = flow_state(fl, n)
    m = market(n)
    if m is None:
        print("لا شموع.")
        return log
    ix, bf, vix, vwap = m
    bar = last_complete_bar(ix, n)
    if bar is None:
        return log
    res, sup = plan_levels(bf, vix, vwap, fs)
    bot = pd.read_csv(BOT_LOG, dtype=str, keep_default_na=False) if os.path.exists(BOT_LOG) else None
    for sig in detect(bar, res, sup):
        why = allowed(sig, st, fs, n, st.get("halted"))
        if why:
            print("رُفضت:", sig[0], round(sig[2], 1), why)
            continue
        side, kind, lvl, tags = sig
        k, S = pick(side, n, vix)
        if not k:
            print("لا عقد مناسب:", side, round(lvl, 1))
            continue
        import live_quotes as lq
        day = n.date().isoformat()
        r = {"id": f"{day}:{n.strftime('%H:%M')}:{side}", "date": day, "time": n.strftime("%H:%M"), "side": side, "kind": kind,
             "level": f"{lvl:.2f}", "tags": "، ".join(tags), "strike": f"{k['strike']:g}", "exp": day,
             "occ": lq.occ("SPXW", day, side, k["strike"]), "entry": f"{k['price']:.2f}", "spot": f"{S:.2f}",
             "gex": f"{fs['gex']:.0f}", "lean": f"{fs['lean']:.0f}", "status": "مفتوحة", "exit_time": "", "exit_ret": "", "last_ret": ""}
        log = pd.concat([log, pd.DataFrame([r])], ignore_index=True)
        st["sent"].append({"level": r["level"], "side": side, "time": n.isoformat()})
        send(entry_message(r, k, fs, opposite_open(side, day, bot)), no_send)
    return log


def main(argv):
    n = datetime.now(NY)
    no_send = "--no-send" in argv
    if "--force" not in argv and (n.weekday() >= 5 or not START <= n.time() <= END):
        print("خارج النافذة.")
        return
    day = n.date().isoformat()
    st, log = load_state(day), read_log()
    log = track(n, log, no_send)
    if not st.get("halted") and halt_check(log):
        st["halted"] = True
        send("⏸️ **توقفت فرص المستويات التجريبية** (شرط الإيقاف المبكر في H-LEVELSIG: متوسط ≤ −15% بعد 15 توصية أو 5 خسائر متتالية). "
             "لن تُرسل توصيات جديدة حتى تقرر.", no_send)
    log = scan(n, st, log, no_send)
    write_log(log)
    save_state(st)


if __name__ == "__main__":
    main(sys.argv[1:])
