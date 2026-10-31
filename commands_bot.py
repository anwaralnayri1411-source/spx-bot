"""أوامر تلجرام: اكتب للبوت ويرد خلال ثوانٍ أثناء ساعات التشغيل (--loop: انتظار طويل لرسائل تيليجرام 14 دقيقة،
ثم يخرج فيحفظ run_job الملفات ويعيد المؤقت تشغيله كل 15 دقيقة). بلا --loop: قراءة واحدة كما كان.
اختصارات: قائمة الأوامر في زر «/» (setMyCommands، بأسماء لاتينية) وأزرار ثابتة تحت خانة الكتابة، ورمز سهم وحده = /تحديث.
الأوامر:
  /تحديث NVDA   تحليل شركة + عقودك المفتوحة عليها + آخر توصية
  /حالة         كل العقود المفتوحة الآن
  /spx          حالة SPX وآخر إشارة
  /سعر NVDA 1.95 [عدد]   سعر دخولك الفعلي لعقد اشتريته (بعد ضغط «اشتريت»)
  /مساعدة       قائمة الأوامر
تقبل أيضاً: /update /status /help (وبدون الشرطة). تُقبل رسائلك أنت فقط (TELEGRAM_CHAT_ID).
البيانات من yfinance المجاني ومتأخرة نحو 15 دقيقة."""
import copy
import json
import os
import re
import sys
import time

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
           "لوحة": "board", "board": "board", "تقرير": "report", "report": "report",
           "spx": "spx", "سعر": "price", "price": "price", "help": "help", "مساعدة": "help", "start": "help", "مساعده": "help"}


LOOP_S = 14 * 60       # مدة التشغيل الواحد في وضع --loop (المؤقت كل 15 دقيقة)
POLL_S = 25            # انتظار تيليجرام الطويل: يرجع فور وصول رسالة
MENU = [("status", "العقود المفتوحة"), ("board", "لوحة العقود بصورة"), ("report", "تقرير SPX اليومي"),
        ("spx", "حالة SPX وآخر إشارة"), ("update", "تحليل شركة: /update NVDA"), ("help", "قائمة الأوامر")]
KEYBOARD = json.dumps({"keyboard": [["حالة", "لوحة", "تقرير"], ["spx", "مساعدة"]],
                       "resize_keyboard": True, "is_persistent": True}, ensure_ascii=False)
TICKER = re.compile(r"^\$?[A-Za-z]{1,5}$")


def set_menu():
    """قائمة الأوامر في زر «/» بتيليجرام (تيليجرام يقبل أسماء لاتينية فقط)."""
    if TOKEN:
        _tg("setMyCommands", {"commands": json.dumps([{"command": c, "description": d} for c, d in MENU], ensure_ascii=False)})


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"offset": 0}


def save_state(s):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f)


def fetch_updates(offset, wait=0):
    if not TOKEN:
        print("لا يوجد TELEGRAM_TOKEN.")
        return []
    try:
        r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                         params={"offset": offset, "timeout": wait, "allowed_updates": json.dumps(["message", "callback_query"])}, timeout=wait + 20)
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
    if cmd is None and len(parts) == 1 and TICKER.match(t):
        return "company", t.lstrip("$").upper()      # اختصار: رمز السهم وحده = /تحديث
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
            "• <code>/حالة</code> كل العقود المفتوحة (نص)\n"
            "• <code>/لوحة</code> العقود المفتوحة بصورة\n"
            "• <code>/تقرير</code> تقرير SPX اليومي بصورة\n"
            "• <code>/spx</code> حالة SPX وآخر إشارة\n"
            "• <code>/سعر NVDA 1.95</code> سعر دخولك الفعلي لعقد اشتريته (وعدد العقود اختياري: <code>/سعر NVDA 1.95 2</code>)\n\n"
            "🔘 تحت كل توصية شركات زرّان: <b>👀 مراقبة</b> أو <b>🛒 اشتريت</b>.\n"
            "💬 أو اكتب سؤالك بكلامك، مثل: <i>ابحث لي عن توصية في SPX</i> أو <i>ليش دخلت INTC؟</i> (المساعد يقرأ ويشرح فقط).\n"
            "⚡ اختصارات: الأزرار تحت خانة الكتابة، وقائمة زر «/»، واكتب رمز السهم وحده (مثل <code>NVDA</code>) لتحليله.\n"
            "⏱️ أرد خلال ثوانٍ في أي وقت.\n"
            "📉 البيانات مجانية ومتأخرة نحو 15 دقيقة.")


def company_text(tk, n):
    if not re.fullmatch(r"[A-Z.\-]{1,6}", tk or ""):
        return "اكتب رمز الشركة مثل: <code>/تحديث NVDA</code>"
    t = yf.Ticker(tk)
    df = t.history(period="6mo")
    if df is None or len(df) < 55:
        return f"لا أجد بيانات كافية للرمز {ob.esc(tk)}."
    df = sb.live_daily(df, tk, n)[0]          # شمعة اليوم بالسعر اللحظي كما في فحص البوت
    spy = sb.live_daily(yf.Ticker("SPY").history(period="6mo"), "SPY", n)[0]["Close"].astype(float)
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


def _tg(method, data):
    """نداء تليجرام. الفشل (مثل انتهاء صلاحية الضغطة) لا يوقف شيئاً."""
    try:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/{method}", data=data, timeout=15)
        if not r.ok:
            print(f"تليجرام {method}:", r.text[:160])
        return r
    except requests.RequestException as e:
        print("تعذر الاتصال بتليجرام:", e)
        return None


def kb_json(mode, base, day):
    """لوحة الأزرار بعد الاختيار: علامة ✅ على ما اخترته، ويمكنك تغييره بالضغط على الآخر."""
    w = "👀 مراقبة" + (" ✅" if mode == "watch" else "")
    b = "🛒 اشتريت" + (" ✅" if mode == "bought" else "")
    return json.dumps({"inline_keyboard": [[{"text": w, "callback_data": f"w|{base}|{day}"},
                                            {"text": b, "callback_data": f"b|{base}|{day}"}]]})


def handle_callback(cq, n):
    """ضغطة زر تحت توصية: تسجّل اختيارك في stocks_mine.json وتؤكد لك."""
    parts = (cq.get("data") or "").split("|")
    if len(parts) != 3 or parts[0] not in ("w", "b"):
        return
    mode = "watch" if parts[0] == "w" else "bought"
    base, day = parts[1], parts[2]
    bits = base.split(":")
    if len(bits) != 4:
        return
    tk, side, exp, strike = bits
    try:
        strike_f = float(strike)
    except ValueError:
        return
    items = sb.load_mine()
    key = sb.mine_key(base, day)
    old = items.get(key) or {}
    keep = mode == "bought" and old.get("mode") == "bought"        # الضغط المتكرر لا يمسح سعرك
    items[key] = {"mode": mode, "ticker": tk, "side": side, "exp": exp, "strike": strike_f, "day": day,
                  "time": n.isoformat(timespec="minutes"),
                  "price": old.get("price") if keep else None, "qty": old.get("qty") if keep else None}
    sb.save_mine(items)
    name = f"{ob.esc(tk)} {strike_f:,.1f} {side}"
    _tg("answerCallbackQuery", {"callback_query_id": cq.get("id"),
                                "text": "سُجّلت في مراقبتك" if mode == "watch" else "سُجّل شراؤك"})
    msg = cq.get("message") or {}
    if msg.get("message_id"):
        _tg("editMessageReplyMarkup", {"chat_id": CHAT_ID, "message_id": msg["message_id"],
                                       "reply_markup": kb_json(mode, base, day)})
    if mode == "watch":
        ob.send_telegram(f"👀 أضفتُ <b>{name}</b> إلى مراقبتك. سأعرضه في «مراقبتي» ضمن التقارير.")
        return
    bot_px = None
    try:
        for p in (sb.load_positions() or {"positions": []})["positions"]:
            if p["id"].split("@")[0] == base and p["time"][:10] == day:
                bot_px = p["entry"]
    except Exception:
        pass
    ref = f" سعر البوت <b>{bot_px:.2f}</b>." if bot_px else ""
    ob.send_telegram(f"🛒 سجّلتُ أنك اشتريت <b>{name}</b>.{ref}\n"
                     f"إن دخلت بسعر مختلف أرسل: <code>/سعر {ob.esc(tk)} 1.95</code> "
                     "(وعدد العقود اختياري: <code>/سعر " + ob.esc(tk) + " 1.95 2</code>). وإلا أعتمد سعر البوت.")


def price_text(text, n):
    """/سعر TICKER PRICE [QTY]: يحدّث سعر دخولك لآخر عقد اشتريته في هذا السهم."""
    bits = (text or "").split()[1:]
    if len(bits) < 2 or not re.fullmatch(r"[A-Za-z.\-]{1,6}", bits[0]):
        return "الصيغة: <code>/سعر NVDA 1.95</code> (وعدد العقود اختياري: <code>/سعر NVDA 1.95 2</code>)"
    tk = bits[0].upper()
    try:
        px = float(bits[1])
        qty = int(bits[2]) if len(bits) > 2 else None
    except ValueError:
        return "السعر والعدد أرقام فقط. مثال: <code>/سعر NVDA 1.95 2</code>"
    if px > 50:
        return (f"{px:g} يبدو قيمة العقد بالدولار. اكتب سعره للسهم الواحد: اقسمه على 100 "
                f"(مثلاً 195$ تُكتب <code>/سعر {ob.esc(tk)} 1.95</code>).")
    if not (0.01 <= px) or (qty is not None and not (1 <= qty <= 1000)):
        return "السعر أو العدد خارج المعقول. مثال صحيح: <code>/سعر NVDA 1.95 2</code>"
    items = sb.load_mine()
    mine = [(k, v) for k, v in items.items() if v.get("ticker") == tk and v.get("mode") == "bought"
            and v.get("exp", "") >= n.date().isoformat()]
    if not mine:
        return f"لا شراء مسجّل في {ob.esc(tk)}. اضغط «🛒 اشتريت» تحت توصيته أولاً."
    k, v = max(mine, key=lambda kv: kv[1].get("time", ""))
    v["price"] = px
    if qty is not None:
        v["qty"] = qty
    sb.save_mine(items)
    more = f" (لديك {len(mine)} عقود في هذا السهم، عدّلتُ آخرها)" if len(mine) > 1 else ""
    return (f"✅ سعر دخولك <b>{ob.esc(tk)} {v['strike']:,.1f} {v['side']}</b> = <b>{px:.2f}</b>"
            f"{f' × {qty}' if qty else ''}{more}. سيُحسب ربحك على هذا السعر في «عقودي المشتراة».")


def handle(cmd, arg, n):
    if cmd == "help":
        return help_text()
    if cmd == "status":
        return status_text(n)
    if cmd == "spx":
        return spx_text(n)
    if cmd == "board":
        P = sb.load_positions() or {"positions": []}
        rows = sb.live_rows(eval_positions(sb.active_positions(P, n), n))
        if not rows:
            return "لا توجد عقود مفتوحة الآن."
        try:
            return ("photo", sb.board_png(rows), "📋 <b>لوحة العقود</b> #حالة")
        except Exception as e:
            print("تعذر إنشاء اللوحة:", e)
            return sb.status_message(rows, n)
    if cmd == "report":
        try:
            png = ob.daily_report_png(n)
        except Exception as e:
            print("تعذر إنشاء التقرير:", e)
            return "تعذر إنشاء بطاقة التقرير الآن."
        return ("photo", png, "🧾 <b>تقرير SPX اليومي</b> #تقرير") if png else "لا توجد إشارات SPX اليوم."
    return company_text(arg, n)


def process(ups, st, n):
    for u in ups:
        st["offset"] = max(st.get("offset", 0), u["update_id"] + 1)
        cq = u.get("callback_query")
        if cq:
            cm = (cq.get("message") or {}).get("chat") or {}
            if str(cm.get("id")) != str(CHAT_ID):
                continue        # ضغطة من غيرك: نتجاهلها
            try:
                handle_callback(cq, n)
            except Exception as e:
                ob.send_telegram(f"⚠️ تعذر تسجيل اختيارك: {ob.esc(e)}")
            continue
        m = u.get("message") or {}
        if str((m.get("chat") or {}).get("id")) != str(CHAT_ID):
            continue            # نتجاهل أي شخص غيرك
        cmd, arg = parse(m.get("text"))
        if cmd is None:
            if (m.get("text") or "").strip():
                import assistant          # رسالة حرة: مساعد المحادثة (قراءة وشرح فقط، يرد عليك وحدك)
                assistant.handle_text(m["text"].strip(), n)
            continue
        try:
            out = price_text(m.get("text"), n) if cmd == "price" else handle(cmd, arg, n)
            if isinstance(out, tuple):
                ob.send_photo(out[1], out[2])
            elif cmd == "help":
                ob.log_message(out)
                _tg("sendMessage", {"chat_id": CHAT_ID, "text": out, "parse_mode": "HTML", "reply_markup": KEYBOARD})
            else:
                ob.send_telegram(out)
        except Exception as e:
            ob.send_telegram(f"⚠️ تعذر تنفيذ الأمر: {ob.esc(e)}")


def main(argv=()):
    loop = "--loop" in argv
    end = time.monotonic() + (LOOP_S if loop else 0)
    st = load_state()
    if loop:
        set_menu()
    while True:
        ups = fetch_updates(st.get("offset", 0), POLL_S if loop else 0)
        if ups:
            process(ups, st, ob.now_ny())
            save_state(st)
        if time.monotonic() + POLL_S > end:
            break


if __name__ == "__main__":
    main(sys.argv[1:])
