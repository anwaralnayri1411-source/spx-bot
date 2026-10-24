#!/usr/bin/env python3
"""ملخص «ما تعلمناه» الأسبوعي بلغة بسيطة (بطلب المستخدم 23/10/2026): يقرأ سجلات المشروع ويكتب ما ثبت وما لم يثبت.

لا يستدعي أي ذكاء اصطناعي ولا شبكة: أرقام من الملفات فقط، وكل قسم يذكر حجم العينة. الصراحة أولاً: لا ندّعي نمطاً قبل أن تكفي البيانات.
الأقسام: (1) الأسبوع بالأرقام (2) الأيام النادرة (قفزات العقود الرخيصة) (3) الدعم والمقاومة (جدران العقود) (4) الأخبار (5) التجارب الظلية.
المخرجات: research/weekly_learning.md (كامل)، ورسالة تيليجرام مختصرة إلى TELEGRAM_CHAT_ID وحده (لا النسخ الإضافية).
تشغيل: python research/weekly_learning.py [--no-send]
"""
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):      # blocked_shadow (جذر المستودع) وstocks_exit_study (research) للأقسام 7 و8
    if _p not in sys.path:
        sys.path.insert(0, _p)
OUT = os.path.join(HERE, "weekly_learning.md")
DOW = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة"]
# فرضية «سياق اليوم» (سُجّلت مسبقاً في CLAUDE.md بتاريخ 23/10/2026): حدود الأثلاث تُثبَّت من عينة الاستكشاف (حتى 22/10)،
# والحكم على الأيام الجديدة فقط (من 23/10): حتى لا نحكم على ما رأيناه عند صياغة الفرضية.
EXPLORE_END = "2026-10-22"
MIN_SIGNALS = 30
CTX_FEATURES = (("prange", "اتساع حركة أمس"), ("pvix", "VIX إغلاق أمس"))
CTX_KINDS = (("atm1", "عقد على السعر لغد قريب (الأساسي)"), ("otm0", "عقد البوت الرخيص 0DTE"))


def _csv(name, **kw):
    p = os.path.join(ROOT, name)
    try:
        return pd.read_csv(p, **kw) if os.path.exists(p) else pd.DataFrame()
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def cnt(n, one, few, many):
    """تمييز العدد بالعربية: 1 مفرد، 2 مثنى (نكتفي بالمفرد المثنّى بصيغة «few»)، 3–10 جمع قلة، غير ذلك مفرد منصوب."""
    n = int(n)
    return f"{n} {one if n == 1 else (few if 2 <= n <= 10 else many)}"


def week_start(today):
    return today - timedelta(days=today.weekday())      # الاثنين


# ============================ الأقسام (دوال نقية: DataFrame ← أسطر) ============================
def sec_week(z, positions, stocks_log, since):
    """z: zero_dte_log، positions: قائمة مراكز الشركات، stocks_log: stocks_log.csv، since: تاريخ بداية الأسبوع."""
    L = []
    s = since.isoformat()
    if len(z):
        w = z[z["date"] >= s]
        if len(w):
            wins = int((w["result"] == "ربح").sum())
            L.append(f"• بوت SPX: {cnt(len(w), 'توصية', 'توصيات', 'توصية')} هذا الأسبوع، {wins} منها رابحة لو احتفظت بالعقد للانتهاء.")
        else:
            L.append("• بوت SPX: لا توصيات هذا الأسبوع (الفلاتر لم تُعطِ إشارة، وهذا ليس عطلاً).")
        L.append(f"  (منذ البداية: {cnt(len(z), 'توصية', 'توصيات', 'توصية')} في {cnt(z['date'].nunique(), 'يوم', 'أيام', 'يوماً')}.)")
    closed = [p for p in positions if p.get("closed") and (p["closed"].get("time") or "")[:10] >= s]
    if closed:
        tp = sum(1 for p in closed if p["closed"]["kind"] == "tp")
        usd = sum(p["closed"]["pnl"] * p["entry"] * 100 for p in closed)
        L.append(f"• بوت الشركات: أُغلق {cnt(len(closed), 'عقد', 'عقود', 'عقداً')} هذا الأسبوع: {tp} هدف و{len(closed) - tp} وقف، الصافي ≈ {usd:+,.0f}$ (بقاعدة +50% / −40%).")
    else:
        L.append("• بوت الشركات: لم يُغلق عقد هذا الأسبوع.")
    allc = [p for p in positions if p.get("closed")]
    if allc:
        tp = sum(1 for p in allc if p["closed"]["kind"] == "tp")
        usd = sum(p["closed"]["pnl"] * p["entry"] * 100 for p in allc)
        L.append(f"  (منذ البداية: {cnt(len(allc), 'مغلق', 'مغلقة', 'مغلقاً')}، نسبة الهدف {tp / len(allc) * 100:.0f}% مقابل ~48% للتعادل، الصافي ≈ {usd:+,.0f}$.)")
    return L


def sec_tail(days, events):
    """قفزات العقود الرخيصة (≥ +100%) من tail_log: هل ترتبط بيوم الأسبوع أم بحركة السوق؟"""
    if len(days) < 5:
        return [f"لا بيانات كافية بعد ({len(days)} يوم)."]
    d = days.copy()
    d["rate"] = d["ge100"] / d["entries"].replace(0, np.nan)
    L = [f"بيانات {cnt(len(d), 'يوم', 'أيام', 'يوماً')} تداول (عقود خارج المال 0DTE بسعر 50–350$ عند الدخول بين 10:00 و14:00):"]
    L.append(f"• في المتوسط {d['rate'].mean() * 100:.0f}% من هذه المحاولات لامست +100% في لحظة ما، لكن عند آخر الجلسة كان متوسط العائد "
             f"{d['mean_close'].astype(float).mean() * 100:+.0f}% (أغلبها ينتهي بلا قيمة؛ القمة لحظة لا تُلتقط غالباً).")
    rows = []
    for k in range(5):
        g = d[d["dow"] == k]
        if len(g):
            rows.append(f"{DOW[k]}: {g['rate'].mean() * 100:.0f}% ({cnt(len(g), 'يوم', 'أيام', 'يوماً')})")
    L.append("• نسبة لمس +100% حسب يوم الأسبوع: " + " | ".join(rows) + ".")
    fri, oth = d[d["dow"] == 4]["rate"], d[d["dow"] != 4]["rate"]
    if len(fri) >= 3 and len(oth) >= 3:
        L.append(f"• الجمعة {fri.mean() * 100:.0f}% مقابل {oth.mean() * 100:.0f}% لباقي الأيام: "
                 + ("فرق ملحوظ" if abs(fri.mean() - oth.mean()) > 0.10 else "فرق صغير") + f" (عينة {len(fri)} جمعة).")
    if d["range_pct"].notna().sum() >= 8:
        c = d["rate"].rank().corr(d["range_pct"].rank())
        if pd.notna(c):
            L.append(f"• الارتباط بين اتساع حركة SPX اليومية ونسبة القفزات: {c:+.2f} "
                     + ("(قوي: القفزات تتبع الأيام المتحركة أكثر من يوم الأسبوع)" if c > 0.5 else "(ضعيف أو متوسط)") + ".")
    if d["opex_month"].sum() >= 2:
        o = d[d["opex_month"] == 1]["rate"].mean() * 100
        L.append(f"• جمعة الانتهاء الشهري: {o:.0f}% ({cnt(d['opex_month'].sum(), 'يوم', 'أيام', 'يوماً')} فقط).")
    big = d[d["range_pct"] >= d["range_pct"].quantile(0.75)]["rate"].mean() * 100
    calm = d[d["range_pct"] <= d["range_pct"].quantile(0.25)]["rate"].mean() * 100
    L.append(f"• أيام الحركة الواسعة (الربع الأعلى): {big:.0f}% مقابل {calm:.0f}% في أهدأ الأيام.")
    return L


def sec_walls(snaps, outcomes):
    """جدران العقود (أول لقطة 0DTE لـSPX كل يوم) مقابل أعلى/أدنى سعر اليوم: هل صمد الجدار؟"""
    if not len(snaps) or not len(outcomes):
        return ["لا لقطات كافية بعد."]
    s = snaps[(snaps["ticker"].isin(["^SPX", "SPX"])) & (snaps["dte"] == 0)].sort_values(["date", "time"]).groupby("date").first()
    o = outcomes[outcomes["ticker"].isin(["^SPX", "SPX"])].drop_duplicates("date", keep="last").set_index("date")
    j = s.join(o, how="inner", lsuffix="_s", rsuffix="_o")
    j = j[j["call_wall"].notna() & j["put_wall"].notna()]
    n = len(j)
    if n < 2:
        return [f"{cnt(n, 'يوم', 'أيام', 'يوماً')} فقط حتى الآن، لا يكفي للحكم."]
    res_hold = int((j["high"] < j["call_wall"]).sum())
    sup_hold = int((j["low"] > j["put_wall"]).sum())
    touch_c = int((j["high"] >= j["call_wall"] - 5).sum())
    touch_p = int((j["low"] <= j["put_wall"] + 5).sum())
    return [f"• بيانات {cnt(n, 'يوم', 'أيام', 'يوماً')} فقط (تتراكم يومياً):",
            f"• جدار الكول (المقاومة المحتملة) لم يُكسر في {res_hold} من {n}؛ لُمس (±5 نقاط) في {touch_c}.",
            f"• جدار البوت (الدعم المحتمل) لم يُكسر في {sup_hold} من {n}؛ لُمس في {touch_p}.",
            "• ⚠️ يوم لا يلمس السعر الجدار يبدو «صامداً» دون اختبار حقيقي؛ المهم الأيام التي لُمس فيها."]


def sec_news(md_text):
    """أول جدول من news_memory_results.md: أنواع الأخبار مع ≥5 حالات: كيف تحرك السهم بعدها؟"""
    rows = []
    for line in md_text.splitlines():
        if not line.startswith("|") or "---" in line or "النوع" in line:
            continue
        c = [x.strip() for x in line.strip("|").split("|")]
        if len(c) >= 5 and c[1].isdigit() and int(c[1]) >= 5:
            rows.append((c[0], int(c[1]), c[2], c[3]))
        elif len(c) < 5 and rows:
            break
    if not rows:
        return ["لا أنواع أخبار بـ5 حالات فأكثر بعد."]
    return ["• بعد أخبار الشركات (حركة السهم فوق السوق حتى الإغلاق، عيّنات صغيرة):"] + \
           [f"  - {k} ({cnt(n, 'حالة', 'حالات', 'حالة')}): صعد {up}، متوسط الحركة {mv}" for k, n, up, mv in rows[:6]]


def context_days(days):
    """سياق كل يوم معروف عند الافتتاح: إغلاق VIX أمس واتساع حركة أمس (بلا أي معلومة من اليوم نفسه)."""
    d = days.sort_values("date").reset_index(drop=True).copy()
    d["pvix"] = pd.to_numeric(d["vix_close"], errors="coerce").shift(1)
    d["prange"] = pd.to_numeric(d["range_pct"], errors="coerce").shift(1)
    return d


def ctx_cutoffs(d):
    """حدا الأثلاث لكل مؤشر، ثابتان من عينة الاستكشاف فقط."""
    e = d[d["date"] <= EXPLORE_END]
    return {f: e[f].dropna().quantile([1 / 3, 2 / 3]).tolist() for f, _ in CTX_FEATURES}


def pair_values(trades, kind, ex="t60"):
    """لكل إشارة: عائد اتجاه البوت وعائد المعاكس ونفس العقد، والفرق = قيمة الاتجاه."""
    t = trades[(trades["kind"] == kind) & (trades["exit"] == ex)].dropna(subset=["real"])
    idx = ["day", "run_m"]
    b = t[t["role"] == "bot"].set_index(idx)["real"]
    o = t[t["role"] == "opp"].set_index(idx)["real"]
    j = pd.concat({"bot": b, "opp": o}, axis=1).dropna().reset_index()
    j["value"] = j["bot"] - j["opp"]
    return j


def boot_ci(j, n=2000, seed=7):
    """فاصل 95% لمتوسط قيمة الاتجاه بإعادة معاينة الأيام (الصفقات في اليوم نفسه مترابطة)."""
    if j["day"].nunique() < 3:
        return None
    groups = [g["value"].values for _, g in j.groupby("day")]
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(n):
        pick = rng.integers(0, len(groups), len(groups))
        means.append(np.concatenate([groups[i] for i in pick]).mean())
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def ctx_stats(j, ctx, feature, cuts, bucket, part):
    """إحصاء فئة واحدة (منخفض/وسط/مرتفع) في جزء واحد (استكشاف أو جديد)."""
    lo, hi = cuts[feature]
    x = j.copy()
    x["c"] = x["day"].map(ctx[feature])
    x = x.dropna(subset=["c"])
    x = x[x["day"] <= EXPLORE_END] if part == "explore" else x[x["day"] > EXPLORE_END]
    x = x[x["c"] <= lo] if bucket == "low" else (x[x["c"] > hi] if bucket == "high" else x[(x["c"] > lo) & (x["c"] <= hi)])
    if x.empty:
        return None
    return {"n": len(x), "days": x["day"].nunique(), "bot": float(x["bot"].mean()), "opp": float(x["opp"].mean()),
            "value": float(x["value"].mean()), "ci": boot_ci(x)}


def _fmt_ci(ci):
    return "فاصل 95%: غير متاح" if ci is None else f"فاصل 95%: {ci[0] * 100:+.0f}…{ci[1] * 100:+.0f}%"


def sec_context(trades, days):
    """هل يتفوق اتجاه البوت على عكسه في الأيام المتحركة (سياق صباحي)؟ استكشاف مقابل بيانات جديدة."""
    if trades.empty or days.empty or "day" not in trades:
        return ["لا بيانات كافية بعد."]
    d = context_days(days)
    ctx = d.set_index("date")
    cuts = ctx_cutoffs(d)
    L = ["الفرضية (سُجّلت 23/10): اتجاه البوت يتفوق على عكسه عندما يكون سياق الصباح «مرتفعاً» (VIX أمس أو اتساع حركة أمس في الثلث الأعلى)، ولا يتفوق في الأيام الهادئة.",
         f"الحكم على الأيام الجديدة فقط (من 23/10)، بحدود أثلاث ثابتة من عينة الاستكشاف؛ ولا حكم قبل {MIN_SIGNALS} إشارة في الفئة المرتفعة وفاصل ثقة لا يشمل الصفر."]
    verdict = None
    for kind, kname in CTX_KINDS:
        j = pair_values(trades, kind)
        if j.empty:
            continue
        L.append(f"{kname}: (قيمة الاتجاه = عائد اتجاه البوت − عائد المعاكس، خروج بعد 60د، أسعار حقيقية)")
        for feat, flabel in CTX_FEATURES:
            for part, pname in (("explore", "استكشاف حتى 22/10"), ("new", "جديد من 23/10")):
                parts = []
                for bucket, bname in (("high", "مرتفع"), ("low", "منخفض")):
                    st = ctx_stats(j, ctx, feat, cuts, bucket, part)
                    if st is None:
                        parts.append(f"{bname}: لا صفقات بعد")
                        continue
                    parts.append(f"{bname}: {cnt(st['n'], 'إشارة', 'إشارات', 'إشارة')}/{cnt(st['days'], 'يوم', 'أيام', 'يوماً')}، "
                                 f"البوت {st['bot'] * 100:+.0f}% مقابل المعاكس {st['opp'] * 100:+.0f}% (الفرق {st['value'] * 100:+.0f}%، {_fmt_ci(st['ci'])})")
                    if kind == "atm1" and feat == "prange" and part == "new" and bucket == "high":
                        verdict = st
                L.append(f"  - {flabel} [{pname}]: " + " | ".join(parts))
    if verdict is None or verdict["n"] < MIN_SIGNALS:
        got = 0 if verdict is None else verdict["n"]
        L.append(f"⚖️ الحكم الأساسي (اتساع حركة أمس، عقد على السعر): لا حكم بعد: {got} من {MIN_SIGNALS} إشارة على الأيام الجديدة.")
    elif verdict["ci"] is not None and verdict["ci"][0] > 0:
        L.append("⚖️ الحكم الأساسي: تحققت الفرضية على أيام جديدة (الفاصل لا يشمل الصفر). الخطوة التالية: ظل يسجّل أثر تخفيض التوصيات في الأيام الهادئة، بموافقتك.")
    else:
        L.append("⚖️ الحكم الأساسي: لم تتحقق الفرضية على الأيام الجديدة؛ نتائج الاستكشاف كانت ضجيجاً أو تغيّر السوق.")
    L.append("⚠️ جرّبنا مؤشرين وعقدين وفئات متعددة: نتيجة لافتة واحدة قد تكون صدفة. ولا يغيّر هذا القسم أي توصية.")
    return L


def sec_shadows(mom):
    L = []
    if len(mom):
        m = mom[(mom["pnl"].astype(str) != "") & mom["pnl"].notna()].copy()
        m["pnl"] = pd.to_numeric(m["pnl"], errors="coerce")
        for defn, name in (("live", "لحظي"), ("delayed", "متأخر (ياهو)")):
            a, b = m[(m["defn"] == defn) & (m["role"] == "signal")], m[(m["defn"] == defn) & (m["role"] == "opposite")]
            if len(a) >= 1 and len(b) >= 1:
                L.append(f"• ظل الزخم ({name}): الإشارة {a['pnl'].mean() * 100:+.0f}% مقابل المعاكس {b['pnl'].mean() * 100:+.0f}% "
                         f"({cnt(len(a), 'صفقة', 'صفقات', 'صفقة')} في {cnt(a['date'].nunique(), 'يوم', 'أيام', 'يوماً')}).")
    return L or ["لا نتائج مكتملة بعد."]


def sec_blocked(log):
    """ظل الحجب: لكل سبب حجب، هل كان ما حُجب سيربح أم يخسر؟ (blocked_shadow_log.csv)"""
    if log is None or log.empty:
        return ["لا حالات مغلقة بعد. الظل يتابع العقود التي حجبتها الشروط حتى هدف/وقف/انتهاء (عقود الشركات أياماً)."]
    import blocked_shadow as bs
    d = log[~log["out"].isin(["no_data", ""]) & log["out_pnl"].notna()].copy()
    d["out_pnl"] = pd.to_numeric(d["out_pnl"], errors="coerce")
    d = d.dropna(subset=["out_pnl"])
    if d.empty:
        return ["لا حالات مغلقة بعد."]
    L = []
    for (proj, tag), g in sorted(d.groupby(["project", "tag"])):
        by_day = {k: v["out_pnl"].tolist() for k, v in g.assign(day=g["entry_time"].str[:10]).groupby("day")}
        name = " + ".join(bs.LABELS.get(x, x) for x in tag.split("+"))
        L.append(f"⚖️ {'SPX' if proj == 'spx' else 'الشركات'} | {name}: {cnt(len(g), 'حالة', 'حالات', 'حالة')} في "
                 f"{cnt(len(by_day), 'يوم', 'أيام', 'يوماً')}، متوسط ما كان سيحدث {g['out_pnl'].mean() * 100:+.0f}% — "
                 f"{bs.verdict(g['out_pnl'].tolist(), by_day)}")
    L.append(f"(الحكم يحتاج {bs.MIN_FOR_VERDICT} حالة لكل سبب. متوسط سالب يعني أن الحجب كان في صالحنا.)")
    return L


def sec_exit(exit_log, stocks_lines):
    """ظل عقد/خروج SPX وSPY (exit_shadow_log.csv) ودراسة وقف سهم الشركات (قراءة فقط)."""
    L = []
    if exit_log is not None and len(exit_log):
        e = exit_log.copy()
        for c in ("r5040_pnl", "t60_pnl"):
            e[c] = pd.to_numeric(e[c], errors="coerce")
        recs = e["pos_id"].nunique()
        parts = []
        for rule, name in (("r5040", "قاعدة البوت +50/−40"), ("t60", "خروج بعد 60د")):
            b, a = e[e["variant"] == "bot"][f"{rule}_pnl"].dropna(), e[e["variant"] == "atm1"][f"{rule}_pnl"].dropna()
            if len(b) and len(a):
                parts.append(f"{name}: عقد البوت {b.mean() * 100:+.0f}% مقابل ATM لغد {a.mean() * 100:+.0f}%")
        L.append(f"⚖️ ظل العقد والخروج (SPX/SPY، {cnt(recs, 'توصية', 'توصيات', 'توصية')} في {cnt(e['date'].nunique(), 'يوم', 'أيام', 'يوماً')}): "
                 + ("؛ ".join(parts) if parts else "لا نتائج مكتملة") + ". لا قرار قبل 20 توصية.")
    else:
        L.append("⚖️ ظل العقد والخروج (SPX/SPY): لا نتائج بعد.")
    L += stocks_lines
    return L


def stocks_study_lines(positions, track):
    """سطور دراسة وقف السهم للشركات من stocks_positions.json وstocks_track.csv (نفس دالة research/stocks_exit_study.py)."""
    try:
        import stocks_exit_study as sx
        d = sx.build(positions, track)
    except Exception:  # noqa: BLE001
        return []
    if d is None or d.empty:
        return []
    def m(x):
        return f"{x.dropna().mean() * 100:+.0f}%"
    long_, short_ = d[d["dte"] >= sx.LONG_DTE]["base"], d[d["dte"] < sx.LONG_DTE]["base"]
    return [f"⚖️ الشركات (إعادة تشغيل على {cnt(len(d), 'توصية', 'توصيات', 'توصية')} في {cnt(d['entry_day'].nunique(), 'يوم', 'أيام', 'يوماً')}): "
            f"قاعدة البوت {m(d['base'])}، وقف السهم 1.5% {m(d['s15'])}؛ حسب المدة: {sx.LONG_DTE} أيام فأكثر {m(long_)} "
            f"({len(long_)}) مقابل الأقصر {m(short_)} ({len(short_)}). عينة صغيرة جداً: للمراقبة لا للحكم."]


SPREAD_BUCKETS = ((0.0, 0.08, "≤8%"), (0.08, 0.15, "8–15%"), (0.15, 9.0, ">15%"))


def spread_lines(positions):
    """أثر فرق الشراء/البيع عند الدخول (يُسجَّل منذ 24/10 في entry_spr) على نتيجة التوصيات المغلقة (قراءة فقط)."""
    rows = [(float(p["entry_spr"]), float(p["closed"]["pnl"])) for p in positions or []
            if p.get("entry_spr") is not None and p.get("closed") and p["closed"].get("pnl") is not None]
    if not rows:
        return ["⚖️ فرق الشراء/البيع عند الدخول (الشركات): يُسجَّل منذ 24/10، ولا توصيات مغلقة به بعد."]
    parts = []
    for lo, hi, name in SPREAD_BUCKETS:
        v = [x for s, x in rows if lo <= s < hi]
        parts.append(f"{name}: {len(v)} " + (f"({sum(v) / len(v) * 100:+.0f}%)" if v else "(-)"))
    return [f"⚖️ فرق الشراء/البيع عند الدخول مقابل نتيجة الشركات المغلقة: " + "، ".join(parts) + ". لا حكم قبل 30 توصية لكل فئة."]


VERDICT_FILES = (("مقارنة درجة الأخبار (Claude مقابل الكلمات)", "research/news_score_compare_results.md"),
                  ("ظل قواعد المحترفين (عقد 0.50 مقابل عقد البوت)", "research/pro_rules_shadow_results.md"),
                  ("المستويات والارتداد (SPY)", "research/levels_study_results.md"),
                  ("الانجراف بعد الأرباح", "research/pead_study_results.md"),
                  ("الفجوة وآخر 30 دقيقة (SPY)", "research/intraday_signals_study_results.md"),
                  ("ارتداد SPY بعد الصدمة", "research/vix_shock_study_results.md"),
                  ("فجوة السهم والنفط", "research/stocks_daily_signals_results.md"),
                  ("ظل هيكل الصفقة (سبريد وكوندور)", "research/structure_shadow_results.md"),
                  ("ظل المرشحين (الزخم وأول 30 دقيقة)", "research/candidates_shadow_results.md"),
                  ("الزخم ونظام التضخم", "research/regime_study_results.md"),
                  ("شذوذ حجم الخيارات (التدفق)", "research/flow_shadow_results.md"))


def sec_verdicts(texts):
    """سطر الحكم (يبدأ بـ ⚖️) من تقرير كل فرضية مسجّلة مسبقاً. texts: {عنوان: نص التقرير}."""
    L = []
    for title, text in texts.items():
        v = [x.strip() for x in str(text).split("\n") if x.strip().startswith("⚖️")]
        L.append(f"⚖️ {title}: " + (" | ".join(x.replace("⚖️", "").replace("**", "").strip() for x in v) if v else "لا تقرير بعد"))
    return L or ["لا فرضيات مسجّلة بعد."]


def build(today=None, data=None):
    """يرجع (نص markdown كامل، رسالة تيليجرام مختصرة)."""
    today = today or datetime.now().date()
    since = week_start(today)
    data = data or load_data()
    parts = [("1) الأسبوع بالأرقام", sec_week(data["z"], data["positions"], data["stocks_log"], since)),
             ("2) الأيام النادرة: لماذا تقفز بعض العقود الرخيصة؟", sec_tail(data["tail_days"], data["tail_events"])),
             ("3) الدعم والمقاومة (جدران العقود)", sec_walls(data["snaps"], data["outcomes"])),
             ("4) الأخبار وتأثيرها", sec_news(data["news_md"])),
             ("5) التجارب الظلية", sec_shadows(data["momentum"])),
             ("6) سياق اليوم: هل يتفوق البوت في الأيام المتحركة؟", sec_context(data.get("trades", pd.DataFrame()), data["tail_days"])),
             ("7) الحجب: هل كان في صالحنا؟", sec_blocked(data.get("blocked"))),
             ("8) العقد والخروج: ظلال ودراسات", sec_exit(data.get("exit_log"), data.get("stocks_study", []))),
             ("9) الفرضيات المسجّلة مسبقاً: أين وصلنا؟", sec_verdicts(data.get("verdict_texts", {})))]
    md = [f"# ما تعلمناه — أسبوع {since.isoformat()}", "",
          "⚠️ هذا ملخص آلي من سجلات المشروع. العينات صغيرة، وما لم يثبت يُقال صراحة. تعليمي وليس توصية مالية.", ""]
    for title, lines in parts:
        md += [f"## {title}"] + lines + [""]
    tg = [f"📚 <b>ما تعلمناه هذا الأسبوع</b> ({since.strftime('%d/%m')})", ""]
    for title, lines in parts:
        shown = [x for x in lines if x.startswith("⚖️")] if title.startswith(("6)", "7)", "8)", "9)")) else lines[:5]     # الأقسام 6–8 في الرسالة: الحكم فقط
        if title.startswith(("7)", "8)", "9)")) and not shown:
            shown = lines[:1]
        tg += [f"<b>{title}</b>"] + [re.sub(r"\s+$", "", x) for x in shown] + [""]
    tg.append("الملف الكامل: research/weekly_learning.md في المستودع.\n⚠️ عينات صغيرة؛ تعليمي وليس توصية.")
    return "\n".join(md), "\n".join(tg)[:3900]


def load_data():
    try:
        positions = json.load(open(os.path.join(ROOT, "stocks_positions.json"), encoding="utf-8")).get("positions", [])
    except Exception:  # noqa: BLE001
        positions = []
    p = os.path.join(HERE, "news_memory_results.md")
    return {"z": _csv("zero_dte_log.csv"), "positions": positions, "stocks_log": _csv("stocks_log.csv"),
            "tail_days": _csv("tail_log_days.csv"), "tail_events": _csv("tail_log_events.csv"),
            "snaps": _csv("gex_snapshots.csv"), "outcomes": _csv("gex_outcomes.csv"), "momentum": _csv("momentum_shadow_log.csv"), "trades": _csv("research/real_price_check_trades.csv"),
            "blocked": _csv("blocked_shadow_log.csv"), "exit_log": _csv("exit_shadow_log.csv"),
            "stocks_study": stocks_study_lines(positions, _csv("stocks_track.csv")) + spread_lines(positions),
            "verdict_texts": {t: (open(os.path.join(ROOT, f), encoding="utf-8").read() if os.path.exists(os.path.join(ROOT, f)) else "") for t, f in VERDICT_FILES},
            "news_md": open(p, encoding="utf-8").read() if os.path.exists(p) else ""}


def send(text):
    """إلى TELEGRAM_CHAT_ID وحده. لا يطبع المفاتيح ولا نص الخطأ."""
    import requests
    tok, chat = os.getenv("TELEGRAM_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not tok or not chat:
        print("لا مفاتيح تيليجرام؛ لن يُرسل.")
        return False
    try:
        return requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                             data={"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
                             timeout=20).ok
    except Exception as e:  # noqa: BLE001
        print("فشل الإرسال:", type(e).__name__)
        return False


def main(argv):
    md, tg = build()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(md + "\n")
    print(md)
    if "--no-send" not in argv:
        print("أُرسل:" , send(tg))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
