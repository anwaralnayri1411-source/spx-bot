#!/usr/bin/env python3
"""سجل دقة الشاشات (H-SCREENS، مسجّل في CLAUDE.md 8/11/2026 بطلب المستخدم: «البوت جالس امام عدت شاشات… ابدا»).

كل 5 دقائق في الجلسة الرسمية: يقرأ كل شاشة (عوامل بوت SPX، والسيولة، ومحرك MGT، وأطراف النطاق، والأخبار،
والمتداول الذكي، والصقر) ويسجل رأيها: صعود +1، هبوط −1، محايد 0. بعد الإغلاق يقيس حركة SPX بعد كل رأي
(30 و60 دقيقة وحتى الإغلاق)، ويصنف اليوم (اتجاه أو تذبذب)، ويحدّث سجل الدقة التراكمي، ويرسل رسالة مختصرة.
قياس فقط: لا يغيّر أي توصية ولا أي قرار. الملفات في SPXBOT_DATA/screens/ (خارج git).
تشغيل: python screens.py [--force] [--no-send] [--report]
"""
import json
import os
import sys
from datetime import datetime, time as dtime, timedelta

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
DIR = os.path.join(DATA, "screens")
LOG, SCORES, STATE = (os.path.join(DIR, f) for f in ("log.csv", "scorecard.csv", "state.json"))
FROM_T, TO_T, REPORT_AT = dtime(9, 35), dtime(15, 55), dtime(16, 10)
HORIZONS = (30, 60)
FLAT = 0.0002          # حركة أقل من 0.02% لا تُعدّ إصابة ولا خطأ
TREND_RATIO = 0.5      # يوم اتجاه إذا |الإغلاق − الافتتاح| ≥ نصف مدى اليوم
LOG_COLS = ["time", "S", "screen", "dir"]


# ====================== دوال صافية ======================
def sgn(x, eps=0.0):
    return 0 if x is None or x != x or abs(x) <= eps else (1 if x > 0 else -1)


def readings(a=None, flow=None, eng=None, fade=None, ai=None, falcon=None):
    """{اسم الشاشة: +1/0/−1} من مدخلات كل شاشة. أي مدخل غائب يُترك."""
    out = {}
    if a:
        for name, bull, bear, *_ in a.get("rows") or []:
            out[name] = sgn(bull - bear)
        out["خلاصة بوت SPX"] = {"CALL": 1, "PUT": -1}.get(a.get("side"), 0)
        lvl = (a.get("shock") or {}).get("level")
        if lvl:
            out["صدمة الأخبار"] = -1 if lvl >= 2 else 0
    if flow is not None and len(flow):
        r = flow.iloc[-1]
        out["صافي الدلتا"] = sgn(pd.to_numeric(r.get("net_delta"), errors="coerce"))
        lean = pd.to_numeric(flow["call_prem"], errors="coerce").fillna(0).sum() - pd.to_numeric(flow["put_prem"], errors="coerce").fillna(0).sum()
        out["علاوة الكول/البوت 30د"] = sgn(lean)
        flip, spot = pd.to_numeric(r.get("flip"), errors="coerce"), pd.to_numeric(r.get("spot"), errors="coerce")
        if flip == flip and spot == spot:
            out["موقع نقطة الانقلاب"] = sgn(spot - flip)
    if eng is not None:
        tot, adx = eng.get("total", 0), eng.get("adx", float("nan"))
        out["محرك MGT"] = sgn(tot) if abs(tot) >= 3 and adx == adx and adx >= 15 else 0
    if fade is not None:
        out["أطراف النطاق"] = {"CALL": 1, "PUT": -1}.get(fade, 0)
    if ai is not None:
        out["المتداول الذكي"] = ai
    if falcon is not None:
        out["الصقر"] = falcon
    return out


def recent_side(df, now, minutes, time_col="time", side_col="side", ok=None):
    """+1/−1 لآخر شراء خلال minutes دقيقة (CALL/PUT)، و0 لقرار آخر، وNone إن لا قرار حديث."""
    if df is None or not len(df):
        return None
    t = pd.to_datetime(df[time_col], errors="coerce", utc=True)
    x = df[t >= pd.Timestamp(now - timedelta(minutes=minutes)).tz_convert("UTC")]
    if not len(x):
        return None
    r = x.iloc[-1]
    if ok is not None and not ok(r):
        return 0
    return {"CALL": 1, "PUT": -1}.get(str(r.get(side_col)), 0)


def day_type(bars):
    o, c = float(bars["Open"].iloc[0]), float(bars["Close"].iloc[-1])
    rng = float(bars["High"].max() - bars["Low"].min())
    return ("اتجاه" if rng > 0 and abs(c - o) / rng >= TREND_RATIO else "تذبذب"), (c / o - 1)


def score(log, bars):
    """لكل رأي غير محايد: حركة SPX بعده بالاتجاه (30د و60د وحتى الإغلاق) وهل أصاب. bars: شموع 5د بعمود Close."""
    close = bars["Close"]
    last = float(close.iloc[-1])
    rows = []
    for r in log.itertuples():
        if r.dir == 0:
            continue
        t = pd.Timestamp(r.time)
        x = close[close.index <= t]
        if not len(x):
            continue
        s0 = float(x.iloc[-1])
        rec = {"screen": r.screen, "dir": r.dir}
        for h in HORIZONS:
            y = close[close.index <= t + pd.Timedelta(minutes=h)]
            m = float(y.iloc[-1]) / s0 - 1 if len(y) and y.index[-1] > x.index[-1] else np.nan
            rec[f"m{h}"] = r.dir * m
            rec[f"hit{h}"] = np.nan if m != m or abs(m) < FLAT else float(np.sign(m) == r.dir)
        rec["mclose"] = r.dir * (last / s0 - 1)
        rows.append(rec)
    d = pd.DataFrame(rows)
    if d.empty:
        return pd.DataFrame(columns=["screen", "n", "hit30", "m30", "hit60", "mclose"])
    g = d.groupby("screen")
    return pd.DataFrame({"n": g.size(), "hit30": g["hit30"].mean(), "m30": g["m30"].mean(), "hit60": g["hit60"].mean(),
                         "mclose": g["mclose"].mean()}).reset_index()


def day_text(day, dtype, ret, sc, cum):
    L = [f"📺 **الشاشات** · {day}", f"اليوم: **{dtype}** (SPX {ret * 100:+.2f}%)", ""]
    s = sc[sc["n"] >= 5].sort_values("hit30", ascending=False)
    if s.empty:
        return "\n".join(L + ["لا قراءات كافية اليوم."])
    fmt = lambda r: f"{r.screen} {r.hit30:.0%} ({int(r.n)})"
    L.append("✅ الأصدق اليوم (إصابة 30د): " + " · ".join(fmt(r) for r in s.head(3).itertuples()))
    L.append("❌ الأضعف اليوم: " + " · ".join(fmt(r) for r in s.tail(3).iloc[::-1].itertuples()))
    if cum is not None and len(cum):
        days = cum["day"].nunique()
        c = cum.groupby("screen").apply(lambda g: np.average(g["hit30"].fillna(0.5), weights=g["n"]), include_groups=False)
        best = c.sort_values(ascending=False).head(3)
        L.append(f"📊 منذ البداية ({days} يوم): " + " · ".join(f"{k} {v:.0%}" for k, v in best.items()))
    L.append("⚠️ قياس فقط؛ الحكم بعد 10 أيام لكل نوع يوم.")
    return "\n".join(L)


def panel_text(log, cum, now, max_age=10):
    """لوحة الشاشات لقرار الصقر: آخر قراءة لكل شاشة (خلال max_age دقيقة) مع إصابتها التراكمية 30د وعدد قراءاتها."""
    if log is None or not len(log):
        return ""
    t = pd.to_datetime(log["time"], errors="coerce", utc=True)
    x = log[t >= pd.Timestamp(now - timedelta(minutes=max_age)).tz_convert("UTC")]
    if not len(x):
        return ""
    x = x[x["time"] == x["time"].max()]
    rate = {}
    if cum is not None and len(cum):
        for name, g in cum.groupby("screen"):
            n = int(g["n"].sum())
            h = g["hit30"].fillna(0.5)
            rate[name] = (float(np.average(h, weights=g["n"])) if n else None, n, g["day"].nunique())
    word = {1: "صعود", -1: "هبوط", 0: "محايد"}
    up, dn = int((x["dir"] == 1).sum()), int((x["dir"] == -1).sum())
    L = [f"## لوحة الشاشات ({x['time'].iloc[0][11:16]}): صعود {up} · هبوط {dn} · محايد {len(x) - up - dn}",
         "(الدقة = نسبة إصابة الشاشة بعد 30 دقيقة منذ بدء القياس؛ أرقام الأيام الأولى قليلة فلا تعتمد عليها وحدها)"]
    for r in x.itertuples():
        hr = rate.get(r.screen)
        acc = f"دقة {hr[0]:.0%} من {hr[1]} قراءة في {hr[2]} يوم" if hr and hr[1] else "لا سجل بعد"
        L.append(f"- {r.screen}: {word.get(int(r.dir), r.dir)} | {acc}")
    return "\n".join(L)


def panel(now):
    """لوحة الشاشات من ملفات الخادم، أو "" إن لا قراءة حديثة."""
    try:
        log = pd.read_csv(LOG)
        cum = pd.read_csv(SCORES) if os.path.exists(SCORES) else None
        return panel_text(log, cum, now)
    except Exception as e:  # noqa: BLE001
        print("لوحة الشاشات:", type(e).__name__)
        return ""


# ====================== التشغيل ======================
def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"report_day": ""}


def snapshot(now):
    import options_bot as ob
    a = ob.analyze(False)
    S = float(a["c"]["S"])
    flow = None
    try:
        fl = pd.read_csv(os.path.join(ROOT, "spx_flow_log.csv"), dtype={"time": str})
        fl = fl[fl["date"] == now.date().isoformat()]
        flow = fl[fl["time"] >= (now - timedelta(minutes=30)).strftime("%H:%M")]
    except Exception as e:  # noqa: BLE001
        print("السيولة:", type(e).__name__)
    eng, fade = None, None
    try:
        import mgt_lab as ml
        import mgt_shadow as ms
        m1, s_eq = ml.spx_m1()
        sb = m1[m1.index >= datetime.combine(now.date(), ms.RTH_START, ms.NY)]
        so = float(sb["o"].iloc[0]) if len(sb) else None
        vw = float((sb["c"] * sb["v"]).sum() / sb["v"].sum()) if len(sb) and sb["v"].sum() > 0 else None
        _s, total, det = ms.decide(m1, so, vw)
        eng = {"total": total, "adx": det["adx"]}
        c = m1["c"]
        fade = ms.fade_side(sb, s_eq, s_eq / float(c.iloc[-31]) - 1 if len(c) > 31 else None) or "none"
    except Exception as e:  # noqa: BLE001
        print("المحرك:", type(e).__name__, e)
    ai = falcon = None
    try:
        ai = recent_side(pd.read_csv(os.path.join(ROOT, "ai_trader_log.csv"), dtype=str, keep_default_na=False), now, 15,
                         ok=lambda r: r.get("action") == "شراء")
    except Exception:  # noqa: BLE001
        pass
    try:
        falcon = recent_side(pd.read_csv(os.path.join(DATA, "falcon", "signals.csv"), dtype=str, keep_default_na=False), now, 5,
                             ok=lambda r: r.get("decision") == "قبول")
    except Exception:  # noqa: BLE001
        pass
    rd = readings(a, flow, eng, fade, ai, falcon)
    t = now.isoformat(timespec="minutes")
    rows = [{"time": t, "S": round(S, 2), "screen": k, "dir": v} for k, v in rd.items()]
    os.makedirs(DIR, exist_ok=True)
    pd.DataFrame(rows, columns=LOG_COLS).to_csv(LOG, mode="a", header=not os.path.exists(LOG), index=False)
    print(t, len(rows), "شاشة", rd)


def report(now, no_send):
    import yfinance as yf
    day = now.date().isoformat()
    log = pd.read_csv(LOG)
    log = log[log["time"].str[:10] == day]
    if log.empty:
        return None
    bars = yf.Ticker("^GSPC").history(start=day, end=(now.date() + timedelta(days=1)).isoformat(), interval="5m")
    bars.index = bars.index.tz_convert("America/New_York")
    bars = bars[(bars.index.strftime("%H:%M") >= "09:30") & (bars.index.strftime("%H:%M") < "16:00")]
    dtype, ret = day_type(bars)
    sc = score(log, bars).assign(day=day, day_type=dtype)
    cum = pd.read_csv(SCORES) if os.path.exists(SCORES) else None
    cum = pd.concat([cum[cum["day"] != day], sc]) if cum is not None else sc
    cum.to_csv(SCORES, index=False)
    txt = day_text(day, dtype, ret, sc, cum)
    print(txt)
    if not no_send:
        import analyst as an
        an.send(txt, "screens")
    return txt


def main(argv):
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    force, no_send = "--force" in argv, "--no-send" in argv
    if now.weekday() >= 5 and not force:
        return
    st = load_state()
    if (now.time() >= REPORT_AT or "--report" in argv) and st.get("report_day") != now.date().isoformat():
        if report(now, no_send) is not None:
            st["report_day"] = now.date().isoformat()
            os.makedirs(DIR, exist_ok=True)
            json.dump(st, open(STATE, "w", encoding="utf-8"))
        return
    if FROM_T <= now.time() <= TO_T or force:
        snapshot(now)


if __name__ == "__main__":
    main(sys.argv[1:])
