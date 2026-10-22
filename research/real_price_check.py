#!/usr/bin/env python3
"""تدقيق الاختبار التاريخي: نفس صفقات الإشارة بأسعار عقود حقيقية (شموع Alpaca 5د لعقود SPXW) مقابل التسعير النظري.
(بطلب المستخدم 23/10، تدقيق الانحيازات بمهارة backtesting-frameworks.)

لكل إشارة من research/structure_study.signals (دالة البوت، شموع متأخرة 15د) وفي الاتجاهين (البوت والمعاكس):
  - العقد: خارج المال 0DTE بقواعد البوت (bt.pick_contract) وعلى السعر 1DTE (أفضل خيط في دراسة الهيكل).
  - الخروج: +50/−40 (البوت، الوقف بعد 15د) وخروج بعد 60د؛ آخر خروج 15:30.
  - النظري: بلاك-شولز على مسار SPX الحقيقي (كما في الدراسات). الحقيقي: الدخول بافتتاح أول شمعة صفقات ≥ وقت الإشارة،
    والقيمة بعدها = إغلاق كل شمعة 5د. التكلفة نفسها للاثنين: نصف فرق 1.5% (المقاس من Alpaca 19–22/10) + عمولة 0.65$.
النتيجة: هل يغيّر التسعير الحقيقي الحكم؟ (متوسط الصفقة، وقيمة الاتجاه = البوت − المعاكس). يحتاج ALPACA_KEY/SECRET (الخادم).
شموع الصفقات قد تغيب لعقد لم يُتداول في شمعة ما: نستعمل آخر سعر معروف، ونستبعد العقد إن لم يُتداول حول الدخول.
تشغيل: python research/real_price_check.py  ← research/real_price_check_results.md
"""
import json
import math
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import structure_study as ss   # noqa: E402
sys.path.insert(0, os.path.dirname(HERE))
import live_quotes as lq   # noqa: E402
bt, ob = ss.bt, ss.ob

OUT = os.path.join(HERE, "real_price_check_results.md")
HS = 0.015
STRUCT = {"otm0": "خارج المال 0DTE (البوت)", "atm1": "على السعر 1DTE"}
EXIT = {"50_40": "+50/−40 (البوت)", "t60": "خروج بعد 60د"}


def bars(symbols, d):
    """{رمز: DataFrame(دقيقة، o، c)} لشموع اليوم d (توقيت نيويورك)."""
    out, page = {}, None
    start = pd.Timestamp(datetime(d.year, d.month, d.day, 9, 30), tz=ob.NY).tz_convert("UTC")
    end = pd.Timestamp(datetime(d.year, d.month, d.day, 16, 0), tz=ob.NY).tz_convert("UTC")
    while True:
        q = {"symbols": ",".join(symbols), "timeframe": "5Min", "start": start.isoformat(), "end": end.isoformat(), "limit": 10000}
        if page:
            q["page_token"] = page
        req = urllib.request.Request("https://data.alpaca.markets/v1beta1/options/bars?" + urllib.parse.urlencode(q),
                                     headers={"APCA-API-KEY-ID": os.getenv("ALPACA_KEY", ""),
                                              "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET", "")})
        j = json.loads(urllib.request.urlopen(req, timeout=30).read())
        for s, b in (j.get("bars") or {}).items():
            out.setdefault(s, []).extend(b)
        page = j.get("next_page_token")
        if not page:
            break
    res = {}
    for s, b in out.items():
        df = pd.DataFrame(b)
        t = pd.to_datetime(df["t"]).dt.tz_convert(ob.NY)
        df["m"] = t.dt.hour * 60 + t.dt.minute
        res[s] = df.set_index("m")[["o", "c"]]
    return res


def real_follow(df, run_m, ex):
    tp, sl, tmax = ss.EXITS[ex]
    first = df[(df.index >= run_m) & (df.index <= run_m + 10)]
    if first.empty:
        return None
    entry = float(first["o"].iloc[0]) * (1 + HS) + bt.COMMISSION
    last = None
    for m_, r in df[df.index >= run_m].iterrows():
        tau = m_ + 5                                        # إغلاق الشمعة
        last = float(r["c"])
        pnl = (last * (1 - HS) - bt.COMMISSION) / entry - 1
        if tmax and tau - run_m >= tmax:
            return pnl
        if sl and tau - run_m >= ob.MIN_AGE_MIN and pnl <= -sl:
            return pnl
        if tp and pnl >= tp:
            return pnl
        if tau >= bt.FINAL_M:
            return pnl
    return None if last is None else (last * (1 - HS) - bt.COMMISSION) / entry - 1


def theo_follow(g, m, side, K, days, run_m, iv0, open_px, ex):
    S = bt.s_at(g, m, run_m, False)
    mid = ss.price_d(S, K, run_m, side, iv0, open_px, days)
    entry = mid * (1 + HS) + bt.COMMISSION
    tp, sl, tmax = ss.EXITS[ex]
    tau = run_m + 5
    while tau <= bt.FINAL_M:
        S = bt.s_at(g, m, tau, False)
        if S is not None:
            pnl = (ss.price_d(S, K, tau, side, iv0, open_px, days) * (1 - HS) - bt.COMMISSION) / entry - 1
            if (tmax and tau - run_m >= tmax) or (sl and tau - run_m >= ob.MIN_AGE_MIN and pnl <= -sl) \
                    or (tp and pnl >= tp) or tau >= bt.FINAL_M:
                return pnl
        tau += 5
    return pnl


def main():
    data = bt.load()
    sig = ss.signals(data)
    spx = data["spx"]
    days_all = sorted(set(spx.index.date))
    rows = []
    for d, sd in sig.groupby("day"):
        g, m = bt.day_bars(spx, d)
        vprev = data["vixd"][data["vixd"].index < pd.Timestamp(d)]
        iv0 = float(vprev.iloc[-1]) / 100 if len(vprev) else 0.16
        open_px = float(g["Open"].values[0])
        nxt = days_all[days_all.index(d) + 1] if days_all.index(d) + 1 < len(days_all) else None
        legs = []
        for _, s in sd.iterrows():
            run_m = int(s["run_m"])
            S = bt.s_at(g, m, run_m, False)
            for role, side in (("bot", s["side"]), ("opp", "PUT" if s["side"] == "CALL" else "CALL")):
                pk = bt.pick_contract(side, S, run_m, iv0, open_px, "base")
                if pk:
                    legs.append((run_m, role, side, "otm0", pk[0], 0, d, s["edge"]))
                if nxt:
                    legs.append((run_m, role, side, "atm1", float(round(S / 5) * 5), 1, nxt, s["edge"]))
        syms = {L: lq.occ("SPXW", L[6].isoformat(), L[2], L[4]) for L in legs}
        try:
            B = {}
            u = sorted(set(syms.values()))
            for i in range(0, len(u), 50):
                B.update(bars(u[i:i + 50], d))
        except Exception as e:
            print(d, "تعذر جلب الشموع:", e)
            continue
        for L in legs:
            run_m, role, side, kind, K, days, _, edge = L
            df = B.get(syms[L])
            for ex in EXIT:
                real = real_follow(df, run_m, ex) if df is not None else None
                theo = theo_follow(g, m, side, K, days, run_m, iv0, open_px, ex)
                rows.append({"day": d, "run_m": run_m, "edge": edge, "role": role, "kind": kind, "exit": ex, "real": real, "theo": theo})
        print(d, len(legs))
    R = pd.DataFrame(rows)
    R.to_csv(os.path.join(HERE, "real_price_check_trades.csv"), index=False)
    pct = lambda v: "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{100 * v:+.1f}%"
    L = ["# تدقيق: الأسعار الحقيقية مقابل النظرية (نتائج آلية)", "",
         f"- الفترة {days_all[0]} → {days_all[-1]}، إشارات البوت {len(sig)} في {sig['day'].nunique()} يوماً، كل إشارة بالاتجاهين.",
         "- الحقيقي = شموع صفقات Alpaca لعقد SPXW نفسه؛ النظري = بلاك-شولز (تذبذب VIX أمس) على مسار SPX نفسه. التكلفة نفسها للاثنين.", "",
         "| العقد | الخروج | صفقات بسعر حقيقي | متوسط البوت (نظري) | متوسط البوت (حقيقي) | قيمة الاتجاه (نظري) | قيمة الاتجاه (حقيقي) | ارتباط نظري/حقيقي |",
         "|---|---|---|---|---|---|---|---|"]
    for edge_min, kind, ex in [(e, k, x) for e in (3, 7) for k in STRUCT for x in EXIT]:
        if True:
            if kind == "otm0" and ex == "50_40" and edge_min == 7:
                L += ["", "**الإشارات القوية فقط (فرق ≥ 7):**", "", L[5], L[6]]
            t = R[(R["kind"] == kind) & (R["exit"] == ex) & (R["edge"] >= edge_min)].dropna(subset=["real"])
            b, o = t[t["role"] == "bot"], t[t["role"] == "opp"]
            dv = {}
            for col in ("theo", "real"):
                j = b.groupby("day")[col].mean().to_frame("b").join(o.groupby("day")[col].mean().to_frame("o"), how="inner")
                diff = (j["b"] - j["o"]).values
                rng = np.random.default_rng(3)
                bs_ = [diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(2000)] if len(diff) else [float("nan")]
                dv[col] = f"{pct(diff.mean() if len(diff) else float('nan'))} ({pct(np.percentile(bs_, 2.5))} … {pct(np.percentile(bs_, 97.5))})"
            corr = t[["theo", "real"]].corr().iloc[0, 1] if len(t) > 2 else float("nan")
            L.append(f"| {STRUCT[kind]} | {EXIT[ex]} | {len(b)} | {pct(b['theo'].mean())} | {pct(b['real'].mean())} | "
                     f"{dv['theo']} | {dv['real']} | {corr:.2f} |")
    miss = R[R["real"].isna()]
    L += ["", f"- صفقات بلا شموع حقيقية حول الدخول (مستبعدة): {len(miss)} من {len(R)}."]
    open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
