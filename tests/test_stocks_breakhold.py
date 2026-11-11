"""H-CONFLUENCE T2: دوال صافية لاختبار الكسر والثبات في بوت الشركات."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import stocks_breakhold_test as st


def test_outcome():
    assert st.outcome("tp", "0.9") == 0.5 and st.outcome("stopped", "-0.9") == -0.4 and st.outcome("open", "-0.2") == -0.2


def test_unheld_and_held_break():
    assert st.unheld_break("CALL", 101, 100, 90, 99, [99, 101]) is True          # كسر اليوم بلا ثبات
    assert st.unheld_break("CALL", 101, 100, 90, 100.5, [101]) is False          # أمس أغلق فوقه: ثابت
    assert st.held_break("CALL", 101, 100, 90, 100.5, [101]) is True
    assert st.unheld_break("CALL", 101, 100, 90, 99, [101] * 24) is False        # ساعتان فوقه: ثابت
    assert st.unheld_break("PUT", 89, 100, 90, 91, [91, 89]) is True


def test_chase_and_vwap():
    assert st.chase("PUT", -0.03, False) and not st.chase("PUT", -0.03, True) and not st.chase("PUT", -0.01, False)
    assert st.vwap_wrong("CALL", 99, 100) and not st.vwap_wrong("PUT", 99, 100) and not st.vwap_wrong("CALL", 99, None)
