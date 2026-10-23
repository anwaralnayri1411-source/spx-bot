"""ظل «إشارة قوية + عقد على السعر لغد + خروج بعد 60 دقيقة» (SPX؛ تسجيل فقط، لا يغيّر التوصيات ولا يرسل شيئاً).
بطلب المستخدم 23/10، بعد research/structure_study.py: أفضل خيط في الاستكشاف كان فرق النقاط ≥ 7 بعقد على السعر 1DTE وخروج بعد ساعة
(متوسط −0.8% مقابل −37% لهيكل البوت، وقيمة الاتجاه +8.8% بفاصل +0.3…+17.8%) — نظري وعلى 15 يوماً فقط، فيحتاج هذا الظل.

يُستدعى من options_bot بعد كل فحص (:05 و:35):
  1) يغلق كل صفقة ظل عمرها ≥ 60 دقيقة بسعر البيع اللحظي (bid)، ويسجّل قيمة العقد عند 30 دقيقة أيضاً.
  2) إن كان فرق النقاط (|صعود − هبوط| كما يحسبه البوت) ≥ EDGE_MIN، والوقت ≤ 14:35 (حتى يُغلق قبل 15:35)، ولم يدخل الظل
     الاتجاه نفسه خلال 90 دقيقة: يشتري على الورق عقد SPXW على السعر (ومنذ 23/10 نسخة SPY الأرخص بالقاعدة نفسها) (أقرب 5 نقاط للسعر اللحظي) لانتهاء يوم التداول التالي
     بسعر الطلب اللحظي (Alpaca ثم Tastytrade)، ومعه العقد المعاكس على السترايك نفسه مرجعاً.
     يسجّل أيضاً هل أرسل البوت توصية في الفحص نفسه، وأسباب المنع (أخبار، حدث...)، فلا يُحجب الظل بأسباب البوت.
بلا سعر لحظي حديث (≤120ث) لا دخول ولا خروج (يُعاد الخروج في الفحص التالي ويُسجَّل العمر الفعلي).
الملفات: edge7_shadow_state.json (المفتوح)، edge7_shadow_log.csv (المغلق). تقرير: python edge7_shadow.py --report
⚠️ فرق النقاط الحي يشمل نقاطاً لم تكن في الاختبار التاريخي (الجدران، نقطة الألم، التدفق، الأخبار). لا حكم قبل 20 إشارة.
"""
import csv
import json
import os
import sys
from datetime import date, datetime, timedelta

import options_bot as ob

STATE = "edge7_shadow_state.json"
LOG = "edge7_shadow_log.csv"
EDGE_MIN = 7.0
HOLD_MIN = 60
LAST_ENTRY = (14, 35)
REPEAT_MIN = 90
COLS = ["entry_time", "side", "role", "contract", "strike", "exp", "edge", "bot_sent", "reasons", "spot", "entry",
        "entry_src", "val30", "exit_time", "age_min", "exit", "exit_src", "pnl"]


def next_trading_day(d):
    d += timedelta(days=1)
    while d.weekday() >= 5 or d.isoformat() in ob.HOLIDAYS:
        d += timedelta(days=1)
    return d


def _load():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {"open": [], "last": {}}


def _save(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _append(rows):
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        w.writerows(rows)


def snapshot(n, a):
    import live_quotes as lq
    st = _load()
    # 1) الخروج والقراءة عند 30 دقيقة
    still, done = [], []
    for it in st["open"]:
        age = (n - datetime.fromisoformat(it["entry_time"])).total_seconds() / 60
        z, src = lq.best_quote(it["contract"])
        if z and age >= 25 and it.get("val30") in (None, ""):
            it["val30"] = round(z["bid"], 2)
        if age >= HOLD_MIN - 2 and z:
            done.append(dict(it, exit_time=n.isoformat(timespec="minutes"), age_min=round(age), exit=round(z["bid"], 2),
                             exit_src=src, pnl=round(z["bid"] / it["entry"] - 1, 4)))
        elif age > 150 or n.date().isoformat() != it["entry_time"][:10]:    # لا سعر حتى الآن: تُغلق بلا نتيجة
            done.append(dict(it, exit_time=n.isoformat(timespec="minutes"), age_min=round(age), exit="", exit_src="none", pnl=""))
        else:
            still.append(it)
    if done:
        _append([{k: r.get(k, "") for k in COLS} for r in done])
    st["open"] = still
    # 2) الدخول
    edge = abs(a["bull"] - a["bear"])
    side = a["side"]
    last = st["last"].get(side)
    if (edge >= EDGE_MIN and (n.hour, n.minute) <= LAST_ENTRY
            and not (last and (n - datetime.fromisoformat(last)).total_seconds() / 60 < REPEAT_MIN)):
        S, _ = lq.spot("SPX")
        S = S or float(a["c"]["S"])
        K = round(S / 5) * 5
        exp = next_trading_day(n.date()).isoformat()
        opp = "PUT" if side == "CALL" else "CALL"
        S_spy, _ = lq.spot("SPY")
        added = []
        # SPXW (العقد ~2,000–3,000$) ونسخة SPY الأرخص (~300–500$، عُشر SPX) بالفكرة نفسها؛ كل أداة بزوجها (الاتجاه + المعاكس)
        for root, Sx, step, pre in (("SPXW", S, 5, ""), ("SPY", S_spy, 1, "spy_")):
            if not Sx:
                continue
            Kx = round(Sx / step) * step
            legs = []
            for role, sd in ((pre + "bot", side), (pre + "opp", opp)):
                c = lq.occ(root, exp, sd, Kx)
                z, src = lq.best_quote(c)
                if not z:
                    legs = []
                    break
                legs.append({"entry_time": n.isoformat(timespec="minutes"), "side": sd, "role": role, "contract": c,
                             "strike": Kx, "exp": exp, "edge": round(edge, 2), "bot_sent": int(bool(a["contracts"])),
                             "reasons": " | ".join(r[:50] for r in a["reasons"][:3]), "spot": round(Sx, 2),
                             "entry": round(z["ask"], 2), "entry_src": src, "val30": ""})
            added += legs
        if added:
            st["open"] += added
            st["last"][side] = n.isoformat(timespec="minutes")
            print(f"ظل الإشارة القوية: {side} لانتهاء {exp}: " + ", ".join(f"{x['contract']} {x['entry']}" for x in added)
                  + f" (فرق {edge:.1f})")
        else:
            print("ظل الإشارة القوية: لا سعر لحظي حديث، لا دخول")
    _save(st)


def report():
    if not os.path.exists(LOG):
        print("لا صفقات مغلقة بعد.")
        return
    R = list(csv.DictReader(open(LOG, encoding="utf-8")))
    for pre, name in (("", "SPXW"), ("spy_", "SPY")):
        bot = {r["entry_time"]: float(r["pnl"]) for r in R if r["role"] == pre + "bot" and r["pnl"]}
        opp = {r["entry_time"]: float(r["pnl"]) for r in R if r["role"] == pre + "opp" and r["pnl"]}
        both = sorted(set(bot) & set(opp))
        if not both:
            print(f"{name}: لا أزواج مكتملة بعد.")
            continue
        mb = sum(bot[t] for t in both) / len(both)
        mo = sum(opp[t] for t in both) / len(both)
        wins = sum(bot[t] > opp[t] for t in both)
        sent = sum(1 for r in R if r["role"] == pre + "bot" and r["bot_sent"] == "1")
        print(f"{name}: إشارات {len(both)} (أرسلها البوت: {sent}) | متوسط عقد الاتجاه {mb * 100:+.1f}% | المعاكس {mo * 100:+.1f}% | "
              f"الاتجاه أفضل من عكسه في {wins}/{len(both)}. لا حكم قبل 20 إشارة.")

if __name__ == "__main__":
    if sys.argv[1:] == ["--report"]:
        report()
    else:
        print(__doc__)
