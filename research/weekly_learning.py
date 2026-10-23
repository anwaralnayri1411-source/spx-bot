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
OUT = os.path.join(HERE, "weekly_learning.md")
DOW = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة"]


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
            rows.append(f"{DOW[k]}: {g['rate'].mean() * 100:.0f}% ({len(g)} أيام)")
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
        L.append(f"• جمعة الانتهاء الشهري: {o:.0f}% ({int(d['opex_month'].sum())} أيام فقط).")
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


def build(today=None, data=None):
    """يرجع (نص markdown كامل، رسالة تيليجرام مختصرة)."""
    today = today or datetime.now().date()
    since = week_start(today)
    data = data or load_data()
    parts = [("1) الأسبوع بالأرقام", sec_week(data["z"], data["positions"], data["stocks_log"], since)),
             ("2) الأيام النادرة: لماذا تقفز بعض العقود الرخيصة؟", sec_tail(data["tail_days"], data["tail_events"])),
             ("3) الدعم والمقاومة (جدران العقود)", sec_walls(data["snaps"], data["outcomes"])),
             ("4) الأخبار وتأثيرها", sec_news(data["news_md"])),
             ("5) التجارب الظلية", sec_shadows(data["momentum"]))]
    md = [f"# ما تعلمناه — أسبوع {since.isoformat()}", "",
          "⚠️ هذا ملخص آلي من سجلات المشروع. العينات صغيرة، وما لم يثبت يُقال صراحة. تعليمي وليس توصية مالية.", ""]
    for title, lines in parts:
        md += [f"## {title}"] + lines + [""]
    tg = [f"📚 <b>ما تعلمناه هذا الأسبوع</b> ({since.strftime('%d/%m')})", ""]
    for title, lines in parts:
        tg += [f"<b>{title}</b>"] + [re.sub(r"\s+$", "", x) for x in lines[:5]] + [""]
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
            "snaps": _csv("gex_snapshots.csv"), "outcomes": _csv("gex_outcomes.csv"), "momentum": _csv("momentum_shadow_log.csv"),
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
