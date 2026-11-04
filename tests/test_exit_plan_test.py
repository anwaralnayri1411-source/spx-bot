"""اختبارات طرق الخروج حول الأرباح (H-EXITPLAN). لا شبكة."""
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import exit_plan_test as xp  # noqa: E402

T0 = datetime(2026, 11, 2, 10, 30, tzinfo=xp.NY)


def _p(vals, step_min=60):
    """مسار مزيف: (وقت، قيمة، عمر)."""
    return [(T0 + timedelta(minutes=step_min * (i + 1)), v, step_min * (i + 1)) for i, v in enumerate(vals)]


CUT1 = T0 + timedelta(days=5)
CUT2 = T0 + timedelta(days=6)


def test_method_a():
    assert xp.method_a(_p([0.1, 0.6, -0.5]), CUT1) == 0.5
    assert xp.method_a(_p([0.1, -0.45, 0.6]), CUT1) == -0.4
    assert xp.method_a(_p([0.1, 0.2]), CUT1) == 0.2


def test_method_b_two_stages():
    assert xp.method_b(_p([0.55, 1.1]), CUT1) == (0.5 + 1.0) / 2        # الأول +50% والثاني +100%
    assert xp.method_b(_p([0.55, 0.2, -0.05]), CUT1) == (0.5 + 0.0) / 2  # الثاني يخرج عند التعادل
    assert xp.method_b(_p([0.55, 0.3]), CUT1) == (0.5 + 0.3) / 2         # الثاني يُغلق قبل الإعلان
    assert xp.method_b(_p([-0.45]), CUT1) == -0.4


def test_method_c_house_money():
    late = [(CUT1 + timedelta(hours=20), -0.7, 9999)]                       # بعد الإعلان
    assert xp.method_c(_p([1.05]) + late, CUT1, CUT2) == (1.0 - 0.7) / 2    # الأول ضاعف والثاني انهار بعد الإعلان
    assert xp.method_c(_p([0.3]) + late, CUT1, CUT2) == 0.3                 # لم يتضاعف: كلاهما قبل الإعلان
    assert xp.method_c(_p([-0.5]) + late, CUT1, CUT2) == -0.4


def test_method_d_through():
    late = [(CUT1 + timedelta(hours=20), -0.7, 9999)]
    assert xp.method_d(_p([0.3]) + late, CUT1, CUT2) == -0.7
    assert xp.method_d(_p([-0.5]) + late, CUT1, CUT2) == -0.4


def test_plan_event_calendar():
    tdays = [date(2026, 10, 19) + timedelta(days=k) for k in range(30)]
    tdays = [d for d in tdays if d.weekday() < 5]
    pl = xp.plan_event(tdays, date(2026, 11, 5))                        # الخميس
    en, ex, pre, post = pl
    assert ex == date(2026, 11, 4) and en == date(2026, 10, 27)
    assert pre == date(2026, 10, 30) and post == date(2026, 11, 6)
    assert xp.plan_event(tdays, date(2026, 10, 20)) is None
