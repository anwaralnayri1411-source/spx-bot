"""يرسل بطاقتين تجريبيتين ببيانات وهمية إلى تلجرام للتأكد من أن الصور تعمل في GitHub."""
import cards
import options_bot as ob

items = [
    {"name": "SPXW 7,753 CALL", "qty": 1, "entry": 2.83, "exit": 4.25, "status": "tp", "peak": 1.90, "pnl": 142},
    {"name": "SPY 772 PUT", "qty": 3, "entry": 1.00, "exit": 0.60, "status": "stop", "peak": 0.10, "pnl": -120},
    {"name": "SPXW 7,740 PUT", "qty": 2, "entry": 2.30, "exit": 2.45, "status": "flat", "peak": 0.20, "pnl": 30},
]
ob.send_photo(cards.daily_card("نموذج تجريبي، بيانات وهمية", items, 0.40, 0.50),
              "🧾 <b>نموذج التقرير اليومي</b> (بيانات وهمية للتجربة)")
board = [
    {"name": "WMT CALL 110", "sub": "الجمعة 16 أكتوبر", "entry": 1.29, "now": 2.04, "pnl": 0.58, "status": "tp"},
    {"name": "XOM CALL 172.5", "sub": "الجمعة 16 أكتوبر", "entry": 1.25, "now": 0.88, "pnl": -0.30, "status": "open"},
    {"name": "PLTR CALL 212.5", "sub": "الجمعة 16 أكتوبر", "entry": 1.77, "now": 1.01, "pnl": -0.43, "status": "stopped"},
]
ob.send_photo(cards.board_card("لوحة عقود الشركات", "نموذج تجريبي، بيانات وهمية", board, 0.40, 0.50),
              "📋 <b>نموذج لوحة العقود</b> (بيانات وهمية للتجربة)")
