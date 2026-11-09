"""أخبار الشركات الثقيلة في SPX وأثرها على المؤشر (H-HEAVY، 9/11/2026 بطلب المستخدم: «الأهم الصقر والبوتين مطلعين… وتقييمهم للصفقات ووضع المؤشر»).

يقرأ أخبار مراقب الأخبار (news_watch_log.csv) لأكبر الشركات وزناً في SPX، وحركة سهم كل شركة لها خبر (داخل الجلسة أو خارجها)،
ويقدّر أثرها على المؤشر = الوزن × الحركة. يستعمله: المتداول الذكي والمتعلّم والصقر (قسم في المدخلات)، والشاشات (شاشة)،
وبوت SPX وبوت الشركات (سطر عرض فقط في التوصية)، وتنبيه للمستخدم عند حركة كبيرة. لا يغيّر منطق أي توصية.
الأوزان تقريبية (نوفمبر 2026) وتُحدَّث يدوياً.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
NEWS = os.path.join(ROOT, "news_watch_log.csv")
STATE = os.path.join(os.getenv("SPXBOT_DATA", "/opt/spxbot/data"), "screens", "heavy_state.json")
HOURS = 18
ALERT_MOVE = 0.02
HEAVY = {"NVDA": (7.5, "nvidia|jensen huang"), "MSFT": (6.5, "microsoft"), "AAPL": (6.3, r"\bapple\b"), "AMZN": (4.0, "amazon"),
         "GOOGL": (4.0, "alphabet|google"), "META": (3.0, r"\bmeta\b"), "AVGO": (2.6, "broadcom"), "TSLA": (2.0, "tesla"),
         "BRK-B": (1.6, "berkshire"), "JPM": (1.4, "jpmorgan"), "LLY": (1.3, "eli lilly"), "V": (1.0, r"\bvisa\b")}
DIR_AR = {"up": "صعود", "down": "هبوط"}
IMP_AR = {"high": "عالية", "medium": "متوسطة"}


# ====================== دوال صافية ======================
def match_ticker(row):
    """الشركة الثقيلة التي يخصها الخبر (من عمود ticker أو اسمها في العنوان)، أو None."""
    tk = str(row.get("ticker") or "").upper()
    if tk in HEAVY:
        return tk
    title = str(row.get("title") or "").lower()
    for t, (_w, pat) in HEAVY.items():
        if re.search(pat, title):
            return t
    return None


def heavy_news(df, now, hours=HOURS):
    """{شركة: [(الموضوع، الاتجاه، الأهمية)]} لأخبار متوسطة/عالية خلال آخر hours ساعة، بلا تكرار للقصة نفسها."""
    if df is None or not len(df):
        return {}
    t = pd.to_datetime(df["time_utc"], errors="coerce", utc=True)
    d = df[(t >= pd.Timestamp(now - timedelta(hours=hours)).tz_convert("UTC")) & df["importance"].isin(["high", "medium"])]
    d = d.assign(tk=[match_ticker(r) for r in d.to_dict("records")])
    d = d[d["tk"].notna()].drop_duplicates("cluster")
    out = {}
    for r in d.sort_values("time_utc", ascending=False).itertuples():
        out.setdefault(r.tk, []).append((r.topic, r.direction, r.importance))
    return out


def impacts(news, moves):
    """[{ticker, weight, move, contrib, items}] مرتبة بحجم الأثر. moves: {شركة: حركة نسبية أو None}."""
    out = []
    for tk, items in news.items():
        w = HEAVY[tk][0]
        mv = moves.get(tk)
        out.append({"ticker": tk, "weight": w, "move": mv, "contrib": None if mv is None else w / 100 * mv, "items": items})
    return sorted(out, key=lambda x: -abs(x["contrib"] or 0))


def net_dir(imp, eps=0.0005):
    """اتجاه أثر الشركات الثقيلة على SPX: من مجموع الأثر، وإلا من اتجاه الأخبار موزوناً."""
    tot = sum(x["contrib"] for x in imp if x["contrib"] is not None)
    if any(x["contrib"] is not None for x in imp) and abs(tot) > eps:
        return 1 if tot > 0 else -1
    v = sum(x["weight"] * {"up": 1, "down": -1}.get(d, 0) for x in imp for _t, d, _i in x["items"][:1])
    return 0 if abs(v) < 1 else (1 if v > 0 else -1)


def block_text(imp):
    """قسم المدخلات للمتداول الذكي والصقر."""
    if not imp:
        return ""
    L = ["## الشركات الثقيلة في SPX: أخبار آخر 18 ساعة وأثرها التقريبي على المؤشر (الوزن تقريبي)"]
    for x in imp:
        mv = f"السهم {x['move'] * 100:+.1f}% ← أثر على SPX ≈ {x['contrib'] * 100:+.2f}%" if x["move"] is not None else "حركة السهم غير متاحة الآن"
        news = "؛ ".join(f"{t} ({DIR_AR.get(d, 'غير واضح')}، {IMP_AR.get(i, i)})" for t, d, i in x["items"][:3])
        L.append(f"- {x['ticker']} (وزن ≈{x['weight']:g}%): {mv} | {news}")
    if any(x["contrib"] is not None for x in imp):
        tot = sum(x["contrib"] for x in imp if x["contrib"] is not None)
        L.append(f"المجموع التقريبي على SPX: {tot * 100:+.2f}%.")
    else:
        L.append(f"الأثر الكمي غير متاح الآن (السهم خارج التداول)؛ اتجاه الأخبار الموزون: {DIR_AR.get({1: 'up', -1: 'down'}.get(net_dir(imp)), 'غير واضح')}.")
    L.append("خبر شركة ثقيلة قد يغلب قراءة المؤشر، وقد يرتد إذا كان قصة شركة لا سوق.")
    return "\n".join(L)


def caption_line(imp, side=None, sym=None):
    """سطر واحد للتوصية: أكبر خبر ثقيل وأثره، مع ✅/⚠️ مقابل جهة التوصية. عرض فقط."""
    if not imp:
        return ""
    x = next((y for y in imp if y["ticker"] == (sym or "").upper()), imp[0])
    topic = x["items"][0][0]
    tail = f"{x['move'] * 100:+.1f}% ≈ {x['contrib'] * 100:+.2f}% على SPX" if x["move"] is not None else DIR_AR.get(x["items"][0][1], "")
    s = {"CALL": 1, "PUT": -1}.get(side or "", 0)
    eff = (1 if (x["contrib"] or 0) > 0 else -1 if (x["contrib"] or 0) < 0 else {"up": 1, "down": -1}.get(x["items"][0][1], 0))
    tag = "" if not (s and eff) else (" ✅ مع التوصية" if s * eff > 0 else " ⚠️ عكس التوصية")
    return f"🏛️ شركة ثقيلة: {x['ticker']} {tail} ({topic}){tag}"


def alerts_due(imp, sent, day):
    """الشركات التي تستحق تنبيهاً اليوم: حركة ≥ 2% ولم يُنبَّه عنها."""
    return [x for x in imp if x["move"] is not None and abs(x["move"]) >= ALERT_MOVE and f"{day}:{x['ticker']}" not in sent]


# ====================== الشبكة ======================
def stock_move(sym, now=None, max_age_min=40):
    """حركة السهم الآن مقابل الإغلاق المرجعي: داخل الجلسة مقابل إغلاق أمس، وخارجها مقابل آخر إغلاق رسمي.
    None إذا كانت آخر صفقة أقدم من max_age_min دقيقة (مثل الليل بين 20:00 و04:00، أو عطلة الأسبوع)، فلا نعرض حركة قديمة."""
    try:
        import yfinance as yf
        h = yf.Ticker(sym).history(period="5d", interval="5m", prepost=True)["Close"].dropna()
        h.index = h.index.tz_convert("America/New_York")
        if not len(h):
            return None
        now = now or datetime.now(timezone.utc)
        if (pd.Timestamp(now) - h.index[-1]).total_seconds() > max_age_min * 60:
            return None
        hm = h.index.strftime("%H:%M")
        reg = h[(hm >= "09:30") & (hm < "16:00")]
        last_t = h.index[-1]
        in_session = "09:30" <= last_t.strftime("%H:%M") < "16:00"
        prev = reg[reg.index.date < last_t.date()] if in_session else reg
        return float(h.iloc[-1]) / float(prev.iloc[-1]) - 1 if len(prev) else None
    except Exception:  # noqa: BLE001
        return None


def current(now=None, news_path=NEWS):
    """الأثر الحالي للشركات الثقيلة: قائمة impacts، أو [] عند غياب الأخبار أو أي فشل."""
    now = now or datetime.now(timezone.utc)
    try:
        df = pd.read_csv(news_path, dtype=str, keep_default_na=False)
    except Exception:  # noqa: BLE001
        return []
    news = heavy_news(df, now)
    return impacts(news, {tk: stock_move(tk, now) for tk in news})


def maybe_alert(imp, now, no_send):
    """يرسل تنبيهاً مرة واحدة يومياً لكل شركة ثقيلة تحركت ≥ 2% ولها خبر."""
    day = now.date().isoformat()
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        st = {"sent": []}
    due = alerts_due(imp, set(st["sent"]), day)
    for x in due:
        txt = (f"🏛️ **خبر شركة ثقيلة: {x['ticker']}** {x['move'] * 100:+.1f}% "
               f"(أثر تقريبي على SPX {x['contrib'] * 100:+.2f}%)\n" + "\n".join(f"• {t}" for t, _d, _i in x["items"][:2]))
        print(txt)
        if not no_send:
            import analyst as an
            an.send(txt, "heavy")
        st["sent"].append(f"{day}:{x['ticker']}")
    if due:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        st["sent"] = st["sent"][-200:]
        json.dump(st, open(STATE, "w", encoding="utf-8"))
    return due
