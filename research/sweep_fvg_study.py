#!/usr/bin/env python3
"""اختبار استراتيجية «سحب السيولة تحت افتتاح اليوم + كسر الهيكل + FVG» على SPY (قراءة فقط، بطلب المستخدم 26/10/2026؛ H-SWEEP مسجّلة في CLAUDE.md قبل التشغيل).

المصدر: فيديو «بصمة سهم». الاتجاه صاعد → لا شراء إلا بعد نزول السعر تحت افتتاح اليوم (سحب سيولة)، ثم كسر هيكل وفجوة FVG، والدخول عند عودة السعر للفجوة، الهدف خط الإغلاق، الوقف تحت آخر قاع.
نسختان:
  أ) exec — قابلة للتنفيذ: الاتجاه = اتجاه شمعة أمس اليومية، الهدف 1.5 ضعف المخاطرة (والثانوي الاحتفاظ للإغلاق).
  ب) oracle — «الفيديو بأثر رجعي» (مرجع غير قابل للتنفيذ): الاتجاه = لون شمعة اليوم نفسها، والهدف = إغلاق اليوم؛ تقيس كم من الربح مصدره معرفة النهاية.
المرجع الوهمي: الإجراء نفسه بافتتاح مُزاح ±0.35%. القياس بوحدات المخاطرة R بعد كلفة 0.002% لكل جهة. نصف التاريخ الأول استكشاف والثاني (بعد 2022-11-23) هو الحكم.
**الفرضية (على exec فقط):** متوسط R الحقيقي (هدف 1.5R) ناقص الوهمي > 0 بفاصل 95% بإعادة معاينة الأيام، بعد 150 صفقة حقيقية في النصف الثاني.
تشغيل: python research/sweep_fvg_study.py [مسار الملف]   المخرج: research/sweep_fvg_study_results.md
"""
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import levels_study as ls  # noqa: E402

OUT = os.path.join(HERE, "sweep_fvg_study_results.md")
SPLIT = "2022-11-23"
SWEEP_LAST_BAR = 60        # آخر شمعة يجوز أن تسحب السيولة (~14:30)
STEP_WINDOW = 12           # مهلة الاستعادة بعد السحب ومهلة العودة للفجوة
ENTRY_LAST_BAR = 69        # لا دخول بعد ~15:15
RISK_CAP = 0.01
R_TARGET = 1.5
COST = 0.00002
SHIFT = 0.0035
MIN_REAL = 150


# ============================ دوال نقية ============================
def bull_setup(o, h, l, c, level, confirm=1, start_above=False):
    """إعداد شراء: سحب تحت level، استعادة وكسر هيكل مع FVG، ثم دخول عند الحد العلوي للفجوة. dict أو None.
    confirm>1: يلزم أن تغلق confirm شمعة متتالية (من شمعة الكسر) فوق level قبل الدخول. start_above: يشترط ألا يبدأ اليوم تحت level (خط من يوم سابق)."""
    n = len(c)
    if start_above and o[0] < level:
        return None
    i = next((t for t in range(1, min(SWEEP_LAST_BAR + 1, n)) if l[t] < level), None)
    if i is None:
        return None
    pre_hi = float(np.max(h[max(0, i - 3):i]))
    j = next((t for t in range(i + 1, min(i + STEP_WINDOW + 1, n))
              if t >= 2 and c[t] > level and c[t] > pre_hi and l[t] > h[t - 2]
              and all(t + q < n and c[t + q] > level for q in range(1, confirm))), None)
    if j is None:
        return None
    gap_top, gap_bot = float(l[j]), float(h[j - 2])
    stop = float(np.min(l[i:j + 1]))
    for k in range(j + confirm, min(j + confirm + STEP_WINDOW, ENTRY_LAST_BAR + 1, n)):
        if c[k] < gap_bot:
            return None
        if l[k] <= gap_top:
            entry = min(float(o[k]), gap_top)
            risk = entry - stop
            if risk <= 0 or risk / abs(entry) > RISK_CAP:          # abs: جهة البيع تعمل بأسعار مَنفيّة
                return None
            return {"k": k, "entry": entry, "stop": stop, "risk": risk}
    return None


def simulate(h, l, c, setup, target=None, hold_to_close=False):
    """R بعد الكلفة. الوقف قبل الهدف في الشمعة نفسها؛ شمعة الدخول لا يُحسب هدفها. target سعر مطلق (أو None للاحتفاظ)."""
    k, entry, stop, risk = setup["k"], setup["entry"], setup["stop"], setup["risk"]
    cost = 2 * COST * abs(entry) / risk
    if l[k] <= stop:
        return -1.0 - cost
    for t in range(k + 1, len(c)):
        if l[t] <= stop:
            return -1.0 - cost
        if not hold_to_close and target is not None and h[t] >= target:
            return (target - entry) / risk - cost
    return (float(c[-1]) - entry) / risk - cost


def day_trade(o, h, l, c, level, bias, mode="t15", target_price=None, confirm=1, start_above=False):
    """نتيجة صفقة اليوم (R) أو None. bias +1 شراء / −1 بيع (يُعكس بالنفي). mode: t15 (1.5R) | close (احتفاظ) | line (هدف سعر target_price)."""
    if bias == 0:
        return None
    if bias < 0:
        o, h, l, c, level = -o, -l, -h, -c, -level
        target_price = None if target_price is None else -target_price
    setup = bull_setup(o, h, l, c, level, confirm, start_above)
    if setup is None:
        return None
    if mode == "t15":
        return simulate(h, l, c, setup, setup["entry"] + R_TARGET * setup["risk"])
    if mode == "close":
        return simulate(h, l, c, setup, hold_to_close=True)
    if target_price is None or target_price <= setup["entry"]:
        return None
    return simulate(h, l, c, setup, target_price)


def day_events(day, o, h, l, c, prev):
    """أحداث اليوم: dict(day, version, kind, mode, R). prev = (افتتاح أمس، إغلاقه) أو None."""
    ev = []
    O = float(o[0])
    if prev is not None:
        bias = int(np.sign(prev[1] - prev[0]))
        for kind, levels in (("real", [O]), ("placebo", [O * (1 + SHIFT), O * (1 - SHIFT)])):
            for lv in levels:
                for mode in ("t15", "close"):
                    r = day_trade(o, h, l, c, lv, bias, mode)
                    if r is not None:
                        ev.append({"day": day, "version": "exec", "kind": kind, "mode": mode, "R": r})
    today_bias = int(np.sign(c[-1] - o[0]))                    # أثر رجعي: غير معروف وقت الدخول
    r = day_trade(o, h, l, c, O, today_bias, "line", target_price=float(c[-1]))
    if r is not None:
        ev.append({"day": day, "version": "oracle", "kind": "real", "mode": "line", "R": r})
    return ev


def day_events_prev(day, o, h, l, c, prev):
    """H-PREV (تفسير المستخدم): الخطان من شمعة أمس — الافتتاح مستوى السحب والإغلاق هدف. prev = (افتتاح أمس، إغلاق أمس) أو None."""
    if prev is None:
        return []
    po, pc = float(prev[0]), float(prev[1])
    bias = int(np.sign(pc - po))
    ev = []
    for kind, levels in (("real", [po]), ("placebo", [po * (1 + SHIFT), po * (1 - SHIFT)])):
        for lv in levels:
            r = day_trade(o, h, l, c, lv, bias, "line", target_price=pc, confirm=2, start_above=True)
            if r is not None:
                ev.append({"day": day, "version": "prev", "kind": kind, "mode": "line", "R": r})
    return ev


def run(d):
    events, prev = [], None
    for day, g in d.groupby(d["t"].dt.date):
        g, half = ls.trim_half_day(g)
        o, h, l, c = (g[k].to_numpy(float) for k in ("o", "h", "l", "c"))
        if len(c) < 30:
            continue
        if not half and len(c) >= 78:
            events += day_events(pd.Timestamp(day), o, h, l, c, prev)
            events += day_events_prev(pd.Timestamp(day), o, h, l, c, prev)
        prev = (float(o[0]), float(c[-1]))
    return pd.DataFrame(events, columns=["day", "version", "kind", "mode", "R"])


def boot_diff(real, plac, all_days, n=3000, seed=47):
    """فرق متوسط R (حقيقي − وهمي) وفاصله بإعادة معاينة الأيام (الأيام بلا صفقات تدخل كعناقيد)."""
    days = list(all_days)
    if len(days) < 20 or len(real) == 0 or len(plac) == 0:
        return None
    ix = {d: i for i, d in enumerate(days)}
    sr, nr, sp, np_ = (np.zeros(len(days)) for _ in range(4))
    for d, v in zip(real["day"], real["R"]):
        sr[ix[d]] += v
        nr[ix[d]] += 1
    for d, v in zip(plac["day"], plac["R"]):
        sp[ix[d]] += v
        np_[ix[d]] += 1
    idx = np.random.default_rng(seed).integers(0, len(days), (n, len(days)))
    with np.errstate(invalid="ignore", divide="ignore"):
        m = sr[idx].sum(1) / nr[idx].sum(1) - sp[idx].sum(1) / np_[idx].sum(1)
    m = m[np.isfinite(m)]
    obs = float(real["R"].mean() - plac["R"].mean())
    return obs, (float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5)))


def boot_mean(df, all_days, n=3000, seed=53):
    if len(df) < 20:
        return None
    days = list(all_days)
    ix = {d: i for i, d in enumerate(days)}
    s, c = np.zeros(len(days)), np.zeros(len(days))
    for d, v in zip(df["day"], df["R"]):
        s[ix[d]] += v
        c[ix[d]] += 1
    idx = np.random.default_rng(seed).integers(0, len(days), (n, len(days)))
    with np.errstate(invalid="ignore", divide="ignore"):
        m = s[idx].sum(1) / c[idx].sum(1)
    m = m[np.isfinite(m)]
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def verdict(ev, all_days_second):
    sel = ev[(ev["version"] == "exec") & (ev["mode"] == "t15")]
    real, plac = sel[sel["kind"] == "real"], sel[sel["kind"] == "placebo"]
    if len(real) < MIN_REAL:
        return f"H-SWEEP: لا حكم بعد ({len(real)} من {MIN_REAL} صفقة حقيقية في النصف الثاني)"
    bd = boot_diff(real, plac, all_days_second)
    if bd is None:
        return "H-SWEEP: لا حكم بعد (أيام قليلة)"
    obs, (lo, hi) = bd
    desc = f"الفرق {obs:+.3f}R بفاصل {lo:+.3f}…{hi:+.3f}"
    return "H-SWEEP: " + ("تحققت (" if lo > 0 else "عكسية (" if hi < 0 else "لم تتحقق (") + desc + (")" if lo > 0 or hi < 0 else " يشمل الصفر)")


def fmt_row(label, df, days):
    if len(df) == 0:
        return f"| {label} | 0 | - | - | - |"
    ci = boot_mean(df, days)
    return f"| {label} | {len(df)} | {100 * (df['R'] > 0).mean():.0f}% | {df['R'].mean():+.3f}R | " + ("-" if ci is None else f"{ci[0]:+.3f}…{ci[1]:+.3f}") + " |"


def report(ev, all_days, meta):
    L = ["# سحب السيولة تحت افتتاح اليوم + كسر الهيكل + FVG على SPY (نتائج آلية)", "", f"- {meta}",
         "- قراءة فقط. R على المؤشر بعد كلفة 0.002% لكل جهة. exec = قابلة للتنفيذ (اتجاه شمعة أمس)؛ oracle = «الفيديو بأثر رجعي» (يعرف لون اليوم وإغلاقه): **مرجع غير قابل للتنفيذ**.",
         "- **الفرضية المسجّلة مسبقاً H-SWEEP (على exec فقط):** الحقيقي − الوهمي > 0 بفاصل 95% بعد 150 صفقة في النصف الثاني.", ""]
    if len(ev) == 0:
        return L + ["لا صفقات."]
    first, second = ev[ev["day"] < SPLIT], ev[ev["day"] >= SPLIT]
    days1 = [d for d in all_days if d < pd.Timestamp(SPLIT)]
    days2 = [d for d in all_days if d >= pd.Timestamp(SPLIT)]
    for label, part, days in (("النصف الأول (استكشاف)", first, days1), ("النصف الثاني (الحكم)", second, days2)):
        L += [f"## {label}", "", "| الصنف | صفقات | رابحة | متوسط R | فاصل 95% |", "|---|---|---|---|---|"]
        for ver, kind, mode, name in (("exec", "real", "t15", "exec حقيقي، هدف 1.5R (الأساسي)"), ("exec", "placebo", "t15", "exec وهمي، هدف 1.5R"),
                                       ("exec", "real", "close", "exec حقيقي، احتفاظ للإغلاق"), ("exec", "placebo", "close", "exec وهمي، احتفاظ للإغلاق"),
                                       ("prev", "real", "line", "prev (خطا أمس، هدف إغلاق أمس) حقيقي — H-PREV"), ("prev", "placebo", "line", "prev وهمي"),
                                       ("oracle", "real", "line", "oracle «الفيديو بأثر رجعي» (غير قابل للتنفيذ)")):
            sel = part[(part["version"] == ver) & (part["kind"] == kind) & (part["mode"] == mode)]
            L.append(fmt_row(name, sel, days))
        L.append("")
    L += ["⚖️ **" + verdict(second, days2) + "**"]
    return L


def main(argv):
    path = argv[0] if argv else ls.DATA
    if not os.path.exists(path):
        print("لا ملف بيانات:", path)
        return 0
    d = ls.load(path)
    ev = run(d)
    all_days = sorted({pd.Timestamp(x) for x in d["t"].dt.date.unique()})
    meta = f"{len(all_days)} يوماً من {all_days[0]:%Y-%m-%d} إلى {all_days[-1]:%Y-%m-%d}، {len(ev)} حدثاً"
    L = report(ev, all_days, meta)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
