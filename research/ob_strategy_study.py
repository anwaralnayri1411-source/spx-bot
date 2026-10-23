"""دراسة «الأوردر بلوك الأقوى» (يدوية، لا تكتب إلا التقرير): تجمع عوامل استراتيجيتي الفيديو (23/10، بطلب المستخدم) ثم تختبرها
أولاً على شموع الساعة (مجمّعة من كاش 5د)، ثم تتحقق بالنزول إلى 5د (أ: دراسة 5د مستقلة، ب: مناطق الساعة بدخول 5د).
العوامل (نقطة لكل منها): fvg، bos، fresh، liq، trend (اتجاه واضح: كفاءة السعر ≥0.3 ومع اتجاه المنطقة)،
time (لمس في ساعات السيولة 9:30-11:30 و14:00-16:00 نيويورك)، conf (شمعة اللمس ممتلئة ≥60% باتجاه الارتداد).
القياس: بعد اللمس (إغلاق شمعة اللمس) أقصى حركة لصالح الارتداد/ضده خلال FWD شمعة؛ المرجع: المنطقة نفسها مُزاحة (وهمي).
الحكم بالأيام (إعادة معاينة الأيام)، لا بالحالات. أسعار نظرية على المؤشر لا العقد."""
import sys

import numpy as np
import pandas as pd

CACHE = "research/spx_5m_cache.csv"
FACT = ["fvg", "bos", "fresh", "liq", "trend", "time", "conf"]
P5 = dict(n_imp=6, disp=0.0025, lb=12, fwd=12, trend_n=72, shift=0.0025, name="5د")
P1H = dict(n_imp=3, disp=0.0030, lb=6, fwd=3, trend_n=8, shift=0.005, name="ساعة")


def load5():
    d = pd.read_csv(CACHE)
    d.index = pd.to_datetime(d["time"], utc=True).dt.tz_convert("America/New_York")
    d = d.rename(columns=str.lower)[["open", "high", "low", "close"]].sort_index()
    return d[(d.index.time >= pd.Timestamp("09:30").time()) & (d.index.time <= pd.Timestamp("15:55").time())]


def to_hour(d5):
    key = (d5.index.hour * 60 + d5.index.minute - 570) // 60
    g = d5.groupby([d5.index.date, key])
    h = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
    h.index = pd.DatetimeIndex([x.index[0] for _, x in g], tz="America/New_York")
    return h.sort_index()


def _liq(g, side, i, j, zl, zh, eq=0.0005):
    pts = []
    for k in range(i + 2, j - 1):
        if side == "bull" and g.low[k] < g.low[k - 1] and g.low[k] < g.low[k + 1] and g.low[k] > zh: pts.append(g.low[k])
        if side == "bear" and g.high[k] > g.high[k - 1] and g.high[k] > g.high[k + 1] and g.high[k] < zl: pts.append(g.high[k])
    return any(abs(a - b) / a <= eq for n, a in enumerate(pts) for b in pts[n + 1:])


def zones(g, p):
    """مناطق OB على g (reset_index) مع العوامل المعروفة عند التكوين."""
    n, out = len(g), []
    for i in range(max(p["lb"], 1), n - p["n_imp"] - 3):
        c, o = g.close[i], g.open[i]
        for side in ("bull", "bear"):
            if side == "bull" and not (c < o and (g.high[i + 1:i + p["n_imp"] + 1].max() - c) / c >= p["disp"]): continue
            if side == "bear" and not (c > o and (c - g.low[i + 1:i + p["n_imp"] + 1].min()) / c >= p["disp"]): continue
            fvg = False
            for fi in range(i + 3, i + 5):
                if fi >= n: continue
                lo, hi = (g.high[fi - 2], g.low[fi]) if side == "bull" else (g.high[fi], g.low[fi - 2])
                fvg = fvg or (hi > lo and (hi - lo) / g.close[fi] >= 0.0006)
            imp = slice(i + 1, i + p["n_imp"] + 1)
            bos = bool(g.high[imp].max() > g.high[i - p["lb"]:i].max()) if side == "bull" else bool(g.low[imp].min() < g.low[i - p["lb"]:i].min())
            e = i + p["n_imp"]; s = max(0, e - p["trend_n"])
            seg = g.close[s:e + 1].values
            er = abs(seg[-1] - seg[0]) / max(np.abs(np.diff(seg)).sum(), 1e-9) if len(seg) > 3 else 0
            trend = bool(er >= 0.3 and ((seg[-1] > seg[0]) == (side == "bull")))
            out.append(dict(side=side, i=i, zl=g.low[i], zh=g.high[i], fvg=fvg, bos=bos, trend=trend, avail=e + 1))
    return out


def touches(g, z, start, zl, zh, fwd, liq_ref):
    """حتى لمستين للمنطقة بدءاً من start؛ ترجع (j, fresh, liq, time, conf, fav, adv)."""
    n, side, res, cnt, j = len(g), z["side"], [], 0, start
    while j < n - fwd and cnt < 2:
        if (g.close[j] < zl) if side == "bull" else (g.close[j] > zh): break
        if (g.low[j] <= zh) if side == "bull" else (g.high[j] >= zl):
            ent = g.close[j]
            fw = g.iloc[j + 1:j + 1 + fwd]
            hi, lo = (fw.high.max() - ent) / ent, (ent - fw.low.min()) / ent
            fav, adv = (hi, lo) if side == "bull" else (lo, hi)
            rng = max(g.high[j] - g.low[j], 1e-9); body = abs(g.close[j] - g.open[j]) / rng
            conf = body >= 0.6 and ((g.close[j] > g.open[j]) == (side == "bull"))
            ts = g.ts[j]; hm = ts.hour * 60 + ts.minute
            tm = 570 <= hm < 690 or 840 <= hm < 960
            res.append((j, cnt == 0, bool(liq_ref(j)), tm, bool(conf), fav, adv))
            cnt += 1; j += max(2, fwd // 2)
        j += 1
    return res


def rows_for(d, p, mtf=None):
    """d: DataFrame شموع (فهرسها وقت). mtf: DataFrame 5د لدخول الساعة بالنزول إلى 5د."""
    g = d.reset_index(); g["ts"] = d.index; g = g.drop(columns=[c for c in g.columns if c not in ("open", "high", "low", "close", "ts")])
    out = []
    G5 = None
    if mtf is not None:
        G5 = mtf.reset_index(); G5["ts"] = mtf.index; G5 = G5[["open", "high", "low", "close", "ts"]]
    for z in zones(g, p):
        for kind, k in (("real", 0.0), ("placebo", -p["shift"] if z["side"] == "bull" else p["shift"])):
            zl, zh = z["zl"] * (1 + k), z["zh"] * (1 + k)
            if G5 is None:
                T = touches(g, z, z["avail"] + 0, zl, zh, p["fwd"], lambda j: _liq(g, z["side"], z["i"], j, zl, zh))
                gg = g
            else:
                t0 = g.ts[min(z["avail"], len(g) - 1)]
                s5 = int(G5.ts.searchsorted(t0))
                i5 = max(0, s5 - 1)
                T = touches(G5, z, s5, zl, zh, P5["fwd"], lambda j: _liq(G5, z["side"], i5, j, zl, zh))
                gg = G5
            for (j, fresh, liq, tm, conf, fav, adv) in T:
                f = dict(fvg=z["fvg"], bos=z["bos"], fresh=fresh, liq=liq, trend=z["trend"], time=tm, conf=conf)
                out.append(dict(day=str(gg.ts[j].date()), kind=kind, side=z["side"], fav=fav, adv=adv, score=sum(f.values()), **f))
    return pd.DataFrame(out)


def _stat(x):
    return f"n={len(x):3d} أيام={x.day.nunique():2d} لصالح>ضد={(x.fav > x.adv).mean():4.0%} متوسط(لصالح−ضد)={(x.fav - x.adv).mean():+.3%}"


def _boot(a, b, n=2000, seed=1):
    """فاصل 95% لفرق متوسط (لصالح−ضد) بين المجموعتين بإعادة معاينة الأيام."""
    rng = np.random.default_rng(seed)
    days = sorted(set(a.day) | set(b.day))
    ga = {d: (a[a.day == d].fav - a[a.day == d].adv).tolist() for d in days}
    gb = {d: (b[b.day == d].fav - b[b.day == d].adv).tolist() for d in days}
    out = []
    for _ in range(n):
        s = rng.choice(len(days), len(days))
        xa = [v for k in s for v in ga[days[k]]]; xb = [v for k in s for v in gb[days[k]]]
        if xa and xb: out.append(np.mean(xa) - np.mean(xb))
    return np.percentile(out, [2.5, 97.5]) if out else (np.nan, np.nan)


def report(df, title):
    print(f"\n=== {title} ===")
    if df.empty:
        print("لا حالات"); return
    R, Pl = df[df.kind == "real"], df[df.kind == "placebo"]
    print("حقيقي ", _stat(R)); print("وهمي  ", _stat(Pl) if len(Pl) else "—")
    for s in range(8):
        x = R[R.score == s]
        if len(x): print(f"  نقاط={s}", _stat(x))
    for f in FACT:
        a, b = R[R[f]], R[~R[f]]
        if len(a) and len(b): print(f"  {f:6s} نعم:", _stat(a), "| لا:", _stat(b))
    core = R[R.fvg & R.bos & R.fresh & R.liq]; rest = R.drop(core.index)
    top = R[R.score >= 5]; low = R.drop(top.index)
    for nm, a, b in (("الخطة الأساسية (fvg+bos+fresh+liq)", core, rest), ("نقاط ≥5 من 7", top, low)):
        if len(a) >= 3 and len(b) >= 3:
            lo, hi = _boot(a, b)
            print(f"  {nm}: {_stat(a)} | الباقي {_stat(b)} | فرق المتوسط 95%: [{lo:+.3%}, {hi:+.3%}]")
        else:
            print(f"  {nm}: عينة صغيرة ({len(a)})")


if __name__ == "__main__":
    d5 = load5(); h1 = to_hour(d5)
    print(f"شموع 5د: {len(d5)}؛ شموع ساعة: {len(h1)}؛ أيام: {len(set(d5.index.date))}")
    report(rows_for(h1, P1H), "1) فريم الساعة (مناطق ودخول على الساعة)")
    report(rows_for(h1, P1H, mtf=d5), "2) مناطق الساعة بدخول 5د (النزول للتحقق)")
    report(rows_for(d5, P5), "3) فريم 5د مستقل")
