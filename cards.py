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


# ------------------------------------------------------------------ الشعار وبطاقة العقد
def logo_svg(size=84):
    """شعار أصلي: حلقة ذهبية بعلامات، وعين تحليل زرقاء، وسهما CALL أخضر وPUT أحمر."""
    ticks = "".join(
        f'<line x1="50" y1="4" x2="50" y2="{11 if i % 3 else 15}" stroke="#d9ae2f" stroke-width="{2.4 if i % 3 else 3.4}" '
        f'transform="rotate({i * 15} 50 50)"/>' for i in range(24))
    return f"""<svg width="{size}" height="{size}" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
<defs><radialGradient id="g" cx="50%" cy="45%" r="55%"><stop offset="0" stop-color="#bff3ff"/><stop offset=".45" stop-color="#2bb3e6"/>
<stop offset="1" stop-color="#0a2a55"/></radialGradient>
<linearGradient id="r" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ffe27a"/><stop offset="1" stop-color="#b8860b"/></linearGradient></defs>
<circle cx="50" cy="50" r="46" fill="#0b1226" stroke="url(#r)" stroke-width="3"/>{ticks}
<path d="M14 50 Q50 20 86 50 Q50 80 14 50Z" fill="#101a38" stroke="url(#r)" stroke-width="2"/>
<circle cx="50" cy="50" r="15" fill="url(#g)"/><circle cx="50" cy="50" r="6.5" fill="#06122b"/><circle cx="46" cy="46" r="2.4" fill="#fff" opacity=".9"/>
<path d="M29 82 L38 68 L47 82Z" fill="#2fd27a"/><path d="M53 68 L71 68 L62 82Z" fill="#ff5e66"/>
</svg>"""


def _gauge(stop, entry, target, now=None):
    pts = [stop, entry, target] + ([now] if now is not None else [])
    lo, hi = min(pts), max(pts)
    pad = (hi - lo) * 0.12 or 0.1
    lo, hi = lo - pad, hi + pad

    def pos(v):
        return max(1.0, min(99.0, (v - lo) / (hi - lo) * 100))

    s, e, t = pos(stop), pos(entry), pos(target)
    mk = ""
    if now is not None:
        mk = (f'<div class="mk" style="left:{pos(now):.1f}%"><span>الآن<br>{ltr(f"{now:.2f}")}</span></div>')
    return f"""<div class="gg"><div class="bar">
<div class="z red" style="left:{s:.1f}%;width:{e - s:.1f}%"></div><div class="z grn" style="left:{e:.1f}%;width:{t - e:.1f}%"></div>
<div class="pt" style="left:{s:.1f}%;background:#ff6b73"></div><div class="pt" style="left:{e:.1f}%;background:#fff"></div>
<div class="pt" style="left:{t:.1f}%;background:#35d07f"></div>{mk}</div>
<div class="lb"><span style="left:{s:.1f}%;color:#ff8c93">وقف<br>{ltr(f"{stop:.2f}")}</span>
<span style="left:{e:.1f}%;color:#fff">دخول<br>{ltr(f"{entry:.2f}")}</span>
<span style="left:{t:.1f}%;color:#58e39a">هدف<br>{ltr(f"{target:.2f}")}</span></div></div>"""


def contract_card_html(kind, name, sub, price, chg, entry, stop, target, now=None, badge="", foot="", brand="Signal Desk"):
    """kind: 'entry' (توصية) | 'follow' (متابعة). chg: نسبة التغير بالنسبة لسعر الدخول أو None."""
    up = (chg or 0) >= 0
    col = "#35d07f" if up else "#ff6b73"
    chg_html = "" if chg is None else f'<div class="chg" style="color:{col}">{ltr(f"{chg * 100:+.0f}%")}</div>'
    label = "سعر الدخول" if kind == "entry" else "السعر الآن"
    badge_html = f'<div class="bd">{esc(badge)}</div>' if badge else ""
    return f"""<!doctype html><html dir="rtl" lang="ar"><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:{W}px;background:#0b0e16;color:#eef1f8;font-family:'Noto Sans Arabic','DejaVu Sans',Tahoma,sans-serif}}
.hd{{display:flex;align-items:center;gap:18px;padding:22px 30px;background:linear-gradient(120deg,#8a6d12,#2a2208 55%,#0b0e16);border-bottom:2px solid #c9a227}}
.hd h1{{font-size:34px;color:#ffd75a;flex:1}} .hd p{{font-size:18px;color:#e6d9a8;margin-top:4px}}
.bd{{background:#121726;border:2px solid {col};color:{col};border-radius:12px;padding:6px 16px;font-size:20px;font-weight:700}}
.mid{{display:flex;align-items:flex-end;gap:26px;padding:28px 36px 6px}}
.px{{font-size:104px;font-weight:700;line-height:1;color:{col}}} .lab{{font-size:19px;color:#aab2c5;margin-bottom:6px}}
.chg{{font-size:40px;font-weight:700;margin-bottom:12px}}
.gg{{padding:46px 56px 8px}} .bar{{position:relative;height:16px;background:#1b2132;border-radius:8px}}
.z{{position:absolute;top:0;height:16px}} .z.red{{background:#7a2328}} .z.grn{{background:#17794a}}
.pt{{position:absolute;top:-6px;width:6px;height:28px;border-radius:3px;transform:translateX(-3px)}}
.mk{{position:absolute;top:-34px;transform:translateX(-50%);text-align:center}}
.mk:after{{content:"";display:block;margin:2px auto 0;width:0;height:0;border:9px solid transparent;border-top-color:#ffd75a;border-bottom:0}}
.mk span{{display:block;font-size:17px;color:#ffd75a;font-weight:700;line-height:1.2}}
.lb{{position:relative;height:60px;margin-top:20px}} .lb span{{position:absolute;transform:translateX(-50%);text-align:center;font-size:19px;line-height:1.3}}
.ft{{padding:14px 30px 20px;color:#6f7890;font-size:14px;display:flex;justify-content:space-between}}
</style><body>
<div class="hd">{logo_svg(76)}<div style="flex:1"><h1>{esc(name)}</h1><p>{esc(sub)}</p></div>{badge_html}</div>
<div class="mid"><div><div class="lab">{label}</div><div class="px">{ltr(f"{price:.2f}")}</div></div>{chg_html}</div>
{_gauge(stop, entry, target, now)}
<div class="ft"><span>{esc(foot)}</span><span>{esc(brand)}</span></div>
</body></html>"""


def contract_card(kind, name, sub, price, chg, entry, stop, target, now=None, badge="", foot="", brand="Signal Desk"):
    return render_png(contract_card_html(kind, name, sub, price, chg, entry, stop, target, now, badge, foot, brand))


def logo_png(size=420):
    return render_png(f"""<!doctype html><meta charset="utf-8"><body style="margin:0;background:#0b0e16;display:flex;justify-content:center;align-items:center;width:{W}px;height:{size + 80}px">
{logo_svg(size)}</body>""")
