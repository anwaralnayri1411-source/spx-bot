"""القياديات: مساهمة أكبر شركات SPX في حركة المؤشر خلال الجلسة (أوزان تقريبية ثابتة، تُراجع دورياً)."""
import pandas as pd

# أوزان تقريبية بالنسبة المئوية من SPX (ليست أرقاماً رسمية لحظية)
WEIGHTS = {"NVDA": 7.5, "MSFT": 6.5, "AAPL": 6.5, "GOOGL": 5.5, "AMZN": 4.0, "META": 3.0,
           "AVGO": 2.8, "TSLA": 2.0, "BRK-B": 1.7, "JPM": 1.4}


def compute():
    """يرجع dict: day (مساهمة % في SPX منذ إغلاق أمس)، m30 (آخر 30 دقيقة)، up (عدد الصاعدة)، n، top (أكبر محرك) أو None."""
    try:
        import yfinance as yf
        df = yf.download(list(WEIGHTS), period="5d", interval="5m", group_by="ticker",
                         progress=False, auto_adjust=False, threads=True)
        day = m30 = 0.0
        up = n = 0
        movers = []
        for t, w in WEIGHTS.items():
            try:
                cl = df[t]["Close"].dropna()
                if len(cl) < 8:
                    continue
                last_d = cl.index[-1].date()
                prev = cl[cl.index.date < last_d]
                if prev.empty:
                    continue
                p0 = float(prev.iloc[-1])
                p1 = float(cl.iloc[-1])
                p30 = float(cl.iloc[-7]) if len(cl) >= 7 else p1
                chg = p1 / p0 - 1
                day += w * chg
                m30 += w * (p1 / p30 - 1)
                up += 1 if chg > 0 else 0
                n += 1
                movers.append((abs(w * chg), t, chg))
            except Exception:
                continue
        if n < 6:
            return None
        movers.sort(reverse=True)
        return {"day": day, "m30": m30, "up": up, "n": n, "top": [(t, c) for _, t, c in movers[:3]]}
    except Exception:
        return None
