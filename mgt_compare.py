"""تسجيل صامت (لا يظهر في أي رسالة): مستويات «التوازن» المستنتجة من تقارير MGT، للمقارنة معها لاحقاً.

الفرضية (من تقرير واحد لـ MGT، لم تُثبت بعد):
  التوازن = أقرب مضاعف لـ 12.5 من السعر | الوحدة u = 0.25% من السعر
  الثبات = التوازن + 0.5u | منطقة البيع = التوازن + (1.5u إلى 2.5u) | منطقة الشراء = التوازن - (1.5u إلى 2.5u)
  ميل التدفق = صعودي إذا السعر فوق التوازن.
المقارنة: ضع أرقام كل تقرير MGT بجانب السطر المقابل بالوقت في mgt_compare_log.csv. إن طابقت في 5 تقارير فأكثر نعرضها."""
import os

import pandas as pd

LOG = "mgt_compare_log.csv"
COLS = ["time", "spot", "spot_bar", "eq", "unit", "stab", "sell_lo", "sell_hi", "buy_lo", "buy_hi", "flow"]


def compute(S):
    eq = round(S / 12.5) * 12.5
    u = S * 0.0025
    flow = "up" if S > eq else ("down" if S < eq else "flat")
    return {"spot": S, "eq": eq, "unit": u, "stab": eq + 0.5 * u, "sell_lo": eq + 1.5 * u, "sell_hi": eq + 2.5 * u,
            "buy_lo": eq - 2.5 * u, "buy_hi": eq - 1.5 * u, "flow": flow}


def log(d):
    """d: قاموس levels.compute (يحوي time وspot وspot_bar)."""
    try:
        row = compute(d["spot"])
        row["time"] = d["time"]
        row["spot_bar"] = d.get("spot_bar")
        new = not os.path.exists(LOG)
        pd.DataFrame([{k: (round(row[k], 2) if isinstance(row.get(k), float) else row.get(k)) for k in COLS}]).to_csv(
            LOG, mode="a", header=new, index=False)
    except Exception as e:
        print("تعذر حفظ مقارنة MGT:", e)
