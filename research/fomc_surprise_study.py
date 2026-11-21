#!/usr/bin/env python3
"""H-FEDSURPRISE: مفاجأة الفيدرالي (من السندات القصيرة لحظة القرار) والعوامل المتزامنة، وأثرها على المؤشر. مسجّل في CLAUDE.md 21/11/2026.

استكشافي (نحو 60 اجتماعاً منذ 2019)، ولا يغيّر البوت.
المفاجأة = عائد SHY من إغلاق 13:55 إلى إغلاق 14:30 (موجب = متساهلة). العوامل قبل القرار: أخبار سياسة/جيوسياسة،
ضعف القياديات، اتجاه صباحي معاكس.
تشغيل: SPXBOT_DATA=/opt/spxbot/data python research/fomc_surprise_study.py   المخرج: research/fomc_surprise_results.md
"""
import csv
import os
import sys
from datetime import date

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brksigns_study as bk  # noqa: E402
import fomc_study as fs  # noqa: E402

DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
OUT = os.path.join(HERE, "fomc_surprise_results.md")
LEADERS = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL"]
I1355, I1430 = 53, 59          # إغلاق شمعة 13:55 وإغلاق شمعة 14:25 (= 14:30)
NPERM = 10000
# نوع القرار من الذاكرة (2019–2025)؛ 2026 غير معروف. وصفي فقط.
HIKE = set("2022-03-16 2022-05-04 2022-06-15 2022-07-27 2022-09-21 2022-11-02 2022-12-14 2023-02-01 2023-03-22 2023-05-03 2023-07-26".split())
CUT = set("2019-07-31 2019-09-18 2019-10-30 2024-09-18 2024-11-07 2024-12-18 2025-09-17 2025-10-29 2025-12-10".split())
POLICY_THEMES_NEG = {"tariffs_trade", "china", "defense_war", "geopolitics", "taxes", "sanctions"}


def decision(d):
    return "رفع" if d in HIKE else "خفض" if d in CUT else ("غير معروف" if d.startswith("2026") else "تثبيت")


def load(sym):
    p = os.path.join(DATA, "spy_5m.csv.gz") if sym == "SPY" else os.path.join(DATA, "bars_5m", f"{sym}.csv.gz")
    d5 = bk.read_bars(p)
    return {dt: g for dt, g in d5.groupby(d5.index.date) if len(g) >= 78}


def policy_days():
    p = os.path.join(HERE, "news_history_policy_days.csv")
    out = {}
    if os.path.exists(p):
        for r in csv.DictReader(open(p, encoding="utf-8")):
            out.setdefault(r["day"], set()).add(r["theme"])
    return out


def rows():
    spy, shy, ief = load("SPY"), load("SHY"), load("IEF")
    lead = {s: load(s) for s in LEADERS}
    pol = policy_days()
    days = sorted(spy)
    nxt = {d: days[i + 1] for i, d in enumerate(days[:-1])}
    out = []
    for ds in fs.FOMC:
        d = date.fromisoformat(ds)
        if d not in spy or d not in shy:
            continue
        s, b = spy[d]["Close"].to_numpy(float), shy[d]["Close"].to_numpy(float)
        f = ief[d]["Close"].to_numpy(float) if d in ief else None
        o = float(spy[d]["Open"].iloc[0])
        r = {"day": ds, "dec": decision(ds),
             "sur": (b[I1430] / b[I1355] - 1) * 1e4,                                 # نقاط أساس
             "ief": (f[I1430] / f[I1355] - 1) * 1e4 if f is not None else np.nan,
             "pre": (s[I1355] / o - 1) * 100, "r30": (s[I1430] / s[I1355] - 1) * 100,
             "r_after": (s[-1] / s[I1430] - 1) * 100, "r_day": (s[-1] / s[I1355] - 1) * 100,
             "r_next": (float(spy[nxt[d]]["Close"].iloc[-1]) / s[-1] - 1) * 100 if d in nxt else np.nan}
        lr = [float(lead[x][d]["Close"].iloc[I1355]) / float(lead[x][d]["Open"].iloc[0]) - 1 for x in LEADERS if d in lead[x]]
        r["lead"] = float(np.mean(lr)) * 100 if lr else np.nan
        th = pol.get(ds, set())
        r["policy"] = ",".join(sorted(th & POLICY_THEMES_NEG)) if ds >= "2025-01-01" else "—"
        sgn = np.sign(r["sur"])
        r["opp_lead"] = bool(r["lead"] < -0.5 and sgn > 0) or bool(r["lead"] > 0.5 and sgn < 0)
        r["opp_pre"] = bool(abs(r["pre"]) >= 0.3 and np.sign(r["pre"]) == -sgn and sgn != 0)
        r["opp"] = r["opp_lead"] or r["opp_pre"]
        out.append(r)
    return out


def spearman(x, y):
    rx, ry = np.argsort(np.argsort(x)), np.argsort(np.argsort(y))
    return float(np.corrcoef(rx, ry)[0, 1])


def perm_p(x, y, seed=1):
    rng = np.random.default_rng(seed)
    obs = spearman(x, y)
    null = np.array([spearman(x, rng.permutation(y)) for _ in range(NPERM)])
    return obs, float((null >= obs).mean())


def agree(rs):
    a = [np.sign(r["sur"]) == np.sign(r["r_day"]) for r in rs if r["sur"] != 0 and r["r_day"] != 0]
    return (float(np.mean(a)) if a else np.nan), len(a)


def report(rs):
    x = np.array([r["sur"] for r in rs])
    L = ["# مفاجأة الفيدرالي والعوامل المتزامنة (H-FEDSURPRISE، استكشافي)", "",
         f"- {len(rs)} اجتماعاً ({rs[0]['day']} → {rs[-1]['day']}). المفاجأة = حركة SHY لحظة القرار (13:55→14:30) بنقاط الأساس: موجب = متساهلة، سالب = متشددة.",
         "- رد المؤشر = SPY بالنسبة المئوية. العوامل قبل القرار (الافتتاح→13:55).", "",
         "## 1) هل المفاجأة تحدد الاتجاه؟ (ارتباط سبيرمان، p بالتبديل، فرضية F1 على أفق 13:55→الإغلاق)", "",
         "| الأفق | الارتباط | p |", "|---|---|---|"]
    for k, nm in (("r30", "أول 30 دقيقة"), ("r_after", "14:30→الإغلاق (بعد المؤتمر)"), ("r_day", "13:55→الإغلاق (F1)"), ("r_next", "اليوم التالي")):
        ok = [(a, r[k]) for a, r in zip(x, rs) if not np.isnan(r[k])]
        c, p = perm_p(np.array([a for a, _ in ok]), np.array([b for _, b in ok]))
        L.append(f"| {nm} | {c:+.2f} | {p:.3f} |")
    xi = np.array([r["ief"] for r in rs])
    m = ~np.isnan(xi)
    c, p = perm_p(xi[m], np.array([r["r_day"] for r in rs])[m])
    L += ["", f"للمقارنة بسندات 7–10 سنوات (IEF): الارتباط مع 13:55→الإغلاق {c:+.2f} (p {p:.3f}).", "",
          "## 2) العوامل المعاكسة (F2): نسبة توافق اتجاه المؤشر مع المفاجأة", ""]
    clean = [r for r in rs if not r["opp"]]
    dirty = [r for r in rs if r["opp"]]
    a1, n1 = agree(clean)
    a2, n2 = agree(dirty)
    al, nl = agree([r for r in rs if r["opp_lead"]])
    ap, npre = agree([r for r in rs if r["opp_pre"]])
    rng = np.random.default_rng(2)
    flags = np.array([r["opp"] for r in rs if r["sur"] != 0 and r["r_day"] != 0])
    hits = np.array([np.sign(r["sur"]) == np.sign(r["r_day"]) for r in rs if r["sur"] != 0 and r["r_day"] != 0])
    obs = hits[~flags].mean() - hits[flags].mean() if flags.any() and (~flags).any() else np.nan
    null = []
    for _ in range(NPERM):
        f = rng.permutation(flags)
        null.append(hits[~f].mean() - hits[f].mean())
    pF2 = float((np.array(null) >= obs).mean()) if not np.isnan(obs) else np.nan
    L += ["| المجموعة | اجتماعات | توافق |", "|---|---|---|",
          f"| بلا عامل معاكس | {n1} | {a1 * 100:.0f}% |", f"| مع عامل معاكس (ب أو ج) | {n2} | {a2 * 100:.0f}% |",
          f"| — منها القياديات تعاكس | {nl} | {al * 100:.0f}% |", f"| — منها الاتجاه الصباحي يعاكس | {npre} | {ap * 100:.0f}% |", "",
          f"الفرق {obs * 100:+.0f} نقطة مئوية، p بالتبديل {pF2:.3f}.", "",
          "## 3) حسب نوع القرار (وصفي)", "", "| القرار | اجتماعات | المؤشر صعد (13:55→الإغلاق) | متوسط | مفاجأة متوسطة |", "|---|---|---|---|---|"]
    for dec in ("خفض", "تثبيت", "رفع", "غير معروف"):
        g = [r for r in rs if r["dec"] == dec]
        if g:
            L.append(f"| {dec} | {len(g)} | {np.mean([r['r_day'] > 0 for r in g]) * 100:.0f}% | {np.mean([r['r_day'] for r in g]):+.2f}% | "
                     f"{np.mean([r['sur'] for r in g]):+.1f} |")
    L += ["", "## 4) كل اجتماع", "",
          "| اليوم | القرار | المفاجأة | قبل القرار | القياديات | 30د | بعد المؤتمر | حتى الإغلاق | الغد | أخبار سياسة | عامل معاكس |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rs:
        tag = "قياديات" if r["opp_lead"] else ""
        tag += ("، " if tag and r["opp_pre"] else "") + ("اتجاه صباحي" if r["opp_pre"] else "")
        L.append(f"| {r['day']} | {r['dec']} | {r['sur']:+.1f} | {r['pre']:+.2f}% | {r['lead']:+.2f}% | {r['r30']:+.2f}% | {r['r_after']:+.2f}% | "
                 f"{r['r_day']:+.2f}% | {r['r_next']:+.2f}% | {r['policy'] or '—'} | {tag or '—'} |")
    L += ["", "تحفظ: نحو 60 حدثاً فقط؛ المفاجأة تقريب من السوق لا من توقعات FedWatch؛ نوع القرار من الذاكرة؛ أخبار السياسة متوفرة من 2025 فقط."]
    return "\n".join(L) + "\n"


def main():
    rs = rows()
    md = report(rs)
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main()
