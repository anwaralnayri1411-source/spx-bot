#!/usr/bin/env python3
"""H-BHFLOW (مسجّل مسبقاً في CLAUDE.md 11/11/2026): هل يؤكد تدفق الخيارات أحداث الكسر والثبات؟ وصفي بلا حكم.

لكل يوم له لقطات تدفق (spx_flow_log.csv وSPXBOT_DATA/spx_flow_raw): أحداث breakhold.events (من 10:00) على شموع SPX 5د،
والتدفق في 30 دقيقة قبل اكتمال الحدث: flow_lean (كول − بوت صافي العلاوة)، flow_delta (صافي الدلتا)،
وتضخم الأسعار iv_tilt = تغيّر متوسط IV للكول بدلتا 0.3–0.6 − تغيّر البوت المماثل خلال 30 دقيقة.
النتيجة: حركة SPX في جهة الحدث بعد 30 و60 دقيقة. تشغيل: python research/bhflow_study.py
"""
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import breakhold as bh  # noqa: E402

DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
RAW = os.path.join(DATA, "spx_flow_raw")
FLOW = os.getenv("FLOW_LOG", os.path.join(ROOT, "spx_flow_log.csv"))
OUT = os.path.join(HERE, "bhflow_results.md")
NY = "America/New_York"


def hm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def iv_series(day):
    """{وقت: (IV الكول، IV البوت)} بمتوسط العقود بدلتا 0.3–0.6 في كل لقطة."""
    p = os.path.join(RAW, f"{day}.csv.gz")
    if not os.path.exists(p):
        return {}
    r = pd.read_csv(p)
    r = r[(r["delta"].abs() >= 0.3) & (r["delta"].abs() <= 0.6) & (r["iv"] > 0)]
    out = {}
    for t, g in r.groupby("time"):
        c, q = g[g["side"] == "C"]["iv"].mean(), g[g["side"] == "P"]["iv"].mean()
        out[t] = (c, q)
    return out


def tilt(ivs, t_end):
    ts = sorted(t for t in ivs if t <= t_end)
    if len(ts) < 2:
        return None
    t0 = [t for t in ts if t >= hm(int(t_end[:2]) * 60 + int(t_end[3:]) - 30)]
    if not t0:
        return None
    a, b = ivs[t0[0]], ivs[ts[-1]]
    if any(x != x for x in (*a, *b)):
        return None
    return (b[0] - a[0]) - (b[1] - a[1])


def main():
    import yfinance as yf
    fl = pd.read_csv(FLOW, dtype={"time": str})
    days = sorted(set(fl["date"]))
    spx = yf.Ticker("^GSPC").history(period="30d", interval="5m")
    spx.index = spx.index.tz_convert(NY)
    rows = []
    for day in days:
        g = spx[spx.index.strftime("%Y-%m-%d") == day]
        if len(g) < 30:
            continue
        prev = spx[spx.index.strftime("%Y-%m-%d") < day]
        pdays = sorted(set(prev.index.strftime("%Y-%m-%d")))
        pd_hl = None
        if pdays:
            pp = prev[prev.index.strftime("%Y-%m-%d") == pdays[-1]]
            pd_hl = (float(pp["High"].max()), float(pp["Low"].min()))
        closes = [float(x) for x in g["Close"].values]
        mins = [t.hour * 60 + t.minute for t in g.index]
        orb = (float(g["High"].iloc[:6].max()), float(g["Low"].iloc[:6].min()))
        lv = bh.levels(orb, pd_hl, min(closes), max(closes))
        f = fl[fl["date"] == day]
        ivs = iv_series(day)
        for e in bh.events(closes, lv, start=6):
            i = e["i"]
            end_m = mins[i] + 5
            if end_m < bh.EVENT_FROM_M or end_m > 15 * 60:
                continue
            te = hm(end_m)
            w = f[(f["time"] > hm(end_m - 30)) & (f["time"] <= te)]
            sg = 1 if bh.DIR[e["kind"]] == "CALL" else -1
            S = closes[i]
            fut = {k: (closes[min(i + k // 5, len(closes) - 1)] - S) * sg for k in (30, 60)}
            rows.append({"day": day, "time": te, "kind": e["kind"], "key": e["key"], "level": e["level"], "dir": bh.DIR[e["kind"]],
                         "lean": float(w["call_prem"].fillna(0).sum() - w["put_prem"].fillna(0).sum()) if len(w) else None,
                         "delta": float(w["net_delta"].fillna(0).sum()) if len(w) else None,
                         "tilt": tilt(ivs, te), "m30": fut[30], "m60": fut[60]})
    df = pd.DataFrame(rows)
    L = ["# H-BHFLOW: تدفق الخيارات عند أحداث الكسر والثبات (وصفي بلا حكم)", "",
         f"- {df['day'].nunique() if len(df) else 0} يوماً ({days[0] if days else ''} → {days[-1] if days else ''})، {len(df)} حدثاً من 10:00 حتى 15:00.",
         "- «يوافق» = إشارة المقياس في جهة الحدث (كول للصعود، بوت للهبوط). النتيجة: نقاط SPX في جهة الحدث.", "",
         "| المجموعة | أحداث | صدق بعد 30د | متوسط 30د | صدق بعد 60د | متوسط 60د |", "|---|---|---|---|---|---|"]

    def line(nm, x):
        if not len(x):
            return f"| {nm} | 0 | — | — | — | — |"
        return (f"| {nm} | {len(x)} | {(x['m30'] > 0).mean() * 100:.0f}% | {x['m30'].mean():+.1f} | "
                f"{(x['m60'] > 0).mean() * 100:.0f}% | {x['m60'].mean():+.1f} |")
    if len(df):
        sg = df["dir"].map({"CALL": 1, "PUT": -1})
        L.append(line("كل الأحداث", df))
        for k, nm in (("hold", "ثبات"), ("fail", "فشل")):
            L.append(line(nm, df[df["kind"].str.startswith(k)]))
        for col, nm in (("lean", "علاوة صافية"), ("delta", "صافي الدلتا"), ("tilt", "تضخم الأسعار (IV)")):
            x = df.dropna(subset=[col])
            s = sg.loc[x.index]
            L.append(line(f"{nm} يوافق", x[x[col] * s > 0]))
            L.append(line(f"{nm} يخالف", x[x[col] * s < 0]))
        both = df.dropna(subset=["lean", "tilt"])
        s = sg.loc[both.index]
        L.append(line("العلاوة والتضخم يوافقان معاً", both[(both["lean"] * s > 0) & (both["tilt"] * s > 0)]))
        L += ["", "## الأحداث", "", "| اليوم | الوقت | الحدث | المستوى | الجهة | علاوة صافية | دلتا | تضخم IV | 30د | 60د |", "|---|---|---|---|---|---|---|---|---|---|"]
        for r in df.itertuples():
            L.append(f"| {r.day} | {r.time} | {r.kind} | {r.level:,.0f} | {r.dir} | {'' if r.lean is None else f'{r.lean / 1e6:+.1f}م'} | "
                     f"{'' if r.delta is None else f'{r.delta:+,.0f}'} | {'' if r.tilt is None or r.tilt != r.tilt else f'{r.tilt * 100:+.2f}'} | {r.m30:+.1f} | {r.m60:+.1f} |")
    L += ["", "وصفي بلا حكم: نحو 8 أيام فقط؛ جهة التدفق تقدير بقاعدة آخر سعر مقابل المنتصف؛ التسجيل اليومي يكبّر العينة."]
    md = "\n".join(L) + "\n"
    open(OUT, "w", encoding="utf-8", newline="\n").write(md)
    print(md)


if __name__ == "__main__":
    main()
