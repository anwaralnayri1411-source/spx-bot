#!/usr/bin/env python3
"""توصيات SPX بقواعد v9 حياً (H-V9LIVE، مسجّل في CLAUDE.md 24/11/2026 بقرار المستخدم «نعم أ»).

يقرأ النسخة الثابتة s0 من مختبر التطور (v9 بالقيد الشرعي) بعد كل تشغيل لـmgt_lab.py، ويرسل لقناة بوت SPX
كل دخول جديد لها (بسقف 550$ للعقد) وكل خروج رداً على رسالة دخوله، وملخصاً بعد 16:05.
الحالة في SPXBOT_DATA/v9_live/state.json (خارج git). تشغيل: python v9_live.py [--no-send]
"""
import json
import os
import sys
from datetime import datetime

import mgt_lab as ml
import mgt_shadow as ms

NY = ms.NY
VID = "s0"
MAX_ASK = 5.50                      # سقف 550$ للعقد (قرار المستخدم 24/11/2026)
DIR = os.path.join(os.getenv("SPXBOT_DATA", "/opt/spxbot/data"), "v9_live")
STATE = os.path.join(DIR, "state.json")
SUMMARY_AT = ms.dtime(16, 5)


def key(e):
    return f"{e['t']}|{e['side']}|{e['strike']:g}"


def load():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"sent": {}, "closed": [], "skipped": [], "summary_day": "", "init": False}


def save(st):
    os.makedirs(DIR, exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def exit_kind(e):
    E, x = e["entry"], e.get("exit_px", 0)
    if x >= 2 * E - 1e-9:
        return "✅ <b>هدف +100%</b>"
    if x <= E * 0.4 + 1e-9:
        return "🛑 <b>وقف −60%</b>"
    return "⏰ <b>إغلاق 15:55</b>"


def entry_msg(e, k):
    E, call = e["entry"], e["side"] == "CALL"
    head = "الدخول الأول اليوم" if k == 1 else f"إضافة رقم {k} مع الاتجاه (كل 10 نقاط، حتى 12)"
    return "\n".join([
        f"{'🟢' if call else '🔴'} <b>SPX {e['side']} — قواعد v9</b> #توصية",
        f"العقد: <b>SPXW {e['strike']:g} {e['side']}</b> ينتهي {e['tdate']}",
        f"الدخول (سعر الطلب): <b>{E:.2f}$</b> = {E * 100:,.0f}$ للعقد",
        f"🎯 الهدف: بيع عند <b>{2 * E:.2f}$</b> (+100%)",
        f"🛑 الوقف: <b>{0.4 * E:.2f}$</b> (−60%)",
        "⏰ إن لم يتحقق أيهما: بيع 15:55",
        f"SPX {e['S']:,.0f} · الأصوات {e['score']:+g} من 7 · ADX {e['adx']:g}",
        f"📌 {head}. اتجاه واحد فقط مفتوح.",
    ])


def exit_msg(e, root="SPXW"):
    return (f"{exit_kind(e)} · بع {root} {e['strike']:g} {e['side']} عند {e.get('exit_px', 0):.2f}$ "
            f"({e.get('exit_t', '')})\nالنتيجة: <b>{e.get('usd', 0):+,.0f}$</b> للعقد #متابعة")


def mirror_entry_msg(e, root):
    E = e["entry"]
    return (f"{'🟢' if e['side'] == 'CALL' else '🔴'} <b>{root} {e['strike']:g} {e['side']}</b> (الجهة نفسها) ينتهي {e['tdate']}\n"
            f"الدخول {E:.2f}$ = {E * 100:,.0f}$ · 🎯 {2 * E:.2f}$ (+100%) · 🛑 {0.4 * E:.2f}$ (−60%) · دلتا {abs(e.get('delta') or 0):.2f}")


def summary_msg(day, rows, extra=None):
    if not rows:
        return f"🧾 <b>v9 اليوم {day}</b>: لا توصيات (لم يتحقق إجماع 4 أصوات مع ADX)."
    w = sum(r["usd"] > 0 for r in rows)
    L = [f"🧾 <b>v9 اليوم {day}</b> #تقرير", f"{len(rows)} توصية: {w} رابحة · {len(rows) - w} خاسرة",
         f"SPX لعقد واحد من كل توصية: <b>{sum(r['usd'] for r in rows):+,.0f}$</b>"]
    for root, rs in (extra or {}).items():
        if rs:
            L.append(f"{root}: <b>{sum(r['usd'] for r in rs):+,.0f}$</b> ({sum(r['usd'] > 0 for r in rs)} من {len(rs)} رابحة)")
    return "\n".join(L)


SHORT = {"✅": "هدف", "🛑": "وقف", "⏰": "15:55"}


def board_msg(day, root, rows):
    """لوحة نهاية اليوم لمؤشر واحد: كل صفقة في سطر ثم الإجمالي."""
    name = "SPX" if root == "SPXW" else root
    if not rows:
        return f"📋 <b>لوحة {name} — v9 {day}</b>\nلا صفقات اليوم."
    rows = sorted(rows, key=lambda e: e["t"])
    w = sum(r["usd"] > 0 for r in rows)
    L = [f"📋 <b>لوحة {name} — v9 {day}</b> #تقرير", f"{len(rows)} صفقة: {w} رابحة · {len(rows) - w} خاسرة", ""]
    for i, e in enumerate(rows, 1):
        k = exit_kind(e)
        L.append(f"{i}. {e['t'][11:16]} {'🟢' if e['side'] == 'CALL' else '🔴'} {e['side']} {e['strike']:g} · "
                 f"{e['entry']:.2f}→{e.get('exit_px', 0):.2f}$ ({e.get('exit_t', '')}) · {k[0]} {SHORT[k[0]]} · "
                 f"<b>{e.get('usd', 0):+,.0f}$</b>")
    L += ["", f"الإجمالي لعقد واحد من كل صفقة: <b>{sum(r['usd'] for r in rows):+,.0f}$</b>"]
    return "\n".join(L)


def mkey(v, e):
    return f"{v['root']}|{key(e)}"


def step_mirrors(st, mirrors, day, send):
    """دخول SPY/NDXP وخروجهما رداً على بطاقة SPX الأم (فقط إن أُرسلت الأم)."""
    out = 0
    for v in mirrors:
        for e in sorted([e for e in v["open"] + v["closed"] if e["tdate"] == day], key=lambda e: e["t"]):
            kk = mkey(v, e)
            if kk in st["sent"]:
                continue
            par = next((k for k in st["sent"] if k.startswith(f"{e['parent']}|{e['side']}|")), None)
            if par is None:
                continue
            st["sent"][kk] = send(mirror_entry_msg(e, v["root"]), st["sent"][par])
            out += 1
        for e in v["closed"]:
            kk = mkey(v, e)
            if kk in st["sent"] and kk not in st["closed"]:
                st["closed"].append(kk)
                send(exit_msg(e, v["root"]), st["sent"][kk])
                out += 1
    return out


def step(st, v, day, send, after_summary=False, mirrors=()):
    """منطق صافٍ: يرسل الجديد ويرجع عدد الرسائل. send(text, reply_to) يرجع رقم الرسالة."""
    entries = sorted([e for e in v["open"] + v["closed"] if e["tdate"] == day], key=lambda e: e["t"])
    if not st.get("init"):                  # أول تشغيل: لا نرسل ما سبق
        st["sent"] = {key(e): None for e in entries}
        st["closed"] = [key(e) for e in v["closed"] if e["tdate"] == day]
        st["sent"].update({mkey(m, e): None for m in mirrors for e in m["open"] + m["closed"] if e["tdate"] == day})
        st["closed"] += [mkey(m, e) for m in mirrors for e in m["closed"] if e["tdate"] == day]
        st["init"], st["summary_day"] = True, day   # ولا ملخص ليوم لم تُرسل توصياته
        return 0
    out, closed_keys = 0, {key(e) for e in v["closed"]}
    for e in entries:
        kk = key(e)
        if kk in st["sent"] or kk in st["skipped"]:
            continue
        if e["entry"] > MAX_ASK:
            st["skipped"].append(kk)
            print("تجاوز السقف:", kk, e["entry"])
            continue
        k = sum(1 for x in entries if x["side"] == e["side"] and key(x) in st["sent"]) + 1
        st["sent"][kk] = send(entry_msg(e, k), None)
        out += 1
    for e in v["closed"]:
        kk = key(e)
        if kk in st["sent"] and kk not in st["closed"]:
            st["closed"].append(kk)
            send(exit_msg(e), st["sent"][kk])
            out += 1
    out += step_mirrors(st, mirrors, day, send)
    if after_summary and st.get("summary_day") != day:
        rows = [e for e in v["closed"] if e["tdate"] == day and key(e) in st["sent"]]
        if not rows:
            send(summary_msg(day, []), None)
            out += 1
        else:
            boards = [("SPXW", rows)] + [(m["root"], [e for e in m["closed"] if e["tdate"] == day
                                                      and mkey(m, e) in st["sent"]]) for m in mirrors]
            for root, rs in boards:
                send(board_msg(day, root, rs), None)
                out += 1
        st["summary_day"] = day
    keep = {key(e) for e in v["open"]} | {key(e) for e in v["closed"] if e["tdate"] >= day}
    keep |= {mkey(m, e) for m in mirrors for e in m["open"] + [c for c in m["closed"] if c["tdate"] >= day]}
    st["sent"] = {k: m for k, m in st["sent"].items() if k in keep}
    st["skipped"] = [k for k in st["skipped"] if k in keep]
    closed_keys |= {mkey(m, e) for m in mirrors for e in m["closed"]}
    st["closed"] = [k for k in st["closed"] if k in closed_keys and k in st["sent"]]
    return out


def main(argv):
    now = datetime.now(NY)
    day = now.date().isoformat()
    try:
        lab = json.load(open(ml.STATE, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print("حالة المختبر:", e)
        return
    v = next((x for x in lab.get("strat", []) if x["vid"] == VID), None)
    if v is None:
        print("لا نسخة", VID)
        return
    if "--no-send" in argv:
        send = lambda t, r: print(t, "\n") or None  # noqa: E731
    else:
        import options_bot as ob
        send = lambda t, r: ob.send_telegram(t, reply_to=r)  # noqa: E731
    st = load()
    after = now.weekday() < 5 and now.time() >= SUMMARY_AT and any(e["tdate"] == day for e in v["closed"])
    step(st, v, day, send, after, lab.get("mirror", []))
    save(st)


if __name__ == "__main__":
    main(sys.argv[1:])
