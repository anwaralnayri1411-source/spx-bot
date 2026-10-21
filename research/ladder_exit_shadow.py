#!/usr/bin/env python3
"""ظل «الأهداف المتدرجة» لخروج توصيات SPX (تقييم بعد الإغلاق؛ تسجيل فقط، لا يغيّر البوت ولا يرسل شيئاً).

السؤال: هل الخروج بأهداف متدرجة مع رفع الوقف (طريقة MGT كما وصفها المستخدم: هدف أول ثم أهداف أعلى، والوقف يرتفع
بعد كل هدف) أفضل من خروج البوت الحالي (+50% كاملاً)؟ أصله: تقرير MGT 20/10 — صفقات الكول بلغت +300% إلى +700%،
وقاعدتنا تكتفي بـ +50%.

لا يجمع بيانات جديدة: يقرأ مسارات أسعار البيع (bid) لعقود توصيات البوت التي يسجّلها ظلّان قائمان كل ~5 دقائق
حتى نهاية الجلسة (حتى بعد خروجهما):
  live  : live_shadow_track.csv      (Alpaca indicative اللحظي)
  yahoo : contract_shadow_track.csv  (ياهو، أسعار حقيقية متأخرة)
لكل عقد (البوت / على السعر / دلتا 0.30) نطبّق السياسات على المسار نفسه:
  fixed   : قاعدة البوت: +50% كاملاً، وقف −40% بعد 15 دقيقة من التوصية، بيع 15:30.
  ladder  : أهداف +50%، +100%، ثم كل +100%؛ الوقف −40% حتى الهدف الأول، ثم يرتفع إلى نقطة الدخول، ثم إلى الهدف السابق.
            خروج كامل عند الوقف المرفوع أو 15:30.
  thirds  : مثل ladder لكن نبيع ثلثاً عند +50% وثلثاً عند +100% والثلث الأخير يتبع الوقف المرفوع.
  mgt     : أهداف كل +1.00$ من سعر العقد (= +100$ للعقد، الهدف الأول كما وصفه المستخدم)، وقف أولي −50%،
            ثم إلى نقطة الدخول بعد الهدف الأول، ثم إلى الهدف السابق.
  hold    : الاحتفاظ حتى آخر تقييم في اليوم (مرجع).
⚠️ التقييم كل ~5 دقائق فقط: قمة بين قراءتين لا تُرى، فالنتائج متحفظة للسياسات المتدرجة. الأهداف تُعتبر محققة عند
أول قراءة تتجاوزها، والخروج بسعر تلك القراءة لا بسعر الهدف بالضبط.

الملفات (جديدة): ladder_exit_log.csv، research/ladder_exit_results.md
تشغيل: python research/ladder_exit_shadow.py
"""
import csv
import os
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SOURCES = {"live": os.path.join(ROOT, "live_shadow_track.csv"), "yahoo": os.path.join(ROOT, "contract_shadow_track.csv")}
SIGNALS = os.path.join(ROOT, "zero_dte_log.csv")
LOG = os.path.join(ROOT, "ladder_exit_log.csv")
REPORT = os.path.join(HERE, "ladder_exit_results.md")
TP, SL, MIN_AGE = 0.50, 0.40, 15          # قاعدة البوت (options_bot.TAKE_PROFIT / STOP_LOSS / MIN_AGE_MIN)
FINAL = (15, 30)
MGT_STEP, MGT_SL = 1.00, 0.50
POLICIES = ("fixed", "ladder", "thirds", "mgt", "hold")
LOG_COLS = ["date", "source", "pos_id", "variant", "entry", "sig_time", "first_time", "last_time", "quotes", "peak_pnl"] + \
           [f"{p}_{k}" for p in POLICIES for k in ("pnl", "out")]


def pct_targets():
    yield 0.50
    t = 1.00
    while True:
        yield t
        t += 1.00


def run_ladder(path, entry, can_stop, thirds=False):
    """path: [(وقت, pnl)]. يرجع (pnl للعقد كاملاً، سبب الخروج)."""
    gen, targets = pct_targets(), []
    nxt = next(gen)
    stop, sold, got = -SL, 0.0, 0.0           # sold: الجزء المباع، got: ربحه الموزون
    for t, p in path:
        while p >= nxt:                        # هدف (أو أكثر) تحقق في هذه القراءة
            targets.append(nxt)
            if thirds and len(targets) <= 2:
                got += p / 3
                sold += 1 / 3
            stop = 0.0 if len(targets) == 1 else targets[-2]
            nxt = next(gen)
        if (t.hour, t.minute) >= FINAL:
            return got + (1 - sold) * p, "final"
        if p <= stop and (targets or can_stop(t)):
            return got + (1 - sold) * p, "trail" if targets else "stop"
    return got + (1 - sold) * path[-1][1], "last"


def run_mgt(path, entry, can_stop):
    k, stop = 0, -MGT_SL
    for t, p in path:
        gain = p * entry                       # بالدولار للسهم
        while gain >= (k + 1) * MGT_STEP:
            k += 1
            stop = 0.0 if k == 1 else (k - 1) * MGT_STEP / entry
        if (t.hour, t.minute) >= FINAL:
            return p, "final"
        if p <= stop and (k or can_stop(t)):
            return p, "trail" if k else "stop"
    return path[-1][1], "last"


def run_fixed(path, can_stop):
    for t, p in path:
        if p >= TP:
            return p, "tp"
        if (t.hour, t.minute) >= FINAL:
            return p, "final"
        if p <= -SL and can_stop(t):
            return p, "stop"
    return path[-1][1], "last"


def sig_times():
    """pos_id (SPY:CALL:771:2026-10-21) ← وقت التوصية من zero_dte_log."""
    out = {}
    if not os.path.exists(SIGNALS):
        return out
    for r in csv.DictReader(open(SIGNALS, encoding="utf-8")):
        sym = "SPX" if r["symbol"] == "^SPX" else r["symbol"]
        try:
            k = f"{sym}:{r['side']}:{float(r['strike']):g}:{r['expiry']}"
            out[k] = datetime.fromisoformat(f"{r['date']}T{r['time']}")
        except Exception:
            continue
    return out


def load_paths():
    paths = defaultdict(list)
    for src, fn in SOURCES.items():
        if not os.path.exists(fn):
            continue
        for r in csv.DictReader(open(fn, encoding="utf-8")):
            try:
                t = datetime.fromisoformat(r["time"]).replace(tzinfo=None)
                paths[(src, r["pos_id"], r["variant"])].append((t, float(r["pnl"]), float(r["bid"])))
            except Exception:
                continue
    return paths


def evaluate():
    sigs, rows = sig_times(), []
    for (src, pid, var), pts in sorted(load_paths().items()):
        pts.sort()
        p0 = pts[0]
        entry = p0[2] / (1 + p0[1]) if p0[1] != -1 else None   # سعر الدخول من bid/(1+pnl)
        if not entry:
            continue
        sig = sigs.get(pid) or p0[0]
        can_stop = lambda t, s=sig: (t - s).total_seconds() / 60 >= MIN_AGE
        path = [(t, p) for t, p, _ in pts]
        res = {"fixed": run_fixed(path, can_stop), "ladder": run_ladder(path, entry, can_stop),
               "thirds": run_ladder(path, entry, can_stop, thirds=True), "mgt": run_mgt(path, entry, can_stop),
               "hold": (path[-1][1], "last")}
        row = {"date": p0[0].date().isoformat(), "source": src, "pos_id": pid, "variant": var, "entry": round(entry, 2),
               "sig_time": sig.strftime("%H:%M"), "first_time": p0[0].strftime("%H:%M"),
               "last_time": pts[-1][0].strftime("%H:%M"), "quotes": len(pts),
               "peak_pnl": round(max(p for _, p in path), 3)}
        for k, (pnl, out) in res.items():
            row[f"{k}_pnl"], row[f"{k}_out"] = round(pnl, 3), out
        rows.append(row)
    with open(LOG, "w", encoding="utf-8", newline="") as f:     # يُعاد بناؤه كاملاً من المسارات كل مرة
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        w.writeheader()
        w.writerows(rows)
    return rows


def report(rows):
    L = ["# ظل الأهداف المتدرجة — نتائج آلية", "",
         f"آخر تحديث: {datetime.now():%Y-%m-%d %H:%M}. تسجيل فقط؛ لا يغيّر خروج البوت.", "",
         "- fixed = قاعدة البوت (+50% كاملاً / −40% بعد 15د / 15:30). ladder = أهداف +50%، +100%، ثم كل +100% مع رفع الوقف.",
         "- thirds = ثلث عند +50% وثلث عند +100% والباقي يتبع الوقف. mgt = هدف كل +100$ للعقد، وقف أولي −50%. hold = حتى آخر تقييم.",
         "- النسب لكل عقد، والدولار = النسبة × سعر الدخول × 100. ⚠️ لا حكم قبل 20 توصية؛ القراءة كل ~5د فالمتدرج متحفظ.", ""]
    if not rows:
        L.append("لا مسارات بعد (تظهر بعد أول توصية SPX/SPY يتابعها live_shadow أو contract_shadow).")
    for src, title in (("live", "Alpaca اللحظي (live_shadow)"), ("yahoo", "ياهو (contract_shadow)")):
        for var in ("bot", "atm", "d30"):
            x = [r for r in rows if r["source"] == src and r["variant"] == var]
            if not x:
                continue
            L += [f"## {title} — عقد {var} ({len(x)} توصية، {len({r['date'] for r in x})} يوم)", "",
                  "| السياسة | متوسط % | متوسط $ للعقد | رابحة | أفضل | أسوأ |", "|---|---|---|---|---|---|"]
            for p in POLICIES:
                v = [r[f"{p}_pnl"] for r in x]
                usd = [r[f"{p}_pnl"] * r["entry"] * 100 for r in x]
                L.append(f"| {p} | {sum(v) / len(v) * 100:+.1f}% | {sum(usd) / len(usd):+.0f}$ | "
                         f"{sum(1 for a in v if a > 0) / len(v) * 100:.0f}% | {max(v) * 100:+.0f}% | {min(v) * 100:+.0f}% |")
            L.append("")
    if rows:
        L += ["## كل التوصيات (عقد البوت)", "", "| اليوم | المصدر | العقد | الدخول | القمة | fixed | ladder | thirds | mgt | hold |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            if r["variant"] != "bot":
                continue
            L.append(f"| {r['date']} | {r['source']} | {r['pos_id'].rsplit(':', 1)[0]} | {r['entry']} | {r['peak_pnl'] * 100:+.0f}% | " +
                     " | ".join(f"{r[f'{p}_pnl'] * 100:+.0f}% ({r[f'{p}_out']})" for p in POLICIES[:4]) +
                     f" | {r['hold_pnl'] * 100:+.0f}% |")
    L += ["", "الحكم: إن تفوّق ladder أو thirds على fixed بوضوح بالدولار بعد عينة كافية، نعرضه على المستخدم قبل أي تغيير للبوت."]
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    report(evaluate())
