"""تقييم قائمة المراقبة (مشروع الشركات فقط، لا علاقة له بـ SPX).

يقرأ stocks_watch_log.csv (أسعار تأخذها من منصتك وتضيفها يدوياً) ويقارنها بسعر دخول البوت
المسجل في stocks_positions.json، ثم يطبع جدولاً: التغير عن دخول البوت، أعلى/أدنى سعر شوهد،
ماذا كانت ستفعل قاعدة البوت (جني +50% / وقف -40%)، وحالة البوت نفسه.

تشغيل:  python stocks_watch_eval.py

حدود صريحة:
- القراءات متفرقة (لقطات)، فنعرف بلوغ الهدف أو الوقف فقط إن ظهر في إحدى اللقطات. قد يفوتنا ما حدث بينها.
- الأسعار من منصتك (آخر سعر تداول). العقد قليل السيولة قد يُظهر سعراً قديماً.
- صف kind=variant (عقد غيّرته أنت) أو unknown لا يُحسب على البوت.
- عينة صغيرة: لا تُبنى عليها أحكام قبل 20 توصية على الأقل.
"""
import csv
import json

TP, SL = 0.50, 0.40
LOG = "stocks_watch_log.csv"
POS = "stocks_positions.json"


def load_positions():
    try:
        data = json.load(open(POS, encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for p in data.get("positions", []):
        out[(p["ticker"], p["exp"], float(p["strike"]), p["side"])] = p   # آخر توصية لنفس العقد تُبقى
    return out


def main():
    pos = load_positions()
    rows = list(csv.DictReader(open(LOG, encoding="utf-8")))
    groups = {}
    for r in rows:
        key = (r["ticker"], r["exp"], float(r["strike"]), r["side"])
        groups.setdefault(key, []).append(r)

    print("| العقد | دخول البوت | آخر سعر | التغير | أعلى/أدنى شوهد | بقاعدة البوت | حالة البوت |")
    print("|---|---|---|---|---|---|---|")
    net = 0.0
    n_bot = n_tp = n_sl = 0
    for key, g in sorted(groups.items()):
        t, exp, k, side = key
        prices = [float(x["price"]) for x in g]
        last = prices[-1]
        p = pos.get(key)
        kind = g[-1]["kind"]
        name = f"{t} {k:g} {exp[5:]}"
        if kind != "bot" or not p:
            tag = "بديل (عدّلته أنت)" if kind == "variant" else "بلا سجل في البوت"
            print(f"| {name} | - | {last:.2f} | - | {max(prices):.2f}/{min(prices):.2f} | - | {tag} |")
            continue
        entry = float(p["entry"])
        chg = last / entry - 1
        hit = "لم يُحسم"
        for x in prices:                       # أول لقطة تصل الهدف أو الوقف
            if x >= entry * (1 + TP):
                hit = "🎯 هدف"
                break
            if x <= entry * (1 - SL):
                hit = "🛑 وقف"
                break
        n_bot += 1
        if hit.startswith("🎯"):
            n_tp += 1
            net += entry * TP * 100
        elif hit.startswith("🛑"):
            n_sl += 1
            net -= entry * SL * 100
        print(f"| {name} | {entry:.2f} | {last:.2f} | {chg * 100:+.0f}% | {max(prices):.2f}/{min(prices):.2f} | {hit} | {p['status']} |")
    print()
    print(f"عقود البوت المقيّمة: {n_bot} | هدف: {n_tp} | وقف: {n_sl} | بلا حسم: {n_bot - n_tp - n_sl}")
    print(f"صافي قاعدة البوت على اللقطات (نظري، لكل عقد واحد): {net:+.0f}$")
    print("⚠️ عينة صغيرة ولقطات متفرقة؛ ليست إثباتاً لربحية ولا لخسارة.")


if __name__ == "__main__":
    main()
