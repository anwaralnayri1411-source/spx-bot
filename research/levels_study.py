#!/usr/bin/env python3
"""دراسة المستويات واختراق أول 30 دقيقة على SPY (قراءة فقط، لا تغيّر أي توصية). بطلب المستخدم 24/10/2026: «قواعد المحللين المشتركة».

البيانات: شموع SPY 5 دقائق لسنوات (`research/spy_history.py`)، الجلسة العادية فقط.
(1) المستويات: أعلى/أدنى/إغلاق أمس (PDH، PDL، PDC)، افتتاح اليوم، VWAP الجاري (بعد 10:00)، أعلى/أدنى أول 30 دقيقة (ORH، ORL)، وأرقام مستديرة (مضاعفات 5$).
    لكل مستوى، أول لمس في اليوم فقط (لتجنب التداخل). الصفقتان:
      fade  = ارتداد: دخول عند المستوى باتجاه الارتداد، الوقف خلفه بمقدار b (0.10%)، الهدف R×b (R=1.5)، والخروج بعد 12 شمعة (ساعة) بالإغلاق.
      break = اختراق: دخول عند إغلاق شمعة تتجاوز المستوى بـ0.02% خلال 6 شموع من اللمس، الوقف خلف المستوى بـb، الهدف R × (مسافة الوقف).
    إن لمس الوقف والهدف في الشمعة نفسها فالوقف أولاً (تحفظ)؛ شمعة اللمس لا يُحسب هدفها (لا نعرف ترتيب الحركة داخلها). تكلفة 0.002% لكل جهة.
    مرجع «وهمي»: المستويات نفسها مُزاحة ±0.35% (سعر لا بنية له) بالإجراء نفسه؛ والسؤال: هل المستوى الحقيقي أفضل من سعر عشوائي؟
(2) اختراق نطاق أول 30 دقيقة (ORB): أول إغلاق بعد 10:00 يتجاوز ORH/ORL بـ0.02%؛ وقف عند الجهة الأخرى للنطاق؛ نتيجتان: هدف 1R أو احتفاظ حتى 15:55؛ والارتداد (fade) عكسها.
القياس بوحدات المخاطرة R، وفاصل 95% بإعادة معاينة الأيام (العينة الفعلية = الأيام). نصف التاريخ الأول «استكشاف» والثاني «اختبار».
**فرضية مسجّلة مسبقاً (24/10/2026، قبل رؤية النتائج):** متوسط R لصفقات fade على PDH وPDL معاً في نصف الاختبار أعلى من الوهمي بفاصل 95% لفرق لا يشمل الصفر، بعد 150 حدثاً على الأقل؛
وكل ما عداها استكشافي (عدد المقارنات كبير: لا يُعتمد أي رقم منفرد جيد).
تشغيل: python research/levels_study.py [مسار الملف]   المخرج: research/levels_study_results.md
"""
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "levels_study_results.md")
DATA = os.path.join(os.getenv("SPXBOT_DATA", "/opt/spxbot/data"), "spy_5m.csv.gz")
B = 0.0010           # مسافة الوقف (نسبة من السعر)
R = 1.5              # الهدف بمضاعف المخاطرة
HOLD = 12            # شموع الاحتفاظ بعد اللمس
BREAK_WIN = 6        # مهلة إغلاق الاختراق بعد اللمس
TOL = 0.0002         # تجاوز الاختراق
COST = 0.00002       # لكل جهة
SHIFT = 0.0035       # إزاحة المرجع الوهمي
OR_BARS = 6          # أول 30 دقيقة
PRIMARY_MIN = 150
FAMILIES = ("PDH", "PDL", "PDC", "OPEN", "VWAP", "ORH", "ORL", "ROUND5")


# ============================ دوال نقية ============================
def first_touch(c, h, l, level, start=1):
    """أول لمس للمستوى منذ start: (فهرس الشمعة، اتجاه الارتداد d) حيث d=−1 مقاومة (اقتراب من الأسفل) و+1 دعم (اقتراب من الأعلى). أو None."""
    for i in range(max(start, 1), len(c)):
        if c[i - 1] < level <= h[i]:
            return i, -1
        if c[i - 1] > level >= l[i]:
            return i, 1
    return None


def sim_fade(h, l, c, i, level, d, b=B, r=R, hold=HOLD):
    """ارتداد: دخول عند level في شمعة اللمس i باتجاه d. يرجع R بعد التكلفة."""
    stop = level - d * b * level
    target = level + d * r * b * level
    risk = b * level
    cost = 2 * COST * level / risk
    last = min(i + hold, len(c) - 1)
    for j in range(i, last + 1):
        if (d == 1 and l[j] <= stop) or (d == -1 and h[j] >= stop):
            return -1.0 - cost
        if j > i and ((d == 1 and h[j] >= target) or (d == -1 and l[j] <= target)):
            return r - cost
    return d * (c[last] - level) / risk - cost


def sim_break(h, l, c, i, level, d, b=B, r=R, hold=HOLD, win=BREAK_WIN, tol=TOL):
    """اختراق: بعد لمس في i بالاتجاه d (d=−1 مقاومة)، نبحث عن إغلاق يتجاوز المستوى في الاتجاه e=−d بـtol خلال win شموع، وندخل عنده. يرجع R أو None."""
    e = -d
    for j in range(i, min(i + win, len(c) - 1) + 1):
        if (e == 1 and c[j] > level * (1 + tol)) or (e == -1 and c[j] < level * (1 - tol)):
            entry = c[j]
            stop = level - e * b * level
            risk = abs(entry - stop)
            target = entry + e * r * risk
            cost = 2 * COST * entry / risk
            last = min(j + hold, len(c) - 1)
            for k in range(j + 1, last + 1):
                if (e == 1 and l[k] <= stop) or (e == -1 and h[k] >= stop):
                    return -1.0 - cost
                if (e == 1 and h[k] >= target) or (e == -1 and l[k] <= target):
                    return r - cost
            return e * (c[last] - entry) / risk - cost
    return None


def day_levels(prev, o, h, l, c, rounds=3):
    """مستويات اليوم: {اسم: [(مستوى، أول فهرس مسموح)]}. prev = (PDH, PDL, PDC) أو None."""
    n = len(c)
    out = {k: [] for k in FAMILIES}
    if prev is not None:
        out["PDH"].append((prev[0], 1))
        out["PDL"].append((prev[1], 1))
        out["PDC"].append((prev[2], 1))
    out["OPEN"].append((o[0], 1))
    if n > OR_BARS + 1:
        out["ORH"].append((float(np.max(h[:OR_BARS])), OR_BARS))
        out["ORL"].append((float(np.min(l[:OR_BARS])), OR_BARS))
    base = 5 * round(o[0] / 5)
    for m in range(-rounds, rounds + 1):
        lv = base + 5 * m
        if abs(lv / o[0] - 1) <= 0.012:
            out["ROUND5"].append((float(lv), 1))
    return out


def vwap_level(h, l, c, v, i):
    tp = (h[: i + 1] + l[: i + 1] + c[: i + 1]) / 3
    vv = v[: i + 1]
    return float((tp * vv).sum() / vv.sum()) if vv.sum() > 0 else float(c[i])


def day_events(day, prev, o, h, l, c, v):
    """كل أحداث اليوم: قائمة dict(day, family, mode, kind, R). kind = real | placebo."""
    ev = []
    lv = day_levels(prev, o, h, l, c)
    if len(c) > OR_BARS + 2:
        lv["VWAP"].append((vwap_level(h, l, c, v, OR_BARS), OR_BARS + 1))
    for fam, items in lv.items():
        for level, start in items:
            for kind, mult in (("real", [1.0]), ("placebo", [1 + SHIFT, 1 - SHIFT])):
                for m in mult:
                    L = level * m
                    t = first_touch(c, h, l, L, start)
                    if t is None:
                        continue
                    i, d = t
                    ev.append({"day": day, "family": fam, "mode": "fade", "kind": kind, "R": sim_fade(h, l, c, i, L, d)})
                    rb = sim_break(h, l, c, i, L, d)
                    if rb is not None:
                        ev.append({"day": day, "family": fam, "mode": "break", "kind": kind, "R": rb})
    return ev


def orb_events(day, o, h, l, c):
    """اختراق أول 30 دقيقة: أول إغلاق يتجاوز ORH/ORL. يرجع قائمة dict بنتيجتين (هدف 1R، احتفاظ للإغلاق) وعكسهما (fade)."""
    if len(c) < OR_BARS + 3:
        return []
    orh, orl = float(np.max(h[:OR_BARS])), float(np.min(l[:OR_BARS]))
    for j in range(OR_BARS, len(c) - 1):
        d = 1 if c[j] > orh * (1 + TOL) else (-1 if c[j] < orl * (1 - TOL) else 0)
        if d == 0:
            continue
        entry = float(c[j])
        stop = orl if d == 1 else orh
        risk = abs(entry - stop)
        if risk <= 0:
            return []
        cost = 2 * COST * entry / risk
        target = entry + d * risk
        r1 = None
        for k in range(j + 1, len(c)):
            if (d == 1 and l[k] <= stop) or (d == -1 and h[k] >= stop):
                r1 = -1.0
                break
            if (d == 1 and h[k] >= target) or (d == -1 and l[k] <= target):
                r1 = 1.0
                break
        if r1 is None:
            r1 = d * (c[-1] - entry) / risk
        rclose = d * (c[-1] - entry) / risk
        return [{"day": day, "family": "ORB", "mode": "breakout 1R", "kind": "real", "R": r1 - cost},
                {"day": day, "family": "ORB", "mode": "breakout حتى الإغلاق", "kind": "real", "R": rclose - cost},
                {"day": day, "family": "ORB", "mode": "ارتداد (عكس الاختراق) حتى الإغلاق", "kind": "real", "R": -rclose - cost}]
    return []


def boot_mean_ci(df, col="R", n=2000, seed=7):
    """فاصل 95% لمتوسط R بإعادة معاينة الأيام (متّجه: مجموع وعدد لكل يوم). None إن قلّت الأيام عن 5."""
    g = df.groupby("day")[col].agg(["sum", "count"])
    if len(g) < 5:
        return None
    S, N = g["sum"].to_numpy(), g["count"].to_numpy()
    idx = np.random.default_rng(seed).integers(0, len(S), (n, len(S)))
    m = S[idx].sum(1) / N[idx].sum(1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def boot_diff_ci(real, plac, n=2000, seed=7):
    """فاصل 95% لفرق متوسط R (حقيقي − وهمي) بإعادة معاينة الأيام نفسها للمجموعتين. None إن قلّت الأيام عن 5."""
    a = real.groupby("day")["R"].agg(["sum", "count"])
    b = plac.groupby("day")["R"].agg(["sum", "count"])
    days = sorted(set(a.index) | set(b.index))
    if len(days) < 5:
        return None
    Sa, Na = a["sum"].reindex(days, fill_value=0).to_numpy(), a["count"].reindex(days, fill_value=0).to_numpy()
    Sb, Nb = b["sum"].reindex(days, fill_value=0).to_numpy(), b["count"].reindex(days, fill_value=0).to_numpy()
    idx = np.random.default_rng(seed).integers(0, len(days), (n, len(days)))
    na, nb = Na[idx].sum(1), Nb[idx].sum(1)
    ok = (na > 0) & (nb > 0)
    d = Sa[idx].sum(1)[ok] / na[ok] - Sb[idx].sum(1)[ok] / nb[ok]
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def stat(df):
    if len(df) == 0:
        return None
    return {"n": len(df), "days": df["day"].nunique(), "win": float((df["R"] > 0).mean()), "mean": float(df["R"].mean()), "ci": boot_mean_ci(df)}


def split_halves(events):
    days = sorted(events["day"].unique())
    cut = days[len(days) // 2]
    return events[events["day"] <= cut], events[events["day"] > cut]


def primary_verdict(test):
    r = test[(test["family"].isin(["PDH", "PDL"])) & (test["mode"] == "fade")]
    real, plac = r[r["kind"] == "real"], r[r["kind"] == "placebo"]
    if len(real) < PRIMARY_MIN or len(plac) == 0:
        return f"لا حكم بعد ({len(real)} من {PRIMARY_MIN} حدثاً في نصف الاختبار)"
    ci = boot_diff_ci(real, plac)
    d = real["R"].mean() - plac["R"].mean()
    if ci is None:
        return "لا حكم بعد (أيام قليلة)"
    if ci[0] > 0:
        return f"تحققت الفرضية: الارتداد من PDH/PDL أفضل من المرجع الوهمي بـ{d:+.3f}R (فاصل {ci[0]:+.3f}…{ci[1]:+.3f})"
    if ci[1] < 0:
        return f"عكسية: المرجع الوهمي أفضل ({d:+.3f}R، فاصل {ci[0]:+.3f}…{ci[1]:+.3f})"
    return f"لم تتحقق: الفرق {d:+.3f}R وفاصله {ci[0]:+.3f}…{ci[1]:+.3f} يشمل الصفر"


def fmt(s):
    if s is None:
        return "-"
    ci = "-" if s["ci"] is None else f"{s['ci'][0]:+.3f}…{s['ci'][1]:+.3f}"
    return f"{s['n']} | {100 * s['win']:.0f}% | {s['mean']:+.3f} | {ci}"


def report(events, orb, meta):
    L = ["# دراسة المستويات واختراق أول 30 دقيقة على SPY (نتائج آلية)", "",
         f"- {meta}. قراءة فقط، ولا تغيّر أي توصية. الوحدة: R (المخاطرة)، بعد تكلفة 0.002% لكل جهة. فاصل 95% بإعادة معاينة الأيام.",
         "- الوقف خلف المستوى بـ0.10% والهدف 1.5R وأقصى احتفاظ ساعة. المرجع الوهمي: المستوى نفسه مُزاحاً ±0.35% بالإجراء نفسه.",
         "- ملاحظة قراءة: القيم المطلقة لـR سالبة عادةً حتى على مشي عشوائي (تحفظ مقصود: وقف شمعة اللمس يُحسب وهدفها لا يُحسب، والوقف أولاً عند التعادل). المقارنة المعتمدة هي **الحقيقي مقابل الوهمي** لا القيمة المطلقة. أيام الإغلاق المبكر (13:00) مستبعدة من التداول.",
         "- ⚠️ مقارنات كثيرة (مستويات × اتجاهان × نصفان): أي رقم منفرد جيد قد يكون صدفة. الحكم فقط على الفرضية المسجّلة مسبقاً.", ""]
    if len(events) == 0:
        return L + ["لا بيانات."]
    explore, test = split_halves(events)
    for title, part in (("استكشاف (النصف الأول من التاريخ)", explore), ("اختبار (النصف الثاني)", test)):
        L += [f"## {title}", f"- أيام: {part['day'].nunique()} | من {part['day'].min()} إلى {part['day'].max()}", "",
              "| المستوى | الصفقة | حقيقي: حالات | فوز | متوسط R | فاصل | وهمي: حالات | فوز | متوسط R | فاصل | الفرق |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        for fam in FAMILIES:
            for mode in ("fade", "break"):
                r = part[(part["family"] == fam) & (part["mode"] == mode)]
                real, plac = r[r["kind"] == "real"], r[r["kind"] == "placebo"]
                sr, sp = stat(real), stat(plac)
                if sr is None:
                    continue
                diff = "-" if sp is None else f"{sr['mean'] - sp['mean']:+.3f}"
                L.append(f"| {fam} | {mode} | {fmt(sr)} | {fmt(sp) if sp else '- | - | - | -'} | {diff} |")
        L.append("")
    L += ["## الفرضية المسجّلة مسبقاً (ارتداد PDH/PDL مقابل الوهمي، نصف الاختبار)", "", "⚖️ **الحكم:** " + primary_verdict(test), ""]
    L += ["## اختراق أول 30 دقيقة (ORB)", ""]
    if len(orb) == 0:
        L.append("لا بيانات.")
    else:
        L += ["| الصفقة | أيام | فوز | متوسط R | فاصل 95% |", "|---|---|---|---|---|"]
        for mode, g in orb.groupby("mode"):
            s = stat(g)
            L.append(f"| {mode} | {s['days']} | {100 * s['win']:.0f}% | {s['mean']:+.3f} | {s['ci'][0]:+.3f}…{s['ci'][1]:+.3f} |" if s["ci"] else f"| {mode} | {s['days']} | - | {s['mean']:+.3f} | - |")
    return L


# ============================ البيانات والتشغيل ============================
def load(path):
    d = pd.read_csv(path)
    d["t"] = pd.to_datetime(d["t"], utc=True).dt.tz_convert("America/New_York")
    d = d.sort_values("t").drop_duplicates("t")
    m = d["t"].dt.hour * 60 + d["t"].dt.minute
    return d[(m >= 570) & (m < 960)].reset_index(drop=True)


HALF_DAY_RATIO = 0.20         # حجم 13:00–16:00 إلى حجم 9:30–13:00 أقل من هذا = إغلاق مبكر 13:00 (ما بعده ساعات ممتدة رقيقة)


def trim_half_day(g):
    """يرجع (اليوم مقصوصاً عند 13:00 إن كان إغلاقاً مبكراً، هل هو إغلاق مبكر). الجلسة الممتدة بعد الإغلاق المبكر تظهر بشموع قليلة الحجم فلا تُحسب جلسة عادية."""
    m = (g["t"].dt.hour * 60 + g["t"].dt.minute).to_numpy()
    am, pm = g["v"].to_numpy()[m < 780].sum(), g["v"].to_numpy()[m >= 780].sum()
    if am > 0 and pm / am < HALF_DAY_RATIO:
        return g[m < 780], True
    return g, False


def run(d):
    ev, orb, prev = [], [], None
    for day, g in d.groupby(d["t"].dt.date):
        g, half = trim_half_day(g)
        o, h, l, c, v = (g[k].to_numpy(float) for k in ("o", "h", "l", "c", "v"))
        if half and len(g) >= 30:             # أمس = اليوم المقصوص، لكن لا نتداول في الإغلاق المبكر
            prev = (float(h.max()), float(l.min()), float(c[-1]))
            continue
        if len(g) < 70:                       # يوم ناقص: لا نستعمله ولا يصير «أمس»
            continue
        ds = str(day)
        ev += day_events(ds, prev, o, h, l, c, v)
        orb += orb_events(ds, o, h, l, c)
        prev = (float(h.max()), float(l.min()), float(c[-1]))
    return pd.DataFrame(ev), pd.DataFrame(orb)


def main(argv):
    path = argv[0] if argv else DATA
    if not os.path.exists(path):
        print("لا ملف بيانات:", path, "(شغّل research/spy_history.py أولاً على الخادم)")
        return 0
    d = load(path)
    events, orb = run(d)
    meta = f"البيانات: {d['t'].dt.date.nunique()} يوماً من {d['t'].min():%Y-%m-%d} إلى {d['t'].max():%Y-%m-%d}، ملف {os.path.basename(path)}"
    L = report(events, orb, meta)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
