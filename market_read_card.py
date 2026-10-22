"""بطاقة «قراءة السوق» لـ SPX (HTML ثم PNG) بهوية عين السوق. لا تعدّل cards.py: تستورد منه الأدوات فقط.
تصميم 22/10 (بطلب المستخدم، بعد مقارنة ببطاقة MGT): القرار ومؤشر القوة أولاً، شريط ملخص، خريطة العقود مع شموع 5د
والمستويات، القراءة الفنية (خلاصة، مع/ضد، سيناريوهان بتفعيل وأهداف)، ثم خانات المراجع. يستخدمها market_read وmarket_live.

d (dict) كما يبنيه market_read.gather():
  time_ry, spot, age, exp, dir ('up'|'down'|'flat'), bull, bear, res/sup: (سعر، [أسماء]) أو None,
  rsi, adx, frames {"H1"/"M30"/"M15"/"M5": +1/-1/0}, ladder [{k, call, put, cv, pv, levels}] من الأعلى للأدنى,
  read {now, next, dest, invalid, confirm}, range (أدنى، أعلى) أو None, refs [(اسم، قيمة)], contracts, pc,
  plan {res, sup}: قوائم (سعر، [أسماء]), levels [(سعر، اسم، res|sup|ref)], candles [(o, h, l, c)] لشموع 5د اليوم,
  vwap, flip, walls (بوت، كول). المفاتيح الإضافية اختيارية: البطاقة تعمل بدونها (مثل --demo).
"""
from cards import esc, logo_svg, ltr, render_png

W = 1080
BG, PANEL, LINE = "#0a0d16", "#111726", "#222b40"
GOLD, GOLD2, GRN, RED, CYAN, DIM, TXT = "#e2b53c", "#ffe08a", "#2fd47f", "#ff5f6d", "#5ad8ff", "#8b93a7", "#eef1f8"


def n(v, d=2):
    return "–" if v is None else f"{v:,.{d}f}"


def strength(d):
    edge = abs((d.get("bull") or 0) - (d.get("bear") or 0))
    return edge, ("قوية" if edge >= 6 else ("متوسطة" if edge >= 3 else "ضعيفة"))


def hero(d):
    dir_ = d.get("dir", "flat")
    word, col, tag = {"up": ("صعود", GRN, "CALL"), "down": ("هبوط", RED, "PUT"), "flat": ("انتظار", GOLD, "لا دخول")}[dir_]
    edge, lab = strength(d)
    sc = max(-1.0, min(1.0, ((d.get("bull") or 0) - (d.get("bear") or 0)) / 10))
    pos = 50 + sc * 46                                   # % من اليسار (هبوط يسار، صعود يمين)
    fr = d.get("frames") or {}
    agree = [k for k, v in fr.items() if (v > 0 and dir_ == "up") or (v < 0 and dir_ == "down")]
    sub = (f"الفريمات {' · '.join(agree)} {'صاعدة' if dir_ == 'up' else 'هابطة'}" if agree and dir_ != "flat"
           else "الفريمات غير متفقة")
    return f"""<div class="hero" style="--c:{col}">
  <div class="dec"><div class="lb">القرار</div><div class="wd">{word}</div></div>
  <div class="mid"><div class="sb">{esc(sub)}</div>
    <span class="tag">{ltr(tag)} · إشارة {lab} <bdi dir="ltr">({edge:.1f})</bdi></span></div>
  <div class="px"><div class="lb">SPX الآن</div><div class="pv">{ltr(n(d['spot']))}</div></div>
  <div class="gauge"><div class="gb"></div><div class="gm" style="left:{pos:.1f}%"></div>
    <div class="gl"><span style="color:{RED}">هبوط قوي</span><span style="color:{GOLD}">انتظار</span><span style="color:{GRN}">صعود قوي</span></div></div>
</div>"""


def strip(d):
    cells = []
    for k in ("H1", "M30", "M15", "M5"):
        v = (d.get("frames") or {}).get(k, 0)
        c, a, t = (GRN, "▲", "صاعد") if v > 0 else ((RED, "▼", "هابط") if v < 0 else (DIM, "●", "بلا اتجاه"))
        cells.append(f'<div class="ce"><b style="color:{c}">{a} {ltr(k)}</b><i>{t}</i></div>')
    r, a = d.get("rsi"), d.get("adx")
    pc = d.get("pc")
    pct = ("كول أكثر" if pc and pc > 1.2 else ("بوت أكثر" if pc and pc < 0.8 else "متوازن"))
    pcc = GRN if pc and pc > 1.2 else (RED if pc and pc < 0.8 else GOLD)
    mom = "ميل صاعد" if r and r > 55 else ("ميل هابط" if r and r < 45 else "متوازن")
    momc = GRN if r and r > 55 else (RED if r and r < 45 else GOLD)
    cells2 = [f'<div class="ce"><b style="color:{momc}">{mom}</b><i>الزخم</i></div>',
              f'<div class="ce"><b>{ltr(f"RSI {n(r, 0)} · ADX {n(a, 0)}")}</b><i>{"اتجاه قوي" if a and a >= 25 else "اتجاه ضعيف"}</i></div>',
              f'<div class="ce"><b style="color:{pcc}">{pct}</b><i>حجم الكول/البوت {ltr(n(pc))}</i></div>']
    return f'<div class="strip"><div class="grp">{"".join(cells)}</div><div class="grp">{"".join(cells2)}</div></div>'


def chart(d):
    rows = d.get("ladder") or []
    if len(rows) < 3:
        return '<div class="panel"><div class="ph">خريطة العقود والمستويات</div><p class="empty">سلسلة العقود غير متاحة الآن.</p></div>'
    S = d["spot"]
    rh, top = 34, 34
    kmax, kmin = rows[0]["k"], rows[-1]["k"]
    step = abs(rows[0]["k"] - rows[1]["k"])
    H = top + rh * len(rows) + 10
    x0, x1 = 150, 940                                   # منطقة الرسم (يسار عمود البوت والسترايك، يمين عمود الكول)
    Wc = 1020

    def y(p):
        return top + (kmax - p) / step * rh + rh / 2

    vmax = max([r.get("cv", 0) + r.get("pv", 0) for r in rows] + [1])
    s = [f'<svg width="{Wc}" height="{H}" viewBox="0 0 {Wc} {H}" xmlns="http://www.w3.org/2000/svg" font-family="Noto Sans Arabic,DejaVu Sans,Tahoma">',
         f'<rect x="0" y="{top}" width="72" height="{rh * len(rows)}" fill="#2a1219"/>',
         f'<rect x="{x1}" y="{top}" width="{Wc - x1}" height="{rh * len(rows)}" fill="#0f2a1d"/>',
         f'<text x="36" y="22" fill="{RED}" font-size="15" text-anchor="middle" font-weight="700">PUT</text>',
         f'<text x="111" y="22" fill="{DIM}" font-size="14" text-anchor="middle">السترايك</text>',
         f'<text x="{(x1 + Wc) / 2}" y="22" fill="{GRN}" font-size="15" text-anchor="middle" font-weight="700">CALL</text>',
         f'<text x="{(x0 + x1) / 2}" y="22" fill="{DIM}" font-size="14" text-anchor="middle">حجم العقود اليوم · الشموع 5د · المستويات</text>']
    for i, r in enumerate(rows):
        yy = top + i * rh
        if i % 2 == 0:
            s.append(f'<rect x="{x0}" y="{yy}" width="{x1 - x0}" height="{rh}" fill="#0e1422"/>')
        tot = r.get("cv", 0) + r.get("pv", 0)
        if tot:
            w = (x1 - x0) * 0.62 * tot / vmax
            col = GRN if r.get("cv", 0) >= r.get("pv", 0) else RED
            s.append(f'<rect x="{x0}" y="{yy + 7}" width="{w:.0f}" height="{rh - 14}" rx="3" fill="{col}" opacity=".28"/>')
            s.append(f'<text x="{x0 + w + 6:.0f}" y="{yy + rh / 2 + 5}" fill="{DIM}" font-size="12">{tot / 1000:.1f}K</text>')
        s.append(f'<text x="36" y="{yy + rh / 2 + 5}" fill="#ffb0b5" font-size="14" text-anchor="middle">{n(r.get("put"))}</text>')
        s.append(f'<text x="111" y="{yy + rh / 2 + 5}" fill="{TXT}" font-size="15" text-anchor="middle" font-weight="700">{r["k"]:,.0f}</text>')
        s.append(f'<text x="{(x1 + Wc) / 2}" y="{yy + rh / 2 + 5}" fill="#9df0c0" font-size="14" text-anchor="middle">{n(r.get("call"))}</text>')
    # الشموع
    cs = d.get("candles") or []
    if cs:
        cw = (x1 - x0 - 230) / max(len(cs), 60)
        for j, (o, h, l, c) in enumerate(cs):
            cx = x0 + 8 + j * cw + cw / 2
            col = GRN if c >= o else RED
            yt, yb = y(max(o, c)), y(min(o, c))
            s.append(f'<line x1="{cx:.1f}" y1="{y(h):.1f}" x2="{cx:.1f}" y2="{y(l):.1f}" stroke="{col}" stroke-width="1.2"/>')
            s.append(f'<rect x="{cx - cw * 0.35:.1f}" y="{yt:.1f}" width="{max(cw * 0.7, 1.5):.1f}" height="{max(yb - yt, 1.2):.1f}" fill="{col}"/>')
    # المستويات
    lv = sorted([x for x in d.get("levels", []) if kmin - step / 2 <= x[0] <= kmax + step / 2], key=lambda x: -x[0])
    lanes = [-99.0, -99.0]
    for p, name, kind in lv:
        col = {"res": RED, "sup": GRN}.get(kind, GOLD)
        yy = y(p)
        s.append(f'<line x1="{x0}" y1="{yy:.1f}" x2="{x1}" y2="{yy:.1f}" stroke="{col}" stroke-width="1.2" stroke-dasharray="6 5" opacity=".75"/>')
        lane = 0 if yy - lanes[0] >= 22 else (1 if yy - lanes[1] >= 22 else None)
        if lane is None:
            continue
        lanes[lane] = yy
        txt = f"{' · '.join(name.split(' · ')[:2])} {p:,.1f}"
        wpx = 11 + 8.6 * len(txt)
        xr = x1 - 8 - lane * (wpx + 10) if lane == 0 else x1 - 8 - 250
        s.append(f'<rect x="{xr - wpx:.0f}" y="{yy - 11:.1f}" width="{wpx:.0f}" height="22" rx="11" fill="#0b0f1a" stroke="{col}"/>')
        s.append(f'<text x="{xr - wpx / 2:.0f}" y="{yy + 5:.1f}" fill="{col}" font-size="13" text-anchor="middle" direction="rtl">{esc(txt)}</text>')
    yS = y(S)
    s.append(f'<line x1="72" y1="{yS:.1f}" x2="{Wc}" y2="{yS:.1f}" stroke="{GOLD}" stroke-width="2.2"/>')
    s.append(f'<rect x="{x0 + 4}" y="{yS - 13:.1f}" width="128" height="26" rx="13" fill="{GOLD}"/>')
    s.append(f'<text x="{x0 + 68}" y="{yS + 5:.1f}" fill="#0b0e16" font-size="14" font-weight="800" text-anchor="middle">الآن {S:,.2f}</text>')
    s.append("</svg>")
    exp = f" · انتهاء {ltr(d['exp'])}" if d.get("exp") else ""
    return f'<div class="panel"><div class="ph"><span>خريطة العقود والمستويات{exp}</span><i>عين السوق</i></div>{"".join(s)}</div>'


def analyst(d):
    S, dir_ = d["spot"], d.get("dir", "flat")
    up, dn = [], []
    for k, v in (d.get("frames") or {}).items():
        (up if v > 0 else dn if v < 0 else []).append(f"فريم {k} {'صاعد' if v > 0 else 'هابط'}")
    r = d.get("rsi")
    if r is not None:
        (up if r > 55 else dn if r < 45 else []).append(f"RSI {r:.0f}")
    if d.get("vwap"):
        (up if S > d["vwap"] else dn).append(f"السعر {'فوق' if S > d['vwap'] else 'تحت'} VWAP {d['vwap']:,.0f}")
    pc = d.get("pc")
    if pc:
        (up if pc > 1.2 else dn if pc < 0.8 else []).append(f"حجم الكول/البوت {pc:.2f}")
    if d.get("flip"):
        (up if S > d["flip"] else dn).append(f"السعر {'فوق' if S > d['flip'] else 'تحت'} انقلاب الجاما {d['flip']:,.0f}")
    pl = d.get("plan") or {"res": [d["res"]] if d.get("res") else [], "sup": [d["sup"]] if d.get("sup") else []}
    R1 = pl["res"][0][0] if pl.get("res") else None
    S1 = pl["sup"][0][0] if pl.get("sup") else None
    _, lab = strength(d)
    summ = {"up": f"أميل إلى الصعود بقوة {lab}، ما دام السعر فوق {n(S1, 0)}.",
            "down": f"أميل إلى الهبوط بقوة {lab}، ما دام السعر تحت {n(R1, 0)}.",
            "flat": f"لا ترجيح واضح: ننتظر إغلاق شمعة 5د خارج {n(S1, 0)}–{n(R1, 0)}."}[dir_]

    def ul(xs, col, a):
        return "".join(f'<li><b style="color:{col}">{a}</b> {esc(x)}</li>' for x in xs) or '<li class="nn">لا دليل واضح الآن.</li>'

    def scen(kind):
        if kind == "up":
            act, tg = R1, [p for p, _ in pl.get("res", [])[1:3]]
            return (GRN, "سيناريو الصعود", act, f"إغلاق شمعة 5د فوق {n(act)}", tg, f"يلغى تحت {n(S1, 0)}")
        act, tg = S1, [p for p, _ in pl.get("sup", [])[1:3]]
        return (RED, "سيناريو الهبوط", act, f"إغلاق شمعة 5د تحت {n(act)}", tg, f"يلغى فوق {n(R1, 0)}")

    cards = []
    for kind in ("up", "down"):
        col, title, act, cond, tg, cancel = scen(kind)
        chips = "".join(f'<span class="chip">{ltr(n(t))}</span>' for t in tg) or '<span class="chip">–</span>'
        cards.append(f'<div class="sc" style="--c:{col}"><div class="st"><b>{title}</b><span>تفعيل<br><em>{ltr(n(act))}</em></span></div>'
                     f'<p>{esc(cond)}</p><div class="tg"><span class="tl">الأهداف</span>{chips}<span class="cx">{esc(cancel)}</span></div></div>')
    return f"""<div class="panel an"><div class="ph"><span>القراءة الفنية</span><i>{esc(d.get('time_ry', ''))}</i></div>
<div class="sum"><b>الخلاصة</b> {esc(summ)}</div>
<div class="two"><div class="col" style="--c:{GRN}"><h4>مع الصعود</h4><ul>{ul(up, GRN, '▲')}</ul></div>
<div class="col" style="--c:{RED}"><h4>مع الهبوط</h4><ul>{ul(dn, RED, '▼')}</ul></div></div>
<div class="two">{''.join(cards)}</div></div>"""


def tiles(d):
    rng = d.get("range")
    w = d.get("walls") or (None, None)
    t = [("النطاق المتوقع اليوم", f"{n(rng[0])} – {n(rng[1])}" if rng else "–"),
         ("الجداران (بوت · كول)", f"{n(w[0], 0)} · {n(w[1], 0)}"),
         ("انقلاب الجاما", n(d.get("flip"), 1)),
         ("VWAP", n(d.get("vwap"), 1))]
    return '<div class="tiles">' + "".join(f'<div class="ti"><i>{a}</i><b>{ltr(b)}</b></div>' for a, b in t) + "</div>"


def card_html(d):
    age = d.get("age")
    live = (f'<span class="dot"></span>بيانات متأخرة ~{age:.0f} د' if age and age < 120 else "السوق مغلق: آخر جلسة")
    return f"""<!doctype html><html dir="rtl" lang="ar"><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:{W}px;background:radial-gradient(1200px 500px at 85% -5%,#3a2c07 0,{BG} 55%);color:{TXT};font-family:'Noto Sans Arabic','DejaVu Sans',Tahoma,sans-serif;padding:26px 30px 18px}}
.hd{{display:flex;align-items:center;gap:16px;margin-bottom:18px}} .hd h1{{font-size:30px;color:{GOLD};letter-spacing:.5px}} .hd p{{color:#d9cfa8;font-size:16px;margin-top:2px}}
.hd .tm{{margin-right:auto;text-align:left;color:{DIM};font-size:15px;line-height:1.6}} .hd .tm b{{color:{TXT};font-size:24px}}
.dot{{display:inline-block;width:10px;height:10px;border-radius:50%;background:{GRN};box-shadow:0 0 10px {GRN};margin-left:6px}}
.hero{{position:relative;border:1.5px solid var(--c);border-radius:22px;padding:22px 26px 18px;background:linear-gradient(135deg,#131b2c,#0c111d);box-shadow:0 0 28px -8px var(--c);display:grid;grid-template-columns:auto 1fr auto;gap:8px 26px;align-items:center}}
.dec .lb,.px .lb{{color:{DIM};font-size:16px}} .dec .wd{{font-size:78px;font-weight:900;color:var(--c);line-height:1.05}}
.mid{{border-right:1px solid {LINE};padding-right:24px}} .mid .sb{{font-size:21px;font-weight:700;line-height:1.5}}
.tag{{display:inline-block;margin-top:10px;padding:5px 16px;border-radius:20px;background:color-mix(in srgb,var(--c) 18%,transparent);color:var(--c);font-weight:700;font-size:16px}}
.px{{text-align:left}} .px .pv{{font-size:46px;font-weight:800}}
.gauge{{grid-column:1/-1;position:relative;margin-top:12px;padding-top:6px}} .gb{{height:10px;border-radius:6px;background:linear-gradient(90deg,{RED},{GOLD} 50%,{GRN})}}
.gm{{position:absolute;top:-2px;width:26px;height:26px;margin-left:-13px;border-radius:50%;background:#0b0e16;border:4px solid #fff;box-shadow:0 0 12px #fff8}}
.gl{{display:flex;justify-content:space-between;flex-direction:row-reverse;font-size:15px;margin-top:8px;font-weight:700}}
.strip{{display:flex;gap:14px;margin:16px 0}} .grp{{flex:1;display:flex;border:1px solid {LINE};border-radius:16px;background:{PANEL}}}
.ce{{flex:1;text-align:center;padding:10px 4px;border-left:1px solid {LINE}}} .ce:last-child{{border-left:0}} .ce b{{display:block;font-size:19px}} .ce i{{font-style:normal;color:{DIM};font-size:13px}}
.panel{{border:1px solid {LINE};border-radius:20px;background:{PANEL};padding:14px 10px 10px;margin-bottom:16px}}
.ph{{display:flex;justify-content:space-between;padding:0 12px 8px;font-size:20px;font-weight:800}} .ph i{{font-style:normal;color:{DIM};font-size:14px;font-weight:400}}
.empty{{color:{DIM};padding:20px}}
.an{{padding:16px 18px}} .sum{{border:1px solid #4a3d12;background:#1a1606;border-radius:14px;padding:12px 16px;font-size:21px;font-weight:700;margin:4px 0 14px}} .sum b{{color:{GOLD};margin-left:8px}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}}
.col{{border:1px solid color-mix(in srgb,var(--c) 35%,{LINE});border-radius:14px;padding:12px 16px;background:color-mix(in srgb,var(--c) 6%,#0e1320)}}
.col h4{{color:var(--c);font-size:19px;margin-bottom:6px}} .col ul{{list-style:none}} .col li{{font-size:17px;line-height:1.75;color:#d7ddea}} .col li.nn{{color:{DIM}}}
.sc{{border:1.5px solid var(--c);border-radius:16px;padding:14px 16px;background:color-mix(in srgb,var(--c) 8%,#0e1320)}}
.st{{display:flex;justify-content:space-between;align-items:flex-start}} .st b{{color:var(--c);font-size:21px}} .st span{{color:{DIM};font-size:13px;text-align:left}}
.st em{{font-style:normal;color:var(--c);font-size:34px;font-weight:800}} .sc p{{font-size:16px;color:#d7ddea;margin:4px 0 10px}}
.tg{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}} .tl{{color:{DIM};font-size:14px}} .chip{{background:#1c2436;border:1px solid {LINE};border-radius:10px;padding:3px 10px;font-weight:800;font-size:16px}}
.cx{{margin-right:auto;color:{DIM};font-size:13px}}
.tiles{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:12px}} .ti{{border:1px solid {LINE};border-radius:14px;background:{PANEL};padding:12px;text-align:center}}
.ti i{{font-style:normal;color:{DIM};font-size:14px;display:block}} .ti b{{font-size:20px}}
.ft{{display:flex;justify-content:space-between;color:{DIM};font-size:13px;padding-top:4px}} .ft b{{color:{GOLD}}}
</style><body>
<div class="hd">{logo_svg(64)}<div><h1>عين السوق · قراءة السوق</h1><p>SPX · خريطة المستويات والعقود</p></div>
<div class="tm"><b>{esc((d.get('time_ry') or '').split(' ')[1] if d.get('time_ry') else '')}</b> الرياض<br>{live}</div></div>
{hero(d)}{strip(d)}{chart(d)}{analyst(d)}{tiles(d)}
<div class="ft"><span>قراءة مشروطة بحركة السعر · ليست توصية دخول · المستويات والعقود متأخرة ~15 د</span><b>عين السوق | Market Eye</b></div>
</body></html>"""


def card_png(d):
    return render_png(card_html(d))
