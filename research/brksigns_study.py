#!/usr/bin/env python3
"""H-BRKSIGNS: علامات الاختراق الحقيقي والكاذب من درس تيك توك مقابل قاعدتنا (3 شموع). مسجّل مسبقاً في CLAUDE.md 15/11/2026. اختبار فقط.

شموع 5د من Alpaca منذ 2019 (SPY والأسهم الست). المستويات: أعلى/أدنى أول 30د وأمس. لكل ذراع حدث واحد لكل (مستوى، جهة) يومياً.
  K1 إغلاق الكسر | K3 الإغلاق الثالث بعد ثبات إغلاقين (قاعدتنا) | S1 جسم قوي يغلق في طرف المدى | S12 S1 + الشمعة التالية تكمل
  S1245 S12 + VWAP + ضغط قبل الكسر | RT إعادة اختبار المستوى ثم إغلاق في الجهة | FK ذيل يعبر والإغلاق يعود: عكس الاختراق
المقياس: عائد الأصل في جهة الصفقة (نقطة أساس) بعد 5/15/30/60د، ناقص متوسط كل الشموع في الجهة نفسها (المرجع العشوائي).
تشغيل: python research/brksigns_study.py <مجلد فيه spy_5m.csv.gz و TICKER.csv.gz>   المخرج: research/brksigns_results.md
"""
import os
import sys
from datetime import time as dtime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "brksigns_results.md")
STOCKS = ["NVDA", "TSLA", "AAPL", "AMZN", "META", "AMD"]
ARMS = ["K1", "K3", "S1", "S12", "S1245", "RT", "FK"]
NAMES = {"K1": "إغلاق الكسر (ضابط)", "K3": "قاعدتنا: 3 شموع", "S1": "علامة 1: جسم قوي", "S12": "علامتا 1+2: شمعتان",
         "S1245": "1+2+الاتجاه+الضغط", "RT": "علامة 3: إعادة الاختبار", "FK": "الكاذب: عكس الذيل"}
HORIZONS = [5, 15, 30, 60]
MAIN_H = 30
SPLIT = pd.Timestamp("2022-11-23").date()
BUF_PCT = 0.0002
FIRST, LAST = dtime(10, 0), dtime(14, 55)          # بداية شمعة الإشارة/الدخول
RT_WAIT = 12
NBOOT = 2000


def read_bars(path):
    d = pd.read_csv(path)
    d.index = pd.DatetimeIndex(pd.to_datetime(d["t"], utc=True)).tz_convert("America/New_York").tz_localize(None)
    d = d.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})[["Open", "High", "Low", "Close", "Volume"]]
    return d[(d.index.time >= dtime(9, 30)) & (d.index.time < dtime(16, 0))].sort_index()


# ============================ الأحداث (نقية، بلا نظرة للمستقبل) ============================
def strong(o, h, l, c, up):
    """العلامة 1: جسم ≥ 50% من المدى، والإغلاق في أبعد 30% من المدى في جهة الكسر."""
    rng = h - l
    if rng <= 0:
        return False
    if abs(c - o) < 0.5 * rng:
        return False
    return (c - l) >= 0.7 * rng if up else (h - c) >= 0.7 * rng


def day_events(g, lvls):
    """g: شموع يوم واحد (DataFrame). lvls: [(السعر، اسم، أول شمعة مسموحة للكسر)].
    يرجع [(ذراع، جهة +1/-1، رقم شمعة الدخول)]، حدثاً واحداً لكل (ذراع، مستوى، جهة)."""
    o, h, l, c, v = (g[k].to_numpy(float) for k in ("Open", "High", "Low", "Close", "Volume"))
    tm = g.index.time
    n = len(c)
    ok_t = np.array([FIRST <= t <= LAST for t in tm])
    tp = (h + l + c) / 3
    cv = np.cumsum(v)
    vwap = np.where(cv > 0, np.cumsum(tp * v) / np.where(cv > 0, cv, 1), c)
    rngs = h - l
    out = []
    for L, _name, s0 in lvls:
        buf = BUF_PCT * L
        for up in (True, False):
            d = 1 if up else -1
            done = set()
            for i in range(max(1, s0), n):
                if not ok_t[i]:
                    continue
                brk = (c[i - 1] <= L and c[i] > L + buf) if up else (c[i - 1] >= L and c[i] < L - buf)
                fake = (c[i - 1] <= L and h[i] > L + buf and c[i] <= L) if up else (c[i - 1] >= L and l[i] < L - buf and c[i] >= L)
                if fake and "FK" not in done:
                    out.append(("FK", -d, i))
                    done.add("FK")
                if not brk:
                    continue
                if "K1" not in done:
                    out.append(("K1", d, i))
                    done.add("K1")
                if "K3" not in done and i + 2 < n and ok_t[i + 2]:
                    if all((c[j] > L) if up else (c[j] < L) for j in (i + 1, i + 2)):
                        out.append(("K3", d, i + 2))
                        done.add("K3")
                if not strong(o[i], h[i], l[i], c[i], up):
                    continue
                if "S1" not in done:
                    out.append(("S1", d, i))
                    done.add("S1")
                cont = i + 1 < n and ok_t[i + 1] and ((c[i + 1] > c[i]) if up else (c[i + 1] < c[i]))
                if cont and "S12" not in done:
                    out.append(("S12", d, i + 1))
                    done.add("S12")
                if cont and "S1245" not in done and i >= 5:
                    trend = (c[i + 1] > vwap[i + 1]) if up else (c[i + 1] < vwap[i + 1])
                    med = float(np.median(rngs[:i]))
                    squeeze = rngs[i - 4:i].mean() <= med and rngs[i] <= 3 * med
                    if trend and squeeze:
                        out.append(("S1245", d, i + 1))
                        done.add("S1245")
                if "RT" not in done:
                    for k in range(i + 1, min(n, i + 1 + RT_WAIT)):
                        if not ok_t[k]:
                            break
                        if (c[k] < L - buf) if up else (c[k] > L + buf):
                            break                                   # إغلاق خلف المستوى عكسياً: يُلغى
                        touch = (l[k] <= L + buf) if up else (h[k] >= L - buf)
                        if touch and ((c[k] > L) if up else (c[k] < L)):
                            out.append(("RT", d, k))
                            done.add("RT")
                            break
    return out


def fwd(c, j, h):
    k = j + h // 5
    return None if k >= len(c) else (c[k] / c[j] - 1) * 1e4


def symbol_rows(sym, d5):
    days = sorted(set(d5.index.date))
    groups = {dt: g for dt, g in d5.groupby(d5.index.date)}
    rows, base = [], []
    prev = None
    for dt in days:
        g = groups[dt]
        if len(g) < 70:
            prev = g
            continue
        c = g["Close"].to_numpy(float)
        tm = g.index.time
        for j in range(len(c)):
            if FIRST <= tm[j] <= LAST:
                base.append({"sym": sym, "day": dt, **{f"r{h}": fwd(c, j, h) for h in HORIZONS}})
        if prev is not None and len(prev) >= 70:
            orb = g.iloc[:6]
            lvls = [(float(orb["High"].max()), "orb_hi", 6), (float(orb["Low"].min()), "orb_lo", 6),
                    (float(prev["High"].max()), "pd_hi", 1), (float(prev["Low"].min()), "pd_lo", 1)]
            for arm, d, j in day_events(g, lvls):
                r = {f"r{h}": (None if fwd(c, j, h) is None else d * fwd(c, j, h)) for h in HORIZONS}
                rows.append({"sym": sym, "day": dt, "arm": arm, "dir": d, **r})
        prev = g
    return pd.DataFrame(rows), pd.DataFrame(base)


def adjust(ev, base):
    """يطرح المرجع العشوائي: متوسط عائد كل الشموع لكل رمز ونصف، في جهة الصفقة."""
    ev = ev.copy()
    for df in (ev, base):
        df["half"] = np.where(df["day"] < SPLIT, 1, 2)
    m = base.groupby(["sym", "half"])[[f"r{h}" for h in HORIZONS]].mean()
    for h in HORIZONS:
        mu = m[f"r{h}"].reindex(pd.MultiIndex.from_arrays([ev["sym"], ev["half"]])).to_numpy()
        ev[f"a{h}"] = ev[f"r{h}"] - ev["dir"] * mu
    return ev


# ============================ الإحصاء ============================
def boot_diff(ev, arm, ref, col, seed=0):
    """(فرق المتوسطين، فاصل 95%، p ثنائي) بإعادة معاينة الأيام. ref=None: متوسط الذراع وحده مقابل الصفر."""
    x = ev[ev[col].notna()]
    days = np.array(sorted(x["day"].unique()))
    idx = {dd: k for k, dd in enumerate(days)}

    def sums(a):
        s = x[x["arm"] == a]
        k = s["day"].map(idx).to_numpy()
        return np.bincount(k, s[col].to_numpy(), len(days)), np.bincount(k, None, len(days))

    sa, na = sums(arm)
    if na.sum() == 0:
        return np.nan, (np.nan, np.nan), np.nan
    sb, nb = sums(ref) if ref else (None, None)
    est = sa.sum() / na.sum() - (sb.sum() / nb.sum() if ref else 0.0)
    rng = np.random.default_rng(seed)
    W = rng.multinomial(len(days), np.full(len(days), 1 / len(days)), size=NBOOT).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        bs = (W @ sa) / (W @ na) - ((W @ sb) / (W @ nb) if ref else 0.0)
    bs = bs[np.isfinite(bs)]
    p = min(1.0, 2 * min((bs <= 0).mean(), (bs >= 0).mean()))
    return est, (np.percentile(bs, 2.5), np.percentile(bs, 97.5)), p


def bh_reject(ps, q=0.05):
    ps = np.asarray(ps, float)
    order = np.argsort(ps)
    m = len(ps)
    rej = np.zeros(m, bool)
    passed = [k for k in range(m) if ps[order[k]] <= q * (k + 1) / m]
    if passed:
        rej[order[:max(passed) + 1]] = True
    return rej


def hypotheses(ev):
    col = f"a{MAIN_H}"
    e1, e2 = ev[ev["half"] == 1], ev[ev["half"] == 2]
    tests = [("H1", "S12", "K3"), ("H2", "S1245", "K3"), ("H3", "RT", "K3"), ("H4", "FK", None)]
    res = []
    for hid, a, r in tests:
        d1 = boot_diff(e1, a, r, col)[0]
        d2, ci, p = boot_diff(e2, a, r, col)
        if hid == "H3":                                              # RT: موجب وأفضل من K3 معاً
            m0, ci0, p0 = boot_diff(e2, a, None, col)
            m01 = boot_diff(e1, a, None, col)[0]
            p = max(p, p0)
            ok_extra = m0 > 0 and ci0[0] > 0 and m01 > 0
        else:
            ok_extra = True
        res.append({"id": hid, "arm": a, "ref": r, "d1": d1, "d2": d2, "ci": ci, "p": p, "extra": ok_extra})
    rej = bh_reject([x["p"] for x in res])
    for x, rj in zip(res, rej):
        x["ok"] = bool(rj and x["d1"] > 0 and x["d2"] > 0 and x["ci"][0] > 0 and x["extra"])
    return res


def table(ev, title):
    L = [f"### {title}", "", "| الذراع | أحداث (النصف 2) | " + " | ".join(f"{h}د" for h in HORIZONS) + " | رابحة بعد شمعة |",
         "|---|---|" + "---|" * len(HORIZONS) + "---|"]
    e2 = ev[ev["half"] == 2]
    for a in ARMS:
        s = e2[e2["arm"] == a]
        if not len(s):
            continue
        cells = [f"{s[f'a{h}'].mean():+.1f}" for h in HORIZONS]
        L.append(f"| {NAMES[a]} | {len(s)} | " + " | ".join(cells) + f" | {(s['r5'] > 0).mean() * 100:.0f}% |")
    return L


def report(groups):
    L = ["# H-BRKSIGNS: علامات الاختراق الحقيقي والكاذب مقابل قاعدة 3 شموع (نتائج آلية)", "",
         "عائد الأصل في جهة الصفقة بنقاط الأساس، ناقص المرجع العشوائي. الجداول للنصف الثاني (الحكم، بعد 2022-11-23).", ""]
    for gname, ev in groups.items():
        L += table(ev, gname) + ["", f"**الفرضيات ({gname}، أفق {MAIN_H}د، النصف الثاني، BH 5%):**", "",
                                 "| الفرضية | المقارنة | النصف 1 | النصف 2 | فاصل 95% | p | الحكم |", "|---|---|---|---|---|---|---|"]
        for x in hypotheses(ev):
            cmp_ = f"{NAMES[x['arm']]} − {NAMES[x['ref']]}" if x["ref"] else f"{NAMES[x['arm']]} > 0"
            L.append(f"| {x['id']} | {cmp_} | {x['d1']:+.2f} | {x['d2']:+.2f} | [{x['ci'][0]:+.2f}، {x['ci'][1]:+.2f}] | {x['p']:.3f} | "
                     f"{'مدعومة ✅' if x['ok'] else 'غير مدعومة'} |")
        L.append("")
    L += ["تحفظ: على الأصل لا العقود، بلا كلفة؛ المستويات أربعة فقط؛ «وقت الأخبار» و«المستوى الملموس مرة» لم يُطبّقا (غير مسجّلين)."]
    return "\n".join(L) + "\n"


def main(argv):
    src = argv[0] if argv else "/opt/spxbot/data"
    files = {"SPY": os.path.join(src, "spy_5m.csv.gz")}
    for s in STOCKS:
        p = os.path.join(src, f"{s}.csv.gz")
        files[s] = p if os.path.exists(p) else os.path.join(src, "bars_5m", f"{s}.csv.gz")
    evs, bases = {}, {}
    for s, p in files.items():
        ev, base = symbol_rows(s, read_bars(p))
        evs[s], bases[s] = ev, base
        print(s, len(ev), "أحداث", flush=True)
    spy = adjust(evs["SPY"], bases["SPY"])
    stk = adjust(pd.concat([evs[s] for s in STOCKS]), pd.concat([bases[s] for s in STOCKS]))
    md = report({"SPY": spy, "الأسهم الست مجمّعة": stk})
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main(sys.argv[1:])
