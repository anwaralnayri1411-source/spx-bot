"""اختبارات سجل الاختبارات: عدّ صفوف الجداول، قراءة الأحكام وحالاتها، وتقرير مجلد مصطنع."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import study_registry as tr  # noqa: E402

MD = """# عنوان

| أ | ب |
|---|---|
| 1 | 2 |
| 3 | 4 |

نص

| ج | د |
|---|---|
| 5 | 6 |

⚖️ **H-A: تحققت (متوسط)**
⚖️ **H-B: لم تتحقق (يشمل الصفر)**
⚖️ **H-C: لا حكم بعد (3 من 20)**
⚖️ **H-D: عكسية**
"""


def test_count_rows_ignores_header_and_separator_per_table():
    assert tr.count_rows(MD) == 3
    assert tr.count_rows("لا جداول") == 0
    assert tr.count_rows("| a |\n|---|\n") == 0


def test_verdict_statuses_distinguish_not_confirmed_from_confirmed():
    st = [s for _, s in tr.verdicts(MD)]
    assert st == ["تحققت", "لم تتحقق", "ينتظر عيّنة", "عكسية"]


def test_scan_and_report_over_a_folder(tmp_path, monkeypatch):
    (tmp_path / "a_results.md").write_text(MD, encoding="utf-8")
    (tmp_path / "b_results.md").write_text("| x |\n|---|\n| 1 |\n| 2 |\n", encoding="utf-8")
    (tmp_path / "ignored.md").write_text(MD, encoding="utf-8")
    monkeypatch.setattr(tr, "OUT", str(tmp_path / "study_registry_results.md"))
    rows = tr.scan(str(tmp_path))
    assert [r["file"] for r in rows] == ["a_results.md", "b_results.md"]
    text = "\n".join(tr.report(rows))
    assert "مقارنات منفردة (صفوف جداول): **5**" in text and "فرضيات مسجّلة مسبقاً بأحكام: **4**" in text
    assert "ينتظر عيّنة 1" in text and "لم تتحقق 1" in text
    assert tr.main([str(tmp_path)]) == 0
    # التشغيل الثاني لا يعدّ ملف السجل نفسه
    assert [r["file"] for r in tr.scan(str(tmp_path))] == ["a_results.md", "b_results.md"]
