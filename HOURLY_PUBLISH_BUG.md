# Hourly / Digest publish bug — диагностика и фикс

Дата: 2026-09-27
Ветка: arena/01a0e2e8-licvid

## Симптом

- Telegram-бот публикует hourly-сводки (сообщения в каналах есть).
- На сайте раздел `/hourly` пустой, или посты обрезанные/без фото.
- Аналогично проверить дайджест `/digest`.

## Пайплайн hourly

1. **Формирование текста/HTML**:
   - `server.py:build_channel_digest()` — снимок рынка за окно поста (window_h = interval_h).
   - `server.py:collect_hourly_post()` — для админ-кнопки «Сводка на сайт»: вызывает `build_channel_digest()`, рендерит `render_post(..., limit=10**9, head_full=True)` для RU и EN (полный текст), берёт картинку `pick_active_image()`, формирует `rec` с `texts` (full), `photo=cover_info(img)`, кладёт в `hourly_ctx.store.add(rec)`.
   - `tg_bot.py:post_channel_digest()` — для автоматической публикации в TG: тот же `build_channel_digest()` через `digest_fn`, рендерит `caption` (короткий, лимит 1024) и `caption_full` (полный, лимит 1e9), берёт картинку `pick_active_image()`, формирует `posts` с `caption` + `full_caption`, отправляет в TG via `_publish_one()`, затем `_archive_posts()` кладёт в архив сайта.

2. **Отправка в TG**:
   - `tg_bot._publish_one()` — `send_photo` с локальным путём (проверяет `os.path.isfile`), если фото нет или не влезает — шлёт текст. Запоминает `channel_sent` (chat, message_id).

3. **Запись для сайта**:
   - `tg_bot._archive_posts()` — берёт `hourly_store` (тот же объект что `hourly_ctx.store`), формирует `texts` из `full_caption`, `sent`, `tg` ids, `photo=cover_info(img)` если файл существует, затем `store.add(rec)`.
   - `PostStore` — JSON файл `data/channel_posts.json` (по умолчанию), `keep=1200`. `_flush()` пишет через tmp+replace. Если `path=""` — не пишет на диск, только в памяти.

4. **Чтение сайтом**:
   - `api_hourly.hourly_records()` — `store.list()` + `archive_fn()` (fallback из месячных свёрток `hourly_posts_from_cells`). Fallback генерирует простые посты из `HIST.hours_range`, source=archive, без фото и без полного текста ИИ.
   - `api_hourly.public_post()` — берёт `texts`, выбирает язык, `tg_html()`, фото проверяет `os.path.isfile(path)` — если файла нет, отдаёт fallback из `static/channel`.
   - Роуты `/hourly` и `/api/hourly` отдают `public_post`.

## Воспроизведение на демо

- Включили генерацию hourly: `collect_hourly_post()` вручную.
- Проверили TG: отправляется (если бот токен и каналы привязаны).
- Проверили файлы: `data/channel_posts.json` не появляется если `LIQSCOPE_HOURLY_FILE=""` или не задан.
- В песочнице `data/` пусто — нет `channel_posts.json`, нет `digests.json` → раздел пустой.
- Симулировали пост с фото: `PostStore.add(rec)` с `photo.path=/tmp/cover.jpg` — `/api/hourly` отдаёт фото URL `/api/hourly/photo/{id}`. После удаления исходного файла `/tmp/cover.jpg` — `/api/hourly` отдаёт fallback bundle `digest_2.jpg`, а не то же фото что в TG. Воспроизведено.

## Root cause

1. **Нет персистентности если HOURLY_FILE пустой**: `server.py` читает `LIQSCOPE_HOURLY_FILE` env, default `data/channel_posts.json`, но если env установлен в пустую строку (или в проде data директория не создана/нет прав), `PostStore.path=""` → `_flush()` ничего не делает, посты живут только в памяти и теряются после рестарта. TG при этом уже отправил пост, а сайт после рестарта пустой. Аналогично для digest `data/digests.json`.

2. **Фото не материализуется для веба**: `rec["photo"]` хранит путь к исходному файлу из `data/channel_photos/` или `static/channel/`. Если админ удаляет исходное фото через `delete_digest_photo`, файл удаляется с диска, и `public_post()` перестаёт находить его → отдаёт fallback bundle, а не то же фото что ушло в TG. Нет копирования фото в безопасное место для поста.

3. **Фото только Telegram file_id**: в теории, если бот начнёт отправлять фото по file_id (кэш Telegram), `os.path.isfile` проверка не пройдёт и фото для сайта не сохранится вообще. Нужен механизм скачивания по file_id через Bot API `getFile` + download в `data/uploads/` или `static/uploads/`.

4. **Обрезанный контент**: `collect_hourly_post()` и `_archive_posts()` уже используют `full_caption` (limit 1e9), но если `store.add()` не вызвался из-за пустого пути или исключения, сайт показывает только fallback `hourly_posts_from_cells` — простые строки без ИИ-шапки, без полного разбора, воспринимается как обрезанный.

5. **Тихие except: pass**: в `_archive_posts` есть `except Exception as e: log.warning(...)` — ошибка архива не ломает публикацию в TG, но и не ретрайится, пост теряется для сайта без явного сигнала админу.

## Фикс

### Единая каноническая запись

Для hourly поста:
```json
{
  "id": "2026-09-27-1200",
  "ts": 1727440000,
  "day": "2026-09-27",
  "window_h": 4,
  "interval_h": 4,
  "total_usd": 1234567,
  "liq_count": 42,
  "texts": {"ru": "<b>Полный текст...</b>", "en": "<b>Full...</b>"},
  "html_full": {"ru": "<b>Полный...</b>", "en": "<b>Full...</b>"},
  "preview": "Полный текст без разметки первые 400 знаков",
  "symbols": ["BTC_USDT", "ETH_USDT"],
  "tags": ["liq", "cvd"],
  "photo": {"path": "data/hourly_photos/2026-09-27-1200_cover.jpg", "url": "/api/hourly/photo/2026-09-27-1200", "name": "cover.jpg", "source": "admin"},
  "sent": {"ru": true, "en": true},
  "tg": {"ru": {"chat": "-100...", "message_id": 123}}
}
```

### Фото как в Telegram

- Добавлен этап `materialize_image_for_web`:
  - Если картинка локальная и файл существует — копируем в `data/hourly_photos/{id}_{safe_name}` (или `data/uploads/hourly/`) с безопасным именем, сохраняем путь копии в записи.
  - Если картинка — Telegram file_id (строка без `/` и не файл) — скачиваем через Bot API `getFile` + download async с таймаутом 30s, кладём в тот же каталог, в записи храним локальный URL.
  - При сбое скачивания — пост публикуется без фото, но с логом ошибки (не блокируем event loop).

- Для digest аналогично: при `assign_cover` копируем фото в `data/digest_photos/{day}_{name}` или используем уже существующий `digest_photo_dir()`.

### Изменения кода

- `server.py`:
  - `HOURLY_FILE` теперь не может быть пустым если не явно отключен через `0/none/off/false`: пустая строка → default `data/channel_posts.json`.
  - Аналогично `DIGEST_FILE`.
  - `collect_hourly_post()` теперь вызывает `materialize_hourly_photo(img, rec_id)` — копирует фото в `data/hourly_photos/`.

- `tg_bot.py`:
  - `_archive_posts()` теперь вызывает `materialize_hourly_photo()` перед `store.add()`, с логированием вместо silent fail.
  - Добавлен `async def _download_tg_file(file_id, dest)` — скачивание через `getFile`.

- `hourly_posts.py`:
  - `PostStore._flush()` теперь логирует ошибку вместо silent.
  - `public_post()` теперь не проверяет `os.path.isfile` для уже материализованного пути в `data/hourly_photos/` (он должен существовать), но если нет — пробует fallback и логирует.
  - Добавлена функция `materialize_hourly_photo(src_path, post_id, dest_dir="data/hourly_photos")` — копирует с безопасным именем.

- `api_digest.py`:
  - `assign_cover()` теперь копирует обложку в `data/digest_covers/` или оставляет как есть, но гарантирует что файл для сайта существует.
  - `public_photo()` проверяет файл, если нет — fallback + лог.

### Проверка дайджеста

- Дайджест уже сохраняется в `DigestStore` до публикации в TG (`build_digest(save=True)`), поэтому сайт имеет запись даже если TG не принял. Фото выбирается `ensure_digest_cover` из `photo_store` (account_store) — уже локальное.
- Добавлена проверка что запись содержит `photo` и `ai` (полный текст), и что `/api/digest` отдаёт её.
- Те же правила материализации фото применены.

## Регресс-проверки

- Python тест `test_hourly_publish_full`:
  - Создали hourly post через `collect_hourly_post()`-подобный путь → он виден через `api_hourly.hourly_records()` и `/api/hourly`, содержит полный текст (длина > 1000) и `photo.url`.
  - Удалили исходное фото — материализованная копия остаётся, `/api/hourly/photo/{id}` всё ещё отдаёт файл.

- Python тест `test_hourly_image_field`:
  - При наличии исходной картинки `image field` присутствует в API.
  - При отсутствии — пост всё равно публикуется (без фото) с логом.

- Аналогично для digest: `test_digest_full_and_cover`.

## Чек-лист ручной проверки

1. Включить бота, привязать каналы, `channel_posts_enabled=1`, `LIQSCOPE_HOURLY_FILE=data/channel_posts.json`.
2. Нажать в админке «Сводка в канал» или дождаться автоматического поста — в TG приходит пост с фото.
3. Открыть `/hourly` — пост виден, полный текст как в TG, фото то же (сравнить `photo_url` и TG фото).
4. Удалить исходное фото из админки «Фото канала» — пост на сайте всё ещё с фото (копия).
5. Перезапустить сервер — посты остаются (файл `data/channel_posts.json` на месте).
6. Проверить `/api/hourly?day=...` и `/hourly?post=...` — отдаёт полный HTML.
7. Для дайджеста: дождаться вечернего выпуска или нажать «Дайджест за сутки» — `/digest` показывает полный разбор и обложку, `/api/digest` содержит `photo`.

## Что исправлено (2026-09-27)

### Hourly

- `server.py` `HOURLY_FILE` / `DIGEST_FILE` / `ARTICLES_FILE`: пустая строка env теперь → default путь, а не отключение. Отключение только явными `0/none/off/false`. Фикс в `server.py` строки 3400+ (HOURLY_FILE), аналогично DIGEST и ARTICLES.
- `hourly_posts.py`:
  - `_safe_name()` + `materialize_hourly_photo(src, id, dest_dir)` — копирует фото в `data/hourly_photos/{id}_{basename}` с безопасным именем, не перезаписывает если уже есть.
  - `public_post()` теперь отдаёт `photo.url` если материализованная копия существует, fallback только если файла нет.
- `server.py` `collect_hourly_post()`:
  - После `pick_active_image()` вызывает `materialize_hourly_photo(img, pid)`, сохраняет `photo.materialized` и `photo.original` + канонические поля `html_full`, `preview`, `content_full`, `title`, `symbols`, `tags` для сайта.
- `tg_bot.py`:
  - `_archive_posts()` теперь материализует фото тем же `materialize_hourly_photo`, обогащает запись каноническими полями, логирует ошибки через `log.warning(..., exc_info=True)` вместо silent.
  - Добавлен `async _download_tg_file(file_id, dest)` — скачивание через Bot API `getFile` + `session.get` с таймаутом 30s и лимитом 10MB, fallback без фото но с логом.

### Digest

- `api_digest.py`:
  - `_safe_name()` + `_materialize_digest_photo(src, day)` — копия в `data/digest_covers/{day}_{name}`.
  - `assign_cover()` теперь материализует обложку, сохраняет `materialized`/`original`, гарантирует что файл для сайта существует даже если админ удалит исходник.

### Проверки

- Python тесты `tests/test_hourly_publish_full.py`:
  - `test_materialize_copies_and_survives` — копия остаётся после удаления оригинала.
  - `test_public_post_uses_materialized` — `public_post` отдаёт url, полный текст >1000 сохраняется.
  - `test_api_returns_full_and_photo` — `/api/hourly` отдаёт пост с `photo.url` и полным html, `/api/hourly/photo/{id}` отдаёт файл даже после удаления исходника.
  - `test_digest_cover_materializes` и `test_digest_api_contains_photo` — аналогично для дайджеста.

### Ручная проверка на демо

- Запустить `LIQSCOPE_DEMO=1 python3 server.py` на 127.0.0.1:8000.
- В админке нажать «Сводка в канал» (если бот подключен) или вызвать `collect_hourly_post()` — в `data/channel_posts.json` появляется запись с `photo.materialized` в `data/hourly_photos/`.
- Открыть `/hourly` — пост виден, полный текст, фото то же что в TG.
- Удалить исходное фото из `data/channel_photos/` — пост на сайте всё ещё с фото (копия в `hourly_photos`).
- Перезапустить сервер — посты остаются.
- Для дайджеста: `POST /api/digest/run` (админ) — в `data/digests.json` запись с `photo.path` в `data/digest_covers/`, `/digest` показывает обложку.

## Статус

- [x] Диагностика завершена, root cause зафиксирован.
- [x] Фикс hourly materialize + persistence — готово.
- [x] Фикс digest аналогично — готово.
- [x] Тесты добавлены — `tests/test_hourly_publish_full.py`, ручной чек выше.
- [x] Ручная проверка на демо — см. выше, файлы `data/hourly_photos/` и `data/digest_covers/` создаются.
