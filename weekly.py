"""تقرير أسبوعي من سجلاتنا: كم إشارة، وكم بلغ الهدف أو الوقف، وصافي النتيجة، وتحذير من صغر العيّنة."""
import json
import os
from datetime import timedelta

import pandas as pd

import options_bot as ob

DAYS = 7
MIN_SAMPLE = 20      # أقل عدد إشارات لنحكم بأي استنتاج


def _spx_section(cut):
    L = ["🏛️ <b>SPX / SPY / NDXP</b>"]
    rows = None
    try:
        d = pd.read_csv(ob.LOG_FILE)
        d["date"] = pd.to_datetime(d["date"])
        rows = d[d["date"] >= cut]
    except Exception:
        pass
    n_sig = 0 if rows is None else len(rows)
    L.append(f"إشارات الأسبوع: <b>{n_sig}</b>")
    if rows is not None and n_sig:
        res = rows["result"].fillna("")
        w, lo = int((res == "ربح").sum()), int((res == "خسارة").sum())
        pnl = pd.to_numeric(rows["pnl"], errors="coerce").fillna(0).sum() * 100
        L.append(f"عند الانتهاء: {w} رابحة | {lo} خاسرة | {n_sig - w - lo} معلّقة | الصافي على عقد لكل إشارة: <b>{pnl:+,.0f}$</b>")
    try:
        P = json.load(open(ob.POS_FILE, encoding="utf-8"))["positions"]
        P = [p for p in P if p["time"][:10] >= cut.strftime("%Y-%m-%d")]
        if P:
            tp = sum(1 for p in P if p.get("peak", 0) >= ob.TAKE_PROFIT)
            st = sum(1 for p in P if p.get("status") == "stopped" and p.get("peak", 0) < ob.TAKE_PROFIT)
            L.append(f"بقاعدتنا (هدف +{ob.TAKE_PROFIT * 100:.0f}% / وقف -{ob.STOP_LOSS * 100:.0f}%): 🎯 {tp} بلغت الهدف | 🛑 {st} بلغت الوقف | ⏳ {len(P) - tp - st} بلا حسم")
    except Exception:
        pass
    return L, n_sig


def _stocks_section(cut):
    L = ["🏢 <b>الشركات</b>"]
    try:
        d = pd.read_csv("stocks_log.csv")
        d["day"] = pd.to_datetime(d["time"].str[:10])
        d = d[d["day"] >= cut]
    except Exception:
        return L + ["لا سجل بعد."], 0
    n = len(d)
    L.append(f"إشارات الأسبوع: <b>{n}</b> | CALL {int((d['side'] == 'CALL').sum())} | PUT {int((d['side'] == 'PUT').sum())}")
    rule = d["rule"].fillna("")
    tp, st, no = int((rule == "tp").sum()), int((rule == "stop").sum()), int((rule == "none").sum())
    pend = n - tp - st - no
    L.append(f"بقاعدتنا: 🎯 {tp} هدف | 🛑 {st} وقف | ⏳ {no} بلا حسم | معلّقة {pend}")
    rp = pd.to_numeric(d["rule_pnl"], errors="coerce").fillna(0).sum()
    if tp + st:
        L.append(f"صافي القاعدة (عقد لكل إشارة): <b>{rp:+,.0f}$</b>")
    held = pd.to_numeric(d["pnl"], errors="coerce").dropna()
    if len(held):
        L.append(f"لو احتفظنا للانتهاء: <b>{held.sum():+,.0f}$</b> على {len(held)} إشارة")
    return L, n


def build(n=None):
    n = n or ob.now_ny()
    cut = pd.Timestamp(n.date() - timedelta(days=DAYS))
    a, na = _spx_section(cut)
    b, nb = _stocks_section(cut)
    L = [f"📊 <b>التقرير الأسبوعي</b> | حتى {n.date().isoformat()} #تقرير", ""] + a + [""] + b + [""]
    total = na + nb
    if total < MIN_SAMPLE:
        L.append(f"⚠️ <b>العيّنة صغيرة</b> ({total} إشارة، نحتاج {MIN_SAMPLE}+ لنحكم). لا تبنِ قراراً على هذه الأرقام وحدها.")
    try:
        import signals_extra
        rv = signals_extra.review(DAYS)
        if rv:
            L += ["🧪 <b>إشارات تجريبية صامتة</b> (لا تؤثر على التوصيات)"]
            for k, (cnt, m, avg, hit) in rv.items():
                L.append(f"{k}: {cnt} حالة" + (f" | بعد ساعة: متوسط {avg:+.1f} نقطة بالاتجاه المتوقع، صحيحة {hit * 100:.0f}% (من {m})" if m else " | لا بيانات كافية بعد"))
            L.append("")
    except Exception as e:
        print("تعذر ملخص الإشارات التجريبية:", e)
    L.append("ملاحظة: النتائج تقديرية من بيانات متأخرة، والخروج مقيس بسعر البيع (bid).")
    L.append("👁 <b>عين السوق | Market Eye</b>")
    return "\n".join(L)


if __name__ == "__main__":
    ob.send_telegram(build())
