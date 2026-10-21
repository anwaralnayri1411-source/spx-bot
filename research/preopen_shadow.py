"""ظل «توصية ما قبل الافتتاح المشروطة» (SPX، 0DTE): تسجيل فقط، لا يُرسل شيئاً ولا يلمس البوت.

نفس قاعدة research/preopen_backtest.py (مثبّتة، بلا ضبط): فجوة الافتتاح ≥ +0.3% → CALL، ≤ −0.3% → PUT،
والتأكيد أن يبقى SPX عند 9:45 في اتجاه الفجوة. لكل يوم فجوة نسجّل عقدين: عقد البوت (bt.pick_contract) وعقداً
على السعر (ATM، مرجع)، وكلاهما بالاتجاه المعاكس أيضاً (مرجع للاتجاه نفسه).

تشغيل واحد يقرر ما يفعل حسب وقت نيويورك (مؤقت الخادم يستدعيه 3 مرات يومياً):
  8:50–9:29  خطة: إغلاق أمس، حركة ES ليلاً، ومستويا الشرط، ونص الرسالة التي كانت ستُرسل → preopen_shadow_msgs.jsonl
  9:45–10:00 لقطة: الافتتاح والسعر الآن (Tastytrade، وإلا ياهو)، القرار، والعقدان، وأسعار الطلب اللحظية لهما
  ≥ 16:05    تقييم مرة واحدة: شموع 5د لليوم، متابعة بقواعد البوت (bt.follow، تسعير نظري كالاختبار التاريخي)
             ولمس ±0.25% أولاً → preopen_shadow_log.csv، والملخص research/preopen_shadow_results.md
الحالة: preopen_shadow_state.json.   تشغيل: python research/preopen_shadow.py [--report]
"""
import csv
import json
import os
import sys
import urllib.parse
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import backtest_spx_v2 as bt     # noqa: E402  (التسعير والمتابعة واختيار العقد)
import live_quotes as lq         # noqa: E402
import options_bot as ob         # noqa: E402
import preopen_backtest as pb    # noqa: E402  (القاعدة نفسها ومقياس اللمس)

STATE = os.path.join(ROOT, "preopen_shadow_state.json")
LOG = os.path.join(ROOT, "preopen_shadow_log.csv")
MSGS = os.path.join(ROOT, "preopen_shadow_msgs.jsonl")
REPORT = os.path.join(HERE, "preopen_shadow_results.md")
LOG_COLS = ["date", "gap", "side", "confirm", "role", "contract", "strike", "S945", "entry_model", "entry_live",
            "live_src", "out", "exit_time", "pnl", "touch", "day_with_gap"]
PLAN_W, SNAP_W, EVAL_FROM = ((8, 50), (9, 29)), ((9, 45), (10, 0)), (16, 5)
OPP = {"CALL": "PUT", "PUT": "CALL"}


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def save_state(s):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


def _yf():
    import yfinance as yf
    return yf


def prev_close(today):
    d = _yf().Ticker("^GSPC").history(period="10d", auto_adjust=False)["Close"].dropna()
    d = d[[x.date().isoformat() < today for x in d.index]]
    return float(d.iloc[-1])


def es_night():
    """حركة ES من إغلاق أمس (16:00 نيويورك) حتى الآن، للعرض فقط (الدراسة: لا تتنبأ بالجلسة وحدها)."""
    try:
        h = bt._ny(_yf().Ticker("ES=F").history(period="5d", interval="5m", auto_adjust=False))["Close"].dropna()
        now = ob.now_ny()
        ref = h[h.index <= now.replace(hour=16, minute=0, second=0, microsecond=0) - pd.Timedelta(days=1)]
        ref = ref[ref.index.weekday < 5]
        return float(h.iloc[-1] / ref.iloc[-1] - 1) if len(ref) else None
    except Exception as e:
        print("تعذر ES:", e)
        return None


def plan(st, n):
    today = n.date().isoformat()
    if st.get("plan_date") == today:
        return
    pc = prev_close(today)
    es = es_night()
    up, dn = pc * (1 + pb.GAP_T), pc * (1 - pb.GAP_T)
    lean = "" if es is None else ("صعود" if es >= pb.GAP_T else ("هبوط" if es <= -pb.GAP_T else "محايد"))
    txt = (f"🌅 خطة ما قبل الافتتاح (ظل، لم تُرسل) | إغلاق أمس {pc:,.1f}"
           + ("" if es is None else f" | ES ليلاً {es * 100:+.2f}% ({lean})") + "\n"
           f"• افتتاح فوق {up:,.0f} وبقاء SPX فوق الافتتاح عند 9:45 → CALL 0DTE (عقد البوت) بهدف +50% ووقف −40%\n"
           f"• افتتاح تحت {dn:,.0f} وبقاء SPX تحت الافتتاح عند 9:45 → PUT 0DTE بنفس القواعد\n"
           f"• بين المستويين: لا توصية افتتاح")
    with open(MSGS, "a", encoding="utf-8") as f:
        f.write(json.dumps({"time": n.isoformat(timespec="minutes"), "text": txt}, ensure_ascii=False) + "\n")
    st.update(plan_date=today, prev_close=round(pc, 2), es_night=None if es is None else round(es, 4))
    print(txt)


def spx_now():
    """(افتتاح اليوم، السعر الآن، المصدر): Tastytrade أولاً، وإلا ياهو (دقيقة)."""
    try:
        if os.getenv("TASTY_CLIENT_SECRET") and os.getenv("TASTY_REFRESH_TOKEN"):
            q = urllib.parse.urlencode({"index": "SPX"})
            it = lq._tasty_req(f"/market-data/by-type?{q}", token=lq._tasty_token())["data"]["items"][0]
            o, last = float(it.get("open") or 0), float(it.get("last") or it.get("mark") or 0)
            if o > 0 and last > 0:
                return o, last, "tasty"
    except Exception as e:
        print("تعذر SPX من Tastytrade:", e)
    h = bt._ny(_yf().Ticker("^GSPC").history(period="1d", interval="1m", auto_adjust=False))
    return float(h["Open"].iloc[0]), float(h["Close"].iloc[-1]), "yahoo"


def iv_prev(today):
    v = _yf().Ticker("^VIX").history(period="10d", auto_adjust=False)["Close"].dropna()
    v = v[[x.date().isoformat() < today for x in v.index]]
    return float(v.iloc[-1]) / 100 if len(v) else 0.16


def contracts(side, S, iv0, open_px):
    """[(اسم، سترايك)]: عقد البوت (نظري، شروطه) وعقد على السعر."""
    out = []
    pk = bt.pick_contract(side, S, pb.ENTRY_M, iv0, open_px, pb.V)
    if pk:
        out.append(("bot", pk[0]))
    out.append(("atm", float(round(S / 5) * 5)))
    return out


def live_ask(exp, side, K):
    c = lq.occ("SPXW", exp, side, K)
    for src, fn in (("tasty", lq.tasty_quote), ("alpaca", lq.quote)):
        try:
            z = fn(c)
            if z.get("status") == "ok":
                return z["ask"], src
        except Exception as e:  # noqa: BLE001
            print("تعذر", src, e)
    return None, ""


def snap(st, n):
    today = n.date().isoformat()
    if st.get("snap_date") == today:
        return
    pc = st.get("prev_close") if st.get("plan_date") == today else prev_close(today)
    o, S, src = spx_now()
    gap = o / pc - 1
    side = "CALL" if gap >= pb.GAP_T else ("PUT" if gap <= -pb.GAP_T else "")
    confirm = bool(side) and ((S >= o) if side == "CALL" else (S <= o))
    legs = []
    if side:
        iv0 = iv_prev(today)
        for role, s_ in (("rule", side), ("opp", OPP[side])):
            for name, K in contracts(s_, S, iv0, o):
                ask, lsrc = live_ask(today, s_, K)
                legs.append({"role": role, "side": s_, "contract": name, "strike": K, "entry_live": ask, "live_src": lsrc})
    st.update(snap_date=today, snap={"time": n.isoformat(timespec="minutes"), "open": o, "S945": S, "src": src,
                                     "prev_close": pc, "gap": round(gap, 5), "side": side, "confirm": confirm, "legs": legs})
    print(f"لقطة 9:45: افتتاح {o:,.2f} الآن {S:,.2f} ({src}) فجوة {gap * 100:+.2f}% → {side or 'لا توصية'}"
          + (f" تأكيد {'✓' if confirm else '✗'}" if side else ""))


def evaluate(st, n):
    today = n.date().isoformat()
    if st.get("eval_date") == today:
        return
    spx = bt._ny(_yf().Ticker("^GSPC").history(period="5d", interval="5m", auto_adjust=False))[["Open", "High", "Low", "Close"]]
    g, m = bt.day_bars(spx, n.date())
    if len(g) < 70:
        print("شموع اليوم ناقصة؛ يُعاد التقييم في التشغيل التالي.")
        return
    sn = st.get("snap") if st.get("snap_date") == today else None
    open_px = float(g["Open"].values[0])
    pc = (sn or {}).get("prev_close") or prev_close(today)
    gap = open_px / pc - 1                          # من الشموع، كالاختبار التاريخي (اللقطة للمقارنة)
    side = "CALL" if gap >= pb.GAP_T else ("PUT" if gap <= -pb.GAP_T else "")
    S = bt.s_at(g, m, pb.ENTRY_M, False)
    confirm = bool(side) and ((S >= open_px) if side == "CALL" else (S <= open_px))
    close = float(g["Close"].values[-1])
    dwg = "" if not side else int((close - open_px) * gap > 0)
    rows = []
    if not side:
        rows.append({"date": today, "gap": round(gap, 5), "side": "", "confirm": "", "role": "", "contract": ""})
    else:
        iv0 = iv_prev(today)
        live = {(x["side"], x["contract"]): x for x in (sn or {}).get("legs", [])}
        for role, s_ in (("rule", side), ("opp", OPP[side])):
            # السترايك من اللقطة الحية إن وُجدت (هو ما كان سيُرسل)، وإلا من الشموع
            legs = [(nm, live[(s_, nm)]["strike"]) for nm in ("bot", "atm") if (s_, nm) in live] or contracts(s_, S, iv0, open_px)
            for name, K in legs:
                ask = bt.price(S, K, pb.ENTRY_M, s_, iv0, open_px, pb.V) * (1 + bt.HAIRCUT)
                out, ex_m, pnl = bt.follow(g, m, s_, K, pb.ENTRY_M, ask, ask + bt.COMMISSION, iv0, open_px, pb.V)
                lv = live.get((s_, name), {})
                rows.append({"date": today, "gap": round(gap, 5), "side": side, "confirm": int(confirm), "role": role,
                             "contract": name, "strike": K, "S945": round(S, 2), "entry_model": round(ask, 2),
                             "entry_live": lv.get("entry_live") or "", "live_src": lv.get("live_src", ""),
                             "out": out, "exit_time": f"{ex_m // 60}:{ex_m % 60:02d}", "pnl": round(pnl, 3),
                             "touch": pb.touch_first(g, m, s_, S), "day_with_gap": dwg})
    new = not os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        if new:
            w.writeheader()
        w.writerows({k: r.get(k, "") for k in LOG_COLS} for r in rows)
    st["eval_date"] = today
    print(f"تقييم {today}: فجوة {gap * 100:+.2f}% → {side or 'لا توصية'}، {len(rows)} صف")


def report():
    if not os.path.exists(LOG):
        print("لا سجل بعد.")
        return
    df = pd.read_csv(LOG)
    days = df["date"].nunique()
    t = df[df["role"].notna() & (df["role"] != "")]
    L = ["# ظل توصية ما قبل الافتتاح المشروطة (حي، تسعير نظري)", "",
         f"_آخر تحديث {datetime.now(ob.NY):%Y-%m-%d %H:%M} نيويورك | الاختبار التاريخي: research/preopen_backtest_results.md_", "",
         f"- أيام مُقيّمة: **{days}** | أيام فجوة ≥ {pb.GAP_T * 100:.1f}%: **{t['date'].nunique()}** | مع تأكيد 9:45: "
         f"**{t[t['confirm'] == 1]['date'].nunique()}**", "",
         "| المجموعة | العقد | صفقات | متوسط العقد | بلغ +50% | لمس ±0.25% مع / ضد |", "|---|---|---|---|---|---|"]
    for lab, sub in (("القاعدة مع تأكيد", t[(t["confirm"] == 1) & (t["role"] == "rule")]),
                     ("المعاكس مع تأكيد", t[(t["confirm"] == 1) & (t["role"] == "opp")]),
                     ("القاعدة كل أيام الفجوة", t[t["role"] == "rule"]),
                     ("المعاكس كل أيام الفجوة", t[t["role"] == "opp"])):
        for c in ("bot", "atm"):
            s = sub[sub["contract"] == c]
            if not len(s):
                L.append(f"| {lab} | {c} | 0 | — | — | — |")
                continue
            L.append(f"| {lab} | {c} | {len(s)} | {s['pnl'].mean() * 100:+.1f}% | {(s['pnl'] >= ob.TAKE_PROFIT - 0.05).mean() * 100:.0f}% | "
                     f"{(s['touch'] == 1).mean() * 100:.0f}% / {(s['touch'] == -1).mean() * 100:.0f}% |")
    lv = t[pd.to_numeric(t["entry_live"], errors="coerce").notna()]
    if len(lv):
        r = (pd.to_numeric(lv["entry_live"]) / lv["entry_model"] - 1)
        L += ["", f"- سعر الطلب اللحظي عند 9:46 مقابل السعر النظري: الوسيط {r.median() * 100:+.0f}% ({len(lv)} عقداً)"]
    L += ["", "لا حكم قبل 20 يوم فجوة على الأقل. «bot» = عقد البوت، «atm» = على السعر (مرجع)."]
    open(REPORT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))


def main():
    if "--report" in sys.argv:
        report()
        return
    n = ob.now_ny()
    if not ob.market_day(n):
        print("ليس يوم تداول.")
        return
    hm = (n.hour, n.minute)
    st = load_state()
    if PLAN_W[0] <= hm <= PLAN_W[1]:
        plan(st, n)
    elif SNAP_W[0] <= hm <= SNAP_W[1]:
        snap(st, n)
    elif hm >= EVAL_FROM:
        evaluate(st, n)
        save_state(st)
        report()
        return
    else:
        print("خارج نوافذ الظل.")
        return
    save_state(st)


if __name__ == "__main__":
    main()
