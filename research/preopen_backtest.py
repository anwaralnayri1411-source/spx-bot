"""اختبار تاريخي لتوصية ما قبل الافتتاح المشروطة (SPX، 0DTE). يدوي، لا يغيّر البوت ولا الكاش.

القاعدة (مثبّتة قبل الاختبار، من research/premarket_results.md: بعد فجوة ±0.3% أغلقت الجلسة بنفس الاتجاه 65%/56%
على 2010-2026):
  الفجوة = افتتاح 9:30 / إغلاق أمس − 1.  ≥ +0.3% → CALL،  ≤ −0.3% → PUT،  غير ذلك لا توصية.
  التأكيد: SPX عند 9:45 في نفس اتجاه الفجوة من سعر الافتتاح (وإلا لا دخول). ونسخة بلا تأكيد للمقارنة.
  الدخول 9:45 بسعر الطلب النموذجي، والعقد بشروط البوت (bt.pick_contract)، والمتابعة بقواعد البوت (bt.follow:
  +50% / −40% بعد 15د / 15:30، على بيانات متأخرة 15د كما في spx_monitor).
المراجع: نفس اللحظة بالاتجاه المعاكس، و«كل يوم CALL عند 9:45» بلا شرط.
المقياس الثاني (بلا تسعير): هل لمس SPX +0.25% باتجاه التوصية قبل −0.25% بعد الدخول.
أسعار العقود نظرية (بلاك-شولز، VIX أمس). العينة 60 يوماً فقط: أيام الفجوة قليلة.
تشغيل (على الخادم، يحتاج yfinance): python research/preopen_backtest.py  → research/preopen_backtest_results.md
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import backtest_spx_v2 as bt   # noqa: E402  (التسعير والمتابعة واختيار العقد)
import options_bot as ob       # noqa: E402

OUT = os.path.join(HERE, "preopen_backtest_results.md")
GAP_T = 0.003
ENTRY_M = 9 * 60 + 45
TOUCH = 0.0025
V = bt.HEADLINE


def load():
    import yfinance as yf
    spx = bt._ny(yf.Ticker("^GSPC").history(period="60d", interval="5m", auto_adjust=False))[["Open", "High", "Low", "Close"]]
    if os.path.exists(bt.CACHE):
        old = pd.read_csv(bt.CACHE, index_col=0)
        old.index = pd.to_datetime(old.index, utc=True).tz_convert(ob.NY)
        spx = pd.concat([old, spx])
    spx = spx[~spx.index.duplicated(keep="last")].sort_index()
    m = spx.index.hour * 60 + spx.index.minute
    spx = spx[(m >= bt.OPEN_M) & (m < bt.CLOSE_M)]
    vixd = yf.Ticker("^VIX").history(period="1y", auto_adjust=False)["Close"].dropna()
    vixd.index = pd.to_datetime(vixd.index.date)
    return spx, vixd


def touch_first(g, m, side, S0):
    """+1 إن لمس SPX +0.25% باتجاه التوصية أولاً، −1 إن لمس العكس أولاً، 0 إن لم يلمس أياً منهما."""
    sg = 1 if side == "CALL" else -1
    for hi, lo, mm in zip(g["High"].values, g["Low"].values, m):
        if mm < ENTRY_M:
            continue
        up, dn = hi / S0 - 1 >= TOUCH, lo / S0 - 1 <= -TOUCH
        if up and dn:
            return 0
        if up:
            return sg
        if dn:
            return -sg
    return 0


def trade(g, m, side, S, iv0, open_px):
    pk = bt.pick_contract(side, S, ENTRY_M, iv0, open_px, V)
    if not pk:
        return None
    K, ask = pk
    real = ask + bt.COMMISSION
    out, ex_m, pnl = bt.follow(g, m, side, K, ENTRY_M, ask, real, iv0, open_px, V)
    return {"K": K, "entry": round(ask, 2), "out": out, "exit_m": ex_m, "pnl": pnl}


def simulate(spx, vixd):
    rows = []
    dates = sorted(set(spx.index.date))
    for di, d in enumerate(dates[1:], 1):
        g, m = bt.day_bars(spx, d)
        p, pm = bt.day_bars(spx, dates[di - 1])
        if len(g) < 70 or len(p) < 70:
            continue
        prev_close = float(p["Close"].values[-1])
        open_px = float(g["Open"].values[0])
        gap = open_px / prev_close - 1
        S = bt.s_at(g, m, ENTRY_M, False)
        vprev = vixd[vixd.index < pd.Timestamp(d)]
        iv0 = float(vprev.iloc[-1]) / 100 if len(vprev) else 0.16
        side = "CALL" if gap >= GAP_T else ("PUT" if gap <= -GAP_T else "")
        confirm = bool(side) and ((S >= open_px) if side == "CALL" else (S <= open_px))
        r = {"date": d.isoformat(), "gap": gap, "side": side, "confirm": confirm, "S945": S}
        close = float(g["Close"].values[-1])
        r["day_with_gap"] = (np.sign(close - open_px) == np.sign(gap)) if side else None
        for role, s_ in (("rule", side), ("opp", {"CALL": "PUT", "PUT": "CALL"}.get(side, ""))):
            if s_:
                t = trade(g, m, s_, S, iv0, open_px)
                r[f"{role}_pnl"] = None if t is None else t["pnl"]
                r[f"{role}_out"] = None if t is None else t["out"]
                r[f"{role}_touch"] = touch_first(g, m, s_, S)
        t = trade(g, m, "CALL", S, iv0, open_px)
        r["allcall_pnl"] = None if t is None else t["pnl"]
        rows.append(r)
    return pd.DataFrame(rows)


def line(label, s, touch=None):
    s = s.dropna()
    if not len(s):
        return f"| {label} | 0 | — | — | — | — |"
    tp = (s >= ob.TAKE_PROFIT - 0.05).mean() * 100
    tc = "—" if touch is None else f"{(touch.dropna() == 1).mean() * 100:.0f}% / {(touch.dropna() == -1).mean() * 100:.0f}%"
    return f"| {label} | {len(s)} | {s.mean() * 100:+.1f}% | {s.median() * 100:+.1f}% | {tp:.0f}% | {tc} |"


def report(df):
    gd = df[df["side"] != ""]
    cf = gd[gd["confirm"]]
    L = ["# اختبار توصية ما قبل الافتتاح المشروطة (60 يوماً، تسعير نظري)", "",
         f"- الأيام: **{len(df)}** ({df['date'].min()} → {df['date'].max()}) | أيام فجوة ≥ {GAP_T * 100:.1f}%: **{len(gd)}** "
         f"(صعود {int((gd['side'] == 'CALL').sum())}، هبوط {int((gd['side'] == 'PUT').sum())}) | مع تأكيد 9:45: **{len(cf)}**",
         f"- أغلقت الجلسة بنفس اتجاه الفجوة (افتتاح→إغلاق): **{gd['day_with_gap'].astype(float).mean() * 100:.0f}%** "
         f"(المرجع التاريخي 2010-2026: 65% صعوداً / 56% هبوطاً)", "",
         "| المجموعة | صفقات | متوسط العقد | الوسيط | بلغ +50% | لمس ±0.25% (مع / ضد) |", "|---|---|---|---|---|---|",
         line("القاعدة مع تأكيد 9:45", cf["rule_pnl"], cf["rule_touch"]),
         line("المعاكس (نفس الأيام)", cf["opp_pnl"], cf["opp_touch"]),
         line("القاعدة بلا تأكيد", gd["rule_pnl"], gd["rule_touch"]),
         line("المعاكس بلا تأكيد", gd["opp_pnl"], gd["opp_touch"]),
         line("كل يوم CALL عند 9:45 (بلا شرط)", df["allcall_pnl"]), "",
         "## الأيام", "", "| اليوم | الفجوة | الاتجاه | تأكيد | العقد | النتيجة | لمس |", "|---|---|---|---|---|---|---|"]
    for r in gd.itertuples():
        pnl = "—" if pd.isna(r.rule_pnl) else f"{r.rule_pnl * 100:+.0f}%"
        L.append(f"| {r.date} | {r.gap * 100:+.2f}% | {r.side} | {'✓' if r.confirm else '✗'} | {pnl} | {r.rule_out or '—'} | "
                 f"{ {1: 'مع', -1: 'ضد', 0: 'لا'}.get(r.rule_touch, '—')} |")
    L += ["", "## تحفظات", "- العينة صغيرة جداً (أقل من 20 صفقة): لا حكم منها؛ الحكم للظل الحي ثم عينة أكبر.",
          "- أسعار العقود نظرية (بلاك-شولز بتذبذب VIX أمس + نصف فرق تقريبي + عمولة)، والمتابعة على بيانات متأخرة 15د.",
          "- القاعدة مثبّتة مسبقاً من دراسة يومية 2010-2026؛ لم تُضبط العتبة على هذه البيانات."]
    open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    report(simulate(*load()))
