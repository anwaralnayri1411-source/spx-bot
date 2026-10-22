#!/bin/bash
# تثبيت حارس الصحة كمؤقت systemd على الخادم (بصلاحية root). مستقل عن deploy/install.sh حتى لا نلمس مواعيد المهام.
# كل 5 دقائق يشغّل deploy/bot_watchdog.py بنفس مفاتيح وضع البوتات (/etc/spxbot/env و/etc/spxbot/runner.env).
set -euo pipefail
U=/etc/systemd/system

cat > $U/spxbot-watchdog.service <<'EOF'
[Unit]
Description=spx-bot watchdog (alerts if a bot stops silently)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=spxbot
EnvironmentFile=/etc/spxbot/env
EnvironmentFile=/etc/spxbot/runner.env
ExecStart=/opt/spxbot/venv/bin/python /opt/spxbot/repo/deploy/bot_watchdog.py
TimeoutStartSec=120
EOF

cat > $U/spxbot-watchdog.timer <<'EOF'
[Unit]
Description=spx-bot watchdog every 5 minutes

[Timer]
OnCalendar=*-*-* *:00/5:30 UTC
AccuracySec=1s
Persistent=false

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now spxbot-watchdog.timer >/dev/null 2>&1
echo "mode: $(cat /etc/spxbot/runner.env)"
systemctl list-timers 'spxbot-watchdog*' --no-pager
