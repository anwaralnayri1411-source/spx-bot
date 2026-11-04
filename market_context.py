"""سياق السوق لتوصيات الشركات (H-CTXSIG، سطر إعلامي؛ بطلب المستخدم 4/11/2026). لا يغيّر أي توصية.

القيادة: متوسط (XLK، XLY، XLC) − متوسط (XLU، XLP، XLV) منذ إغلاق أمس؛ > +0.3% هجومي، < −0.3% دفاعي.
محرك الشركة: IBIT (البتكوين) لـCOIN وMSTR وHOOD وSOFI وTSLA، وUSO (النفط) لـXOM وCVX؛ ±0.5% يُعد حركة.
الاتساع: عدد القطاعات الـ11 المتحركة باتجاه الصفقة.
في الاختبار التاريخي (research/context_signals_results.md) كان المحرك والقيادة الأقرب لفرق حقيقي، ولم يثبتا بعد.
"""
from datetime import time as dtime
from zoneinfo import ZoneInfo

import numpy as np

NY = ZoneInfo("America/New_York")
SECTORS = ("XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE", "XLC")
RISK_ON, DEFENSIVE = ("XLK", "XLY", "XLC"), ("XLU", "XLP", "XLV")
DRIVER = {"COIN": "IBIT", "MSTR": "IBIT", "HOOD": "IBIT", "SOFI": "IBIT", "TSLA": "IBIT", "XOM": "USO", "CVX": "USO"}
DRIVER_AR = {"IBIT": "البتكوين", "USO": "النفط"}
LEAD_TH, DRV_TH = 0.003, 0.005
SYMS = sorted(set(SECTORS) | {"IBIT", "USO"})


def moves_from(bars):
    """{رمز: حركة آخر سعر منذ إغلاق الجلسة السابقة} من شموع 5د بفهرس نيويورك بلا منطقة (ساعات السوق)."""
    out = {}
    for s, d in bars.items():
        if d is None or not len(d):
            continue
        last_day = d.index.date[-1]
        prev = d[d.index.date < last_day]
        if len(prev):
            out[s] = float(d["Close"].iloc[-1] / prev["Close"].iloc[-1] - 1)
    return out


def fetch():
    """حركات الصناديق الآن (ياهو، طلب واحد). {} عند الفشل."""
    try:
        import yfinance as yf
        raw = yf.download(list(SYMS), period="5d", interval="5m", group_by="ticker", auto_adjust=False, progress=False, threads=True)
        bars = {}
        for s in SYMS:
            try:
                d = raw[s].dropna(subset=["Close"])
            except Exception:  # noqa: BLE001
                continue
            d.index = d.index.tz_convert(NY).tz_localize(None)
            bars[s] = d[(d.index.time >= dtime(9, 30)) & (d.index.time < dtime(16, 0))]
        return moves_from(bars)
    except Exception as e:  # noqa: BLE001
        print("تعذر قراءة سياق السوق:", type(e).__name__)
        return {}


def read(moves, sym, side):
    """سياق صفقة: lead (+1 موافق، −1 مخالف، 0 محايد)، والمحرك وحركته وتوافقه، والاتساع. {} بلا بيانات."""
    if not moves:
        return {}
    s = 1 if side == "CALL" else -1
    out = {}
    ro, de = [moves.get(k) for k in RISK_ON], [moves.get(k) for k in DEFENSIVE]
    if None not in ro and None not in de:
        lv = float(np.mean(ro) - np.mean(de))
        out["lead_val"] = round(lv, 4)
        out["lead"] = 0 if abs(lv) < LEAD_TH else (1 if lv * s > 0 else -1)
    sec = [moves.get(k) for k in SECTORS if moves.get(k) is not None]
    if len(sec) >= len(SECTORS) - 1:
        out["breadth"] = sum(1 for m in sec if m * s > 0)
        out["n_sec"] = len(sec)
    drv = DRIVER.get(sym)
    if drv and moves.get(drv) is not None:
        dm = moves[drv]
        out.update(driver=drv, driver_move=round(dm, 4), driver_al=0 if abs(dm) < DRV_TH else (1 if dm * s > 0 else -1))
    return out


def line(ctx):
    """سطر عربي للتوصية، أو "" بلا سياق."""
    if not ctx:
        return ""
    parts = []
    if "lead" in ctx:
        mood = "هجومي (تقنية واستهلاك)" if ctx["lead_val"] >= LEAD_TH else ("دفاعي (مرافق وسلع أساسية)" if ctx["lead_val"] <= -LEAD_TH else "متوازن")
        parts.append(f"السوق {mood}" + (" ✅ مع العقد" if ctx["lead"] == 1 else (" ⚠️ ضد العقد" if ctx["lead"] == -1 else "")))
    if "driver" in ctx:
        tag = " ✅ مع العقد" if ctx["driver_al"] == 1 else (" ⚠️ ضد العقد" if ctx["driver_al"] == -1 else "")
        parts.append(f"{DRIVER_AR[ctx['driver']]} {ctx['driver_move'] * 100:+.1f}%{tag}")
    if "breadth" in ctx:
        parts.append(f"{ctx['breadth']}/{ctx['n_sec']} قطاعات مع العقد")
    return "🧭 <b>السياق (تجريبي):</b> " + " · ".join(parts) if parts else ""
