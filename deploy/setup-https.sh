#!/usr/bin/env bash
# Домен без :8000 + Let's Encrypt.
# Запуск на сервере от root:
#   sudo bash deploy/setup-https.sh liqscope.online you@email.com
set -euo pipefail

DOMAIN="${1:-liqscope.online}"
EMAIL="${2:-}"
APP="127.0.0.1:8000"
HERE="$(cd "$(dirname "$0")" && pwd)"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "нужен root: sudo bash $0 $DOMAIN [email]"
    exit 1
fi

echo "== DNS =="
RESOLVED="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk '{print $1; exit}')"
echo "  $DOMAIN → ${RESOLVED:-не резолвится}"
if [[ -z "$RESOLVED" ]]; then
    echo "сначала A-запись $DOMAIN на IP сервера (сейчас 78.17.66.215)."
    exit 1
fi

echo "== пакеты =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx certbot python3-certbot-nginx

mkdir -p /var/www/certbot
# uvicorn должен слушать localhost:8000, чтобы снаружи был только nginx.
# Юнит пока может быть на 0.0.0.0:8000 — nginx всё равно проксирует.
# Если приложение не на 8000 — скрипт упадёт на проверке.
echo "== приложение на $APP =="
if ! curl -fsS -m 5 "http://$APP/api/health" >/dev/null; then
    echo "uvicorn не отвечает на http://$APP — сначала поднимите licvid.service"
    exit 1
fi

echo "== nginx HTTP =="
rm -f /etc/nginx/sites-enabled/default
cp "$HERE/nginx-liqscope.http.conf" /etc/nginx/sites-available/liqscope
ln -sfn /etc/nginx/sites-available/liqscope /etc/nginx/sites-enabled/liqscope
# подставить домен, если вызывали не с дефолтом
sed -i "s/liqscope.online/${DOMAIN}/g" /etc/nginx/sites-available/liqscope
nginx -t
systemctl enable --now nginx
systemctl reload nginx

echo "== Let's Encrypt =="
CERTBOT_EXTRA=(--nginx --agree-tos --redirect --non-interactive)
if [[ -n "$EMAIL" ]]; then
    CERTBOT_EXTRA+=(--email "$EMAIL")
else
    CERTBOT_EXTRA+=(--register-unsafely-without-email)
    echo "email не задан — сертификат без уведомлений об истечении"
fi

DOMAINS=(-d "$DOMAIN")
if getent ahostsv4 "www.$DOMAIN" >/dev/null 2>&1; then
    DOMAINS+=(-d "www.$DOMAIN")
fi

certbot "${CERTBOT_EXTRA[@]}" "${DOMAINS[@]}"

# итоговый конфиг с WS-таймаутами (certbot мог упростить location /)
cp "$HERE/nginx-liqscope.conf" /etc/nginx/sites-available/liqscope
sed -i "s/liqscope.online/${DOMAIN}/g" /etc/nginx/sites-available/liqscope
nginx -t
systemctl reload nginx

echo
echo "готово: https://${DOMAIN}/"
echo
echo "в systemd-юнит добавьте и перезапустите сервис:"
echo "  Environment=LIQSCOPE_PUBLIC_URL=https://${DOMAIN}"
echo "  Environment=LIQSCOPE_COOKIE_SECURE=1"
echo "  Environment=LIQSCOPE_SECRET=\$(openssl rand -hex 32)   # в drop-in, не в git"
echo "  ExecStart=... --host 127.0.0.1 --port 8000"
echo
echo "порт 80 и 443 должны быть открыты (ufw allow 80,443/tcp)."
echo "у BotFather: /setdomain ${DOMAIN}"
