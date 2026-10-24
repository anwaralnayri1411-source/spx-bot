#!/bin/bash
# تثبيت مؤقتات systemd لمهام البوت على الخادم (يُشغَّل بصلاحية root). يعيد كتابة الوحدات ولا يغيّر الوضع (shadow/live).
# المواعيد نسخة من cron في .github/workflows (بتوقيت UTC). عند تعديل موعد هناك عدّله هنا أيضاً.
set -euo pipefail
U=/etc/systemd/system

# job | OnCalendar (عدة مواعيد بفاصلة منقوطة)
SCHEDULE='
bot|Mon..Fri *-*-* 13..21:05,35:00
spx_monitor|Mon..Fri *-*-* 13..21:00/5:00
stocks|Mon..Fri *-*-* 14,15:20:00;Mon..Fri *-*-* 17,19:00:00;Mon..Fri *-*-* 13..21:12,42:00
commands|Mon..Fri *-*-* 13..21:03/15:00
preopen|Mon..Fri *-*-* 12,13:41:00;Mon..Fri *-*-* 13,14:03,23:00
contract_shadow|Mon..Fri *-*-* 13..21:02/5:00
exit_shadow|Mon..Fri *-*-* 13..21:03/5:00
blocked_shadow|Mon..Fri *-*-* 13..21:07/5:00
pro_rules_shadow|Mon..Fri *-*-* 13..21:08/5:00
structure_shadow|Mon..Fri *-*-* 13..21:09/5:00
candidates_shadow|Mon..Fri *-*-* 13..21:01/5:00
flow_shadow|Mon..Fri *-*-* 19,20:45:00
gex|Mon..Fri *-*-* 13:50:00;Mon..Fri *-*-* 21:45:00
shadow|Mon..Fri *-*-* 21:40:00
weekly|Fri *-*-* 21:30:00
news_shadow|Mon..Fri *-*-* 21:25:00
ladder_exit|Mon..Fri *-*-* 22:15:00
stocks_sector|Mon..Fri *-*-* 22:40:00
tasty_health|Mon..Fri *-*-* 13..20:07/15:00
market_read|Mon..Fri *-*-* 14,15:05:00;Mon..Fri *-*-* 17,18:00:00
market_live|Mon..Fri *-*-* 13..20:00/5:00
preopen_shadow|Mon..Fri *-*-* 12,13:55:00;Mon..Fri *-*-* 13,14:46:00;Mon..Fri *-*-* 20,21:20:00
stocks_watcher|Mon..Fri *-*-* 11..20:00/5:00
momentum_shadow|Mon..Fri *-*-* 11..20:00/5:00
live_watcher|Mon..Fri *-*-* 11..20:00/5:00
news_memory|Mon..Fri *-*-* 11..22:09/30:00
news_memory_eval|Mon..Fri *-*-* 22:50:00
news_score|Mon..Fri *-*-* 22:55:00
spx_cache|Mon..Fri *-*-* 22:20:00
tail_log|Mon..Fri *-*-* 22:30:00
real_price_check|Sat *-*-* 08:00:00
stocks_exit_study|Sat *-*-* 08:30:00
stocks_contract_replay|Sat *-*-* 08:45:00
weekly_learning|Sat *-*-* 09:00:00
strategy_lab|Sat *-*-1..7 10:00:00
levels_study|Sat *-*-1..7 10:30:00
signals_batch|Sat *-*-1..7 11:00:00
long_price_calibration|Sat *-*-1..7 12:00:00
'
# المهام الطويلة (آخر ثلاث): المؤقت يعيد تشغيلها كل 5 دقائق إن توقفت (حد 345 دقيقة في الكود)، ولا يبدأ نسخة ثانية
# إن كانت تعمل. run_job.sh يتجاهلها خارج 7:00–16:00 نيويورك.

mkdir -p /var/log/spxbot && chown spxbot:spxbot /var/log/spxbot
# هوية git لمستخدم البوت: بدونها يفشل pull --rebase (إعادة ترتيب الالتزام المحلي) فيضيع حفظ ذلك التشغيل
sudo -u spxbot -H git config --global user.name spx-bot-server
sudo -u spxbot -H git config --global user.email bot@users.noreply.github.com
# حفظ منتصف الجلسة داخل كود المهام الطويلة يستعمل pull --rebase عادياً: autoStash يمنع ملفاً متتبَّعاً معدَّلاً من إيقافه
sudo -u spxbot -H git config --global rebase.autoStash true
[ -f /etc/spxbot/runner.env ] || echo "SPXBOT_MODE=shadow" > /etc/spxbot/runner.env
chown root:spxbot /etc/spxbot/runner.env && chmod 640 /etc/spxbot/runner.env

cat > $U/spxbot@.service <<'EOF'
[Unit]
Description=spx-bot job %i
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=spxbot
EnvironmentFile=/etc/spxbot/env
EnvironmentFile=/etc/spxbot/runner.env
ExecStartPre=/bin/sh -c 'flock -w 120 /run/lock/spxbot-code.lock git -C /opt/spxbot/repo pull -q --ff-only origin main || true'
ExecStart=/opt/spxbot/repo/deploy/run_job.sh %i
TimeoutStartSec=6h
EOF

echo "$SCHEDULE" | while IFS='|' read -r job cal; do
  [ -n "$job" ] || continue
  {
    printf '[Unit]\nDescription=spx-bot timer %s\n\n[Timer]\n' "$job"
    IFS=';' read -ra parts <<< "$cal"
    for c in "${parts[@]}"; do printf 'OnCalendar=%s UTC\n' "$c"; done
    printf 'AccuracySec=1s\nPersistent=false\nUnit=spxbot@%s.service\n\n[Install]\nWantedBy=timers.target\n' "$job"
  } > "$U/spxbot-$job.timer"
done

# تحديث المكتبات أسبوعياً (GitHub يثبّت أحدث نسخة في كل تشغيل؛ ياهو يتغيّر فيحتاج yfinance حديثاً)
cat > $U/spxbot-pip.service <<'EOF'
[Unit]
Description=spx-bot weekly library update

[Service]
Type=oneshot
User=spxbot
ExecStart=/opt/spxbot/venv/bin/pip install -q -U yfinance pandas requests numpy anthropic playwright
ExecStart=/opt/spxbot/venv/bin/playwright install chromium
EOF
cat > $U/spxbot-pip.timer <<'EOF'
[Unit]
Description=spx-bot weekly library update

[Timer]
OnCalendar=Sat *-*-* 12:00:00 UTC

[Install]
WantedBy=timers.target
EOF

cat > /etc/logrotate.d/spxbot <<'EOF'
/var/log/spxbot/*.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
EOF

# مهام أُوقفت (24/10): نعطّل مؤقتاتها ونحذفها؛ كودها وسجلاتها ونتائجها تبقى في المستودع
for job in feed_compare live_shadow; do
  systemctl disable --now "spxbot-$job.timer" >/dev/null 2>&1 || true   # بعد حذفه في تثبيت سابق يفشل، فلا نوقف التثبيت
  rm -f "$U/spxbot-$job.timer"
done
systemctl daemon-reload
echo "$SCHEDULE" | while IFS='|' read -r job cal; do
  [ -n "$job" ] || continue
  systemctl enable --now "spxbot-$job.timer" >/dev/null 2>&1
done
systemctl enable --now spxbot-pip.timer >/dev/null 2>&1
echo "mode: $(cat /etc/spxbot/runner.env)"
systemctl list-timers 'spxbot-*' --no-pager
