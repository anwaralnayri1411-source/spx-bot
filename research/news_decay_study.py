#!/usr/bin/env python3
"""H-NEWSDECAY: منحنى أثر الخبر على الشركات — هل يتلاشى الاندفاع الأول أم يستمر خلال ساعات وأيام؟
(مسجّل مسبقاً في CLAUDE.md 3/11/2026 بطلب المستخدم). دراسة فقط، لا تغيّر أي بوت.

الأخبار: news_memory_log.csv (أخبار شركات جديدة، أهمية متوسطة/عالية، أول خبر لكل شركة ونوع ويوم).
الحركة فوق السوق (السهم − SPY) من شموع 5د الحقيقية. الاندفاع الأول = t0 → t0+30د، ثم المراحل بإشارته:
+30د→+2س، +30د→الإغلاق، +30د→إغلاق الغد، +30د→إغلاق +3 أيام (موجب = استمرار، سالب = تلاشٍ).
الضابط: النقاط نفسها في أوقات عشوائية للسهم نفسه.
تشغيل (الخادم): SPXBOT_DATA=/opt/spxbot/data python research/news_decay_study.py [مسار السجل]
المخرج: research/news_decay_results.md
"""
import os
import sys
from datetime import time as dtime, timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
LOG = os.path.join(ROOT, "news_memory_log.csv")
OUT = os.path.join(HERE, "news_decay_results.md")
MIN_INIT = 0.005
MIN_N1 = 150
OOS_FROM = "2026-11-03"       # N2/N3: أخبار من هذا اليوم فقط (خارج عينة الاكتشاف)
MIN_N2_BIG, MIN_N2_SMALL, MIN_N3 = 60, 100, 100
N_BOOT = 4000
STAGES = (("c_2h", "+30د → ساعتين"), ("c_close", "+30د → الإغلاق"), ("c_next", "+30د → إغلاق الغد"), ("c_3d", "+30د → +3 أيام"))
EVENT_AR = {"analyst_upgrade": "ترقية محلل", "analyst_downgrade": "تخفيض محلل", "earnings_beat": "أرباح فوق التوقع",
            "earnings_miss": "أرباح دون التوقع", "guidance_up": "رفع التوقعات", "guidance_down": "خفض التوقعات",
            "contract_product": "عقد/منتج", "deal_merger": "صفقة/استحواذ", "market_move": "حركة السوق", "other": "أخرى",
            "lawsuit_probe": "قضية/تحقيق", "management": "إدارة", "regulation": "تنظيم", "china": "الصين",
            "crypto_move": "حركة الكريبتو", "trump_policy": "قرار/تصريح ترامب", "oil_move": "حركة النفط"}


def read_bars(sym):
    from tournament import read_bars as rb
    return rb(os.path.join(DATA, "bars_5m", f"{sym}.csv.gz"))


def load_news(path):
    d = pd.read_csv(path, dtype=str, keep_default_na=False)
    d = d[(d["new"] == "1") & d["importance"].isin(["medium", "high"]) & (d["ticker"] != "MARKET")].copy()
    d["ts"] = pd.to_datetime(d["time_utc"], utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
    d["day"] = d["ts"].dt.date
    return d.sort_values("ts").drop_duplicates(["ticker", "event", "day"])


class Px:
    """أسعار سهم من شموع 5د (ساعات السوق): افتتاح الشمعة عند/بعد وقت، وإغلاق الجلسة."""

    def __init__(self, d5):
        self.d = d5
        self.t = d5.index.values
        g = d5.groupby(d5.index.date)
        self.days = list(g.groups.keys())
        self.close = g["Close"].last()
        self.open = g["Open"].first()

    def at(self, ts):
        i = np.searchsorted(self.t, np.datetime64(ts))
        return (float(self.d["Open"].iloc[i]), pd.Timestamp(self.t[i])) if i < len(self.t) else (None, None)

    def day_close(self, day, k=0):
        if day not in self.close.index:
            return None
        j = self.days.index(day) + k
        return float(self.close.iloc[j]) if 0 <= j < len(self.days) else None


def anchor(px, ts):
    """t0: أول شمعة تبدأ عند/بعد النشر في الجلسة؛ خارجها → افتتاح الجلسة التالية. يرجع (t0، فجوة أو None)."""
    p, t0 = px.at(ts)
    if t0 is None:
        return None, None
    inside = dtime(9, 30) <= ts.time() < dtime(15, 55) and t0.date() == ts.date()
    gap = None
    if not inside:
        prev = px.day_close(t0.date(), -1)
        gap = (p / prev - 1) if prev else None
    return t0, gap


def path(px, spy, t0):
    """الحركة فوق السوق لكل مرحلة (بلا إشارة). None إن نقصت البيانات."""
    end_day = pd.Timestamp.combine(t0.date(), dtime(16, 0))

    def pt(p_, ts):
        if ts >= end_day:
            return p_.day_close(t0.date()), end_day
        v, t = p_.at(ts)
        if t is None or t.date() != t0.date():
            return p_.day_close(t0.date()), end_day
        return v, t

    s0, _ = px.at(t0)
    m0, _ = spy.at(t0)
    s30, t30 = pt(px, t0 + timedelta(minutes=30))
    m30, _ = pt(spy, t0 + timedelta(minutes=30))
    s2h, _ = pt(px, t0 + timedelta(minutes=120))
    m2h, _ = pt(spy, t0 + timedelta(minutes=120))
    d = t0.date()
    sc, mc = px.day_close(d), spy.day_close(d)
    sn, mn = px.day_close(d, 1), spy.day_close(d, 1)
    s3, m3 = px.day_close(d, 3), spy.day_close(d, 3)
    if None in (s0, m0, s30, m30, s2h, m2h, sc, mc) or t30 >= end_day:
        return None
    ab = lambda a, b, x, y: (b / a - 1) - (y / x - 1)
    return {"init": ab(s0, s30, m0, m30), "a_2h": ab(s30, s2h, m30, m2h), "a_close": ab(s30, sc, m30, mc),
            "a_next": ab(s30, sn, m30, mn) if sn and mn else np.nan, "a_3d": ab(s30, s3, m30, m3) if s3 and m3 else np.nan}


def signed(r):
    s = np.sign(r["init"])
    return {"init": r["init"], **{c: s * r["a" + c[1:]] for c, _ in STAGES}}


def boot(v, days, n=N_BOOT, seed=0, q=(2.5, 97.5)):
    v, days = np.asarray(v, float), np.asarray(days)
    ok = ~np.isnan(v)
    v, days = v[ok], days[ok]
    u = np.unique(days)
    if len(u) < 3:
        return np.nan, (np.nan, np.nan)
    by = {d: v[days == d] for d in u}
    rng = np.random.default_rng(seed)
    ms = [np.mean(np.concatenate([by[d] for d in rng.choice(u, len(u))])) for _ in range(n)]
    return float(np.mean(v)), tuple(np.percentile(ms, list(q)))


def run(news):
    spy = Px(read_bars("SPY"))
    rng = np.random.default_rng(5)
    rows, ctrl = [], []
    for tk, g in news.groupby("ticker"):
        try:
            px = Px(read_bars(tk))
        except Exception as ex:  # noqa: BLE001
            print("تخطي", tk, type(ex).__name__)
            continue
        cand_days = [d for d in px.days if d >= min(news["day"]) and d not in set(g["day"])]
        for r in g.itertuples():
            t0, gap = anchor(px, r.ts)
            if t0 is None:
                continue
            p = path(px, spy, t0)
            if p is None:
                continue
            ex = {"up": 1, "down": -1}.get(r.expect, 0)
            rows.append({"ticker": tk, "day": str(t0.date()), "event": r.event, "importance": r.importance, "expect": r.expect,
                         "inside": gap is None, "gap": gap, "claude_next": ex * p["a_next"] if ex else np.nan, **signed(p)})
            if cand_days:                      # ضابط: وقت عشوائي 10:00–15:00 في يوم بلا خبر للسهم نفسه
                d = cand_days[rng.integers(len(cand_days))]
                tt = pd.Timestamp.combine(d, dtime(10, 0)) + timedelta(minutes=5 * int(rng.integers(0, 60)))
                q = path(px, spy, tt)
                if q is not None:
                    ctrl.append({"day": str(d), **signed(q)})
    return pd.DataFrame(rows), pd.DataFrame(ctrl)


def stage_row(name, g):
    cells = []
    for c, _ in STAGES:
        v = g[c].dropna()
        cells.append(f"{v.mean() * 100:+.2f}% ({(v > 0).mean() * 100:.0f}%)" if len(v) else "-")
    return f"| {name} | {len(g)} | {g['init'].abs().mean() * 100:.2f}% | " + " | ".join(cells) + " |"


def boot_diff(a, b, n=N_BOOT, seed=2, q=(2.5, 97.5)):
    """فرق المتوسطين (a − b) بإعادة معاينة الأيام المشتركة."""
    a, b = a.dropna(subset=["v"]), b.dropna(subset=["v"])
    u = np.unique(np.r_[a["day"].values, b["day"].values])
    if len(u) < 3 or not len(a) or not len(b):
        return np.nan, (np.nan, np.nan)
    ba = {d: g["v"].values for d, g in a.groupby("day")}
    bb = {d: g["v"].values for d, g in b.groupby("day")}
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        s_ = rng.choice(u, len(u))
        xa = np.concatenate([ba.get(d, np.array([])) for d in s_])
        xb = np.concatenate([bb.get(d, np.array([])) for d in s_])
        if len(xa) and len(xb):
            out.append(xa.mean() - xb.mean())
    return float(a["v"].mean() - b["v"].mean()), tuple(np.percentile(out, list(q)))


def oos_section(ev):
    """N2 وN3 على أخبار OOS_FROM فصاعداً فقط، مع تصحيح BH على الاثنتين عند اكتمال العينتين."""
    o = ev[(ev["day"] >= OOS_FROM) & (ev["init"].abs() >= MIN_INIT)]
    big = o[o["init"].abs() >= 0.015][["day", "c_next"]].rename(columns={"c_next": "v"})
    small = o[o["init"].abs() < 0.015][["day", "c_next"]].rename(columns={"c_next": "v"})
    d2, (lo2, hi2) = boot_diff(big, small)
    opp = o[o["expect"].isin(["up", "down"]) & (np.sign(o["init"]) != o["expect"].map({"up": 1, "down": -1}))]
    m3, (lo3, hi3) = boot(opp["claude_next"], opp["day"])
    nb, ns, n3 = big["v"].notna().sum(), small["v"].notna().sum(), opp["claude_next"].notna().sum()
    ready = nb >= MIN_N2_BIG and ns >= MIN_N2_SMALL and n3 >= MIN_N3
    if not nb and not ns and not n3:
        return ["", f"## N2 وN3 (أخبار من {OOS_FROM} فقط، خارج عينة الاكتشاف)", "", "- لا أحداث بعد.",
                "⚖️ **N2 وN3: لا حكم بعد (العينة أقل من الحد).**"]
    L = ["", f"## N2 وN3 (أخبار من {OOS_FROM} فقط، خارج عينة الاكتشاف)", "",
         f"- **N2** (كبير − صغير، +30د → إغلاق الغد): {d2 * 100:+.2f} نقطة [{lo2 * 100:+.2f}، {hi2 * 100:+.2f}] — كبير {nb} (الحد {MIN_N2_BIG})، صغير {ns} (الحد {MIN_N2_SMALL}).",
         f"- **N3** (اندفاع عكس Claude، الحركة بإشارة Claude): {m3 * 100:+.2f}% [{lo3 * 100:+.2f}، {hi3 * 100:+.2f}] — {n3} حدثاً (الحد {MIN_N3})."]
    if not ready:
        L.append("⚖️ **N2 وN3: لا حكم بعد (العينة أقل من الحد).**")
        return L
    # BH بمعدل 5% على فرضيتين: إن استثنى فاصل 95% الصفر في الاثنتين تمرّان معاً؛ وإلا تمرّ الواحدة فقط إن استثنى فاصل 97.5% الصفر.
    a95, b95 = lo2 > 0, lo3 > 0
    a975 = boot_diff(big, small, q=(1.25, 98.75))[1][0] > 0
    b975 = boot(opp["claude_next"], opp["day"], q=(1.25, 98.75))[1][0] > 0
    n2, n3_ok = (a95, b95) if (a95 and b95) else (a975, b975)
    L.append(f"⚖️ **N2: {'تحققت' if n2 else 'لم تتحقق'} | N3: {'تحققت' if n3_ok else 'لم تتحقق'}** (تصحيح BH بمعدل 5%)")
    return L


def report(ev, ctrl):
    big = ev[ev["init"].abs() >= MIN_INIT]
    cb = ctrl[ctrl["init"].abs() >= MIN_INIT]
    L = ["# H-NEWSDECAY: منحنى أثر الخبر على الشركات (نتائج آلية)", "",
         f"- الأخبار: {ev['day'].min()} → {ev['day'].max()} ({ev['day'].nunique()} يوم تداول)، {len(ev)} خبراً مقيساً، "
         f"منها {len(big)} باندفاع أول ≥ {MIN_INIT * 100:.1f}%.",
         "- الحركة فوق السوق (السهم − SPY). كل مرحلة بإشارة الاندفاع الأول (أول 30د): موجب = استمر، سالب = تلاشى. بين قوسين: نسبة الاستمرار.", "",
         "| المجموعة | العدد | متوسط |الاندفاع| | " + " | ".join(n for _, n in STAGES) + " |", "|---|---|---|" + "---|" * len(STAGES)]
    L.append(stage_row("**كل الأخبار باندفاع ≥0.5%**", big))
    L.append(stage_row("ضابط: أوقات عشوائية بلا خبر (≥0.5%)", cb))
    L.append(stage_row("اندفاع 0.5–1.5%", big[big["init"].abs() < 0.015]))
    L.append(stage_row("اندفاع > 1.5%", big[big["init"].abs() >= 0.015]))
    L.append(stage_row("خبر أثناء الجلسة", big[big["inside"]]))
    L.append(stage_row("خبر خارج الجلسة (من الافتتاح)", big[~big["inside"]]))
    L.append(stage_row("أهمية عالية", big[big["importance"] == "high"]))
    agree = np.sign(big["init"]) == big["expect"].map({"up": 1, "down": -1})
    L.append(stage_row("الاندفاع وافق توقع Claude", big[agree]))
    L.append(stage_row("الاندفاع عكس توقع Claude", big[big["expect"].isin(["up", "down"]) & ~agree]))
    L += ["", "## حسب نوع الخبر (8 أحداث فأكثر باندفاع ≥0.5%)", "",
          "| النوع | العدد | متوسط |الاندفاع| | " + " | ".join(n for _, n in STAGES) + " |", "|---|---|---|" + "---|" * len(STAGES)]
    for e, g in sorted(big.groupby("event"), key=lambda x: -len(x[1])):
        if len(g) >= 8:
            L.append(stage_row(EVENT_AR.get(e, e), g))
    m, (lo, hi) = boot(big["c_next"], big["day"])
    n1 = big["c_next"].notna().sum()
    L += ["", "## الفرضية N1", "",
          f"- +30د → إغلاق الغد بإشارة الاندفاع: {m * 100:+.2f}% [{lo * 100:+.2f}، {hi * 100:+.2f}] على {n1} حدثاً (الحد {MIN_N1}).",
          f"⚖️ **N1: {'لا حكم بعد (العينة أقل من الحد)' if n1 < MIN_N1 else ('استمرار مثبت → اتبع الخبر بعد 30د' if lo > 0 else ('تلاشٍ مثبت → اعكسه بعد 30د' if hi < 0 else 'لم تتحقق'))}**",
          *oos_section(ev),
          "", "تحفظ: وقت النشر في Google News قد يتأخر عن الحدث؛ عينة أيام قليلة في سوق واحد؛ حركة السهم ليست ربح العقد."]
    return "\n".join(L) + "\n"


def main(argv):
    news = load_news(argv[0] if argv else LOG)
    ev, ctrl = run(news)
    md = report(ev, ctrl)
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main(sys.argv[1:])
