# Домен без порта и HTTPS

Браузер на `http://liqscope.online` стучится в **порт 80**.
Приложение крутится на **8000**. Поэтому голый домен молчит, а
`http://liqscope.online:8000` открывается.

Схема:

```
браузер ──80/443──► nginx ──127.0.0.1:8000──► uvicorn (licvid.service)
```

## 1. DNS

У регистратора домена:

| тип | имя | значение |
|---|---|---|
| A | `@` (liqscope.online) | `78.17.66.215` |
| A | `www` | `78.17.66.215` (по желанию) |

Подождать 1–5 минут. Проверка: `ping liqscope.online` должен показать этот IP.

## 2. Порты

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
# 8000 снаружи больше не нужен — его закроет nginx
```

## 3. Поставить nginx + сертификат

На сервере, из каталога репозитория:

```bash
cd /root/Licvid
git pull
sudo bash deploy/setup-https.sh liqscope.online ваш@email.com
```

Скрипт поставит nginx, Let’s Encrypt и прокси с WebSocket (`/ws`).

Сайт: **https://liqscope.online/**

## 4. Сказать приложению новый адрес

```bash
sudo systemctl edit --full licvid    # или nano /etc/systemd/system/licvid.service
```

В `[Service]`:

```ini
Environment=LIQSCOPE_PUBLIC_URL=https://liqscope.online
Environment=LIQSCOPE_COOKIE_SECURE=1
ExecStart=/root/Licvid/venv/bin/python3 -m uvicorn server:app --host 127.0.0.1 --port 8000
```

`--host 127.0.0.1` прячет :8000 снаружи. Если venv в другом месте — оставьте свой путь, смените только host.

```bash
sudo systemctl daemon-reload
sudo systemctl restart licvid nginx
```

## 5. Telegram

В BotFather: `/setdomain` → `liqscope.online` (без https, без порта).

Сертификат Let’s Encrypt сам продлевается (`certbot.timer`).
