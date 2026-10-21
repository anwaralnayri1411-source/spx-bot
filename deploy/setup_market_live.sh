#!/bin/bash
# تجهيز الصفحة الحية لبطاقة قراءة السوق (مرة واحدة، بصلاحية root). يعاد تشغيله بأمان.
# nginx للعرض فقط على المنفذ 80: لا فهرسة للمجلدات، وكل مسار غير /m/<رمز>/ يرجع 404. الرمز السري يُولَّد مرة واحدة
# ويُحفظ مع الرابط في /etc/spxbot/market_live.conf (يقرؤه market_live.py وmarket_read.py). لتغيير الرابط: احذف الملف وأعد التشغيل.
set -euo pipefail
CONF=/etc/spxbot/market_live.conf
DOCROOT=/var/www/spxbot

DEBIAN_FRONTEND=noninteractive apt-get install -y -q nginx >/dev/null

if [ ! -f "$CONF" ]; then
  tok=$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)
  ip=$(curl -4 -fsS --max-time 10 https://api.ipify.org || hostname -I | awk '{print $1}')
  printf 'dir=%s/m/%s\nurl=http://%s/m/%s/\n' "$DOCROOT" "$tok" "$ip" "$tok" > "$CONF"
fi
chown root:spxbot "$CONF" && chmod 640 "$CONF"

mkdir -p "$DOCROOT/m"
chown root:root "$DOCROOT" && chmod 755 "$DOCROOT"
chown spxbot:spxbot "$DOCROOT/m" && chmod 755 "$DOCROOT/m"

cat > /etc/nginx/sites-available/spxbot <<'EOF'
server {
  listen 80 default_server;
  listen [::]:80 default_server;
  server_name _;
  server_tokens off;
  root /var/www/spxbot;
  autoindex off;
  index index.html;
  add_header X-Robots-Tag "noindex, nofollow" always;
  add_header Cache-Control "no-store" always;
  add_header X-Content-Type-Options nosniff always;
  location /m/ { limit_except GET HEAD { deny all; } try_files $uri $uri/ =404; }
  location / { return 404; }
}
EOF
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/spxbot /etc/nginx/sites-enabled/spxbot
nginx -t -q
systemctl enable --now nginx >/dev/null 2>&1
systemctl reload nginx
ufw allow 80/tcp >/dev/null
echo "الصفحة الحية جاهزة: $(grep '^url=' "$CONF" | cut -d= -f2-)"
