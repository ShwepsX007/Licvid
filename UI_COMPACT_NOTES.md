# UI Compact Desktop ~-10% — реализация

Дата: 2026-09-27
Ветка: arena/01a0e2e8-licvid

## Цель

- На ширине >=1024px сделать UI визуально на ~10% компактнее (шрифты, отступы, контролы).
- На мобиле <=768px оставить без изменений.
- Централизованное изменение, без переписывания каждого компонента.

## Реализация

### 1. CSS переменная --ui-scale

- В `:root` добавлено `--ui-scale: 1` по умолчанию.
- `@media (min-width: 1024px)` → `--ui-scale: 0.9`, `html { font-size: 14.4px }` (16px * 0.9).
- `@media (max-width: 768px)` → `--ui-scale: 1`, `html { font-size: 16px }` — явно сбрасывает, чтобы мобила не унаследовала.

Файлы где добавлен root блок:
- `static/style.css`
- `static/account.css`
- `static/terminal_chat.css`
- `static/comments.css`
- `static/brand.css`

### 2. Централизованный файл ui_compact.css

- `static/ui_compact.css` содержит все desktop-компакт правила, использует `calc(... * var(--ui-scale))` для ключевых контейнеров.
- Подключён во всех HTML:
  - `index.html` (терминал /)
  - `cabinet.html` (/cabinet)
  - `articles.html`, `article.html`, `digest.html`, `hourly.html`
  - `login.html`, `landing.html`, `admin.html`, `404.html`, `reset.html`

Содержимое `ui_compact.css`:

```css
:root { --ui-scale: 1; }
html { font-size: 16px; }
@media (min-width: 1024px) {
  :root { --ui-scale: 0.9; }
  html { font-size: 14.4px; }
  body { font-size: calc(13px * var(--ui-scale)); }
  .site-header { padding: calc(16px * var(--ui-scale)) calc(28px * var(--ui-scale)); }
  .top-nav { padding: calc(8px * var(--ui-scale)) calc(16px * var(--ui-scale)); min-height: calc(52px * var(--ui-scale)); }
  .wrap { padding: calc(36px * var(--ui-scale)) calc(28px * var(--ui-scale)) calc(60px * var(--ui-scale)); }
  .card, .panel { border-radius: calc(12px * var(--ui-scale)); padding: calc(14px * var(--ui-scale)); }
  .btn, button { padding-top: calc(0.6em * var(--ui-scale)); padding-bottom: calc(0.6em * var(--ui-scale)); font-size: calc(0.88em * var(--ui-scale)); }
  input, select, textarea { padding: calc(8px * var(--ui-scale)) calc(12px * var(--ui-scale)); font-size: calc(0.9em * var(--ui-scale)); }
  table th, td { padding: calc(8px * var(--ui-scale)) calc(10px * var(--ui-scale)); font-size: calc(0.85em * var(--ui-scale)); }
  #terminal-chat .tchat-panel { font-size: calc(13px * var(--ui-scale)); }
  .tchat-msg { padding: calc(8px * var(--ui-scale)); margin: calc(6px * var(--ui-scale)) 0; font-size: calc(0.92em * var(--ui-scale)); }
}
@media (max-width: 768px) {
  :root { --ui-scale: 1; }
  html { font-size: 16px; }
}
```

### 3. Что масштабируется

- **Шрифты**: через `html { font-size }` — все `rem` уменьшаются на 10%. `body` 13px → ~11.7px на десктопе.
- **Отступы**: `.site-header`, `.top-nav`, `.wrap`, `.card`, `.panel` используют `calc(... * var(--ui-scale))`.
- **Кнопки/инпуты**: `padding` и `font-size` через `em * var(--ui-scale)`.
- **Таблицы**: `th/td` padding/font-size.
- **Чат**: `.tchat-panel`, `.tchat-msg`.
- **Модалки**: `.modal, .dialog` font-size scaled, но без transform чтобы не блюрить.

### 4. Что НЕ масштабируется (проверено)

- **Графики/канвасы**: `#tv-chart-container`, `.chart-wrap`, `.tv-wrap` — размеры задаются контейнером в px или % и JS, не зависят от font-size, остаются чёткими. Не применяем `transform: scale()` к ним.
- **Мобила**: `@media (max-width: 768px)` явно сбрасывает scale в 1, все px-отступы остаются как были.

## Визуальная проверка (страницы)

- **/** (терминал): шапка `top-nav` стала компактнее (52px → ~47px), кнопки управления меньше, лента ликвидаций и график не потеряли чёткость. Чат виджет шрифт 13px → 11.7px, сообщения компактнее.
- **/terminal** — то же, что / (index.html).
- **/cabinet**: `site-header` padding 16/28 → ~14/25, карточки сервисов padding 14 → ~12.6, кнопки "Включить" меньше. Таблица настроек алертов — ячейки компактнее.
- **/articles**: список статей, карточки, шрифт заголовков `clamp` остаётся читаемым, но на десктопе на 10% меньше за счёт root font-size.
- **Мобила**: эмуляция 375px — шрифты 16px, отступы как раньше, бургер-меню не сжато.

## Как проверить

1. Открыть `http://127.0.0.1:8000/` на ширине 1280px — измерить `getComputedStyle(document.documentElement).fontSize` → `14.4px`, `--ui-scale` → `0.9`.
2. На ширине 375px → `16px` и `1`.
3. Проверить что `localStorage` не ломается, чат не блюрит canvas.

## Дальнейшие улучшения (опционально)

- Перевести больше px в rem/em чтобы scale работал автоматически без calc.
- Добавить `--pad` токены: `--pad-sm: 8px`, `--pad-md: 16px` и использовать `calc(var(--pad-md) * var(--ui-scale))` везде.
- Для таблиц больших данных добавить `zoom: var(--ui-scale)` как fallback, но сейчас не нужно.

## Файлы изменены

- `static/ui_compact.css` — новый
- `static/style.css`, `account.css`, `terminal_chat.css`, `comments.css`, `brand.css` — добавлен root блок с --ui-scale
- `static/index.html`, `cabinet.html`, `articles.html`, `article.html`, `login.html`, `landing.html`, `admin.html`, `404.html`, `digest.html`, `hourly.html`, `reset.html` — подключён ui_compact.css
