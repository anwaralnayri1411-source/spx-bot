#!/usr/bin/env python3
"""فحص الحقول المتاحة من Alpaca وTastytrade لعقد SPY وسهم (قراءة فقط، بطلب المستخدم 24/10/2026): هل يقدمان الحجم والعقود المفتوحة وصفقات الخيارات غير ما نقرأه الآن؟

نطبع **أسماء الحقول وأمثلة قيم عددية فقط** (بلا ترويسات ولا مفاتيح ولا رموز دخول) لنقرر ما نستعمله في ظل التدفق. نجرب أيضاً هل نقاط نهاية الصفقات متاحة بالباقة (الرمز والخطأ فقط).
يحتاج ALPACA_KEY/SECRET وTASTY_CLIENT_SECRET/TASTY_REFRESH_TOKEN (الخادم). تشغيل: python research/data_fields_probe.py   المخرج: research/data_fields_probe_results.md
"""
import os
import sys
import urllib.error
import urllib.parse
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import live_quotes as lq  # noqa: E402

OUT = os.path.join(HERE, "data_fields_probe_results.md")


# ============================ دوال نقية ============================
def flatten(d, prefix="", depth=3):
    """{مسار: قيمة} لقاموس متداخل حتى عمق depth. القوائم تُمثَّل بطولها."""
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict) and depth > 1:
                out.update(flatten(v, p, depth - 1))
            elif isinstance(v, list):
                out[p] = f"list[{len(v)}]"
            else:
                out[p] = v
    return out


def show(v):
    """قيمة صالحة للعرض: أرقام وقصير النص فقط (نقطع الطويل)."""
    if isinstance(v, bool) or v is None:
        return str(v)
    if isinstance(v, (int, float)):
        return f"{v:g}"
    s = str(v)
    return s if len(s) <= 40 else s[:37] + "..."


def table(title, d):
    L = [f"### {title}", ""]
    if not d:
        return L + ["لا بيانات.", ""]
    L += ["| الحقل | مثال |", "|---|---|"] + [f"| `{k}` | {show(v)} |" for k, v in sorted(flatten(d).items())] + [""]
    return L


def attempt(name, fn):
    """(اسم، نتيجة dict أو None، سطر الحالة). لا يرفع استثناء؛ يسجل رمز HTTP فقط."""
    try:
        return fn(), "ok"
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return None, type(e).__name__


def target_contract(spot, today=None):
    """عقد كول ATM على SPY بأقرب انتهاء يوم تداول (اليوم إن كان يوم عمل، وإلا التالي)."""
    d = today or datetime.now().date()
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return lq.occ("SPY", d.isoformat(), "CALL", round(spot)), d


# ============================ التشغيل ============================
def main(argv):
    if not (os.getenv("ALPACA_KEY") and os.getenv("ALPACA_SECRET")):
        print("يلزم مفاتيح Alpaca (الخادم).")
        return 0
    spot, _ = lq.spot("SPY")
    spot = spot or 700.0
    occ, exp = target_contract(spot)
    L = ["# حقول البيانات المتاحة من Alpaca وTastytrade (نتائج آلية)", "", f"- عقد الفحص: `{occ}` (انتهاء {exp})، سعر SPY ≈ {spot:.2f}. أسماء حقول وأمثلة أرقام فقط.", ""]
    q = urllib.parse.urlencode({"symbols": occ, "feed": "indicative"})
    snap, st = attempt("snapshot", lambda: (lq._get(f"{lq.DATA}/v1beta1/options/snapshots?{q}").get("snapshots") or {}).get(occ))
    L += [f"## Alpaca (feed=indicative) — لقطة العقد: {st}", ""] + table("حقول اللقطة", snap)
    for name, path in (("آخر صفقة", f"/v1beta1/options/trades/latest?{q}"), ("سجل الصفقات", f"/v1beta1/options/trades?{q}&limit=5"),
                       ("شموع يومية", f"/v1beta1/options/bars?symbols={urllib.parse.quote(occ)}&timeframe=1Day&limit=3")):
        r, st = attempt(name, lambda p=path: lq._get(lq.DATA + p))
        L += [f"- {name}: {st}"]
        if r:
            L += [""] + table(name, r)
    r, st = attempt("opra", lambda: lq._get(f"{lq.DATA}/v1beta1/options/snapshots?{urllib.parse.urlencode({'symbols': occ, 'feed': 'opra'})}"))
    L += ["", f"- تغذية OPRA الكاملة: {st}", ""]
    if os.getenv("TASTY_CLIENT_SECRET") and os.getenv("TASTY_REFRESH_TOKEN"):
        L += ["## Tastytrade — market-data/by-type", ""]
        for kind, sym in (("equity-option", lq._tasty_occ(occ)), ("equity", "SPY")):
            qq = urllib.parse.urlencode({kind: sym})
            r, st = attempt(kind, lambda qq=qq: (lq._tasty_req(f"/market-data/by-type?{qq}", token=lq._tasty_token()).get("data") or {}).get("items"))
            L += [f"- {kind}: {st}", ""]
            if r:
                L += table(f"حقول {kind}", r[0])
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
