"""بوت BTC على OKX: قاعدة متوسط 50 يوماً (سبوت فقط، بلا رافعة). مشروع منفصل عن SPX والشركات.

القاعدة (من crypto/trend_study_results.md، قاعدة T50): عند إغلاق اليوم UTC، إن كان إغلاق BTC-USDT
فوق متوسط آخر 50 إغلاقاً → داخل السوق (BTC)، وإلا → نقد (USDT). الهدف حماية رأس المال من الانهيارات
لا التفوق على الاحتفاظ (لم تثبت ميزة توقيت: p = 0.13 مقابل توقيت عشوائي).

الأمان:
- OKX_MODE = demo (الافتراضي، رأس x-simulated-trading: 1) أو live، والحي يحتاج أيضاً OKX_LIVE_CONFIRM=yes.
- البوت يدير «حصة» خاصة به فقط: OKX_MAX_USD دولار (الافتراضي 1000)، ويتتبع كمية BTC التي اشتراها هو،
  فلا يلمس أي رصيد آخر في الحساب ولا ما تشتريه يدوياً.
- إيقاف آلي: إن نزلت قيمة الحصة تحت (رأس المال − OKX_MAX_LOSS_USD) يبيع ويتوقف حتى تعيد تشغيله
  بحذف halted من crypto/okx_trend_state.json.
- بلا مفاتيح: يحسب الإشارة ويسجّلها فقط.
- الرسائل إلى TELEGRAM_CHAT_ID وحده (دالة إرسال خاصة بهذا الملف).
"""
import base64
import csv
import hashlib
import hmac
import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN

import requests

BASE = "https://www.okx.com"
INST = "BTC-USDT"
SMA_N = 50
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "okx_trend_state.json")
LOG = os.path.join(HERE, "okx_trend_log.csv")
MSGS = os.path.join(HERE, "messages_crypto_bot.jsonl")
LOG_COLS = ["run_utc", "day", "mode", "close", "sma50", "signal", "action", "status",
            "fill_px", "fill_sz", "fee", "bot_btc", "bot_usdt", "equity", "bh_equity", "note"]


# ---------- إعدادات ----------
def config(env=os.environ):
    mode = env.get("OKX_MODE", "demo").strip().lower()
    if mode not in ("demo", "live"):
        mode = "demo"
    if mode == "live" and env.get("OKX_LIVE_CONFIRM", "") != "yes":
        mode = "demo"
    return dict(
        mode=mode,
        key=env.get("OKX_API_KEY", ""),
        secret=env.get("OKX_API_SECRET", ""),
        passphrase=env.get("OKX_API_PASSPHRASE", ""),
        max_usd=float(env.get("OKX_MAX_USD", "1000") or 1000),
        max_loss=float(env.get("OKX_MAX_LOSS_USD", "300") or 300),
    )


# ---------- الإشارة ----------
def daily_closes(get=requests.get):
    """إغلاقات يومية UTC مؤكدة (اليوم الجاري مستبعد)، الأقدم أولاً: [(يوم، إغلاق)]."""
    r = get(f"{BASE}/api/v5/market/candles", params={"instId": INST, "bar": "1Dutc", "limit": "300"},
            timeout=20)
    d = r.json()
    if d.get("code") != "0":
        raise RuntimeError(f"candles: {d.get('msg')}")
    rows = [x for x in d["data"] if str(x[8]) == "1"]
    out = [(datetime.fromtimestamp(int(x[0]) / 1000, timezone.utc).date().isoformat(), float(x[4]))
           for x in rows]
    return sorted(out)


def signal(closes):
    """1 = داخل السوق، 0 = نقد. يحتاج 50 إغلاقاً على الأقل."""
    if len(closes) < SMA_N:
        raise ValueError("not enough closes")
    last = closes[-1][1]
    sma = sum(c for _, c in closes[-SMA_N:]) / SMA_N
    return (1 if last > sma else 0), last, sma


def decide(sig, st, min_sz):
    """buy / sell / hold بحسب الإشارة وحصة البوت."""
    if st.get("halted"):
        return "hold"
    if sig == 1 and st["bot_usdt"] >= 10:
        return "buy"
    if sig == 0 and st["bot_btc"] >= min_sz:
        return "sell"
    return "hold"


# ---------- OKX الخاص ----------
class Okx:
    def __init__(self, cfg, session=None):
        self.cfg, self.s = cfg, session or requests.Session()

    def headers(self, method, path, body=""):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
            f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"
        sign = base64.b64encode(hmac.new(self.cfg["secret"].encode(), (ts + method + path + body).encode(),
                                         hashlib.sha256).digest()).decode()
        h = {"OK-ACCESS-KEY": self.cfg["key"], "OK-ACCESS-SIGN": sign, "OK-ACCESS-TIMESTAMP": ts,
             "OK-ACCESS-PASSPHRASE": self.cfg["passphrase"], "Content-Type": "application/json"}
        if self.cfg["mode"] == "demo":
            h["x-simulated-trading"] = "1"
        return h

    def req(self, method, path, body=None):
        b = json.dumps(body) if body is not None else ""
        r = self.s.request(method, BASE + path, headers=self.headers(method, path, b), data=b or None,
                           timeout=20)
        d = r.json()
        if d.get("code") != "0":
            detail = (d.get("data") or [{}])[0].get("sMsg", "") if isinstance(d.get("data"), list) else ""
            raise RuntimeError(f"OKX {path.split('?')[0]}: {d.get('code')} {d.get('msg')} {detail}".strip())
        return d["data"]

    def balances(self):
        data = self.req("GET", "/api/v5/account/balance?ccy=BTC,USDT")
        out = {"BTC": 0.0, "USDT": 0.0}
        for det in (data[0].get("details", []) if data else []):
            if det.get("ccy") in out:
                out[det["ccy"]] = float(det.get("availBal") or 0)
        return out

    def instrument(self):
        r = self.s.get(BASE + "/api/v5/public/instruments", params={"instType": "SPOT", "instId": INST},
                       timeout=20).json()
        i = r["data"][0]
        return float(i["minSz"]), i["lotSz"]

    def market(self, side, sz, on_placed=None):
        body = {"instId": INST, "tdMode": "cash", "side": side, "ordType": "market", "sz": sz}
        if side == "buy":
            body["tgtCcy"] = "quote_ccy"   # sz بالدولار
        oid = self.req("POST", "/api/v5/trade/order", body)[0]["ordId"]
        if on_placed:
            on_placed(oid)
        return self.wait(oid)

    def order(self, oid):
        return self.req("GET", f"/api/v5/trade/order?instId={INST}&ordId={oid}")[0]

    def wait(self, oid):
        for _ in range(15):
            time.sleep(1)
            o = self.order(oid)
            if o.get("state") == "filled":
                return o
            if o.get("state") == "canceled":
                break
        raise RuntimeError(f"order {oid} not filled")


def floor_to(x, step):
    return str(Decimal(str(x)).quantize(Decimal(step), rounding=ROUND_DOWN))


def apply_fill(st, side, o):
    """يحدّث حصة البوت من تنفيذ الأمر (العمولة سالبة بعملة الاستلام في OKX)."""
    px, sz = float(o["avgPx"]), float(o["accFillSz"])
    fee, fccy = float(o.get("fee") or 0), o.get("feeCcy", "")
    if side == "buy":
        spent = px * sz
        st["bot_usdt"] = max(0.0, st["bot_usdt"] - spent)
        st["bot_btc"] += sz + (fee if fccy == "BTC" else 0)
        if fccy == "USDT":
            st["bot_usdt"] = max(0.0, st["bot_usdt"] + fee)
    else:
        st["bot_btc"] = max(0.0, st["bot_btc"] - sz)
        st["bot_usdt"] += px * sz + (fee if fccy == "USDT" else 0)
    return px, sz, fee


# ---------- حالة وسجل وإرسال ----------
def load_state(cfg):
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {}
    if st.get("mode") != cfg["mode"]:   # تبديل demo/live يبدأ حصة جديدة
        st = {"mode": cfg["mode"], "bot_usdt": cfg["max_usd"], "bot_btc": 0.0, "start_usd": cfg["max_usd"],
              "start_px": None, "last_day": None}
    return st


def save_state(st):
    json.dump(st, open(STATE, "w"), ensure_ascii=False, indent=1)


def log_row(row):
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in LOG_COLS})


def send(text):
    with open(MSGS, "a") as f:
        f.write(json.dumps({"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "text": text},
                           ensure_ascii=False) + "\n")
    tok, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        print("[no telegram]\n" + text)
        return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      data={"chat_id": chat, "text": text}, timeout=20)
    except Exception as e:  # noqa: BLE001
        print("telegram failed:", type(e).__name__)


# ---------- التشغيل ----------
def run(cfg=None, okx=None, closes=None, now=None):
    cfg = cfg or config()
    now = now or datetime.now(timezone.utc)
    st = load_state(cfg)
    closes = closes if closes is not None else daily_closes()
    sig, close, sma = signal(closes)
    day = closes[-1][0]
    tag = "🧪 تجريبي" if cfg["mode"] == "demo" else "💵 حقيقي"
    row = dict(run_utc=now.isoformat(timespec="seconds"), day=day, mode=cfg["mode"], close=round(close, 2),
               sma50=round(sma, 2), signal=sig)

    if st.get("last_day") == day:
        print(f"already ran for {day}")
        return "skip"
    if st.get("start_px") is None:
        st["start_px"] = close

    if not (cfg["key"] and cfg["secret"] and cfg["passphrase"]):
        row.update(action="none", status="no_keys")
        log_row(row)
        st["last_day"] = day
        save_state(st)
        print(f"no keys: signal={sig} close={close:.0f} sma50={sma:.0f}")
        return "no_keys"

    okx = okx or Okx(cfg)
    action, status, note, px = "hold", "ok", "", None

    def placed(side):
        def f(oid):   # يُحفظ قبل انتظار التنفيذ: إن انقطع التشغيل لا يُعاد الأمر مرتين
            st["pending"] = {"oid": oid, "side": side}
            save_state(st)
        return f

    try:
        if st.get("pending"):
            p = st["pending"]
            o = okx.order(p["oid"])
            if o.get("state") in ("filled", "partially_filled", "canceled") and float(o.get("accFillSz") or 0) > 0:
                apply_fill(st, p["side"], o)
                note = f"سابق: {p['side']} {o.get('accFillSz')}"
            if o.get("state") in ("filled", "canceled", "mmp_canceled"):
                st.pop("pending")
            else:
                raise RuntimeError(f"pending order {p['oid']} state {o.get('state')}")
        min_sz, lot = okx.instrument()
        action = decide(sig, st, min_sz)
        bal = okx.balances()
        if action == "buy":
            usd = min(st["bot_usdt"], bal["USDT"]) * 0.995   # هامش للعمولة والتقريب
            if usd < 10:
                action, status, note = "hold", "no_funds", f"USDT المتاح {bal['USDT']:.2f}"
            else:
                o = okx.market("buy", f"{usd:.2f}", placed("buy"))
                st.pop("pending", None)
                px, sz, fee = apply_fill(st, "buy", o)
                row.update(fill_px=px, fill_sz=sz, fee=fee)
        elif action == "sell":
            sz = floor_to(min(st["bot_btc"], bal["BTC"]), lot)
            if float(sz) < min_sz:
                action, status, note = "hold", "dust", sz
            else:
                o = okx.market("sell", sz, placed("sell"))
                st.pop("pending", None)
                px, sz2, fee = apply_fill(st, "sell", o)
                row.update(fill_px=px, fill_sz=sz2, fee=fee)
    except Exception as e:  # noqa: BLE001
        status, note = "error", str(e)[:200]

    equity = st["bot_usdt"] + st["bot_btc"] * close
    bh = st["start_usd"] * close / st["start_px"]
    if status != "error" and not st.get("halted") and equity < st["start_usd"] - cfg["max_loss"]:
        st["halted"] = now.isoformat(timespec="seconds")
        note = (note + " " if note else "") + "halted: max loss"
        if st["bot_btc"] > 0:
            try:
                min_sz, lot = okx.instrument()
                sz = floor_to(st["bot_btc"], lot)
                if float(sz) >= min_sz:
                    apply_fill(st, "sell", okx.market("sell", sz, placed("sell")))
                    st.pop("pending", None)
                    action = "sell_halt"
            except Exception as e:  # noqa: BLE001
                note += f" sell failed: {str(e)[:100]}"
        equity = st["bot_usdt"] + st["bot_btc"] * close

    row.update(action=action, status=status, note=note, bot_btc=round(st["bot_btc"], 8),
               bot_usdt=round(st["bot_usdt"], 2), equity=round(equity, 2), bh_equity=round(bh, 2))
    log_row(row)
    if status != "error":
        st["last_day"] = day   # عند الخطأ يُعاد المحاولة في الموعد الاحتياطي
    save_state(st)

    pos = "داخل السوق (BTC)" if sig else "نقد (USDT)"
    head = {"buy": "🟢 شراء BTC", "sell": "🔴 بيع BTC → USDT", "sell_halt": "⛔ بيع وإيقاف"}.get(action)
    weekly = now.weekday() == 6
    if head or status == "error" or weekly:
        lines = [f"₿ بوت BTC · {tag}", head or ("⚠️ خطأ" if status == "error" else "📊 ملخص الأسبوع")]
        lines.append(f"إغلاق {day}: {close:,.0f}$ · متوسط 50: {sma:,.0f}$ → {pos}")
        if px:
            lines.append(f"التنفيذ: {px:,.0f}$")
        lines.append(f"الحصة: {equity:,.0f}$ (بدأت {st['start_usd']:,.0f}$) · لو احتفظت: {bh:,.0f}$")
        if note:
            lines.append(f"ملاحظة: {note}")
        if st.get("halted"):
            lines.append("البوت متوقف (حد الخسارة). لا أوامر حتى تعيد تشغيله.")
        lines.append("القاعدة تحمي من الانهيارات ولا تضمن ربحاً.")
        send("\n".join(lines))
    print(json.dumps(row, ensure_ascii=False))
    return status


if __name__ == "__main__":
    sys.exit(0 if run() in ("ok", "skip", "no_keys") else 1)
