"""بطاقة «قراءة السوق» لـ SPX (HTML ثم PNG). تصميم عين السوق، ولا تعدّل cards.py: تستورد منه الأدوات فقط.

d (dict) كما يبنيه market_read.gather():
  time_ry, spot, age, dir ('up'|'down'|'flat'), bull, bear, res/sup: (سعر، [أسماء]) أو None,
  rsi, adx, frames {"M5": +1/-1/0, ...}, ladder [{k, call, put, cv, pv, levels}] من الأعلى للأدنى,
  read {now, next, dest, invalid, confirm}, range (أدنى، أعلى) أو None, refs [(اسم، قيمة)], contracts, pc
"""
from cards import W, esc, ltr, logo_svg, render_png

GRN, RED, GOLD, CYAN, PINK, DIM = "#35d07f", "#ff6b73", "#ffd75a", "#4fe0ff", "#ff5c8a", "#8d95aa"
DIR = {"up": ("صعود", "▲ CALL", GRN), "down": ("هبوط", "▼ PUT", RED), "flat": ("محايد", "— انتظار", DIM)}


def _n(v, nd=2):
    return "–" if v is None else f"{v:,.{nd}f}"


def _ladder(d):
    rows = d.get("ladder") or []
    if not rows:
        return '<div class="empty">سلسلة العقود غير متاحة الآن.</div>'
    S = d["spot"]
    res = d["res"][0] if d.get("res") else None
    sup = d["sup"][0] if d.get("sup") else None
    step = abs(rows[0]["k"] - rows[1]["k"]) if len(rows) > 1 else 5
    vmax = max([r.get("cv", 0) + r.get("pv", 0) for r in rows] + [1])
    out, spot_done = [], False
    for r in rows:
        if not spot_done and r["k"] < S:
            out.append(f'<div class="spot"><span>الآن {ltr(_n(S))}</span></div>')
            spot_done = True
        tot = r.get("cv", 0) + r.get("pv", 0)
        w = max(2, tot / vmax * 100) if tot else 0
        col = GRN if r.get("cv", 0) >= r.get("pv", 0) else RED
        cls = ""
        if res is not None and abs(r["k"] - res) < step / 2:
            cls = " hr"
        elif sup is not None and abs(r["k"] - sup) < step / 2:
            cls = " hs"
        tags = "".join(f'<i>{esc(t)}</i>' for t in r.get("levels", [])[:2])
        vol = f"{tot / 1000:.1f}K" if tot >= 1000 else (f"{tot:.0f}" if tot else "")
        out.append(
            f'<div class="lr{cls}"><b class="pp">{ltr(_n(r.get("put")))}</b><b class="kk">{ltr(f"{r['k']:,.0f}")}</b>'
            f'<div class="bw"><div class="bar" style="width:{w:.0f}%;background:{col}"></div>'
            f'<span class="vl">{ltr(vol)}</span><span class="tg">{tags}</span></div>'
            f'<b class="cp">{ltr(_n(r.get("call")))}</b></div>')
    if not spot_done:
        out.append(f'<div class="spot"><span>الآن {ltr(_n(S))}</span></div>')
    exp = f" · انتهاء {ltr(d['exp'])}" if d.get("exp") else ""
    return ('<div class="lh"><span>PUT</span><span>السترايك</span>'
            f'<span>حجم العقود (كول أخضر / بوت أحمر) والمستويات{exp}</span>'
            '<span>CALL</span></div>' + "".join(out))


def _frames(fr):
    out = []
    for k, v in (fr or {}).items():
        c, t = (GRN, "CALL") if v > 0 else ((RED, "PUT") if v < 0 else (DIM, "—"))
        out.append(f'<span class="fr" style="border-color:{c};color:{c}">{ltr(k)} {ltr(t)}</span>')
    return "".join(out)


def card_html(d):
    name, badge, col = DIR[d.get("dir", "flat")]
    S = d["spot"]

    def box(num, title, color, price, note):
        return (f'<div class="bx" style="border-color:{color}"><div class="no" style="background:{color}">{ltr(num)}</div>'
                f'<div class="bt" style="color:{color}">{esc(title)}</div><div class="bp" style="color:{color}">{price}</div>'
                f'<div class="bn">{note}</div></div>')

    boxes = []
    if d.get("res"):
        p, nm = d["res"]
        boxes.append(box(1, "مقاومة لحظية", PINK, ltr(f"{p:,.0f}"),
                         f"فوق السعر بـ {ltr(f'{p - S:.2f}')} نقطة<br><small>{esc('، '.join(nm))}</small>"))
    if d.get("sup"):
        p, nm = d["sup"]
        boxes.append(box(2, "دعم لحظي", GOLD, ltr(f"{p:,.0f}"),
                         f"تحت السعر بـ {ltr(f'{S - p:.2f}')} نقطة<br><small>{esc('، '.join(nm))}</small>"))
    mom = f"{ltr('RSI ' + _n(d.get('rsi'), 1))}<br>{ltr('ADX ' + _n(d.get('adx'), 1))}"
    adx = d.get("adx")
    strength = "" if adx is None else ("اتجاه قوي" if adx >= 25 else ("اتجاه متوسط" if adx >= 20 else "اتجاه ضعيف"))
    pc = d.get("pc")
    pc_txt = "" if pc is None else f"<br>حجم الكول/البوت اليوم: {ltr(f'{pc:.2f}')}"
    boxes.append(box(3, "الزخم والسيولة", CYAN, f'<span style="font-size:34px;line-height:1.3">{mom}</span>',
                     f"{esc(strength)}{pc_txt}<br><small>السيولة اللحظية تحتاج بيانات لحظية (غير مشترك).</small>"))

    rd = d.get("read") or {}
    rng = d.get("range")
    refs = " · ".join((ltr(f"{k} {_n(v, 0)}") if k.isascii() else f"{esc(k)} {ltr(_n(v, 0))}")
                      for k, v in (d.get("refs") or []) if v)
    age = d.get("age")
    if age is not None and age > 60:
        age_txt = "السوق مغلق: آخر بيانات من الجلسة السابقة"
    else:
        age_txt = f"البيانات متأخرة ~{age if age is not None else 15:.0f} دقيقة"

    return f"""<!doctype html><html dir="rtl" lang="ar"><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:{W}px;background:#0b0e16;color:#eef1f8;font-family:'Noto Sans Arabic','DejaVu Sans',Tahoma,sans-serif}}
.hd{{display:flex;align-items:center;gap:18px;padding:20px 28px;background:linear-gradient(120deg,#8a6d12,#2a2208 55%,#0b0e16);border-bottom:2px solid #c9a227}}
.hd h1{{font-size:36px;color:{GOLD}}} .hd p{{font-size:17px;color:#e6d9a8;margin-top:4px}} .hd .tm{{margin-right:auto;text-align:left;font-size:15px;color:#e6d9a8}}
.dirs{{display:flex;align-items:center;gap:18px;margin:16px 24px 6px;padding:16px 22px;border:2px solid {col};border-radius:16px;background:#121726}}
.dirs .px{{font-size:32px;font-weight:700;flex:1}} .dirs .bd{{font-size:24px;font-weight:700;color:#0b0e16;background:{col};border-radius:10px;padding:4px 16px}}
.dirs .nm{{font-size:40px;font-weight:800;color:{col}}} .dirs .pt{{font-size:15px;color:{DIM}}}
.frs{{margin:4px 24px 0;display:flex;gap:8px;align-items:center;font-size:15px;color:{DIM}}}
.fr{{border:1.5px solid;border-radius:8px;padding:2px 10px;font-size:15px;font-weight:700}}
.main{{display:flex;gap:16px;padding:12px 24px}}
.side{{width:300px;display:flex;flex-direction:column;gap:16px}}
.bx{{position:relative;border:2.5px solid;border-radius:18px;background:#121726;padding:34px 14px 16px;text-align:center}}
.no{{position:absolute;top:-16px;right:50%;transform:translateX(50%);width:38px;height:38px;border-radius:50%;color:#0b0e16;font-size:22px;font-weight:800;line-height:38px}}
.bt{{font-size:26px;font-weight:700}} .bp{{font-size:58px;font-weight:800;margin:6px 0}} .bn{{font-size:18px;color:#d5dbea;line-height:1.5}}
.bn small{{font-size:14px;color:{DIM}}}
.lad{{flex:1;border:2px solid #2a8ca0;border-radius:16px;background:#0e1320;padding:8px 8px 10px;font-size:15px}}
.lh{{display:flex;gap:6px;color:{DIM};font-size:13px;padding:2px 4px 6px;border-bottom:1px solid #222a3d}}
.lh span:nth-child(1),.lh span:nth-child(4){{width:62px;text-align:center}} .lh span:nth-child(2){{width:58px;text-align:center}} .lh span:nth-child(3){{flex:1;text-align:center}}
.lr{{display:flex;gap:6px;align-items:center;height:27px;border-radius:6px;padding:0 4px}}
.lr.hr{{outline:2px solid {PINK};background:#2a1220}} .lr.hs{{outline:2px solid {GOLD};background:#2a2410}}
.pp{{width:62px;text-align:center;color:#ff9aa0;background:#2a1418;border-radius:4px}} .cp{{width:62px;text-align:center;color:#7fe6ad;background:#10291c;border-radius:4px}}
.kk{{width:58px;text-align:center;color:#eef1f8}}
.bw{{flex:1;position:relative;height:20px;background:#151b2a;border-radius:4px;overflow:hidden}}
.bar{{position:absolute;top:0;bottom:0;right:0;opacity:.55;border-radius:4px}}
.vl{{position:absolute;right:6px;top:1px;font-size:12px;color:#cfd6e6}}
.tg{{position:absolute;left:4px;top:1px;display:flex;gap:4px}} .tg i{{font-style:normal;font-size:12px;background:#0b0e16cc;border:1px solid #3a4560;border-radius:4px;padding:0 5px;color:#e6d9a8}}
.spot{{height:0;border-top:2.5px dashed {GOLD};margin:8px 0;position:relative}}
.spot span{{position:absolute;left:50%;top:-12px;transform:translateX(-50%);background:{GOLD};color:#0b0e16;font-weight:800;font-size:14px;border-radius:6px;padding:0 10px}}
.empty{{padding:40px;text-align:center;color:{DIM}}}
.how{{margin:4px 24px 0;border:2px solid #2a8ca0;border-radius:16px;background:#0e1320;padding:16px 20px}}
.how h2{{font-size:28px;margin-bottom:10px}} .g{{display:grid;grid-template-columns:1fr 1fr;gap:12px 22px}}
.g h3{{font-size:20px;margin-bottom:4px}} .g p{{font-size:17px;color:#d5dbea;line-height:1.55}}
.cf{{margin-top:12px;padding-top:10px;border-top:1px solid #222a3d;font-size:16px;color:#d5dbea;line-height:1.6}}
.ft{{margin:12px 24px 0;border:1.5px solid #222a3d;border-radius:14px;padding:12px 18px;font-size:16px;line-height:1.75;color:#d5dbea}}
.ft b{{color:{CYAN}}}
.dis{{display:flex;justify-content:space-between;padding:10px 24px 18px;font-size:13px;color:#6f7890}}
</style><body>
<div class="hd">{logo_svg(70)}<div><h1>قراءة السوق</h1><p>ملخص بصري للمستويات · SPX</p></div>
<div class="tm">{esc(d.get("time_ry", ""))}<br>{esc(age_txt)}</div></div>
<div class="dirs"><div class="px">SPX · {ltr(_n(S))}</div><div><div class="pt">اتجاه الجلسة · صعود {ltr(_n(d.get("bull"), 1))} / هبوط {ltr(_n(d.get("bear"), 1))}</div>
<div class="nm">{esc(name)}</div></div><div class="bd">{ltr(badge)}</div></div>
<div class="frs">الفريمات: {_frames(d.get("frames"))}</div>
<div class="main"><div class="lad">{_ladder(d)}</div><div class="side">{"".join(boxes)}</div></div>
<div class="how"><h2>كيف نقرأ هذه اللقطة؟</h2><div class="g">
<div><h3 style="color:{CYAN}">الآن</h3><p>{rd.get("now", "")}</p></div>
<div><h3 style="color:{CYAN}">التالي</h3><p>{rd.get("next", "")}</p></div>
<div><h3 style="color:{GOLD}">الوجهة المشروطة</h3><p>{rd.get("dest", "")}</p></div>
<div><h3 style="color:{PINK}">يتغيّر التقييم إذا</h3><p>{rd.get("invalid", "")}</p></div>
</div><div class="cf"><b>تأكيد القراءة:</b> {rd.get("confirm", "")}</div></div>
<div class="ft">
<div><b>النطاق المتوقع اليوم:</b> {ltr(f"{rng[0]:,.1f} — {rng[1]:,.1f}") if rng else "–"}</div>
<div><b>مراجع الجلسة:</b> {refs or "–"}</div>
<div><b>العقود:</b> {d.get("contracts", "–")}</div></div>
<div class="dis"><span>قراءة مشروطة بحركة السعر · ليست توصية دخول ولا نتيجة مضمونة · أسعار العقود متأخرة</span><span>عين السوق | Market Eye</span></div>
</body></html>"""


def card_png(d):
    return render_png(card_html(d))
