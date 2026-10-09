#!/usr/bin/env bash
# Домен без :8000 + Let's Encrypt.
# Запуск на сервере от root:
#   sudo bash deploy/setup-https.sh liqscope.online you@email.com
set -euo pipefail

DOMAIN="${1:-liqscope.online}"
EMAIL="${2:-}"
APP="127.0.0.1:8000"
HERE="$(cd "$(dirname "$0")" && pwd)"
# Каталог приложения: nginx отдаёт статику с диска (location /static/), поэтому
# путь в конфиге должен совпадать с WorkingDirectory юнита licvid.service.
APP_DIR="$(cd "$HERE/.." && pwd)"
STATIC_DIR="$APP_DIR/static"

# Поставить конфиг в sites-available: подставляем домен и фактический путь к
# static (в файле он записан как /root/Licvid/static/ — рабочий расклад юнита).
install_conf() {
    local src="$1"
    cp "$src" /etc/nginx/sites-available/liqscope
    sed -i "s/liqscope.online/${DOMAIN}/g" /etc/nginx/sites-available/liqscope
    sed -i "s#alias /root/Licvid/static/#alias ${STATIC_DIR}/#g" \
        /etc/nginx/sites-available/liqscope
    nginx -t
}

# Рабочий nginx (www-data) должен читать static, иначе вместо файлов будет 403
# и каждый запрос уйдёт в приложение запасным путём (@static_app).
check_static_perms() {
    local probe="$STATIC_DIR/app.js"
    local runner="www-data"
    id "$runner" >/dev/null 2>&1 || runner="nobody"
    if su -s /bin/sh "$runner" -c "test -r '$probe'" 2>/dev/null; then
        echo "  ok: $runner читает $STATIC_DIR — статику отдаёт nginx"
    else
        echo "  ВНИМАНИЕ: $runner не читает $probe"
        echo "  Сайт при этом работает: nginx отдаст статику через приложение"
        echo "  (@static_app), но каждый .js/.css/.png пойдёт в Python — а весь"
        echo "  смысл микрокэша в том, чтобы разгрузить единственный воркер."
        echo "  Откройте каталог на чтение nginx (вариант 1 — только группе,"
        echo "  /root остаётся закрытым для всех остальных):"
        echo "    chgrp $runner $(dirname "$APP_DIR") $APP_DIR   # без -R: только проход по каталогам"
        echo "    chmod g+x $(dirname "$APP_DIR") $APP_DIR"
        echo "    chgrp -R $runner $STATIC_DIR && chmod -R g+rX $STATIC_DIR"
        echo "  вариант 2 — всем локальным пользователям (проще, но шире):"
        echo "    chmod o+x $(dirname "$APP_DIR") $APP_DIR"
        echo "    chmod -R o+rX $STATIC_DIR"
        echo "  затем: systemctl reload nginx"
        echo "  проверка: su -s /bin/sh $runner -c \"test -r $probe\" && echo ok"
    fi
}

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
ln -sfn /etc/nginx/sites-available/liqscope /etc/nginx/sites-enabled/liqscope
install_conf "$HERE/nginx-liqscope.http.conf"
check_static_perms
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
install_conf "$HERE/nginx-liqscope.conf"
check_static_perms
systemctl reload nginx

echo
echo "проверка микрокэша и статики (X-Cache-Status: MISS → HIT):"
echo "  curl -sD - -o /dev/null 'https://${DOMAIN}/api/klines?symbol=BTC_USDT&timeframe=5' | grep -i x-cache"
echo "  sleep 1"
echo "  curl -sD - -o /dev/null 'https://${DOMAIN}/api/klines?symbol=BTC_USDT&timeframe=5' | grep -i x-cache"
echo "  curl -sD - -o /dev/null 'https://${DOMAIN}/static/logo.png' | grep -iE 'cache-control|x-content'"

echo
echo "готово: https://${DOMAIN}/"
echo
echo "в systemd-юнит добавьте и перезапустите сервис (рабочий юнит — licvid,"
echo "deploy/liqscope.service устарел):"
echo "  Environment=LIQSCOPE_PUBLIC_URL=https://${DOMAIN}"
echo "  Environment=LIQSCOPE_COOKIE_SECURE=1"
echo "  Environment=LIQSCOPE_SECRET=\$(openssl rand -hex 32)   # в drop-in, не в git"
echo "  ExecStart=... --host 127.0.0.1 --port 8000 --workers 1"
echo "  systemctl daemon-reload && systemctl restart licvid"
echo "  systemctl status licvid --no-pager | head -5"
echo
echo "зависимости приложения должны быть установлены в его venv (orjson —"
echo "быстрый JSON; без него сервер работает, но медленнее и пишет warning):"
echo "  ${APP_DIR}/venv/bin/pip install -r ${APP_DIR}/requirements.txt"
echo "  curl -s localhost:8000/api/health | grep -o '\"fast_json\":[a-z]*'   # true"
echo
echo "порт 80 и 443 должны быть открыты (ufw allow 80,443/tcp)."
echo "у BotFather: /setdomain ${DOMAIN}"
