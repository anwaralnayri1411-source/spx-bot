"""تشغيل: python research/fade_extreme_study.py (يقرأ الكاش فقط ويطبع النتائج؛ الملخص في research/reversal_results.md)
«القاع نفسه عكس الزخم»: حركة ≥0.25% في 30د تصنع قاع/قمة جديدة لليوم → نعاكسها. دخول لحظي ومتأخر، ودلالة، وعقد نظري."""
import math, numpy as np, pandas as pd
from math import comb
d = pd.read_csv(__import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "spx_5m_cache.csv"))
d["time"] = pd.to_datetime(d["time"], utc=True).dt.tz_convert("America/New_York")
d = d.set_index("time").sort_index(); d["day"] = d.index.date
days = [g for _, g in d.groupby("day")]
N = lambda x: 0.5 * (1 + math.erf(x / 2 ** .5)); IV = 0.15
def bs(S, K, T, call):
    if T <= 0: return max(0.0, S - K) if call else max(0.0, K - S)
    v = IV * math.sqrt(T); d1 = (math.log(S / K) + v * v / 2) / v; d2 = d1 - v
    return S * N(d1) - K * N(d2) if call else K * N(-d2) - S * N(-d1)
Tl = lambda t: max(16 * 60 - (t.hour * 60 + t.minute), 0) / (390 * 252)
rng = np.random.default_rng(5)
def run(delay_bars, otm, latest=(15, 0)):
    out = []
    for di in range(1, len(days)):
        g = days[di]; last = -99
        for i in range(6, len(g) - 1):
            t = g.index[i]
            if (t.hour, t.minute) > latest: break
            mv = g["Close"].iloc[i] / g["Close"].iloc[i - 6] - 1
            if abs(mv) < 0.0025 or i - last < 6: continue
            last = i; mom = 1 if mv > 0 else -1
            ext = g["High"].iloc[:i + 1].max() if mom == 1 else g["Low"].iloc[:i + 1].min()
            if abs(g["Close"].iloc[i] / ext - 1) > 0.0005: continue
            s = -mom; j = i + delay_bars
            if j >= len(g) - 1: continue
            e = g["Close"].iloc[j]; te = g.index[j]; hit = np.nan
            for _, b in g.iloc[j + 1:].iterrows():
                up = (b["High"] >= e * 1.0025) if s == 1 else (b["Low"] <= e * 0.9975)
                dn = (b["Low"] <= e * 0.9975) if s == 1 else (b["High"] >= e * 1.0025)
                if up and dn: hit = 0.5; break
                if up: hit = 1.0; break
                if dn: hit = 0.0; break
            K = round(e * (1 + otm * s) / 5) * 5; call = s == 1
            p0 = bs(e, K, Tl(te), call); rule = np.nan
            if p0 > 0.3:
                fut = g.iloc[j + 1:]; rule = None
                for tt, b in fut.iterrows():
                    best = bs(b["High"] if call else b["Low"], K, Tl(tt), call); cv = bs(b["Close"], K, Tl(tt), call)
                    if best >= 1.5 * p0: rule = 0.5; break
                    if (tt - te).seconds >= 900 and cv <= 0.6 * p0: rule = cv / p0 - 1; break
                    if (tt.hour, tt.minute) >= (15, 30): rule = cv / p0 - 1; break
                if rule is None: rule = bs(fut["Close"].iloc[-1], K, 0, call) / p0 - 1
            out.append(dict(di=di, t=te.strftime("%H:%M"), hit=hit, r60=s * (g["Close"].iloc[min(j + 12, len(g) - 1)] - e), rule=rule))
    return pd.DataFrame(out)
def pval(h):
    h = h.dropna(); k = int(round(h.sum())); n = len(h)
    return sum(comb(n, x) for x in range(k, n + 1)) / 2 ** n
def bootci(z, col):
    by = {k: v[col].dropna().values for k, v in z.groupby("di")}; ks = list(by); m = []
    for _ in range(2000):
        p = rng.choice(len(ks), len(ks)); a = np.concatenate([by[ks[q]] for q in p]); m.append(a.mean() if len(a) else np.nan)
    return np.nanpercentile(m, [2.5, 97.5])
print("| الدخول | العقد | صفقات | أيام | لمس الهدف أولاً | p (ذو طرف) | 60د نقاط [فاصل أيام] | العقد بقاعدة +50/−40 [فاصل] |")
for lab, db in (("لحظي", 0), ("متأخر 5د", 1), ("متأخر 15د", 3)):
    for otm in (0.0, 0.004):
        z = run(db, otm)
        c1 = bootci(z, "r60"); c2 = bootci(z, "rule")
        print(f"| {lab} | {'على السعر' if otm == 0 else 'خارج 0.4%'} | {len(z)} | {z.di.nunique()} | {np.nanmean(z.hit)*100:.0f}% | {pval(z.hit):.3f} | {z.r60.mean():+.1f} [{c1[0]:+.1f},{c1[1]:+.1f}] | {np.nanmean(z.rule)*100:+.0f}% [{c2[0]*100:+.0f},{c2[1]*100:+.0f}] |")
z = run(0, 0.0)
print("\nحسب الوقت (لحظي):")
for lab, y in (("قبل 10:45", z[z.t <= "10:45"]), ("بعد 10:45", z[z.t > "10:45"])):
    print(f"  {lab}: n={len(y)} لمس {np.nanmean(y.hit)*100:.0f}% 60د {y.r60.mean():+.1f} عقد {np.nanmean(y.rule)*100:+.0f}%")
