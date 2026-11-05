"""بوت شبكة BTC-USDT على OKX — للتدريب على الحساب التجريبي (مشروع الكريبتو، منفصل عن SPX والشركات).

تنبيه صريح: الاختبار التاريخي (crypto/grid_study_results.md، H-CRYPTO-GRID) وجد الشبكة أضعف من محفظة
50/50 بكل إعداداتها. هذا البوت للتعلّم ورؤية الصفقات على مال وهمي، لا لأنه مربح.

كيف يعمل (سبوت، بلا رافعة):
- نطاق ±GRID_RANGE حول السعر عند البدء (الافتراضي ±10%) مقسوم هندسياً إلى GRID_CELLS خلية (20، ≈1% لكل خلية).
- لكل خلية [Lᵢ، Lᵢ₊₁] حصة متساوية من GRID_MAX_USD (الافتراضي 1000$). الخلايا فوق السعر تبدأ بـ BTC (شراء سوقي
  واحد) وعليها أمر بيع محدد عند Lᵢ₊₁؛ والتي تحته تبدأ بـ USDT وعليها أمر شراء محدد عند Lᵢ.
- كل تنفيذ يقلب الخلية: شراء نُفّذ ← أمر بيع عند الحد الأعلى؛ بيع نُفّذ ← ربح الدورة وأمر شراء عند الحد الأدنى.
- يفحص كل 5 دقائق. فوق النطاق 24 ساعة (كله USDT) ← يعيد البناء حول السعر الجديد. تحت النطاق ← يحتفظ بالـ BTC وينبّه مرة.
- GRID_ENABLED=0 في /etc/spxbot/env ← يلغي أوامره ويبيع BTC الخاص به ويتوقف؛ إعادته إلى 1 تبدأ شبكة جديدة.
- الحساب التجريبي فقط ما لم يُضبط OKX_MODE=live وOKX_LIVE_CONFIRM=yes **وGRID_LIVE=yes** معاً.
- يتتبّع أوامره بمعرّف خاص (clOrdId يبدأ بـ g) فلا يلمس أوامرك ولا أوامر بوت الاتجاه.
- الرسائل لـ TELEGRAM_CHAT_ID وحده: كل تنفيذ، وملخص يومي، والأخطاء (مرة في الساعة على الأكثر).
"""
import csv
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import okx_trend_bot as tb  # noqa: E402  (عميل OKX، IPv4، الإرسال)

INST = tb.INST
COIN = "BTC"
TAG = ""          # يُضاف لمعرّفات الأوامر لغير BTC (مثل sol) فلا تتداخل الشبكات
STATE = os.path.join(HERE, "grid_bot_state.json")
LOG = os.path.join(HERE, "grid_bot_log.csv")
MSGS = os.path.join(HERE, "messages_grid_bot.jsonl")


def setup(inst):
    """يضبط الزوج وملفاته: BTC-USDT بأسماء الملفات الأصلية، وغيره بلاحقة العملة (مثل grid_bot_state_SOL.json)."""
    global INST, COIN, TAG, STATE, LOG, MSGS
    INST = inst
    COIN = inst.split("-")[0]
    if COIN != "BTC":
        TAG = COIN.lower()
        STATE = os.path.join(HERE, f"grid_bot_state_{COIN}.json")
        LOG = os.path.join(HERE, f"grid_bot_log_{COIN}.csv")
        MSGS = os.path.join(HERE, f"messages_grid_bot_{COIN}.jsonl")


def fp(x):
    return f"{x:,.0f}" if x >= 1000 else f"{x:,.2f}"
LOG_COLS = ["t", "gen", "cell", "side", "px", "sz", "fee", "usd", "profit", "realized", "note"]
OUT_ABOVE_H = 24


def config(env=os.environ):
    """BTC يقرأ GRID_*؛ غيره يقرأ GRID_<COIN>_* أولاً (مثل GRID_SOL_RANGE) ثم القيم الافتراضية."""
    inst = env.get("GRID_INST", "BTC-USDT")
    coin = inst.split("-")[0]
    if coin != "BTC":
        env = dict(env)
        for k in ("MAX_USD", "RANGE", "CELLS", "ENABLED"):
            env.pop(f"GRID_{k}", None)
            if f"GRID_{coin}_{k}" in env:
                env[f"GRID_{k}"] = env[f"GRID_{coin}_{k}"]
    c = tb.config(env)
    c["inst"] = inst
    if c["mode"] == "live" and env.get("GRID_LIVE", "") != "yes":
        c["mode"] = "demo"
    c.update(max_usd=float(env.get("GRID_MAX_USD", "1000") or 1000),
             rng=float(env.get("GRID_RANGE", "0.10") or 0.10),
             cells=int(env.get("GRID_CELLS", "20") or 20),
             enabled=env.get("GRID_ENABLED", "1") != "0")
    return c


class GridOkx(tb.Okx):
    def ticker(self):
        r = self.s.get(tb.BASE + "/api/v5/market/ticker", params={"instId": INST}, timeout=20).json()
        return float(r["data"][0]["last"])

    def spec(self):
        r = self.s.get(tb.BASE + "/api/v5/public/instruments", params={"instType": "SPOT", "instId": INST},
                       timeout=20).json()["data"][0]
        return float(r["minSz"]), float(r["lotSz"]), float(r["tickSz"])

    def get_cl(self, cl):
        try:
            return self.req("GET", f"/api/v5/trade/order?instId={INST}&clOrdId={cl}")[0]
        except RuntimeError as e:
            if "51603" in str(e):   # الأمر غير موجود (لم يصل الإرسال)
                return None
            raise

    def place(self, cl, side, ordtype, sz, px=None, quote=False):
        body = {"instId": INST, "tdMode": "cash", "side": side, "ordType": ordtype, "sz": sz, "clOrdId": cl}
        if px is not None:
            body["px"] = px
        if quote:
            body["tgtCcy"] = "quote_ccy"
        return self.req("POST", "/api/v5/trade/order", body)[0]

    def cancel(self, cl):
        try:
            self.req("POST", "/api/v5/trade/cancel-order", {"instId": INST, "clOrdId": cl})
        except RuntimeError as e:
            if not any(c in str(e) for c in ("51400", "51401", "51402", "51603")):   # منفّذ/ملغى/غير موجود
                raise


# ---------- أدوات ----------
def fdown(x, step):
    return math.floor(x / step + 1e-9) * step


def fmt(x, step):
    d = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return f"{x:.{d}f}"


def levels(p0, r, n):
    lo, hi = p0 * (1 - r), p0 * (1 + r)
    return [lo * (hi / lo) ** (i / n) for i in range(n + 1)]


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return None


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
    tb.MSGS, old = MSGS, tb.MSGS
    try:
        tb.send(text)
    finally:
        tb.MSGS = old


def equity(st, px):
    return sum(c["usdt"] + c["btc"] * px for c in st["cells"])


# ---------- البناء ----------
def new_grid(cfg, okx, st_prev=None, now=None):
    now = now or datetime.now(timezone.utc)
    p0 = okx.ticker()
    lv = levels(p0, cfg["rng"], cfg["cells"])
    gen = (st_prev or {}).get("gen", 0) + 1
    u = cfg["max_usd"] / cfg["cells"]
    if st_prev:   # إعادة بناء: رأس المال = قيمة الشبكة السابقة
        u = equity(st_prev, p0) / cfg["cells"]
    cells = []
    for i in range(cfg["cells"]):
        sell = lv[i] >= p0
        cells.append(dict(i=i, lo=lv[i], hi=lv[i + 1], side="sell" if sell else "buy", usdt=u, btc=0.0,
                          cost=0.0, n=0, cl=None))
    st = dict(mode=cfg["mode"], gen=gen, center=p0, built=now.isoformat(timespec="seconds"),
              start_usd=(st_prev or {}).get("start_usd", cfg["max_usd"]),
              start_px=(st_prev or {}).get("start_px", p0), cells=cells,
              realized=(st_prev or {}).get("realized", 0.0), fills=(st_prev or {}).get("fills", 0),
              init_buy=None, out_above=None, below_alerted=False, last_err=None,
              last_daily=(st_prev or {}).get("last_daily"), stopped=False, rng=cfg["rng"], ncells=cfg["cells"])
    k = sum(1 for c in cells if c["side"] == "sell")
    if k:
        st["init_buy"] = {"cl": f"g{TAG}{gen}init", "usd": round(u * k, 2), "done": False}
    save_state(st)
    return st


def finish_init_buy(st, okx):
    ib = st.get("init_buy")
    if not ib or ib["done"]:
        return True
    o = okx.get_cl(ib["cl"])
    if o is None:
        okx.place(ib["cl"], "buy", "market", f"{ib['usd']:.2f}", quote=True)
        for _ in range(15):
            time.sleep(1)
            o = okx.get_cl(ib["cl"])
            if o and o.get("state") == "filled":
                break
    if not o or o.get("state") != "filled":
        return False
    px, sz = float(o["avgPx"]), float(o["accFillSz"])
    fee = float(o.get("fee") or 0) if o.get("feeCcy") == COIN else 0.0
    net = sz + fee
    sells = [c for c in st["cells"] if c["side"] == "sell"]
    for c in sells:
        c["btc"] = net / len(sells)
        c["usdt"] = 0.0
        c["cost"] = px * sz / len(sells)
    ib["done"] = True
    ib["px"] = px
    save_state(st)
    return True


def ensure_order(st, okx, c, spec):
    """يضمن وجود أمر الخلية: يُحفظ المعرّف قبل الإرسال فلا يتكرر الأمر."""
    min_sz, lot, tick = spec
    if c["side"] == "buy":
        px = fdown(c["lo"], tick)
        sz = fdown(c["usdt"] / px, lot) if px > 0 else 0
    else:
        px = math.ceil(c["hi"] / tick - 1e-9) * tick
        sz = fdown(c["btc"], lot)
    if sz < min_sz:
        c["cl"] = None
        return
    if not c.get("cl"):
        c["n"] += 1
        c["cl"] = f"g{TAG}{st['gen']}c{c['i']}n{c['n']}"
        save_state(st)
    if okx.get_cl(c["cl"]) is None:
        okx.place(c["cl"], c["side"], "limit", fmt(sz, lot), px=fmt(px, tick))


def apply_fill(st, c, o, now):
    """يقلب الخلية بعد التنفيذ ويعيد سطر السجل."""
    px, sz = float(o["avgPx"] or 0), float(o["accFillSz"] or 0)
    fee, fccy = float(o.get("fee") or 0), o.get("feeCcy", "")
    row = dict(t=now.isoformat(timespec="seconds"), gen=st["gen"], cell=c["i"], side=c["side"], px=px, sz=sz,
               fee=fee)
    if c["side"] == "buy":
        spent = px * sz
        c["usdt"] -= spent
        c["btc"] += sz + (fee if fccy == COIN else 0)
        c["cost"] = spent
        c["side"] = "sell"
        row["usd"] = round(-spent, 2)
    else:
        got = px * sz + (fee if fccy == "USDT" else 0)
        c["btc"] = max(0.0, c["btc"] - sz)
        c["usdt"] += got
        profit = got - c["cost"]
        c["cost"] = 0.0
        c["side"] = "buy"
        st["realized"] += profit
        row.update(usd=round(got, 2), profit=round(profit, 4))
    st["fills"] += 1
    row["realized"] = round(st["realized"], 4)
    c["cl"] = None
    return row


def stop_grid(st, okx, spec, note):
    for c in st["cells"]:
        if c.get("cl"):
            okx.cancel(c["cl"])
            o = okx.get_cl(c["cl"])
            if o and float(o.get("accFillSz") or 0) > 0:
                apply_fill(st, c, o, datetime.now(timezone.utc))
            c["cl"] = None
    btc = fdown(sum(c["btc"] for c in st["cells"]), spec[1])
    if btc >= spec[0]:
        cl = f"g{TAG}{st['gen']}stop"
        if okx.get_cl(cl) is None:
            okx.place(cl, "sell", "market", fmt(btc, spec[1]))
        for _ in range(15):
            time.sleep(1)
            o = okx.get_cl(cl)
            if o and o.get("state") == "filled":
                got = float(o["avgPx"]) * float(o["accFillSz"]) + (
                    float(o.get("fee") or 0) if o.get("feeCcy") == "USDT" else 0)
                total = sum(c["btc"] for c in st["cells"])
                for c in st["cells"]:
                    share = c["btc"] / total if total else 0
                    c["usdt"] += got * share
                    st["realized"] += got * share - c["cost"]
                    c["btc"], c["cost"] = 0.0, 0.0
                break
    st["stopped"] = note
    save_state(st)


# ---------- التشغيل ----------
def run(cfg=None, okx=None, now=None):
    cfg = cfg or config()
    now = now or datetime.now(timezone.utc)
    if not (cfg["key"] and cfg["secret"] and cfg["passphrase"]):
        print("no keys")
        return "no_keys"
    okx = okx or GridOkx(cfg)
    st = load_state()
    if st and st.get("mode") != cfg["mode"]:
        st = None
    tag = "🧪 تجريبي" if cfg["mode"] == "demo" else "💵 حقيقي"
    msgs = []
    try:
        spec = okx.spec()
        if st and not st.get("stopped") and not cfg["enabled"]:
            stop_grid(st, okx, spec, "disabled")
            msgs.append(f"⏹ أوقفت الشبكة (GRID_ENABLED=0). الحصة الآن {equity(st, okx.ticker()):,.0f}$ نقداً.")
            return finish(st, msgs, tag, now, cfg, okx)
        if not cfg["enabled"]:
            print("disabled")
            return "disabled"
        if st is None or st.get("stopped"):
            st = new_grid(cfg, okx, st if st and st.get("stopped") else None, now)
            msgs.append(f"🧱 شبكة {COIN} جديدة حول {fp(st['center'])}$: {cfg['cells']} خلية من "
                        f"{fp(st['cells'][0]['lo'])} إلى {fp(st['cells'][-1]['hi'])}$ "
                        f"(كل خلية ≈{(st['cells'][0]['hi'] / st['cells'][0]['lo'] - 1) * 100:.1f}%).")
        if not finish_init_buy(st, okx):
            return finish(st, msgs, tag, now, cfg, okx, status="pending")

        rows = []
        for c in st["cells"]:
            if c.get("cl"):
                o = okx.get_cl(c["cl"])
                if o is None:
                    pass   # يُعاد إرساله أدناه
                elif o.get("state") == "filled" or (o.get("state") == "canceled"
                                                    and float(o.get("accFillSz") or 0) > 0):
                    rows.append(apply_fill(st, c, o, now))
                elif o.get("state") == "canceled":
                    c["cl"] = None   # أُلغي من خارج البوت ← يُعاد وضعه
                save_state(st)
            ensure_order(st, okx, c, spec)
        save_state(st)
        for r in rows:
            log_row(r)
            if r["side"] == "buy":
                msgs.append(f"🟢 شراء {r['sz']:.5g} {COIN} عند {fp(r['px'])}$ (خلية {r['cell'] + 1})")
            else:
                msgs.append(f"🔴 بيع {r['sz']:.5g} {COIN} عند {fp(r['px'])}$ (خلية {r['cell'] + 1}) "
                            f"· ربح الدورة {r['profit']:+.2f}$")

        px = okx.ticker()
        lo, hi = st["cells"][0]["lo"], st["cells"][-1]["hi"]
        if px > hi:
            st["out_above"] = st.get("out_above") or now.isoformat(timespec="seconds")
            hours = (now - datetime.fromisoformat(st["out_above"])).total_seconds() / 3600
            if hours >= OUT_ABOVE_H:
                stop_grid(st, okx, spec, "recenter")
                msgs.append(f"↗️ السعر فوق النطاق {OUT_ABOVE_H} ساعة ({fp(px)}$): أعيد بناء الشبكة حول السعر الجديد.")
        else:
            st["out_above"] = None
        if px < lo and not st.get("below_alerted"):
            st["below_alerted"] = True
            msgs.append(f"↘️ السعر تحت النطاق ({fp(px)}$ < {fp(lo)}$): الشبكة كلها {COIN} الآن وتنتظر الصعود. "
                        f"لإيقافها وبيع {COIN}: " + ("GRID_ENABLED=0." if COIN == "BTC" else f"GRID_{COIN}_ENABLED=0."))
        elif px >= lo:
            st["below_alerted"] = False
        save_state(st)
    except Exception as e:  # noqa: BLE001
        if st is not None:
            last = st.get("last_err")
            if not last or (now - datetime.fromisoformat(last)).total_seconds() > 3600:
                st["last_err"] = now.isoformat(timespec="seconds")
                msgs.append(f"⚠️ خطأ: {str(e)[:200]}")
                save_state(st)
        else:
            msgs.append(f"⚠️ خطأ: {str(e)[:200]}")
        print("error:", str(e)[:200])
        if msgs:
            send("\n".join([f"🕸 شبكة {COIN} · {tag}"] + msgs))
        return "error"
    return finish(st, msgs, tag, now, cfg, okx)


def finish(st, msgs, tag, now, cfg, okx, status="ok"):
    day = now.date().isoformat()
    if st.get("last_daily") != day and now.hour == 0:
        st["last_daily"] = day
        px = okx.ticker()
        eq = equity(st, px)
        hold = st["start_usd"] * px / st["start_px"]
        msgs.append(f"📊 ملخص يومي: الحصة {eq:,.0f}$ (بدأت {st['start_usd']:,.0f}$) · ربح الدورات المحقق "
                    f"{st['realized']:+.2f}$ · {st['fills']} تنفيذ · لو احتفظت بـ {COIN}: {hold:,.0f}$")
        save_state(st)
    if msgs:
        msgs.append("للتدريب: الاختبار التاريخي لم يجد ميزة للشبكة.")
        send("\n".join([f"🕸 شبكة {COIN} · {tag}"] + msgs))
    print(status, f"fills={st['fills']}", f"realized={st['realized']:.2f}")
    return status


if __name__ == "__main__":
    _cfg = config()
    setup(_cfg["inst"])
    sys.exit(0 if run(_cfg) in ("ok", "pending", "no_keys", "disabled") else 1)
