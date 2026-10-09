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

ob.send_photo(cards.contract_card("entry", "SPXW 7,815 CALL", "انتهاء اليوم · SPX 7,809", 2.00, None, 2.00, 1.20, 3.00,
                                  badge="CALL 🟢", foot="نموذج تجريبي، بيانات وهمية"),
              "✅ <b>نموذج توصية دخول</b> (بيانات وهمية)\n💵 بسعر حتى <b>2.00</b> | تكلفة <b>$200</b>\n🎯 الهدف <b>3.00</b> (+50%)\n🛑 الوقف <b>1.20</b> (-40%)")
ob.send_photo(cards.contract_card("follow", "MSTR 162.5 PUT", "انتهاء 09 Oct", 4.60, 0.59, 2.89, 1.73, 4.34, now=4.60,
                                  badge="🟢 يقترب من الهدف", foot="نموذج تجريبي، بيانات وهمية"),
              "📌 <b>نموذج متابعة عقد</b> (بيانات وهمية)\n💵 دخول 2.89 ← الآن 4.60 | <b>+59%</b>")
ob.send_photo(cards.logo_png(), "🪪 <b>مقترح شعار البوت</b> (تصميم أصلي، قابل للتغيير)")
