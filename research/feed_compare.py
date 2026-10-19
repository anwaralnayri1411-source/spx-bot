#!/usr/bin/env python3
"""مقارنة مصدر البيانات: Alpaca (indicative، لحظي) مقابل ياهو (متأخر ~15 دقيقة). تسجيل فقط؛ لا يلمس البوت.

الفكرة: ياهو يعرض أسعار العقود متأخرة ~15 دقيقة، فسعر Alpaca الآن يجب أن يطابق ما سيعرضه ياهو بعد ~15 دقيقة
إذا كانت أسعار Alpaca صحيحة. كل تشغيل (كل 5 دقائق أثناء الجلسة):
  1) يسجّل من Alpaca عيّنة عقود انتهاء اليوم لـ SPXW وSPY: قريبة من السعر، وبعيدة، و«شبيهة بعقد البوت» (طلب ≈ 2$).
  2) يسجّل من ياهو نفس العقود التي سجّلها Alpaca خلال آخر 40 دقيقة.
التقرير يطابق كل سعر Alpaca عند الوقت t مع ياهو عند t + التأخير (0..30 دقيقة) ويختار التأخير الأقل خطأً،
ويعرض نسبة التطابق ومتوسط الفرق وعدد الأعطال.

الملفات (جديدة): research/feed_compare_obs.csv، research/feed_compare_results.md
يحتاج: ALPACA_KEY، ALPACA_SECRET (للتسجيل فقط؛ التقرير لا يحتاجها).   تشغيل: python research/feed_compare.py [--report]
"""
import csv
import os
import sys
import urllib.parse
from datetime import datetime, timedelta
from statistics import median

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import options_bot as ob          # noqa: E402  قراءة فقط (الوقت والعطل وتصفية SPXW)
import alpaca_live_check as alc   # noqa: E402

OBS = os.path.join(HERE, "feed_compare_obs.csv")
REPORT = os.path.join(HERE, "feed_compare_results.md")
COLS = ["run", "source", "sym", "contract", "type", "strike", "bid", "ask", "mid", "q_age_s", "spot", "tag"]
# رمز ← (رمز سلسلة Alpaca، مضاعف التقدير من SPY، نصف النافذة، إزاحات العيّنة بالنقاط، رمز ياهو)
SYMS = {"SPX": ("SPXW", alc.RATIO_GUESS, alc.WIDE, (0, 10, 25, 45), "^SPX"),
        "SPY": ("SPY", 1.0, alc.WIDE / 10, (0, 1, 3, 5), "SPY")}
LAGS = range(0, 35, 5)
MATCH_ABS, MATCH_REL = 0.05, 0.05     # «متطابق» = الفرق ≤ 5 سنت أو ≤ 5%
LOOKBACK_MIN = 40


def _mid(b, a):
    return (b + a) / 2 if b and a and b > 0 and a > 0 else None


def append(rows):
    if not rows:
        return
    new = not os.path.exists(OBS)
    with open(OBS, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        w.writerows(rows)


def read_obs():
    return list(csv.DictReader(open(OBS, encoding="utf-8"))) if os.path.exists(OBS) else []


def alpaca_sample(sym, exp, spy_px, run):
    und, mult, wide, offs, _ = SYMS[sym]
    guess = spy_px * mult
    st, snaps = alc.chain(und, alc.FEEDS[1], exp, guess - wide, guess + wide)
    if st != 200:
        return [], f"alpaca {sym} {st}"
    rows = alc.rows_of(snaps)
    if not rows:
        return [], f"alpaca {sym} empty"
    S = alc.parity_spot(rows, guess) or guess
    pick = {}
    for typ in ("CALL", "PUT"):
        side = [r for r in rows if r["type"] == typ and r["mid"] is not None]
        if not side:
            continue
        for o in offs:                                  # خارج المال بمسافة o (0 = أقرب سترايك للسعر)
            target = S + o if typ == "CALL" else S - o
            r = min(side, key=lambda r: abs(r["strike"] - target))
            pick[r["sym"]] = (r, f"otm{o}")
        r = min(side, key=lambda r: abs((r["ask"] or 99) - 2.0))   # شبيه بعقد البوت (100–350$)
        pick.setdefault(r["sym"], (r, "botlike"))
    out = [{"run": run, "source": "alpaca", "sym": sym, "contract": r["sym"], "type": r["type"], "strike": r["strike"],
            "bid": r["bid"], "ask": r["ask"], "mid": round(r["mid"], 4),
            "q_age_s": None if r["q_age"] is None else round(r["q_age"]), "spot": round(S, 2), "tag": tag}
           for r, tag in pick.values()]
    return out, None


def yahoo_rows(sym, exp, wanted, run):
    import yfinance as yf
    t = yf.Ticker(SYMS[sym][4])
    ch = t.option_chain(exp)
    calls, puts = (ob.spxw_rows(ch.calls), ob.spxw_rows(ch.puts)) if sym == "SPX" else (ch.calls, ch.puts)
    out = []
    for typ, df in (("CALL", calls), ("PUT", puts)):
        for r in df.to_dict("records"):
            cs = str(r.get("contractSymbol"))
            if cs not in wanted:
                continue
            b, a = float(r.get("bid") or 0), float(r.get("ask") or 0)
            m = _mid(b, a)
            out.append({"run": run, "source": "yahoo", "sym": sym, "contract": cs, "type": typ,
                        "strike": float(r["strike"]), "bid": b, "ask": a, "mid": None if m is None else round(m, 4),
                        "q_age_s": "", "spot": "", "tag": "" if m is not None else "no_bid_ask"})
    return out


# ---- عقود الشركات: مراكز بوت الشركات المفتوحة + عقد على السعر لشركتين قياديتين (أقرب انتهاء جمعة) ----
LEADERS = ("AAPL", "NVDA")
STOCK_POS = os.path.join(ROOT, "stocks_positions.json")


def _occ(root, exp, side, strike):
    return f"{root}{exp.replace('-', '')[2:]}{'C' if side == 'CALL' else 'P'}{int(round(float(strike) * 1000)):08d}"


def stock_contracts(n):
    """{رمز OCC: الوسم} لعقود الشركات."""
    import json
    want = {}
    try:
        for p in json.load(open(STOCK_POS, encoding="utf-8")).get("positions", []):
            if p.get("status") == "open" and str(p.get("exp", "")) >= n.date().isoformat() and p.get("ticker"):
                want[_occ(p["ticker"], p["exp"], p["side"], p["strike"])] = "botpos"
    except Exception as e:
        print("تعذر قراءة مراكز الشركات:", e)
    fri = n.date() + timedelta(days=(4 - n.weekday()) % 7)
    for t in LEADERS:
        st, d = alc.get(f"{alc.DATA}/v2/stocks/{t}/trades/latest")
        px = (d.get("trade") or {}).get("p") if st == 200 and isinstance(d, dict) else None
        if not px:
            continue
        st, snaps = alc.chain(t, alc.FEEDS[1], fri.isoformat(), px * 0.97, px * 1.03)
        rows = alc.rows_of(snaps) if st == 200 else []
        for typ in ("CALL", "PUT"):
            side = [r for r in rows if r["type"] == typ and r["mid"] is not None]
            if side:
                want.setdefault(min(side, key=lambda r: abs(r["strike"] - px))["sym"], "lead_atm")
    return want


def alpaca_stocks(n, run_s):
    want = stock_contracts(n)
    out, syms = [], list(want)
    for i in range(0, len(syms), 50):
        q = urllib.parse.urlencode({"symbols": ",".join(syms[i:i + 50]), "feed": alc.FEEDS[1]})
        st, d = alc.get(f"{alc.DATA}/v1beta1/options/snapshots?{q}")
        if st != 200 or not isinstance(d, dict):
            return out, f"alpaca stocks {st}"
        for r in alc.rows_of(d.get("snapshots") or {}):
            if r["mid"] is None:
                continue
            out.append({"run": run_s, "source": "alpaca", "sym": r["root"], "contract": r["sym"], "type": r["type"],
                        "strike": r["strike"], "bid": r["bid"], "ask": r["ask"], "mid": round(r["mid"], 4),
                        "q_age_s": None if r["q_age"] is None else round(r["q_age"]), "spot": "",
                        "tag": want.get(r["sym"], "")})
    return out, None


def yahoo_stocks(wanted, run_s):
    """نفس عقود الشركات من ياهو، مجمّعة حسب (الشركة، الانتهاء)."""
    import yfinance as yf
    groups = {}
    for cs in wanted:
        info = alc.parse_occ(cs)
        if info:
            groups.setdefault((info["root"], info["exp"]), set()).add(cs)
    out = []
    for (root, exp), cs_set in groups.items():
        ch = yf.Ticker(root).option_chain(exp)
        for typ, df in (("CALL", ch.calls), ("PUT", ch.puts)):
            for r in df.to_dict("records"):
                cs = str(r.get("contractSymbol"))
                if cs in cs_set:
                    b, a = float(r.get("bid") or 0), float(r.get("ask") or 0)
                    m = _mid(b, a)
                    out.append({"run": run_s, "source": "yahoo", "sym": root, "contract": cs, "type": typ,
                                "strike": float(r["strike"]), "bid": b, "ask": a,
                                "mid": None if m is None else round(m, 4), "q_age_s": "", "spot": "",
                                "tag": "" if m is not None else "no_bid_ask"})
    return out


def run(n=None):
    n = n or ob.now_ny()
    if not ob.market_day(n) or not ((9, 35) <= (n.hour, n.minute) <= (16, 0)):
        print("خارج الجلسة.")
        return
    run_s = n.isoformat(timespec="minutes")
    exp = n.date().isoformat()
    rows, errs = [], []
    st, d = alc.get(f"{alc.DATA}/v2/stocks/SPY/trades/latest")
    px = (d.get("trade") or {}).get("p") if st == 200 and isinstance(d, dict) else None
    if px:
        for sym in SYMS:
            try:
                got, err = alpaca_sample(sym, exp, px, run_s)
                rows += got
                if err:
                    errs.append(err)
            except Exception as e:
                errs.append(f"alpaca {sym} {e}")
    else:
        errs.append(f"alpaca SPY {st}")
    try:
        got, err = alpaca_stocks(n, run_s)
        rows += got
        if err:
            errs.append(err)
    except Exception as e:
        errs.append(f"alpaca stocks {e}")
    since = (n - timedelta(minutes=LOOKBACK_MIN)).isoformat(timespec="minutes")
    hist = [r for r in read_obs() if r["source"] == "alpaca" and r["run"] >= since] + rows
    for sym in SYMS:
        wanted = {r["contract"] for r in hist if r["sym"] == sym}
        if not wanted:
            continue
        try:
            rows += yahoo_rows(sym, exp, wanted, run_s)
        except Exception as e:
            errs.append(f"yahoo {sym} {e}")
    stock_wanted = {r["contract"] for r in hist if r["sym"] not in SYMS}
    if stock_wanted:
        try:
            rows += yahoo_stocks(stock_wanted, run_s)
        except Exception as e:
            errs.append(f"yahoo stocks {e}")
    for e in errs:
        rows.append({"run": run_s, "source": "error", "sym": "", "contract": "", "type": "", "strike": "", "bid": "",
                     "ask": "", "mid": "", "q_age_s": "", "spot": "", "tag": str(e)[:120]})
    append(rows)
    print(f"{run_s}: Alpaca {sum(r['source'] == 'alpaca' for r in rows)} | ياهو {sum(r['source'] == 'yahoo' for r in rows)} | أخطاء {len(errs)}")


# ============================ التقرير ============================
def _t(s):
    return datetime.fromisoformat(s)


def match(obs, lag):
    """أزواج (سعر Alpaca عند t، سعر ياهو عند t+lag ± 3 دقائق) لنفس العقد."""
    yh = {}
    for r in obs:
        if r["source"] == "yahoo" and r["mid"]:
            yh.setdefault(r["contract"], []).append((_t(r["run"]), float(r["mid"])))
    pairs = []
    for r in obs:
        if r["source"] != "alpaca" or not r["mid"]:
            continue
        cand = yh.get(r["contract"])
        if not cand:
            continue
        tgt = _t(r["run"]) + timedelta(minutes=lag)
        best = min(cand, key=lambda x: abs((x[0] - tgt).total_seconds()))
        if abs((best[0] - tgt).total_seconds()) <= 180:
            pairs.append((r, float(r["mid"]), best[1]))
    return pairs


def stats(pairs):
    if not pairs:
        return None
    d = [abs(a - y) for _, a, y in pairs]
    rel = [abs(a - y) / max(y, 0.01) for _, a, y in pairs]
    ok = sum(1 for (_, a, y) in pairs if abs(a - y) <= MATCH_ABS or abs(a - y) / max(y, 0.01) <= MATCH_REL)
    return {"n": len(pairs), "med_abs": median(d), "med_rel": median(rel), "ok": ok / len(pairs)}


def bar_check(obs):
    """التحقق الأقوى (لا يتأثر بتأخر جدولة GitHub): سعر Alpaca عند t مقابل صفقات العقد الفعلية حول t
    من شموع ياهو التاريخية، ومقابل صفقات t−15 (لو كان Alpaca متأخراً لطابقها أفضل)."""
    import yfinance as yf
    al = [r for r in obs if r["source"] == "alpaca" and r["mid"]]
    bars = {}
    for c in sorted({r["contract"] for r in al}):
        try:
            h = yf.Ticker(c).history(period="5d", interval="5m")
            bars[c] = h.tz_convert(ob.NY) if len(h) else None
        except Exception:
            bars[c] = None

    def err(rows, shift):
        e = []
        for r in rows:
            h = bars.get(r["contract"])
            if h is None:
                continue
            t = _t(r["run"]) + timedelta(minutes=shift)
            w = h[(h.index >= t - timedelta(minutes=5)) & (h.index <= t)]      # الشمعة المنتهية عند t
            if len(w):
                f = float(w["Close"].iloc[-1])
                e.append(abs(float(r["mid"]) - f) / max(f, 0.01))
        return e
    L = ["", "## التحقق بالصفقات الفعلية (شموع ياهو التاريخية للعقد نفسه)", "",
         "| المجموعة | قراءات لها صفقات | وسيط الخطأ مقابل صفقات اللحظة نفسها | مقابل صفقات قبل 15 دقيقة | الحكم |",
         "|---|---|---|---|---|"]
    for label, f in (("SPX", lambda r: r["sym"] == "SPX"), ("SPY", lambda r: r["sym"] == "SPY"),
                     ("الشركات", lambda r: r["sym"] not in SYMS), ("عقود بوت الشركات", lambda r: r["tag"] == "botpos")):
        rows = [r for r in al if f(r)]
        e0, e15 = err(rows, 0), err(rows, -15)
        if not e0:
            L.append(f"| {label} | 0 | - | - | لا صفقات كافية |")
            continue
        m0, m15 = median(e0), (median(e15) if e15 else None)
        verdict = ("لحظي ✓" if m15 is not None and m0 < m15 * 0.7 else "غير واضح")
        L.append(f"| {label} | {len(e0)} | {100 * m0:.1f}% | {'-' if m15 is None else f'{100 * m15:.1f}%'} | {verdict} |")
    L.append("- العقود قليلة التداول (كثير من عقود الشركات) قد لا تكون لها صفقات كافية؛ عندها الحكم من جدول المطابقة أعلاه.")
    return L


def report(bars=False):
    obs = read_obs()
    days = sorted({r["run"][:10] for r in obs})
    runs = sorted({r["run"] for r in obs})
    al = [r for r in obs if r["source"] == "alpaca"]
    errs = [r for r in obs if r["source"] == "error"]
    ages = [float(r["q_age_s"]) for r in al if r["q_age_s"] not in ("", None)]
    L = ["# مقارنة مصدر البيانات: Alpaca (لحظي) مقابل ياهو (متأخر) — نتائج آلية", "",
         f"- الأيام: {', '.join(days) or '—'} | تشغيلات: {len(runs)} | قراءات Alpaca: {len(al)} | أخطاء: {len(errs)}",
         f"- عمر تسعيرات Alpaca: الوسيط {median(ages):.0f} ث، وأقدم من 120 ث في {sum(a > 120 for a in ages)} قراءة" if ages else
         "- لا قراءات Alpaca بعد.",
         f"- «متطابق» = الفرق في سعر الوسط ≤ {MATCH_ABS * 100:.0f} سنت أو ≤ {MATCH_REL * 100:.0f}%.", "",
         "## مطابقة سعر Alpaca الآن مع ياهو بعد «التأخير»", "",
         "| التأخير المفترض لياهو | أزواج | متطابق | وسيط الفرق | وسيط الفرق النسبي |", "|---|---|---|---|---|"]
    best = None
    for lag in LAGS:
        s = stats(match(obs, lag))
        if not s:
            L.append(f"| {lag} د | 0 | - | - | - |")
            continue
        L.append(f"| {lag} د | {s['n']} | {100 * s['ok']:.0f}% | {s['med_abs']:.3f}$ | {100 * s['med_rel']:.1f}% |")
        if s["n"] >= 10 and (best is None or s["med_rel"] < best[1]["med_rel"]):
            best = (lag, s)
    if best:
        lag, s = best
        L += ["", f"**أفضل تطابق عند تأخير {lag} دقيقة**: {100 * s['ok']:.0f}% متطابق، ووسيط الفرق {s['med_abs']:.3f}$."]
        L.append("")
        L.append("| المجموعة (عند أفضل تأخير) | أزواج | متطابق | وسيط الفرق النسبي |")
        L.append("|---|---|---|---|")
        P = match(obs, lag)
        for label, f in (("SPX", lambda r: r["sym"] == "SPX"), ("SPY", lambda r: r["sym"] == "SPY"),
                         ("الشركات (كلها)", lambda r: r["sym"] not in SYMS),
                         ("عقود بوت الشركات المفتوحة", lambda r: r["tag"] == "botpos"),
                         ("قريبة من السعر (otm0)", lambda r: r["tag"] == "otm0"),
                         ("بعيدة (otm25/otm45 أو otm3/otm5)", lambda r: r["tag"] in ("otm25", "otm45", "otm3", "otm5")),
                         ("شبيهة بعقد البوت (طلب ≈ 2$)", lambda r: r["tag"] == "botlike")):
            s2 = stats([p for p in P if f(p[0])])
            L.append(f"| {label} | {s2['n']} | {100 * s2['ok']:.0f}% | {100 * s2['med_rel']:.1f}% |" if s2 else f"| {label} | 0 | - | - |")
    if bars:
        L += bar_check(obs)
    L += ["", "## كيف تقرأ النتيجة", "",
          "- إذا كان أفضل تطابق عند 10–20 دقيقة وبنسبة تطابق عالية (≥ 80%)، فأسعار Alpaca اللحظية هي نفسها أسعار السوق "
          "التي يعرضها ياهو لاحقاً → المصدر صالح للمتابعة.",
          "- إذا كان التطابق ضعيفاً عند كل التأخيرات، فأسعار indicative لا تعكس السوق بدقة → لا نبدّل المصدر بها.",
          "- عقود SPX البعيدة الرخيصة أهم صف للبوت: انظر «شبيهة بعقد البوت».",
          "- الأخطاء الكثيرة أو التسعيرات القديمة تعني أن المصدر غير ثابت."]
    if errs:
        L += ["", "### آخر الأخطاء", ""] + [f"- {r['run'][11:16]}: {r['tag']}" for r in errs[-8:]]
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    if "--report" in sys.argv:
        _n = ob.now_ny()
        report(bars="--bars" in sys.argv or (_n.hour, _n.minute) >= (15, 45))   # التحقق بالصفقات ثقيل: آخر الجلسة فقط
    elif not alc.KEY or not alc.SEC:
        print("✗ المفاتيح غير موجودة (ALPACA_KEY / ALPACA_SECRET).")
        sys.exit(1)
    else:
        run()
