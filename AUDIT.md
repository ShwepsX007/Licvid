# Аудит LiqScope — 28.09.2026 (обновлённый)

Дата: 2026-09-28. Объект: ветка `arena/01a0e4ea-licvid` на коммите
`d96d187` («Свечи: чистка битых баров + lazy-load вкладок дока») —
дерево после серии `59c7aad..d96d187` (мост WS, лимитер, метрики,
кэш API, чистка свечей). Предыдущий текст аудита остаётся
в git-истории `AUDIT.md`; здесь — текущее состояние, а не повтор.

Метод: чтение кода + живой стенд (uvicorn, `LIQSCOPE_DEMO=1`,
кэш API выключен) + штатный раннер `tools/run_tests.sh --python`
+ выборочные jsdom e2e (`chart_follow`, `legend_layout`,
`workspace_e2e`, `workspace_manager`, `ui_smoke`). Все падения
сверены с бейзлайном — деревом до серии изменений.

## Что изменилось с прошлого аудита

- Один WebSocket на окно: iframe `embed=1` сокет не открывает
  (`connectWs` ранний возврат), родитель раздаёт кадры через
  `postMessage`-мост (`LiqScopeWsBridge` + `_startWsBridge`).
- Лёгкий `bootEmbed`: без ленты/чата/статистики/кабинета/рекламы/
  присутствия (ранние возвраты по `embed-mode`); свои свечи —
  REST `/api/klines` раз в 15 с + движение последней свечи ценой.
- Чистка свечей: серверный `_clean_candles` на всех ветках
  `get_candles` + отсев неконечных тиков; клиентский
  `sanitizeCandles` + `try/catch` вокруг `setData`.
- `RateLimitMiddleware`: `/ws` 5/мин с IP (30 — с cookie сессии),
  мутирующие `POST/PUT/PATCH/DELETE /api/*` 60/10 мин (600 — с cookie),
  мониторинг вне лимита, 429 с `Retry-After`, WS-отказ кодом 1008.
- `/api/metrics` + расширенный `/api/health` (RSS, WAL, SQLite ms,
  RPS, p95); счётчики по секундным корзинам с чисткой.
- In-memory `TTLCache` (`web_cache.py`): health 5 с, дайджест 30 с,
  `Cache-Control: public` на публичных списках статей/дайджеста.
- `loading="lazy"` у iframe дока; `workspace.js` отключён от
  `index.html`; кнопка автоследования переехала из плашки слоёв
  в шапку графика; systemd явно фиксирует `--workers 1`.

## Что в порядке (перепроверено)

| Область | Состояние |
|---|---|
| XSS через имя монеты | Сервер: `SYM_RE ^[A-Z0-9]{1,32}_[A-Z0-9]{2,10}$`. Док и embed: `^[A-Z0-9]{2,20}_[A-Z0-9]{2,6}$`. URL iframe строит `URL` + `searchParams`, метки — через `textContent`, `frame.title` — свойством. |
| TF / id слота / localStorage | TF только из `[1,3,5,15,60,240,1440]`; id — `native` или `c_[a-z0-9]{6,12}`; `_restore`: `try/parse`, срез `MAX`, whitelist `cols`/`view`, `validId`/`validSymbol`/`validTf`/`kind==="embed"`. |
| `postMessage` дока | Приём: `origin === location.origin` + `source === "liqscope-dock"` с обеих сторон, до разбора типа. Отправка — с `targetOrigin = location.origin`. |
| Фильтры моста | Док пересылает только `prices/tick/candle/candles` + `ws-state`; iframe принимает тот же набор (`EMBED_ALLOW`). Лента/чат/статистика в iframe не уходят. |
| `embed-sub` | Валидируются `slot`, `symbol`, `tf`; запись — только в `rec.kind === "embed"`; в ответ — лишь снимок состояния сокета. |
| Секрет | Дефолт в `_KNOWN_BAD_SECRETS`; без нормального `LIQSCOPE_SECRET` PROD не стартует (видно в логах стенда); в репозитории секретов нет (скан). |
| CORS | Список из `PUBLIC_URL`/`SITE_URL` + константы + `LIQSCOPE_CORS_ORIGINS`, без `*`. |
| Заголовки (живьём) | `nosniff`, `Referrer-Policy`, `Permissions-Policy`, `X-Frame-Options: SAMEORIGIN`, `frame-ancestors 'self'` — сняты с `/terminal` стенда; оба nginx-конфига отдают то же. |
| `eval` / `new Function` / `document.write` | Отсутствуют в `server.py`, `articles.py`, `web_account.py` и всех грузимых скриптах (`app`, `chart_dock`, `account`, `ads`, `terminal_chat`, `presence`, `consent`, `i18n`). |
| Вынос окон | `window.open(` в доке нет (совпадения grep — `window.opener` в legacy-возврате `?pop=1`, отвечает `postMessage` со своим origin). |
| Кап графиков | `MAX = 6`, дальше `alert`; `index.html` не грузит `chart_panel.js`/`workspace.js` (остался лишь HTML-комментарий). |
| Чистка свечей (живьём) | `/api/klines`: 121 свеча, времена отсортированы и уникальны, OHLCV конечны. Пустой результат чистки проваливается в «старый кэш»/«заготовку», а не отдаёт пустоту. |
| Лимитер: память | `RateLimiter`: окно по ключу + чистка при >5000 ключей + общий `Lock`. Некуда расти. |
| Лимитер: IP | `X-Forwarded-For`/`X-Real-Ip` читаются только от доверенных прокси (дефолт и systemd: `127.0.0.1,::1`, CIDR поддерживаются). Поддельный XFF покрыт тестом `test_ops.py` — ok. |
| Кэш API | `TTLCache`: `Lock`, капы `maxsize`, TTL, kill-switch `LIQSCOPE_API_CACHE`; ключи `(lang, lim)` без пользовательских данных; инвалидация после публикации дайджеста. |
| `/api/health`, `/api/metrics` | Публичны, но отдают только телеметрию (счётчики, RPS, p95, RSS, WAL, SQLite ms, списки бирж). Секретов/токенов/PII в ответах нет — проверено живьём. |
| Один воркер | `deploy/licvid.service`: `--workers 1` с комментарием (хаб, лимиты, кэши — в памяти процесса). |
| SQLite | `ping()` под lock'ом, `-1` при ошибке; WAL-чекпойнт каждые 10 мин + `journal_size_limit=64M`. |

## Находки

### P2. Документы безопасности всё ещё противоречат коду (перенесено, открыто)

`SECURITY_NOTES.md:68,70` и `README.md:155` требуют `X-Frame-Options: DENY`
и `frame-ancestors 'none'`. Код, оба nginx-конфига и живые заголовки стенда
отдают `SAMEORIGIN` / `'self'` — иначе iframe дополнительных графиков
не откроются. Риск прежний: деплой по чек-листу «вернёт» `DENY` и сломает
док; остаточный риск — фрейминг терминала страницей того же origin при XSS
на нём. Рекомендация прежняя: поправить оба файла.

### P3. Льготный бакет лимитера выдаётся по самозваному cookie (новое)

`_scope_sid` считает «залогиненным» любой непустой `liqscope_sid` без
проверки сессии в `account_store`. Значит, заголовок
`Cookie: liqscope_sid=x` поднимает лимиты WS с 5 до 30/мин и мутирующих
POST с 60 до 600/10 мин, а ротация значения cookie снимает лимит почти
полностью. Смягчено: это второй рубеж поверх nginx (`limit_conn 20/IP`,
`limit_req` на `/api/symbols/add`), плюс глобальный `WS_MAX_CLIENTS=400`.
Рекомендация: проверять sid по стору сессий (рукопожатие WS — разовое,
запрос в SQLite дешёвый) либо убрать льготный бакет и резать всегда по IP.

### P3. Полного CSP нет (перенесено, открыто)

Только `frame-ancestors 'self'`; `script-src` нет — страницы на инлайновых
скриптах. Осознанный остаток, не регресс. Закрывать отдельно.

### P3. Два разных regex символа (перенесено, открыто)

Бэкенд шире (`1–32`/`2–10`), фронт строже (`2–20`/`2–6`). Длинная легальная
пара может не открыться в доке; XSS нет, док строже. Свести к одному,
не ослабляя фронт.

### P4. `LiqScopeWsBridge.lastInit` пишется, но никому не отдаётся (новое, мелочь)

Родитель запоминает последний `init`, но `embed-sub` возвращает iframe
только `ws-state` — поле мёртвое. Не влияет ни на что; либо дослать `init`
новым фреймам, либо удалить поле.

## Мёртвый мультичарт: статус

`index.html` больше не подключает `workspace.js`, `chart_panel.js`
по-прежнему не подключается. Файлы оставлены: их читают старые тесты
(`workspace_manager.js`, `workspace_e2e.js` — оба прогнаны, см. ниже).
Рекомендация прежняя: не подключать обратно.

## Проверено прогоном

- `node --check`: `app.js`, `chart_dock.js`, `account.js`, `ads.js`,
  `presence.js`, `terminal_chat.js` — ok. `py_compile`: `server.py`,
  `accounts.py`, `api_articles.py`, `api_digest.py`, `web_cache.py`,
  `web_account.py`, `market_feed.py` — ok.
- Штатный раннер python (`tools/run_tests.sh --python`): **65 ok,
  5 FAIL — все пять побайтово те же на дереве до изменений** (проверено через
  `git stash`): `test_archive_restore` и `test_chat_read_state`
  (`ModuleNotFoundError` — скрипты не кладут корень в `sys.path`),
  `test_channel_digest` (ассерт обложки), `test_oi` (exit 2),
  `test_bot_i18n` (`clean_tree` — дерево грязное по определению +
  непереведённая строка `'Сводка '`). Новый `tests/test_ops.py`
  (лимиты, 429/`Retry-After`, 1008, XFF, метрики) — ok.
- jsdom e2e на стенде (jsdom из npm, внешней сети нет):
  `chart_follow` 67 ok / 1 fail (только шрифты Google),
  `legend_layout` 47 ok / 2 fail (шрифты + stale-проверка
  «расшифровки» — та же на HEAD), `workspace_e2e` 14 ok / 1 fail
  («dock puts the chart back» — тот же на HEAD),
  `workspace_manager` 20 ok / 0, `ui_smoke` — ИТОГ ок.
  **Новых падений относительно HEAD: 0.**
- Живой стенд: заголовки `/terminal` ✓, `/api/health` и `/api/metrics`
  без секретов ✓, `/terminal?embed=1&slot=&symbol=&tf=` — 200 ✓,
  `/api/klines` — чистые сортированные свечи ✓.

## Чего этот аудит не доказывает

- Прод `liqscope.online` не открывался, заголовки живого nginx не снимались.
- Нагрузка связки «6 iframe × мост» на реальном сокете не измерялась
  (`tools/load_test.py` есть, но не гонялся).
- Полный jsdom-прогон всех `tests/*.js` не делался — только 5 файлов
  у изменённых областей.
- Биржевые WS из песочницы недоступны: живой тиковый поток не проверялся,
  стенд работал в DEMO.

## Короткий вывод

Дерево держит прошлые гарантии (whitelist символов/TF/id, проверки
`postMessage`, `SAMEORIGIN`/`'self'`, кап 6, отсутствие `eval`), а мост
с одним сокетом на окно закрыл главный хвост прошлого аудита. Новое
нашлось одно существенное — самозваный sid в лимитере (P3, смягчено
nginx). Документы про `DENY`, полный CSP и второй regex остаются
открытыми хвостами. Тесты: 0 новых падений python и jsdom относительно
бейзлайна.
