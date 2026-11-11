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
NLOG, NSCORES, NSTATE, PREOPEN = (os.path.join(DIR, f) for f in ("night_log.csv", "night_scorecard.csv", "night_state.json", "preopen.csv"))
NIGHT_FROM, NIGHT_TO, CARD_FROM, CARD_TO = dtime(18, 0), dtime(9, 30), dtime(9, 0), dtime(9, 15)
EUROPE_FROM, EUROPE_TO = dtime(3, 0), dtime(11, 30)
NIGHT_MARKETS = {"أوروبا": (["^GDAXI", "^FTSE", "^STOXX50E"], 1), "آسيا": (["^N225", "^HSI"], 1), "الدولار": (["DX-Y.NYB"], -1),
                 "الين": (["USDJPY=X"], 1), "السندات العشرية": (["ZN=F"], 1), "VIX": (["^VIX"], -1), "الذهب": (["GC=F"], -1),
                 "البتكوين": (["BTC-USD"], 1)}
FROM_T, TO_T, REPORT_AT = dtime(9, 35), dtime(15, 55), dtime(16, 10)
HORIZONS = (30, 60)
FLAT = 0.0002          # حركة أقل من 0.02% لا تُعدّ إصابة ولا خطأ
TREND_RATIO = 0.5      # يوم اتجاه إذا |الإغلاق − الافتتاح| ≥ نصف مدى اليوم
LOG_COLS = ["time", "S", "screen", "dir"]


# ====================== دوال صافية ======================
def sgn(x, eps=0.0):
    return 0 if x is None or x != x or abs(x) <= eps else (1 if x > 0 else -1)


def readings(a=None, flow=None, eng=None, fade=None, ai=None, falcon=None, heavy=None):
    """{اسم الشاشة: +1/0/−1} من مدخلات كل شاشة. أي مدخل غائب يُترك."""
    out = {}
    if a:
        for name, bull, bear, *_ in a.get("rows") or []:
            out[name] = sgn(bull - bear)
        out["خلاصة بوت SPX"] = {"CALL": 1, "PUT": -1}.get(a.get("side"), 0)
        mh = (a.get("c") or {}).get("macd")
        if mh is not None:
            out["MACD 5د"] = sgn(mh)          # H-BHVOTE: شاشة جديدة 11/11/2026
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
    if heavy is not None:
        out["الشركات الثقيلة"] = heavy
    return out


def in_night(now):
    t, wd = now.time(), now.weekday()
    return (t >= NIGHT_FROM and wd in (6, 0, 1, 2, 3)) or (t < NIGHT_TO and wd in (0, 1, 2, 3, 4))


def night_readings(es=None, settle=None, es60=None, eng=None, changes=None, now=None, heavy=None):
    """{شاشة: +1/0/−1} ليلاً. changes: {اسم السوق: متوسط التغير اليومي}."""
    out = {}
    if es and settle:
        out["ES منذ التسوية"] = sgn(es / settle - 1, 0.001)
    if es and es60:
        out["زخم ES 60د"] = sgn(es / es60 - 1, 0.0005)
    if eng is not None:
        tot, adx = eng.get("total", 0), eng.get("adx", float("nan"))
        out["محرك MGT ليلي"] = sgn(tot) if abs(tot) >= 3 and adx == adx and adx >= 15 else 0
    for name, ch in (changes or {}).items():
        if name == "أوروبا" and now is not None and not EUROPE_FROM <= now.time() <= EUROPE_TO:
            continue
        if ch is None or ch != ch:
            continue
        out[name] = NIGHT_MARKETS[name][1] * sgn(ch, 0.02 if name == "VIX" else 0.001)
    if heavy is not None:
        out["الشركات الثقيلة"] = heavy
    return out


def consensus(rd):
    """(الاتجاه +1/−1/0، صعود، هبوط) من مجموع الآراء."""
    up, dn = sum(v == 1 for v in rd.values()), sum(v == -1 for v in rd.values())
    return sgn(up - dn), up, dn


def card_text(rd, es_ch, day):
    d, up, dn = consensus(rd)
    word = {1: "صعود", -1: "هبوط", 0: "غير واضحة"}[d]
    key = [k for k in ("أوروبا", "آسيا", "الدولار", "VIX") if k in rd]
    ctx = " · ".join(f"{k} {'↑' if rd[k] * NIGHT_MARKETS[k][1] > 0 else '↓' if rd[k] else '–'}" for k in key)
    return (f"🌙 **قبل الافتتاح** · {day}\n"
            f"القراءة: **{word}** ({up} صعود · {dn} هبوط من {len(rd)} شاشة) | ES {es_ch * 100:+.2f}% منذ التسوية\n"
            + (f"{ctx}\n" if ctx else "") + "⚠️ قراءة الليل للدراسة، وليست توصية.")


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


def night_line(pre, day):
    """سطر قراءة الليل للوحة الصقر من قراءة 09:00 المحفوظة."""
    if not pre or pre.get("day") != day:
        return ""
    w = {1: "صعود", -1: "هبوط", 0: "غير واضحة"}[pre["dir"]]
    return f"## قراءة الليل (09:00): {w} ({pre['up']} صعود · {pre['dn']} هبوط) | ES {pre['es_ch'] * 100:+.2f}% منذ التسوية"


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
        nl = night_line(load_state(NSTATE).get("preopen"), now.date().isoformat())
        log = pd.read_csv(LOG)
        cum = pd.read_csv(SCORES) if os.path.exists(SCORES) else None
        return "\n".join(x for x in (nl, panel_text(log, cum, now)) if x)
    except Exception as e:  # noqa: BLE001
        print("لوحة الشاشات:", type(e).__name__)
        return ""


# ====================== التشغيل ======================
def load_state(path=None):
    try:
        return json.load(open(path or STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"report_day": ""}


def heavy_dir(now, no_send=False):
    """اتجاه أثر الشركات الثقيلة (H-HEAVY) مع التنبيه عند الحاجة، أو None إن لا أخبار."""
    try:
        import heavyweights as hv
        imp = hv.current(now)
        if not imp:
            return None
        if "--no-send" not in sys.argv:
            hv.maybe_alert(imp, now, no_send)
        return hv.net_dir(imp)
    except Exception as e:  # noqa: BLE001
        print("الشركات الثقيلة:", type(e).__name__)
        return None


def market_changes():
    """{اسم السوق: متوسط تغيره منذ الإغلاق السابق} من ياهو (متأخر ~15 دقيقة)."""
    import yfinance as yf
    out = {}
    for name, (syms, _sign) in NIGHT_MARKETS.items():
        ch = []
        for s in syms:
            try:
                f = yf.Ticker(s).fast_info
                ch.append(float(f["last_price"]) / float(f["previous_close"]) - 1)
            except Exception:  # noqa: BLE001
                pass
        out[name] = sum(ch) / len(ch) if ch else None
    return out


def night_snapshot(now, no_send):
    import mgt_lab as ml
    import mgt_shadow as ms
    es, settle = ms.es_mark()
    ticks = ms.load_ticks(days=2)
    es60 = None
    if ticks is not None and len(ticks):
        old = ticks[ticks.index <= now - timedelta(minutes=60)]
        es60 = float(old["mark"].iloc[-1]) if len(old) else None
    eng = None
    try:
        m1 = ms.live_m1(ms.es_bars(), ticks)
        _s, total, det = ms.decide(m1, None, None)
        eng = {"total": total, "adx": det["adx"]}
    except Exception as e:  # noqa: BLE001
        print("المحرك الليلي:", type(e).__name__)
    rd = night_readings(es, settle, es60, eng, market_changes(), now, heavy_dir(now, no_send))
    t = now.isoformat(timespec="minutes")
    os.makedirs(DIR, exist_ok=True)
    pd.DataFrame([{"time": t, "S": round(es, 2), "screen": k, "dir": v} for k, v in rd.items()], columns=LOG_COLS) \
        .to_csv(NLOG, mode="a", header=not os.path.exists(NLOG), index=False)
    print(t, "ليلي", rd)
    st = load_state(NSTATE)
    day = now.date().isoformat()
    if CARD_FROM <= now.time() <= CARD_TO and st.get("card_day") != day and now.weekday() < 5:
        d, up, dn = consensus(rd)
        st["preopen"] = {"day": day, "dir": d, "up": up, "dn": dn, "es_ch": round(es / settle - 1, 5) if settle else 0.0}
        txt = card_text(rd, st["preopen"]["es_ch"], day)
        print(txt)
        if not no_send:
            import analyst as an
            an.send(txt, "screens")
        st["card_day"] = day
        json.dump(st, open(NSTATE, "w", encoding="utf-8"))


def night_report(now, bars):
    """(سطر الليل لرسالة اليوم) من سجل الليل السابق: دقة الشاشات على ES، وقراءة 09:00 مقابل أول ساعة."""
    import mgt_shadow as ms
    day = now.date().isoformat()
    try:
        log = pd.read_csv(NLOG)
    except Exception:  # noqa: BLE001
        return ""
    t = pd.to_datetime(log["time"], errors="coerce", utc=True).dt.tz_convert("America/New_York")
    start = datetime.combine(now.date() - timedelta(days=3 if now.weekday() == 0 else 1), NIGHT_FROM, t.dt.tz)
    log = log[(t >= start) & (t.dt.strftime("%Y-%m-%d %H:%M") < f"{day} 09:30")]
    L = []
    ticks = ms.load_ticks(days=4)
    if len(log) and ticks is not None and len(ticks):
        es = ticks["mark"].resample("1min").last().dropna().to_frame("Close")
        sc = score(log, es).assign(day=day)
        cum = pd.read_csv(NSCORES) if os.path.exists(NSCORES) else None
        cum = pd.concat([cum[cum["day"] != day], sc]) if cum is not None else sc
        cum.to_csv(NSCORES, index=False)
        s = sc[sc["n"] >= 4].sort_values("hit30", ascending=False)
        if len(s):
            L.append("🌙 الليل (إصابة ES بعد 30د): " + " · ".join(f"{r.screen} {r.hit30:.0%}" for r in s.head(3).itertuples()))
    pre = load_state(NSTATE).get("preopen")
    if pre and pre.get("day") == day and bars is not None and len(bars):
        o = float(bars["Open"].iloc[0])
        h = bars[bars.index.strftime("%H:%M") <= "10:25"]
        fh = float(h["Close"].iloc[-1]) / o - 1 if len(h) else float("nan")
        ok = "✅" if sgn(fh, FLAT) == pre["dir"] and pre["dir"] else ("❌" if pre["dir"] and sgn(fh, FLAT) else "–")
        w = {1: "صعود", -1: "هبوط", 0: "غير واضحة"}[pre["dir"]]
        L.append(f"🌙 قراءة 09:00: {w} ← أول ساعة {fh * 100:+.2f}% {ok}")
        pd.DataFrame([{"day": day, "dir": pre["dir"], "first_hour": round(fh, 5)}]).to_csv(
            PREOPEN, mode="a", header=not os.path.exists(PREOPEN), index=False)
    return "\n".join(L)


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
    rd = readings(a, flow, eng, fade, ai, falcon, heavy_dir(now))
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
    try:
        nl = night_report(now, bars)
    except Exception as e:  # noqa: BLE001
        print("تقرير الليل:", type(e).__name__, e)
        nl = ""
    if nl:
        txt = txt.replace("⚠️ قياس فقط", nl + "\n⚠️ قياس فقط", 1)
    print(txt)
    if not no_send:
        import analyst as an
        an.send(txt, "screens")
    return txt


def main(argv):
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    force, no_send = "--force" in argv, "--no-send" in argv
    if "--night" in argv:
        if in_night(now) or force:
            night_snapshot(now, no_send)
        return
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
