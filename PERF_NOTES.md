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
