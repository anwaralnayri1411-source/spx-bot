#!/usr/bin/env python3
"""الصقر (H-FALCON، مسجّل في CLAUDE.md 6/11/2026 بطلب المستخدم). ورقي واستكشافي: لا أوامر لأي وسيط ولا يغيّر أي بوت.

كل دقيقة في الجلسة الرسمية: محرك الظل يقرأ تصويت الفريمات من شموع ES اللحظية، وعند إغلاق M5 يعبر خط Murrey
أو تقدم 5 نقاط بعد إشارة سابقة تُرسل الإشارة إلى نموذج المتداول الذكي بمدخلاته كاملة وذاكرة المتعلّم، فيوافق أو يرفض أو يبيع.
الوقف 50% والهدف +50% بالكود (حكم H-STOPSTAGES). كل إشارة تُسجَّل مع حركة السوق بعدها، وتقرير واحد بعد الجلسة.
تشغيل: python falcon.py [--force] [--no-send]
"""
import json
import os
import sys
from datetime import datetime, time as dtime

import pandas as pd

import ai_trader as at
import mgt_lab as ml
import mgt_shadow as ms
import screens as scr

NY = ms.NY
DIR = os.path.join(os.getenv("SPXBOT_DATA", "/opt/spxbot/data"), "falcon")
STATE, LOG, JOURNAL, USAGE, SIGNALS = (os.path.join(DIR, f) for f in
                                       ("state.json", "log.csv", "journal.md", "usage.csv", "signals.csv"))
A_STATE = os.path.join(at.ROOT, "ai_trader_state.json")
END_T = dtime(16, 10)
MAX_CALLS, GAP_MIN = 20, 5
STOP_MULT, TARGET_MULT = 0.5, 1.5
SIG_COLS = ["time", "side", "kind", "line", "S", "score", "adx", "decision", "executed", "contract", "qty", "entry", "reason",
            "fav_pts", "adv_pts", "end_pts"]
RULES = at.RULES_B + """
أنت الآن «الصقر»: يصلك هذا الطلب لأن محرك الإشارة رصد فرصة في جهة محددة. مهمتك فحصها بكل المدخلات:
وافق بالشراء في جهة الإشارة فقط واختر العقد والكمية، أو ارفض بالانتظار مع السبب، أو بِع مركزك المفتوح إن كانت الإشارة تعاكسه.
الوقف 50% والهدف +50% يحددهما الكود، فلا تعتمد على وقف أو هدف آخر.
في «لوحة الشاشات» رأي كل شاشة الآن ودقتها السابقة: أعطِ وزناً أكبر للشاشات الأدق عندما يكون سجلها كافياً، واذكر في قراءتك أي الشاشات اعتمدت عليها."""


# ====================== دوال صافية ======================
def new_state(cash):
    return dict(at.new_state(), cash=cash, start_cash=cash, calls_today=0, last_call="", last_sig={}, last_bar=None)


def pick_signal(eng, st, day):
    """{side, kind, line} عند إغلاق شمعة 5د جديدة والأصوات والقوة كافية وعبَر خطاً أو تقدم 5 نقاط، وإلا None."""
    if not eng["new_bar"]:
        return None
    sd = ml.side_for(ml.CONTROL, eng["total"], eng["det"]["adx"])
    if not sd:
        return None
    hit = eng["hit"].get(sd)
    if hit:
        return {"side": sd, "kind": "خط", "line": hit[0]}
    last = st.get("last_sig", {}).get(sd)
    if last and last["day"] == day and (eng["S"] - last["S"]) * (1 if sd == "CALL" else -1) >= ms.LADDER_PTS:
        return {"side": sd, "kind": "سلّم", "line": ""}
    return None


def may_call(st, now):
    if st.get("calls_today", 0) >= MAX_CALLS or now.time() > at.LAST_DECIDE:
        return False
    last = st.get("last_call")
    return not last or (now - datetime.fromisoformat(last)).total_seconds() >= GAP_MIN * 60


def apply_engine_rules(d, sig, menu):
    """الشراء في جهة الإشارة فقط، والوقف والهدف بالكود. يعدّل القرار ويرجعه."""
    if d["action"] != "شراء":
        return d
    row = next((r for r in menu if r["symbol"] == (d["buy"].get("symbol") or "").strip()), None)
    if row is not None and row["side"] != sig["side"]:
        return dict(d, action="انتظار", wait_reason=f"خالف جهة الإشارة ({row['side']} مقابل {sig['side']})")
    if row is not None:
        ask = float(row["ask"] or 0)
        d["buy"].update(stop_price=round(ask * STOP_MULT, 2), target_price=round(ask * TARGET_MULT, 2))
    return d


def decision_word(d, ok):
    if d is None:
        return "فشل الطلب"
    if d["action"] == "شراء":
        return "قبول" if ok else "قبول مرفوض بالقيود"
    return "بيع" if d["action"] == "بيع" else "رفض"


def fill_moves(sig, m1):
    """حركة SPX بعد كل إشارة بالنقاط في اتجاهها: أفضل وأسوأ وعند الإغلاق."""
    out = sig.copy()
    out[["fav_pts", "adv_pts", "end_pts"]] = out[["fav_pts", "adv_pts", "end_pts"]].astype(object)
    for i, r in out.iterrows():
        x = m1[m1.index > pd.Timestamp(r["time"])]
        x = x[x.index.strftime("%H:%M") < "16:00"]
        if x.empty:
            continue
        s, sg = float(r["S"]), 1 if r["side"] == "CALL" else -1
        moves = [(float(x["h"].max()) - s) * sg, (float(x["l"].min()) - s) * sg]
        out.loc[i, ["fav_pts", "adv_pts", "end_pts"]] = [round(max(moves), 1), round(min(moves), 1), round((float(x["c"].iloc[-1]) - s) * sg, 1)]
    return out


def signals_text(sig):
    if sig.empty:
        return "إشارات المحرك اليوم: 0"
    c = sig["decision"].value_counts()
    L = [f"**إشارات المحرك: {len(sig)}** | قبول {c.get('قبول', 0)} · رفض {c.get('رفض', 0)} · بيع {c.get('بيع', 0)}"]
    for word, name in (("قبول", "المقبولة"), ("رفض", "المرفوضة")):
        x = pd.to_numeric(sig.loc[sig["decision"] == word, "end_pts"], errors="coerce").dropna()
        if len(x):
            L.append(f"بعد الإشارات {name}: تحرك SPX حتى الإغلاق بمتوسط {x.mean():+,.1f} نقطة في اتجاهها")
    return "\n".join(L)


# ====================== الملفات ======================
def use_paths():
    os.makedirs(DIR, exist_ok=True)
    at.STATE, at.LOG, at.JOURNAL, at.USAGE = STATE, LOG, JOURNAL, USAGE


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        try:
            cash = json.load(open(A_STATE, encoding="utf-8"))["cash"]
        except Exception:  # noqa: BLE001
            cash = at.START_CASH
        return new_state(cash)


def save(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)


def add_signal(row):
    pd.DataFrame([{k: row.get(k, "") for k in SIG_COLS}]).to_csv(SIGNALS, mode="a", header=not os.path.exists(SIGNALS), index=False)


def read_signals(day):
    try:
        d = pd.read_csv(SIGNALS, dtype=str, keep_default_na=False)
        return d, d[d["time"].str[:10] == day]
    except Exception:  # noqa: BLE001
        return pd.DataFrame(columns=SIG_COLS), pd.DataFrame(columns=SIG_COLS)


def send(text, no_send):
    print(text)
    if not no_send:
        import analyst as an
        an.send(text, "falcon")


# ====================== التشغيل ======================
def handle_signal(st, sig, eng, marks, now):
    """يطلب قرار النموذج وينفّذه ضمن القيود، ويسجّل الإشارة."""
    row = {"time": now.isoformat(timespec="minutes"), "side": sig["side"], "kind": sig["kind"], "line": sig["line"],
           "S": round(eng["S"], 2), "score": eng["total"], "adx": round(eng["det"]["adx"], 1)}
    st.setdefault("last_sig", {})[sig["side"]] = {"S": eng["S"], "day": now.date().isoformat()}
    if not may_call(st, now):
        add_signal(dict(row, decision="لم يُطلب", reason="حد الطلبات أو الفاصل الزمني"))
        return
    st["calls_today"] = st.get("calls_today", 0) + 1
    st["last_call"] = now.isoformat(timespec="seconds")
    text, menu, spot, _ = at.gather(now)
    marks.update({m["symbol"]: m for m in menu})
    note = f"## إشارة المحرك الآن: {sig['side']} ({'عبور خط ' + str(sig['line']) if sig['kind'] == 'خط' else 'تقدم 5 نقاط'}) | الأصوات {eng['total']} | ADX {eng['det']['adx']:.0f}"
    prompt = "\n\n".join(x for x in [text, scr.panel(now), at.memory_text(at.load_memory()), at.portfolio_text(st, marks, now), note,
                                       f"الوقت الآن {now:%H:%M} نيويورك. قرّر."] if x)
    d = at.ask_claude(prompt, now, at.SCHEMA_B, RULES)
    if d is None:
        add_signal(dict(row, decision=decision_word(None, False)))
        return
    d = apply_engine_rules(d, sig, menu)
    st["decisions"] = st.get("decisions", 0) + 1
    st["decisions_today"] = st.get("decisions_today", 0) + 1
    r, qty, ok, msg = at.execute(st, d, menu, marks, now, spot, True)
    at.log_row(now, spot, d, r, qty, ok, msg)
    at.journal(now, spot, d, "نُفّذ" if ok else "لم يُنفَّذ", msg)
    b = d.get("buy") or {}
    add_signal(dict(row, decision=decision_word(d, ok), executed=ok, contract=(r or {}).get("symbol", ""), qty=qty, entry=(r or {}).get("ask", ""),
                    reason=at.short(b.get("short_reason") if d["action"] == "شراء" else d.get("wait_reason") or d.get("sell_reason"))))


def report(st, now, no_send):
    day = now.date().isoformat()
    at.record_after(st, day)
    allsig, today = read_signals(day)
    if len(today):
        try:
            m1, _ = ml.spx_m1()
            today = fill_moves(today, m1)
            allsig.loc[today.index, today.columns] = today.astype(str)
            allsig.to_csv(SIGNALS, index=False)
        except Exception as e:  # noqa: BLE001
            print("حركة ما بعد الإشارة:", type(e).__name__, e)
    if len(today) or st["positions"] or any(c["exit_time"][:10] == day for c in st["closed"]):
        txt = at.day_report(st, now, at.today_recs(day)).replace("🤖 **المتداول الذكي · نهاية الجلسة**", "🦅 **الصقر · نهاية الجلسة**", 1)
        txt = txt.replace("research/ai_trader_journal.md", "falcon/journal.md على الخادم")
        send(txt + "\n\n" + signals_text(today), no_send)
    st["report_day"] = day


def main(argv):
    now = datetime.now(NY)
    force, no_send = "--force" in argv, "--no-send" in argv
    if not force and (now.weekday() >= 5 or not at.OPEN_T <= now.time() <= END_T):
        return
    use_paths()
    st = load_state()
    day = now.date().isoformat()
    if st.get("day") != day:
        st.update(day=day, decisions_today=0, calls_today=0, last_call="")
    marks = {q["symbol"]: q for q in at.quotes([p["symbol"] for p in st["positions"]])} if st["positions"] else {}
    at.auto_exits(st, marks, now)
    if now.time() <= at.LAST_DECIDE or force:
        try:
            eng = ml.read_engine(now, "rth", st.get("last_bar"))
        except Exception as e:  # noqa: BLE001
            print("المحرك:", type(e).__name__, e)
            eng = None
        if eng:
            st["last_bar"] = eng["bar"]
            sig = pick_signal(eng, st, day)
            if sig:
                handle_signal(st, sig, eng, marks, now)
    if now.time() >= at.REPORT_AT and st.get("report_day") != day:
        report(st, now, no_send)
    save(st)


if __name__ == "__main__":
    main(sys.argv[1:])
