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
venv/bin/pip install -r requirements.txt   # новый orjson: без него сервер медленнее и пишет warning
sudo bash deploy/setup-https.sh liqscope.online ваш@email.com
```

Скрипт поставит nginx, Let’s Encrypt и прокси с WebSocket (`/ws`).

Заодно он:

* подставляет в конфиг ваш домен **и** фактический путь к `static/`
  (`alias` берётся из каталога репозитория, а не захардкожен — иначе nginx
  отдавал бы 404 на статику);
* проверяет `nginx -t` **до** установки каждого конфига и не трогает рабочий,
  если проверка не прошла;
* подсказывает `chmod`, если nginx (`www-data`/`nobody`) не может прочитать
  `static/app.js`.

Конфигов два, и включён всегда один: `nginx-liqscope.http.conf` (только :80,
нужен certbot для первого выпуска) и `nginx-liqscope.conf` (:80 → :443 + сам
сайт). `proxy_cache_path` объявлен в обоих — зон `API_CACHE` не задваивается
именно потому, что одновременно подключён один файл. Что кэшируется и почему —
в `PERF_NOTES.md` (раздел «Лаги при 50+ зрителях»).

Сайт: **https://liqscope.online/**

## 4. Сказать приложению новый адрес

```bash
sudo systemctl edit --full licvid    # или nano /etc/systemd/system/licvid.service
```

В `[Service]` (рабочий юнит — `deploy/licvid.service`, не устаревший `liqscope.service`):

```ini
Environment=LIQSCOPE_PUBLIC_URL=https://liqscope.online
Environment=LIQSCOPE_COOKIE_SECURE=1
Environment=LIQSCOPE_REQUIRE_SECRET=1
# openssl rand -hex 32 — в drop-in, не в git. Дефолт liqscope-change-me не принимается.
Environment=LIQSCOPE_SECRET=<случайная-строка>
ExecStart=/root/Licvid/venv/bin/python3 -m uvicorn server:app --host 127.0.0.1 --port 8000
```

Без `LIQSCOPE_SECRET` сервис не поднимется: на опубликованной соли обратимы хеши IP.

`--host 127.0.0.1` прячет :8000 снаружи. Если venv в другом месте — оставьте свой путь, смените только host.

```bash
sudo systemctl daemon-reload
sudo systemctl restart licvid nginx
```

Если `restart` отвечает `Unit licvid.service has a bad unit file setting` —
в юните две строки `ExecStart` (для `Type=simple` systemd это запрещает).
Проверка и правка:

```bash
systemctl cat licvid.service | grep ExecStart
# оставьте одну, например:
# ExecStart=/root/Licvid/venv/bin/python3 -m uvicorn server:app --host 127.0.0.1 --port 8000
sudo systemctl edit --full licvid
sudo systemctl daemon-reload
sudo systemctl restart licvid
```

## 5. Telegram

В BotFather: `/setdomain` → `liqscope.online` (без https, без порта).

Сертификат Let’s Encrypt сам продлевается (`certbot.timer`).

## 6. Проверить кэш nginx и статику

Nginx кэширует публичные ручки на 3–5 секунд: при 50+ зрителях в приложение
уходит один запрос, а не пятьдесят. Проверяется снаружи, заголовком
`X-Cache-Status` (его ставит сам конфиг).

```bash
# микрокэш свечей: первый запрос MISS, второй в пределах 3 с — HIT
curl -sD - -o /dev/null 'https://liqscope.online/api/klines?symbol=BTC_USDT&timeframe=5' | grep -i x-cache-status
curl -sD - -o /dev/null 'https://liqscope.online/api/klines?symbol=BTC_USDT&timeframe=5' | grep -i x-cache-status
#   X-Cache-Status: MISS
#   X-Cache-Status: HIT

# публичные ручки (5 с)
curl -sD - -o /dev/null https://liqscope.online/api/stats  | grep -i x-cache-status
curl -sD - -o /dev/null https://liqscope.online/api/digest | grep -i 'x-cache-status\|cache-control'

# чего в кэше быть НЕ должно: у личных и пишущих ручек заголовка нет вовсе
curl -sD - -o /dev/null https://liqscope.online/api/auth/me | grep -i x-cache-status   # пусто
```

`EXPIRED`/`STALE` — тоже норма: `STALE` значит, что приложение задумалось или
упало, а зрители получили прежние свечи вместо ошибки.

```bash
# статика: nginx отдаёт её сам, кэш по маске
curl -sD - -o /dev/null 'https://liqscope.online/static/app.js?v=quota1' | grep -i 'cache-control\|x-cache'
#   Cache-Control: public, max-age=31536000, immutable   (есть ?v=)
curl -sD - -o /dev/null 'https://liqscope.online/static/i18n.pages.en.js' | grep -i cache-control
#   Cache-Control: public, max-age=3600                  (без ?v= — час, не год)
curl -sD - -o /dev/null 'https://liqscope.online/static/icon-192.png' | grep -i cache-control
#   Cache-Control: public, max-age=604800                (картинки — 7 суток)

# кэш лежит на диске и не разрастается (лимит 100 МБ, неактивное живёт минуту)
du -sh /tmp/nginx_api_cache
```

Если `X-Cache-Status` пустой на `/api/klines` — конфиг не тот: проверьте
`sudo nginx -T | grep -n 'proxy_cache\|API_CACHE'` и что
`/etc/nginx/sites-enabled/` ссылается на `nginx-liqscope.conf`, а не на старую
копию. Если статика отдаётся 404 — смотрите `alias` в конфиге: путь должен
совпадать с каталогом репозитория (`setup-https.sh` подставляет его сам), а
файлы должен читать пользователь nginx:

```bash
sudo -u www-data test -r /root/Licvid/static/app.js && echo ok
```

Приложение при этом отвечает за миллисекунды и не ходит на биржу внутри
запроса — даже для монеты, которой нет в списке:

```bash
curl -s localhost:8000/api/health | grep -o '"fast_json":[a-z]*'   # true = orjson включён
curl -s -o /dev/null -w 'klines %{time_total}с http=%{http_code}\n' \
  'localhost:8000/api/klines?symbol=НОВАЯ_МОНЕТА_USDT&timeframe=5'  # ~0.01с, source:"stub"
```

Лимиты добавления монет: аноним новую монету не добавляет (401 `auth`),
аккаунт — не больше пяти (`LIQSCOPE_USER_SYMBOL_CAP`), админ — без счёта.

```bash
curl -i -X POST 'https://liqscope.online/api/symbols/add?symbol=GRAM_USDT' | head -1
#   HTTP/2 401   (или 404, если пары нет в каталоге бирж)
```
