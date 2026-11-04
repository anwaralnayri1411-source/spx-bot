"""مساعد محادثة (Agent) داخل بوت الأوامر — بطلب المستخدم 31/10/2026 (التصميم في CLAUDE.md).
أي رسالة نصية ليست أمراً معروفاً تصل هنا: Claude يستدعي أدوات قراءة فقط (حالة العقود، تحليل شركة، فحص البوت الآن
بلا تسجيل ولا إرسال للمشتركين، سجل التوصيات، أسعار لحظية، الأرباح، نتائج الاختبارات) ثم يرد عليك وحدك.
لا ينفّذ صفقات ولا يصدر توصيات من عنده ولا يغيّر أي ملف للبوتين.
إعدادات: ASSISTANT_MODEL (افتراضي claude-haiku-5-5)، ASSISTANT_DAILY_USD (سقف يومي، افتراضي 0.5).
يحتاج ANTHROPIC_API_KEY. السجلات: assistant_history.json (آخر الرسائل)، assistant_usage.csv (التكلفة لكل طلب)."""
import csv
import html
import json
import os
import re
from contextlib import ExitStack
from datetime import datetime
from unittest import mock

import pandas as pd
import requests

import options_bot as ob
import stocks_bot as sb

MODEL = os.getenv("ASSISTANT_MODEL", "claude-haiku-5-5")
DAILY_USD = float(os.getenv("ASSISTANT_DAILY_USD", "0.5"))
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0)}  # $/مليون، 06/10/2026
HIST_FILE = "assistant_history.json"
USAGE_FILE = "assistant_usage.csv"
CONTEXT_FILE = "assistant_context.md"
DATA = os.getenv("SPXBOT_DATA", "/opt/spxbot/data")
HIST_KEEP = 10          # آخر 10 رسائل (5 أسئلة وأجوبتها)
MAX_STEPS = 8           # أقصى عدد ردود من النموذج في السؤال الواحد (يحد التكلفة)
USAGE_COLS = ["time", "model", "input", "output", "cache_write", "cache_read", "usd", "tools", "status"]


def _txt(s):
    """نص البوت (HTML تيليجرام) إلى نص عادي للنموذج."""
    return html.unescape(re.sub(r"<[^>]+>", "", str(s)))


def _cb():
    import commands_bot as cb      # استيراد متأخر: commands_bot يستورد هذا الملف
    return cb


def readonly():
    """فحص عند الطلب لا يكتب في سجلات البوتين ولا ظلالهما (الفحوص المجدولة وحدها تُسجَّل)."""
    import data_gaps
    import live_bars
    import live_quotes
    st = ExitStack()
    st.enter_context(mock.patch.object(ob, "_log_live_bars", lambda *a, **k: ob.LIVE_CMP.clear()))
    st.enter_context(mock.patch.object(data_gaps, "note", lambda *a, **k: None))
    st.enter_context(mock.patch.object(live_bars, "log", lambda *a, **k: None))
    st.enter_context(mock.patch.object(live_quotes, "log", lambda *a, **k: None))
    st.enter_context(mock.patch.object(sb, "_rule_compare", lambda *a, **k: None))
    st.enter_context(mock.patch.object(sb, "shadow_log", lambda *a, **k: None))
    return st


# ====================== الأدوات (قراءة فقط) ======================
def t_open_positions():
    n = ob.now_ny()
    return _txt(_cb().status_text(n))


def t_company(ticker):
    tk = (ticker or "").upper().lstrip("$")
    return _txt(_cb().company_text(tk, ob.now_ny()))          # النص يذكر مصدر السعر (لحظي أو ياهو) منذ 2/11


def t_spx_status():
    return _txt(_cb().spx_text(ob.now_ny()))


def t_spx_scan():
    n = ob.now_ny()
    with readonly():
        a = ob.analyze(True)
        if a["contracts"]:
            ob._live_prices(a)
    head = (f"وقت الفحص {n:%Y-%m-%d %H:%M} نيويورك | داخل وقت التداول: {'نعم' if ob.in_window(n) else 'لا'}"
            f"{' (السوق مغلق: العقود تجريبية لأقرب انتهاء)' if a['is_test'] else ''}\n"
            f"نقاط الاتجاه: صعود {a['bull']:.1f} / هبوط {a['bear']:.1f} | الفرق {a['edge']:.1f} والحد {ob.MIN_EDGE}")
    if a["contracts"]:
        src = ", ".join(sorted({k.get("price_src", "yahoo") for k in a["contracts"]}))
        return f"{head}\nمصدر سعر العقد: {src}\n\n{_txt(ob.signal_message(a))}"
    return f"{head}\n\n{_txt(ob.no_signal_message(a))}"


def t_stocks_scan():
    with readonly():
        a = sb.analyze(True)
        sb._live_prices_stock(a["ideas"])
    L = [f"وقت الفحص {a['n']:%Y-%m-%d %H:%M} نيويورك | فُحصت {a['scanned']} شركة | الحد الأدنى للفرق {sb.MIN_EDGE}"
         f"{' | خارج وقت التداول' if a['is_test'] else ''}"]
    if not a["ideas"]:
        L.append("لا فرصة تجتاز قواعد البوت الآن.")
    for i in a["ideas"]:
        src = i["contracts"][0].get("price_src", "yahoo") if i["contracts"] else ""
        L += ["", f"[مصدر سعر العقد: {src}]", _txt(sb.idea_caption(i))]
    if a["watch"]:
        L += ["", "قريبة ولم تجتز:"] + [f"- {tk}: {why}" for tk, why in a["watch"][:8]]
    return "\n".join(L)


def t_history(symbol="", limit=10):
    sym = (symbol or "").upper().lstrip("$^")
    limit = max(1, min(int(limit or 10), 30))
    out = []
    for name, path, col in (("الشركات", sb.LOG_FILE, "ticker"), ("SPX", ob.LOG_FILE, "symbol")):
        try:
            df = pd.read_csv(path, dtype=str).fillna("")
        except Exception as e:
            out.append(f"{name}: تعذر قراءة السجل ({type(e).__name__})")
            continue
        if sym:
            df = df[df[col].str.upper().str.lstrip("^") == sym]
        if len(df):
            out.append(f"== {name} (آخر {min(limit, len(df))} من {len(df)}) ==\n" + df.tail(limit).to_csv(index=False))
    return "\n".join(out) or "لا توصيات مسجّلة لهذا الرمز."


def t_quote(symbol, side="", exp="", strike=0.0):
    import live_quotes as lq
    sym = (symbol or "").upper().lstrip("$^")
    px, src = lq.spot(sym)
    L = [f"{sym}: " + (f"{px:,.2f} (مصدر {src}، لحظي)" if px else "لا سعر لحظي الآن (السوق مغلق أو المصدر لا يرد)")]
    if side and exp and strike:
        side = "CALL" if side.upper().startswith("C") else "PUT"
        root = ob.LIVE_ROOTS.get(sym, sym)
        q, qs = lq.best_quote(lq.occ(root, exp, side, float(strike)))
        L.append(f"العقد {sym} {float(strike):g} {side} {exp}: " +
                 (f"bid {q['bid']} / ask {q['ask']} (مصدر {qs}، عمر {q['age'] or 0:.0f}ث)" if q
                  else "لا تسعيرة لحظية حديثة (السوق مغلق أو العقد غير موجود)"))
    return "\n".join(L)


def t_earnings(ticker):
    tk = (ticker or "").upper().lstrip("$")
    try:
        df = pd.read_csv(os.path.join(DATA, "earnings_dates.csv"), dtype=str)
    except Exception as e:
        return f"تعذر قراءة تواريخ الأرباح ({type(e).__name__})."
    df = df[df["sym"] == tk]
    if df.empty:
        return f"لا تواريخ أرباح محفوظة لـ {tk} (القائمة تغطي شركات البوت فقط)."
    ts = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    now = pd.Timestamp.now(tz="UTC")
    nxt, past = ts[ts >= now].sort_values(), ts[ts < now].sort_values()
    L = [f"{tk}: الأرباح القادمة " + (f"{nxt.iloc[0].tz_convert('America/New_York'):%Y-%m-%d %H:%M} نيويورك" if len(nxt) else "غير معروفة")]
    if len(past):
        r = df.loc[past.index[-1]]
        L.append(f"آخر أرباح {past.iloc[-1].tz_convert('America/New_York'):%Y-%m-%d %H:%M} | المفاجأة {r['surprise'] or '-'}%")
    return "\n".join(L)


def t_research(name=""):
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "research")
    files = sorted(f for f in os.listdir(d) if f.endswith("_results.md")) if os.path.isdir(d) else []
    if not name:
        return "الملفات المتاحة:\n" + "\n".join(files)
    hits = sorted((f for f in files if name.lower() in f.lower()), key=lambda f: -os.path.getmtime(os.path.join(d, f)))
    if not hits:
        return f"لا ملف يطابق «{name}». المتاحة:\n" + "\n".join(files)
    with open(os.path.join(d, hits[0]), encoding="utf-8") as f:
        s = f.read()
    more = f"\n(ملفات أخرى تطابق: {'، '.join(hits[1:])})" if len(hits) > 1 else ""
    return f"{hits[0]} (الأحدث):{more}\n{s[:6000]}" + ("\n…(مقتطع)" if len(s) > 6000 else "")


LIVE = os.getenv("SPXBOT_LIVE", "/opt/spxbot/live")


def _fresh_csv(name, group):
    """أحدث نسخة من سجل مهمة أخرى: شجرتها على الخادم أولاً (أحدث من نسخة GitHub في شجرة هذه المهمة)."""
    for p in (os.path.join(LIVE, group, name), os.path.join(os.path.dirname(os.path.abspath(__file__)), name)):
        if os.path.exists(p):
            try:
                return pd.read_csv(p, dtype=str, keep_default_na=False)
            except Exception:
                pass
    return pd.DataFrame()


def t_news(ticker="", hours=24):
    """أخبار السوق والشركة من مصادر البوت: مراقب الأخبار خارج الجلسة، والعقود الآجلة، ومستوى صدمة الأخبار بقواعد البوت الآن،
    وذاكرة أخبار الشركات. قراءة فقط."""
    import news_watch as nw
    n = ob.now_ny()
    tk = (ticker or "").upper().lstrip("$")
    hours = max(1, min(int(hours or 24), 96))
    cut = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=hours)).isoformat()
    L = [f"وقت القراءة {n:%Y-%m-%d %H:%M} نيويورك | آخر {hours} ساعة"]
    w = _fresh_csv("news_watch_log.csv", "news-watch")
    if len(w):
        w = w[(w["time_utc"] >= cut) & (w["new"] == "1") & w["importance"].isin(["high", "medium"])]
        w = w[(w["scope"] == "market") | (w["ticker"] == tk)] if tk else w[w["scope"].isin(["market", "company"])]
        w = w.sort_values("time_utc").drop_duplicates("cluster")          # أول عنوان للحدث
        w = w.assign(r=w["importance"].map({"high": 0, "medium": 1})).sort_values(["r", "time_utc"], ascending=[True, False]).head(10)
    L.append("\n== مراقب الأخبار خارج الجلسة (Claude يصنّف العناوين؛ الاتجاه توقّع غير مختبر) ==")
    if len(w):
        for r in w.itertuples():
            t = pd.Timestamp(r.time_utc).tz_convert("America/New_York")
            L.append(f"[{nw.IMP_AR.get(r.importance, r.importance)}] {t:%m-%d %H:%M} — {r.topic} — الأثر المتوقع: "
                     f"{nw.DIR_AR.get(r.direction, '-')}{' — ' + r.ticker if r.ticker else ''}\n  {r.title}")
    else:
        L.append("لا أخبار مهمة مسجّلة في هذه الفترة (المراقب يعمل خارج الجلسة فقط، كل 30 دقيقة، منذ 2/11/2026).")
    try:
        ref = nw.last_close(n)
        L.append("\n" + nw.fut_line(nw.fut_moves(n, ref))
                 + f" | الإغلاق المرجعي {ref:%m-%d %H:%M}")
    except Exception as e:
        L.append(f"\nالعقود الآجلة: تعذر ({type(e).__name__})")
    try:
        sh = ob.market_shock(ob.fetch_headlines())
        L.append(f"\n== صدمة الأخبار بقواعد بوت SPX الآن: المستوى {sh['level']} (1 هادئ، 2 متوسط، 3 مرتفع) ==\n"
                 + ("\n".join(f"- {x}" for x in sh["flags"]) or "- لا أسباب")
                 + "\n(القاعدة: حتى 30/10/2026 كان المستوى 3 يمنع أي توصية؛ منذ جلسة 2/11 لا يمنعها بل نصف الكمية مع تحذير. "
                 + "VIX والشركات القيادية من ياهو بتغيّرها اليومي" + ("؛ خارج الجلسة هي أرقام آخر جلسة.)" if not ob.in_window(n) else ".)"))
    except Exception as e:
        L.append(f"\nصدمة الأخبار: تعذر الحساب ({type(e).__name__})")
    if tk:
        m = _fresh_csv("news_memory_log.csv", "news-memory")
        if len(m):
            m = m[(m["ticker"] == tk) & (m["time_utc"] >= cut) & (m["new"] == "1")].sort_values("time_utc", ascending=False).head(8)
        L.append(f"\n== أخبار {tk} في ذاكرة الأخبار (أيام التداول) ==")
        L += ([f"{pd.Timestamp(r.time_utc).tz_convert('America/New_York'):%m-%d %H:%M} [{r.importance}] {r.event} "
               f"(متوقع: {r.expect}) — {r.title}" for r in m.itertuples()] if len(m) else ["لا أخبار جديدة مسجّلة لها في هذه الفترة."])
    return "\n".join(L)


TOOL_FUNCS = {
    "open_positions": (t_open_positions, "العقود المفتوحة الآن لبوت الشركات مع ربحها الحالي.", {}),
    "company_analysis": (t_company, "تحليل شركة بنقاط بوت الشركات (الميل الفني، المتوسطات، RSI) وعقود البوت المفتوحة عليها وآخر توصياته لها.",
                         {"ticker": {"type": "string", "description": "رمز السهم مثل NVDA"}}),
    "spx_status": (t_spx_status, "حالة SPX الآن: السعر، VIX، فحوص اليوم، آخر إشارة.", {}),
    "spx_scan_now": (t_spx_scan, "يشغّل فحص بوت SPX الآن بقواعده (قراءة فقط: لا يفتح مركزاً ولا يرسل للمشتركين). "
                     "يرجع التوصية بعقدها وهدفها ووقفها إن وُجدت، أو أسباب عدم التوصية. يستغرق ~30 ثانية.", {}),
    "stocks_scan_now": (t_stocks_scan, "يشغّل فحص بوت الشركات الآن على كل القائمة (قراءة فقط). يرجع الفرص التي تجتاز قواعده بعقودها، "
                        "والقريبة التي لم تجتز. يستغرق دقيقة أو أكثر؛ لسؤال عن شركة واحدة استعمل company_analysis.", {}),
    "recommendation_history": (t_history, "سجل توصيات البوتين ونتائجها (ربح/خسارة). اختياري: رمز محدد وعدد الصفوف.",
                               {"symbol": {"type": "string", "description": "رمز اختياري، فارغ = الكل"},
                                "limit": {"type": "integer", "description": "عدد الصفوف (1-30)"}}),
    "live_quote": (t_quote, "سعر لحظي لسهم/مؤشر، واختيارياً تسعيرة عقد خيار (bid/ask) من Alpaca/Tastytrade.",
                   {"symbol": {"type": "string", "description": "مثل AVGO أو SPX أو SPY"},
                    "side": {"type": "string", "description": "CALL أو PUT (اختياري)"},
                    "exp": {"type": "string", "description": "تاريخ الانتهاء YYYY-MM-DD (اختياري)"},
                    "strike": {"type": "number", "description": "سعر التنفيذ (اختياري)"}}),
    "earnings_date": (t_earnings, "موعد الأرباح القادمة وآخر أرباح ومفاجأتها لشركة من قائمة البوت.",
                      {"ticker": {"type": "string", "description": "رمز السهم"}}),
    "market_news": (t_news, "أخبار السوق والشركات من مصادر البوت: أهم أخبار الليل والعطلة المصنّفة (الأهمية والاتجاه المتوقع)، "
                    "وحركة العقود الآجلة ES/NQ منذ آخر إغلاق، ومستوى صدمة الأخبار بقواعد بوت SPX الآن وأسبابه، "
                    "واختيارياً أخبار شركة محددة. استعمله لأي سؤال عن الأخبار أو وضع السوق قبل الافتتاح.",
                    {"ticker": {"type": "string", "description": "رمز شركة اختياري، فارغ = السوق"},
                     "hours": {"type": "integer", "description": "الفترة بالساعات (1-96، افتراضي 24)"}}),
    "research_results": (t_research, "نتائج اختباراتنا المحفوظة. بلا اسم: قائمة الملفات؛ بالاسم (جزء منه مثل pead): محتوى الملف.",
                         {"name": {"type": "string", "description": "جزء من اسم الملف، فارغ = القائمة"}}),
}


def tool_defs():
    return [{"name": k, "description": d, "input_schema": {"type": "object", "properties": p, "required": [
        x for x in p if x in ("ticker",) or (k == "live_quote" and x == "symbol")]}}
            for k, (_, d, p) in TOOL_FUNCS.items()]


def run_tool(name, args):
    f = TOOL_FUNCS.get(name, (None,))[0]
    if f is None:
        return f"أداة غير معروفة: {name}", True
    try:
        return f(**(args or {})) or "(لا نتيجة)", False
    except Exception as e:
        return f"فشلت الأداة {name}: {type(e).__name__}: {e}", True


# ====================== التكلفة والذاكرة ======================
def cost(u, model=MODEL):
    pin, pout = PRICES.get(model, PRICES["claude-opus-5-5"])
    return ((u["input"] + 1.25 * u["cache_write"] + 0.1 * u["cache_read"]) * pin + u["output"] * pout) / 1e6


def spent_today(n):
    try:
        df = pd.read_csv(USAGE_FILE, dtype=str)
        return float(pd.to_numeric(df.loc[df["time"].str[:10] == n.date().isoformat(), "usd"], errors="coerce").sum())
    except Exception:
        return 0.0


def log_usage(row):
    new = not os.path.exists(USAGE_FILE)
    with open(USAGE_FILE, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=USAGE_COLS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in USAGE_COLS})


def load_hist():
    try:
        with open(HIST_FILE, encoding="utf-8") as f:
            return json.load(f)[-HIST_KEEP:]
    except Exception:
        return []


def save_hist(h):
    with open(HIST_FILE, "w", encoding="utf-8") as f:
        json.dump(h[-HIST_KEEP:], f, ensure_ascii=False)


def system_prompt():
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), CONTEXT_FILE), encoding="utf-8") as f:
        return f.read()


# ====================== الحلقة ======================
def answer(text, n, client=None):
    """يرجع (الرد، معلومات الاستهلاك). client قابل للاستبدال في الاختبار."""
    if spent_today(n) >= DAILY_USD:
        return f"⏸️ بلغتُ سقف اليوم ({DAILY_USD:g}$). أعود للرد غداً، والأوامر العادية تعمل كالمعتاد.", None
    if client is None:
        import anthropic
        key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
        if not key:
            return "⚠️ المساعد غير مفعّل: لا يوجد ANTHROPIC_API_KEY.", None
        client = anthropic.Anthropic(api_key=key, timeout=120.0)
    hist = load_hist()
    msgs = hist + [{"role": "user", "content": f"[{n:%Y-%m-%d %H:%M} نيويورك] {text}"}]
    system = [{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}]
    u = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
    used, status, reply = [], "ok", ""
    for _ in range(MAX_STEPS):
        r = client.messages.create(model=MODEL, max_tokens=8000, system=system, tools=tool_defs(), messages=msgs)
        for k, a in (("input", "input_tokens"), ("output", "output_tokens"),
                     ("cache_write", "cache_creation_input_tokens"), ("cache_read", "cache_read_input_tokens")):
            u[k] += getattr(r.usage, a, 0) or 0
        if r.stop_reason == "refusal":
            status, reply = "refusal", "لا أستطيع الإجابة عن هذا الطلب. جرّب صياغة أخرى."
            break
        calls = [b for b in r.content if b.type == "tool_use"]
        if r.stop_reason != "tool_use" or not calls:
            reply = "\n".join(b.text for b in r.content if b.type == "text").strip()
            if r.stop_reason == "max_tokens":
                status, reply = "max_tokens", reply + "\n…(انقطع الرد لطوله)"
            break
        msgs.append({"role": "assistant", "content": r.content})
        results = []
        for b in calls:
            out, err = run_tool(b.name, b.input)
            used.append(b.name + ("!" if err else ""))
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": out[:12000], "is_error": err})
        msgs.append({"role": "user", "content": results})
    else:
        status, reply = "max_steps", "لم أكمل الجواب في عدد الخطوات المسموح. جرّب سؤالاً أضيق."
    usd = cost(u)
    log_usage({"time": n.isoformat(timespec="seconds"), "model": MODEL, **u, "usd": round(usd, 5),
               "tools": " ".join(used), "status": status})
    reply = reply or "لم أجد ما أقوله. جرّب صياغة أخرى."
    # مصدر الرد يُحفظ معه حتى لا يتراجع النموذج لاحقاً عن أرقام جاءت من أداة لا يرى نتيجتها (حدث 30/10 و1/11)
    src = (f"\n[مصدر هذا الرد: نتائج الأدوات {'، '.join(used)} وقت {n:%Y-%m-%d %H:%M} نيويورك]" if used
           else f"\n[هذا الرد بلا أدوات، {n:%Y-%m-%d %H:%M} نيويورك]")
    save_hist(hist + [{"role": "user", "content": text}, {"role": "assistant", "content": reply + src}])
    return reply, dict(u, usd=usd, tools=used, status=status)


def to_html(text):
    """**غامق** من النموذج إلى <b> تيليجرام، وعناوين # إلى غامق، وكل ما عداه نص مهرَّب."""
    t = ob.esc(text)
    t = re.sub(r"(?m)^#{1,6}\s*(.+)$", r"**\1**", t)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t).replace("**", "")


def send_private(text):
    """للمستخدم وحده (TELEGRAM_CHAT_ID)، بلا نسخ للمشاهدين، مقسّم لحد تيليجرام؛ خطأ التنسيق → نص عادي."""
    ob.log_message(text)
    if not ob.TOKEN or not ob.CHAT_ID:
        print(text)
        return
    text = to_html(text)
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3800 and cur:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    parts.append(cur)
    for p in parts:
        try:
            r = requests.post(f"https://api.telegram.org/bot{ob.TOKEN}/sendMessage",
                              data={"chat_id": ob.CHAT_ID, "text": p, "parse_mode": "HTML"}, timeout=15)
            if not r.ok:
                requests.post(f"https://api.telegram.org/bot{ob.TOKEN}/sendMessage",
                              data={"chat_id": ob.CHAT_ID, "text": html.unescape(re.sub(r"</?b>", "", p))}, timeout=15)
        except requests.RequestException as e:
            print("تعذر إرسال رد المساعد:", e)


def typing():
    try:
        requests.post(f"https://api.telegram.org/bot{ob.TOKEN}/sendChatAction",
                      data={"chat_id": ob.CHAT_ID, "action": "typing"}, timeout=10)
    except requests.RequestException:
        pass


def handle_text(text, n):
    """نقطة الدخول من commands_bot: يرد على رسالة حرة. أي خطأ يُبلَّغ للمستخدم ولا يوقف بوت الأوامر."""
    if ob.TOKEN:
        typing()
    try:
        reply, _ = answer(text, n)
    except Exception as e:
        print("خطأ المساعد:", type(e).__name__, e)
        reply = f"⚠️ تعذر على المساعد الرد الآن ({type(e).__name__}). الأوامر العادية تعمل كالمعتاد."
    send_private(reply)
