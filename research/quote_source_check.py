#!/usr/bin/env python3
"""فحص مصدر أسعار عقود SPXW (H-STRENGTHPICK، 23/11/2026): في اللحظة نفسها، طلب ياهو والسلسلة اللحظية (overlay ومصدرها)
وTastytrade لعقود قريبة من السعر. تشغيل أثناء الجلسة على الخادم:
  sudo -E -u spxbot /opt/spxbot/venv/bin/python research/quote_source_check.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import live_quotes as lq  # noqa: E402
import options_bot as ob  # noqa: E402
import yfinance as yf  # noqa: E402


def main():
    n = ob.now_ny()
    t = yf.Ticker("^SPX")
    exp = n.date().isoformat()
    ch = t.option_chain(exp)
    S = ob._live_spot("SPX", float(t.history(period="1d", interval="1m")["Close"].iloc[-1]))
    print(f"{n:%H:%M:%S} نيويورك | SPX {S:,.2f} | انتهاء {exp}")
    print("العقد | ياهو | لحظي (المصدر) | Tastytrade | فرق اللحظي عن Tastytrade")
    for side, df in (("C", ch.calls), ("P", ch.puts)):
        df = ob.spxw_rows(df)
        band = df[(df["strike"] - S).abs() <= S * 0.012].copy()
        ov = ob._live_chain(band, S)
        for _, r in ov.sort_values("strike").iterrows():
            K = float(r["strike"])
            y = band[band["strike"] == K]["ask"].iloc[0]
            tq = lq.tasty_quote(lq.occ("SPXW", exp, "CALL" if side == "C" else "PUT", K))
            ta = tq.get("ask") if tq and tq.get("status") == "ok" else None
            d = f"{(r['ask'] / ta - 1) * 100:+.1f}%" if ta else "—"
            print(f"{K:,.0f}{side} | {y:.2f} | {r['ask']:.2f} ({r.get('live_src', '?')}) | {ta if ta is not None else '—'} | {d}")


if __name__ == "__main__":
    main()
