# PERF NOTES — Licvid / LiqScope

## Замеры производительности LiqScope — 28.09.2026

Стенд: uvicorn, 1 воркер, `LIQSCOPE_DEMO=1`, `LIQSCOPE_API_CACHE=0`,
биржевые WS недоступны (песочница) — поток синтетический.
Коммит: серия поверх `1624097` (sid-лимитер, nginx `location /static/`,
удаление `lastInit`).

### Стресс-тест `tools/load_test.py` — ПРОШЁЛ

```
python3 tools/load_test.py --url http://127.0.0.1:8003 --ws 50 --rps 100 --seconds 60
# сервер на время прогона: LIQSCOPE_RATE_LIMIT=0 (иначе 5 WS/мин с IP
# придушат сам тест — так задумано, см. docstring скрипта)
```

| Метрика | Факт | Цель |
|---|---|---|
| WS подключилось / init / кадров | 50/50, 50, 66800 | — |
| Обрывы WS | 0 | 0 |
| Отклонено WS | 0 | 0 |
| HTTP запросов / ошибок | 4663 / 0 | — |
| HTTP p50 / p95 / max | 3.3 / 5.2 / 23.2 мс | p95 < 200 мс |
| RSS до → после | 71.0 → 75.9 МБ | < 500 МБ |
| Серверные метрики (хвост прогона) | 1106 сообщ/с, 77.7 запр/с, p95 2.4 мс | — |

Запас по p95 — ~40×, по памяти — ~6×. HTTP недобрал до 100 rps
(~93 rps эффективных: 10 с форы коннектам + планировщик) — на вывод
не влияет.

### Вес ответов (gzip уже включён: `GZipMiddleware`, `minimum_size=500`)

| Ответ | Без сжатия | gzip | Выигрыш |
|---|---|---|---|
| `GET /terminal` | 48 676 Б | 10 697 Б | 4.5× |
| `/static/app.js` | 397 130 Б | 98 945 Б | 4× |

Порог из задачи (`/terminal` < 500 КБ со сжатием) — с запасом 45×.
Nginx сверху добавляет свой gzip (`gzip_proxied any`, уровень 5).

### i18n и кэш

- `/` и `/terminal` отдают `/static/i18n.pages.{lang}.js` под язык
  запроса (`?lang=` → cookie `liqscope_lang` → браузер → страна):
  проверено `en` по умолчанию и `ru` по cookie. Общий
  `i18n.pages.js` (516 КБ) в HTML не подставляется.
- Статика: `?v=` → `Cache-Control: public, max-age=31536000, immutable`,
  без версии → `public, max-age=3600` (проверено живьём; те же значения
  продублированы в `location /static/` обоих nginx-конфигов).

### Лимитер с проверкой сессии

Фейковый `Cookie: liqscope_sid=fake123` больше не открывает льготный
бакет: 61-й мутирующий POST — `429` + `Retry-After: 60` (было бы 404
до 600-го). WS с фейковым sid — те же 5 подключений/мин, 6-е — `1008`.
Покрыто `tests/test_ops.py` (`test_fake_cookie_gets_anon_bucket`,
`test_fake_cookie_ws_stays_anon`), 12/12 зелёных.



Дата: 2026-09-27, main = 5cc6f5a + workspace fixes до 162cdc0
Стенд: LIQSCOPE_DEMO=1 на 127.0.0.1:8000, без внешней сети (биржи недоступны, но статика и HTML отдаются как в проде)

## Workspace Perf

### Что измеряли для workspace
- Вес новых файлов: `chart_panel.js` 33KB, `workspace.js` 31KB, `workspace.css` 6KB raw → gz ~10KB total
- Кол-во WS: один на окно (main + detached) — проверено `WorkspaceManager._hookWs` + `BroadcastChannel`
- Max panels 6 с alert — предотвращает DoS
- Debounced save 600ms — `localStorage` не спамится
- Resize: `ResizeObserver` + `requestAnimationFrame` + `getBoundingClientRect` — нет polling
- Init: `_initChartWithRetry` 30 ретраев 200ms, затем 100/500/1500ms resize — chart не создается с 0 размером
- Layers: `loadLevels` с `cache:no-store` + retry 500/1500ms, периодический refresh 30s, priceLines fallback (до 20) — не блокирует main thread
- Flex layout resizable both — CSS only, no JS layout thrashing

### Замеры
- `/terminal` с workspace: HTML 47KB + статика 1.43MB raw → gz 373KB + workspace 10KB gz → **~383KB** на проводе (было 373KB без workspace, +2.6%)
- TTFB `/terminal` 2.5ms, `/api/klines?symbol=BTC_USDT&timeframe=5` 5-10ms (demo 122 свечи)
- `localStorage` workspace: ~1.2KB для 2 панелей, <2KB для 6 панелей
- WS messages: routing по symbol, без N× нагрузки, один WS на окно
- e2e `workspace_e2e.js` 22 ok за 8-9s (jsdom + lightweight-charts setTransform mock errors игнорируются)

### Что осталось
- `lightweight-charts` vendored 197KB raw / 62KB gz, npm ^5.2.1 не используется — можно удалить из package.json
- Canvas `clusterCanvas` 400×360px per panel, 6 панелей → 6×~0.5MB bitmap memory, OK для десктопа, на мобиле 1 панель 100% width

## Предыдущий аудит

Дата: 2026-09-27
Стенд: LIQSCOPE_DEMO=1 на 127.0.0.1:8001, без внешней сети (биржи недоступны, но статика и HTML отдаются как в проде)

## Что измеряли

1. Суммарный вес статики для `/terminal` (лендинг `/` и `/digest` для сравнения)
2. Наличие gzip (Content-Encoding)
3. Cache-Control для статики
4. TTFB HTML
5. Влияние разбиения `i18n.pages.js`

Инструменты: `curl -D`, `wc -c`, проверка заголовков.

## До (из AUDIT.md)

- `/terminal` HTML 44.6 КБ + 20 файлов = **1955.7 КБ** несжатых
- `i18n.pages.js` = 490 КБ (все 5 языков сразу)
- Сжатия нет ни в приложении, ни в nginx (Accept-Encoding игнорировался)
- Cache-Control / Last-Modified на статике нет, только ETag — браузер ревалидирует каждый раз
- TTFB не измерялся в аудите, но HTML отдавался без кэша

Причины медленной загрузки:
- Отсутствие gzip: 1.96 МБ → на проводе 1.96 МБ
- Монолитный i18n: каждый посетитель тянет 400 КБ лишних переводов
- Нет Cache-Control immutable для версионированных `?v=` — повторные визиты ревалидируют
- Дублирование `lightweight-charts`: vendored 196 КБ + npm зависимость `^5.2.1` (версии разъедутся)

## После (текущая ветка)

### Gzip

- Включён на двух уровнях:
  - nginx: `gzip on; gzip_types ...` в `deploy/nginx-liqscope.conf` и `nginx-liqscope.http.conf`
  - FastAPI: `GZipMiddleware(minimum_size=500)` как страховка для демо без nginx
- Проверка:
  ```
  curl -H "Accept-Encoding: gzip" /static/app.js -o body
  content-encoding: gzip
  raw 370620 → gz 91948 (75% экономия)
  ```
- Суммарно для `/terminal`:
  - raw 1_434_216 байт (с per-lang en) → gz **372_791 байт**
  - со старым combined i18n.pages.js: raw 1_845_xxx → gz 478_xxx
  - экономия gzip: ~74% (1.43 МБ → 0.37 МБ)

### Cache-Control

- Реализован `CachedStaticFiles`:
  - `?v=` → `public, max-age=31536000, immutable`
  - без версии → `public, max-age=3600`
- Проверка:
  ```
  /static/app.js?v=123 → cache-control: public, max-age=31536000, immutable
  /static/app.js → cache-control: public, max-age=3600
  ```
- Эффект: повторные загрузки не тянут статику вообще (браузер + CDN).

### i18n split

- Генератор `tools/build_i18n_pages.py` теперь делает:
  - `i18n.pages.js` (совместимость, 516 КБ raw / 136 КБ gz) — все языки
  - `i18n.pages.ru.js` 53 КБ raw / 12 КБ gz
  - `i18n.pages.en.js` 104 КБ raw / 30 КБ gz
  - `i18n.pages.zh.js` 100 КБ raw / ~28 КБ gz
  - `i18n.pages.hi.js` 147 КБ raw / ~40 КБ gz
  - `i18n.pages.es.js` 106 КБ raw / ~30 КБ gz
- Сервер `seo_pages.py` подменяет в HTML:
  ```
  /static/i18n.pages.js → /static/i18n.pages.{lang}.js
  ```
  по языку запроса (`?lang=`, cookie, Accept-Language).
- Экономия на `/terminal`:
  - raw: 516 КБ → 104 КБ (en) = **-412 КБ**
  - gz: 136 КБ → 30 КБ = **-106 КБ**
- API i18n не ломали: `window.LIQSCOPE_I18N_PAGES` по-прежнему объект `{lang: {keys,phrases}}`, просто с одним ключом.

### TTFB

- `/` 0.0035s starttransfer, 56 КБ HTML
- `/terminal` 0.0025s, 47 КБ HTML
- `/digest` 0.0026s, 16 КБ HTML
- До оптимизаций TTFB был сопоставим (HTML маленький), основное время уходило на статику.

### Итог по весу

| Страница | До (raw) | До (gz, оценка) | После per-lang raw | После per-lang gz |
|----------|----------|-----------------|--------------------|-------------------|
| /terminal | 1955 КБ | ~600 КБ (если бы gzip был) | 1434 КБ | **373 КБ** |
| / (лендинг) | 1259 КБ | ~400 КБ | ~850 КБ | ~280 КБ |

- С учётом gzip + per-lang: **~2 МБ → ~0.37 МБ** на проводе для `/terminal` (в 5 раз меньше).
- Без gzip, но с per-lang: 1.95 МБ → 1.43 МБ (чисто за счёт i18n).
- С gzip, без per-lang: 1.95 МБ → 0.48 МБ.

### Что ещё можно (B3, не обязательно сейчас)

- Убрать дублирование `lightweight-charts`: выбрать один источник (npm или vendored). Сейчас vendored 197 КБ raw / 62 КБ gz, npm ^5.2.1 не используется в сборке — можно удалить из package.json или наоборот собрать через bundler.
- Добавить ETag/Last-Modified уже есть, главное Cache-Control.
- Проверить, нет ли тяжёлого JSON в HTML — в `seo_pages.render` вставляется только язык и hreflang, JSON нет.
- WS init payload: `custom_symbols` теперь кап 120, `is_safe_symbol` фильтрует, рост ограничен.

### Как воспроизвести замеры

```bash
LIQSCOPE_DEMO=1 LIQSCOPE_SECRET=test-secret-not-the-published-default \
  python3 -m uvicorn server:app --host 127.0.0.1 --port 8001

# TTFB
curl -w "TTFB %{time_starttransfer} total %{time_total} size %{size_download}\n" http://127.0.0.1:8001/terminal

# gzip
curl -H "Accept-Encoding: gzip" -D - http://127.0.0.1:8001/static/app.js -o /tmp/a.gz
wc -c /tmp/a.gz; gzip -d -c /tmp/a.gz | wc -c

# cache-control
curl -D - "http://127.0.0.1:8001/static/app.js?v=123" -o /dev/null | grep -i cache
curl -D - "http://127.0.0.1:8001/static/i18n.pages.en.js?v=1" -o /dev/null | grep -i cache

# CORS
curl -D - -H "Origin: https://evil.example" http://127.0.0.1:8001/api/stats | grep -i access-control
curl -D - -H "Origin: https://liqscope.online" http://127.0.0.1:8001/api/stats | grep -i access-control
```

---

## Лаги при 50+ зрителях и защита IP от бана биржи — 28.09.2026

Стенд: uvicorn, **1 воркер**, `LIQSCOPE_DEMO=1`, биржевые WS/REST из песочницы
недоступны (значит, любой синхронный поход на биржу в обработчике виден сразу —
он заканчивается таймаутом, а не данными). Коммит: серия поверх `b0b4cea`.

### Симптом

При 50+ зрителях интерфейс замирает, `uvicorn` упирается в 100% CPU, а биржа в
ответ на долбёжку отдаёт 429/418 и может забанить IP сервера.

Сложились три причины:

1. **`GET /api/klines` ходил на биржу внутри запроса.** `get_candles` звал
   `tracker.ensure_symbol` (сеть) и `fetch_klines` (Binance → Bybit → OKX)
   прямо в обработчике: открытие нового графика вешало единственный воркер на
   секунды и плодило запросы к биржам **по числу зрителей** (для свежей монеты —
   сразу пачку на все таймфреймы). Отсюда и лаги, и 429/418.
2. **Стандартный `json` на всём.** Сериализация пачки свечей — 220 мкс,
   broadcast кадра 50 клиентам — ~1.3 мс чистого CPU одного воркера; разбор
   входящих WS-кадров биржи — 15.6 мкс на кадр при тысячах кадров в секунду.
3. **nginx ничего не кэшировал** и проксировал даже `/static/`: одинаковые
   публичные ответы собирались заново на каждый запрос.

### Что сделано (воркер по-прежнему один — это сознательно)

`--workers 1` в `deploy/licvid.service` не меняли: WS-мост `LiqScopeWsBridge`,
dock-iframe и `Client.send` рассчитаны на один процесс (общая память фида и
хаба). Второй воркер без Redis/общего состояния сломал бы подписки. Вместо
масштабирования воркеров разгружаем тот, что есть — слоями.

#### 1. nginx-микрокэш (`deploy/nginx-liqscope.conf`, `.http.conf`)

```nginx
proxy_cache_path /tmp/nginx_api_cache levels=1:2 keys_zone=API_CACHE:10m
                 max_size=100m inactive=1m use_temp_path=off;
```

| Путь | TTL | Зачем |
|---|---|---|
| `/api/klines` | `proxy_cache_valid 200 3s` | 50 зрителей одной монеты = 1 поход в приложение за 3 с (свечи всё равно обновляются не чаще) |
| `/api/stats`, `/api/digest`, `/api/digest/today`, `/api/digest/ГГГГ-ММ-ДД` | `5s` | публичные, неперсонализированные; у дайджеста свой `Cache-Control: public, max-age=60` остаётся сверху |
| `/static/` | отдаёт nginx (`alias` + `try_files @static_app`) | статика не доходит до Python вовсе |
| `/ws`, `/api/symbols/add`, `/api/auth/*`, `/api/admin/*`, кабинет | **не кэшируются** | сессии, личные данные, запись |

Ключевые детали конфига:

* `proxy_cache_lock on` + `proxy_cache_lock_timeout 5s` — при толпе на бэкенд
  идёт **один** запрос, остальные ждут готовый ответ, а не плодят дубли;
* `proxy_cache_use_stale error timeout updating http_500 … http_504` вместе с
  `proxy_cache_background_update on` — если приложение задумалось или упало,
  зрители видят прежние свечи (`STALE`), а свежее догружается в фоне;
* `proxy_hide_header Set-Cookie` на обеих кэшируемых ветках + `proxy_ignore_headers
  Set-Cookie` — кэш не отравляется кукой виджета (`liqscope_vid`) и не разносит
  её зрителям (это же причина, по которой кэш не стоит на `/ws`); на
  `stats`/`digest` добавлено `proxy_ignore_headers Cache-Control Expires`: срок
  кэша nginx держит своим (5 с), а `Cache-Control: public, max-age=60`
  приложения остаётся заголовком для браузера;
* `proxy_set_header Accept-Encoding ""` — апстрим отдаёт несжатое, gzip делает
  nginx один раз на всех;
* `add_header X-Cache-Status $upstream_cache_status` — видно `MISS`/`HIT`/`STALE`
  снаружи, без логов;
* `access_log off` в `/static/` — журнал не тонет в запросах картинок;
* `Cache-Control` для статики собирается `map`-ом по URI:
  **год + immutable** при `?v=`, **7 суток** для картинок/шрифтов,
  **1 час** для `.js/.css/.json/.html/webmanifest` без версии — потому что
  `i18n.pages.{lang}.js` подставляется в HTML без `?v=`, и вечный кэш оставил бы
  переводы у зрителей на год;
* заголовки безопасности (`X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, CSP `frame-ancestors 'self'`, `Permissions-Policy`, HSTS)
  продублированы в каждом `location` со своим `add_header`: nginx **сбрасывает**
  унаследованные `add_header`, когда в блоке появляется свой — раньше
  `/static/` из-за этого уходил без `nosniff`.

#### 2. orjson вместо стандартного json

`FastAPI(default_response_class=ORJSONResponse)`, `orjson` в
`requirements.txt`, `json_dumps_text()` для WS-кадров
(`ws.send_text` вместо `send_json` — сериализуем один раз на кадр, а не на
клиента), `orjson.loads` на входящих кадрах биржи в `market_feed.py` (19 мест).

Замеры на этом же стенде (кадр ленты ликвидаций 1129 Б, пачка свечей 15 657 Б):

| Операция | stdlib `json` | `orjson` | Выигрыш |
|---|---|---|---|
| разбор кадра WS-ленты | 15.6 мкс | 6.0 мкс | **2.6×** |
| сериализация `/api/klines` | 220.7 мкс | 22.4 мкс | **9.8×** |
| broadcast кадра 50 клиентам | 1253.7 мкс | 152.6 мкс | **8.2×** |

Пакет не обязателен: без orjson приложение поднимается на прежнем
`JSONResponse` (поведение то же), а в логе при старте — предупреждение;
`/api/health` → `"fast_json": true/false`. REST-ответы биржи остались на
загрузчике aiohttp: REST зовётся раз в секунды, а двойники ответов в тестах
повторяют базовую сигнатуру `json(content_type=…)`; выигрыш там несоизмеримо
меньше горячего WS-пути.

#### 3. `/api/klines` — читатель буфера, а не поход на биржу

Обработчик больше **никогда** не ждёт биржу. Порядок чтения в `get_candles`:

1. кэш отдач `CANDLES` (`KLINE_TTL` 20 с, повторный рефетч не чаще
   `CANDLES_REFETCH_SEC`);
2. локальный буфер фида — `market_feed.get_candles_cached(symbol, tf)`;
3. заготовка из последней цены — `source: "stub"`, `pending: true`, `candles: []`.

История новой монеты догружается **фоновой** `asyncio.create_task`
(`schedule_candles` в `market_feed.py`, одна задача на серию + дроссель
`_candles_fetch_at`, карта подрезается по `CANDLES_CACHE_MAX`), а готовые свечи
приезжают зрителю по WS (`{"type":"candles"}` — пуш только на переходе
`stub → exchange`, чтобы не спамить). `force=true` (внутренние прогрева,
дайджест, посты в канал) по-прежнему ждёт биржу — там настоящие данные нужны в
публикуемом тексте, поэтому `digest_ctx.candles_fn` и подсчёт объёмов в
`slot_flows` зовут `get_candles(..., force=True)`.

Замер на стенде (биржи недоступны — значит, любой синхронный поход был бы
секундами и ошибкой):

```
GET /api/klines?symbol=BTC_USDT&timeframe=5         → 11.1 / 7.1 / 7.1 мс, 24 КБ
GET /api/klines?symbol=BRANDNEWCOIN_USDT&timeframe=15 → 17 мс
GET /api/stats                                      → 3.2 мс, 2.3 КБ
GET /api/digest                                     → 3.3 мс, Cache-Control: public, max-age=60
GET /static/app.js?v=quota1                          → 10.4 мс, 399 КБ, max-age=31536000, immutable
GET /                                                → 3.5 мс
```

Ответ `/api/klines`: `{symbol, timeframe, source, candles, stale, pending,
age_sec}` — `source` честно говорит, откуда данные (`cache`/`feed`/`stub`/
`demo`/`exchange`), а `pending` — что история ещё в пути.

#### 4. Лимиты на добавление монет (защита подписок, памяти и IP)

| Кто | Новые монеты | Ответ при отказе |
|---|---|---|
| Аноним | **0** (уже лежащую в списке пару открыть можно — она ничего не прибавляет) | 401 `error:"auth"`, «Авторизуйтесь для добавления монет» |
| Аккаунт (`liqscope_sid`) | `LIQSCOPE_USER_SYMBOL_CAP` = **5**, счётчик в `accounts.db` (`user_symbols`), сверенный со списком фида (`_live_symbol_quota`) | 409 `error:"quota"` + `limit`/`used`/`left`/`symbols` |
| Админ | без счёта (`cap = -1`), `force=true` только ему | 403 `error:"admin"` на `force` от неадмина |
| Все вместе | `MAX_CUSTOM_SYMBOLS` = **120** (без изменений) и `SYMBOLS_CAP` = 120 | 409 `error:"full"` |

Повтор пары, которая уже в общем списке, квоту не тратит. Слот держит только та пара, которая реально лежит в `custom_symbols` фида: список живёт в памяти процесса, поэтому после рестарта сервиса слоты **освобождаются** (строки базы, чьи пары из терминала исчезли, удаляются при следующей проверке квоты), а не сгорают навсегда — иначе «5 монет» превратились бы в пожизненный лимит. Та же монета, добавленная заново, слот не удваивает (`PRIMARY KEY (user_id, symbol)`). Чтение сессии и
счётчика — через `asyncio.to_thread`: единственный воркер не стоит на диске,
пока остальные ждут ответ. Фронт (`static/app.js`) различает коды и показывает
перевод (`search.need_auth`, `search.quota_full` с `{n}`) вместо общего
«не добавлено»; версии `?v=quota1` проставлены в 12 HTML, чтобы кэш отдал
новый `i18n.js`/`app.js`.

### Что не меняли (сознательно)

* `--workers 1` в `deploy/licvid.service` — до появления Redis/общего состояния;
* WS-мост `LiqScopeWsBridge` в `static/chart_dock.js` и dock-iframe — не тронуты;
* `MAX_CUSTOM_SYMBOLS` = 120 и `SYMBOLS_CAP` = 120;
* `proxy_cache` не стоит на `/ws`, `/api/symbols/add` и любых личных ручках.

### Проверка на бою после деплоя

```bash
cd /root/Licvid && git pull && venv/bin/pip install -r requirements.txt   # orjson!
sudo nginx -t && sudo systemctl reload nginx && sudo systemctl restart licvid

# ВАЖНО: uvicorn открывает порт в КОНЦЕ startup, а после рестарта воркер ещё
# ~20 с занят прогревом (каталог трёх бирж, WS десяти источников, OI по топу,
# восстановление истории). Замеры в это окно непоказательны: curl на
# localhost:8000 получает connection refused (пустой вывод и «0.0006с»), а
# nginx — 502, поэтому X-Cache-Status будет MISS за MISS. Сначала ждём:
for i in $(seq 40); do curl -sf -o /dev/null localhost:8000/api/health && break; sleep 2; done
sleep 20                                   # дать прогреву отпустить event loop

# микрокэш: первый запрос MISS, второй (в пределах 3 с) HIT
curl -sD - -o /dev/null 'https://liqscope.online/api/klines?symbol=BTC_USDT&timeframe=5' | grep -i 'x-cache-status\|cache-control'
curl -sD - -o /dev/null 'https://liqscope.online/api/klines?symbol=BTC_USDT&timeframe=5' | grep -i x-cache-status
#   → X-Cache-Status: MISS
#   → X-Cache-Status: HIT

# публичные ручки (5 с)
curl -sD - -o /dev/null https://liqscope.online/api/stats  | grep -i x-cache-status
curl -sD - -o /dev/null https://liqscope.online/api/digest | grep -i 'x-cache-status\|cache-control'

# статика: год для ?v=, час для i18n.pages.*.js без версии
curl -sD - -o /dev/null 'https://liqscope.online/static/app.js?v=quota1' | grep -i cache-control
curl -sD - -o /dev/null 'https://liqscope.online/static/i18n.pages.en.js' | grep -i cache-control

# кэш реально лежит на диске и не разрастается
ls /tmp/nginx_api_cache | head; du -sh /tmp/nginx_api_cache

# доля HIT: 20 подряд идущих зрителей одного графика = 1 поход в приложение
U='https://liqscope.online/api/klines?symbol=BTC_USDT&timeframe=5'
for i in $(seq 20); do curl -sD - -o /dev/null "$U" | grep -io 'x-cache-status: [a-z]*'; done | sort | uniq -c
#   1 x-cache-status: MISS
#  19 x-cache-status: HIT

# установившаяся latency (НЕ сразу после рестарта): единицы миллисекунд
for i in $(seq 10); do curl -s -o /dev/null -w '%{time_total} ' \
  'localhost:8000/api/klines?symbol=BTC_USDT&timeframe=5'; sleep 0.3; done; echo

# orjson включён и /api/klines не ждёт биржу
curl -s localhost:8000/api/health | grep -o '"fast_json":[a-z]*'
curl -s -o /dev/null -w 'klines %{time_total}с http=%{http_code}\n' 'localhost:8000/api/klines?symbol=НОВАЯ_МОНЕТА_USDT&timeframe=5'

# статику отдаёт nginx, а не Python: no-transform и 604800 ставит map конфига
curl -sD - -o /dev/null 'https://liqscope.online/static/logo.png' | grep -i cache-control
journalctl -u licvid --since "-2 min" | grep -c "GET /static/"      # 0
sudo -u www-data test -r /root/Licvid/static/app.js && echo "www-data читает static"

# лимиты: аноним новую монету не добавляет
curl -i -X POST 'https://liqscope.online/api/symbols/add?symbol=GRAM_USDT' | head -1
```

Что видно в `/var/log/nginx/error.log` во время `systemctl restart licvid`:
`connect() failed (111 …)` по некэшируемым ручкам (`/ws`, `/api/chat/*`,
`/api/oi`) — приложение ещё не слушает; и те же ручки с пометкой
`subrequest: "/api/klines"` / `"/api/stats"` — это
`proxy_cache_background_update` догружает протухшее в фоне, пока зрителям
уходит `STALE` из кэша. То есть микрокэш отрабатывает ровно так, как задуман:
часть трафика переживает рестарт без ошибок. Полностью убрать окно перезапуска
можно, только открывая порт до прогрева (вынести `feed.start()` из lifespan в
фоновую задачу) — сознательно не делали: порядок «сначала история, потом
биржевые потоки» важнее двадцати секунд при деплое.

`deploy/setup-https.sh` сам подставляет домен и путь к статике в оба конфига,
проверяет `nginx -t` перед установкой, подсказывает `chmod`, если nginx не
читает `/static/`, и печатает эти же проверки в конце.

### Чем проверено

| Проверка | Результат |
|---|---|
| `tests/test_klines_isolation.py` | 12/12: запрос не ждёт биржу, 50 зрителей = 1 поход, `stub → exchange` по WS, `force=True` ждёт, `CANDLES_CACHE_MAX`, `get_candles_cached`, чтение буфера фида, дроссель рефетча |
| `tests/test_symbol_quota.py` | 22/22: 401 анониму, 409 на 6-й монете, счётчик переживает пересоздание `accounts.Store`, исчезнувшие пары освобождают слот (а живые — держат), админ без лимита, изоляция квот по аккаунтам, чужая cookie = аноним, кап 120, orjson как ответчик приложения |
| `tests/test_nginx_conf.py` | 27/27: разобрал оба конфига (парсер с учётом кавычек) — кэш только где надо, `X-Cache-Status`, `proxy_cache_lock`, статика через `alias`, заголовки в каждом `location`, `limit_req`/`limit_conn`, HSTS; синтаксис — `crossplane` (обёртка в `http{}`), без него проверка пропускается |
| `tools/run_tests.sh --python` | падают только 5 тестов, которые падали **до** правок (`test_archive_restore`, `test_bot_i18n`, `test_channel_digest`, `test_chat_read_state`, `test_oi`) — новых падений нет |
| живой uvicorn на :8000 | замеры выше; `/api/health` → `fast_json: true` |

Nginx-двоичника в песочнице нет (собирать не из чего: закрыт доступ к
`nginx.org`, нет `pcre`/`zlib`-заголовков), поэтому конфиг проверен
`crossplane` — он валидирует контекст каждой директивы и число аргументов по
таблице nginx; на сервере `setup-https.sh` дополнительно гоняет `nginx -t`.

### Как воспроизвести замеры

```bash
LIQSCOPE_DEMO=1 LIQSCOPE_SECRET=test-secret-not-the-published-default \
  /path/to/venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8000

for i in 1 2 3; do
  curl -s -o /dev/null -w "klines %{time_total}с\n" \
    'http://127.0.0.1:8000/api/klines?symbol=BTC_USDT&timeframe=5'; sleep 0.3
done

# orjson против stdlib json на реальных формах ответов
python3 - <<'PY'
import json, time, orjson
candles = {"candles": [{"time": 1790512200 + i * 300, "open": 61000 + i,
                        "high": 61100 + i, "low": 60900 + i, "close": 61050 + i,
                        "volume": 100 + i * 1.7, "cvd": i * 3.5} for i in range(121)]}
for name, fn, n in (("json.dumps", lambda: json.dumps(candles), 3000),
                    ("orjson.dumps", lambda: orjson.dumps(candles), 3000)):
    t0 = time.perf_counter()
    for _ in range(n): fn()
    print(name, "%.1f мкс" % ((time.perf_counter() - t0) / n * 1e6))
PY
```

## Хвост p95 в секундах при живом обработчике — 29.09.2026

### Что показал бой

Прогоны `tools/load_test.py` на сервере (1 воркер, 50 WS + 100 rps к
`/api/stats`), установившийся режим — перед замером выждали 120 с после
`systemctl restart`, чтобы не мерить прогрев:

| Замер | WS | HTTP p50 | HTTP p95 (клиент) | p95 обработчика (сервер) | RSS текущий / пик |
|---|---|---|---|---|---|
| `--url http://127.0.0.1:8000 --ws 50 --rps 100` | 50/50, обрывов 0, кадров 27466 | 21.8 мс | **5660 мс** (max 7130) | **6.1 мс** | 244.7 → 258.3 МБ / 1226.9 МБ |
| то же по внешнему IP:8000 | 50/50, обрывов 0 | 9.8 мс | **2117 мс** | **6.2 мс** | → 262.3 МБ / 1226.9 МБ |
| через nginx (`/api/klines`, 20 зрителей) | — | — | 19 `HIT` + 1 `MISS` | — | — |

Разрыв в три порядка — «обработчик быстрый, а снаружи секунды» — означает, что
запросы **стояли в очереди event loop**, пока единственный воркер был занят чем-то
другим. Ни nginx, ни прогрев, ни лимитер (`LIQSCOPE_RATE_LIMIT=0` в drop-in
подтвердил себя: 50/50 коннектов, 0 отказов) к этому не относятся.

### Причина: рассылка кадров дожидалась сокет каждого зрителя

`Hub.broadcast` шёл по клиентам последовательно и на каждом вызывал
`Client.send`, который (а) **заново сериализовал тот же кадр** и (б) `await`-ил
`ws.send_text(...)` с `SEND_TIMEOUT = 5.0`. У зрителя с полным TCP-буфером
(мобильная сеть, заснувшая вкладка, `drain()` не отпускает) это держало всю
рассылку — а с ней и event loop — **до пяти секунд на каждого** такого клиента.
Ровно тот хвост, что виден в замере: p95 5660 мс при max 7130 мс. HTTP-запросы
в это время не обрабатывались вовсе: они ждали своей очереди в цикле, поэтому
серверный p95 оставался 6 мс (он меряет только время обработчика).

### Что сделано

1. **Кадр сериализуется один раз на всех.** `Hub.broadcast` зовёт
   `json_dumps_text(msg)` один раз и передаёт готовый текст в
   `Client.send(msg, text=frame)`; адресные отправки (`init`, чат, `pong`)
   сериализуют себя сами, как и раньше.
2. **У каждого клиента своя очередь исходящих кадров.** `broadcast` больше не
   ждёт сокет: он кладёт кадр в `Client.out` (`offer()`) и уходит. В сокет пишет
   отдельная задача клиента (`Client._pump()`), поэтому порядок кадров у зрителя
   сохранён (один писатель, FIFO), а забитый сокет вяжет **только своего**
   писателя. Прежний `Client.send()` остался для адресных отправок — сигнатура
   расширена необязательным `text=`, поведение то же.
3. **Отставший зритель отключается, а не ест память.** Пределы очереди —
   `Client.OUT_MAX_FRAMES = 200` и `OUT_MAX_BYTES = 512 КБ`: превысили → клиент
   помечается мёртвым, очередь сбрасывается, хаб его выкидывает (фронт
   переподключится и пересинхронизируется — WS-мост `LiqScopeWsBridge` не тронут).
4. **Отказ одного сокета не обрывает рассылку остальным**: исключение в
   `_deliver` логируется в debug, виновник выкидывается, живые получают кадр.
5. **Сторож пауз воркера** (`loop_lag_watchdog`, задача `loop-lag`): меряет
   фактическую задержку тика в 200 мс и пишет в журнал всё, что длиннее
   `LIQSCOPE_LOOP_LAG_MS` (по умолчанию 500 мс, не чаще раза в 5 с). Накопленное
   видно в `/api/health` и `/api/metrics`: `loop_lag_max_ms`, `loop_lag_last_ms`,
   `loop_stalls`. Если хвост вернётся — по времени пауз в `journalctl` рядом с
   `[loop]` сразу видно, кто держал воркер.
6. **Глубина очередей в мониторинге** (`/api/metrics`): `ws_send_queue_frames`,
   `ws_send_queue_bytes`, `ws_send_queue_worst_frames`, `ws_slow_clients`
   (клиенты с очередью больше 10 кадров).
7. **`tools/load_test.py`**: `--ws 0` больше не падает
   (`ValueError: Set of coroutines/Futures is empty` на пустом `asyncio.wait`),
   скрипт печатает паузы воркера из `/api/metrics`, объясняет разрыв
   клиент/сервер и показывает залипшие запросы (> 500 мс) с отметкой времени от
   старта прогона и медианным интервалом между ними — ровная периодичность
   указывает на фоновую задачу (15 с — `kline_refresher`), рваная — на очередь
   под нагрузкой.

### Замер фан-аута (кадр свечей 11.9 КБ, 50 зрителей)

| Сценарий | Было | Стало | Доставка |
|---|---|---|---|
| все сокеты быстрые | 2.2 мс на кадр | **0.33 мс** (7×) | 50/50 сразу в обоих случаях |
| один сокет забит 2 с | **2004 мс** на кадр | **0.21 мс** (~9500×) | было: все стоят за забитым; стало: 49/50 сразу, забитый дописывает сам |
| сериализация кадра | 50× = 1.10 мс | 1× = 0.023 мс | экономия 1.08 мс CPU на каждый кадр рассылки |

Воспроизведение: `/tmp/bench_fanout.py` — двойники сокетов (`FastWS`/`StuckWS`),
прежняя схема против `Hub.broadcast` на этом же коде.

### Что осталось смотреть на бою

* p95 после деплоя при тех же 50 WS + 100 rps. Ожидание: хвост уходит к
  десяткам миллисекунд; если он останется — `loop_lag_max_ms` и строки `[loop]`
  в журнале назовут следующую причину (кандидат №2 — `kline_refresher`: раз в
  15 с последовательно тянет `force=True` все просматриваемые пары).
* `ws_slow_clients` > 0 надолго — зрители реально не успевают читать (или
  канал узкий, или кадров слишком много).
* `--url http://127.0.0.1:8000` по-прежнему меряет худший случай мимо
  микрокэша nginx: картину боя даёт `--url https://ваш-домен`.

### Чем проверено

| Проверка | Результат |
|---|---|
| `tests/test_ws_fanout.py` | 18/18: кадр сериализован один раз на 50 клиентов, порядок сохранён, predicate, мёртвый/сломанный клиент не обрывает рассылку, забитый сокет не задерживает остальных (< 200 мс на фан-аут), переполнение очереди отключает отставшего, лимит по байтам, `remove` останавливает писателя, глубина очереди в метриках, сторож пауз ловит синхронную блокировку цикла и молчит на свободном, `loop_lag_*` в `/api/health` и `/api/metrics` |
| `tests/test_klines_isolation.py` | 12/12 (двойник `FakeViewer.send` приведён к новой сигнатуре с `text=`) |
| `tests/test_resilience.py`, `test_ops.py`, `test_symbol_quota.py`, `test_nginx_conf.py`, `test_tick_flow.py`, `test_side_feed.py` | без падений |
| `tools/run_tests.sh --python` | падают только те же 5 файлов, что падали до правок (`test_archive_restore`, `test_bot_i18n`, `test_channel_digest`, `test_chat_read_state`, `test_oi`) |

## Чем именно занят воркер: трассировка стека и debug-режим asyncio — 29.09.2026 (вечер)

Сторож пауз (`loop_lag_watchdog`) отвечает на вопрос «был ли воркер занят и
сколько», но не на вопрос «кем». DEMO-стенд на :8003 (`LIQSCOPE_DEMO=1`,
**без единого клиента**) показал, что паузы есть и без зрителей:

```
21:51:58 [loop] воркер был занят  829 мс; пауз таких 1,  максимум  829 мс
21:52:45 [loop] воркер был занят  857 мс; пауз таких 4,  максимум  857 мс
21:53:18 [loop] воркер был занят  866 мс; пауз таких 7,  максимум  866 мс
21:53:52 [loop] воркер был занят 1085 мс; пауз таких 10, максимум 1085 мс
21:54:24 [loop] воркер был занят  746 мс; пауз таких 13, максимум 1085 мс
21:54:58 [loop] воркер был занят  887 мс; пауз таких 16, максимум 1122 мс
```

16 пауз за ~4 минуты ≈ одна в 11-15 с, по 0.7-1.1 с каждая. Клиентов не было —
значит, это **не** WS-фан-аут (его как раз развязали очередями), а фоновая
работа и/или нехватка CPU. Кандидаты по коду:

| Задача | Период | Что делает синхронно |
|---|---|---|
| `pump_loop` («Сторож монет») | `LIQSCOPE_PUMP_POLL_SEC` = 25 с | `gate_ticker_snapshot()` разбирает ~1000 USDT-контрактов Gate одним `for`, затем `PUMPS.add_prices()` по всем монетам и `pump_notify()` → `PUMPS.scan()` на каждого подписчика |
| `alert_loop` | 20 с + 8 с | перебор подписок × монет, `alerts_due(...)` |
| `kline_refresher` | 15 с | `get_candles(..., force=True)` по каждой просматриваемой паре (без зрителей — пусто) |
| `hot_symbols_watcher` | 3 с | пересчёт горячих монет |
| `_oi_engine` / `oi_history_task` | 5 с | OI по топу, REST + разбор |
| DEMO-задачи (`demo_generator` 0.15-0.9 с, `demo_price_walk` 1 с × 20 монет, `demo_tick_walk` 0.05 с) | постоянно | те же обработчики `on_liquidation`/`on_price`/`on_trade`, что и в бою, — **только на демо-стенде** |
| `hl_infer` (Hyperliquid) | непрерывно | 7135 сделок/мин в разборе + 47 вызовов `/info` (6 ошибок) |

Плюс внешний фактор: демо-стенд крутился **рядом с боевым** сервисом на той же
машине, то есть два uvicorn с десятью биржами и 40 монетами делили CPU. Сторож
меряет wall-clock, поэтому нехватку процессора он видит так же, как и
синхронную работу в коде.

### Два инструмента, которые называют виновника

Оба включаются переменными окружения и по умолчанию выключены.

| Переменная | По умолчанию | Что даёт |
|---|---|---|
| `LIQSCOPE_LOOP_TRACE` | `0` | поток-сэмплер: раз в 50 мс снимает стек главного потока (в нём живёт event loop) и пишет тот, который держится дольше порога — `файл:строка:функция <- кто позвал`. То есть называет **строку кода** |
| `LIQSCOPE_LOOP_TRACE_MS` | `400` | порог сэмплера в миллисекундах |
| `LIQSCOPE_ASYNCIO_DEBUG` | `0` | штатный debug-режим цикла: asyncio сам пишет `Executing <Task … coro=<pump_loop()…>> took 0.857 seconds`, то есть называет **задачу** |
| `LIQSCOPE_SLOW_CALLBACK_SEC` | `0.25` | порог «медленного колбэка» для debug-режима |
| `LIQSCOPE_LOOP_LAG_MS` | `500` | порог сторожа пауз (был добавлен ранее) |

`PYTHONASYNCIODEBUG=1` **не работает** под uvicorn — проверено замером: цикл
поднимается с `debug=false`, журнал пуст. Поэтому debug включается из
приложения (`_enable_asyncio_debug()` в lifespan: `loop.set_debug(True)` +
`slow_callback_duration`).

Результат трассировки виден и без журнала — в `/api/health` и `/api/metrics`:
`loop_trace` (включена ли), `loop_trace_stalls`, `loop_trace_held_ms`,
`loop_trace_last` (последний пойманный стек). `tools/load_test.py` печатает
`loop_trace_last` в итоге прогона, а если паузы есть, а стек не записан —
подсказывает включить флаг.

### Подводный камень uvloop (найден сквозной проверкой)

`uvicorn[standard]` из `requirements.txt` тянет **uvloop** (в стенде 0.22.1), а
у него механика цикла написана на Cython и в python-стеке **не видна**: самый
внутренний кадр простоя — `runners.py:118:run <- _compat.py:30:asyncio_run <-
server.py:86:run <- main.py:632:run <- core.py:910:invoke …`, а не
`selectors.py:select`. Первая версия правила «стек простоя = весь стек из
внутренностей asyncio» принимала такой простой за занятость: на живом uvicorn
вышло 25-32 ложных срабатывания за 40 с при `loop_lag_max_ms` 20 мс.

Рабочее правило (`_stack_work`/`_stack_is_idle`): границей считается первый
кадр механики запуска (`_LOOP_BOUNDARY`: `base_events.py`, `selectors.py`,
`runners.py`, `_compat.py`, `uvloop`, …), а занятостью — кадры **глубже**
границы. Простою соответствует пустой набор рабочих кадров; в отчёт идут только
они, без обвязки uvicorn/click/runpy.

### Проверка инструментов

| Проверка | Результат |
|---|---|
| сквозной прогон под настоящим uvloop (`uvloop.Loop`), 3 синхронные блокировки по 0.5 с | пойманы все 3, стек виновника `_sync_block <- heavy <- main`; за 2 с простоя — 0 срабатываний |
| живой uvicorn, 40 с простоя (порог 150 мс) | 0 срабатываний сэмплера, `loop_lag_max_ms` 20.1, `loop_trace_stalls` 0 |
| живой uvicorn под нагрузкой: 20 WS + 100 rps, 30 с | 2319 запросов, p50 3.6 / **p95 17.8 мс**, max 85.7 мс, 14340 кадров, обрывов 0, `loop_stalls` 0, ложных срабатываний 0 |
| `tests/test_ws_fanout.py` | **29/29**: сэмплер называет блокирующий кадр, простой под asyncio и под uvloop не считается занятостью, настоящая работа под obвязкой uvicorn — считается, `_stack_work` отдаёт только рабочие кадры, флаг asyncio-debug включает `loop.set_debug` и порог, поля трассировки видны в health/metrics |
| `tools/run_tests.sh --python` | в FAIL только прежние 5 файлов |

### Как включить на бою

```bash
sudo systemctl edit licvid
#   [Service]
#   Environment=LIQSCOPE_LOOP_TRACE=1
#   Environment=LIQSCOPE_LOOP_TRACE_MS=400
sudo systemctl daemon-reload && sudo systemctl restart licvid

journalctl -u licvid -f | grep --line-buffered -E "\[loop"
#   [loop-trace] воркер 857 мс в одном месте (всего 3): pump_scan.py:135:add_prices <- server.py:2331:pump_loop

# либо без журнала, одним запросом:
curl -s localhost:8000/api/health | python3 -c 'import json,sys; d=json.load(sys.stdin); \
print("пауз:", d["loop_stalls"], "| максимум:", d["loop_lag_max_ms"], "мс"); \
print("стек:", d["loop_trace_last"] or "(не поймано)")'
```

Оба флага диагностические: debug-режим asyncio добавляет накладных расходов,
сэмплер — поток, просыпающийся 20 раз в секунду. После выяснения причины их
стоит выключить (то же `systemctl edit` → удалить строки → `daemon-reload` +
`restart`), как и `LIQSCOPE_RATE_LIMIT=0`.

Отличить «синхронная работа в коде» от «не хватает CPU» просто: если
`loop_trace_last` называет конкретную функцию приложения — виноват код; если
паузы есть, а стек каждый раз разный и короткий, а `top` показывает оба uvicorn
под 100% — виноват процессор (демо-стенд рядом с боевым, или одно ядро на
десять биржевых лент).

## Виновник назван: уровни ликвидаций читали 7 дней истории с диска в цикле — 29.09.2026 (ночь)

### Что показал бой после развязки фан-аута

`tools/load_test.py` на боевом сервисе (1 воркер, очередь кадров уже на месте):

| Прогон | WS | HTTP p50 | HTTP p95 | max | p95 обработчика | Паузы воркера |
|---|---|---|---|---|---|---|
| `--url http://127.0.0.1:8000 --ws 50 --rps 100` (мимо nginx) | 50/50, 17100 кадров, обрывов 0 | 7.6 мс | **335.7 мс** (было **5660 мс**) | 3364 мс | 5.5 мс | максимум **4480 мс**, 12 пауз |
| `--url https://liqscope.online --ws 0 --rps 50` (картина боя) | — | 2.0 мс | **3.3 мс** | 113.7 мс | 5.8 мс | — |

То есть очередь исходящих кадров убрала хвост в **17 раз** в худшем случае
(5660 → 336 мс), а через nginx с микрокэшем p95 — **3.3 мс**: порог 200 мс
пройден с запасом. Остаток (336 мс / max 3.4 с) — уже не рассылка.

### Стек виновника

Трассировка на бою назвала его прямо:

```
[loop-trace] воркер 428 мс в одном месте (всего 17):
  history.py:330:iter_events <- liq_levels.py:1057:_events <-
  liq_levels.py:1151:payload <- server.py:1388:liq_levels_task
[loop] воркер был занят 1809 мс; пауз таких 27, максимум 5735 мс
```

`liq_levels_task` каждые `LEVELS_SNAP_SEC` (20 с) считает батч из 4 монет, и для
каждой `payload()` звал `_events()` → `history.iter_events()` с окном
`calib_days` (≥ 7 суток). А `iter_events` **открывает дневные шарды и разбирает
их построчно** (`json.loads` каждой строки, шард дня — десятки мегабайт),
синхронно, в event loop. Отсюда паузы 0.4-1.8 с (максимум 5.7 с) и очередь из
214 HTTP-запросов, которые встали разом на отметке +17.7 с прогона.

Показательно, что рядом в том же `payload()` тяжёлые расчёты `build_ladder` и
`apply_executed` уже были обёрнуты в `asyncio.to_thread` с комментарием «иначе WS
и REST висят по 1-2 сек» — чтение диска просто не попало в этот список.

### Что сделано

1. **`LevelsEngine._events_cached()`**: чтение истории уходит в
   `asyncio.to_thread` (тот же приём, что для `build_ladder`), плюс короткий кэш
   на окно калибровки — `LIQSCOPE_LEVELS_EVENTS_TTL_SEC` (по умолчанию 60 с,
   бакет `int(until // TTL)`), не больше `EVENTS_CACHE_MAX` (64) монет. Новые
   ликвидации меняют семидневное окно несущественно, а перечитывать шарды каждые
   20 с на каждую монету батча — это диск и CPU единственного воркера. Кэш в
   `LEVELS.save()` не persists: он процессный.
2. **Трассировка переведена на сторожа.** Первый вариант признака «один и тот же
   стек держится N мс» дал на бою ложное срабатывание
   `[loop-trace] воркер 480 мс: ssl.py:917:read` — под uvloop короткий кадр
   TLS-чтения вызывается постоянно на десяти биржевых лентах и попадает в каждый
   сэмпл, выглядел как блокировка. Теперь сэмплер только кладёт стеки в кольцо
   (256 × 50 мс ≈ 13 с), а виновника ищет сторож и только когда цикл РЕАЛЬНО
   стоял: берёт из кольца стек, преобладавший в окне паузы, и дописывает его в
   ту же строку — `[loop] воркер был занят 1809 мс … ; в это время он был в:
   history.py:330:iter_events <- …  (16 из 16 сэмплов окна)`. `loop_trace_last`
   в `/api/health` — стек последней настоящей паузы.
3. **`tools/load_test.py`**: разбор залипших запросов кластерами по секундам
   («214 шт. в 1 паузе воркера») вместо медианы интервалов — запросы
   дописывались в список по мере завершения, поэтому медиана выходила 0.0 с и
   даже отрицательной.

### Чем проверено

| Проверка | Результат |
|---|---|
| `tests/test_liq_levels.py` | **50/50** (5 новых): чтение истории идёт в потоке и цикл продолжает тикать (максимальный разрыв тиков < 250 мс при 500 мс «чтения диска»), повторный вызов в пределах TTL не перечитывает шард, новое окно перечитывается, кэш ограничен, `payload()` зовёт `_events_cached`, а не `_events` |
| `tests/test_ws_fanout.py` | **31/31**: сторож называет блокирующий стек по кольцу сэмплов, повторяющийся короткий кадр **не** считается блокировкой (регрессия на `ssl.py:read`), `_loop_trace_explain` выбирает преобладающий стек окна и отсекает старые сэмплы, простой под asyncio и под uvloop не считается занятостью |
| `tools/run_tests.sh --python` | в FAIL только прежние 5 файлов |

### Что смотреть после деплоя

* `loop_lag_max_ms` и `loop_stalls` в `/api/metrics`: пауз должно стать заметно
  меньше, а `loop_trace_last` — назвать следующего виновника, если он есть.
* Повторить прогон B (`--ws 50 --rps 100` мимо nginx): ожидание — p95 ниже
  200 мс и max без трёхсекундных рывков.
* Кэш событий не должен прятать свежие ликвидации надолго: TTL 60 с, при нужде
  уменьшать `LIQSCOPE_LEVELS_EVENTS_TTL_SEC` (минимум 5 с).

## Виновник №2: перебор слотов биржи на каждой точке OI — 30 секунд CPU на один расчёт — 29.09.2026 (утро)

Трассировка, включённая через `systemctl edit` (`LIQSCOPE_LOOP_TRACE=1`), назвала
два стека в окнах пауз на тех же прогонах, где p95 ушёл в секунды.

### Прогон A — 50 зрителей + 100 rps, мимо nginx (`http://127.0.0.1:8000`)

| Метрика | Значение |
|---|---|
| p50 / **p95** / max | 115.8 / **6349.3** / 6664.4 мс |
| Ошибок клиента | 2 (ClientOSError — ответ не успел уйти) |
| p95 на стороне сервера | 9.6 мс (обработчик не при чём) |
| `loop_lag_max` | **2912 мс**, 17 пауз |
| Стек в окне паузы (11 из 20 сэмплов) | `gzip.py:214:_compress_body <- gzip.py:209:apply_compression <- gzip.py:134:send_with_compression <- server.py:3948:send_wrap <- …` |

### Прогон B — то же, по внешнему IP (`http://78.17.66.215:8000`)

| Метрика | Значение |
|---|---|
| p50 / **p95** / max | 22.9 / **9497.8** / 11635.9 мс |
| Ошибок клиента | 0 |
| p95 на стороне сервера | 6.5 мс |
| Пауз воркера | 31 |
| Стек в окне паузы (5 из 13 сэмплов) | `side_feed.py:61:_num <- :159:_share_of <- :192:nearest_bucket <- :395:taker_share_at <- :421:side_at <- liq_levels.py:976:share_at <- :278:build_rows <- :1182:payload <- server.py:1414:liq_levels_task` |

### Разбор: один виновник настоящий, второй — следствие

**`build_rows` → `side_at` → `nearest_bucket`.** Строки позиций строились прямо
в цикле (в `asyncio.to_thread` были только `build_ladder` и `apply_executed`), а
`nearest_bucket` на каждый вызов **перебирал весь ряд слотов** биржи, считая
разрыв до каждого. `build_rows` зовёт `side_at` на каждую точку OI: 30 дней окна
по 5-минуткам — 8640 точек × 8640 слотов. Замер на синтетике тех же размеров:

```
перебор (было):    30287.3 мс на 8640 обращений   ← 30 секунд CPU в одном воркере
бинарный (стало):      8.1 мс                      → ускорение ×3761
результаты совпадают в 8640/8640 случаях
без кэша ключей (сортировка каждый раз): 4388 мс   ← поэтому ключи кэшируются
```

**`gzip.py:_compress_body`.** `GZipMiddleware` был подключён со значением по
умолчанию — уровнем 9, самым медленным. Сам по себе он не даёт секунд
(0.45 мс на ответ ~38 КБ ≈ 0.045 с CPU/с при 100 rps), но в окне паузы он
оказался преобладающим стеком потому, что **сжимал пачку ответов, накопившихся
пока цикл стоял** — то есть следствие, а не причина. Уровень тем не менее
снижен: на одном воркере каждая миллисекунда умножается на RPS, а основной gzip
в бою делает nginx (`gzip_proxied any`, `gzip_comp_level 5`) — приложению
остаются прямые заходы.

```
gzip уровень 1: 0.04 мс на ответ (38 КБ → 0.6 КБ) | при 100 rps ≈ 0.004 с CPU/с
gzip уровень 5: 0.29 мс                            | ≈ 0.03 с CPU/с
gzip уровень 9: 0.45 мс (38 КБ → 0.5 КБ)           | ≈ 0.045 с CPU/с
```

### Что изменено

1. **`side_feed.nearest_bucket`: бинарный поиск вместо перебора.** Разрыв
   считался от середины слота (`|начало + 150 − ts|`), поэтому ищем точку
   вставки `bisect_left(начало_слотов, ts − 150)` и расходимся от неё наружу
   строго по росту разрыва: как только разрыв превысил `TAKER_TOL_SEC` — дальше
   смотреть нечего. Семантика сохранена: ближайший слот с известной долей, при
   равной близости более свежий (сторона позиций меняется быстро), слоты без
   доли пропускаются, вне допуска — `None`.
2. **`bucket_keys()` + кэш ключей в `SideFeed`.** Сортировать 8640 слотов на
   каждый вызов — тоже 4.4 с на расчёт, поэтому отсортированные начала хранятся
   в `_taker_keys` / `_lsr_keys` и сверяются **по идентичности ряда**: при
   загрузке `_taker[sym] = parsed` всегда присваивает новый словарь, так что
   кэш сам себя сбрасывает и не может отдать устаревшие ключи.
3. **`build_rows` ушёл в `asyncio.to_thread`** в `liq_levels.payload()` — рядом
   с `build_ladder` и `apply_executed`. Считается то же самое, только не на
   единственном воркере; при отказе потока — запасной путь inline.
4. **Уровень gzip настраивается**: `LIQSCOPE_GZIP_LEVEL` (по умолчанию **1**) и
   `LIQSCOPE_GZIP_MIN_SIZE` (по умолчанию 500). Старая starlette без
   `compresslevel` обрабатывается через `TypeError`.

### Чем проверено

| Проверка | Результат |
|---|---|
| `tests/test_side_feed.py` | **23/23** (7 новых): бинарный поиск совпадает с прежним перебором на регулярном ряде и на ряде с дырами/пропусками, вне допуска — `None`, при равенстве разрыва берётся свежий слот, мусорный ввод не роняет, 8640×8640 считается быстрее 2 с (регрессия на возврат перебора), кэш ключей переиспользуется и сбрасывается при замене ряда |
| `tests/test_liq_levels.py` | **51/51** (1 новый): `payload()` при дорогом `side_at` (400 точек × 1.5 мс ≈ 0.6 с работы) не блокирует цикл — максимальный разрыв тиков < 300 мс |
| `tests/test_ops.py` | **16/16** (4 новых): уровень по умолчанию 1, `GZipMiddleware` получил `compresslevel` и `minimum_size`, ответы по-прежнему сжимаются и распаковываются целыми, уровень 1 дешевле 9 и не даёт ответ больше чем на 25% |
| `tools/run_tests.sh --python` | **101 ок, 9 ошибок** — те же 5 предсуществующих файлов (archive_restore, bot_i18n, channel_digest, chat_read_state, oi), новых провалов нет |

### Что смотреть после деплоя

* `loop_stalls` и `loop_lag_max_ms` в `/api/metrics`: пауз должно стать на
  порядок меньше — 30 секунд CPU на расчёт уровней исчезли.
* Повторить прогон B (`--ws 50 --rps 100` мимо nginx): ожидание — p95 ниже
  200 мс, без рывков в секунды.
* Если `loop_trace_last` снова назовёт стек — он теперь будет называть
  настоящего виновника: предыдущий стек-«следствие» (gzip) уйдёт вместе с
  блокировкой цикла.
* `LIQSCOPE_GZIP_LEVEL=9` вернёт прежнее сжатие, если трафик окажется важнее
  CPU (nginx в бою жмёт сам, так что обычно не нужно).

## Виновник №3: кэш калибровки раздул кучу до гигабайта — паузы давал сборщик мусора, а не gzip — 29.09.2026 (день)

Прогоны после бинарного поиска слотов и `build_rows` в потоке:

| Прогон | p50 | **p95** | max | ошибок | p95 сервера | пауз воркера | максимум паузы |
|---|---|---|---|---|---|---|---|
| A, мимо nginx (`127.0.0.1:8000`) | 6.8 мс | **216.7 мс** | 631.9 мс | 0 | 5.1 мс | 1 | 1282 мс |
| B, по внешнему IP (`78.17.66.215:8000`) | 28.6 мс | **4689.3 мс** | 8145.3 мс | 0 | 6.0 мс | 11 | 2322 мс |

Прогон A улучшился в **29 раз** (6349.3 → 216.7 мс) и не добил до порога 200 мс
совсем немного. Прогон B остался плохим, и вот почему.

### Gzip исключён как причина

Стек в окнах пауз прогона B снова назвал `gzip.py:214:_compress_body <-
gzip.py:209:apply_compression`. Строки совпадают со starlette 1.7.0 буквально:
209 — это inline-путь «сжать в event loop» (тела короче `thread_minimum_size` =
128 КиБ), 214 — сам `compressor.compress(body) + flush()`.

Но `MetricsMiddleware` у нас **самый внешний**, то есть серверная метрика
включает сжатие. Она показала p95 **6.0 мс** на весь ответ вместе с gzip при
4689 мс снаружи. Значит сжатие стоит единицы миллисекунд, а паузы рождаются
**вне обработки запросов**: 1292 залипших запроса стояли в очереди ещё до того,
как попали в приложение.

### Настоящая причина: куча в гигабайт и сборка мусора

В том же прогоне: пик RSS **1224.1 МБ**, 11 пауз по 1-2.3 с, медиана интервала
между паузами 1 с. Это профиль сборки мусора: на большой куче каждая сборка
второго поколения останавливает процесс целиком, а сэмплер стеков называет
кадр, который в этот момент **аллоцировал память** — поэтому «виновником»
выходит gzip.

Считаем кучу. Кэш окна калибровки уровней (`LevelsEngine._events_cache`,
добавлен в `2cacb2b`) держал до `EVENTS_CACHE_MAX` = 64 записей, запись —
разобранный список событий окна ≥ 7 суток при `MAX_EVENTS` = 20000, а один
словарь ликвидации стоит в куче ~830 байт:

```
64 записи × 20000 событий × 830 Б ≈ 1.06 ГБ   ← наблюдаемый пик RSS 1224 МБ
```

### Прогон A: журнал писался в event loop

Стек единственной паузы прогона A (1282 мс):

```
logging/__init__.py:1103:emit <- :968:handle <- :1696:callHandlers <- :1634:handle
<- :1624:_log <- :1477:info <- websockets_sansio_impl.py:440:send
<- websockets.py:76:send <- websockets.py:110:accept <- server.py:5271:ws_endpoint
```

uvicorn 0.54 пишет в журнал **каждое** принятое WS-соединение
(`'%s - "WebSocket %s" [accepted]'`) и каждый HTTP-запрос (access log): при
100 rps это сотни синхронных записей в секунду. Под systemd stdout уходит в
journald, и когда тот не успевает, буфер трубы заполняется — `write()`
блокирует единственный воркер. 50 зрителей подключаются разом, отсюда 1282 мс.

### Что изменено

1. **Кэш событий ограничен по памяти, а не по числу монет**: `EVENTS_CACHE_MAX`
   64 → **8** записей (`LIQSCOPE_LEVELS_EVENTS_CACHE_MAX`) плюс потолок по
   суммарному числу событий **100000** (`LIQSCOPE_LEVELS_EVENTS_CACHE_EVENTS`)
   ≈ 83 МБ вместо 1.06 ГБ. При нехватке места выбрасываются самые крупные
   записи (они же самые дорогие для сборщика), а окно, которое не влезает само
   по себе, не кэшируется вовсе — такую монету дешевле перечитать с диска.
   Счётчик `_events_cache_events` ведётся явно, замена записи той же монеты не
   удваивает его. Состояние видно в `/api/health` → `levels_events_cache`.
2. **`gc.freeze()` на старте** (`LIQSCOPE_GC_FREEZE=1`): куча старта — модули,
   справочники, загруженная история — убирается из поколений, и сборки
   перебирают только новые объекты. Замер: 71654 объекта заморожено, сборка
   после заморозки 0.0 мс.
3. **Сборка мусора измеряется**: `gc.callbacks` пишут длительность каждой
   сборки и поколение, при ≥ `LIQSCOPE_GC_LOG_MS` (200 мс) идёт строка
   `[gc] сборка 2 поколения остановила воркер на 1830 мс (собрано N объектов)`.
   В `/api/health`: `gc_max_ms`, `gc_last_ms`, `gc_total_ms`, `gc_collections`,
   `gc_gen2_collections`, `gc_frozen_objects`.
4. **Сторож пауз теперь различает причину**: если внутри окна паузы шла сборка
   мусора, в `loop_trace_last` и в строку `[loop]` дописывается
   `во время паузы шла сборка мусора 2 поколения (1830 мс) — кадр в стеке
   просто аллоцировал память`. Больше нельзя принять следствие за причину.
5. **Журнал ушёл из event loop** (`LIQSCOPE_LOG_ASYNC=1`): хендлеры корневого
   логгера и логгеров `uvicorn`, `uvicorn.error`, `uvicorn.access`,
   `websockets` (у них `propagate=False` и собственные хендлеры, поэтому одного
   корневого мало) перенесены в `QueueListener`, а на их место встал
   `QueueHandler`. Переполнение очереди роняет запись, а не воркер:
   `/api/health` → `log_async`, `log_queue_size`, `log_queue_max`,
   `log_dropped`. На выходе очередь дописывается (`stop_async_logging()` в
   `finally` lifespan и в `atexit`), хвост журнала не теряется.
6. **Сжатие уходит в поток**: starlette жмёт тело в event loop, пока оно короче
   `thread_minimum_size` (по умолчанию 128 КиБ), поэтому порог lowered до
   **32 КиБ** (`LIQSCOPE_GZIP_THREAD_MIN_SIZE`) — в потоке zlib отпускает GIL и
   сжатие идёт на других ядрах. Параметры передаются по сигнатуре
   `GZipMiddleware.__init__` через `inspect`: `add_middleware` объект не создаёт,
   поэтому `TypeError` за лишний параметр прилетел бы уже на старте приложения.
   Фактически применённое видно в `/api/health` → `config.gzip`
   (`{'minimum_size': 500, 'compresslevel': 1, 'thread_minimum_size': 32768}`).

### Чем проверено

| Проверка | Результат |
|---|---|
| `tests/test_liq_levels.py` | **57/57** (6 новых): прежний потолок 64 × 20000 не должен давать больше 256 МБ, окно сверх потолка не кэшируется, выбрасывается самая крупная запись и счётчик сходится (220 из 300), потолок по числу записей работает, замена записи той же монеты не удваивает счётчик, `events_cache_stats()` отдаёт потолки |
| `tests/test_ops.py` | **26/26** (10 новых): `gc.callbacks` считает сборки, `gc_during` находит сборку внутри окна паузы и не находит в чужом, `freeze_gc_heap` замораживает кучу, `/api/health` несёт gc/журнал/gzip/кэш уровней, хендлеры перенесены в очередь (включая логгеры uvicorn с `propagate=False`), запись доходит до хендлера через поток слушателя, полная очередь роняет записи за < 0.5 с и считает их, повторный `install_async_logging` не поднимает второго слушателя, middleware получил `thread_minimum_size` ≤ 128 КиБ, а все переданные параметры принимаются установленной starlette |
| `tools/run_tests.sh --python` | **101 ок, 9 ошибок** — те же 5 предсуществующих файлов, новых провалов нет |

### Что смотреть после деплоя

* `rss_peak_bytes` в `/api/health`: должен остаться в пределах ~400-500 МБ
  вместо 1224 МБ. Если снова растёт — смотреть `levels_events_cache.events`.
* `gc_max_ms` и `gc_collections`: если максимум единицы-десятки миллисекунд,
  причина пауз была именно в сборщике. Строки `[gc] …` в journalctl показывают
  каждую заметную сборку.
* `loop_trace_last`: если в нём появилось «во время паузы шла сборка мусора …»,
  стек рядом — не причина, а кадр, который аллоцировал память.
* `log_async` должен быть `true`, `log_dropped` — расти только при реально
  забитом journald (тогда `journalctl -u licvid | tail` покажет пропуски, а
  воркер перестанет вставать на записи).
* Повторить оба прогона: ожидание для A — p95 < 200 мс и ни одной паузы > 500 мс;
  для B — p95 в сотни миллисекунд. Если B останется в секундах, а `gc_max_ms`
  мал — дело в пропускной способности внешнего интерфейса, и тогда смотреть надо
  `p50` (в прогоне B он был 28.6 мс против 6.8 мс на loopback — путь до внешнего
  IP сам по себе вчетверо дороже).

## Кэш лестниц не срабатывал никогда: в ключе была цена с точностью до 8 знаков — 29.09.2026 (вечер)

### Прогоны после заморозки кучи, журнала в потоке и потолка кэша

| Прогон | p50 | **p95** | max | ошибок | p95 сервера | пауз | макс. пауза |
|---|---|---|---|---|---|---|---|
| A, мимо nginx | 480.9 мс | **11304.1 мс** | 13415.7 мс | 652 | 12.0 мс | 8 | 2331 мс |
| B, по внешнему IP | 26.2 мс | **883.0 мс** | 2881.5 мс | 0 | 9.6 мс | 9 | 2331 мс |

**Прогон A недействителен**: `systemctl restart` + `sleep 25` не хватило — сервер
ещё поднимался (восстановление истории с диска), поэтому `curl /api/health`
вернул пустой ответ, 50 WS-подключений получили `ClientConnectorError`
(инструмент напечатал подсказку про лимит, но отклонения были не от лимита), а
652 HTTP-запроса леглись в отказ соединения. Ждать готовности надо опросом, а не
фиксированной паузой.

**Прогон B улучшился в 5.3 раза**: 4689.3 → 883.0 мс.

### Гипотеза про сборщик мусора опровергнута — моим же замером

Сторож пауз теперь дописывает в окно паузы признак сборки:

```
gzip.py:214:_compress_body <- … <- server.py:4117:send_wrap  (2 из 9 сэмплов)
  | во время паузы шла сборка мусора 1 поколения (16 мс) — кадр в стеке просто аллоцировал память
server.py:5527:ws_endpoint <- routing.py:796:app <- …        (12 из 39 сэмплов)
  | во время паузы шла сборка мусора 0 поколения (7 мс)
```

Сборки стоят 7-16 мс, а не секунды: потолок кэша событий и `gc.freeze()`
сработали. Значит паузы 0.5-2.3 с даёт что-то другое. Текущий RSS 269-272 МБ
вместо прежних 301 МБ; пик `ru_maxrss` 1251 МБ остаётся от **старта**
(восстановление истории разбирало дневные шарды) и на прогон не влияет.

### Настоящая находка: кэш готовых лестниц не попадал никогда

`LevelsEngine.payload()` кэширует ответ на `PAYLOAD_TTL` = 20 с, но в ключ кэша
входила цена с точностью до восьми знаков:

```python
key = (sym, min_usd, side, win, round(float(price or 0.0), 8))
```

Цену передают **оба** потребителя: `/api/liq_levels?price=…` — цену со своего
графика (она своя у каждого клиента и меняется каждый запрос), а фон
`liq_levels_task` — текущую цену ленты. Ключ менялся всегда, поэтому на каждый
запрос шёл **полный пересчёт**: события истории за окно, `build_rows`,
`build_ladder`, `apply_executed`, калибровка. Кэш был мёртвым грузом, а фон
уровней каждые 20-30 с считал четыре лестницы заново — на единственном воркере
рядом с живой нагрузкой.

**Исправление** — логарифмический бакет цены (`price_key()`), шаг
`LIQSCOPE_LEVELS_PRICE_KEY_STEP` = 0.0005 (0.05%): одинаковый ключ при движении
цены в пределах шага и разный при заметном. Бакет логарифмический, поэтому шаг
одинаково работает и для BTC по 61000, и для монеты по 0.0000123. Логики это не
ломает: ответ и так может быть старше `PAYLOAD_TTL` = 20 с, а за 20 с цена
уходит заметно дальше 0.05%. Проверка: 61000.00 и 61020.00 (+0.03%) — один
бакет, 61000.00 и 61500.00 (+0.8%) — разные. Попадания/промахи видны в
`/api/health` → `levels_events_cache.payload_cache` (`hits`, `misses`, `entries`).

### Вторая находка: init-пакет WS копировал всю историю

Стек второй паузы прогона B назвал `server.py:5527:ws_endpoint` (12 из 39
сэмплов). В развёрнутой версии на этой строке:

```python
recent = list(LIQUIDATIONS)[-_recent_limit:]
```

`list(deque)` копирует **всю** историю (до `HISTORY_MAX` = 60000 событий), чтобы
взять последние 100. На каждом подключении, а при 50 зрителях разом — 50 раз
подряд. Заменено на `list(islice(reversed(LIQUIDATIONS), limit))[::-1]`:
O(100) вместо O(60000), порядок тот же.

### Замер фона уровней: теперь он считает себя сам

Главный подозреваемый в периодических паузах — расчёт уровней, и до этого
проверить догадку было нечем. Проход вынесен в `levels_bg_pass(state)` (отдельно
от цикла задачи, чтобы проверялся тестом, а не только прогоном на бою) и меряет:

* `last_pass_ms` / `max_pass_ms` — длительность прохода целиком;
* `last_warm_ms` / `max_warm_ms` — прогрев рядов биржи;
* `last_payload_ms` / `max_payload_ms` / `payload_total_ms` — лестницы;
* `passes`, `symbols`, `slow_passes`, `last_at`, `off`.

Всё — в `/api/health` → `levels_bg`. Проход дольше `LIQSCOPE_LEVELS_BG_SLOW_MS`
(1000 мс) пишет раскладку по монетам:
`[levels] проход фона уровней занял 2340 мс (порог 1000 мс): прогрев 180 мс,
лестницы BTC_USDT:920мс, ETH_USDT:740мс, …`.

Ручки для проверки и разгрузки:

* `LIQSCOPE_LEVELS_BG=0` — фон не считает ничего (`levels_bg.off = 1` в
  `/api/health`). Это **диагностический A/B**: прогон нагрузки с выключенным
  фоном сразу показывает, он ли давал паузы. Для постоянной работы не
  предназначено — без снимка перестанут работать алерты подхода к уровню.
* `LIQSCOPE_LEVELS_BATCH` (4) — сколько монет считать за проход.
* `LIQSCOPE_LEVELS_WARM_SEC` (30) — период прохода, `LIQSCOPE_LEVELS_SNAP_SEC`
  (20) — как часто обновлять снимок.

Плюс в `/api/health` добавлены `cpu_count` и `load_avg`: паузы бывают не от
кода, а от нехватки ядер, и без этих чисел одно от другого не отличить.

### Чем проверено

| Проверка | Результат |
|---|---|
| `tests/test_liq_levels.py` | **61/61** (4 новых): бакет цены сводит 0.03% движения и разводит 0.8%, работает на дорогой и дешёвой монете, ноль и мусор не роняют; второй вызов с ценой в пределах шага — **попадание** в кэш (тот же объект, промахов не прибавилось); цена на 3.3% дальше пересчитывает; `cache_stats()` отдаёт TTL и шаг |
| `tests/test_ops.py` | **33/33** (7 новых): init-пакет WS несёт последние события в хронологическом порядке и в пределах лимита, в источнике `ws_endpoint` больше нет `list(LIQUIDATIONS)[-`; проход фона пишет длительность, прогрев, число монет, снимок и круг `asked`; медленный проход считается и логируется с раскладкой по монетам; `LIQSCOPE_LEVELS_BG=0` не греет биржи и не считает лестницы, а `off` виден в health; ошибка одной монеты не рвёт проход; `/api/health` несёт `levels_bg`, `cpu_count`, `load_avg` и `payload_cache` |
| `tools/run_tests.sh --python` | **101 ок, 9 ошибок** — те же 5 предсуществующих файлов, новых провалов нет |

### Как повторить прогон правильно

```bash
sudo systemctl restart licvid
# ждать готовности опросом, а не sleep 25: старт разбирает историю с диска
for i in $(seq 1 60); do
  curl -sf localhost:8000/api/health >/dev/null && { echo "готов через ${i}0 c"; break; }
  sleep 10
done
python3 tools/load_test.py --ws 50 --rps 100 --seconds 60 --url http://78.17.66.215:8000
```

### Что смотреть после деплоя

* `levels_events_cache.payload_cache.hits` против `misses`: попадания должны
  расти. Если `misses` по-прежнему растёт на каждый запрос — бакет цены мал,
  увеличивать `LIQSCOPE_LEVELS_PRICE_KEY_STEP` (например 0.002 = 0.2%).
* `levels_bg.max_pass_ms` и строки `[levels]` в journalctl: если проходы
  длятся тысячами миллисекунд и кластеры пауз в прогоне совпадают с ними —
  виновник подтверждён, уменьшать `LIQSCOPE_LEVELS_BATCH` и/или увеличивать
  `LIQSCOPE_LEVELS_WARM_SEC`.
* `cpu_count` и `load_avg`: если `load_avg` выше числа ядер, машина просто
  насыщена — тогда помогает только уменьшение работы, а не её перестановка.
* A/B-проверка: прогон с `LIQSCOPE_LEVELS_BG=0` против обычного. Разница в p95
  и есть вклад расчёта уровней.

## 29.09.2026 — Причина названа точно: расчёт лестниц, а не диск, не GC и не gzip

### A/B на бою: весь вклад — фон уровней

Прогон `tools/load_test.py --ws 50 --rps 100 --seconds 60 --url http://78.17.66.215:8000`,
сервер поднят и прогрет (готовность проверялась опросом `/api/health`):

| режим | p50 | p95 | max | кластеров пауз | залипших > 500 мс |
|---|---|---|---|---|---|
| уровни включены | 103.6 | **1129.8** | 7389.0 | 19 | 987 |
| уровни включены | 76.5 | **1288.5** | 5182.9 | 18 | 883 |
| `LIQSCOPE_LEVELS_BG=0` | **12.2** | **227.7** | 621.0 | **1** | **15** |

`/api/health` до прогона: `levels_bg.last_payload_ms` **1516.3 и 504.1 мс** — столько
стоит расчёт ОДНОЙ монеты; `payload_cache` почти не попадал; `cpu_count` 2, `load_avg`
0.3 (машина не насыщена — значит, дело в коде, а не в железе); сборки мусора 73-81 мс.

### Замер по стадиям: где именно горело

Синтетика, повторяющая боевой профиль: 8 дневных шардов × 30000 событий = 34.5 МБ,
20000 событий на монету за окно калибровки 7 дней, 8640 точек OI (30 суток по 5 минут)
→ 8639 строк позиций:

| стадия | было | стало | во сколько |
|---|---|---|---|
| `iter_events` (чтение шардов с диска) | 136 мс | 136 мс | — (не трогали) |
| `build_rows` | 41 мс | 34 мс | — (bisect из `2cacb2b`) |
| **`calibrate`** | **98 881 мс** | **1164 мс** | **×85** |
| **`build_ladder`** | **4295 мс** | **802 мс** | **×5.4** |
| **`apply_executed`** | **1334 мс** | **481 мс** | **×2.8** |
| всего на одну монету | 115 с | 2.5 с | ×46 |

Диск даёт 3.9 мс на мегабайт — даже 300 МБ окна это ~1.2 с, а калибровка на тех же
данных стоила 99 секунд. Гипотеза «медленное чтение истории» опровергнута собственным
замером, как до неё — сборщик мусора и gzip.

### Почему это вешало весь сервер

Все три стадии — чистый Python под GIL. `asyncio.to_thread` переносит вызов в поток, но
поток с Python-кодом держит GIL: на двух ядрах воркер не получал свободы, и HTTP-запросы
зрителей стояли в очереди event loop ровно столько, сколько считалась монета. Проход фона
идёт каждые `LEVELS_WARM_SEC` (30 с) батчем `LEVELS_BATCH` (4), а `level_alert_symbols()`
включает каждую монету каждого зрителя — под прогоном с 50 WS это десятки дорогих
лестниц за минуту.

Отдельная ловушка: кэш калибровок `CALIB_TTL_SEC` = сутки, но **после рестарта он пуст**.
Первый проход фона после каждого перезапуска платил полную калибровку по всем монетам —
десятки секунд блокировки. Именно поэтому худшие прогоны (max 7.4 с) приходились на
первые минуты после рестарта.

### Что изменено

1. **`build_ladder` — инварианты наружу.** Смещения колокола в шагах сетки
   (`round(off_rel / step_rel)`) и номер корзины не зависят от внутреннего цикла, но
   пересчитывались в нём: `level_index(base, step)` звался на каждом из 9 отсчётов
   колокола, для каждой стороны, каждого плеча и каждой строки. Теперь `ksteps`
   считается один раз на вызов, номер корзины — один раз на (строку, плечо, сторону),
   `index_price`/`_cell` развёрнуты в арифметику и прямой доступ к словарю. Переход
   «цена → корзина → цена → корзина» убран: `level_index(snap(p))` ≡ `level_index(p)`,
   поэтому результат побитово прежний.

2. **`calibrate` — гистограмма масс вместо 25 лестниц.** `overlap_score` смотрит только
   на массу по цене (`_hist_usd`): разбивка по сторонам и плечам ему не нужна. Поэтому
   строки обходятся **один раз на масштаб плеч** (5 вместо 25) в `base_mass_histogram` —
   словарь «номер корзины → доллары» без ячеек, — а ширина колокола применяется уже к
   гистограмме через `spread_mass`. Свёртка линейна, так что масса по ценам получается
   та же, что и у полной лестницы, зато стоимость зависит от числа занятых корзин
   (сотни), а не от числа строк (тысячи).

3. **`_match_cell` — бинарный спуск.** Отсортированные цены лестницы считаются один раз
   на вызов `apply_executed` (ключи внутри цикла не меняются: пустые корзины удаляются
   после него), дальше `bisect` к точке вставки и короткий проход в стороны — первая же
   корзина с массой и есть ближайшая, потому что стороны перебираются по возрастанию
   зазора. При равном зазоре берётся нижняя цена: раньше решал порядок вставки в
   словарь, который по смыслу ничего не значил, зато делал поиск линейным.

### Эквивалентность проверена, а не заявлена

Один и тот же вход, старая и новая реализация рядом (`/tmp/prof/equiv.py` в прогоне):

* `build_ladder`: 170 ячеек, сумма масс 4627285.873 — совпало; топ-5 ячеек совпал.
* `calibrate`: `lev_scale` 2.0 → 2.0, `spread_scale` 0.5 → 0.5, оценка **0.475414 →
  0.475414, расхождение 0.00e+00** (против честного перебора 25 лестниц).
* `apply_executed`: `{"usd": 1295640.04, "events": 111, "skipped": 19889}` — идентично;
  лестница после вычитания совпала по ключам, максимальное расхождение массы 0.000000.

### Чем проверено

* `tests/test_liq_levels.py` — 66 (5 новых): гистограмма масс + свёртка равны массам
  лестницы на девяти комбинациях `lev_scale`/`spread_scale`; быстрая калибровка выбирает
  те же параметры и оценку, что перебор сетки; при строках вне окна цены — честный отказ
  «нет массы в окне цены» вместо падения; `_match_cell` с индексом и без него даёт одну
  ячейку на 80 ценах; пустая ближайшая корзина, граница допуска и нулевой допуск.
* `tests/test_ops.py` 33, `tests/test_side_feed.py` 23, `tests/test_ws_fanout.py` 31.
* Полный набор: 101 ок / 9 ошибок — тот же базовый уровень, что и до правки
  (проверено прогоном с `git stash`: 1334 теста / 25 провалов до, 1355 / 24 после).

### Что осталось после выключения фона

p95 227.7 мс при `LIQSCOPE_LEVELS_BG=0` — это уже не расчёт уровней. Единственная пауза
в прогоне (max 621 мс) имела стек `flow_feed.py:130:_window ← server.py:5625:ws_endpoint`,
и этот кадр проверен замером, а не принят на веру:

| что | стоимость |
|---|---|
| `flow_snapshot()` целиком (3 × `rows()` + `summary`), боевой `keep_min` = 720 | **13.6 мс** |
| то же после перевода окна на диапазон минут | **6.7 мс** |
| `compute_stats()` под кэшем `_STATS_CACHE_TTL` = 5 с | попадание в кэш, проход по кольцу не чаще раза в 5 с |

То есть 13.6 мс на паузу в 621 мс не тянут: сэмплер снова показал кадр, который просто
выполнялся, пока воркер стоял по другой причине. Так было с gzip в прошлом прогоне и со
`ssl.py:read` до него — стек без замера стоимости не доказательство.

Узкое место в `_window` всё-таки было, и оно исправлено заодно: `sorted(rows)` по ВСЕМ
минутам монеты (в бою `LIQSCOPE_FLOW_KEEP_MIN` = 720) ради пяти нужных, шесть раз на
каждый снимок (три `rows()` в `flow_snapshot` плюс три внутри `summary`), на каждом
WS-подключении и каждые 3 с рассылки. Теперь окно обходится диапазоном
`range(first, hi + 1, MINUTE)` — `window_min` шагов, и стоимость больше не растёт вместе
с `keep_min`. Эквивалентность сверена с прежним обходом на 240 окнах (6 размеров окна ×
40 монет): расхождений 0, включая разреженные минуты и минуты с биржевыми часами впереди
наших. 50 подключений подряд — 0.68 → 0.34 с чистого CPU.

Реальный остаток p95 при выключенных уровнях — очередь на двух ядрах при 100 rps
(нагрузочный тест бьёт в `/api/stats` мимо nginx, то есть мимо микрокэша) и сборки мусора:
`/api/health` в том прогоне показывал `gc_max_ms` 73-81 мс, а в прошлом прогоне одна
сборка второго поколения дала 202 мс. Дальше мерить надо их, а не новые стеки: после этого
фикса прогон стоит повторить с включённым фоном и сравнить `levels_bg.max_pass_ms`,
`gc_max_ms` и число кластеров пауз.

### Переменные окружения после этого фикса

* `LIQSCOPE_LEVELS_BG=0` — **убрать**: это был диагностический A/B, а не лечение. С ним
  не работают оповещения об уровнях и слой уровней на графике.
* `LIQSCOPE_RATE_LIMIT=0` — **убрать обязательно**: это отключает защиту от бана IP
  биржи (429/418), ради которой ставились лимиты.
* `LIQSCOPE_LOOP_TRACE=1` и `LIQSCOPE_LOOP_TRACE_MS=400` — оставить до контрольного
  прогона: сторож пауз показывает виновника в строке `[loop]`.
* `LIQSCOPE_LEVELS_BATCH` (4) — если проходы всё ещё видны в `levels_bg.max_pass_ms`,
  уменьшить до 2; после фикса это скорее страховка, чем необходимость.
