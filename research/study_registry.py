#!/usr/bin/env python3
"""سجل الاختبارات والمقارنات (قراءة فقط): كم مقارنة أجريناها فعلاً، وأي الفرضيات المسجّلة مسبقاً تحققت؟ بطلب المستخدم 24/10/2026.

لماذا: كل مقارنة إضافية ترفع احتمال أن «يبدو» شيء رابحاً صدفة (عند مستوى 5% يُتوقع خطأ واحد من كل 20 مقارنة بلا أي ميزة حقيقية). نقيس العدد الفعلي بدل التخمين.
يمسح research/*_results.md: صفوف الجداول = مقارنات منفردة (كل صف جدول بيانات)، وأسطر ⚖️ = أحكام الفرضيات المسجّلة مسبقاً (حالتها من نص الحكم).
المخرج: research/study_registry_results.md. تشغيل: python research/study_registry.py
"""
import glob
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "study_registry_results.md")
EXCLUDE = {"account_erosion_sim_results.md"}      # حساب رياضي لا اختبار فرضيات: لا يُعدّ ضمن المقارنات
STATUS = (("لم تتحقق", "لم تتحقق"), ("تحققت", "تحققت"), ("عكسية", "عكسية"), ("لا حكم بعد", "ينتظر عيّنة"), ("لا تقرير", "لا تقرير"))


def count_rows(text):
    """عدد صفوف البيانات في كل جداول markdown في النص (بلا الرأس وسطر الفاصل)."""
    n, in_table, skip = 0, False, 0
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("|"):
            if not in_table:
                in_table, skip = True, 2
            if skip:
                skip -= 1
            else:
                n += 1
        else:
            in_table, skip = False, 0
    return n


def verdicts(text):
    """[(نص الحكم، الحالة)] من أسطر ⚖️."""
    out = []
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("⚖️"):
            body = s.replace("⚖️", "").replace("**", "").strip()
            st = next((label for key, label in STATUS if key in body), "غير محدد")
            out.append((body, st))
    return out


def scan(folder=HERE):
    rows = []
    for p in sorted(glob.glob(os.path.join(folder, "*_results.md"))):
        name = os.path.basename(p)
        if name == os.path.basename(OUT) or name in EXCLUDE:
            continue
        try:
            text = open(p, encoding="utf-8").read()
        except OSError:
            continue
        rows.append({"file": name, "comparisons": count_rows(text), "verdicts": verdicts(text)})
    return rows


def report(rows):
    total = sum(r["comparisons"] for r in rows)
    pre = [(r["file"], v) for r in rows for v in r["verdicts"]]
    L = ["# سجل الاختبارات والمقارنات (آلي)", "",
         f"- ملفات النتائج: {len(rows)} | مقارنات منفردة (صفوف جداول): **{total}** | فرضيات مسجّلة مسبقاً بأحكام: **{len(pre)}**",
         f"- عند مستوى 5%، بلا أي ميزة حقيقية يُتوقع نحو **{total * 0.05:.0f}** نتيجة «مهمة» صدفة من هذه المقارنات وحدها. لذلك لا نعتمد رقماً استكشافياً منفرداً؛ نعتمد الفرضيات المسجّلة مسبقاً على أيام جديدة فقط.",
         "- العدّ تقريبي (صف جدول = مقارنة؛ بعض الصفوف وصفية لا اختبارية)، لكنه يعطي رتبة الحجم الحقيقية.", ""]
    L += ["## الفرضيات المسجّلة مسبقاً وحالتها", "", "| الملف | الحكم | الحالة |", "|---|---|---|"]
    for f, (body, st) in pre:
        L.append(f"| {f} | {body[:140].replace('|', '/')} | {st} |")
    if not pre:
        L.append("| - | لا أحكام بعد | - |")
    counts = {}
    for _, (_, st) in pre:
        counts[st] = counts.get(st, 0) + 1
    L += ["", "- ملخص الحالات: " + "، ".join(f"{k} {v}" for k, v in sorted(counts.items())) if counts else "- لا أحكام بعد.", "",
          "## المقارنات لكل ملف", "", "| الملف | مقارنات | أحكام |", "|---|---|---|"]
    for r in sorted(rows, key=lambda r: -r["comparisons"]):
        L.append(f"| {r['file']} | {r['comparisons']} | {len(r['verdicts'])} |")
    return L


def main(argv):
    rows = scan(argv[0] if argv else HERE)
    L = report(rows)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + f"\n(آخر تشغيل {datetime.now():%Y-%m-%d})\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
