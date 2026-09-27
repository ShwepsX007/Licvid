# CHAT READ STATE — диагностика и фикс «вечных сообщений»

Дата: 2026-09-27
Ветка: arena/01a0e2e8-licvid

## Какие чаты затронуты

1. **Терминальный чат** (`terminal_chat.py` + `static/terminal_chat.js`): общий чат терминала, 3 дня истории, таблица `terminal_chat(id, user_id, display_name, text, created_at, is_admin)`. Пишут зарегистрированные, читают все.
2. **Личные чаты** (`private_chat.py` + `private_chats`, `private_messages`, `private_reads`): DM между двумя пользователями, 30 дней, per-room `last_read_id` в `private_reads(room_id, user_id, last_read_id)`. Есть `private_notify_counts` для бейджа.
3. **Сервисные сигналы** (`service_chat.py` + `user_service_chat`): персональные сигналы per-user (book/pump/alerts/corr), 30 дней, таблица `user_service_chat(id, user_id, kind, text, meta, created_at)`. Нет таблицы чтения.
4. **Поддержка** (`support_chat.py` + `support_chat`, `support_reads`): per-thread чтение `user_last_read_id` / `admin_last_read_id` уже есть.
5. **Комментарии/нотификации** — используют тот же механизм чата (не отдельный).

## Откуда берутся сообщения при входе (до фикса)

### Terminal chat
- **HTTP**: `GET /api/terminal/chat?limit=100&after_id=0` — всегда последние 100, без учёта пользователя. В `terminal_chat.js:loadInitial()` вызывается `fetchList(0,100)` независимо от `LS_LAST_ID`.
- **localStorage**: `liqscope.tchat.lastId` хранит последний id, но используется только для poll `after_id=lastId`, не для фильтрации initial. Ключ глобальный, не per-user.
- **WS**: `type=terminal_chat` broadcast всем, клиент добавляет в ленту если `me` есть, иначе игнор.

**Почему после «прочитал» не исчезает**: нет per-user `last_read` на сервере, нет фильтра `id > last_read` в initial. После relogin `lastId` из LS остаётся, но initial всё равно грузит 100 последних и показывает их как актуальные. Бейдж `publicUnread` считается только для сообщений, пришедших когда view != public, а не для initial.

### Private DM
- **HTTP**: `GET /api/chat/dm/rooms` → `private_rooms_for()` считает unread через `private_reads`. `GET /api/chat/dm/{room}/messages?after_id=0` → `private_room_messages()` + сразу `private_mark_read(room, user, last_id)` — помечает прочитанным до конца.
- **localStorage**: нет кэша сообщений, только UI состояние.
- **WS**: `chat_dm` per-user broadcast.

**Почему после relogin старые DM снова как новые**: 
- Если пользователь прочитал комнату, `private_reads` обновляется. Но при выходе из системы LS не чистится, а при входе `rooms` загружаются с `unread` из БД — должно быть 0 если прочитано. Однако баг: `private_rooms_for()` берёт `updated_at>=edge` (30 дней) и считает unread через `id > last_read_id`, но если `private_reads` нет (0), то все сообщения считаются непрочитанными. При первом чтении `after_id=0` он помечает, но если пользователь никогда не открывал комнату после получения сообщения, а просто видел бейдж, то после relogin бейдж снова покажет то же количество как "новое". Также нет отдельного маркера "последнее посещение чата в целом" — только per-room.

### Service signals
- **HTTP**: `GET /api/chat/services?limit=100&after_id=0` — всегда последние 100 per-user, без read tracking. Клиент хранит `lastSvcId` только в памяти, не в LS, не per-user, не на сервере. После relogin `lastSvcId=0` → снова грузит 100 последних как новые, бейдж `svcUnread` показывает их как непрочитанные.
- **WS**: `service_chat` per-user broadcast, клиент добавляет если view != services.

**Почему вечные**: нет таблицы чтения, нет персистентного last_read, нет лимита "только после last_read + контекст".

## Требуемая модель (из задачи)

- Per-user, per-chat `last_read_ts` или `last_read_id` (id надёжнее, есть seq).
- Клиент при открытии/скролле до конца отправляет `markRead(chat_id, last_id)` с debounce 1-3 сек.
- При входе сервер отдаёт только последние N (50) как контекст + все новые после last_read, с лимитом по количеству и байтам.
- При logout — очистка LS ключей `chat:*` / `dm:*` или неймспейс по user_id.
- Защита от "WS init шлёт всё": `MAX_CHAT_HISTORY=50`.

## Реализация (фактическая)

### Сервер

- **Таблица** `chat_reads(user_id INTEGER, chat_id TEXT, last_read_id INTEGER, last_read_ts REAL, updated_at REAL, PRIMARY KEY(user_id, chat_id))` в `accounts.py`. Создаётся в `CREATE TABLE IF NOT EXISTS`, миграция на лету для старых БД.
- **Методы** `Store.chat_mark_read(user_id, chat_id, last_id, last_ts)`, `chat_last_read()`, `chat_unread_counts()` (считает unread для public/services/dm/support).
- **Эндпоинты** в `terminal_chat.py`:
  - `GET /api/chat/read_state` — auth, отдаёт `reads` map + `unread` counts.
  - `POST /api/chat/read` {chat_id, last_read_id, last_read_ts} — auth, монотонно растёт (MAX).
  - `GET /api/chat/history?chat_id=public|services&before_id=&before_ts=&limit=50` — пагинация старше, кнопка "Загрузить ещё".
  - `GET /api/terminal/chat` теперь `limit` default 50, max 100, и при `after_id==0` отдаёт не более `MAX_CHAT_HISTORY=50`.
  - `GET /api/chat/services` аналогично capped 50.
- **Private DM**: при `GET /api/chat/dm/{room}/messages` теперь также вызывает `chat_mark_read(f"dm:{room_id}")` и `chat_mark_read("dm")` для общего бейджа.
- **Constants**: `MAX_CHAT_HISTORY=50`, `MAX_CHAT_BYTES=128k` (защита от огромного ответа), `MAX_SVC_HISTORY=50`.

### Клиент (`static/terminal_chat.js` v11)

- **Per-user LS namespace**:
  - `LS_LAST_ID = liqscope.tchat.lastId`, но хранится как `liqscope.tchat.lastId.{uid}` если uid есть. Аналогично `svcLastId`, `supLastId`, `open`.
  - Helpers `lsKey(base, uid)`, `lsGet`, `lsSet`, `lsGetInt`, `getStoredUid()`, `setStoredUid()`, `clearUserChatKeys(uid)`.
  - При смене пользователя (prevUid != curUid) — очистка старых ключей, загрузка per-user lastId.
  - При logout — `clearUserChatKeys(uid)` + `setStoredUid(0)`, sweep `chat:*` / `dm:*`.
- **Read tracking**:
  - `serverReads` map из `/api/chat/read_state`.
  - `markRead(chat_id, last_id, ts)` debounced 1200ms, POST `/api/chat/read`.
  - `isNewAfterRead(msg, chat_id)` — сообщение считается новым только если `id > last_read_id` (сервер + LS). При init старые сообщения не считаются новыми.
  - `fetchReadState()` вызывается в `loadInitial()` до `fetchList`, устанавливает `serverReads` и начальные unread из сервера.
  - `saveLastIds()` пишет per-user LS.
- **History limit**:
  - `MAX_HISTORY=50` везде, `fetchList(0, 50)` вместо 100.
  - Кнопка "Загрузить ещё" вверху ленты: `GET /api/chat/history?chat_id=...&before_id=firstId&limit=50`, вставляет старше в начало, сохраняет scroll.
- **Triggers для markRead**:
  - `setView(public|services|support)` → `markRead(chat_id)` + сброс бейджа.
  - `setOpen(true)` → markRead текущего view.
  - scroll near bottom (gap <120) в `listEl` / `svcListEl` → markRead + сброс unread.
  - focus / visibilitychange → markRead.
  - poll: если новые сообщения пришли когда чат открыт и скролл внизу — сразу markRead, иначе бейдж только для реально новых после last_read.
  - WS: аналогично, если view открыт и внизу — markRead, иначе бейдж.
  - `doSend` после отправки своего сообщения — markRead.
- **Dedup**: `renderMsgs` уже проверяет `data-mid` existence, плюс `isNewAfterRead` предотвращает показ <=last_read как new.
- **Logout**:
  - В `terminal_chat.js` перехват `fetch("/api/auth/logout")` и событие `liqscope:logout` → очистка.
  - В `account.js` оба logout handler теперь чистят `liqscope.tchat.*.{uid}` и `chat:/dm:` ключи и диспатчат событие.

### Тесты

- **Python**: `tests/test_chat_read_state.py` — создаёт двух юзеров, пишет 3 сообщения в public, юзер помечает read до id=2, проверяет `chat_last_read` и что `chat_unread_counts` =1, после markRead до 3 — unread 0. Аналогично services, dm.
- **JS smoke**: в `CHAT_READ_STATE.md` ручной чек-лист + автоматический тест в `tests/test_chat_read_state.py` проверяет server не отдаёт старое как новое после markRead (fetch after_id=last_read).

## Ручной чек-лист проверки

1. **Public chat read**:
   - Войти как user A, написать 2 сообщения в общий чат, прочитать (открыть чат, прокрутить до конца).
   - Выйти, войти снова как A — должно показать последние 50 как контекст, но не как "непрочитанные" (бейдж 0). Новые сообщения от B после last_read — показываются и бейдж +1.
   - Проверить LS: ключ `liqscope.tchat.lastId.{A.id}` существует, `liqscope.tchat.lastId` глобальный не используется для A.

2. **Service signals**:
   - Включить алерты в кабинете, дождаться сигнала, открыть таб "Сервисы" (должен вызвать markRead).
   - Relogin — сигналы не должны снова гореть как новые, бейдж 0, в ленте последние 50.
   - Проверить `/api/chat/read_state` → `reads.services.last_read_id` >= последнего сигнала.

3. **DM**:
   - User A приглашает B, B принимает, A пишет сообщение.
   - B видит бейдж 1, открывает DM — бейдж 0, mark_read.
   - B выходит, входит снова — бейдж 0, сообщение не как новое.
   - Проверить `private_reads` и `chat_reads` dm:*.

4. **Logout очистка**:
   - Войти как A, открыть чаты, выйти — LS ключи `tchat.*.{A.id}` должны удалиться или быть изолированы, чтобы при входе как C не подтянулись метки A.
   - Проверить `localStorage.getItem("liqscope.tchat.curUid")` == 0 после logout.

5. **History limit**:
   - Написать 60 сообщений, перезайти — в ленте только 50 последних, кнопка "Загрузить ещё" загружает старше.
   - Проверить `/api/terminal/chat?limit=200` отдаёт не более 100, а default 50.

6. **WS init не шлёт всё**:
   - Открыть WS, проверить что initial history не 1000, а 50.
   - После markRead новые сообщения приходят только после last_read.

7. **Guests**:
   - Гость видит только support, LS `supGuest` работает, без auth `/api/chat/read_state` отдаёт guest:true и не падает.
