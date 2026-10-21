"""قراءة السوق لـ SPX: بطاقة صورة تجمع ما يحسبه البوت أصلاً (الاتجاه والمستويات والجدران) وتضيف
سلّم السترايكات (أسعار CALL/PUT وحجم اليوم) وRSI/ADX ونقطة التحكم وأعلى/أدنى القيمة من حجم SPY.

قراءة فقط (تجربة): لا يغيّر منطق التوصيات ولا يكتب في ملفات حالة البوتات الأخرى،
ويرسل إلى TELEGRAM_CHAT_ID وحده، ومع --all (بموافقة المستخدم 22/10) نسخة لـ TELEGRAM_EXTRA_CHAT_IDS أيضاً.
--session-only: لا يعمل خارج الجلسة (بعد الإغلاق تنتقل السلسلة لليوم التالي فتخرج أرقام خاطئة مثل نقطة الألم).
تشغيل: python market_read.py [--no-send] [--all] [--session-only] [--html out.html] [--png out.png] [--demo]
"""
import argparse
import math

LADDER_STEP = 5      # خطوة سترايك SPX
LADDER_HALF = 11     # عدد السترايكات فوق السعر وتحته في السلّم
CAPTION = "🧭 <b>قراءة السوق · SPX</b> #قراءة\n🧪 تجربة: تصلك وحدك حتى توافق عليها. قراءة وليست توصية."
CAPTION_ALL = "🧭 <b>قراءة السوق · SPX</b> #قراءة\nقراءة مشروطة بحركة السعر، وليست توصية دخول."


# ====================== مؤشرات ======================
def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    v = 100 - 100 / (1 + up / dn.replace(0, float("nan")))
    return None if v.dropna().empty else float(v.dropna().iloc[-1])


def adx(h, l, c, n=14):
    import pandas as pd
    up, dn = h.diff(), -l.diff()
    pdm = up.where((up > dn) & (up > 0), 0.0)
    mdm = dn.where((dn > up) & (dn > 0), 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pdm.ewm(alpha=1 / n, adjust=False).mean() / atr
    mdi = 100 * mdm.ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, float("nan"))
    v = dx.ewm(alpha=1 / n, adjust=False).mean().dropna()
    return None if v.empty else float(v.iloc[-1])


def trend(closes, fast=9, slow=21):
    """+1 صاعد، -1 هابط (متوسط 9 مقابل 21)، مثل signals_extra."""
    if len(closes) < slow + 2:
        return 0
    f = closes.ewm(span=fast, adjust=False).mean().iloc[-1]
    s = closes.ewm(span=slow, adjust=False).mean().iloc[-1]
    return 1 if f > s else (-1 if f < s else 0)


def volume_profile(df, bin_w):
    """نقطة التحكم (POC) وأعلى/أدنى القيمة (70% من الحجم) من شموع جلسة واحدة. يرجع (poc, vah, val) أو None."""
    df = df[df["Volume"] > 0]
    if len(df) < 6:
        return None
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    bins = (tp / bin_w).round().astype(int)
    vol = df["Volume"].groupby(bins).sum().sort_index()
    keys = list(vol.index)
    i = keys.index(vol.idxmax())
    lo = hi = i
    tot, acc = vol.sum(), vol.iloc[i]
    while acc < 0.7 * tot and (lo > 0 or hi < len(keys) - 1):
        a = vol.iloc[lo - 1] if lo > 0 else -1
        b = vol.iloc[hi + 1] if hi < len(keys) - 1 else -1
        if b >= a:
            hi += 1
            acc += b
        else:
            lo -= 1
            acc += a
    return keys[i] * bin_w, keys[hi] * bin_w, keys[lo] * bin_w


# ====================== السلّم ======================
def _px(r):
    bid, ask = float(r.get("bid") or 0), float(r.get("ask") or 0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2
    last = float(r.get("lastPrice") or 0)
    return last or None


def _vol(r):
    v = r.get("volume")
    try:
        return 0.0 if v is None or math.isnan(float(v)) else float(v)
    except (TypeError, ValueError):
        return 0.0


def ladder(calls, puts, S, named):
    """صفوف السلّم من الأعلى للأدنى. named: [(سعر، اسم)] يُلصق كل مستوى بأقرب سترايك."""
    cb = {float(r["strike"]): r for r in calls.to_dict("records")}
    pb = {float(r["strike"]): r for r in puts.to_dict("records")}
    k0 = round(S / LADDER_STEP) * LADDER_STEP
    rows = []
    for i in range(LADDER_HALF, -LADDER_HALF - 1, -1):
        K = float(k0 + i * LADDER_STEP)
        c, p = cb.get(K), pb.get(K)
        rows.append({"k": K, "call": _px(c) if c else None, "put": _px(p) if p else None,
                     "cv": _vol(c) if c else 0.0, "pv": _vol(p) if p else 0.0, "levels": []})
    for price, name in named:
        if price is None:
            continue
        best = min(rows, key=lambda r: abs(r["k"] - price))
        if abs(best["k"] - price) <= LADDER_STEP / 2 and name not in best["levels"]:
            best["levels"].append(name)
    return rows


# ====================== النص ======================
def _f(x):
    return f'<bdi dir="ltr">{x:,.0f}</bdi>'


def build_read(S, dir_, plan, frames, rsi_v, adx_v, gex, has_orb):
    R1, rn = plan["res"][0]
    S1, sn = plan["sup"][0]
    res2 = [p for p, _ in plan["res"][1:3]]
    sup2 = [p for p, _ in plan["sup"][1:3]]
    now = f"السعر <bdi dir=\"ltr\">{S:,.2f}</bdi> بين الدعم {_f(S1)} ({'، '.join(sn)}) والمقاومة {_f(R1)} ({'، '.join(rn)})."
    nxt = f"نراقب تفاعل السعر مع {_f(S1)} و{_f(R1)} قبل ترجيح الاستمرار أو الارتداد."
    if not has_orb:
        nxt += " نطاق أول 30 دقيقة لم يكتمل بعد."
    down = f"دون {_f(S1)} نراقب " + (" ثم ".join(_f(p) for p in sup2) if sup2 else "الدعم التالي")
    up = f"فوق {_f(R1)} نراقب " + (" ثم ".join(_f(p) for p in res2) if res2 else "المقاومة التالية")
    dest = (up + "؛ و" + down) if dir_ == "up" else (down + "؛ و" + up)
    dest += "."
    if dir_ == "down":
        inv = f"عودة فوق {_f(R1)} والثبات فوقه يُضعف القراءة الهابطة."
    elif dir_ == "up":
        inv = f"كسر {_f(S1)} والثبات تحته يُضعف القراءة الصاعدة."
    else:
        inv = f"لا اتجاه واضح بعد: إغلاق شمعة 5د خارج النطاق {_f(S1)}–{_f(R1)} يحدد الوجهة."
    v = [x for x in frames.values()]
    agree = ("متفقة على الصعود" if all(x > 0 for x in v) else
             "متفقة على الهبوط" if all(x < 0 for x in v) else "مختلطة")
    conf = [f"الفريمات {agree}."]
    if rsi_v is not None:
        st = "تشبع بيع" if rsi_v < 30 else ("تشبع شراء" if rsi_v > 70 else ("ميل هابط" if rsi_v < 45 else ("ميل صاعد" if rsi_v > 55 else "متوازن")))
        conf.append(f"<bdi dir=\"ltr\">RSI {rsi_v:.0f}</bdi> ({st}).")
    if adx_v is not None:
        conf.append(f"<bdi dir=\"ltr\">ADX {adx_v:.0f}</bdi> " +("(الاتجاه قوي)." if adx_v >= 25 else ("(متوسط)." if adx_v >= 20 else "(ضعيف: الحركة قد تكون متذبذبة).")))
    if gex:
        conf.append("جاما الصنّاع موجبة: ميل للتماسك." if gex > 0 else "جاما الصنّاع سالبة: الحركة قد تتسع.")
    return {"now": now, "next": nxt, "dest": dest, "invalid": inv, "confirm": " ".join(conf)}


# ====================== التجميع ======================
def gather():
    from datetime import date
    import yfinance as yf
    import options_bot as ob

    n = ob.now_ny()
    a = ob.analyze(False)                       # نفس تحليل البوت (قراءة فقط)
    c = a["c"]
    S = c["S"]
    plan = ob.build_plan(c, a["bull"], a["bear"])
    dir_ = ("up" if a["side"] == "CALL" else "down") if a["edge"] >= ob.MIN_EDGE else "flat"

    t = yf.Ticker(ob.SYMBOL)
    ix = t.history(period="5d", interval="5m").dropna(subset=["Close"])
    cl = ix["Close"]
    frames = {"M5": trend(cl), "M15": trend(cl.resample("15min").last().dropna()),
              "M30": trend(cl.resample("30min").last().dropna()), "H1": trend(cl.resample("60min").last().dropna())}
    rsi_v = rsi(cl)
    adx_v = adx(ix["High"], ix["Low"], cl)

    vp = None
    try:
        fl = yf.Ticker(ob.FLOW_SYMBOL).history(period="5d", interval="5m")
        last = fl.index[-1].date()
        sess = fl[fl.index.date == last]
        ratio = S / float(sess["Close"].iloc[-1])
        p = volume_profile(sess, LADDER_STEP / ratio)
        if p:
            vp = tuple(x * ratio for x in p)
    except Exception as e:
        print("تعذر حساب ملف الحجم:", e)

    lv = {}
    try:
        import levels
        lv = levels.compute(n)
    except Exception as e:
        print("تعذر حساب المستويات:", e)

    m = c.get("map") or {}
    cw = m.get("call_wall")[0] if m.get("call_wall") else None
    pw = m.get("put_wall")[0] if m.get("put_wall") else None
    refs = [("انقلاب الجاما", lv.get("flip")), ("نقطة الألم", c.get("max_pain")), ("جدار الكول", cw),
            ("جدار البوت", pw), ("نقطة التحكم", vp[0] if vp else None), ("أعلى القيمة", vp[1] if vp else None),
            ("أدنى القيمة", vp[2] if vp else None), ("VWAP", c.get("vwap_spx"))]

    named = [(v, k) for k, v in refs]
    named += [(p, t_) for p, nm in plan["res"] + plan["sup"] for t_ in nm if t_ != "رقم دائري"]
    if c.get("pd_hl"):
        named += [(c["pd_hl"][0], "أعلى أمس"), (c["pd_hl"][1], "أدنى أمس")]
    if c.get("day_hl"):
        named += [(c["day_hl"][0], "قمة الجلسة"), (c["day_hl"][1], "قاع الجلسة")]

    rows = []
    try:
        ch = t.option_chain(a["exp"])
        rows = ladder(ob.spxw_rows(ch.calls), ob.spxw_rows(ch.puts), S, named)
    except Exception as e:
        print("تعذر بناء السلّم:", e)

    rng = None
    if lv.get("em_rest"):
        rng = (S - lv["em_rest"], S + lv["em_rest"])
    elif c.get("vix"):
        base = c.get("prev_close") or S
        em = base * c["vix"] / 100 / math.sqrt(252)
        rng = (base - em, base + em)

    if a["reasons"]:
        contracts = "لا عقود مؤهلة الآن: " + ob.esc(a["reasons"][0])
    else:
        contracts = "البوت يرى عقوداً مؤهلة، وتصلك في رسالة التوصية المعتادة."

    return {"time_ry": n.astimezone(ob.RY).strftime("%Y-%m-%d %I:%M %p").replace("AM", "ص").replace("PM", "م") + " الرياض",
            "spot": S, "age": c.get("age"), "exp": a["exp"], "dir": dir_, "bull": a["bull"], "bear": a["bear"],
            "res": plan["res"][0], "sup": plan["sup"][0], "rsi": rsi_v, "adx": adx_v, "frames": frames,
            "ladder": rows, "read": build_read(S, dir_, plan, frames, rsi_v, adx_v, m.get("gex"), plan["has_orb"]),
            "range": rng, "refs": refs, "contracts": contracts, "pc": c.get("pc")}


def demo():
    """بيانات وهمية لتجربة شكل البطاقة دون اتصال."""
    S = 7684.85
    named = [(7690, "مقاومة مباشرة"), (7680, "دعم مباشر"), (7700, "نقطة التحكم"), (7705, "قمة الجلسة"),
             (7693.3, "أدنى أمس"), (7718.9, "VWAP"), (7735.4, "جدار الكول"), (7660, "أدنى القيمة"),
             (7656.3, "جدار البوت"), (7685, "قاع الجلسة")]
    rows = []
    for i in range(LADDER_HALF, -LADDER_HALF - 1, -1):
        K = round(S / 5) * 5 + i * 5
        d = K - S
        call = max(0.05, 9.5 * math.exp(-d / 18)) if d > 0 else -d + 9.5 * math.exp(d / 18)
        put = max(0.05, 9.5 * math.exp(d / 18)) if d < 0 else d + 9.5 * math.exp(-d / 18)
        rows.append({"k": float(K), "call": call, "put": put, "cv": max(0, 2400 - abs(d) * 45 + (K % 25 == 0) * 900),
                     "pv": max(0, 2100 - abs(d + 10) * 40 + (K % 25 == 0) * 700), "levels": []})
    for p, nm in named:
        best = min(rows, key=lambda r: abs(r["k"] - p))
        if abs(best["k"] - p) <= 2.5:
            best["levels"].append(nm)
    plan = {"res": [(7690.0, ["مقاومة مباشرة"]), (7700.0, ["نقطة التحكم", "رقم دائري"]), (7718.9, ["VWAP"])],
            "sup": [(7680.0, ["دعم مباشر"]), (7660.0, ["أدنى القيمة"]), (7656.3, ["جدار البوت"])]}
    frames = {"M5": -1, "M15": -1, "M30": -1, "H1": 1}
    return {"time_ry": "2026-10-15 04:30 م الرياض (تجربة)", "spot": S, "age": 16, "dir": "down", "bull": 1.0, "bear": 4.5,
            "res": plan["res"][0], "sup": plan["sup"][0], "rsi": 42.1, "adx": 17.7, "frames": frames, "ladder": rows,
            "read": build_read(S, "down", plan, frames, 42.1, 17.7, -1, False),
            "range": (7633.8, 7735.4), "refs": [("انقلاب الجاما", 7776.7), ("نقطة الألم", 7700), ("جدار الكول", 7735),
                                                 ("جدار البوت", 7656), ("نقطة التحكم", 7700), ("أعلى القيمة", 7780),
                                                 ("أدنى القيمة", 7660), ("VWAP", 7718.9)],
            "contracts": "لا عقود مؤهلة الآن: لا اتجاه واضح (صعود 1.0 مقابل هبوط 4.5).", "pc": 0.84}


# ====================== الإرسال ======================
def send(png, text_fallback, everyone=False):
    """إلى المستخدم (TELEGRAM_CHAT_ID)، ومع everyone نسخة لكل TELEGRAM_EXTRA_CHAT_IDS (فشل أحدهم لا يوقف الباقين).
    لا يستخدم دوال الإرسال المشتركة."""
    import os
    import requests
    token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("[تنبيه] لم تُضبط TELEGRAM_TOKEN / TELEGRAM_CHAT_ID، فلم تُرسل البطاقة.")
        return False
    chats = [chat]
    if everyone:
        chats += [x.strip() for x in (os.getenv("TELEGRAM_EXTRA_CHAT_IDS") or "").replace(";", ",").split(",") if x.strip()]
    cap = CAPTION_ALL if everyone else CAPTION
    ok = True
    for cid in chats:
        try:
            if png:
                r = requests.post(f"https://api.telegram.org/bot{token}/sendPhoto",
                                  data={"chat_id": cid, "caption": cap, "parse_mode": "HTML"},
                                  files={"photo": ("market_read.png", png, "image/png")}, timeout=40)
            else:
                r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                  data={"chat_id": cid, "text": cap + "\n\n" + text_fallback, "parse_mode": "HTML"},
                                  timeout=15)
            if not r.ok:
                print("خطأ من تليجرام:", r.text)
            ok = ok and r.ok
        except Exception as e:
            print("تعذر الإرسال:", e)
            ok = False
    return ok


def text_version(d):
    rd = d["read"]
    return "\n".join([f"SPX {d['spot']:,.2f}", f"<b>الآن:</b> {rd['now']}", f"<b>التالي:</b> {rd['next']}",
                      f"<b>الوجهة:</b> {rd['dest']}", f"<b>يتغيّر التقييم:</b> {rd['invalid']}",
                      f"<b>التأكيد:</b> {rd['confirm']}"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--all", action="store_true", help="نسخة لـ TELEGRAM_EXTRA_CHAT_IDS أيضاً")
    ap.add_argument("--session-only", action="store_true", help="لا شيء خارج الجلسة")
    ap.add_argument("--demo", action="store_true", help="بيانات وهمية لتجربة الشكل")
    ap.add_argument("--html")
    ap.add_argument("--png")
    args = ap.parse_args()
    if args.session_only:
        import options_bot as ob
        if not ob.in_window(ob.now_ny()):
            print("خارج الجلسة.")
            raise SystemExit(0)

    from market_read_card import card_html, card_png
    d = demo() if args.demo else gather()
    if args.html:
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(card_html(d))
    png = None
    if args.png or not args.no_send:
        try:
            png = card_png(d)
        except Exception as e:
            print("تعذر رسم البطاقة:", e)
    if args.png and png:
        with open(args.png, "wb") as f:
            f.write(png)
    if not args.no_send:
        send(png, text_version(d), everyone=args.all)
