# Levels Drawing Notes — расчётные уровни

Дата: 2026-09-27
Ветка: arena/01a0e2e8-licvid

## Что было

- В `static/app.js:drawLiqLevels()` полосы уровней рисовались с `globalAlpha 0.8-0.96` и `fillStyle rgba(255,45,149,0.92)` / `rgba(0,214,255,0.92)` — почти непрозрачно (0.74-0.88 итоговой альфы).
- Под полосами свечи становились плохо читаемы, особенно когда много уровней.
- Клип `ctx.rect(0,0,plotRight,plotBottom)` уже был, но `priceAxisWidthPx()` иногда возвращал 68px по умолчанию, а не реальную ширину шкалы, из-за чего при узком окне полоса могла заходить на цифры цены (зазор 1px недостаточен).
- Пунктирные магниты рисовались с `globalAlpha 0.8` и `lineWidth 1`, без подсветки, без ограничения по plot area (рисовались от 6px до xL, что внутри plot, но без учёта highlight).

## Что стало

### Task 1: убрать фон под полосами и не закрывать свечи/цифры цены

- **Альфа**: теперь `globalAlpha = magnet||active ? 0.18 : 0.12` для заливки, и `0.35/0.22` для обводки. Итоговая видимость ~0.11-0.16, свечи читаемы.
- **Контур**: добавлен тонкий `strokeRect` с тем же цветом, но чуть выше альфой, чтобы масса была видна даже при низкой заливке.
- **Клип**: сохранён `ctx.rect(0,0,plotRight,plotBottom)` + `clip()`, где `plotRight = W - priceAxisWidthPx()`. `priceAxisWidthPx()` берёт max из API `priceScale().width()` и DOM ширины ячейки, с fallback 68px и ограничением `W*0.5`. Теперь правый край колонки `xL = plotRight - colW` всегда вплотную к шкале, но не поверх цифр.
- **Лейблы**: фон лейбла `rgba(4,7,13,0.88)` → остаётся, но рисуется только для сильных уровней (magnet или top 4 по массе, max 6 лейблов), чтобы не загромождать.

Параметры:
- `barH = clamp(med*0.72, 2, 7)` — высота полосы по медиане зазоров.
- `maxBar = colW - 8` — максимальная длина, чтобы не вылезать за колонку.
- `colW = clamp(plotRight*0.15, 68, 116)` и не более 42% plotRight.

### Task 2: пунктирный уровень триггер

- Добавлены константы:
  - `DASHED_TRIGGER_PCT = 0.0001` (0.01%)
  - `DASHED_COOLDOWN_MS = 15000`
  - `DASHED_FADE_MS = 3000`
- `levelHighlights: { [price]: {lastTrigger, inZone, highlightUntil} }`
- `shouldTrigger(price, level, state, now)` — чистая функция: `abs(price-level)/level <= 0.0001` и `!inZone` и `now-lastTrigger >= cooldown`. При выходе из зоны `inZone=false`.
- `checkDashedLevelTriggers(price)` вызывается из `updatePriceDisplay(price)` и `prices` WS case — O(N) по магнитам (обычно <10), допустимо.
- При триггере:
  - `highlightUntil = now + 3000`
  - `playLevelBeep()` — мягкий beep 880→440Hz 0.38s, с учётом `levelsAlertEnabled` и `localStorage.muted`.
  - `queueRedraw()` — перерисовка.
- Отрисовка highlight в `drawLiqLevels`:
  - Если `now < highlightUntil`, `hlAlpha = (highlightUntil-now)/3000`, рисуем красным `#ff2a5f` с `shadowBlur = 8*alpha` (glow) и `lineWidth 2` для пунктира, и для полосы `fill #ff2a5f` с `globalAlpha 0.35*alpha+0.12` и glow `shadowBlur 10*alpha`.
  - Плавное затухание за 3s через `requestAnimationFrame` не нужно — каждый `queueRedraw` пересчитывает alpha по времени.

### Настройка вкл/выкл

- `state.levelsAlertEnabled` default true, хранится в `localStorage liqscope.levelsAlert`.
- Кнопка `levels-alert-toggle` в `index.html` рядом с `levels-toggle`, toggling active class и текста 🔔/🔕.

## Файлы

- `static/app.js` — `drawLiqLevels`, `priceAxisWidthPx`, `levelsPlotBox`, `shouldTrigger`, `checkDashedLevelTriggers`, `playLevelBeep`, toggle.
- `static/index.html` — кнопка `levels-alert-toggle`.

## Ручная проверка

1. Открыть `/terminal`, включить «Ликвидации (оценка)» — полосы полупрозрачные, свечи под ними видны.
2. Изменить ширину окна — шкала цены (цифры справа) не перекрывается полосами.
3. Дождаться магнита (пунктир) близко к цене — при подходе ±0.01% должен пикнуть и покраснеть с свечением, затем за 3s погаснуть.
4. Не должно флудить: один уровень не триггерится чаще 15s, пока цена не выйдет из зоны и снова не войдёт.
5. Выключить «🔔 Уровни» — звук и подсветка не срабатывают, но уровни рисуются.
6. Мобила 375px — уровни и кнопка работают, не ломают layout.

## Скриншоты (описание, т.к. нет автоматических)

- До: полосы ярко-маджента/циан, свечи под ними темнеют, цифры цены иногда под полосой.
- После: полосы бледные (alpha 0.12), свечи читаемы, цифры цены чистые, магнит при триггере красный с glow.

## Дальше

- Перевести больше px в rem для компактности.
- Добавить настройку порога ±0.01% в админку.
- Для больших N магнитов — фильтровать только видимые по `priceToCoordinate`.
