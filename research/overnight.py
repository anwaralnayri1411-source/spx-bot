"""قياس تاريخي: هل تتنبأ إشارات ما قبل الافتتاح (الفجوة + آسيا + بداية أوروبا) باتجاه جلسة SPX؟

كل ما هنا يستخدم بيانات يومية مجانية من Yahoo (حوالي 10 سنوات). الحدود:
- فجوة SPX = سعر الافتتاح / إغلاق أمس - 1، وهي أقرب بديل متاح لتغير عقود ES قبل الافتتاح.
- آسيا (نيكاي، هانغ سنغ): إغلاق نفس التاريخ يسبق افتتاح أمريكا فعلاً.
- أوروبا: فجوة افتتاحها (افتتاح اليوم / إغلاق أمس) متاحة قبل افتتاح أمريكا. أما إغلاقها فلا (يقع بعد الافتتاح).
- الهدف: عائد الجلسة من الافتتاح إلى الإغلاق (open→close). لا نعرف من البيانات اليومية ماذا حدث داخل الجلسة.
"""
import sys
from datetime import datetime

import numpy as np
import pandas as pd

TICKERS = {"SPX": "^GSPC", "N225": "^N225", "HSI": "^HSI", "FTSE": "^FTSE", "DAX": "^GDAXI"}
GAP_T = 0.004            # نفس عتبة البوت لفجوة الافتتاح (0.4%)
OUT = "research/overnight_results.md"


def build(frames):
    """frames: dict اسم -> DataFrame فيه Open/Close. يرجع جدولاً موحداً بالتاريخ."""
    out = pd.DataFrame(index=frames["SPX"].index)
    s = frames["SPX"]
    out["gap"] = s["Open"] / s["Close"].shift(1) - 1
    out["oc"] = s["Close"] / s["Open"] - 1
    out["cc"] = s["Close"] / s["Close"].shift(1) - 1
    for k in ("N225", "HSI"):
        f = frames[k].reindex(out.index)
        out[k] = f["Close"] / f["Close"].shift(1) - 1   # عائد إغلاق نفس التاريخ (يسبق الافتتاح الأمريكي)
    for k in ("FTSE", "DAX"):
        f = frames[k].reindex(out.index)
        out[k] = f["Open"] / frames[k]["Close"].shift(1).reindex(out.index) - 1   # فجوة افتتاح أوروبا
    return out.dropna(subset=["gap", "oc"])


def hit(series_up, mask):
    m = series_up[mask]
    return (len(m), float(m.mean() * 100) if len(m) else float("nan"))


def tstat(x):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) < 5 or x.std(ddof=1) == 0:
        return float("nan")
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def section_gap(d):
    L = ["## 1) هل تتنبأ فجوة الافتتاح باتجاه الجلسة؟ (الافتتاح → الإغلاق)", ""]
    up = d["oc"] > 0
    L.append(f"- عدد الأيام: **{len(d)}** | نسبة الأيام الصاعدة عموماً (أساس المقارنة): **{up.mean() * 100:.1f}%**")
    L.append(f"- الارتباط بين الفجوة وعائد الجلسة: **{d['gap'].corr(d['oc']):+.3f}**")
    L.append("")
    L.append("| فئة الفجوة | الأيام | الجلسة صاعدة % | متوسط عائد الجلسة | t |")
    L.append("|---|---|---|---|---|")
    bins = [(-9, -0.008, "هبوط أكبر من 0.8%"), (-0.008, -GAP_T, "هبوط 0.4-0.8%"), (-GAP_T, GAP_T, "محايد (±0.4%)"),
            (GAP_T, 0.008, "صعود 0.4-0.8%"), (0.008, 9, "صعود أكبر من 0.8%")]
    for lo, hi, name in bins:
        m = (d["gap"] > lo) & (d["gap"] <= hi)
        n, h = hit(up, m)
        L.append(f"| {name} | {n} | {h:.1f} | {d.loc[m, 'oc'].mean() * 100:+.3f}% | {tstat(d.loc[m, 'oc']):+.1f} |")
    L.append("")
    return L


def section_halves(d):
    L = ["## 2) هل النتيجة مستقرة عبر الزمن؟", "", "| الفترة | الارتباط (فجوة، جلسة) | صعود الجلسة بعد فجوة صاعدة >0.4% | هبوط الجلسة بعد فجوة هابطة <-0.4% |",
         "|---|---|---|---|"]
    mid = d.index[len(d) // 2]
    for name, part in (("النصف الأول", d[d.index < mid]), ("النصف الثاني", d[d.index >= mid])):
        a = part[part["gap"] > GAP_T]
        b = part[part["gap"] < -GAP_T]
        ha = f"{(a['oc'] > 0).mean() * 100:.0f}% (n={len(a)})" if len(a) else "-"
        hb = f"{(b['oc'] < 0).mean() * 100:.0f}% (n={len(b)})" if len(b) else "-"
        L.append(f"| {name} ({part.index[0].date()} → {part.index[-1].date()}) | {part['gap'].corr(part['oc']):+.3f} | {ha} | {hb} |")
    L.append("")
    return L


def section_regions(d):
    L = ["## 3) هل تفسّر آسيا وأوروبا الفجوة، وهل تضيف شيئاً على اتجاه الجلسة؟", "",
         "| المصدر | ارتباطه بفجوة SPX | ارتباطه بعائد الجلسة | أيام |", "|---|---|---|---|"]
    for k, name in (("N225", "نيكاي (إغلاق)"), ("HSI", "هانغ سنغ (إغلاق)"), ("FTSE", "فجوة افتتاح لندن"), ("DAX", "فجوة افتتاح ألمانيا")):
        x = d.dropna(subset=[k])
        if len(x) > 50:
            L.append(f"| {name} | {x[k].corr(x['gap']):+.3f} | {x[k].corr(x['oc']):+.3f} | {len(x)} |")
    L.append("")
    x = d.dropna(subset=["N225", "HSI", "FTSE", "DAX"])
    if len(x) > 100:
        A = np.column_stack([x["N225"], x["HSI"], x["FTSE"], x["DAX"], np.ones(len(x))])
        coef, *_ = np.linalg.lstsq(A, x["gap"].values, rcond=None)
        r2 = 1 - ((x["gap"].values - A @ coef) ** 2).sum() / ((x["gap"].values - x["gap"].mean()) ** 2).sum()
        L.append(f"- نسبة تفسير آسيا وأوروبا لفجوة SPX (R²): **{r2 * 100:.0f}%**")
        # هل تضيف الأسواق الأخرى على الفجوة نفسها في توقع الجلسة؟
        resid = x["gap"].values - A @ coef
        L.append(f"- ارتباط الجزء غير المفسَّر من الفجوة بعائد الجلسة: **{np.corrcoef(resid, x['oc'].values)[0, 1]:+.3f}**")
        L.append(f"- ارتباط الجزء المفسَّر (ما تنقله آسيا وأوروبا) بعائد الجلسة: **{np.corrcoef(A @ coef, x['oc'].values)[0, 1]:+.3f}**")
    L.append("")
    return L


def section_combo(d):
    L = ["## 4) اتفاق الإشارات: هل يرفع الدقة؟", "", "| الحالة | الأيام | الجلسة بنفس الاتجاه % |", "|---|---|---|"]
    x = d.dropna(subset=["N225", "HSI"]).copy()
    asia = (x["N225"] + x["HSI"]) / 2
    cases = [
        ("فجوة صاعدة وآسيا صاعدة", (x["gap"] > GAP_T) & (asia > 0.003), True),
        ("فجوة صاعدة وآسيا هابطة", (x["gap"] > GAP_T) & (asia < -0.003), True),
        ("فجوة هابطة وآسيا هابطة", (x["gap"] < -GAP_T) & (asia < -0.003), False),
        ("فجوة هابطة وآسيا صاعدة", (x["gap"] < -GAP_T) & (asia > 0.003), False),
    ]
    for name, m, up in cases:
        sub = x[m]
        if len(sub):
            rate = (sub["oc"] > 0).mean() * 100 if up else (sub["oc"] < 0).mean() * 100
            L.append(f"| {name} | {len(sub)} | {rate:.0f}% |")
    L.append("")
    return L


def run(frames):
    d = build(frames)
    L = [f"# قياس إشارات ما قبل الافتتاح على SPX", f"_شُغّل في {datetime.utcnow():%Y-%m-%d %H:%M} UTC | "
         f"{d.index[0].date()} → {d.index[-1].date()}_", ""]
    for sec in (section_gap, section_halves, section_regions, section_combo):
        L += sec(d)
    L += ["## ملاحظات منهجية",
          "- الفجوة المستخدمة هي فجوة SPX الفعلية، وهي ما تُظهره عقود ES قبل الافتتاح تقريباً.",
          "- البيانات يومية: لا نرى ما حدث داخل الجلسة ولا أول 30 دقيقة.",
          "- لا تكاليف تداول ولا خيارات هنا. هذا قياس لاتجاه المؤشر فقط، وليس ربح عقود.",
          "- ارتباط ضعيف لا يعني عدم الفائدة عند الفجوات الكبيرة، فانظر جدول الفئات."]
    return "\n".join(L)


def download():
    import yfinance as yf
    frames = {}
    for k, t in TICKERS.items():
        df = yf.download(t, start="2015-01-01", auto_adjust=False, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=["Open", "Close"])
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        frames[k] = df
        print(k, len(df), df.index[0].date(), df.index[-1].date())
    return frames


if __name__ == "__main__":
    text = run(download())
    print(text)
    import os
    os.makedirs("research", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
