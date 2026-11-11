"""الجدران الحية وخريطة السيولة كل ساعة (H-LIVEWALLS، الإصدار 22؛ مسجّل في CLAUDE.md 11/11/2026 بقرار المستخدم).

من لقطات مراقب السيولة (SPXBOT_DATA/spx_flow_raw/YYYY-MM-DD.csv.gz، كل 5 دقائق، حجم اليوم لكل سترايك 0DTE):
جدار الكول = أكبر حجم كول فوق السعر بـ MIN_GAP، وجدار البوت = أكبر حجم بوت تحته؛ وحالته في آخر ساعة (قوي/هش/عادي)
وانتقاله وحصة الشراء عنده (تقدير). دوال صافية عدا load.
"""
import os

import pandas as pd

MIN_GAP = 3.0
BACK = 60
STRONG, FRAGILE = 1.2, 0.6
STATE_AR = {"strong": "قوي", "fragile": "هش", "normal": "عادي"}


def raw_dir():
    return os.path.join(os.getenv("SPXBOT_DATA", "/opt/spxbot/data"), "spx_flow_raw")


def load(day, path=None):
    """لقطات اليوم أو None."""
    try:
        p = os.path.join(path or raw_dir(), f"{day}.csv.gz")
        df = pd.read_csv(p, usecols=["time", "side", "strike", "volume", "gamma", "delta", "bid", "ask", "last"], dtype={"time": str})
        return df if len(df) else None
    except Exception:  # noqa: BLE001
        return None


def _m(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def wall(snap, side, S, min_gap=MIN_GAP):
    """(السترايك، الحجم) لأكبر حجم في جهة side بعد السعر، أو None."""
    d = snap[snap["side"] == side]
    d = d[d["strike"] > S + min_gap] if side == "C" else d[d["strike"] < S - min_gap]
    d = d[d["volume"] > 0]
    if not len(d):
        return None
    r = d.loc[d["volume"].idxmax()]
    return float(r["strike"]), float(r["volume"])


def _vol(snap, side, K):
    x = snap[(snap["side"] == side) & (snap["strike"] == K)]["volume"]
    return float(x.iloc[0]) if len(x) else 0.0


def buy_share(df, side, K, t0, t1):
    """حصة الحجم المضاف عند K بين t0 وt1 الذي نُفذ عند المنتصف أو أعلى (تقدير الشراء)، أو None."""
    d = df[(df["side"] == side) & (df["strike"] == K) & (df["time"] >= t0) & (df["time"] <= t1)].sort_values("time")
    buys = tot = 0.0
    prev = None
    for r in d.itertuples():
        if prev is not None:
            dv = max(float(r.volume) - prev, 0.0)
            tot += dv
            mid = (float(r.bid) + float(r.ask)) / 2
            if float(r.last) >= mid:
                buys += dv
        prev = float(r.volume)
    return round(buys / tot, 2) if tot > 0 else None


def side_state(df, side, S, t_now, t_then):
    """{wall، then_wall، state، share_hour، share_day، buy} لجهة واحدة، أو None."""
    now = df[df["time"] == t_now]
    w = wall(now, side, S)
    if not w:
        return None
    out = {"wall": w, "then_wall": None, "state": "normal", "share_hour": None, "share_day": None, "buy": None}
    tot_now = float(now[now["side"] == side]["volume"].sum())
    out["share_day"] = round(w[1] / tot_now, 3) if tot_now > 0 else None
    if t_then and _m(t_now) - _m(t_then) >= 30:
        then = df[df["time"] == t_then]
        tw = wall(then, side, S)
        out["then_wall"] = tw
        added = tot_now - float(then[then["side"] == side]["volume"].sum())
        mine = w[1] - _vol(then, side, w[0])
        if added > 0 and out["share_day"]:
            sh = mine / added
            out["share_hour"] = round(sh, 3)
            if sh >= STRONG * out["share_day"]:
                out["state"] = "strong"
            elif sh <= FRAGILE * out["share_day"]:
                out["state"] = "fragile"
        out["buy"] = buy_share(df, side, w[0], t_then, t_now)
    return out


def summary(df, S, hhmm, back=BACK):
    """{call، put، t} عند آخر لقطة ≤ hhmm، أو None."""
    if df is None:
        return None
    ts = sorted(t for t in set(df["time"]) if t <= hhmm)
    if not ts:
        return None
    t_now = ts[-1]
    older = [t for t in ts if _m(t) <= _m(t_now) - back]
    t_then = older[-1] if older else ts[0]
    c, p = side_state(df, "C", S, t_now, t_then), side_state(df, "P", S, t_now, t_then)
    if not c and not p:
        return None
    return {"call": c, "put": p, "t": t_now, "t_then": t_then}


def move_text(st):
    if not st or not st.get("then_wall") or st["then_wall"][0] == st["wall"][0]:
        return ""
    a, b = st["then_wall"][0], st["wall"][0]
    return f"، انتقل من {a:,.0f} إلى {b:,.0f} {'⬆️' if b > a else '⬇️'}"


def side_line(st, name):
    if not st:
        return f"{name}: لا بيانات"
    K, v = st["wall"]
    buy = f"، الشراء {st['buy'] * 100:.0f}%" if st.get("buy") is not None else ""
    return f"{name}: <b>{K:,.0f}</b> ({v / 1000:,.1f} ألف عقد اليوم) — {STATE_AR[st['state']]}{move_text(st)}{buy}"


def card(lw, S, side=None, t_label=""):
    """نص بطاقة الساعة (HTML بسيط)."""
    c, p = lw.get("call"), lw.get("put")
    L = [f"🗺️ <b>خريطة السيولة الحية</b> | SPX {S:,.1f} | {t_label} #خريطة", "",
         "🔴 " + side_line(c, "جدار الكول (مقاومة)"), "🟢 " + side_line(p, "جدار البوت (دعم)"), ""]
    if p and c:
        sup, res = p["wall"][0], c["wall"][0]
        L.append(f"📈 سيناريو الصعود: دخول CALL قرب الدعم {sup:,.0f}، الوقف تحت {sup - 5:,.0f}، الهدف {res:,.0f}"
                 + (" (المقاومة هشة: قد تُخترق)" if c["state"] == "fragile" else ""))
        L.append(f"📉 سيناريو الهبوط: دخول PUT قرب المقاومة {res:,.0f}، الوقف فوق {res + 5:,.0f}، الهدف {sup:,.0f}"
                 + (" (الدعم هش: قد يُكسر)" if p["state"] == "fragile" else ""))
    if side:
        L.append(f"🤖 جهة البوت الآن: {side}")
    L += ["", "<i>معلومات من حجم اليوم لا توصية؛ الشراء تقدير، والإغلاق الحقيقي يظهر صباح الغد.</i>"]
    return "\n".join(L)


def expected_move(df):
    """(السترايك، النطاق المتوقع بالنقاط، الوقت) من أول لقطة: كول + بوت (المنتصف) عند السترايك الأقرب لدلتا 0.5 (H-PRO1)."""
    if df is None or "delta" not in df:
        return None
    t0 = min(df["time"])
    snap = df[df["time"] == t0]
    c = snap[(snap["side"] == "C") & snap["delta"].notna()]
    if not len(c):
        return None
    K = float(c.loc[(c["delta"] - 0.5).abs().idxmin(), "strike"])
    mid = lambda r: (float(r["bid"]) + float(r["ask"])) / 2 if r["bid"] > 0 and r["ask"] > 0 else float(r["last"])
    cr, pr = snap[(snap["side"] == "C") & (snap["strike"] == K)], snap[(snap["side"] == "P") & (snap["strike"] == K)]
    if not len(cr) or not len(pr):
        return None
    em = mid(cr.iloc[0]) + mid(pr.iloc[0])
    return (K, round(em, 2), t0) if em > 0 else None


def vol_gex(df, S, hhmm):
    """(Σ حجم الكول × غاما − Σ حجم البوت × غاما) × 100 × S² × 0.01 من آخر لقطة ≤ hhmm، أو None (H-PRO1)."""
    if df is None or "gamma" not in df:
        return None
    ts = [t for t in set(df["time"]) if t <= hhmm]
    if not ts:
        return None
    snap = df[df["time"] == max(ts)]
    g = (snap["volume"].fillna(0) * snap["gamma"].fillna(0))
    net = float(g[snap["side"] == "C"].sum() - g[snap["side"] == "P"].sum())
    return net * 100 * S * S * 0.01
