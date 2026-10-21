"""تشغيل: python research/opening_reversal_study.py (يقرأ الكاش فقط ويطبع النتائج؛ الملخص في research/reversal_results.md)
دراسة موسّعة: (أ) ارتداد الهبوط/الصعود الافتتاحي، (ب) شراء القاع عكس الزخم مقابل اتباع الزخم، (ج) معدلات أساسية.
SPX 5د، 60 يوماً. استكشاف = أول 40 يوماً، محجوز = آخر 19 يوماً. المقياس الرئيسي: لمس +M باتجاه الصفقة قبل −M ضدها
(M = 0.25%؛ العشوائي ≈ 50%)، ونقاط بعد 60د. خارج المستودع، قراءة فقط."""
import numpy as np, pandas as pd
d = pd.read_csv(__import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "spx_5m_cache.csv"))
d["time"] = pd.to_datetime(d["time"], utc=True).dt.tz_convert("America/New_York")
d = d.set_index("time").sort_index(); d["day"] = d.index.date
days = [g for _, g in d.groupby("day")]
SPLIT = 40
M = 0.0025

def touch(g, j, s, m=M):
    e = g["Close"].iloc[j]
    for _, b in g.iloc[j + 1:].iterrows():
        up = (b["High"] >= e * (1 + m)) if s == 1 else (b["Low"] <= e * (1 - m))
        dn = (b["Low"] <= e * (1 - m)) if s == 1 else (b["High"] >= e * (1 + m))
        if up and dn: return 0.5
        if up: return 1.0
        if dn: return 0.0
    return np.nan  # لا لمس حتى الإغلاق

def r(g, j, s, k):
    return s * (g["Close"].iloc[min(j + k, len(g) - 1)] - g["Close"].iloc[j])

def summ(rows, label):
    df = pd.DataFrame(rows)
    out = []
    for part, z in (("استكشاف", df[df.di < SPLIT]), ("محجوز", df[df.di >= SPLIT]), ("الكل", df)):
        if len(z) == 0: out.append(f"{part}: 0"); continue
        out.append(f"{part}: n={len(z)} أيام={z.di.nunique()} لمس={np.nanmean(z.t)*100:.0f}% (بلا لمس {z.t.isna().mean()*100:.0f}%) 60د={z.r60.mean():+.1f} إغلاق={z.rc.mean():+.1f}")
    print(f"- {label}\n    " + "\n    ".join(out))
    return df

# ---------- (ج) معدلات أساسية ----------
print("## (ج) معدلات أساسية")
lo30 = lo60 = hi30 = hi60 = 0; firsthr_drop = []; n = 0
for di in range(1, len(days)):
    g = days[di]; pc = days[di - 1]["Close"].iloc[-1]; n += 1
    dl, dh = g["Low"].min(), g["High"].max()
    f30, f60 = g.iloc[:6], g.iloc[:12]
    lo30 += f30["Low"].min() == dl; lo60 += f60["Low"].min() == dl
    hi30 += f30["High"].max() == dh; hi60 += f60["High"].max() == dh
    if f60["Low"].min() <= pc * 0.997:
        firsthr_drop.append((f60["Low"].min() == dl, g["High"].max() >= pc, g["Close"].iloc[-1] > f60["Close"].iloc[-1]))
print(f"أيام={n}: أدنى اليوم في أول 30د {lo30/n*100:.0f}%، في أول ساعة {lo60/n*100:.0f}% | أعلى اليوم في أول 30د {hi30/n*100:.0f}%، أول ساعة {hi60/n*100:.0f}%")
f = np.array(firsthr_drop)
print(f"أيام هبط فيها أول ساعة ≥0.3% تحت إغلاق أمس: {len(f)} | قاع أول ساعة بقي قاع اليوم {f[:,0].mean()*100:.0f}% | سُدّت الفجوة (لمس إغلاق أمس) {f[:,1].mean()*100:.0f}% | الإغلاق فوق سعر 10:30 {f[:,2].mean()*100:.0f}%")

# ---------- (أ) ارتداد افتتاحي بشبكة إعدادات ----------
print("\n## (أ) ارتداد الهبوط/الصعود الافتتاحي (كول بعد هبوط، بوت بعد صعود)")
grid = {}
for X in (0.002, 0.003, 0.005, 0.007):
    for R in (0.0005, 0.001, 0.002):
        for end in ((10, 15), (10, 45), (11, 30)):
            for vw in (False, True):
                rows = []
                for di in range(1, len(days)):
                    g = days[di]; pc = days[di - 1]["Close"].iloc[-1]
                    tp = (g["High"] + g["Low"] + g["Close"]) / 3; vwap = tp.expanding().mean()
                    for s in (1, -1):
                        for i in range(1, len(g) - 1):
                            t = g.index[i]
                            if (t.hour, t.minute) > end: break
                            ext = g["Low"].iloc[:i + 1].min() if s == 1 else g["High"].iloc[:i + 1].max()
                            c = g["Close"].iloc[i]
                            fl = ext <= pc * (1 - X) if s == 1 else ext >= pc * (1 + X)
                            cf = c >= ext * (1 + R) if s == 1 else c <= ext * (1 - R)
                            if vw: cf = cf and ((c > vwap.iloc[i]) if s == 1 else (c < vwap.iloc[i]))
                            if fl and cf:
                                rows.append(dict(di=di, s=s, t=touch(g, i, s), r60=r(g, i, s, 12), rc=r(g, i, s, 999)))
                                break
                if rows:
                    z = pd.DataFrame(rows)
                    ex, ho = z[z.di < SPLIT], z[z.di >= SPLIT]
                    grid[(X, R, end, vw)] = (len(ex), np.nanmean(ex.t) if len(ex) else np.nan, ex.r60.mean() if len(ex) else np.nan,
                                              len(ho), np.nanmean(ho.t) if len(ho) else np.nan, ho.r60.mean() if len(ho) else np.nan)
G = pd.DataFrame(grid, index=["n_ex", "touch_ex", "r60_ex", "n_ho", "touch_ho", "r60_ho"]).T
G = G[G.n_ex >= 10].sort_values("touch_ex", ascending=False)
print(f"إعدادات مجرّبة: {len(grid)} (المعروض: ≥10 صفقات في الاستكشاف)")
print(f"وسيط اللمس في الاستكشاف عبر كل الإعدادات: {G.touch_ex.median()*100:.0f}% | في المحجوز: {G.touch_ho.median()*100:.0f}%")
print("أفضل 5 في الاستكشاف ثم أداؤها في المحجوز:")
for k, v in G.head(5).iterrows():
    print(f"  X={k[0]*100:.1f}% R={k[1]*100:.2f}% حتى {k[2][0]}:{k[2][1]:02d} VWAP={'نعم' if k[3] else 'لا'} | استكشاف n={v.n_ex:.0f} لمس {v.touch_ex*100:.0f}% 60د {v.r60_ex:+.1f} | محجوز n={v.n_ho:.0f} لمس {v.touch_ho*100:.0f}% 60د {v.r60_ho:+.1f}")

# ---------- (ب) زخم 30 دقيقة: اتباع مقابل عكس، حسب موقع السعر والوقت ----------
print("\n## (ب) حدث زخم (حركة ≥0.25% في 30د): اتباع مقابل عكس (القاع/القمة)")
ev = []
for di in range(1, len(days)):
    g = days[di]; last = -99
    for i in range(6, len(g) - 1):
        t = g.index[i]
        if (t.hour, t.minute) > (15, 0): break
        mv = g["Close"].iloc[i] / g["Close"].iloc[i - 6] - 1
        if abs(mv) >= 0.0025 and i - last >= 6:
            last = i; s = 1 if mv > 0 else -1
            ext = (g["Low"].iloc[:i + 1].min() if s == -1 else g["High"].iloc[:i + 1].max())
            at_ext = abs(g["Close"].iloc[i] / ext - 1) <= 0.0005   # عند قاع/قمة اليوم حتى الآن
            early = (t.hour, t.minute) <= (10, 45)
            ev.append(dict(di=di, s=s, at_ext=at_ext, early=early, tf=touch(g, i, s), r60f=r(g, i, s, 12), rcf=r(g, i, s, 999)))
E = pd.DataFrame(ev)
for lab, z in (("كل الأحداث", E), ("مبكر ≤10:45", E[E.early]), ("بعد 10:45", E[~E.early]),
               ("عند قمة/قاع اليوم", E[E.at_ext]), ("مبكر وعند الطرف", E[E.early & E.at_ext])):
    for part, y in (("استكشاف", z[z.di < SPLIT]), ("محجوز", z[z.di >= SPLIT])):
        if len(y) == 0: continue
        tf = np.nanmean(y.tf)
        print(f"  {lab} [{part}] n={len(y)}: اتباع يلمس أولاً {tf*100:.0f}% (العكس {(1-tf)*100:.0f}%)، 60د اتباع {y.r60f.mean():+.1f} نقطة (العكس {-y.r60f.mean():+.1f})")
