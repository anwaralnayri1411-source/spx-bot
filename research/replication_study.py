#!/usr/bin/env python3
"""تكرار الفرضيات المستخرجة من SPY على سلاسل أخرى بلا أي تعديل (قراءة فقط؛ H-REPL مسجّلة في CLAUDE.md قبل وجود الكود والبيانات؛ بطلب المستخدم 26/10/2026).

المجموعتان: صناديق (QQQ وIWM وDIA) وشركات القائمة (33 سهماً). بيانات 5 دقائق من `bars_history.py`. كل أيام هذه السلاسل خارج العينة (القواعد اكتُشفت على SPY).
الإحصاءة (وحدة: نسبة مئوية من السعر): متوسط (إشارة × عائد زائد)، العائد الزائد = العائد − متوسط عائد السلسلة نفسها لنفس النافذة (إزالة الاتجاه العام):
  H-MOM   زخم الساعة السابقة × عائد 60 دقيقة بعد دخول 10:30/11:30/12:30/13:30
  H-F30   اتجاه 9:30→10:00 × عائد 60 دقيقة (نفس الدخولات)
  H-AGREE اتفاق الاثنين × عائد 60 دقيقة
  H-LAST30 اتجاه أول 30 دقيقة × عائد 15:30→16:00
  H-SWEEP (R الحقيقي − R الوهمي) لنسخة exec (هدف 1.5R) من sweep_fvg_study
فاصل 95% (والأشد 99.6% لـ12 اختباراً) بإعادة معاينة **الأيام** (سلاسل اليوم معاً). «تتكرر» إن استثنى فاصل **كل** مجموعة الصفر صعوداً، ولفرضيات 60 دقيقة والآخر 30 دقيقة أيضاً متوسط ≥ 3 نقاط أساس (حاجز كلفة عقد ATM).
تشغيل (الخادم): python research/replication_study.py   المخرج: research/replication_study_results.md
"""
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import intraday_signals_study as iss  # noqa: E402
import levels_study as ls  # noqa: E402
import option_buyer_cost as obc  # noqa: E402
import strategy_sim as sim  # noqa: E402
import sweep_fvg_study as sw  # noqa: E402

OUT = os.path.join(HERE, "replication_study_results.md")
BARS_DIR = os.path.join(os.getenv("SPXBOT_DATA", "/opt/spxbot/data"), "bars_5m")
ETFS = ("QQQ", "IWM", "DIA")
HURDLE = 0.0003            # 3 نقاط أساس
MIN_DAYS = 250
HYPS = ("MOM", "F30", "AGREE", "LAST30")
LEVELS = (95.0, 99.6)


# ============================ دوال نقية ============================
def sixty_minute_values(d):
    """DataFrame(day, hyp, val): إشارة × عائد 60 دقيقة زائد لـMOM وF30 وAGREE لسلسلة واحدة."""
    S = obc.samples(d).merge(sim.features(d), on=["day", "i"], how="inner")
    if len(S) == 0:
        return pd.DataFrame(columns=["day", "hyp", "val"])
    fwd = S["s60"] / S["s0"] - 1
    ex = (fwd - fwd.mean()).to_numpy()
    mom, f30 = np.sign(S["r_prev60"].to_numpy()), np.sign(S["r_first"].to_numpy())
    sigs = {"MOM": mom, "F30": f30, "AGREE": np.where(mom == f30, mom, 0.0)}
    out = []
    for name, s in sigs.items():
        m = s != 0
        out.append(pd.DataFrame({"day": S["day"].to_numpy()[m], "hyp": name, "val": (s * ex)[m]}))
    return pd.concat(out, ignore_index=True)


def last30_values(d):
    B = iss.build_days(d)
    if len(B) == 0:
        return pd.DataFrame(columns=["day", "hyp", "val"])
    ex = B["r_last30"] - B["r_last30"].mean()
    s = np.sign(B["r_first"].to_numpy())
    m = s != 0
    return pd.DataFrame({"day": B["day"].to_numpy()[m], "hyp": "LAST30", "val": (s * ex.to_numpy())[m]})


def split_sweep(ev):
    """من أحداث sw.run: ((حقيقي، وهمي) لنسخة exec بهدف 1.5R، (حقيقي، وهمي) لنسخة prev بهدف إغلاق أمس)."""
    out = []
    for ver, mode in (("exec", "t15"), ("prev", "line")):
        sel = ev[(ev["version"] == ver) & (ev["mode"] == mode)]
        out.append((sel[sel["kind"] == "real"][["day", "R"]], sel[sel["kind"] == "placebo"][["day", "R"]]))
    return out


def sweep_values(d):
    return split_sweep(sw.run(d))[0]


MIN_PREV_REAL = 300


def prev_verdict(by_group, pooled, n_real):
    """H-PREV: يتحقق إن استثنى فاصل 95% لكل مجموعة وللمجمّع الصفر صعوداً بعد MIN_PREV_REAL صفقة حقيقية."""
    if pooled is None or any(v is None for v in by_group.values()):
        return "لا حكم (بيانات ناقصة)"
    if n_real < MIN_PREV_REAL:
        return f"لا حكم بعد ({n_real} من {MIN_PREV_REAL} صفقة حقيقية)"
    ok = pooled[2][95.0][0] > 0 and all(v[2][95.0][0] > 0 for v in by_group.values())
    return "تتحقق" if ok else ("عكسية" if pooled[2][95.0][1] < 0 else "لا تتحقق")


def day_ci(df, all_days, levels=LEVELS, n=2000, seed=61, col="val"):
    """متوسط وفواصل بإعادة معاينة الأيام: (n، المتوسط، {مستوى: (lo, hi)}) أو None."""
    days = list(all_days)
    if len(df) < 30 or len(days) < MIN_DAYS // 5:
        return None
    ix = {d: i for i, d in enumerate(days)}
    s, c = np.zeros(len(days)), np.zeros(len(days))
    for dd, v in zip(df["day"], df[col]):
        s[ix[dd]] += v
        c[ix[dd]] += 1
    idx = np.random.default_rng(seed).integers(0, len(days), (n, len(days)))
    with np.errstate(invalid="ignore", divide="ignore"):
        m = s[idx].sum(1) / c[idx].sum(1)
    m = m[np.isfinite(m)]
    return len(df), float(df[col].mean()), {lv: (float(np.percentile(m, (100 - lv) / 2)), float(np.percentile(m, 100 - (100 - lv) / 2))) for lv in levels}


def diff_ci(real, plac, all_days, levels=LEVELS, n=2000, seed=67):
    days = list(all_days)
    if len(real) < 30 or len(plac) < 30:
        return None
    ix = {d: i for i, d in enumerate(days)}
    sr, nr, sp, np_ = (np.zeros(len(days)) for _ in range(4))
    for dd, v in zip(real["day"], real["R"]):
        sr[ix[dd]] += v
        nr[ix[dd]] += 1
    for dd, v in zip(plac["day"], plac["R"]):
        sp[ix[dd]] += v
        np_[ix[dd]] += 1
    idx = np.random.default_rng(seed).integers(0, len(days), (n, len(days)))
    with np.errstate(invalid="ignore", divide="ignore"):
        m = sr[idx].sum(1) / nr[idx].sum(1) - sp[idx].sum(1) / np_[idx].sum(1)
    m = m[np.isfinite(m)]
    return len(real), float(real["R"].mean() - plac["R"].mean()), {lv: (float(np.percentile(m, (100 - lv) / 2)), float(np.percentile(m, 100 - (100 - lv) / 2))) for lv in levels}


def per_series_t(df):
    """متوسط الصفقات (نفس وزن الإحصاءة المجمّعة) داخل سلسلة واحدة وإحصاءة t بخطأ معياري عنقودي باليوم (دخولات اليوم مترابطة): (متوسط، t) أو None."""
    g = df.groupby("day")["val"].agg(["sum", "count"])
    if len(g) < 60:
        return None
    n = g["count"].sum()
    m = g["sum"].sum() / n
    se = float(np.sqrt(((g["sum"] - m * g["count"]) ** 2).sum()) / n)
    if se == 0:
        return None
    return float(m), float(m / se)


def replicates(hyp, stats_by_group):
    """نعم إن استثنى فاصل 95% لكل مجموعة الصفر صعوداً، ولغير SWEEP أيضاً المتوسط ≥ الحاجز. stats_by_group: {مجموعة: نتيجة day_ci أو None}."""
    if not stats_by_group or any(v is None for v in stats_by_group.values()):
        return "لا حكم (بيانات ناقصة)"
    stat_ok = all(v[2][95.0][0] > 0 for v in stats_by_group.values())
    econ_ok = hyp == "SWEEP" or all(v[1] >= HURDLE for v in stats_by_group.values())
    if stat_ok and econ_ok:
        return "تتكرر"
    if stat_ok:
        return "إحصائياً نعم، اقتصادياً لا (أقل من حاجز الكلفة)"
    return "لا تتكرر"


def fmt(v, pct=True):
    if v is None:
        return "- | - | - | -"
    n, mean, ci = v
    f = (lambda x: f"{100 * x:+.3f}%") if pct else (lambda x: f"{x:+.3f}R")
    return f"{n} | {f(mean)} | {f(ci[95.0][0])}…{f(ci[95.0][1])} | {f(ci[99.6][0])}…{f(ci[99.6][1])}"


def analyze(series):
    """series: {رمز: DataFrame شموع}. يرجع (stats[(مجموعة، hyp)]، per[(مجموعة، hyp)]=[(رمز، متوسط، t)]، أيام كل مجموعة).
    SPY (إن وُجد) يدخل مجموعة الصناديق لفرضية PREV فقط؛ فرضيات H-REPL الأصلية تبقى على QQQ/IWM/DIA والشركات."""
    groups = {"صناديق": [s for s in series if s in ETFS or s == "SPY"], "شركات": [s for s in series if s not in ETFS and s != "SPY"]}
    stats, per, days_by_group = {}, {}, {}
    all_prev_real, all_prev_plac, all_days = [], [], set()
    for gname, syms in groups.items():
        if not syms:
            continue
        vals, reals, placs, preal, pplac = [], [], [], [], []
        gdays = set()
        for s in syms:
            d = series[s]
            gdays |= {pd.Timestamp(x) for x in d["t"].dt.date.unique()}
            ev = sw.run(d)
            (_, _), (pr, pp) = split_sweep(ev)
            preal.append(pr)
            pplac.append(pp)
            if s == "SPY":
                continue
            v = pd.concat([sixty_minute_values(d), last30_values(d)], ignore_index=True)
            vals.append(v.assign(sym=s))
            (r, p), _ = split_sweep(ev)
            reals.append(r.assign(sym=s))
            placs.append(p.assign(sym=s))
        days = sorted(gdays)
        days_by_group[gname] = days
        all_days |= gdays
        if vals:
            V = pd.concat(vals, ignore_index=True)
            for h in HYPS:
                sel = V[V["hyp"] == h]
                stats[(gname, h)] = day_ci(sel, days)
                per[(gname, h)] = [(s, *r) for s, g in sel.groupby("sym") if (r := per_series_t(g)) is not None]
            stats[(gname, "SWEEP")] = diff_ci(pd.concat(reals, ignore_index=True), pd.concat(placs, ignore_index=True), days)
        PR, PP = pd.concat(preal, ignore_index=True), pd.concat(pplac, ignore_index=True)
        stats[(gname, "PREV")] = diff_ci(PR, PP, days)
        all_prev_real.append(PR)
        all_prev_plac.append(PP)
    if all_prev_real:
        AR, AP = pd.concat(all_prev_real, ignore_index=True), pd.concat(all_prev_plac, ignore_index=True)
        stats[("الكل", "PREV")] = diff_ci(AR, AP, sorted(all_days))
        stats[("الكل", "PREV_n")] = len(AR)
    return stats, per, days_by_group


def report(stats, per, days_by_group, n_series, meta):
    L = ["# تكرار الفرضيات المستخرجة من SPY على صناديق وشركات (نتائج آلية)", "", f"- {meta}",
         "- قراءة فقط. قواعد SPY بلا تعديل؛ كل أيام هذه السلاسل خارج العينة. القيم المئوية % من السعر (نقطة أساس = 0.01%). فاصل بإعادة معاينة الأيام. الحاجز الاقتصادي 3 نقاط أساس لفرضيات الأفق القصير.",
         "- **الفرضية المسجّلة مسبقاً H-REPL:** تتكرر الفرضية إن استثنى فاصل 95% لكل مجموعة (صناديق وشركات) الصفر صعوداً، ولغير SWEEP أيضاً المتوسط ≥ 0.03%.", ""]
    for h in list(HYPS) + ["SWEEP"]:
        L += [f"## H-{h}", "", "| المجموعة | عدد | المتوسط | فاصل 95% | فاصل 99.6% |", "|---|---|---|---|---|"]
        for g in ("صناديق", "شركات"):
            if (g, h) in stats:
                L.append(f"| {g} | {fmt(stats[(g, h)], pct=(h != 'SWEEP'))} |")
        by_group = {g: stats[(g, h)] for g in ("صناديق", "شركات") if (g, h) in stats}
        L += ["", "⚖️ **H-" + h + ": " + replicates(h, by_group) + "**", ""]
        for g in ("صناديق", "شركات"):
            rows = per.get((g, h), [])
            if rows:
                pos = sum(1 for _, m, t in rows if m > 0)
                sig = sum(1 for _, m, t in rows if t > 1.96)
                L.append(f"- {g}: {pos} من {len(rows)} سلسلة بمتوسط موجب، {sig} منها بدلالة (t>1.96)؛ المتوقع بالصدفة نحو {0.025 * len(rows):.1f}.")
        L.append("")
    pg = {g: stats[(g, "PREV")] for g in ("صناديق", "شركات") if (g, "PREV") in stats}
    L += ["## H-PREV (تفسير المستخدم: خطا افتتاح وإغلاق أمس؛ الحقيقي − الوهمي بوحدة R، هدف إغلاق أمس)", "",
          "| المجموعة | صفقات حقيقية | الفرق | فاصل 95% | فاصل 99.6% |", "|---|---|---|---|---|"]
    for g in ("صناديق", "شركات", "الكل"):
        if (g, "PREV") in stats:
            L.append(f"| {g}{' (مع SPY)' if g == 'صناديق' else ''} | {fmt(stats[(g, 'PREV')], pct=False)} |")
    L += ["", "⚖️ **H-PREV: " + prev_verdict(pg, stats.get(("الكل", "PREV")), stats.get(("الكل", "PREV_n"), 0)) + "**", ""]
    return L


def load_series(base=BARS_DIR):
    out = {}
    if not os.path.isdir(base):
        return out
    for fn in sorted(os.listdir(base)):
        if fn.endswith(".csv.gz"):
            try:
                d = ls.load(os.path.join(base, fn))
            except Exception:  # noqa: BLE001
                continue
            if d["t"].dt.date.nunique() >= MIN_DAYS:
                out[fn[:-7]] = d
    if os.path.exists(ls.DATA):                       # SPY من ملفه (يدخل فرضية PREV فقط)
        try:
            out["SPY"] = ls.load(ls.DATA)
        except Exception:  # noqa: BLE001
            pass
    return out


def main(argv):
    series = load_series(argv[0] if argv else BARS_DIR)
    if not series:
        print("لا بيانات سلاسل (شغّل bars_history.py أولاً).")
        return 0
    stats, per, days = analyze(series)
    meta = f"{len(series)} سلسلة ({sum(1 for s in series if s in ETFS or s == 'SPY')} صناديق مع SPY، {sum(1 for s in series if s not in ETFS and s != 'SPY')} شركات)"
    L = report(stats, per, days, len(series), meta)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
