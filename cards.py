"""بطاقات صور بسيطة (PNG) للتقرير اليومي ولوحة العقود. HTML ثم لقطة من Chrome (يتقن العربية).
الألوان: أخضر = رابح/هدف، أحمر = وقف/خسارة، كهرماني = قريب من الوقف، رمادي = بلا حسم."""
import html
import os

W = 980
ROW_STYLE = {"tp": ("#0f3d27", "#35d07f", "🎯"), "win": ("#0f3d27", "#35d07f", "🟢"),
             "stop": ("#4a1519", "#ff6b73", "🛑"), "loss": ("#4a1519", "#ff6b73", "🔴"),
             "warn": ("#4a3510", "#ffb340", "🟠"), "flat": ("#1d2230", "#aab2c5", "⏳")}


def esc(x):
    return html.escape(str(x))


def ltr(x):
    """يمنع انقلاب الإشارات والأرقام داخل النص العربي (مثل +58% أو ×3)."""
    return f'<bdi dir="ltr">{html.escape(str(x))}</bdi>'


def _money(v, sign=True):
    if v is None:
        return "–"
    s = f"{abs(v):,.0f}"
    if sign:
        return f"{'+' if v > 0 else ('-' if v < 0 else '')}${s}"
    return f"${s}"


def card_html(title, subtitle, tiles, rows, notes, columns):
    """tiles: [(label, value, color)] | rows: [dict(cells=[...], style=key)] | columns: [titles]"""
    tiles_html = "".join(
        f'<div class="tile" style="border-color:{c}"><div class="tl">{esc(l)}</div>'
        f'<div class="tv" style="color:{c}">{ltr(v)}</div></div>' for l, v, c in tiles)
    head = "".join(f"<th>{esc(c)}</th>" for c in columns)
    body = ""
    for r in rows:
        bg, fg, _ = ROW_STYLE.get(r.get("style", "flat"), ROW_STYLE["flat"])
        cells = "".join(f"<td>{c}</td>" for c in r["cells"])
        body += f'<tr style="background:{bg}"><td class="dot" style="color:{fg}">{ROW_STYLE.get(r.get("style","flat"))[2]}</td>{cells}</tr>'
    notes_html = "".join(f"<div class='note'>{esc(n)}</div>" for n in notes)
    return f"""<!doctype html><html dir="rtl" lang="ar"><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:{W}px;background:#0b0e16;color:#eef1f8;font-family:'Noto Sans Arabic','DejaVu Sans',Tahoma,sans-serif;padding:0}}
.hd{{background:linear-gradient(120deg,#8a6d12,#2a2208 55%,#0b0e16);padding:26px 30px;border-bottom:2px solid #c9a227}}
.hd h1{{font-size:30px;color:#ffd75a}} .hd p{{margin-top:6px;color:#e6d9a8;font-size:17px}}
.tiles{{display:flex;gap:14px;padding:20px 30px 6px}}
.tile{{flex:1;border:2px solid;border-radius:14px;padding:12px 14px;background:#121726}}
.tl{{font-size:15px;color:#aab2c5}} .tv{{font-size:30px;font-weight:700;margin-top:4px}}
table{{width:calc(100% - 60px);margin:14px 30px 6px;border-collapse:separate;border-spacing:0 6px}}
th{{background:linear-gradient(90deg,#f4b81a,#f58a1f);color:#1a1405;padding:10px 8px;font-size:16px}}
th:first-child{{border-radius:0 12px 12px 0}} th:last-child{{border-radius:12px 0 0 12px}}
td{{padding:12px 8px;text-align:center;font-size:19px}}
td:first-child{{border-radius:0 12px 12px 0;width:44px}} td:last-child{{border-radius:12px 0 0 12px;font-weight:700}}
.dim{{color:#8d95aa;font-size:16px}}
.note{{padding:3px 30px;color:#9aa3b8;font-size:15px}}
.ft{{padding:12px 30px 20px;color:#6f7890;font-size:14px}}
</style><body>
<div class="hd"><h1>{esc(title)}</h1><p>{esc(subtitle)}</p></div>
<div class="tiles">{tiles_html}</div>
<table><tr><th></th>{head}</tr>{body}</table>
{notes_html}
<div class="ft">تعليمي وليس توصية. الأسعار تقديرية من بيانات متأخرة، وقد يختلف تنفيذك.</div>
</body></html>"""


def render_png(html_text):
    """يرجع bytes لصورة PNG، أو يرفع استثناء إن تعذر Chrome."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        last = None
        browser = None
        for kw in ({}, {"channel": "chrome"}, {"executable_path": os.getenv("CHROME_PATH", "/usr/bin/google-chrome")}):
            try:
                browser = p.chromium.launch(args=["--no-sandbox"], **kw)
                break
            except Exception as e:
                last = e
        if browser is None:
            raise RuntimeError(f"تعذر تشغيل Chrome: {last}")
        page = browser.new_page(viewport={"width": W, "height": 10}, device_scale_factor=1.5)
        page.set_content(html_text)
        png = page.screenshot(full_page=True)
        browser.close()
        return png


def make_card(title, subtitle, tiles, rows, notes, columns):
    return render_png(card_html(title, subtitle, tiles, rows, notes, columns))


# ------------------------------------------------------------------ محتوى جاهز
COLS_DAILY = ["العقد", "الدخول", "الخروج", "الوقف", "الهدف", "أعلى سعر", "الربح"]


def daily_card(date_s, items, sl, tp, bot_name="SPX"):
    """items: dict(name, qty, entry, exit, status('tp'|'stop'|'flat'), peak(نسبة), pnl(دولار))"""
    rows, wins, losses, net, peak_net = [], 0, 0, 0.0, 0.0
    for it in items:
        e = it["entry"]
        st = {"tp": "tp", "stop": "stop"}.get(it["status"], "win" if it["pnl"] > 0 else ("loss" if it["pnl"] < 0 else "flat"))
        if st in ("tp", "win"):
            wins += 1
        elif st in ("stop", "loss"):
            losses += 1
        net += it["pnl"]
        peak_net += max(0.0, it["peak"] * e * 100 * it["qty"])
        rows.append({"style": st, "cells": [
            f"<b>{ltr(it['name'])}</b><div class='dim'>{ltr('×' + str(it['qty']))}</div>", f"{e:.2f}", f"{it['exit']:.2f}",
            f"{e * (1 - sl):.2f}", f"{e * (1 + tp):.2f}", f"<span class='dim'>{e * (1 + it['peak']):.2f}</span>",
            f"<span style='color:{'#35d07f' if it['pnl'] > 0 else ('#ff6b73' if it['pnl'] < 0 else '#aab2c5')}'>{ltr(_money(it['pnl']))}</span>"]})
    done = wins + losses
    tiles = [("الصافي بقاعدة الخروج", _money(net), "#35d07f" if net >= 0 else "#ff6b73"),
             ("رابحة", f"{wins}", "#35d07f"), ("خاسرة", f"{losses}", "#ff6b73"),
             ("نسبة النجاح", f"{wins / done * 100:.0f}%" if done else "–", "#6ea8ff")]
    notes = [f"النتيجة تُحسب بقاعدة الخروج الفعلية: هدف +{tp * 100:.0f}% أو وقف -{sl * 100:.0f}%، وليس بأعلى سعر.",
             f"لو حُسبت الأرباح عند القمم (غير قابل للتنفيذ) لظهر ربح إجمالي {peak_net:,.0f} دولار، ولذلك لا نعتمده."]
    return make_card(f"تقرير {bot_name} اليومي", date_s, tiles, rows, notes, COLS_DAILY)


COLS_BOARD = ["العقد", "الدخول", "الآن", "التغير", "الوقف", "الهدف"]


def board_card(title, subtitle, items, sl, tp):
    """items: dict(name, sub, entry, now, pnl(نسبة), status('open'|'tp'|'stopped'))"""
    rows, net_n, up, down, stopped = [], 0.0, 0, 0, 0
    for it in items:
        pn = it["pnl"]
        if it["status"] == "stopped":
            st, stopped = "stop", stopped + 1
        elif it["status"] == "tp":
            st, up = "tp", up + 1
        elif pn <= -0.25:
            st, down = "warn", down + 1
        elif pn >= 0:
            st, up = "win", up + 1
        else:
            st, down = "flat", down + 1
        net_n += (it["now"] - it["entry"]) * 100 * it.get("qty", 1)
        col = "#35d07f" if pn >= 0 else "#ff6b73"
        rows.append({"style": st, "cells": [
            f"<b>{ltr(it['name'])}</b><div class='dim'>{esc(it.get('sub', ''))}</div>", f"{it['entry']:.2f}", f"{it['now']:.2f}",
            f"<span style='color:{col}'>{ltr(f'{pn * 100:+.0f}%')}</span>", f"{it['entry'] * (1 - sl):.2f}", f"{it['entry'] * (1 + tp):.2f}"]})
    tiles = [("المجموع على الورق", _money(net_n), "#35d07f" if net_n >= 0 else "#ff6b73"),
             ("نشطة رابحة", f"{up}", "#35d07f"), ("نازلة", f"{down}", "#ffb340"), ("تحت الوقف", f"{stopped}", "#ff6b73")]
    return make_card(title, subtitle, tiles, rows, ["القيمة بسعر الوسط بين العرض والطلب، وقد تختلف عن سعر بيعك."], COLS_BOARD)
