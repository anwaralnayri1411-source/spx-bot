#!/usr/bin/env python3
"""H-EARNLVL: أين ترتد الأسهم حول إعلانات الأرباح؟ وهل أعلى/أدنى يوم الإعلان دعم ومقاومة لاحقاً؟
(مسجّلة مسبقاً في CLAUDE.md 3/11/2026 بطلب المستخدم). دراسة فقط، لا تغيّر أي بوت.

البيانات: شموع 5د الحقيقية في SPXBOT_DATA/bars_5m (ساعات السوق) وearnings_dates.csv.
أ) وصفي: استمرار/انعكاس الفجوة ≥3% وسد الفجوة خلال 10 أيام.
ب) وصفي: نقطة الانعكاس خلال D0..D+10 قرب أي مستوى معروف صباح D0، مقابل نوافذ عشوائية بلا أرباح.
ج) الفرضية E-LVL: أعلى/أدنى D0 يصمدان عند أول لمس (شموع الساعة، D+1..D+20) أكثر من أعلى/أدنى يوم عادي (ضابط).
تشغيل (الخادم): SPXBOT_DATA=/opt/spxbot/data python research/earnings_levels_study.py   المخرج: research/earnings_levels_results.md
"""
import glob
import os
import sys
from datetime import time as dtime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
OUT = os.path.join(HERE, "earnings_levels_results.md")
ETFS = {"SPY", "QQQ", "DIA", "IWM"}
GAP_BIG = 0.03
NEAR = 0.005                  # ب: «قرب المستوى» ضمن 0.5%
FWD = 10                      # ب: نافذة الانعكاس D0..D+10
TOUCH_DAYS = 20               # ج: نافذة اللمس D+1..D+20
HOLD_DAYS = 5                 # ج: مهلة الحكم على الصمود
AWAY, BREAK = 0.015, 0.005    # ج: صمد = ابتعد 1.5% قبل إغلاق ساعة خلفه بـ0.5%
MIN_TOUCH = 150
N_BOOT = 5000
LEVEL_NAMES = {"prev_close": "إغلاق أمس (سد الفجوة)", "prev_hl": "أعلى/أدنى أمس", "hl20": "أعلى/أدنى 20 يوماً",
               "sma50": "متوسط 50 يوماً", "piv1h": "قمة/قاع على فريم الساعة (10 أيام)", "round": "رقم دائري",
               "orb": "نطاق أول 30د من اليوم"}


# ====================== البيانات ======================
def read_bars(path):
    from tournament import read_bars as rb
    return rb(path)


def daily(d5):
    g = d5.groupby(d5.index.date)
    t = pd.DataFrame({"open": g["Open"].first(), "high": g["High"].max(), "low": g["Low"].min(), "close": g["Close"].last()})
    first6 = d5[d5.index.time < dtime(10, 0)].groupby(d5[d5.index.time < dtime(10, 0)].index.date)
    t["orb_hi"], t["orb_lo"] = first6["High"].max(), first6["Low"].min()
    return t.dropna(subset=["open", "close"])


def hourly(d5):
    """شموع ساعة تبدأ 9:30 (آخرها 15:30–16:00)."""
    mins = (d5.index.hour * 60 + d5.index.minute - 570) // 60
    key = [d5.index.date, mins]
    g = d5.groupby(key)
    h = pd.DataFrame({"high": g["High"].max(), "low": g["Low"].min(), "close": g["Close"].last()})
    h.index = h.index.set_names(["day", "slot"])
    return h.reset_index()


def event_day(ts, days):
    """قاعدة pead_test نفسها: الإعلان بعد 9:30 → الجلسة التالية."""
    d = ts.date()
    i = np.searchsorted(days, d)
    if ts.time() >= dtime(9, 30) and i < len(days) and days[i] == d:
        i += 1
    return i if 0 < i < len(days) else None


def load_earnings():
    e = pd.read_csv(os.path.join(DATA, "earnings_dates.csv"))
    e["ts"] = pd.to_datetime(e["ts"], utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
    return {s: sorted(g["ts"]) for s, g in e.groupby("sym")}


# ====================== المستويات ======================
def round_step(p):
    return 1.0 if p < 50 else (5.0 if p < 300 else 10.0)


def pivots_1h(h, d_from, d_to):
    """قمم/قيعان فريم الساعة (أعلى/أدنى ±2 شمعة) في الأيام [d_from, d_to)."""
    w = h[(h["day"] >= d_from) & (h["day"] < d_to)].reset_index(drop=True)
    out = []
    for k in range(2, len(w) - 2):
        if w.at[k, "high"] == w["high"].iloc[k - 2:k + 3].max():
            out.append(float(w.at[k, "high"]))
        if w.at[k, "low"] == w["low"].iloc[k - 2:k + 3].min():
            out.append(float(w.at[k, "low"]))
    return out


def levels_at(t, h, i):
    """المستويات المعروفة صباح اليوم i (نطاق أول 30د يُعرف بعد 10:00)."""
    days = t.index
    prev = t.iloc[i - 1]
    win = t.iloc[max(0, i - 20):i]
    sma50 = float(t["close"].iloc[max(0, i - 50):i].mean())
    p = float(t["open"].iloc[i])
    st = round_step(p)
    return {"prev_close": [float(prev["close"])], "prev_hl": [float(prev["high"]), float(prev["low"])],
            "hl20": [float(win["high"].max()), float(win["low"].min())], "sma50": [sma50],
            "piv1h": pivots_1h(h, days[max(0, i - 10)], days[i]),
            "round": [np.floor(p / st) * st + k * st for k in range(-6, 7)],
            "orb": [float(t["orb_hi"].iloc[i]), float(t["orb_lo"].iloc[i])]}


def near_types(x, lv):
    return {k for k, ps in lv.items() if any(abs(x / q - 1) <= NEAR for q in ps if q and q > 0)}


# ====================== الأجزاء ======================
def part_ab(t, h, i, s):
    """(أ) عوائد بإشارة الفجوة وسد الفجوة؛ (ب) أنواع المستويات قرب نقطة الانعكاس."""
    o, pc = t["open"].iloc[i], t["close"].iloc[i - 1]
    w = t.iloc[i:i + FWD + 1]
    ext = float(w["high"].max()) if s > 0 else float(w["low"].min())
    filled = bool((w["low"] <= pc).any()) if s > 0 else bool((w["high"] >= pc).any())
    return {"r0": s * (t["close"].iloc[i] / o - 1), "r5": s * (t["close"].iloc[i + 5] / o - 1),
            "r10": s * (t["close"].iloc[i + 10] / o - 1), "filled": filled,
            "near": near_types(ext, levels_at(t, h, i))}


def first_touch(hw, L, role):
    """أول لمس للمستوى في شموع الساعة hw: role='res' من تحت، 'sup' من فوق. يرجع (موضع اللمس، صمد 1/0 أو None)."""
    hw = hw.reset_index(drop=True)
    for k in range(1, len(hw)):
        was = hw.at[k - 1, "close"] < L if role == "res" else hw.at[k - 1, "close"] > L
        hit = hw.at[k, "high"] >= L if role == "res" else hw.at[k, "low"] <= L
        if not (was and hit):
            continue
        for m in range(k, min(len(hw), k + 7 * HOLD_DAYS)):
            if role == "res":
                if hw.at[m, "low"] <= L * (1 - AWAY):
                    return k, 1
                if hw.at[m, "close"] >= L * (1 + BREAK):
                    return k, 0
            else:
                if hw.at[m, "high"] >= L * (1 + AWAY):
                    return k, 1
                if hw.at[m, "close"] <= L * (1 - BREAK):
                    return k, 0
        return k, None
    return None, None


def part_c(t, h, i, ref_i):
    """لمس أعلى/أدنى اليوم ref_i في نافذة D+1..D+20 بعد الحدث i. الدور يتحدد من إغلاق D0."""
    days = t.index
    hw = h[(h["day"] > days[i]) & (h["day"] <= days[min(len(days) - 1, i + TOUCH_DAYS)])]
    c0, out = float(t["close"].iloc[i]), []
    for L in (float(t["high"].iloc[ref_i]), float(t["low"].iloc[ref_i])):
        role = "res" if L > c0 else "sup"
        _, held = first_touch(hw, L, role)
        if held is not None:
            out.append(held)
    return out


def earn_days(t, ets):
    days = np.array(t.index)
    return [i for i in (event_day(ts, days) for ts in ets) if i is not None]


def study_symbol(sym, d5, ets, rng):
    t, h = daily(d5), hourly(d5)
    E = sorted(set(earn_days(t, ets)))
    near_e = lambda j, a, b: any(a <= j - e <= b for e in E)
    rows_ab, rows_base, rows_c = [], [], []
    for i in E:
        if i < 60 or i + TOUCH_DAYS + HOLD_DAYS >= len(t):
            continue
        gap = t["open"].iloc[i] / t["close"].iloc[i - 1] - 1
        day = str(t.index[i])
        s = 1 if gap > 0 else -1
        if abs(gap) >= GAP_BIG:
            r = part_ab(t, h, i, s)
            rows_ab.append({"sym": sym, "day": day, "gap": gap, **{k: v for k, v in r.items() if k != "near"},
                            "near": "|".join(sorted(r["near"]))})
            cand = [j for j in range(60, len(t) - FWD - 1) if not any(-15 <= j - e <= 5 for e in E)]
            for j in rng.choice(cand, size=min(3, len(cand)), replace=False) if cand else []:
                rows_base.append({"near": "|".join(sorted(part_ab(t, h, int(j), s)["near"]))})
        for held in part_c(t, h, i, i):
            rows_c.append({"sym": sym, "day": day, "kind": "earn", "held": held, "big": abs(gap) >= GAP_BIG})
        ctrl = [k for k in range(i - 60, i - 19) if k > 0 and not near_e(k, -5, 5)]
        for k in rng.choice(ctrl, size=min(2, len(ctrl)), replace=False) if ctrl else []:
            for held in part_c(t, h, i, int(k)):
                rows_c.append({"sym": sym, "day": day, "kind": "ctrl", "held": held, "big": abs(gap) >= GAP_BIG})
    return rows_ab, rows_base, rows_c


# ====================== الإحصاء والتقرير ======================
def boot_diff(c, n=N_BOOT, seed=0):
    """فرق نسبة الصمود (إعلان − ضابط) مع فاصل 95% بإعادة معاينة الأحداث (sym+day)."""
    c = c.assign(ev=c["sym"] + c["day"])
    evs = c["ev"].unique()
    by = {e: g for e, g in c.groupby("ev")}
    rng = np.random.default_rng(seed)
    rate = lambda d, k: d[d["kind"] == k]["held"].mean()
    d0 = rate(c, "earn") - rate(c, "ctrl")
    bs = []
    for _ in range(n):
        s = pd.concat([by[e] for e in rng.choice(evs, len(evs))])
        bs.append(rate(s, "earn") - rate(s, "ctrl"))
    return d0, tuple(np.nanpercentile(bs, [2.5, 97.5]))


def pct(x):
    return f"{x * 100:+.1f}%"


def report(ab, base, c, meta):
    L = ["# H-EARNLVL: مستويات الأسهم حول إعلانات الأرباح (نتائج آلية)", "", f"- {meta}",
         "- شموع 5د الحقيقية (ساعات السوق). D0 = أول جلسة بعد الإعلان. «كبير» = فجوة ≥ 3%.", ""]
    L += ["## أ) بعد فجوة الإعلان: استمرار أم انعكاس؟ (وصفي)", "",
          "| المجموعة | أحداث | يوم الإعلان (افتتاح→إغلاق) | +5 أيام | +10 أيام | سُدّت الفجوة خلال 10 أيام |", "|---|---|---|---|---|---|"]
    for name, g in (("كل الفجوات ≥3%", ab), ("فجوة صاعدة", ab[ab["gap"] > 0]), ("فجوة هابطة", ab[ab["gap"] < 0]),
                    ("فجوة ≥ 8%", ab[ab["gap"].abs() >= 0.08])):
        if len(g):
            L.append(f"| {name} | {len(g)} | {pct(g['r0'].mean())} | {pct(g['r5'].mean())} | {pct(g['r10'].mean())} | {g['filled'].mean() * 100:.0f}% |")
    L += ["", "العوائد بإشارة الفجوة: موجب = استمر في اتجاه الفجوة، سالب = انعكس.", "",
          "## ب) أين انعكس السهم؟ (أقصى سعر في اتجاه الفجوة خلال 10 أيام، ضمن 0.5% من المستوى؛ وصفي)", "",
          "| المستوى | بعد الإعلان | نوافذ عشوائية بلا أرباح |", "|---|---|---|"]
    has = lambda df, k: df["near"].str.split("|").apply(lambda x: k in x).mean() if len(df) else np.nan
    for k, name in LEVEL_NAMES.items():
        L.append(f"| {name} | {has(ab, k) * 100:.0f}% | {has(base, k) * 100:.0f}% |")
    anyl = lambda df: (df["near"] != "").mean()
    L.append(f"| **أي مستوى منها** | **{anyl(ab) * 100:.0f}%** | **{anyl(base) * 100:.0f}%** |")
    L += ["", "## ج) الفرضية E-LVL: هل أعلى/أدنى يوم الإعلان يصمد كدعم ومقاومة؟", ""]
    e, k = c[c["kind"] == "earn"], c[c["kind"] == "ctrl"]
    d0, (lo, hi) = boot_diff(c)
    days = sorted(c["day"].unique())
    mid = days[len(days) // 2]
    halves = []
    for part in (c[c["day"] < mid], c[c["day"] >= mid]):
        halves.append(part[part["kind"] == "earn"]["held"].mean() - part[part["kind"] == "ctrl"]["held"].mean())
    L += ["| النوع | لمسات حُسمت | صمد |", "|---|---|---|",
          f"| أعلى/أدنى يوم الإعلان | {len(e)} | {e['held'].mean() * 100:.0f}% |",
          f"| أعلى/أدنى يوم عادي (ضابط) | {len(k)} | {k['held'].mean() * 100:.0f}% |",
          f"| يوم إعلان بفجوة ≥3% فقط | {len(e[e['big']])} | {e[e['big']]['held'].mean() * 100:.0f}% |", "",
          f"- **الفرق (إعلان − ضابط):** {d0 * 100:+.1f} نقطة، فاصل 95% [{lo * 100:+.1f}، {hi * 100:+.1f}]",
          f"- النصف الأول: {halves[0] * 100:+.1f} نقطة | النصف الثاني: {halves[1] * 100:+.1f} نقطة"]
    ok = len(e) >= MIN_TOUCH and len(k) >= MIN_TOUCH and lo > 0 and min(halves) > 0
    L += ["", f"⚖️ **E-LVL: {'تحققت' if ok else 'لم تتحقق'}**", "",
          "## أكثر الأسهم ارتداداً عند مستوى بعد الإعلان (ب، 6 أحداث فأكثر)", "",
          "| السهم | أحداث كبيرة | انعكس قرب مستوى | سُدّت الفجوة |", "|---|---|---|---|"]
    per = ab.groupby("sym").agg(n=("gap", "size"), lvl=("near", lambda s: (s != "").mean()), fill=("filled", "mean"))
    for s_, r in per[per["n"] >= 6].sort_values("lvl", ascending=False).iterrows():
        L.append(f"| {s_} | {int(r['n'])} | {r['lvl'] * 100:.0f}% | {r['fill'] * 100:.0f}% |")
    L += ["", "تحفظ: تواريخ الأرباح من ياهو؛ شموع ما قبل 2024 من IEX أقل دقة؛ حركة السهم ليست ربح العقد؛ أنواع المستويات متداخلة."]
    return "\n".join(L) + "\n", ok


def main():
    rng = np.random.default_rng(7)
    paths = {os.path.basename(p).split(".")[0]: p for p in glob.glob(os.path.join(DATA, "bars_5m", "*.csv.gz"))}
    earn = load_earnings()
    AB, BASE, C, used = [], [], [], []
    for s in sorted(p for p in paths if p not in ETFS and p in earn):
        try:
            ab, base, c = study_symbol(s, read_bars(paths[s]), earn[s], rng)
        except Exception as ex:  # noqa: BLE001
            print("تخطي", s, type(ex).__name__, ex)
            continue
        AB += ab
        BASE += base
        C += c
        used.append(s)
        print(s, len(ab), len(c))
    ab, base, c = pd.DataFrame(AB), pd.DataFrame(BASE), pd.DataFrame(C)
    meta = f"{len(used)} شركة؛ {len(ab)} حدثاً بفجوة ≥3%؛ {len(c)} لمساً محسوماً (إعلان وضابط)"
    md, _ = report(ab, base, c, meta)
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main()
