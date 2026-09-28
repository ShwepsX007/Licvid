# Security notes — Licvid / LiqScope

Дата: 2026-09-27, ветка main = 5cc6f5a (merge workspace)

## Workspace Multi-Chart Security (новый)

### Модель угроз workspace
- Пользователь может подделать localStorage `liqscope_terminal_workspace_v1` с XSS payload в panelId/symbol
- Detached window URL `/terminal?mode=panel&panel=<id>` — id из URL может содержать XSS
- BroadcastChannel сообщения между окнами — злоумышленник с same origin может слать поддельные PANEL_STATE_UPDATE
- window.open — name и URL должны быть валидированы

### Что закрыто для workspace

- **panelId**: regex `^[a-zA-Z0-9_-]{1,64}$` в `_broadcast` и `_onBroadcastMessage`, в `detachPanel` URL `encodeURIComponent(id)` + name `liqscope_panel_${id}` validated
- **workspaceId**: проверка equality `msg.workspaceId !== this.workspaceId` → ignore, генерируется `ws_` + random
- **symbol**: `_validateSymbol` — `ALL` или regex `^[A-Z0-9]{2,20}_[A-Z0-9]{2,6}$`, trim upper, в `_populateSymbolSelect` via `textContent`
- **timeframe**: `_validateTf` — whitelist `[1,3,5,15,60,240,1440]` или 1-1440
- **innerHTML**: только статика (header, layersPop, toolbar), user data через `textContent` или `_esc()` (escape &, <, >, ")
- **URL**: все params через `encodeURIComponent`, detached URL формируется как `/terminal?mode=panel&panel=${encodeURIComponent(id)}&workspace=${encodeURIComponent(wsId)}&symbol=${encodeURIComponent(sym)}&tf=${encodeURIComponent(tf)}`
- **Broadcast**: `_broadcast` валидирует panelId regex перед postMessage, `_onBroadcastMessage` валидирует workspaceId equality + panelId regex + symbol via `setSymbol` (который валидирует)
- **localStorage**: `loadWorkspaceRaw` try/catch JSON.parse, `saveWorkspace` debounced, size < 2KB
- **No eval**: нет `eval`, `Function`, `setTimeout(string)`
- **Max panels**: 6 с alert, prevents DoS via many charts
- **Tests**: `tests/workspace_manager.js` — `detach URL rejects XSS panelId`, `invalid broadcast with XSS fails`, `invalid symbol in broadcast fails`, `max panels limit enforced`

### Остатки
- `window.open` без `noopener` — same origin, не критично, но можно добавить `noopener` в features (сейчас `width=900,height=600,menubar=no...` без `noopener` — не уязвимость, т.к. same origin и name validated)
- BroadcastChannel без origin check — same origin по spec, OK

## Предыдущий аудит

Дата: 2026-09-27, ветка arena/01a0e2e8-licvid

## Модель угроз (коротко)

- Публичный сайт, WS-лента ликвидаций, кабинет (email/telegram), админка, статьи (markdown).
- Основные риски: XSS через список монет/статьи, CSRF/CORS, дефолтный секрет → обратимые HMAC IP, DoS через рост данных/WS, XFF spoofing → обход rate limit.

## Что закрыто

### P0 XSS /api/symbols/add

- **Вход**: `market_feed.is_safe_symbol()` — строгий regex `^[A-Z0-9]{1,10}_[A-Z0-9]{1,10}$`, длина ≤20, кап `custom_symbols` 120, total 200. `force` только админ (`require_admin`).
- **Выход**: фронт `app.js` — `escapeHtml()` для innerHTML и `textContent` для custom list, нет прямой вставки символа в HTML.
- **Rate limit**: 20 запросов / 10 минут на `/api/symbols/add`, общий 60/10м, плюс nginx `limit_req 2r/s` на тот же путь.
- **Проверка**: `curl -X POST /api/symbols/add?symbol=<img>` → `{"error":"invalid"}`, `force=true` anon → `admin`, `tests/symbol_xss.js` — jsdom не создаёт реального `<img>`/`<svg>` из экранированной строки.

### P1 Markdown XSS articles.py

- `inline_html()` теперь `html.escape(url, quote=True)` для href/src/alt, фильтр `javascript:` сохранён (если url начинается с `javascript:` — остаётся текст, не ссылка).
- Тест `test_articles.py` — `![x" onerror="alert(1)](url)` → `alt="x&quot; onerror=&quot;alert(1)"` без вырывания атрибута.

### P1 CORS * + credentials

- Было: `allow_origins=["*"]` + `allow_credentials=True` → любой origin мог читать приватные ответы.
- Стало: `CORS_ORIGINS` из `SITE_URL`, `PUBLIC_URL`, `LIQSCOPE_CORS_ORIGINS` (csv), `*` запрещён. `allow_credentials=True` только для перечисленных.
- Проверка: `curl -H "Origin: https://evil.example" /api/stats` → нет `Access-Control-Allow-Origin`, `liqscope.online` → есть.

### P1 Default LIQSCOPE_SECRET

- Было: дефолт `liqscope-change-me` в коде, в `licvid.service` переменная закомментирована → HMAC IP обратим перебором.
- Стало: `_KNOWN_BAD_SECRETS`, fail-fast `SystemExit(2)` в PROD если секрета нет/дефолт/короткий. В DEMO — warning + авто-генерация в `data/secret` (chmod 600). `LIQSCOPE_REQUIRE_SECRET=1` в `licvid.service` + инструкция генерировать `openssl rand -hex 32` и класть в drop-in `systemctl edit licvid`.
- Проверка: `LIQSCOPE_SECRET=` в PROD → падает с сообщением, в DEMO → warning, `test_secret_guard`.

### P2 Security headers

- App middleware `SecurityHeadersMiddleware`: `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=()`, `X-Frame-Options: SAMEORIGIN`, `CSP: frame-ancestors 'self'`.
- Nginx `deploy/nginx-liqscope.conf` и `deploy/nginx-liqscope.http.conf`: те же заголовки + `Strict-Transport-Security` в https, `gzip on`.
- Проверка: `curl -D /terminal` → `x-frame-options: SAMEORIGIN`, `content-security-policy: frame-ancestors 'self'`.
- SAMEORIGIN и 'self' — осознанный выбор: дополнительные графики терминала работают через iframe того же origin. Чужой origin по-прежнему не может встроить терминал. Не ужесточать до полного запрета фреймов — сломается док.

### P2 Gzip + Cache-Control + i18n split

- `GZipMiddleware` + nginx gzip → 75% экономии (app.js 370KB → 91KB).
- `CachedStaticFiles`: `?v=` → `public, max-age=31536000, immutable`, без версии → `max-age=3600`.
- `i18n.pages.js` 490KB → per-lang 53-147KB raw, gz 12-30KB, сервер отдаёт только нужный язык (`seo_pages.render` подменяет src). См. `PERF_NOTES.md`.

### Anti-DoS

- WS: nginx `limit_conn` 20 на IP для `/ws`.
- Body limit middleware: 1MB для обычных, 5MB для `/api/articles`, `/api/hourly/posts`.
- XFF: доверяется только если клиент из `LIQSCOPE_TRUSTED_PROXIES` (по умолчанию `127.0.0.1,::1`, можно задать CIDR и список). Реализовано в `server.py:_is_trusted_proxy()` и `web_account.py:_is_trusted_proxy()` (geo делегирует).
- Проверка: spoof `X-Forwarded-For: 1.2.3.4` от внешнего IP → игнорируется, rate limit считается по реальному IP.

## Что осталось (P3)

- Молчаливые `except Exception: pass` — 64 места, не критично для безопасности, но скрывает ошибки.
- Дублирование `lightweight-charts` — убрали npm зависимость, оставили vendored как single source of truth.
- 40 env не описаны — частично покрыто README чек-листом.

## Как проверить после деплоя

```bash
# secret
sudo systemctl show licvid -p Environment | grep SECRET
# headers
curl -D - https://liqscope.online/terminal | grep -i "x-frame\|content-security\|nosniff"
# gzip
curl -H "Accept-Encoding: gzip" -D - https://liqscope.online/static/app.js?v=x -o /tmp/a.gz
# CORS
curl -H "Origin: https://evil.example" -D - https://liqscope.online/api/stats | grep -i access-control
# symbols/add
curl -X POST "https://liqscope.online/api/symbols/add?symbol=%3Cimg%3E&force=true" -i
# backup
systemctl list-timers licvid-backup.timer
journalctl -u licvid-backup -n 20
```

## Артефакты

- `deploy/licvid.service` — рабочий юнит, 127.0.0.1:8000, REQUIRE_SECRET, TRUSTED_PROXIES
- `deploy/liqscope.service` — помечен obsolete, не использовать, слушает 127.0.0.1
- `deploy/nginx-liqscope.conf` / `http.conf` — gzip + security headers + limit_conn/req
- `deploy/licvid-backup.service/.timer` + `deploy/BACKUP.md` — sqlite3 .backup, WAL checkpoint
- `.github/workflows/tests.yml` — CI
- `PERF_NOTES.md` — замеры
