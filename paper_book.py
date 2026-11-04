#!/usr/bin/env python3
"""الدفتر الورقي بالدولار (H-PAPER، مسجّل في CLAUDE.md 4/11/2026 بطلب المستخدم). قراءة فقط، لا يغيّر أي توصية.

عقد واحد لكل توصية منذ PAPER_START: ربح/خسارة بالدولار لكل استراتيجية:
  SPX/SPY والشركات (من analyst_cards.csv: عائد أول خروج بالقاعدة)، والمستويات (level_signals_log.csv)، وما قبل الأرباح
  (earnings_runup_log.csv)؛ وأثر R1/R2 = نتيجة المحجوب (blocked_shadow_log.csv، earn_in_life وbtc_against) لو أُرسل.
تنبيه: استراتيجية صافيها ≤ −500$ أو 5 خسائر متتالية (التجريبيتان تتوقفان وحدهما؛ البوتان الأساسيان: تنبيه والقرار للمستخدم).
الملفات: research/paper_book.md، paper_book_state.json. تشغيل: python paper_book.py [--no-send]
"""
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
NY = ZoneInfo("America/New_York")
OUT = os.path.join(ROOT, "research", "paper_book.md")
STATE = os.path.join(ROOT, "paper_book_state.json")
PAPER_START = "2026-11-05"
STOP_USD, STOP_STREAK = -500.0, 5
NAMES = {"spx": "SPX/SPY (البوت)", "stocks": "الشركات (البوت)", "levels": "فرص المستويات (تجريبية)",
         "runup": "ما قبل الأرباح (تجريبية)", "blocked_earn": "محجوب R1: أرباح داخل العقد (لو أُرسل)",
         "blocked_btc": "محجوب R2: ضد البتكوين (لو أُرسل)"}


def _read(name):
    p = os.path.join(ROOT, name)
    try:
        return pd.read_csv(p, dtype=str, keep_default_na=False) if os.path.exists(p) else pd.DataFrame()
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def trades(start=PAPER_START):
    """إطار موحّد: strategy، day، closed (محسومة؟)، usd."""
    out = []
    c = _read("analyst_cards.csv")
    if len(c):
        c = c[c["day"] >= start]
        for r in c.itertuples():
            e, x = _num(pd.Series([r.entry])).iloc[0], _num(pd.Series([r.exit_ret])).iloc[0]
            if pd.notna(e) and pd.notna(x):
                out.append({"strategy": r.project, "day": r.day, "closed": r.exit_kind in ("هدف", "وقف"), "usd": e * 100 * x})
    lv = _read("level_signals_log.csv")
    if len(lv):
        for r in lv[lv["date"] >= start].itertuples():
            x = _num(pd.Series([r.exit_ret or r.last_ret])).iloc[0]
            if pd.notna(x):
                out.append({"strategy": "levels", "day": r.date, "closed": r.status != "مفتوحة", "usd": float(r.entry) * 100 * x})
    ru = _read("earnings_runup_log.csv")
    if len(ru):
        for r in ru[ru["date"] >= start].itertuples():
            if r.status != "مفتوحة" and r.pnl_usd:
                out.append({"strategy": "runup", "day": r.date, "closed": True, "usd": float(r.pnl_usd)})
            elif r.last_ret:
                out.append({"strategy": "runup", "day": r.date, "closed": False, "usd": float(r.entry) * 100 * float(r.last_ret)})
    b = _read("blocked_shadow_log.csv")
    if len(b):
        b = b[(b["project"] == "stocks") & b["tag"].isin(["earn_in_life", "btc_against"]) & (b["entry_time"].str[:10] >= start)]
        for r in b.itertuples():
            x = _num(pd.Series([r.out_pnl])).iloc[0]
            if pd.notna(x):
                out.append({"strategy": "blocked_earn" if r.tag == "earn_in_life" else "blocked_btc", "day": r.entry_time[:10],
                            "closed": r.out in ("tp", "stop"), "usd": float(r.entry) * 100 * x})
    return pd.DataFrame(out, columns=["strategy", "day", "closed", "usd"])


def stop_hit(g):
    """(صافٍ ≤ −500$ أو 5 خسائر متتالية) على المحسوم."""
    d = g[g["closed"]].sort_values("day")
    if d["usd"].sum() <= STOP_USD:
        return True
    streak = 0
    for x in d["usd"]:
        streak = streak + 1 if x < 0 else 0
    return streak >= STOP_STREAK


def report(t, now):
    L = [f"📒 **الدفتر الورقي بالدولار** (عقد واحد لكل توصية منذ {PAPER_START})", ""]
    alerts = []
    for k, name in NAMES.items():
        g = t[t["strategy"] == k]
        if not len(g):
            L.append(f"• {name}: لا صفقات بعد")
            continue
        cl = g[g["closed"]]
        wins = int((cl["usd"] > 0).sum())
        L.append(f"• **{name}**: {len(g)} صفقة ({len(cl)} محسومة، {wins} رابحة) | الصافي المحسوم **{cl['usd'].sum():+,.0f}$**"
                 f" | المفتوح على الورق {g[~g['closed']]['usd'].sum():+,.0f}$")
        if not k.startswith("blocked") and stop_hit(g):
            alerts.append(name)
    be, bb = t[t["strategy"] == "blocked_earn"], t[t["strategy"] == "blocked_btc"]
    L += ["", "**أثر القواعد الجديدة:** المحجوب لو أُرسل — سالب = القاعدة حمتنا، موجب = فوّتت ربحاً."]
    if alerts:
        L += ["", "⚠️ **بلغت حد الإيقاف (−500$ أو 5 خسائر متتالية):** " + "، ".join(alerts) +
              ". التجريبيتان تتوقفان وحدهما؛ البوتان الأساسيان القرار فيهما لك."]
    L += ["", f"الحكم بعد شهر (~5/12/2026): صافٍ موجب → تبقى، سالب → تُلغى. آخر تحديث {now:%Y-%m-%d %H:%M} نيويورك."]
    return "\n".join(L), alerts


def main(argv):
    now = datetime.now(NY)
    t = trades()
    text, alerts = report(t, now)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8").write(text.replace("**", "") + "\n")
    print(text)
    if "--no-send" not in argv:
        import analyst as an
        an.send(text, "paper_book")
    json.dump({"updated": now.isoformat(timespec="minutes"), "alerts": alerts}, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    main(sys.argv[1:])
