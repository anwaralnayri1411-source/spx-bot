"""يفحص هل تتوفر خيارات ناسداك 100 (NDX/NDXP) من yfinance المجاني، ويرسل النتيجة."""
import yfinance as yf
import options_bot as ob

L = ["🔎 <b>فحص بيانات NDXP</b>"]
for sym in ("^NDX", "NDX", "^NDXP", "QQQ"):
    try:
        t = yf.Ticker(sym)
        px = t.history(period="5d")["Close"].dropna()
        opts = list(t.options)
        info = f"{sym}: سعر {px.iloc[-1]:,.1f}" if len(px) else f"{sym}: لا سعر"
        if opts:
            ch = t.option_chain(opts[0])
            near = ch.calls.iloc[(ch.calls["strike"] - float(px.iloc[-1])).abs().argsort()[:1]]
            r = near.iloc[0]
            info += f" | {len(opts)} تواريخ، أقربها {opts[0]} | قريب من السعر: {r['strike']:.0f} آسك {r['ask']} بيد {r['bid']} OI {r['openInterest']}"
        else:
            info += " | لا خيارات"
        L.append(info)
    except Exception as e:
        L.append(f"{sym}: خطأ {str(e)[:80]}")
ob.send_telegram("\n".join(L))
