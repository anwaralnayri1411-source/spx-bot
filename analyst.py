#!/usr/bin/env python3
"""المحلل الذكي (H-ANALYST، مسجّل مسبقاً في CLAUDE.md 31/10/2026 بطلب المستخدم).

يراقب ويدرس ويقترح فقط: لا يغيّر توصية ولا ظلاً ولا إعداداً، ولا يطلق تجارب. يرسل إلى TELEGRAM_CHAT_ID وحده.
  1) بطاقة لكل صفقة (analyst_cards.csv، تُبنى من السجلات كل تشغيل): السياق وقت التوصية (معلومة معروفة وقتها فقط)
     واتفاق الظلال، ونتيجة أول خروج بالقاعدة.
  2) يومياً بعد الإغلاق: قسم أرقام يكتبه الكود + «قراءة المحلل» يكتبها Claude من الأرقام فقط (حارس: أي رقم ليس في
     البيانات يُسقط القراءة)، + صحة التشغيل (مهام فشلت، تجمّد أسعار، فجوات بيانات، عقود متعاكسة).
  3) أسبوعياً (السبت): جدول وصفي لكل عامل وللتداخلات الثنائية، ثم حتى 3 فرضيات مكتوبة مسبقاً ومراجعة ملاحظات الأسبوع السابق.
المخرجات: analyst_cards.csv، research/analyst_journal.md، research/analyst_results.md، analyst_state.json، analyst_usage.csv.
تشغيل: python analyst.py [--weekly] [--no-send] [--force] [--day YYYY-MM-DD]
"""
import csv
import html
import itertools
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

import shadow_manager as sm

ROOT = os.path.dirname(os.path.abspath(__file__))
CARDS = os.path.join(ROOT, "analyst_cards.csv")
STATE = os.path.join(ROOT, "analyst_state.json")
USAGE = os.path.join(ROOT, "analyst_usage.csv")
JOURNAL = os.path.join(ROOT, "research", "analyst_journal.md")
RESULTS = os.path.join(ROOT, "research", "analyst_results.md")
LOGDIR = os.getenv("SPXBOT_LOGDIR", "/var/log/spxbot")
MODEL = os.getenv("ANALYST_MODEL", "claude-haiku-5-5")
WEEKLY_MODEL = os.getenv("ANALYST_WEEKLY_MODEL", "claude-sonnet-5-5")
DAILY_USD = float(os.getenv("ANALYST_DAILY_USD", "0.3"))
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0)}  # $/مليون، 06/10/2026
USAGE_COLS = ["time", "kind", "model", "input", "output", "usd", "status"]
SEND_AFTER = (16, 40)    # بعد مدير الظلال (16:25) وتقييم الأخبار
CELL_MIN = 5             # لا رقم لخانة أقل من 5 صفقات في الجداول
SMALL = 30               # كل شيء «عينة صغيرة» حتى 30 صفقة
FLAT = 0.0005            # حركة 30/60 دقيقة أقل من 0.05% = مسطحة
GAP = 0.003              # فجوة الافتتاح (حدود H-CONTEXT نفسها)
SIDE = {"CALL": 1, "PUT": -1}
FEATURES = (("tod", "وقت اليوم"), ("ctx_vix", "VIX"), ("ctx_range", "مدى أمس"), ("ctx_trend", "اتجاه أمس"), ("ctx_gap", "فجوة اليوم"),
            ("ctx_event", "يوم حدث"), ("r30_b", "حركة 30د"), ("r60_b", "حركة 60د"), ("rday_b", "حركة اليوم"), ("pos_b", "الموقع في مدى اليوم"),
            ("cand_mom", "زخم آخر ساعة"), ("cand_f30", "أول 30 دقيقة"), ("edge7", "إشارة قوية edge7"), ("mom_live", "الزخم الحي"),
            ("live_side", "الشموع اللحظية"), ("news", "الأخبار"),
            ("s_news", "أخبار الشركة"), ("s_flow", "تدفق الخيارات"), ("s_delta", "دلتا العقد"), ("s_dte", "أيام العقد"),
            ("s_earn", "أرباح داخل العقد"), ("s_exdiv", "توزيعات داخل العقد"))
CARD_COLS = ["project", "id", "day", "time", "sym", "side", "strike", "exp", "entry", "tod",
             "ctx_vix", "ctx_range", "ctx_trend", "ctx_gap", "ctx_event", "gap", "r30", "r60", "rday", "pos",
             "r30_b", "r60_b", "rday_b", "pos_b", "cand_mom", "cand_f30", "edge7", "mom_live", "live_side", "news_level", "news_kind", "news",
             "s_news", "s_flow", "s_delta", "s_dte", "s_earn", "s_exdiv",
             "exit_kind", "exit_ret", "exit_time", "mins", "peak_before", "after_peak", "last", "hold_ret"]


# ============================ أدوات ============================
def read(name):
    p = os.path.join(ROOT, name)
    try:
        return pd.read_csv(p, dtype=str, keep_default_na=False) if os.path.exists(p) else pd.DataFrame()
    except Exception as e:  # noqa: BLE001
        print("تعذر قراءة", name, type(e).__name__)
        return pd.DataFrame()


def num(x):
    try:
        v = float(x)
        return None if np.isnan(v) else v
    except (TypeError, ValueError):
        return None


def ts(s):
    return sm._ts(str(s))


def pct(x, d=0):
    return "-" if x is None else f"{x * 100:+.{d}f}%".replace("-", "−")


def ny_now():
    return sm.ny_now()


def side_rel(v, side):
    """اتجاه إشارة/حركة نسبةً إلى اتجاه الصفقة: مع/ضد/محايد."""
    v = num(v)
    if v is None:
        return ""
    s = v * SIDE.get(side, 0)
    return "مع" if s > 0 else "ضد" if s < 0 else "محايد"


def move_b(r, side):
    if r is None:
        return ""
    if abs(r) < FLAT:
        return "مسطح"
    return "مع الاتجاه" if r * SIDE.get(side, 0) > 0 else "ضد الاتجاه"


# ============================ شموع SPX ============================
def load_bars(days):
    """{يوم: DataFrame(t, Open, High, Low, Close)} من المخبأ، وما نقص من ياهو (الماضي لا يتأخر)."""
    out = {}
    cache = read(os.path.join("research", "spx_5m_cache.csv"))
    frames = []
    if len(cache):
        frames.append(cache)
    missing = [d for d in days if not len(cache) or not cache["time"].str.startswith(d).any()]
    if missing:
        try:
            import yfinance as yf
            h = yf.Ticker("^GSPC").history(period="10d", interval="5m")
            if len(h):
                h.index = h.index.tz_convert("America/New_York")
                frames.append(pd.DataFrame({"time": [str(i) for i in h.index], "Open": h["Open"].values, "High": h["High"].values,
                                            "Low": h["Low"].values, "Close": h["Close"].values}))
        except Exception as e:  # noqa: BLE001
            print("شموع ياهو:", type(e).__name__)
    if not frames:
        return out
    b = pd.concat(frames, ignore_index=True)
    b["t"] = [ts(x) for x in b["time"]]
    b = b.dropna(subset=["t"]).drop_duplicates("t").sort_values("t")
    for c in ("Open", "High", "Low", "Close"):
        b[c] = pd.to_numeric(b[c], errors="coerce")
    b["day"] = [t.date().isoformat() for t in b["t"]]
    for d, g in b.groupby("day"):
        out[d] = g.reset_index(drop=True)
    return out


def bar_features(bars, day, t):
    """فجوة الافتتاح، حركة 30 و60 دقيقة قبل t، حركة اليوم، موقع السعر في مدى اليوم — من الشموع المكتملة قبل t فقط."""
    g = bars.get(day)
    out = {"gap": None, "r30": None, "r60": None, "rday": None, "pos": None}
    if g is None or not len(g) or t is None:
        return out
    done = g[g["t"] + timedelta(minutes=5) <= t]
    if not len(done):
        return out
    last = float(done["Close"].iloc[-1])
    op = float(g["Open"].iloc[0])
    prev = [d for d in bars if d < day]
    if prev:
        pc = float(bars[max(prev)]["Close"].iloc[-1])
        out["gap"] = op / pc - 1
    if len(done) > 6:
        out["r30"] = last / float(done["Close"].iloc[-7]) - 1
    if len(done) > 12:
        out["r60"] = last / float(done["Close"].iloc[-13]) - 1
    out["rday"] = last / op - 1
    hi, lo = float(done["High"].max()), float(done["Low"].min())
    out["pos"] = (last - lo) / (hi - lo) if hi > lo else None
    return out


# ============================ سياق الظلال ============================
def shadow_context(day, t, side, logs):
    """اتفاق الظلال مع الصفقة وقت التوصية (آخر معلومة قبلها في اليوم نفسه). فارغ = الظل لم يكن يعمل ذلك اليوم."""
    out = {"cand_mom": "", "cand_f30": "", "edge7": "", "mom_live": "", "live_side": "", "news_level": "", "news_kind": "", "news": ""}
    hm = t.strftime("%H:%M")
    c = logs["cand"]
    if len(c) and (c["day"] == day).any():
        g = c[(c["day"] == day) & (c["slot"] <= hm)].sort_values("slot")
        if len(g):
            out["cand_mom"] = side_rel(g["mom"].iloc[-1], side) or "محايد"
            out["cand_f30"] = side_rel(g["f30"].iloc[-1], side) or "محايد"
        else:
            out["cand_mom"] = out["cand_f30"] = "قبل أول موعد"
    e = logs["edge7"]
    if len(e) and e["entry_time"].str.startswith(day).any():
        g = e[e["entry_time"].str.startswith(day) & e["role"].isin(["bot", "spy_bot"])]
        near = [r for r in g.itertuples() if (tt := ts(r.entry_time)) and abs((tt - t).total_seconds()) <= 600 and r.side == side]
        out["edge7"] = "نعم" if near else "لا"
    m = logs["mom"]
    if len(m) and (m["date"] == day).any():
        g = m[(m["date"] == day) & (m["defn"] == "live") & (m["role"] == "signal")]
        prior = [r for r in g.itertuples() if (tt := ts(r.sig_time)) and tt <= t]
        out["mom_live"] = ("مع" if prior[-1].side == side else "ضد") if prior else "لا إشارة"
    lb = logs["live_bars"]
    if len(lb) and lb["time"].str.startswith(day).any():
        near = [r for r in lb.itertuples() if (tt := ts(r.time)) and abs((tt - t).total_seconds()) <= 180]
        if near:
            sl = near[-1].side_live
            out["live_side"] = "مع" if sl == side else "لا إشارة" if not sl else "ضد"
    n = logs["news"]
    if len(n) and n["time"].str.startswith(day).any():
        near = [r for r in n.itertuples() if (tt := ts(r.time)) and abs((tt - t).total_seconds()) <= 180]
        if near:
            out["news_level"], out["news_kind"] = near[-1].level, near[-1].kind
        out["news"] = f"مستوى {out['news_level']}" if out["news_level"] else "هادئة"
    return out


def load_logs():
    return {"cand": read("candidates_shadow_log.csv"), "edge7": read("edge7_shadow_log.csv"), "mom": read("momentum_shadow_log.csv"),
            "live_bars": read("live_bars_log.csv"), "news": read("news_shadow_log.csv")}


# ============================ النتائج ============================
def path_outcome(rows, entry_t=None):
    """rows: [(وقت، حالة، ربح)] مرتبة. أول خروج بالقاعدة (tp/stopped) وأعلى ربح قبله وما بعده؛ بلا حسم = آخر قراءة."""
    rows = [(t, st, p) for t, st, p in rows if p is not None]
    out = {"exit_kind": "", "exit_ret": None, "exit_time": "", "mins": None, "peak_before": None, "after_peak": None, "last": None}
    if not rows:
        return out
    idx = next((i for i, (_, st, _) in enumerate(rows) if st in ("tp", "stopped")), None)
    out["last"] = rows[-1][2]
    t0 = entry_t or rows[0][0]
    if idx is None:
        out.update(exit_kind="بلا حسم", exit_ret=rows[-1][2], peak_before=max(p for _, _, p in rows))
        return out
    t, st, p = rows[idx]
    after = [x[2] for x in rows[idx + 1:]]
    out.update(exit_kind="هدف" if st == "tp" else "وقف", exit_ret=p, exit_time=t.strftime("%H:%M") if t else "",
               mins=round((t - t0).total_seconds() / 60) if t and t0 else None,
               peak_before=max(x[2] for x in rows[:idx + 1]), after_peak=max(after) if after else None)
    return out


def track_rows(track, pid):
    g = track[track["id"] == pid] if len(track) else track
    return sorted([(ts(r.time), r.status, num(r.pnl)) for r in g.itertuples()], key=lambda x: x[0] or datetime.min)


def spx_cards(bars, logs, ctx):
    z, track = read("zero_dte_log.csv"), read("spx_track.csv")
    out = []
    for r in z.itertuples():
        sym, strike = r.symbol.lstrip("^"), num(r.strike)
        t = ts(f"{r.date} {r.time}")
        if t is None or strike is None:
            continue
        pid = f"{sym}:{r.side}:{strike:g}:{r.expiry}"
        price, pnl = num(r.price), num(r.pnl)
        c = {"project": "spx", "id": pid, "day": r.date, "time": r.time, "sym": sym, "side": r.side, "strike": strike, "exp": r.expiry,
             "entry": price, "tod": sm.tod(r.time), "hold_ret": (pnl / price) if (pnl is not None and price) else None}
        c.update({f"ctx_{k}": v for k, v in (ctx.get(r.date) or {}).items()})
        f = bar_features(bars, r.date, t)
        c.update(f, r30_b=move_b(f["r30"], r.side), r60_b=move_b(f["r60"], r.side), rday_b=move_b(f["rday"], r.side),
                 pos_b="" if f["pos"] is None else "أعلى المدى" if f["pos"] >= 2 / 3 else "أدنى المدى" if f["pos"] <= 1 / 3 else "وسط المدى")
        c.update(shadow_context(r.date, t, r.side, logs))
        if c["news_kind"] == "allowed_half":
            c["news"] = "نصف كمية"
        c.update(path_outcome(track_rows(track, pid), t))
        out.append(c)
    return out


def stocks_cards(ctx):
    try:
        P = json.load(open(os.path.join(ROOT, "stocks_positions.json"), encoding="utf-8")).get("positions", [])
    except Exception:  # noqa: BLE001
        P = []
    track = read("stocks_track.csv")
    out = []
    for p in P:
        t = ts(p.get("time"))
        if t is None:
            continue
        day, x = t.date().isoformat(), p.get("ctx") or {}
        dl, dte = num(x.get("delta")), num(x.get("dte"))
        c = {"project": "stocks", "id": p["id"], "day": day, "time": t.strftime("%H:%M"), "sym": p.get("ticker"), "side": p.get("side"),
             "strike": p.get("strike"), "exp": p.get("exp"), "entry": p.get("entry"), "tod": sm.tod(t.strftime("%H:%M")),
             "s_news": "" if num(x.get("news_e")) is None else ("مع" if num(x["news_e"]) > 0 else "ضد" if num(x["news_e"]) < 0 else "محايدة"),
             "s_flow": "" if num(x.get("flow_e")) is None else ("مع" if num(x["flow_e"]) > 0 else "ضد" if num(x["flow_e"]) < 0 else "محايد"),
             "s_delta": "" if dl is None else "بعيد (<0.30)" if dl < 0.30 else "قريب (≥0.45)" if dl >= 0.45 else "متوسط",
             "s_dte": "" if dte is None else "≤7 أيام" if dte <= 7 else "8–21 يوماً" if dte <= 21 else ">21 يوماً",
             "s_earn": "" if "earn_in_life" not in x else ("نعم" if x["earn_in_life"] else "لا"),
             "s_exdiv": "" if "exdiv_in_life" not in x else ("نعم" if x["exdiv_in_life"] else "لا")}
        c.update({f"ctx_{k}": v for k, v in (ctx.get(day) or {}).items()})
        c.update(path_outcome(track_rows(track, p["id"]), t))
        out.append(c)
    return out


def build_cards(today):
    logs = load_logs()
    days = sorted(set(read("zero_dte_log.csv").get("date", pd.Series(dtype=str)).tolist()) | {today})
    bars = load_bars(days)
    sdays = set()
    for p in (json.load(open(os.path.join(ROOT, "stocks_positions.json"), encoding="utf-8")).get("positions", [])
              if os.path.exists(os.path.join(ROOT, "stocks_positions.json")) else []):
        if ts(p.get("time")):
            sdays.add(ts(p["time"]).date().isoformat())
    ctx = sm.load_context(sorted(set(days) | sdays))
    cards = spx_cards(bars, logs, ctx) + stocks_cards(ctx)
    write_cards(cards)
    return cards


def write_cards(cards):
    with open(CARDS, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CARD_COLS, extrasaction="ignore")
        w.writeheader()
        for c in cards:
            w.writerow({k: ("" if c.get(k) is None else round(c[k], 4) if isinstance(c.get(k), float) else c[k]) for k in CARD_COLS})


# ============================ صحة التشغيل ============================
def _ny_day(utc_iso):
    try:
        return pd.Timestamp(utc_iso).tz_convert("America/New_York").date().isoformat()
    except Exception:  # noqa: BLE001
        return None


def failed_runs(day):
    """{مهمة: عدد} لتشغيلات اليوم الفاشلة: rc غير صفر في runs.csv، وما لا يصل runs.csv أصلاً
    (فشل git قبل التشغيل كخطأ SSH مع GitHub 30/10، أو انتظار القفل) من سجل كل مهمة."""
    bad = {}
    runs = os.path.join(LOGDIR, "runs.csv")
    if os.path.exists(runs):
        for row in csv.reader(open(runs, encoding="utf-8", errors="replace")):
            if len(row) >= 5 and row[4].strip() not in ("0", "") and _ny_day(row[0]) == day:
                bad[row[2]] = bad.get(row[2], 0) + 1
    for name in sorted(os.listdir(LOGDIR)) if os.path.isdir(LOGDIR) else []:
        if not name.endswith(".log"):
            continue
        job, start = name[:-4], None
        with open(os.path.join(LOGDIR, name), encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("=== ") and " start " in line:
                    start = _ny_day(line.split()[1])
                elif ("git prepare failed" in line or line.startswith("lock timeout")) and start == day:
                    bad[job] = bad.get(job, 0) + 1
    return bad


def health(day, cards, now=None):
    """قائمة أسطر تحذير (فارغة = سليم). لا يرفع استثناءً."""
    L = []
    try:
        bad = failed_runs(day)
        if bad:
            L.append("مهام فشلت اليوم: " + "، ".join(f"{k} ×{v}" for k, v in sorted(bad.items())))
        botlog = os.path.join(LOGDIR, "bot.log")
        if os.path.exists(botlog):
            ids = set()
            with open(botlog, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "⚠️ سعر عقد" in line and f"({day}T" in line:
                        m = re.search(r"سعر عقد (\S+)", line)
                        if m:
                            ids.add(m.group(1))
            if ids:
                L.append("سعر عقد ثابت والأصل يتحرك (مصدر قديم؟): " + "، ".join(sorted(ids)))
        for proj in ("spx", "stocks"):
            g = read(f"data_gaps_{proj}_log.csv")
            if len(g) and "time_ny" in g:
                g = g[g["time_ny"].str.startswith(day)]
                if len(g):
                    L.append(f"فجوات بيانات {proj}: " + "، ".join(f"{k} ×{v}" for k, v in g["item"].value_counts().items()))
        lb = read("live_bars_log.csv")
        if len(lb) and "status" in lb:
            g = lb[lb["time"].str.startswith(day)]
            bad = int((g["status"] != "ok").sum())
            if bad:
                L.append(f"فحوص الشموع اللحظية غير السليمة اليوم: {bad} من {len(g)}")
        L += opposite_overlaps(day, cards, now)
    except Exception as e:  # noqa: BLE001
        L.append(f"تعذر فحص صحة التشغيل ({type(e).__name__})")
    return L


def opposite_overlaps(day, cards, now=None):
    """تنبيه فقط (القرار للمستخدم): عقدان متعاكسان على الأصل نفسه مفتوحان في الوقت نفسه اليوم. SPX وSPY أصل واحد."""
    end_day = datetime.fromisoformat(f"{day}T16:00")
    iv = []
    for c in cards:
        if c["day"] > day or (c["project"] == "spx" and c["day"] != day):
            continue
        t0 = datetime.fromisoformat(f"{c['day']}T{c['time']}")
        if c.get("exit_time") and c["exit_kind"] in ("هدف", "وقف"):
            if c["project"] == "stocks":
                continue    # لا نعرف تاريخ الإقفال بدقة هنا؛ نكتفي بالمفتوحة
            t1 = datetime.fromisoformat(f"{c['day']}T{c['exit_time']}")
        else:
            if c["project"] == "stocks" and c["exp"] and str(c["exp"]) < day:
                continue
            t1 = end_day
        under = "SPX/SPY" if c["project"] == "spx" and c["sym"] in ("SPX", "SPY") else c["sym"]
        iv.append((under, c["side"], max(t0, datetime.fromisoformat(f"{day}T09:30")), t1, c["id"]))
    out = []
    for a, b in itertools.combinations(iv, 2):
        if a[0] == b[0] and a[1] != b[1] and a[2] < b[3] and b[2] < a[3]:
            out.append(f"عقدان متعاكسان على {a[0]} في الوقت نفسه: {a[4]} و{b[4]} (تنبيه فقط)")
    return out


# ============================ اليومي ============================
def card_line(c):
    ctx = [c["tod"]]
    if c["project"] == "spx":
        for k, label in (("r30_b", "30د"), ("cand_mom", "mom"), ("cand_f30", "f30"), ("edge7", "edge7"), ("mom_live", "زخم حي"), ("live_side", "لحظي")):
            if c.get(k):
                ctx.append(f"{label} {c[k]}")
        if c.get("news"):
            ctx.append(f"أخبار {c['news']}")
    else:
        for k, label in (("s_news", "أخبار"), ("s_flow", "تدفق"), ("s_delta", "دلتا"), ("s_earn", "أرباح")):
            if c.get(k):
                ctx.append(f"{label} {c[k]}")
    res = c["exit_kind"] or "بلا بيانات"
    if c["exit_ret"] is not None:
        res += f" {pct(c['exit_ret'])}"
    if c.get("mins") is not None:
        m = c["mins"]
        res += f" بعد {m}د" if m < 120 else f" بعد {m / 60:.0f} ساعة" if m < 1440 else f" بعد {m / 1440:.0f} يوم"
    if c.get("peak_before") is not None and c["exit_kind"] == "وقف":
        res += f" (أعلى قبلها {pct(c['peak_before'])})"
    if c.get("after_peak") is not None:
        res += f"، وبعدها بلغ {pct(c['after_peak'])}"
    k = num(c["strike"])
    return f"• {c['time']} {c['sym']} {c['side']} {f'{k:g}' if k is not None else c['strike']} — {res}\n   {' | '.join(ctx)}"


def blocked_today(day):
    b = read("blocked_shadow_log.csv")
    if not len(b):
        return None
    g = b[b["entry_time"].str.startswith(day)]
    if not len(g):
        return {"n": 0}
    return {"n": len(g), "tp": int((g["out"] == "tp").sum()), "stop": int((g["out"] == "stop").sum()),
            "tags": g["tag"].value_counts().to_dict()}


def totals(cards, project):
    g = [c for c in cards if c["project"] == project and c["exit_ret"] is not None]
    if not g:
        return None
    r = [c["exit_ret"] for c in g]
    return {"n": len(g), "tp": sum(c["exit_kind"] == "هدف" for c in g), "stop": sum(c["exit_kind"] == "وقف" for c in g),
            "mean": float(np.mean(r))}


def daily_facts(day, cards, now=None):
    spx = [c for c in cards if c["project"] == "spx" and c["day"] == day]
    stocks_new = [c for c in cards if c["project"] == "stocks" and c["day"] == day]
    stocks_open = [c for c in cards if c["project"] == "stocks" and c["exit_kind"] == "بلا حسم" and c["day"] < day and str(c["exp"]) >= day]
    return {"day": day, "spx": spx, "stocks_new": stocks_new, "stocks_open": stocks_open, "blocked": blocked_today(day),
            "health": health(day, cards, now), "tot_spx": totals(cards, "spx"), "tot_stocks": totals(cards, "stocks")}


def daily_text(F):
    L = [f"🧠 **المحلل اليومي · {F['day']}**", ""]
    n = len(F["spx"])
    L.append("**SPX اليوم:** " + ("لا توصيات" if not n else "توصية واحدة" if n == 1 else "توصيتان" if n == 2 else f"{n} توصيات"))
    L += [card_line(c) for c in F["spx"]]
    if F["stocks_new"]:
        L += ["", f"**الشركات — جديدة اليوم:** {len(F['stocks_new'])}"] + [card_line(c) for c in F["stocks_new"]]
    if F["stocks_open"]:
        L += ["", "**الشركات — مفتوحة من قبل:** " + "، ".join(f"{c['sym']} {c['side']} {pct(c['last'])}" for c in F["stocks_open"])]
    b = F["blocked"]
    if b and b.get("n"):
        L += ["", f"**المحجوبة اليوم:** {b['n']} (هدف {b['tp']}، وقف {b['stop']}) — " + "، ".join(f"{k} ×{v}" for k, v in b["tags"].items())]
    for k, name in (("tot_spx", "SPX"), ("tot_stocks", "الشركات")):
        t = F[k]
        if t:
            L.append(f"📊 حصيلة {name} منذ البداية: {t['n']} صفقة، هدف {t['tp']}، وقف {t['stop']}، متوسط أول خروج {pct(t['mean'])}"
                     + (" (عينة صغيرة)" if t["n"] < SMALL else ""))
    L += ["", "**صحة التشغيل:** " + ("✅ سليمة" if not F["health"] else "")] + [f"⚠️ {h}" for h in F["health"]]
    return "\n".join(L)


def facts_for_model(F):
    def slim(c):
        keep = ("time", "sym", "side", "strike", "tod", "ctx_vix", "ctx_range", "ctx_trend", "ctx_gap", "ctx_event", "r30_b", "r60_b", "rday_b",
                "pos_b", "cand_mom", "cand_f30", "edge7", "mom_live", "live_side", "news", "s_news", "s_flow", "s_delta", "s_dte", "s_earn",
                "s_exdiv", "exit_kind", "mins")
        d = {k: c[k] for k in keep if c.get(k) not in (None, "")}
        for k in ("exit_ret", "peak_before", "after_peak", "last", "hold_ret"):
            if c.get(k) is not None:
                d[k + "_pct"] = round(c[k] * 100, 1)
        return d
    G = dict(F, spx=[slim(c) for c in F["spx"]], stocks_new=[slim(c) for c in F["stocks_new"]],
             stocks_open=[slim(c) for c in F["stocks_open"]])
    for k in ("tot_spx", "tot_stocks"):
        if G[k]:
            G[k] = dict(G[k], mean_pct=round(G[k]["mean"] * 100, 1))
            G[k].pop("mean")
    return json.dumps(G, ensure_ascii=False, default=str)


DAILY_SYSTEM = """أنت «المحلل» في مشروع «عين السوق»: محلل تداول محترف يقرأ نتائج بوت SPX (عقود اليوم نفسه) وبوت عقود الشركات بعد إغلاق كل يوم.
تصلك حقائق اليوم بصيغة JSON حسبها الكود من السجلات. معاني الحقول:
- tod وقت اليوم. ctx_vix/ctx_range/ctx_trend/ctx_gap/ctx_event: ظروف اليوم (VIX أمس، مدى أمس، اتجاه شمعة أمس، فجوة الافتتاح، يوم حدث مجدول).
- r30_b/r60_b/rday_b: حركة SPX خلال 30 و60 دقيقة قبل التوصية ومنذ الافتتاح، نسبةً إلى اتجاه الصفقة («مع الاتجاه» = المؤشر كان يتحرك في اتجاه الصفقة قبل الدخول). pos_b: موقع السعر في مدى اليوم لحظة التوصية.
- cand_mom: ظل «زخم آخر ساعة» في آخر موعد قبل التوصية. cand_f30: ظل «اتجاه أول 30 دقيقة من الجلسة». edge7: هل سجّل ظل الإشارة القوية (فرق نقاط ≥7) الاتجاه نفسه خلال 10 دقائق. mom_live: آخر إشارة من ظل الزخم الحي قبلها. live_side: اتجاه البوت بالشموع اللحظية في الفحص نفسه. news: الأخبار وقت التوصية.
- exit_kind/exit_ret_pct: أول خروج بقاعدة البوت (هدف +50% أو وقف −40%) وعائده. mins: دقائق حتى الخروج. peak_before_pct: أعلى ربح قبل الخروج. after_peak_pct: أعلى ربح بعد الخروج (المتابعة تستمر بعده). hold_ret_pct: الاحتفاظ حتى الانتهاء.
- الشركات: s_news أخبار الشركة، s_flow تدفق الخيارات (مع/ضد الاتجاه)، s_delta بُعد العقد، s_dte أيام العقد، s_earn/s_exdiv أرباح أو توزيعات داخل عمر العقد.
- blocked: توصيات منعتها قواعد البوت اليوم ونتيجتها في الظل. tot_spx/tot_stocks: الحصيلة منذ بداية السجلات، وليست لليوم. health: مشاكل التشغيل.
اكتب «قراءة المحلل» بالعربية في 4–6 أسطر قصيرة (أقل من 130 كلمة)، بلا عنوان:
- ماذا حدث اليوم ولماذا على الأرجح: اربط نتيجة كل صفقة بسياقها (وقت اليوم، الحركة قبلها، اتفاق الظلال أو خلافها، الأخبار).
- ما الذي يستحق المراقبة في الأيام القادمة (ملاحظة لا قاعدة).
- إن كانت في صحة التشغيل مشكلة فاذكر أثرها على موثوقية نتائج اليوم.
قواعد صارمة:
- استعمل الأرقام الموجودة في الحقائق فقط، وانسخها كما هي. لا تحسب أرقاماً جديدة ولا تقدّر ولا تذكر أسعاراً غير موجودة.
- كل استنتاج «ملاحظة وصفية، عينة صغيرة». لا تقترح تعديل البوت الآن: التعديلات تُدرس في مراجعة 9–14 نوفمبر فقط.
- لا توصيات ولا وعود بأرباح. «مع/ضد» تعني اتفاق الظل أو الحركة مع اتجاه الصفقة.
- نص عادي، للتمييز **غامق** فقط، بلا جداول ولا عناوين."""

WEEKLY_SYSTEM = """أنت «المحلل» في مشروع «عين السوق» (بوت SPX لعقود اليوم نفسه وبوت عقود الشركات). مهمتك الأسبوعية: اقتراح فرضيات للاختبار، لا تعديلات.
يصلك: جدول وصفي حسبه الكود (نتيجة الصفقات حسب كل عامل وحسب تداخل عاملين)، وقراءاتك اليومية لهذا الأسبوع، وفرضياتك السابقة إن وُجدت.
اكتب بالعربية:
1) **مراجعة الأسبوع السابق:** لكل ملاحظة أو فرضية سابقة: هل دعمتها بيانات هذا الأسبوع أم لا أم لا تكفي. كن صريحاً حين تخطئ.
2) **فرضيات مقترحة (حتى 3)**، لكل واحدة: الفكرة، الدليل (أرقام من الجدول كما هي فقط)، المقياس، معيار النجاح، أقل عينة (لا تقل عن 30 صفقة على 15 يوماً)، ما يبطلها، ونوعها (ظل جديد أو تعديل إعداد). الاختبار على أيام لاحقة فقط.
3) **ما لا يُستنتج بعد:** ما يبدو نمطاً لكنه عينة صغيرة.
قواعد: لا تقترح ما يخالف الشريعة (لا عقود متعاكسة على الأصل نفسه في وقت واحد)، ولا تغيير الوقف أو الحجم في البوت الحي. فضّل فرضية بسيطة واحدة قوية على ثلاث ضعيفة. التداخلات كثيرة فبعضها «ينجح» بالصدفة: اذكر ذلك.
نص عادي، للتمييز **غامق** فقط، بلا جداول."""


# ============================ النموذج ============================
def cost(u, model):
    pin, pout = PRICES.get(model, PRICES["claude-opus-5-5"])
    return (u["input"] * pin + u["output"] * pout) / 1e6


def spent_today(day):
    try:
        df = pd.read_csv(USAGE, dtype=str)
        return float(pd.to_numeric(df.loc[df["time"].str[:10] == day, "usd"], errors="coerce").sum())
    except Exception:  # noqa: BLE001
        return 0.0


def log_usage(row):
    new = not os.path.exists(USAGE)
    with open(USAGE, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=USAGE_COLS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in USAGE_COLS})


def ask(system, user, model, kind, now, client=None, max_tokens=8000):
    """نص النموذج أو None (لا مفتاح، السقف، خطأ). لا يطبع المفاتيح ولا نص الخطأ."""
    day = now.date().isoformat()
    if spent_today(day) >= DAILY_USD:
        print("سقف المحلل اليومي بُلغ؛ بلا قراءة.")
        return None
    if client is None:
        key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
        if not key:
            print("لا ANTHROPIC_API_KEY؛ بلا قراءة.")
            return None
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=key, timeout=180.0)
        except Exception as e:  # noqa: BLE001
            print("anthropic:", type(e).__name__)
            return None
    try:
        r = client.messages.create(model=model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}])
    except Exception as e:  # noqa: BLE001
        print("فشل طلب المحلل:", type(e).__name__)
        log_usage({"time": now.isoformat(timespec="seconds"), "kind": kind, "model": model, "status": "error"})
        return None
    u = {"input": getattr(r.usage, "input_tokens", 0) or 0, "output": getattr(r.usage, "output_tokens", 0) or 0}
    status = "refusal" if r.stop_reason == "refusal" else "max_tokens" if r.stop_reason == "max_tokens" else "ok"
    log_usage({"time": now.isoformat(timespec="seconds"), "kind": kind, "model": model, **u, "usd": round(cost(u, model), 5), "status": status})
    if status == "refusal":
        return None
    text = "\n".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()
    return text or None


_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def numbers(s):
    return [float(x.replace(",", ".")) for x in _NUM.findall(str(s).replace("٫", "."))]


def unverified(text, *sources):
    """أرقام في نص النموذج لا توجد في المصادر (فرق تقريب ≤ 0.051). الأعداد الصغيرة ≤ 31 مسموحة (عدّ وتواريخ)."""
    allowed = {round(v, 2) for s in sources for v in numbers(s)}
    bad = []
    for v in numbers(text):
        if v <= 31 and v == int(v):
            continue
        if not any(abs(v - a) <= 0.051 for a in allowed):
            bad.append(v)
    return bad


def daily_reading(F, det, now, client=None):
    facts = facts_for_model(F)
    text = ask(DAILY_SYSTEM, f"حقائق يوم {F['day']}:\n{facts}", MODEL, "daily", now, client)
    if not text:
        return None, "none"
    bad = unverified(text, facts, det, DAILY_SYSTEM)
    if bad:
        print("حارس الأرقام أسقط القراءة:", bad[:5])
        return None, "guard"
    return text, "ok"


# ============================ الأسبوعي ============================
def factor_table(cards, project):
    """[(اسم العامل، الفئة، عدد، نسبة الهدف، متوسط العائد)] — وصفي."""
    g = [c for c in cards if c["project"] == project and c["exit_ret"] is not None]
    rows = []
    for f, name in FEATURES:
        groups = {}
        for c in g:
            if c.get(f):
                groups.setdefault(c[f], []).append(c)
        for cat, v in sorted(groups.items()):
            rows.append((name, cat, len(v), sum(c["exit_kind"] == "هدف" for c in v) / len(v), float(np.mean([c["exit_ret"] for c in v]))))
    return rows


def pair_table(cards, project, top=12):
    g = [c for c in cards if c["project"] == project and c["exit_ret"] is not None]
    feats = [f for f, _ in FEATURES if any(c.get(f) for c in g)]
    names = dict(FEATURES)
    cells = {}
    for c in g:
        for a, b in itertools.combinations(feats, 2):
            if c.get(a) and c.get(b):
                cells.setdefault((names[a], c[a], names[b], c[b]), []).append(c)
    rows = [(k, len(v), sum(c["exit_kind"] == "هدف" for c in v) / len(v), float(np.mean([c["exit_ret"] for c in v])))
            for k, v in cells.items() if len(v) >= CELL_MIN]
    return sorted(rows, key=lambda r: -abs(r[3]))[:top]


def weekly_md(cards, day):
    L = [f"# المحلل الذكي — النتائج الوصفية ({day})", "",
         "وصفي فقط (H-ANALYST). العائد = أول خروج بالقاعدة (بلا حسم = آخر قراءة). خانة أقل من "
         f"{CELL_MIN} صفقات بلا أرقام، وكل شيء «عينة صغيرة» حتى {SMALL} صفقة. التداخلات كثيرة، فبعضها يبدو قوياً بالصدفة.", ""]
    for project, title in (("spx", "SPX"), ("stocks", "الشركات")):
        t = totals(cards, project)
        if not t:
            continue
        L += [f"## {title}: {t['n']} صفقة، هدف {t['tp']}، وقف {t['stop']}، متوسط {pct(t['mean'], 1)}", "",
              "| العامل | الفئة | صفقات | نسبة الهدف | متوسط العائد |", "|---|---|---|---|---|"]
        for name, cat, n, w, m in factor_table(cards, project):
            L.append(f"| {name} | {cat} | {n} | {f'{w * 100:.0f}%' if n >= CELL_MIN else '-'} | {pct(m, 1) if n >= CELL_MIN else '-'} |")
        pairs = pair_table(cards, project)
        if pairs:
            L += ["", f"### أبرز التداخلات الثنائية ({title}، {CELL_MIN}+ صفقات)", "", "| التداخل | صفقات | نسبة الهدف | متوسط العائد |", "|---|---|---|---|"]
            for (a, av, b, bv), n, w, m in pairs:
                L.append(f"| {a}={av} و{b}={bv} | {n} | {w * 100:.0f}% | {pct(m, 1)} |")
        L.append("")
    return "\n".join(L)


def journal_since(day0):
    """قراءات المحلل اليومية منذ day0 (نص)."""
    if not os.path.exists(JOURNAL):
        return ""
    s = open(JOURNAL, encoding="utf-8").read()
    parts = re.split(r"(?m)^## ", s)
    return "\n".join("## " + p for p in parts if p[:10] >= day0)[-12000:]


def previous_hypotheses():
    if not os.path.exists(RESULTS):
        return ""
    s = open(RESULTS, encoding="utf-8").read()
    i = s.find("## فرضيات المحلل")
    return s[i:][:6000] if i >= 0 else ""


# ============================ الإرسال والحفظ ============================
def to_html(text):
    t = html.escape(text, quote=False)
    t = re.sub(r"(?m)^#{1,6}\s*(.+)$", r"**\1**", t)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t).replace("**", "")


def send(text):
    """إلى TELEGRAM_CHAT_ID وحده، مقسّم لحد تيليجرام؛ خطأ التنسيق → نص عادي. يحفظ النص في messages_analyst.jsonl."""
    try:
        with open(os.path.join(ROOT, "messages_analyst.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), "text": text}, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass
    import requests
    tok, chat = os.getenv("TELEGRAM_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not tok or not chat:
        print("لا مفاتيح تيليجرام؛ لن يُرسل.")
        return False
    parts, cur = [], ""
    for line in to_html(text).split("\n"):
        if len(cur) + len(line) + 1 > 3800 and cur:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    parts.append(cur)
    ok = True
    for p in parts:
        try:
            r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                              data={"chat_id": chat, "text": p, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)
            if not r.ok:
                r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                                  data={"chat_id": chat, "text": html.unescape(re.sub(r"</?b>", "", p))}, timeout=20)
            ok = ok and r.ok
        except Exception as e:  # noqa: BLE001
            print("فشل الإرسال:", type(e).__name__)
            ok = False
    return ok


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_state(st):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f)


def append_journal(day, det, reading):
    os.makedirs(os.path.dirname(JOURNAL), exist_ok=True)
    new = not os.path.exists(JOURNAL)
    with open(JOURNAL, "a", encoding="utf-8", newline="\n") as f:
        if new:
            f.write("# سجل المحلل اليومي (H-ANALYST)\n\nوصفي فقط؛ لا تعديل على البوت قبل مراجعة 9–14/11 وبموافقة المستخدم.\n\n")
        f.write(f"## {day}\n\n{det}\n\n### قراءة المحلل\n\n{reading or '(لا قراءة)'}\n\n")


def session_day(day):
    """يوم جلسة؟ (أي سجل حي لليوم من المرشحين أو الشموع اللحظية أو توصيات البوت)."""
    for name, col in (("candidates_shadow_log.csv", "day"), ("live_bars_log.csv", "time"), ("zero_dte_log.csv", "date")):
        d = read(name)
        if len(d) and col in d and d[col].str.startswith(day).any():
            return True
    return False


# ============================ التشغيل ============================
def run_daily(now, force=False, no_send=False, day=None, client=None):
    day = day or now.date().isoformat()
    st = load_state()
    if not force and (now.weekday() >= 5 or (now.hour, now.minute) < SEND_AFTER or st.get("last_daily") == day):
        print(f"خارج نافذة المحلل أو أُرسل اليوم ({now:%Y-%m-%d %H:%M} نيويورك).")
        return 0
    if not force and not session_day(day):
        print("لا جلسة اليوم (عطلة؟)؛ لا تقرير.")
        return 0
    cards = build_cards(day)
    F = daily_facts(day, cards, now)
    det = daily_text(F)
    reading, status = daily_reading(F, det, now, client)
    text = det + "\n\n🧠 **قراءة المحلل:**\n" + (reading or ("(حُذفت: ذكرت رقماً غير موجود في البيانات)" if status == "guard" else "(غير متاحة اليوم)"))
    text += "\n\nوصفي فقط؛ لا تعديل قبل مراجعة 9–14/11 وبموافقتك."
    print(text)
    if no_send:
        return 0
    if send(text):
        append_journal(day, det, reading)
        st["last_daily"] = day
        save_state(st)
    return 0


def run_weekly(now, force=False, no_send=False, client=None):
    day = now.date().isoformat()
    st = load_state()
    if not force and (now.weekday() != 5 or st.get("last_weekly") == day):
        print("الأسبوعي يعمل السبت مرة واحدة.")
        return 0
    cards = build_cards(day)
    md = weekly_md(cards, day)
    prev = previous_hypotheses()
    week0 = (now.date() - timedelta(days=7)).isoformat()
    user = f"{md}\n\n# قراءاتك اليومية منذ {week0}\n{journal_since(week0) or '(لا شيء بعد)'}\n\n# فرضياتك السابقة\n{prev or '(لا شيء بعد)'}"
    text = ask(WEEKLY_SYSTEM, user, WEEKLY_MODEL, "weekly", now, client, max_tokens=4000)
    hyp = text or "(لم تُكتب فرضيات هذا الأسبوع: النموذج غير متاح)"
    os.makedirs(os.path.dirname(RESULTS), exist_ok=True)
    with open(RESULTS, "w", encoding="utf-8", newline="\n") as f:
        f.write(md + f"\n## فرضيات المحلل ({day}) — مقترحة، لا تُنفّذ إلا بعد المراجعة وبموافقة المستخدم\n\n{hyp}\n")
    os.makedirs(os.path.dirname(JOURNAL), exist_ok=True)
    with open(JOURNAL, "a", encoding="utf-8", newline="\n") as f:
        f.write(f"## {day} (أسبوعي)\n\n{hyp}\n\n")
    msg = f"🧠 **المحلل · مراجعة الأسبوع ({day})**\n\n{hyp}\n\nالجدول الكامل: research/analyst_results.md"
    print(msg)
    if not no_send and send(msg):
        st["last_weekly"] = day
        save_state(st)
    return 0


def main(argv):
    now = ny_now()
    force, no_send = "--force" in argv, "--no-send" in argv
    day = argv[argv.index("--day") + 1] if "--day" in argv else None
    if "--weekly" in argv:
        return run_weekly(now, force, no_send)
    return run_daily(now, force, no_send, day)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
