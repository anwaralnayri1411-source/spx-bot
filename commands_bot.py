"""أوامر تلجرام: اكتب للبوت وسيرد عند أول تشغيل مجدول (كل ~15 دقيقة أثناء الجلسة).
الأوامر:
  /تحديث NVDA   تحليل شركة + عقودك المفتوحة عليها + آخر توصية
  /حالة         كل العقود المفتوحة الآن
  /spx          حالة SPX وآخر إشارة
  /مساعدة       قائمة الأوامر
تقبل أيضاً: /update /status /help (وبدون الشرطة). تُقبل رسائلك أنت فقط (TELEGRAM_CHAT_ID).
البيانات من yfinance المجاني ومتأخرة نحو 15 دقيقة."""
import copy
import json
import os
import re

import pandas as pd
import requests
import yfinance as yf

import options_bot as ob
import stocks_bot as sb

STATE_FILE = "commands_state.json"
TOKEN = ob.TOKEN
CHAT_ID = ob.CHAT_ID
ALIASES = {"update": "company", "تحديث": "company", "شركة": "company", "company": "company",
           "status": "status", "حالة": "status", "positions": "status", "عقود": "status",
           "spx": "spx", "help": "help", "مساعدة": "help", "start": "help", "مساعده": "help"}


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"offset": 0}


def save_state(s):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f)


def fetch_updates(offset):
    if not TOKEN:
        print("لا يوجد TELEGRAM_TOKEN.")
        return []
    try:
        r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                         params={"offset": offset, "timeout": 0, "allowed_updates": json.dumps(["message"])}, timeout=20)
        data = r.json()
        return data.get("result", []) if data.get("ok") else []
    except Exception as e:
        print("تعذر قراءة الأوامر:", e)
        return []


def parse(text):
    """يرجع (الأمر، الوسيط) أو (None, None)."""
    t = (text or "").strip()
    if not t:
        return None, None
    parts = t.split()
    word = parts[0].lstrip("/").split("@")[0].lower()
    cmd = ALIASES.get(word)
    if cmd is None:
        return None, None
    arg = parts[1].upper() if len(parts) > 1 else None
    return cmd, arg


def eval_positions(plist, n):
    """يقيّم عقوداً مفتوحة على نسخ (لا يغيّر الحالة المحفوظة)."""
    rows, cache, tickers = [], {}, {}
    spy_spot = sb.spot_now(yf.Ticker("SPY")) if plist else None
    for p0 in plist:
        p = copy.deepcopy(p0)
        try:
            tk = p["ticker"]
            if tk not in tickers:
                t = yf.Ticker(tk)
                tickers[tk] = (t, sb.spot_now(t))
            t, spot = tickers[tk]
            q = sb.contract_quote(t, p, cache)
            if q is None:
                continue
            info, _ = sb.evaluate(p, n, q, spot, spy_spot)
            rows.append((p, info))
        except Exception as e:
            print("تعذر تقييم", p0.get("id"), e)
    return rows


def help_text():
    return ("🤖 <b>أوامر البوت</b>\n"
            "• <code>/تحديث NVDA</code> تحليل الشركة وعقودك المفتوحة عليها\n"
            "• <code>/حالة</code> كل العقود المفتوحة\n"
            "• <code>/spx</code> حالة SPX وآخر إشارة\n\n"
            "⏱️ أرد عند أول فحص مجدول، وقد يتأخر 15 دقيقة أو أكثر.\n"
            "📉 البيانات مجانية ومتأخرة نحو 15 دقيقة.")


def company_text(tk, n):
    if not re.fullmatch(r"[A-Z.\-]{1,6}", tk or ""):
        return "اكتب رمز الشركة مثل: <code>/تحديث NVDA</code>"
    t = yf.Ticker(tk)
    df = t.history(period="6mo")
    if df is None or len(df) < 55:
        return f"لا أجد بيانات كافية للرمز {ob.esc(tk)}."
    spy = yf.Ticker("SPY").history(period="6mo")["Close"].astype(float)
    spy5 = float(spy.iloc[-1] / spy.iloc[-6] - 1)
    drv = None
    bull, bear, why, info = sb.tech_points(df, spy5, drv, sb.completed_volume(df, n))
    S = sb.spot_now(t) or info["S"]
    c = df["Close"].astype(float)
    day = float(c.iloc[-1] / c.iloc[-2] - 1)
    edge = bull - bear
    lean = "صعود 🟢" if edge >= 2 else ("هبوط 🔴" if edge <= -2 else "محايد ⚪")
    L = [f"🏢 <b>{ob.esc(tk)}</b> | السعر ≈ {S:,.2f} | اليوم {day * 100:+.1f}%",
         f"🧭 الميل الفني: <b>{lean}</b> (صعود {bull:.1f} / هبوط {bear:.1f})",
         f"📐 متوسط 20/50: {info['sma20']:.1f} / {info['sma50']:.1f} | RSI {info['rsi']:.0f} | تذبذب 20 يوم {info['rv'] * 100:.0f}%"]
    for side, txt in why[:5]:
        L.append(f"{'🟢' if side == 'bull' else '🔴'} {ob.esc(txt)}")
    P = sb.load_positions() or {"positions": []}
    mine = [p for p in sb.active_positions(P, n) if p["ticker"] == tk]
    if mine:
        L += ["", "📌 <b>عقودك المفتوحة عليها</b>"]
        for p, inf in eval_positions(mine, n):
            tag = " (بلغ الوقف)" if p["status"] == "stopped" else (" (بلغ الهدف)" if p["status"] == "tp" else "")
            L.append(f"{p['side']} {p['strike']:,.1f} | {sb.date_ar(p['exp'])}: ${p['entry'] * 100:,.0f} ← "
                     f"${inf['val'] * 100:,.0f} (<b>{inf['pnl'] * 100:+.0f}%</b>){tag}")
    try:
        lg = pd.read_csv(sb.LOG_FILE, dtype=str).fillna("")
        last = lg[lg["ticker"] == tk].tail(2)
        if len(last):
            L += ["", "🗂️ <b>آخر توصيات البوت لها</b>"]
            for _, r in last.iterrows():
                L.append(f"{r['time'][:10]} | {r['side']} {r['strike']} {r['exp']} | دخول {r['price']}")
    except Exception:
        pass
    L += ["", "⚠️ <i>تعليمي وليست توصية. البيانات متأخرة ~15 دقيقة.</i>"]
    return "\n".join(L)


def status_text(n):
    P = sb.load_positions() or {"positions": []}
    act = sb.active_positions(P, n)
    if not act:
        return "📋 <b>الشركات — حالة العقود المفتوحة</b>\nلا توجد عقود مفتوحة الآن."
    rows = eval_positions(act, n)
    if not rows:
        return "تعذر جلب أسعار العقود الآن (قد يكون السوق مغلقاً)."
    return sb.status_message(rows, n)


def spx_text(n):
    L = ["🏛️ <b>SPX الآن</b>"]
    try:
        h = yf.Ticker("^SPX").history(period="5d", interval="5m")["Close"].dropna()
        d = yf.Ticker("^SPX").history(period="7d")["Close"].dropna()
        prev = float(d[[x.date() < n.date() for x in d.index]].iloc[-1])
        L.append(f"📍 {float(h.iloc[-1]):,.1f} ({(float(h.iloc[-1]) / prev - 1) * 100:+.2f}% عن إغلاق أمس)")
    except Exception:
        L.append("تعذر قراءة سعر SPX.")
    vix, pct = ob.vix_info()
    if vix is not None:
        L.append(f"🌡️ VIX {vix:.1f} (أعلى من {pct:.0f}% من أيام السنة)")
    try:
        with open(ob.STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
        if st.get("date") == n.date().isoformat():
            L.append(f"📊 اليوم: {st.get('runs', 0)} فحص | {st.get('signals', 0)} إشارة | آخر اتجاه: {st.get('last_side') or '-'}")
    except Exception:
        pass
    try:
        lg = pd.read_csv(ob.LOG_FILE, dtype=str).fillna("")
        if len(lg):
            r = lg.iloc[-1]
            L.append(f"🗂️ آخر إشارة: {r['date']} {r['time']} | {r['symbol']} {r['side']} {r['strike']} | دخول {r['price']} | {r['result'] or 'مفتوحة'}")
    except Exception:
        pass
    L += ["", "⚠️ <i>تعليمي وليست توصية. البيانات متأخرة ~15 دقيقة.</i>"]
    return "\n".join(L)


def handle(cmd, arg, n):
    if cmd == "help":
        return help_text()
    if cmd == "status":
        return status_text(n)
    if cmd == "spx":
        return spx_text(n)
    return company_text(arg, n)


def main():
    st = load_state()
    ups = fetch_updates(st.get("offset", 0))
    if not ups:
        return
    n = ob.now_ny()
    for u in ups:
        st["offset"] = max(st.get("offset", 0), u["update_id"] + 1)
        m = u.get("message") or {}
        if str((m.get("chat") or {}).get("id")) != str(CHAT_ID):
            continue            # نتجاهل أي شخص غيرك
        cmd, arg = parse(m.get("text"))
        if cmd is None:
            continue
        try:
            ob.send_telegram(handle(cmd, arg, n))
        except Exception as e:
            ob.send_telegram(f"⚠️ تعذر تنفيذ الأمر: {ob.esc(e)}")
    save_state(st)


if __name__ == "__main__":
    main()
