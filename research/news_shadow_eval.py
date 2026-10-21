"""تقييم ظل فلتر الأخبار: ماذا فعل SPX بعد كل فحص حجبه الفلتر، مقارنةً بالتوصيات التي أُرسلت فعلاً.
يقرأ news_shadow_log.csv (يكتبه news_shadow.py من داخل البوت) وzero_dte_log.csv، ويجلب شموع ^SPX 5د من ياهو
(آخر 5 أيام؛ النتائج السابقة تُحفظ في news_shadow_outcomes.csv فلا تضيع). الدخول = إغلاق شمعة لحظة الفحص الفعلية
(سعر حقيقي لا متأخر). الإصابة = لمس +0.25% باتجاه البوت قبل −0.25% ضده خلال الجلسة. تسجيل فقط، لا رسائل.
تشغيل: python research/news_shadow_eval.py"""
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "news_shadow_log.csv")
SENT = os.path.join(ROOT, "zero_dte_log.csv")
OUT = os.path.join(ROOT, "news_shadow_outcomes.csv")
MD = os.path.join(ROOT, "research", "news_shadow_results.md")
MOVE = 0.0025
TZ = "America/New_York"


def bars():
    import yfinance as yf
    h = yf.Ticker("^SPX").history(period="5d", interval="5m")
    h.index = h.index.tz_convert(TZ)
    return h


def outcome(h, t, side):
    """نتيجة دخول عند t (نيويورك) باتجاه side. None إن لم تتوفر الشموع أو لم يُغلق اليوم بعد."""
    day = h[h.index.date == t.date()]
    if day.empty or day.index[-1].hour < 15 or (day.index[-1].hour == 15 and day.index[-1].minute < 55):
        return None
    past = day[day.index <= t]
    if past.empty:
        return None
    e = float(past["Close"].iloc[-1])
    fut = day[day.index > t]
    s = 1 if side == "CALL" else -1

    def at(m):
        x = day[day.index <= t + pd.Timedelta(minutes=m)]
        return round(s * (float(x["Close"].iloc[-1]) - e), 2)

    hit = "none"
    for _, b in fut.iterrows():
        up = b["High"] >= e * (1 + MOVE) if s == 1 else b["Low"] <= e * (1 - MOVE)
        dn = b["Low"] <= e * (1 - MOVE) if s == 1 else b["High"] >= e * (1 + MOVE)
        if up and dn:
            hit = "both"
            break
        if up or dn:
            hit = "win" if up else "loss"
            break
    return {"entry": round(e, 2), "r30": at(30), "r60": at(60), "rclose": round(s * (float(day["Close"].iloc[-1]) - e), 2), "hit": hit}


def collect(h):
    rows = []
    if os.path.exists(LOG):
        for _, r in pd.read_csv(LOG).iterrows():
            rows.append(("news", r["time"], r["side"], r["kind"], r["edge"]))
    if os.path.exists(SENT):
        s = pd.read_csv(SENT, header=None, usecols=range(5), names=["d", "hm", "sym", "exp", "side"])
        first = os.path.exists(LOG) and len(rows) and min(r[1] for r in rows)[:10]
        for _, r in s.iterrows():
            if first and str(r["d"]) >= first and str(r["side"]) in ("CALL", "PUT"):
                rows.append(("sent", f"{r['d']} {r['hm']}", r["side"], "sent", ""))
    old = pd.read_csv(OUT) if os.path.exists(OUT) else pd.DataFrame()
    done = set(zip(old.get("group", []), old.get("time", []), old.get("side", [])))
    new = []
    for g, tm, side, kind, edge in rows:
        if (g, tm, side) in done:
            continue
        o = outcome(h, pd.Timestamp(tm).tz_localize(TZ), side)
        if o:
            new.append({"group": g, "time": tm, "side": side, "kind": kind, "edge": edge, **o})
    allr = pd.concat([old, pd.DataFrame(new)], ignore_index=True) if new else old
    if not allr.empty:
        allr.sort_values(["time", "group"]).to_csv(OUT, index=False)
    return allr


def report(df):
    L = ["# ظل فلتر الأخبار — نتائج آلية\n",
         "- **blocked**: صدمة أخبار مستوى 3 أوقفت إشارة كان فرقها ≥ الحد. **weakened**: مستوى 2 خصم نقطة فنزل الفرق تحت الحد.",
         "- الدخول بسعر SPX الحقيقي لحظة الفحص. «إصابة» = لمس +0.25% باتجاه البوت قبل −0.25% ضده.",
         "- المرجع: توصيات البوت المُرسلة فعلاً في الفترة نفسها. ⚠️ لا حكم قبل 10 أيام حجب على الأقل.\n"]
    if df is None or df.empty:
        L.append("لا حالات مكتملة بعد.")
    else:
        df = df.copy()
        df["day"] = df["time"].str[:10]
        L += ["| المجموعة | فحوص | أيام | إصابة / خسارة / لا شيء | متوسط نقاط 30د | 60د | للإغلاق |", "|---|---|---|---|---|---|---|"]
        for name, z in (("محجوبة (كل الفحوص)", df[df.group == "news"]),
                        ("محجوبة (أول فحص في اليوم)", df[df.group == "news"].sort_values("time").groupby(["day", "side"]).head(1)),
                        ("مُرسلة فعلاً", df[df.group == "sent"])):
            if z.empty:
                L.append(f"| {name} | 0 | 0 | - | - | - | - |")
                continue
            c = z["hit"].value_counts()
            L.append(f"| {name} | {len(z)} | {z.day.nunique()} | {c.get('win', 0)} / {c.get('loss', 0)} / {c.get('none', 0) + c.get('both', 0)} "
                     f"| {z.r30.mean():+.1f} | {z.r60.mean():+.1f} | {z.rclose.mean():+.1f} |")
        L += ["\n## الحالات المحجوبة\n", "| الوقت | النوع | الاتجاه | الفرق | النتيجة | 60د | للإغلاق |", "|---|---|---|---|---|---|---|"]
        for _, r in df[df.group == "news"].sort_values("time").iterrows():
            L.append(f"| {r['time']} | {r['kind']} | {r['side']} | {r['edge']} | {r['hit']} | {r['r60']:+.1f} | {r['rclose']:+.1f} |")
    L.append("\nإذا تفوّقت المحجوبة على المُرسلة بوضوح بعد عينة كافية → نعرض على المستخدم تخفيف الفلتر. لا تغيير قبل ذلك.")
    with open(MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    try:
        h = bars()
    except Exception as e:
        print("تعذر جلب شموع SPX:", e)
        sys.exit(0)
    report(collect(h))
