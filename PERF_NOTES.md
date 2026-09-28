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
