/**
 * LiqScope — мультиязычность всего сайта.
 *
 * Поддерживаются: русский (по умолчанию), английский, китайский (мандарин),
 * хинди, испанский. Язык определяется по серверу (?lang=, cookie, Accept-Language),
 * затем по браузеру, переключается в шапке и запоминается в localStorage.
 *
 * Использование:
 *   <script src="/static/i18n.pages.js"></script>   // словари кабинета и страниц
 *   <script src="/static/i18n.js"></script>
 *   LiqScopeI18n.init();                    // применить сохранённый/броузерный язык
 *   LiqScopeI18n.t("feed.title");           // перевод строки
 *   LiqScopeI18n.plural(n, [one, few, many]);
 *   LiqScopeI18n.onChange(fn);              // перерисовка динамики при смене языка
 *
 * Статичный текст размечается атрибутами:
 *   data-i18n="key"               — textContent
 *   data-i18n-placeholder="key"   — placeholder
 *   data-i18n-title="key"         — title
 *   data-i18n-meta="key"          — content у <meta name="description">
 *
 * Динамический текст (кабинет, доски сервисов, админка) собирается кодом из
 * русских кусков, поэтому переводится постфактум — прямо в DOM: см. блок
 * «Перевод живого текста» ниже. Русский фрагмент ищется в текстовых узлах
 * и подменяется переводом из ``phrases`` (файл i18n.pages.js). Атрибут
 * ``data-i18n-skip`` защищает текст пользователя — переписку, рекламные
 * посты, письма — от перевода.
 */
(function (global) {
    "use strict";

    var LANGS = [
        { code: "ru", label: "Русский", flag: "🇷🇺", hreflang: "ru" },
        { code: "en", label: "English", flag: "🇬🇧", hreflang: "en" },
        { code: "zh", label: "中文", flag: "🇨🇳", hreflang: "zh-Hans" },
        { code: "hi", label: "हिन्दी", flag: "🇮🇳", hreflang: "hi" },
        { code: "es", label: "Español", flag: "🇪🇸", hreflang: "es" },
    ];
    var LOCALE_TAGS = { ru: "ru-RU", en: "en-US", zh: "zh-CN", hi: "hi-IN", es: "es-ES" };
    //: как язык зовётся в localStorage и в cookie сервера
    var STORE_KEY = "liqscope.lang";
    var COOKIE = "liqscope_lang";

    /* Партнёрская (реферальная) ссылка Gate — одна на весь сайт. В разметке
       ссылка прописана русская (чтобы работала и без JS, и в первом ответе
       сервера), а любой элемент с атрибутом data-gate-ref получает адрес по
       языку: русским — русскую страницу, остальным — английскую (регистрация
       Gate локализована не на все наши языки). */
    var GATE_REFS = {
        ru: "https://www.gate.com/ru/signup/VLFCAVWMBW?ref_type=103",
        en: "https://www.gate.com/signup/VLFCAVWMBW?ref_type=103",
    };

    /* =====================================================================
     * СЛОВАРИ
     * ===================================================================== */
    var RU = {
        "terminal.title": "LiqScope Terminal — Live Liquidation Stream & Cluster Chart",
        "terminal.meta": "LiqScope Terminal — live-лента ликвидаций крипто-фьючерсов с 8 бирж, свечной график, кластеры и профиль объёмов.",
        "brand.badge": "TERMINAL",
        "brand.home_title": "На главную — LiqScope Terminal",
        "layout.pane_split": "Высота блока: потяните за разделитель, двойной клик — сброс",
        "layout.feed_split": "Ширина ленты: потяните за разделитель",
        "layout.coins_split": "Высота блока «Лидеры»: потяните за разделитель",
        "layout.chart_split": "Высота графика: потяните вверх или вниз",
        "conn.connecting": "ПОДКЛЮЧЕНИЕ…",
        "conn.live": "LIVE WS",
        "conn.reconnecting": "ПЕРЕПОДКЛЮЧЕНИЕ…",
        "stats.24h": "Ликвидации (24ч)",
        "stats.1h": "Ликвидации (1ч)",
        "stats.5m": "Ликвидации (5м)",
        "stats.longs": "Longs",
        "stats.shorts": "Shorts",
        "stats.cvd24h": "24ч",
        "stats.cvd1h": "1ч",
        "stats.cvd5m": "5м",
        "stats.cvd_title": "CVD монеты на графике: тейкер-покупки минус продажи",
        "stats.oi_title": "Изменение OI монеты на графике за период — нажмите, чтобы сменить",
        "stats.box_liq": "Ликвидации",
        "stats.liq_title": "Ликвидации за выбранный период — нажмите, чтобы сменить",
        "stats.w1m": "1 мин",
        "stats.w5m": "5 мин",
        "stats.w15m": "15 мин",
        "stats.w30m": "30 мин",
        "stats.w1h": "1 ч",
        "stats.w4h": "4 ч",
        "stats.w24h": "24 ч",
        "ctrl.moneta": "Монета:",
        "ctrl.tf": "Таймфрейм:",
        "ctrl.minvol": "Мин. объем:",
        "ctrl.exchange": "Биржа:",
        "search.placeholder": "Поиск пары: BTC, SOL, GRAM…",
        "search.hint": "Поиск менее ликвидных монет.",
        "filter.all_usd": "Все ($0+) ▾",
        "filter.min_ge": "≥ ${v} ▾",
        "filter.usd_placeholder": "Например 10000",
        "filter.ok": "OK",
        "filter.preset_all": "Все",
        "filter.usd_note": "Показываются только ликвидации ≥ порога.",
        "filter.th_liq": "Ликвидации, $",
        "filter.th_short_liq": "Ликв",
        "filter.th_ge": "≥ ${v}",
        "filter.th_cvd": "CVD за свечу, $",
        "filter.th_oi": "OI Δ за свечу, $",
        "filter.presets_for": "Пресеты — для открытой вкладки ленты:",
        "filter.th_note": "Каждый порог фильтрует свою серию — ленту и метки на графике: ликвидации, CVD-треугольники и OI-шарики. Ноль = показывать всё.",
        "filter.exch_header": "Включённые биржи",
        "filter.all": "Все",
        "filter.none": "Ничего",
        "filter.all_exchanges": "Все биржи ▾",
        "filter.disabled": "Выключены ▾",
        "filter.thresholds_title": "Пороги объёма: ликвидации, CVD и OI Δ",
        "filter.exchanges_title": "Фильтр по биржам",
        "filter.cvd_placeholder": "Например 50000",
        "filter.oi_placeholder": "Например 1000000",
        "chart.cvd_current_title": "Кто двигает цену в текущей свече",
        "feed.show_all_title": "Показать все монеты",
        "lang.select_title": "Language / Язык",

        "chart.long": "Ликв. лонгов",
        "chart.short": "Ликв. шортов",
        "chart.whale": "Кит ($100k+)",
        "chart.whale_title": "Шкала китов: $100K — жёлтый … $1M+ — ярко-красный",
        "chart.profile": "📊 Профиль",
        "chart.toggle_title": "Свернуть/развернуть график",
        "chart.expand_title": "Развернуть график на весь экран",
        "chart.draw_title": "Рисовать фигуры теханализа",
        "chart.layers": "☰ Слои",
        "chart.layers_title": "Показать/скрыть кнопки слоёв",
        "chart.follow": "🎯 Авто",
        "chart.follow_full": "🎯 Автоследование за ценой",
        "chart.follow_on": "Автоследование включено: цена держится в 15% от верха и низа, свеча — в 3% от правого края; ручной сдвиг график возвращает сам. Нажмите, чтобы выключить",
        "chart.follow_off": "Автоследование выключено: график стоит там, куда его поставили. Нажмите, чтобы включить",
        "chart.liq_label": "Ликвидации",
        "chart.profile_label": "Профиль",
        "chart.cvd_label": "CVD",
        "chart.oi_label": "OI",
        "draw.line": "Трендовая линия",
        "draw.ray": "Луч",
        "draw.horiz": "Горизонтальная линия",
        "draw.pane_hint": "Здесь тоже можно рисовать фигуры теханализа",
        "draw.rect": "Прямоугольник",
        "draw.fib": "Уровни Фибоначчи",
        "draw.eraser": "Ластик: клик по фигуре удаляет её",
        "draw.clear": "Убрать все фигуры",
        "draw.close": "Закрыть рисование",
        "chart.collapse_title": "Вернуть обычный размер графика",
        "chart.profile_on": "Скрыть профиль объёмов ликвидаций",
        "chart.profile_off": "Показать профиль объёмов ликвидаций",
        "chart.liq": "⚡ Ликвидации",
        "chart.liq_on": "Скрыть прямоугольники ликвидаций с графика",
        "chart.liq_off": "Показать прямоугольники ликвидаций на графике",
        "chart.cvd": "🎯 CVD",
        "chart.oi": "● OI",
        "chart.live_liq": "Ликвидации текущей свечи",
        "chart.live_profile": "Ликвидации видимого диапазона",
        "chart.live_oi": "Изменение OI текущей свечи",
        "chart.oi_on": "Скрыть шарики открытого интереса",
        "chart.oi_off": "Показать шарики открытого интереса: 🟢 рост, 🔴 падение за свечу",
        "chart.book": "📖 Стакан",
        "chart.book_on": "Скрыть стены лимитных ордеров из стакана",
        "chart.book_off": "Показать стены лимитных ордеров: полосы по ценам крупных лимиток (L2)",
        "chart.pane_liq": "💥 Окно LIQ",
        "chart.pane_cvd": "🌊 Окно CVD",
        "chart.pane_oi": "📈 Окно OI",
        "chart.pane_liq_head": "💥 LIQ",
        "chart.pane_cvd_head": "🌊 CVD",
        "chart.pane_oi_head": "📈 OI",
        "chart.pane_close": "Скрыть окно индикатора",
        "chart.pane_liq_on": "Скрыть окно ликвидаций под графиком",
        "chart.pane_liq_off": "Показать окно ликвидаций под графиком",
        "chart.pane_cvd_on": "Скрыть окно CVD под графиком",
        "chart.pane_cvd_off": "Показать окно CVD (тейкер-дельта) под графиком",
        "chart.pane_oi_on": "Скрыть окно OI под графиком",
        "chart.pane_oi_off": "Показать окно открытого интереса под графиком",
        "chart.cvd_on": "Скрыть CVD-треугольники (перевес покупок/продаж в свече)",
        "chart.cvd_off": "Показать CVD-треугольники: ▲ покупатели, ▼ продавцы в каждой свече",
        "chart.cvd_buy": "CVD ▲ покупки",
        "chart.cvd_sell": "CVD ▼ продажи",
        "chart.error": "Библиотека графика не загрузилась (/static/lightweight-charts.js).",
        "src.no_link": "нет связи с биржей",
        "src.demo": "демо-поток",
        "src.waiting": "ожидание тиков…",
        "src.no_ticks": "тиков нет {s} с",
        "src.ticks": "⚡ тики: {n} · задержка {ms} мс",
        "feed.title": "⚡ ПРЯМОЙ ЭФИР ЛИКВИДАЦИЙ",
        "feed.tab_liq": "⚡ Ликвидации",
        "feed.tab_cvd": "🎯 CVD",
        "feed.tab_oi": "● OI",
        "feed.empty_cvd": "Нет значимых изменений CVD на монете графика…",
        "feed.empty_oi": "Нет значимых изменений OI на монете графика…",
        "feed.tab_book": "📖 Заявки",
        "feed.empty_book": "Стен в стакане этой монеты пока нет — ждём лимитники с бирж…",
        "feed.book_off": "Слой «📖 Стакан» выключен — включите его на панели слоёв",
        "feed.col_book": "СТЕНА ($)",
        "feed.book_live": "жива",
        "feed.book_eaten": "съедена",
        "feed.book_gone": "ушла",
        "feed.book_walls_n": "стен",
        "feed.book_levels_n": "ур.",
        "feed.col_cvd": "CVD ($)",
        "feed.col_oi": "OI Δ ($)",
        "feed.cvd_buy": "CVD ▲",
        "feed.cvd_sell": "CVD ▼",
        "feed.oi_up": "OI ▲",
        "feed.oi_down": "OI ▼",
        "feed.count_one": "{n} событие",
        "feed.count_few": "{n} события",
        "feed.count_many": "{n} событий",
        "feed.col_time": "Время",
        "feed.col_coin": "Монета",
        "feed.col_exch": "Биржа",
        "feed.col_type": "Тип",
        "feed.col_usd": "СУММА ЛИКВИДАЦИИ ($)",
        "feed.col_price": "Цена ($)",
        "feed.empty": "Ожидание событий с бирж…",
        "feed.open_chart": "Лента и график по {sym}",
        "feed.empty_flow": "Собираем поток по всем монетам — ждём первые минутки…",
        "flow.window": "Окно",
        "flow.window_val": "{n} мин",
        "flow.window_short": "{n}м",
        "flow.cvd": "CVD (покупки − продажи)",
        "flow.oi_delta": "Изменение OI",
        "flow.share": "Доля от объёма",
        "flow.oi_level": "OI сейчас",
        "flow.liq_long": "Ликвидации лонгов",
        "flow.liq_short": "Ликвидации шортов",
        "flow.vol": "Объём за окно",
        "flow.price": "Цена",
        "flow.note": "Строка — минутный поток монеты, а не свеча графика: так видно все монеты сразу, пока график остаётся на выбранной.",
        "feed.chip_title": "Показать все монеты",
        "feed.all_feed": "лента: все монеты",
        "top.title": "🔥 Лидеры по ликвидациям (24ч)",
        "top.by_vol": "💥 По объёму",
        "top.by_count": "🔢 По количеству",
        "top.count": "{n} ликв.",
        "sound.title": "Звуковой сигнал",
        "clear.title": "Очистить",
        "clear.btn": "🧹 Очистить",
        "clear.menu_title": "Очистить или вернуть историю",
        "clear.do": "🧹 Очистить график",
        "clear.undo": "↩️ Возобновить историю",
        "clear.undone": "история вернётся: события снова подтянутся с сервера",
        "modal.title": "Детали ликвидации",
        "modal.liq_of": "Ликвидация {sym}",
        "modal.exch": "Биржа:",
        "modal.src": "Источник:",
        "modal.src_tape": "выведено из ленты сделок и подтверждено по адресу через /info",
        "modal.src_feed": "поток биржи",
        "feed.tape_tip": "Hyperliquid не публикует ликвидации: событие вычислено по всплеску маркет-ордеров в ленте сделок и подтверждено /info по адресу кошелька",
        "modal.exchs": "Биржи:",
        "modal.type": "Тип:",
        "modal.long_desc": "LONG (принудительная продажа)",
        "modal.short_desc": "SHORT (принудительная покупка)",
        "modal.price": "Цена:",
        "modal.usd": "Сумма:",
        "modal.qty": "Количество:",
        "modal.time": "Время:",
        "modal.cvd_of": "CVD {sym}",
        "modal.oi_of": "OI {sym}",
        "modal.candle": "Свеча:",
        "modal.live": "формируется",
        "modal.direction": "Направление:",
        "modal.cvd_buy": "▲ Покупатели перевесили",
        "modal.cvd_sell": "▼ Продавцы перевесили",
        "modal.oi_up": "🟢 Интерес растёт — деньги заходят",
        "modal.oi_down": "🔴 Интерес падает — деньги уходят",
        "modal.strength": "Сила:",
        "modal.top10": "топ-10% свечей",
        "modal.p90mult": "{r} × p90",
        "modal.price_move": "Цена за свечу:",
        "modal.tier": "Тир:",
        "modal.tier_mini": "мини",
        "modal.cvd_about": "CVD — перевес агрессивных покупок над продажами за свечу: кто бил по рынку сильнее.",
        "modal.oi_about": "OI — изменение открытого интереса за свечу: рост — деньги заходят в позиции, падение — уходят.",
        "modal.book_title": "📖 Кластер заявок {sym}",
        "modal.book_zone": "Зона цены:",
        "modal.book_walls": "Стены в зоне:",
        "modal.book_about": "Стена — плотный уровень лимитных ордеров в стакане (L2). Плашка стоит на той свече, где стену нашли: живёт там, пока её не уберут или не исполнят; «съедена» — осталось меньше 70% пика, её разбирали маркет-ордерами.",
        "modal.clust_of": "Ликвидации {sym}",
        "modal.level": "Уровень:",
        "modal.events": "Событий:",
        "modal.whale": "кит",
        "modal.split": "Сплит:",
        "modal.clust_about": "Кластер — ликвидации свечи, слипшиеся рядом по цене. Связанные события подсвечены в ленте.",
        "sym.all": "🌐 ВСЕ",
        "sym.choose": "Выбрать монету",
        "sym.all_title": "Все монеты в ленте (график остаётся на текущей монете)",
        "sym.click": "Клик — лента и график по монете",
        "sym.shift": "Shift+клик — только перевести график, ленту не трогать",
        "sym.custom": "Добавлена вручную через поиск",
        "sym.empty": "Ничего не найдено",
        "search.nothing": "Ничего не найдено: \"{q}\"",
        "search.add": "＋ Добавить",
        "search.open": "📈 Открыть",
        "search.vol24": "24ч {v}",
        "search.added_ok": "✓ {sym} добавлена в список и открыта на графике",
        "search.added_fail": "⚠ Не удалось найти пару {sym}",
        "search.network_error": "⚠ Ошибка связи с сервером при поиске пары",
        "health.prices": "цены",
        "health.ticks": "тики",
        "health.connected": "подключено, событий: {n}",
        "health.noconn": "нет связи: {err}",
        "health.open": "Состояние бирж — наведите или нажмите",
        "health.summary": "{ok}/{n}",
        "health.gate_ref": "💠 Торговать на Gate — скидка на комиссию",
        "land.title": "LiqScope — терминал ликвидаций крипто-фьючерсов в реальном времени",
        "land.gate.link": "💠 Торговать на Gate — скидка на комиссию",
        "land.gate.note": "Партнёрская ссылка: регистрация на Gate.io поддерживает проект — терминал остаётся бесплатным.",
        "land.meta": "LiqScope — бесплатный live-терминал ликвидаций: Binance, Bybit, OKX, Gate.io, Bitget, HTX. Свечной график с кластерами, поиск любой пары, фильтры по объёму и биржам.",
        "land.badge": "Поток ликвидаций в прямом эфире",
        "land.h1.1": "Ликвидации крипто-фьючерсов",
        "land.h1.2": "в реальном времени",
        "land.sub": "LiqScope ловит принудительные закрытия позиций на Binance, Bybit, OKX, Gate.io, Bitget, HTX и так далее и рисует их на живом свечном графике — с кластерами, профилем объёмов и поиском любой торгуемой пары.",
        "land.hero.more": "Что умеет",
        "land.nav.live": "Живой эфир",
        "land.nav.features": "Возможности",
        "land.nav.how": "Как работает",

        "land.nav.digest": "Дайджест",
        "land.nav.hourly": "Сводки по часам",
        "hour.title": "Сводки по часам",
        "hour.meta": "LiqScope — сводки рынка крипто-фьючерсов каждые несколько часов: ликвидации окна, лидеры часов, оборот и открытый интерес. Те же посты, что вышли в канале, с фото.",
        "hour.lead": "Публикация сводки рынка несколько раз в сутки.",
        "hour.fresh": "Свежие сводки",
        "hour.pick": "Выбрать день",
        "hour.total": "Сводок в архиве",
        "hour.days": "Дней в архиве",
        "hour.channel": "Канал в Telegram",
        "hour.every": "каждые {h} ч",
        "hour.window": "за {h} ч",
        "hour.events": "{n} событий",
        "hour.n_posts": "сводок: {n}",
        "hour.has": "есть сводки",
        "hour.in_archive": "в архиве {n}",
        "hour.more": "В архиве {n} сводок: выше свежие, остальные открываются по дню.",
        "hour.tz": "дни по {tz}",
        "hour.prev": "Предыдущий месяц",
        "hour.next": "Следующий месяц",
        "hour.loading": "Загружаю…",
        "hour.empty": "Сводок пока нет — первая появится после поста в канал.",
        "hour.empty_day": "За этот день сводок нет.",
        "hour.photo_alt": "Фото сводки",
        "hour.link": "Ссылка на сводку",
        "hour.digest": "Дайджест",
        "hour.to_terminal": "К терминалу",
        "hour.cabinet": "Кабинет",
        "hour.admin": "Админка",
        "hour.login": "Кабинет",
        "dig.title": "Дневной дайджест рынка",
        "dig.meta": "LiqScope — дневной дайджест рынка: ликвидации, биржи, открытый интерес, цены и настроение за сутки.",
        "dig.lead": "Каждый вечер около 22:00 МСК — итоги дня: крупнейшая ликвидация, биржи, монеты с самым тяжёлым открытым интересом относительно оборота, цены топ-5 и настроение рынка.",
        "dig.archive": "Выпуски",
        "dig.fresh": "Свежие выпуски",
        "dig.pick": "Выбрать дату",
        "dig.more": "В архиве {n} выпусков: выше — свежие, остальные открываются по дате.",
        "dig.has": "есть выпуск",
        "dig.in_archive": "в архиве {n}",
        "dig.cabinet": "Кабинет",
        "dig.admin": "Админка",
        "dig.login": "Кабинет",
        "dig.prev": "Предыдущий месяц",
        "dig.next": "Следующий месяц",
        "dig.empty": "Первый выпуск появится вечером — архив пока пуст.",
        "dig.loading": "Загружаю…",
        "dig.published": "опубликован",
        "dig.draft": "черновик",
        "dig.mood": "Настроение",
        "dig.total": "Ликвидации за сутки",
        "dig.count": "Событий",
        "dig.back": "К терминалу",
        "ad.tag": "Реклама",
        "ad.open": "Открыть",
        "ad.prev": "Предыдущее",
        "ad.next": "Следующее",
        "dig.schedule": "Выходит каждый вечер ~22:00 МСК",
        "land.cta.open": "Открыть терминал",
        "land.stat.24h": "Ликвидаций за 24ч",
        "land.stat.1h": "За последний час",
        "land.stat.exch": "Бирж в эфире",
        "land.stat.ratio": "Лонги × шорты (24ч)",
        "land.cvd.title": "CVD {sym} — кто давит: покупатели или продавцы",
        "land.cvd.hint": "тейкер-покупки минус продажи · обновляется каждые 4 секунды",
        "land.oi.title": "OI {sym} — деньги в позициях: приток и отток",
        "land.oi.hint": "открытый интерес со всех бирж · обновляется каждые 4 секунды",
        "land.stat.cvd24h": "CVD за 24ч",
        "land.stat.cvd1h": "CVD за 1ч",
        "land.stat.cvd5m": "CVD за 5м",
        "land.stat.oiTotal": "Открытый интерес",
        "land.stat.oi24h": "OI Δ за 24ч",
        "land.stat.oi1h": "OI Δ за 1ч",
        "land.stat.oi5m": "OI Δ за 5м",
        "land.live.title": "Живой эфир прямо с сервера",
        "land.live.hint": "обновляется каждые 4 секунды · полная версия в терминале",
        "land.live.connect": "Подключение к серверу…",
        "land.feat.title": "Что умеет терминал",
        "land.feat.lead": "Никаких ключей и платной регистрации — только публичные данные бирж.",
        "land.card1.noun": "бирж",
        "land.card1.noun_one": "биржа",
        "land.card1.noun_few": "биржи",
        "land.card1.rest": "в одном эфире",
        "land.card1.desc": "Ликвидации с Binance, Bybit, OKX, Gate.io, Bitget, HTX сливаются в одну живую ленту без дублей и с нормализованными парами.",
        "land.card2.title": "Свечной график с кластерами",
        "land.card2.desc": "Живые свечи 1м–4ч, а каждая ликвидация рисуется прямоугольником точно в своей цене: маджента — лонги, циан — шорты, киты от $100K — по тепловой шкале от жёлтого к ярко-красному.",
        "land.card3.title": "Профиль ликвидаций по ценам",
        "land.card3.desc": "Индикатор на графике показывает, на каких ценовых уровнях сосредоточен объём ликвидаций за видимый период. Включается и выключается одной кнопкой.",
        "land.card4.title": "Поиск любой пары",
        "land.card4.desc": "Нужной монеты нет в топе? Введите код — терминал найдёт её по полному каталогу бирж и добавит на график. GRAM, новинки, редкие перпетуалы.",
        "land.card5.title": "Гибкие фильтры",
        "land.card5.desc": "Минимальный объём ликвидации задаётся вручную, биржи включаются и отключаются галочками. Настройки сохраняются в браузере.",
        "land.card6.title": "Работает с телефона",
        "land.card6.desc": "Одноколоночная мобильная раскладка: график, лента с кликабельной монетой и фильтры всегда под рукой.",
        "land.card7.title": "Тики без задержки",
        "land.card7.desc": "Каждая сделка по открытой монете сразу двигает свечу. Задержка в миллисекундах видна прямо над графиком.",
        "land.card8.title": "Диагностика в реальном времени",
        "land.card8.desc": "Индикаторы в шапке показывают, какая биржа подключена, сколько событий пришло и откуда идут цены. Источники автоматически переключаются при сбое.",
        "land.card9.title": "Бесплатные алерты",
        "land.card9.desc": "После регистрации можно получать бесплатные алерты о ликвидациях в Telegram: своя монета, порог и окно — и ничего платить не нужно.",
        "land.card9.cta": "Зарегистрироваться бесплатно",
        "land.how.title": "Как это работает",
        "land.step1.title": "Слушаем биржи",
        "land.step1.desc": "Публичные WebSocket-каналы ликвидаций и сделок — напрямую, без посредников.",
        "land.step2.title": "Нормализуем данные",
        "land.step2.desc": "Пары, объёмы и стороны приводятся к единому виду, дубли отсекаются.",
        "land.step3.title": "Рисуем на графике",
        "land.step3.desc": "Ликвидации ложатся на свечи в свою цену, профиль показывает их объёмы по уровням.",
        "land.step4.title": "Вы смотрите",
        "land.step4.desc": "Лента, статистика 5м/1ч/24ч, топ монет — всё обновляется в реальном времени.",
        "land.cta.title": "Готовы увидеть рынок изнутри?",
        "land.cta.note": "Бесплатно · без платной регистрации · без API-ключей · данные с публичных потоков бирж",
        "land.footer.copy": "© 2026 LiqScope Terminal · данные с публичных WebSocket бирж",
        "cookie.title": "Сайт использует cookie",
        "cookie.text": "Это стандартная практика: cookie держат вас в аккаунте, помнят выбранный язык, а ещё помогают считать посещаемость — без них мы не отличаем живых гостей от служебных запросов.",
        "cookie.ok": "Понятно",
        "cookie.more": "Что именно хранится",
        "cookie.sid": "вход в аккаунт — пока вы не выйдете сами",
        "cookie.vid": "номер гостя — только для статистики посещений",
        "cookie.pref": "язык, тема и настройки графиков — в вашем браузере",
        "cookie.note": "Продолжая пользоваться сайтом, вы соглашаетесь с этим.",
        "land.footer.race": "Не является инвестиционной рекомендацией",
        "land.footer.open": "Открыть терминал →",
            "gate.title": "Зарегистрируйтесь бесплатно",
        "gate.text": "Слои графика доступны всем. При регистрации вы получите сигналы в Telegram и терминал без рекламы.",
        "gate.f1": "Сигналы в Telegram",
        "gate.f2": "Терминал без рекламы",
        "gate.f3": "Кабинет и сервисы",
        "gate.cta": "Зарегистрироваться бесплатно",
        "gate.later": "Продолжить",
        "gate.close_title": "Закрыть окно",
        "gate.trial_badge": "{min}м",
        "gate.trial_title": "Слои доступны всем",
};

    var EN = {
        "terminal.title": "LiqScope Terminal — Live Liquidation Stream & Cluster Chart",
        "terminal.meta": "LiqScope Terminal — live crypto futures liquidation feed from 8 exchanges, candlestick chart, clusters and volume profile.",
        "brand.badge": "TERMINAL",
        "brand.home_title": "Back to home — LiqScope Terminal",
        "layout.pane_split": "Block height — drag the divider, double click to reset",
        "layout.feed_split": "Feed width — drag the divider",
        "layout.coins_split": "Top coins block height — drag the divider",
        "layout.chart_split": "Chart height — drag up or down",
        "conn.connecting": "CONNECTING…",
        "conn.live": "LIVE WS",
        "conn.reconnecting": "RECONNECTING…",
        "stats.24h": "Liquidations (24h)",
        "stats.1h": "Liquidations (1h)",
        "stats.5m": "Liquidations (5m)",
        "stats.longs": "Longs",
        "stats.shorts": "Shorts",
        "stats.cvd24h": "24h",
        "stats.cvd1h": "1h",
        "stats.cvd5m": "5m",
        "stats.cvd_title": "Chart coin CVD: taker buys minus sells",
        "stats.oi_title": "Change in the chart coin OI over the window — click to change",
        "stats.box_liq": "Liquidations",
        "stats.liq_title": "Liquidations in the selected window — click to change",
        "stats.w1m": "1 min",
        "stats.w5m": "5 min",
        "stats.w15m": "15 min",
        "stats.w30m": "30 min",
        "stats.w1h": "1 h",
        "stats.w4h": "4 h",
        "stats.w24h": "24 h",
        "ctrl.moneta": "Coin:",
        "ctrl.tf": "Timeframe:",
        "ctrl.minvol": "Min. size:",
        "ctrl.exchange": "Exchange:",
        "search.placeholder": "Find pair: BTC, SOL, GRAM…",
        "search.hint": "Search for less liquid coins.",
        "filter.all_usd": "All ($0+) ▾",
        "filter.min_ge": "≥ ${v} ▾",
        "filter.usd_placeholder": "e.g. 10000",
        "filter.ok": "OK",
        "filter.preset_all": "All",
        "filter.usd_note": "Only liquidations ≥ threshold are shown.",
        "filter.th_liq": "Liquidations, $",
        "filter.th_short_liq": "Liq",
        "filter.th_ge": "≥ ${v}",
        "filter.th_cvd": "CVD per candle, $",
        "filter.th_oi": "OI Δ per candle, $",
        "filter.presets_for": "Presets apply to the open feed tab:",
        "filter.th_note": "Each threshold filters its own series — the feed and the chart marks: liquidations, CVD triangles and OI balls. Zero shows everything.",
        "filter.exch_header": "Enabled exchanges",
        "filter.all": "All",
        "filter.none": "None",
        "filter.all_exchanges": "All exchanges ▾",
        "filter.disabled": "Disabled ▾",
        "filter.thresholds_title": "Volume thresholds: liquidations, CVD and OI Δ",
        "filter.exchanges_title": "Filter by exchanges",
        "filter.cvd_placeholder": "e.g. 50000",
        "filter.oi_placeholder": "e.g. 1000000",
        "chart.cvd_current_title": "Who moves the price in the current candle",
        "feed.show_all_title": "Show all coins",
        "lang.select_title": "Language / Язык",

        "chart.long": "Long liq.",
        "chart.short": "Short liq.",
        "chart.whale": "Whale ($100k+)",
        "chart.whale_title": "Whale scale: $100K yellow … $1M+ bright red",
        "chart.profile": "📊 Profile",
        "chart.toggle_title": "Collapse/expand chart",
        "chart.expand_title": "Expand chart to fullscreen",
        "chart.draw_title": "Draw analysis figures",
        "chart.layers": "☰ Layers",
        "chart.layers_title": "Show/hide layer buttons",
        "chart.follow": "🎯 Auto",
        "chart.follow_full": "🎯 Auto-follow the price",
        "chart.follow_on": "Auto-follow is on: the price is kept 15% off the top and bottom, the candle 3% off the right edge, and manual moves snap back. Click to turn it off",
        "chart.follow_off": "Auto-follow is off: the chart stays where you put it. Click to turn it on",
        "chart.liq_label": "Liquidations",
        "chart.profile_label": "Profile",
        "chart.cvd_label": "CVD",
        "chart.oi_label": "OI",
        "draw.line": "Trend line",
        "draw.ray": "Ray",
        "draw.horiz": "Horizontal line",
        "draw.pane_hint": "You can draw technical figures in this pane too",
        "draw.rect": "Rectangle",
        "draw.fib": "Fibonacci retracement",
        "draw.eraser": "Eraser: click a figure to delete it",
        "draw.clear": "Remove all figures",
        "draw.close": "Close drawing",
        "chart.collapse_title": "Exit fullscreen chart",
        "chart.profile_on": "Hide liquidation volume profile",
        "chart.profile_off": "Show liquidation volume profile",
        "chart.liq": "⚡ Liquidations",
        "chart.liq_on": "Hide liquidation blocks from the chart",
        "chart.liq_off": "Show liquidation blocks on the chart",
        "chart.cvd": "🎯 CVD",
        "chart.oi": "● OI",
        "chart.live_liq": "Current-candle liquidations",
        "chart.live_profile": "Visible-range liquidations",
        "chart.live_oi": "Current-candle OI change",
        "chart.oi_on": "Hide open interest balls",
        "chart.oi_off": "Show open interest balls: 🟢 rise, 🔴 fall per candle",
        "chart.book": "📖 Book",
        "chart.book_on": "Hide order-book walls",
        "chart.book_off": "Show order-book walls: bands at large resting limit orders (L2)",
        "chart.pane_liq": "💥 LIQ Pane",
        "chart.pane_cvd": "🌊 CVD Pane",
        "chart.pane_oi": "📈 OI Pane",
        "chart.pane_liq_head": "💥 LIQ",
        "chart.pane_cvd_head": "🌊 CVD",
        "chart.pane_oi_head": "📈 OI",
        "chart.pane_close": "Hide indicator pane",
        "chart.pane_liq_on": "Hide the liquidations pane under the chart",
        "chart.pane_liq_off": "Show the liquidations pane under the chart",
        "chart.pane_cvd_on": "Hide the CVD pane under the chart",
        "chart.pane_cvd_off": "Show the CVD (taker delta) pane under the chart",
        "chart.pane_oi_on": "Hide the OI pane under the chart",
        "chart.pane_oi_off": "Show the open interest pane under the chart",
        "chart.cvd_on": "Hide CVD triangles (taker buy/sell imbalance per candle)",
        "chart.cvd_off": "Show CVD triangles: ▲ buyers vs ▼ sellers in each candle",
        "chart.cvd_buy": "CVD ▲ buys",
        "chart.cvd_sell": "CVD ▼ sells",
        "chart.error": "Chart library failed to load (/static/lightweight-charts.js).",
        "src.no_link": "no exchange connection",
        "src.demo": "demo feed",
        "src.waiting": "waiting for ticks…",
        "src.no_ticks": "no ticks for {s}s",
        "src.ticks": "⚡ ticks: {n} · lag {ms} ms",
        "feed.title": "⚡ LIVE LIQUIDATION FEED",
        "feed.tab_liq": "⚡ Liquidations",
        "feed.tab_cvd": "🎯 CVD",
        "feed.tab_oi": "● OI",
        "feed.empty_cvd": "No significant CVD changes on the chart coin…",
        "feed.empty_oi": "No significant OI changes on the chart coin…",
        "feed.tab_book": "📖 Orders",
        "feed.empty_book": "No walls in this coin's book yet — waiting for limit orders from exchanges…",
        "feed.book_off": "The 📖 order-book layer is off — turn it on in the layers panel",
        "feed.col_book": "WALL ($)",
        "feed.book_live": "live",
        "feed.book_eaten": "eaten",
        "feed.book_gone": "gone",
        "feed.book_walls_n": "walls",
        "feed.book_levels_n": "lvls",
        "feed.col_cvd": "CVD ($)",
        "feed.col_oi": "OI Δ ($)",
        "feed.cvd_buy": "CVD ▲",
        "feed.cvd_sell": "CVD ▼",
        "feed.oi_up": "OI ▲",
        "feed.oi_down": "OI ▼",
        "feed.count_one": "{n} event",
        "feed.count_few": "{n} events",
        "feed.count_many": "{n} events",
        "feed.col_time": "Time",
        "feed.col_coin": "Coin",
        "feed.col_exch": "Exchange",
        "feed.col_type": "Side",
        "feed.col_usd": "LIQ. SIZE ($)",
        "feed.col_price": "Price ($)",
        "feed.empty": "Waiting for exchange events…",
        "feed.open_chart": "Feed and chart for {sym}",
        "feed.empty_flow": "Collecting the flow across all coins — waiting for the first minutes…",
        "flow.window": "Window",
        "flow.window_val": "{n} min",
        "flow.window_short": "{n}m",
        "flow.cvd": "CVD (buys − sells)",
        "flow.oi_delta": "OI change",
        "flow.share": "Share of volume",
        "flow.oi_level": "OI now",
        "flow.liq_long": "Long liquidations",
        "flow.liq_short": "Short liquidations",
        "flow.vol": "Volume in window",
        "flow.price": "Price",
        "flow.note": "The row is a per-minute flow of one coin, not a chart candle: every coin is visible at once while the chart stays on your pick.",
        "feed.chip_title": "Show all coins",
        "feed.all_feed": "feed: all coins",
        "top.title": "🔥 Top liquidations (24h)",
        "top.by_vol": "💥 By volume",
        "top.by_count": "🔢 By count",
        "top.count": "{n} fills",
        "sound.title": "Sound alert",
        "clear.title": "Clear",
        "clear.btn": "🧹 Clear",
        "clear.menu_title": "Clear or bring the history back",
        "clear.do": "🧹 Clear the chart",
        "clear.undo": "↩️ Restore history",
        "clear.undone": "history is back on: events will be re-fetched from the server",
        "modal.title": "Liquidation details",
        "modal.liq_of": "Liquidation {sym}",
        "modal.exch": "Exchange:",
        "modal.src": "Source:",
        "modal.src_tape": "inferred from the trade tape, confirmed via /info by wallet",
        "modal.src_feed": "exchange feed",
        "feed.tape_tip": "Hyperliquid does not publish liquidations: the event is inferred from a market-order burst in the trade tape and confirmed via /info",
        "modal.exchs": "Exchanges:",
        "modal.type": "Side:",
        "modal.long_desc": "LONG (forced sell)",
        "modal.short_desc": "SHORT (forced buy)",
        "modal.price": "Price:",
        "modal.usd": "Size:",
        "modal.qty": "Quantity:",
        "modal.time": "Time:",
        "modal.cvd_of": "CVD {sym}",
        "modal.oi_of": "OI {sym}",
        "modal.candle": "Candle:",
        "modal.live": "forming",
        "modal.direction": "Direction:",
        "modal.cvd_buy": "▲ Buyers prevailed",
        "modal.cvd_sell": "▼ Sellers prevailed",
        "modal.oi_up": "🟢 Interest rising — money flowing in",
        "modal.oi_down": "🔴 Interest falling — money flowing out",
        "modal.strength": "Strength:",
        "modal.top10": "top 10% of candles",
        "modal.p90mult": "{r} × p90",
        "modal.price_move": "Price action:",
        "modal.tier": "Tier:",
        "modal.tier_mini": "mini",
        "modal.cvd_about": "CVD — taker buy vs sell imbalance over the candle: who hit the market harder.",
        "modal.oi_about": "OI — open interest change over the candle: rising means money entering positions, falling means leaving.",
        "modal.book_title": "📖 Order cluster {sym}",
        "modal.book_zone": "Price zone:",
        "modal.book_walls": "Walls in zone:",
        "modal.book_about": "A wall is a dense L2 limit-order level. The plate sits on the candle where the wall was found and stays until it's pulled or filled; “eaten” means under 70% of its peak left — market orders were chewing through it.",
        "modal.clust_of": "Liquidations {sym}",
        "modal.level": "Level:",
        "modal.events": "Events:",
        "modal.whale": "whale",
        "modal.split": "Split:",
        "modal.clust_about": "A cluster merges a candle's liquidations at nearby prices. Linked events are highlighted in the feed.",
        "sym.all": "🌐 ALL",
        "sym.choose": "Select a coin",
        "sym.all_title": "All coins in the feed (chart stays on the current coin)",
        "sym.click": "Click — feed and chart for this coin",
        "sym.shift": "Shift+click — switch chart only, keep the feed",
        "sym.custom": "Added manually via search",
        "sym.empty": "Nothing found",
        "search.nothing": "Nothing found: \"{q}\"",
        "search.add": "＋ Add",
        "search.open": "📈 Open",
        "search.vol24": "24h {v}",
        "search.added_ok": "✓ {sym} added to the list and opened on the chart",
        "search.added_fail": "⚠ Could not find pair {sym}",
        "search.network_error": "⚠ Connection error while searching",
        "health.prices": "prices",
        "health.ticks": "ticks",
        "health.connected": "connected, events: {n}",
        "health.noconn": "no connection: {err}",
        "health.open": "Exchange status — hover or click",
        "health.summary": "{ok}/{n}",
        "health.gate_ref": "💠 Trade on Gate — up to 20% off fees",
        "land.title": "LiqScope — real-time crypto futures liquidation terminal",
        "land.gate.link": "💠 Trade on Gate — up to 20% off fees",
        "land.gate.note": "Partner link: signing up on Gate.io supports the project — the terminal stays free.",
        "land.meta": "LiqScope — free live liquidation terminal: Binance, Bybit, OKX, Gate.io, Bitget, HTX. Candlestick chart with clusters, pair search, size and exchange filters.",
        "land.badge": "Live liquidation feed",
        "land.h1.1": "Crypto futures liquidations",
        "land.h1.2": "in real time",
        "land.sub": "LiqScope captures forced position closures on Binance, Bybit, OKX, Gate.io, Bitget, HTX and more, and draws them on a live candlestick chart — with clusters, a volume profile and search for any tradable pair.",
        "land.hero.more": "What it does",
        "land.nav.live": "Live feed",
        "land.nav.features": "Features",
        "land.nav.how": "How it works",

        "land.nav.digest": "Digest",
        "land.nav.hourly": "Hourly summaries",
        "hour.title": "Hourly market summaries",
        "hour.meta": "LiqScope hourly market summaries: liquidations of the window, hour leaders, turnover and open interest. The very posts published in our channel, with photos.",
        "hour.lead": "A market summary is published several times a day.",
        "hour.fresh": "Latest summaries",
        "hour.pick": "Pick a day",
        "hour.total": "Summaries in the archive",
        "hour.days": "Days in the archive",
        "hour.channel": "Telegram channel",
        "hour.every": "every {h} h",
        "hour.window": "{h} h window",
        "hour.events": "{n} events",
        "hour.n_posts": "{n} posts",
        "hour.has": "summaries available",
        "hour.in_archive": "{n} in the archive",
        "hour.more": "{n} summaries in the archive: the latest are above, the rest open by day.",
        "hour.tz": "days in {tz}",
        "hour.prev": "Previous month",
        "hour.next": "Next month",
        "hour.loading": "Loading…",
        "hour.empty": "No summaries yet — the first one arrives after the channel post.",
        "hour.empty_day": "No summaries for this day.",
        "hour.photo_alt": "Summary photo",
        "hour.link": "Link to the summary",
        "hour.digest": "Digest",
        "hour.to_terminal": "To the terminal",
        "hour.cabinet": "Account",
        "hour.admin": "Admin",
        "hour.login": "Account",
        "dig.title": "Daily market digest",
        "dig.meta": "LiqScope daily digest: liquidations, exchanges, open interest, prices and market mood for the last 24 hours.",
        "dig.lead": "Every evening around 22:00 MSK — the day in review: the biggest liquidation, exchanges, coins with the heaviest open interest against turnover, top-5 prices and the overall market mood.",
        "dig.archive": "Issues",
        "dig.fresh": "Latest issues",
        "dig.pick": "Pick a date",
        "dig.more": "{n} issues in the archive: the fresh ones are above, the rest open by date.",
        "dig.has": "issue available",
        "dig.in_archive": "{n} in the archive",
        "dig.cabinet": "Account",
        "dig.admin": "Admin",
        "dig.login": "Account",
        "dig.prev": "Previous month",
        "dig.next": "Next month",
        "dig.empty": "The first issue arrives tonight — the archive is empty so far.",
        "dig.loading": "Loading…",
        "dig.published": "published",
        "dig.draft": "draft",
        "dig.mood": "Mood",
        "dig.total": "Liquidations, 24h",
        "dig.count": "Events",
        "dig.back": "To the terminal",
        "ad.tag": "Ad",
        "ad.open": "Open",
        "ad.prev": "Previous",
        "ad.next": "Next",
        "dig.schedule": "Published every evening ~22:00 MSK",
        "land.cta.open": "Open terminal",
        "land.stat.24h": "Liquidations in 24h",
        "land.stat.1h": "In the last hour",
        "land.stat.exch": "Exchanges live",
        "land.stat.ratio": "Longs × shorts (24h)",
        "land.cvd.title": "CVD {sym} — who's pushing: buyers or sellers",
        "land.cvd.hint": "taker buys minus sells · updates every 4 seconds",
        "land.oi.title": "OI {sym} — money in positions: inflows and outflows",
        "land.oi.hint": "open interest across all exchanges · updates every 4 seconds",
        "land.stat.cvd24h": "CVD in 24h",
        "land.stat.cvd1h": "CVD in 1h",
        "land.stat.cvd5m": "CVD in 5m",
        "land.stat.oiTotal": "Open interest",
        "land.stat.oi24h": "OI Δ in 24h",
        "land.stat.oi1h": "OI Δ in 1h",
        "land.stat.oi5m": "OI Δ in 5m",
        "land.live.title": "Live feed straight from the server",
        "land.live.hint": "updates every 4 seconds · full version in the terminal",
        "land.live.connect": "Connecting to the server…",
        "land.feat.title": "What the terminal can do",
        "land.feat.lead": "No keys, no paid signup — only public exchange data.",
        "land.card1.noun": "exchanges",
        "land.card1.noun_one": "exchange",
        "land.card1.noun_few": "exchanges",
        "land.card1.rest": "in one feed",
        "land.card1.desc": "Liquidations from Binance, Bybit, OKX, Gate.io, Bitget, HTX merge into one live feed with no duplicates and normalized pairs.",
        "land.card2.title": "Candlestick chart with clusters",
        "land.card2.desc": "Live 1m–4h candles; each liquidation is drawn as a block at its exact price: magenta — longs, cyan — shorts, whales from $100K — on a heat scale from yellow to bright red.",
        "land.card3.title": "Liquidation volume profile",
        "land.card3.desc": "An on-chart indicator shows at which price levels liquidation volume is concentrated over the visible period. Toggle it with one button.",
        "land.card4.title": "Search any pair",
        "land.card4.desc": "Coin not in the top list? Type its code — the terminal finds it in the full exchange catalog and adds it to the chart. GRAM, new listings, rare perpetuals.",
        "land.card5.title": "Flexible filters",
        "land.card5.desc": "Set the minimum liquidation size by hand and toggle exchanges with checkboxes. Settings are saved in your browser.",
        "land.card6.title": "Works on mobile",
        "land.card6.desc": "Single-column mobile layout: chart, feed with a clickable coin and filters are always at hand.",
        "land.card7.title": "Zero-lag ticks",
        "land.card7.desc": "Every trade in an open chart immediately moves the candle. The delay in milliseconds is shown right above the chart.",
        "land.card8.title": "Real-time diagnostics",
        "land.card8.desc": "Header indicators show which exchange is connected, how many events arrived and where prices come from. Sources switch automatically on failure.",
        "land.card9.title": "Free alerts",
        "land.card9.desc": "After signup you can get free liquidation alerts in Telegram: your own coin, threshold and window — and nothing to pay for.",
        "land.card9.cta": "Sign up for free",
        "land.how.title": "How it works",
        "land.step1.title": "We listen to exchanges",
        "land.step1.desc": "Public WebSocket channels of liquidations and trades — directly, without intermediaries.",
        "land.step2.title": "We normalize data",
        "land.step2.desc": "Pairs, sizes and sides are brought to a common format, duplicates are removed.",
        "land.step3.title": "We draw on the chart",
        "land.step3.desc": "Liquidations land on candles at their price; the profile shows their volume by levels.",
        "land.step4.title": "You watch",
        "land.step4.desc": "Feed, 5m/1h/24h stats, top coins — everything updates in real time.",
        "land.cta.title": "Ready to see the market from the inside?",
        "land.cta.note": "Free · no paid signup · no API keys · data from public exchange streams",
        "land.footer.copy": "© 2026 LiqScope Terminal · data from public exchange WebSockets",
        "cookie.title": "This site uses cookies",
        "cookie.text": "Standard practice: cookies keep you signed in, remember the language you picked and help count visits — without them we cannot tell real guests from service requests.",
        "cookie.ok": "Got it",
        "cookie.more": "What exactly is stored",
        "cookie.sid": "your session — until you sign out yourself",
        "cookie.vid": "a guest number — for visit statistics only",
        "cookie.pref": "language, theme and chart settings — in your browser",
        "cookie.note": "By continuing to use the site you agree to this.",
        "land.footer.race": "Not investment advice",
        "land.footer.open": "Open terminal →",
            "gate.title": "Register for free",
        "gate.text": "Chart layers are available to everyone. Sign up to get Telegram signals and an ad-free terminal.",
        "gate.f1": "Telegram signals",
        "gate.f2": "Ad-free terminal",
        "gate.f3": "Account and services",
        "gate.cta": "Register for free",
        "gate.later": "Continue",
        "gate.close_title": "Close this window",
        "gate.trial_badge": "{min}m",
        "gate.trial_title": "Layers available to everyone",
};

    var ZH = {
        "terminal.title": "LiqScope 终端 — 实时爆仓流与K线图",
        "terminal.meta": "LiqScope 终端 — 来自8家交易所的实时加密货币期货爆仓流、K线图、爆仓聚类和成交量分布。",
        "brand.badge": "终端",
        "brand.home_title": "返回首页 — LiqScope 终端",
        "layout.pane_split": "区块高度 — 拖动分隔条，双击重置",
        "layout.feed_split": "列表宽度 — 拖动分隔条调整",
        "layout.coins_split": "币种榜高度 — 拖动分隔条调整",
        "layout.chart_split": "图表高度 — 上下拖动",
        "conn.connecting": "连接中…",
        "conn.live": "LIVE WS",
        "conn.reconnecting": "重新连接中…",
        "stats.24h": "爆仓量 (24小时)",
        "stats.1h": "爆仓量 (1小时)",
        "stats.5m": "爆仓量 (5分钟)",
        "stats.longs": "多单",
        "stats.shorts": "空单",
        "stats.cvd24h": "24小时",
        "stats.cvd1h": "1小时",
        "stats.cvd5m": "5分钟",
        "stats.cvd_title": "图表币种 CVD：主动买入减去卖出",
        "stats.oi_title": "图表币种在所选周期内的 OI 变化 — 点击切换",
        "stats.box_liq": "爆仓",
        "stats.liq_title": "所选周期内的爆仓 — 点击切换",
        "stats.w1m": "1分钟",
        "stats.w5m": "5分钟",
        "stats.w15m": "15分钟",
        "stats.w30m": "30分钟",
        "stats.w1h": "1小时",
        "stats.w4h": "4小时",
        "stats.w24h": "24小时",
        "ctrl.moneta": "币种:",
        "ctrl.tf": "周期:",
        "ctrl.minvol": "最小金额:",
        "ctrl.exchange": "交易所:",
        "search.placeholder": "搜索交易对: BTC、SOL、GRAM…",
        "search.hint": "搜索流动性较低的币种。",
        "filter.all_usd": "全部 ($0+) ▾",
        "filter.min_ge": "≥ ${v} ▾",
        "filter.usd_placeholder": "例如 10000",
        "filter.ok": "确定",
        "filter.preset_all": "全部",
        "filter.usd_note": "仅显示金额 ≥ 阈值的爆仓。",
        "filter.th_liq": "爆仓金额, $",
        "filter.th_short_liq": "爆仓",
        "filter.th_ge": "≥ ${v}",
        "filter.th_cvd": "每根K线 CVD, $",
        "filter.th_oi": "每根K线 OI Δ, $",
        "filter.presets_for": "预设值适用于当前打开的底部列表:",
        "filter.th_note": "每个阈值只过滤自己的序列 — 列表和图表标记: 爆仓、CVD 三角和 OI 圆点。0 表示全部显示。",
        "filter.exch_header": "已启用的交易所",
        "filter.all": "全部",
        "filter.none": "无",
        "filter.all_exchanges": "全部交易所 ▾",
        "filter.disabled": "已关闭 ▾",
        "filter.thresholds_title": "成交量阈值：清算、CVD 和 OI Δ",
        "filter.exchanges_title": "按交易所筛选",
        "filter.cvd_placeholder": "例如 50000",
        "filter.oi_placeholder": "例如 1000000",
        "chart.cvd_current_title": "谁在推动当前K线的价格",
        "feed.show_all_title": "显示所有币种",
        "lang.select_title": "Language / 语言",

        "chart.long": "多单爆仓",
        "chart.short": "空单爆仓",
        "chart.whale": "大户 ($100k+)",
        "chart.whale_title": "巨鲸色阶：$100K 黄色 … $1M+ 亮红",
        "chart.profile": "📊 分布",
        "chart.toggle_title": "折叠/展开图表",
        "chart.expand_title": "全屏显示图表",
        "chart.draw_title": "绘制技术分析图形",
        "chart.layers": "☰ 图层",
        "chart.layers_title": "显示/隐藏图层按钮",
        "chart.follow": "🎯 自动",
        "chart.follow_full": "🎯 自动跟随价格",
        "chart.follow_on": "自动跟随已开启：价格保持在上下各15%以内，K线距右边缘3%；手动移动会自动归位。点击关闭",
        "chart.follow_off": "自动跟随已关闭：图表保持不动。点击开启",
        "chart.liq_label": "爆仓",
        "chart.profile_label": "分布",
        "chart.cvd_label": "CVD",
        "chart.oi_label": "OI",
        "draw.line": "趋势线",
        "draw.ray": "射线",
        "draw.horiz": "水平线",
        "draw.pane_hint": "在这个窗口里也可以画技术分析图形",
        "draw.rect": "矩形",
        "draw.fib": "斐波那契回撤",
        "draw.eraser": "橡皮擦:点击图形删除",
        "draw.clear": "清除所有图形",
        "draw.close": "关闭绘图",
        "chart.collapse_title": "退出图表全屏",
        "chart.profile_on": "隐藏爆仓量分布",
        "chart.profile_off": "显示爆仓量分布",
        "chart.liq": "⚡ 清算",
        "chart.liq_on": "隐藏图表上的清算方块",
        "chart.liq_off": "在图表上显示清算方块",
        "chart.cvd": "🎯 CVD",
        "chart.oi": "● OI",
        "chart.live_liq": "当前K线爆仓",
        "chart.live_profile": "可见范围爆仓",
        "chart.live_oi": "当前K线OI变化",
        "chart.oi_on": "隐藏持仓量圆点",
        "chart.oi_off": "显示持仓量圆点：🟢 上涨，🔴 下跌",
        "chart.pane_liq": "💥 清算窗口",
        "chart.pane_cvd": "🌊 CVD 窗口",
        "chart.pane_oi": "📈 持仓量窗口",
        "chart.pane_liq_head": "💥 LIQ",
        "chart.pane_cvd_head": "🌊 CVD",
        "chart.pane_oi_head": "📈 OI",
        "chart.pane_close": "隐藏指标窗口",
        "chart.pane_liq_on": "隐藏图表下方的清算窗口",
        "chart.pane_liq_off": "显示图表下方的清算窗口",
        "chart.pane_cvd_on": "隐藏图表下方的 CVD 窗口",
        "chart.pane_cvd_off": "显示图表下方的 CVD 窗口",
        "chart.pane_oi_on": "隐藏图表下方的持仓量窗口",
        "chart.pane_oi_off": "显示图表下方的持仓量窗口",
        "chart.cvd_on": "隐藏 CVD 三角标记（每根K线的主动买/卖差）",
        "chart.cvd_off": "显示 CVD 三角标记：▲ 主动买方，▼ 主动卖方",
        "chart.cvd_buy": "CVD ▲ 买方",
        "chart.cvd_sell": "CVD ▼ 卖方",
        "chart.error": "图表库加载失败 (/static/lightweight-charts.js)。",
        "src.no_link": "无法连接交易所",
        "src.demo": "演示数据流",
        "src.waiting": "等待 tick…",
        "src.no_ticks": "已 {s} 秒无 tick",
        "src.ticks": "⚡ tick: {n} · 延迟 {ms} ms",
        "feed.title": "⚡ 实时爆仓流",
        "feed.tab_liq": "⚡ 爆仓",
        "feed.tab_cvd": "🎯 CVD",
        "feed.tab_oi": "● OI",
        "feed.empty_cvd": "图表币种暂无显著 CVD 变化…",
        "feed.empty_oi": "图表币种暂无显著 OI 变化…",
        "feed.tab_book": "📖 挂单",
        "feed.empty_book": "该币的盘口暂无大单墙——等待交易所挂单…",
        "feed.book_off": "「📖 盘口」图层已关闭——请在图层面板开启",
        "feed.col_book": "墙 ($)",
        "feed.book_live": "在挂",
        "feed.book_eaten": "被吃",
        "feed.book_gone": "撤单",
        "feed.book_walls_n": "笔",
        "feed.book_levels_n": "档",
        "feed.col_cvd": "CVD ($)",
        "feed.col_oi": "OI Δ ($)",
        "feed.cvd_buy": "CVD ▲",
        "feed.cvd_sell": "CVD ▼",
        "feed.oi_up": "OI ▲",
        "feed.oi_down": "OI ▼",
        "feed.count_one": "{n} 个事件",
        "feed.count_few": "{n} 个事件",
        "feed.count_many": "{n} 个事件",
        "feed.col_time": "时间",
        "feed.col_coin": "币种",
        "feed.col_exch": "交易所",
        "feed.col_type": "方向",
        "feed.col_usd": "爆仓金额 ($)",
        "feed.col_price": "价格 ($)",
        "feed.empty": "等待交易所事件…",
        "feed.open_chart": "仅看 {sym} 的行情与图表",
        "feed.empty_flow": "正在汇总全部币种的资金流，请稍候…",
        "flow.window": "时间窗口",
        "flow.window_val": "{n} 分钟",
        "flow.window_short": "{n}分",
        "flow.cvd": "CVD（买 − 卖）",
        "flow.oi_delta": "持仓量变化",
        "flow.share": "占成交量比例",
        "flow.oi_level": "当前持仓量",
        "flow.liq_long": "多头爆仓",
        "flow.liq_short": "空头爆仓",
        "flow.vol": "窗口成交量",
        "flow.price": "价格",
        "flow.note": "该行是单个币种的分钟资金流，不是图表K线：图表仍停留在所选币种，同时可以看到全部币种。",
        "feed.chip_title": "显示所有币种",
        "feed.all_feed": "全部币种",
        "top.title": "🔥 爆仓排行 (24小时)",
        "top.by_vol": "💥 按金额",
        "top.by_count": "🔢 按次数",
        "top.count": "{n} 次",
        "sound.title": "声音提醒",
        "clear.title": "清除",
        "clear.btn": "🧹 清除",
        "clear.menu_title": "清除或恢复历史",
        "clear.do": "🧹 清除图表",
        "clear.undo": "↩️ 恢复历史",
        "clear.undone": "已恢复：事件将重新从服务器获取",
        "modal.title": "爆仓详情",
        "modal.liq_of": "{sym} 爆仓",
        "modal.exch": "交易所:",
        "modal.src": "来源：",
        "modal.src_tape": "由成交流推断，并通过 /info 按地址确认",
        "modal.src_feed": "交易所推送",
        "feed.tape_tip": "Hyperliquid 不公开强平事件：由成交流中的市价单突发推断，并通过 /info 确认",
        "modal.exchs": "交易所:",
        "modal.type": "方向:",
        "modal.long_desc": "LONG (强制卖出)",
        "modal.short_desc": "SHORT (强制买入)",
        "modal.price": "价格:",
        "modal.usd": "金额:",
        "modal.qty": "数量:",
        "modal.time": "时间:",
        "modal.cvd_of": "CVD {sym}",
        "modal.oi_of": "OI {sym}",
        "modal.candle": "K线:",
        "modal.live": "形成中",
        "modal.direction": "方向:",
        "modal.cvd_buy": "▲ 买方占优",
        "modal.cvd_sell": "▼ 卖方占优",
        "modal.oi_up": "🟢 持仓增加 — 资金流入",
        "modal.oi_down": "🔴 持仓减少 — 资金流出",
        "modal.strength": "强度:",
        "modal.top10": "前10% K线",
        "modal.p90mult": "{r} × p90",
        "modal.price_move": "本根价格:",
        "modal.tier": "等级:",
        "modal.tier_mini": "迷你",
        "modal.cvd_about": "CVD — 本根K线主动买入与卖出的差值：谁打市场更激进。",
        "modal.oi_about": "OI — 本根K线持仓量的变化：增加是资金进场，减少是资金离场。",
        "modal.book_title": "📖 挂单簇 {sym}",
        "modal.book_zone": "价格区间：",
        "modal.book_walls": "区间内的墙：",
        "modal.book_about": "墙 = 盘口(L2)中密集的限价单档位。色块钉在发现它的那根K线上，直到撤单或成交；“被吃”指剩余不足峰值的70%——正被市价单消化。",
        "modal.clust_of": "{sym} 爆仓",
        "modal.level": "价格:",
        "modal.events": "笔数:",
        "modal.whale": "巨鲸",
        "modal.split": "多空:",
        "modal.clust_about": "簇——将同一根K线附近价格的爆仓合并显示，关联记录在订单流中高亮。",
        "sym.all": "🌐 全部",
        "sym.choose": "选择币种",
        "sym.all_title": "全部币种进入信息流（图表保持当前币种）",
        "sym.click": "单击 — 信息流和图表切换到此币种",
        "sym.shift": "Shift+单击 — 仅切换图表，信息流不变",
        "sym.custom": "通过搜索手动添加",
        "sym.empty": "未找到",
        "search.nothing": "未找到：\"{q}\"",
        "search.add": "＋ 添加",
        "search.open": "📈 打开",
        "search.vol24": "24时 {v}",
        "search.added_ok": "✓ 已将 {sym} 加入列表并打开图表",
        "search.added_fail": "⚠ 未找到交易对 {sym}",
        "search.network_error": "⚠ 搜索时连接服务器出错",
        "health.prices": "价格",
        "health.ticks": "tick",
        "health.connected": "已连接，事件: {n}",
        "health.noconn": "无连接: {err}",
        "health.open": "交易所状态 — 悬停或点击",
        "health.summary": "{ok}/{n}",
        "health.gate_ref": "💠 在 Gate 交易 — 手续费折扣",
        "land.title": "LiqScope — 实时加密货币期货爆仓终端",
        "land.gate.link": "💠 在 Gate 交易 — 手续费折扣",
        "land.gate.note": "合作伙伴链接：在 Gate.io 注册即支持本项目，终端保持免费。",
        "land.meta": "LiqScope — 免费实时爆仓终端: Binance、Bybit、OKX、Gate.io、Bitget、HTX。带聚类K线图、交易对搜索、金额和交易所筛选。",
        "land.badge": "实时清算流",
        "land.h1.1": "加密货币期货爆仓",
        "land.h1.2": "实时",
        "land.sub": "LiqScope 抓取 Binance、Bybit、OKX、Gate.io、Bitget、HTX 等交易所上的强制平仓，并绘制在实时K线图上——带聚类、成交量分布以及对任意交易对的搜索。",
        "land.hero.more": "功能一览",
        "land.nav.live": "实时行情",
        "land.nav.features": "功能",
        "land.nav.how": "工作原理",

        "land.nav.digest": "日报",
        "land.nav.hourly": "小时简报",
        "hour.title": "小时市场简报",
        "hour.meta": "LiqScope 小时简报：区间清算、小时冠军、成交额与未平仓合约。与频道发布的帖子相同，含图片。",
        "hour.lead": "每天多次发布市场简报。",
        "hour.fresh": "最新简报",
        "hour.pick": "选择日期",
        "hour.total": "归档简报",
        "hour.days": "归档天数",
        "hour.channel": "Telegram 频道",
        "hour.every": "每 {h} 小时",
        "hour.window": "{h} 小时窗口",
        "hour.events": "{n} 个事件",
        "hour.n_posts": "{n} 条",
        "hour.has": "有简报",
        "hour.in_archive": "归档 {n}",
        "hour.more": "归档共 {n} 条：上方为最新，其余按日期打开。",
        "hour.tz": "日期按 {tz}",
        "hour.prev": "上个月",
        "hour.next": "下个月",
        "hour.loading": "加载中…",
        "hour.empty": "暂无简报——频道发布后就会出现第一条。",
        "hour.empty_day": "这一天没有简报。",
        "hour.photo_alt": "简报图片",
        "hour.link": "简报链接",
        "hour.digest": "日报",
        "hour.to_terminal": "返回终端",
        "hour.cabinet": "账户",
        "hour.admin": "管理",
        "hour.login": "账户",
        "dig.title": "每日市场日报",
        "dig.meta": "LiqScope 每日摘要：24 小时清算、交易所、未平仓合约、价格与市场情绪。",
        "dig.lead": "每晚约 22:00（莫斯科时间）发布当日总结：最大清算、交易所、未平仓合约相对成交额最重的币种、前五大币价与市场情绪。",
        "dig.archive": "往期",
        "dig.fresh": "最新期号",
        "dig.pick": "选择日期",
        "dig.more": "归档共 {n} 期：上方为最新，其余按日期打开。",
        "dig.has": "有期号",
        "dig.in_archive": "归档 {n} 期",
        "dig.cabinet": "账户",
        "dig.admin": "管理",
        "dig.login": "账户",
        "dig.prev": "上个月",
        "dig.next": "下个月",
        "dig.empty": "第一期将在今晚发布，归档暂时为空。",
        "dig.loading": "加载中…",
        "dig.published": "已发布",
        "dig.draft": "草稿",
        "dig.mood": "市场情绪",
        "dig.total": "24 小时清算",
        "dig.count": "事件",
        "dig.back": "返回终端",
        "ad.tag": "广告",
        "ad.open": "打开",
        "ad.prev": "上一个",
        "ad.next": "下一个",
        "dig.schedule": "每晚约 22:00（莫斯科时间）发布",
        "land.cta.open": "打开终端",
        "land.stat.24h": "24小时爆仓",
        "land.stat.1h": "最近一小时",
        "land.stat.exch": "在线交易所",
        "land.stat.ratio": "多单 × 空单 (24时)",
        "land.cvd.title": "CVD {sym} — 谁在推动：买方还是卖方",
        "land.cvd.hint": "主动买入减去卖出 · 每 4 秒更新",
        "land.oi.title": "OI {sym} —— 持仓资金流入与流出",
        "land.oi.hint": "全网持仓量 · 每 4 秒更新",
        "land.stat.cvd24h": "24小时 CVD",
        "land.stat.cvd1h": "1小时 CVD",
        "land.stat.cvd5m": "5分钟 CVD",
        "land.stat.oiTotal": "持仓量",
        "land.stat.oi24h": "OI变化 24小时",
        "land.stat.oi1h": "OI变化 1小时",
        "land.stat.oi5m": "OI变化 5分钟",
        "land.live.title": "来自服务器的实时信息流",
        "land.live.hint": "每 4 秒更新 · 完整版在终端中",
        "land.live.connect": "正在连接服务器…",
        "land.feat.title": "终端能做什么",
        "land.feat.lead": "无需密钥、无需付费注册 — 仅使用交易所公开数据。",
        "land.card1.noun": "家交易所",
        "land.card1.noun_one": "家交易所",
        "land.card1.noun_few": "家交易所",
        "land.card1.rest": "汇聚一个信息流",
        "land.card1.desc": "来自 Binance、Bybit、OKX、Gate.io、Bitget 和 HTX 的爆仓融合为一条实时信息流，无重复、交易对已标准化。",
        "land.card2.title": "带聚类的K线图",
        "land.card2.desc": "1分钟–4小时实时K线；每次爆仓按精确价格绘制方块：洋红 — 多单，青色 — 空单，$100K 以上巨鲸 — 按从黄到亮红的热力色阶。",
        "land.card3.title": "按价格分布的爆仓量",
        "land.card3.desc": "图表内指标显示可见区间内爆仓量集中在哪些价位。一键开关。",
        "land.card4.title": "搜索任意交易对",
        "land.card4.desc": "币种不在列表中？输入代码——终端会在交易所完整目录中找到并添加到图表。GRAM、新上线、稀有永续合约。",
        "land.card5.title": "灵活的筛选器",
        "land.card5.desc": "手动输入最小爆仓金额，用复选框开关各交易所。设置保存在浏览器中。",
        "land.card6.title": "手机可用",
        "land.card6.desc": "单列移动布局：图表、可点击币种的信息流和筛选器始终触手可及。",
        "land.card7.title": "零延迟 tick",
        "land.card7.desc": "已打开图表中的每笔成交都会立即推动K线。毫秒级延迟直接显示在图表上方。",
        "land.card8.title": "实时诊断",
        "land.card8.desc": "顶部指示器显示哪家交易所已连接、收到多少事件以及价格来自哪里。故障时自动切换数据源。",
        "land.card9.title": "免费提醒",
        "land.card9.desc": "注册后可在 Telegram 免费接收爆仓提醒：自选币种、阈值与时间窗口 — 完全无需付费。",
        "land.card9.cta": "免费注册",
        "land.how.title": "工作原理",
        "land.step1.title": "监听交易所",
        "land.step1.desc": "爆仓和成交的公开 WebSocket 频道——直接连接，无中间商。",
        "land.step2.title": "数据标准化",
        "land.step2.desc": "交易对、数量和方向统一格式，去除重复。",
        "land.step3.title": "绘制到图表",
        "land.step3.desc": "爆仓按价格落在K线上；分布图按价格层级显示其成交量。",
        "land.step4.title": "您来观察",
        "land.step4.desc": "信息流、5分/1时/24时统计、币种排行——全部实时更新。",
        "land.cta.title": "准备好从内部看市场了吗？",
        "land.cta.note": "免费 · 无需付费注册 · 无需 API 密钥 · 数据来自交易所公开流",
        "land.footer.copy": "© 2026 LiqScope 终端 · 数据来自交易所公开 WebSocket",
        "cookie.title": "本站使用 cookie",
        "cookie.text": "这是通行做法：cookie 让你保持登录、记住所选语言，并帮助我们统计访问量——没有它们我们无法区分真实访客与服务请求。",
        "cookie.ok": "知道了",
        "cookie.more": "具体存储什么",
        "cookie.sid": "登录状态——直到你自行退出",
        "cookie.vid": "访客编号——仅用于访问统计",
        "cookie.pref": "语言、主题与图表设置——保存在你的浏览器",
        "cookie.note": "继续使用本站即表示你同意这一点。",
        "land.footer.race": "不构成投资建议",
        "land.footer.open": "打开终端 →",
            "gate.title": "免费注册",
        "gate.text": "图表图层对所有人开放。注册后即可获得 Telegram 信号和无广告终端。",
        "gate.f1": "Telegram 信号",
        "gate.f2": "无广告终端",
        "gate.f3": "个人中心与服务",
        "gate.cta": "免费注册",
        "gate.later": "继续",
        "gate.close_title": "关闭窗口",
        "gate.trial_badge": "{min}分",
        "gate.trial_title": "图层对所有人开放",
};

    var HI = {
        "terminal.title": "LiqScope टर्मिनल — लाइव लिक्विडेशन स्ट्रीम और चार्ट",
        "terminal.meta": "LiqScope टर्मिनल — 8 एक्सचेंजों से क्रिप्टो फ्यूचर्स लिक्विडेशन की लाइव फ़ीड, कैंडल चार्ट, क्लस्टर और वॉल्यूम प्रोफ़ाइल।",
        "brand.badge": "टर्मिनल",
        "brand.home_title": "होम पर वापस — LiqScope टर्मिनल",
        "layout.pane_split": "ब्लॉक की ऊँचाई — डिवाइडर खींचें, रीसेट के लिए डबल क्लिक",
        "layout.feed_split": "फ़ीड की चौड़ाई — डिवाइडर खींचें",
        "layout.coins_split": "टॉप कॉइन ब्लॉक की ऊँचाई — डिवाइडर खींचें",
        "layout.chart_split": "चार्ट की ऊँचाई — ऊपर/नीचे खींचें",
        "conn.connecting": "कनेक्ट हो रहा है…",
        "conn.live": "LIVE WS",
        "conn.reconnecting": "फिर से कनेक्ट…",
        "stats.24h": "लिक्विडेशन (24 घंटे)",
        "stats.1h": "लिक्विडेशन (1 घंटा)",
        "stats.5m": "लिक्विडेशन (5 मिनट)",
        "stats.longs": "लॉन्ग",
        "stats.shorts": "शॉर्ट",
        "stats.cvd24h": "24घं",
        "stats.cvd1h": "1घं",
        "stats.cvd5m": "5मि",
        "stats.cvd_title": "चार्ट सिक्के का CVD: टेकर खरीद घटा बिक्री",
        "stats.oi_title": "चुनी अवधि में चार्ट कॉइन के OI में बदलाव — बदलने के लिए क्लिक करें",
        "stats.box_liq": "लिक्विडेशन",
        "stats.liq_title": "चुनी अवधि के लिक्विडेशन — बदलने के लिए क्लिक करें",
        "stats.w1m": "1 मि",
        "stats.w5m": "5 मि",
        "stats.w15m": "15 मि",
        "stats.w30m": "30 मि",
        "stats.w1h": "1 घं",
        "stats.w4h": "4 घं",
        "stats.w24h": "24 घं",
        "ctrl.moneta": "सिक्का:",
        "ctrl.tf": "टाइमफ्रेम:",
        "ctrl.minvol": "न्यूनतम राशि:",
        "ctrl.exchange": "एक्सचेंज:",
        "search.placeholder": "पेयर खोजें: BTC, SOL, GRAM…",
        "search.hint": "कम लिक्विड सिक्के खोजें।",
        "filter.all_usd": "सभी ($0+) ▾",
        "filter.min_ge": "≥ ${v} ▾",
        "filter.usd_placeholder": "उदा. 10000",
        "filter.ok": "ठीक",
        "filter.preset_all": "सभी",
        "filter.usd_note": "केवल सीमा ≥ वाले लिक्विडेशन दिखेंगे।",
        "filter.exch_header": "चालू एक्सचेंज",
        "filter.all": "सभी",
        "filter.none": "कोई नहीं",
        "filter.all_exchanges": "सभी एक्सचेंज ▾",
        "filter.disabled": "बंद ▾",
        "filter.thresholds_title": "वॉल्यूम थ्रेशोल्ड: लिक्विडेशन, CVD और OI Δ",
        "filter.exchanges_title": "एक्सचेंज के अनुसार फ़िल्टर",
        "filter.cvd_placeholder": "जैसे 50000",
        "filter.oi_placeholder": "जैसे 1000000",
        "chart.cvd_current_title": "मौजूदा कैंडल में कीमत कौन चला रहा है",
        "feed.show_all_title": "सभी कॉइन्स दिखाएं",
        "lang.select_title": "भाषा / Language",

        "filter.th_liq": "लिक्विडेशन, $",
        "filter.th_short_liq": "लिक्वि",
        "filter.th_ge": "≥ ${v}",
        "filter.th_cvd": "प्रति कैंडल CVD, $",
        "filter.th_oi": "प्रति कैंडल OI Δ, $",
        "filter.presets_for": "प्रीसेट खुली फ़ीड टैब पर लागू होते हैं:",
        "filter.th_note": "हर थ्रेशोल्ड अपनी सीरीज़ फ़िल्टर करता है — फ़ीड और चार्ट के निशान: लिक्विडेशन, CVD त्रिकोण और OI गोले। शून्य = सब दिखाएँ।",
        "chart.long": "लॉन्ग लिक्विडेशन",
        "chart.short": "शॉर्ट लिक्विडेशन",
        "chart.whale": "व्हेल ($100k+)",
        "chart.whale_title": "व्हेल स्केल: $100K पीला … $1M+ चमकीला लाल",
        "chart.profile": "📊 प्रोफ़ाइल",
        "chart.toggle_title": "चार्ट छोटा/बड़ा करें",
        "chart.expand_title": "चार्ट फुलस्क्रीन करें",
        "chart.draw_title": "तकनीकी विश्लेषण आकृतियाँ बनाएँ",
        "chart.layers": "☰ परतें",
        "chart.layers_title": "परत बटन दिखाएँ/छिपाएँ",
        "chart.follow": "🎯 ऑटो",
        "chart.follow_full": "🎯 कीमत का ऑटो-फ़ॉलो",
        "chart.follow_on": "ऑटो-फ़ॉलो चालू: कीमत ऊपर-नीचे 15% के भीतर, कैंडल दाएँ किनारे से 3% पर; हाथ से खिसकाया तो अपने-आप लौट आता है। बंद करने के लिए दबाएँ",
        "chart.follow_off": "ऑटो-फ़ॉलो बंद: चार्ट अपनी जगह रहता है। चालू करने के लिए दबाएँ",
        "chart.liq_label": "लिक्विडेशन",
        "chart.profile_label": "प्रोफ़ाइल",
        "chart.cvd_label": "CVD",
        "chart.oi_label": "OI",
        "draw.line": "ट्रेंड लाइन",
        "draw.ray": "किरण",
        "draw.horiz": "क्षैतिज रेखा",
        "draw.pane_hint": "इस विंडो में भी टेक्निकल फिगर बना सकते हैं",
        "draw.rect": "आयत",
        "draw.fib": "फिबोनाची रिट्रेसमेंट",
        "draw.eraser": "रबड़: हटाने के लिए आकृति पर क्लिक करें",
        "draw.clear": "सभी आकृतियाँ हटाएँ",
        "draw.close": "ड्राइंग बंद करें",
        "chart.collapse_title": "फुलस्क्रीन से बाहर निकलें",
        "chart.profile_on": "लिक्विडेशन वॉल्यूम प्रोफ़ाइल छिपाएँ",
        "chart.profile_off": "लिक्विडेशन वॉल्यूम प्रोफ़ाइल दिखाएँ",
        "chart.liq": "⚡ लिक्विडेशन",
        "chart.liq_on": "चार्ट से लिक्विडेशन आयत छिपाएँ",
        "chart.liq_off": "चार्ट पर लिक्विडेशन आयत दिखाएँ",
        "chart.cvd": "🎯 CVD",
        "chart.oi": "● OI",
        "chart.live_liq": "मौजूदा कैंडल के लिक्विडेशन",
        "chart.live_profile": "दिख रही रेंज के लिक्विडेशन",
        "chart.live_oi": "मौजूदा कैंडल का OI बदलाव",
        "chart.oi_on": "ओपन इंटरेस्ट गोले छिपाएँ",
        "chart.oi_off": "ओपन इंटरेस्ट गोले दिखाएँ: 🟢 बढ़त, 🔴 गिरावट",
        "chart.pane_liq": "💥 LIQ विंडो",
        "chart.pane_cvd": "🌊 CVD विंडो",
        "chart.pane_oi": "📈 OI विंडो",
        "chart.pane_liq_head": "💥 LIQ",
        "chart.pane_cvd_head": "🌊 CVD",
        "chart.pane_oi_head": "📈 OI",
        "chart.pane_close": "सूचक विंडो छिपाएँ",
        "chart.pane_liq_on": "चार्ट के नीचे लिक्विडेशन विंडो छिपाएँ",
        "chart.pane_liq_off": "चार्ट के नीचे लिक्विडेशन विंडो दिखाएँ",
        "chart.pane_cvd_on": "चार्ट के नीचे CVD विंडो छिपाएँ",
        "chart.pane_cvd_off": "चार्ट के नीचे CVD विंडो दिखाएँ",
        "chart.pane_oi_on": "चार्ट के नीचे OI विंडो छिपाएँ",
        "chart.pane_oi_off": "चार्ट के नीचे ओपन इंटरेस्ट विंडो दिखाएँ",
        "chart.cvd_on": "CVD त्रिकोण छिपाएँ (कैंडल में खरीद/बिक्री का अंतर)",
        "chart.cvd_off": "CVD त्रिकोण दिखाएँ: हर कैंडल में ▲ खरीदार, ▼ विक्रेता",
        "chart.cvd_buy": "CVD ▲ खरीद",
        "chart.cvd_sell": "CVD ▼ बिक्री",
        "chart.error": "चार्ट लाइब्रेरी लोड नहीं हुई (/static/lightweight-charts.js).",
        "src.no_link": "एक्सचेंज से संपर्क नहीं",
        "src.demo": "डेमो स्ट्रीम",
        "src.waiting": "टिक्स का इंतज़ार…",
        "src.no_ticks": "{s} सेकंड से टिक नहीं",
        "src.ticks": "⚡ टिक्स: {n} · विलंब {ms} ms",
        "feed.title": "⚡ लाइव लिक्विडेशन फ़ीड",
        "feed.tab_liq": "⚡ लिक्विडेशन",
        "feed.tab_cvd": "🎯 CVD",
        "feed.tab_oi": "● OI",
        "feed.empty_cvd": "चार्ट सिक्के पर कोई खास CVD बदलाव नहीं…",
        "feed.empty_oi": "चार्ट सिक्के पर कोई खास OI बदलाव नहीं…",
        "feed.tab_book": "📖 ऑर्डर",
        "feed.empty_book": "इस कॉइन के ऑर्डर बुक में फिलहाल कोई दीवार नहीं — एक्सचेंज लिमिट ऑर्डर का इंतज़ार…",
        "feed.book_off": "📖 ऑर्डर-बुक लेयर बंद है — लेयर्स पैनल से चालू करें",
        "feed.col_book": "WALL ($)",
        "feed.book_live": "ज़िंदा",
        "feed.book_eaten": "खाई गई",
        "feed.book_gone": "हटी",
        "feed.book_walls_n": "दीवारें",
        "feed.book_levels_n": "लेवल",
        "feed.col_cvd": "CVD ($)",
        "feed.col_oi": "OI Δ ($)",
        "feed.cvd_buy": "CVD ▲",
        "feed.cvd_sell": "CVD ▼",
        "feed.oi_up": "OI ▲",
        "feed.oi_down": "OI ▼",
        "feed.count_one": "{n} घटना",
        "feed.count_few": "{n} घटनाएँ",
        "feed.count_many": "{n} घटनाएँ",
        "feed.col_time": "समय",
        "feed.col_coin": "सिक्का",
        "feed.col_exch": "एक्सचेंज",
        "feed.col_type": "प्रकार",
        "feed.col_usd": "लिक्विडेशन राशि ($)",
        "feed.col_price": "कीमत ($)",
        "feed.empty": "एक्सचेंज से घटनाओं का इंतज़ार…",
        "feed.open_chart": "{sym} की फ़ीड और चार्ट",
        "feed.empty_flow": "सभी कॉइन का फ़्लो जमा किया जा रहा है — पहले मिनट का इंतज़ार…",
        "flow.window": "विंडो",
        "flow.window_val": "{n} मिनट",
        "flow.window_short": "{n}मि",
        "flow.cvd": "CVD (खरीद − बिक्री)",
        "flow.oi_delta": "OI में बदलाव",
        "flow.share": "वॉल्यूम में हिस्सा",
        "flow.oi_level": "अभी OI",
        "flow.liq_long": "लॉन्ग लिक्विडेशन",
        "flow.liq_short": "शॉर्ट लिक्विडेशन",
        "flow.vol": "विंडो का वॉल्यूम",
        "flow.price": "कीमत",
        "flow.note": "यह पंक्ति एक कॉइन का मिनट-फ़्लो है, चार्ट की कैंडल नहीं: चार्ट आपकी चुनी कॉइन पर रहता है, पर सभी कॉइन एक साथ दिखते हैं।",
        "feed.chip_title": "सभी सिक्के दिखाएँ",
        "feed.all_feed": "फ़ीड: सभी सिक्के",
        "top.title": "🔥 शीर्ष लिक्विडेशन (24घं)",
        "top.by_vol": "💥 राशि के अनुसार",
        "top.by_count": "🔢 संख्या के अनुसार",
        "top.count": "{n} बार",
        "sound.title": "ध्वनि चेतावनी",
        "clear.title": "साफ़ करें",
        "clear.btn": "🧹 साफ़ करें",
        "clear.menu_title": "साफ़ करें या इतिहास लौटाएँ",
        "clear.do": "🧹 चार्ट साफ़ करें",
        "clear.undo": "↩️ इतिहास लौटाएँ",
        "clear.undone": "इतिहास लौट आया: घटनाएँ सर्वर से फिर आएँगी",
        "modal.title": "लिक्विडेशन विवरण",
        "modal.liq_of": "{sym} लिक्विडेशन",
        "modal.exch": "एक्सचेंज:",
        "modal.src": "स्रोत:",
        "modal.src_tape": "टेप से अनुमानित, /info से पते पर पुष्टि",
        "modal.src_feed": "एक्सचेंज फ़ीड",
        "feed.tape_tip": "Hyperliquid लाइक्विडेशन प्रकाशित नहीं करता: घटना टेप से अनुमानित कर /info से पुष्टि की गई",
        "modal.exchs": "एक्सचेंज:",
        "modal.type": "प्रकार:",
        "modal.long_desc": "LONG (जबरन बिक्री)",
        "modal.short_desc": "SHORT (जबरन खरीद)",
        "modal.price": "कीमत:",
        "modal.usd": "राशि:",
        "modal.qty": "मात्रा:",
        "modal.time": "समय:",
        "modal.cvd_of": "CVD {sym}",
        "modal.oi_of": "OI {sym}",
        "modal.candle": "कैंडल:",
        "modal.live": "बन रही है",
        "modal.direction": "दिशा:",
        "modal.cvd_buy": "▲ खरीदार हावी",
        "modal.cvd_sell": "▼ विक्रेता हावी",
        "modal.oi_up": "🟢 ब्याज बढ़ रहा — पैसा आ रहा",
        "modal.oi_down": "🔴 ब्याज घट रहा — पैसा जा रहा",
        "modal.strength": "ताक़त:",
        "modal.top10": "शीर्ष 10% कैंडल",
        "modal.p90mult": "{r} × p90",
        "modal.price_move": "कैंडल में भाव:",
        "modal.tier": "टियर:",
        "modal.tier_mini": "मिनी",
        "modal.cvd_about": "CVD — कैंडल में आक्रामक खरीद बनाम बिक्री का अंतर: बाज़ार में किसने ज़ोर लगाया।",
        "modal.oi_about": "OI — कैंडल में ओपन इंटरेस्ट का बदलाव: बढ़त मतलब पोज़िशन में पैसा आ रहा, घटत मतलब जा रहा।",
        "modal.book_title": "📖 ऑर्डर क्लस्टर {sym}",
        "modal.book_zone": "प्राइस ज़ोन:",
        "modal.book_walls": "ज़ोन में दीवारें:",
        "modal.book_about": "दीवार = L2 बुक में लिमिट ऑर्डरों का घना स्तर। प्लेट उसी कैंडल पर टिकी रहती है जहाँ दीवार मिली — जब तक हटाई या फुल न हो जाए; «खाई गई» मतलब पीक का 70% से कम बचा।",
        "modal.clust_of": "{sym} लिक्विडेशन",
        "modal.level": "स्तर:",
        "modal.events": "इवेंट:",
        "modal.whale": "व्हेल",
        "modal.split": "स्प्लिट:",
        "modal.clust_about": "क्लस्टर — कैंडल के आस-पास भाव के लिक्विडेशन एक साथ; जुड़े इवेंट फ़ीड में हाइलाइट हैं।",
        "sym.all": "🌐 सभी",
        "sym.choose": "सिक्का चुनें",
        "sym.all_title": "फ़ीड में सभी सिक्के (चार्ट उसी सिक्के पर रहेगा)",
        "sym.click": "क्लिक — फ़ीड और चार्ट इस सिक्के पर",
        "sym.shift": "Shift+क्लिक — केवल चार्ट बदलें, फ़ीड नहीं",
        "sym.custom": "खोज से मैन्युअल जोड़ा गया",
        "sym.empty": "कुछ नहीं मिला",
        "search.nothing": "कुछ नहीं मिला: \"{q}\"",
        "search.add": "＋ जोड़ें",
        "search.open": "📈 खोलें",
        "search.vol24": "24गं {v}",
        "search.added_ok": "✓ {sym} सूची में जोड़ा गया और चार्ट पर खोला गया",
        "search.added_fail": "⚠ पेयर {sym} नहीं मिला",
        "search.network_error": "⚠ खोज में सर्वर से संपर्क में त्रुटि",
        "health.prices": "कीमतें",
        "health.ticks": "टिक्स",
        "health.connected": "जुड़ा, घटनाएँ: {n}",
        "health.noconn": "संपर्क नहीं: {err}",
        "health.open": "एक्सचेंज स्थिति — होवर या क्लिक",
        "health.summary": "{ok}/{n}",
        "health.gate_ref": "💠 Gate पर ट्रेड करें — फ़ीस में छूट",
        "land.title": "LiqScope — रीयल-टाइम क्रिप्टो फ्यूचर्स लिक्विडेशन टर्मिनल",
        "land.gate.link": "💠 Gate पर ट्रेड करें — फ़ीस में छूट",
        "land.gate.note": "पार्टनर लिंक: Gate.io पर रजिस्ट्रेशन प्रोजेक्ट को सपोर्ट करता है — टर्मिनल मुफ़्त रहता है।",
        "land.meta": "LiqScope — मुफ़्त लाइव लिक्विडेशन टर्मिनल: Binance, Bybit, OKX, Gate.io, Bitget, HTX। क्लस्टर के साथ कैंडल चार्ट, पेयर खोज, राशि और एक्सचेंज फ़िल्टर।",
        "land.badge": "लाइव लिक्विडेशन फीड",
        "land.h1.1": "क्रिप्टो फ्यूचर्स लिक्विडेशन",
        "land.h1.2": "रीयल-टाइम में",
        "land.sub": "LiqScope Binance, Bybit, OKX, Gate.io, Bitget, HTX और कई अन्य एक्सचेंज पर जबरन पोज़िशन बंद होने को पकड़ता है और उन्हें लाइव कैंडल चार्ट पर दिखाता है — क्लस्टर, वॉल्यूम प्रोफ़ाइल और किसी भी पेयर की खोज के साथ।",
        "land.hero.more": "क्या करता है",
        "land.nav.live": "लाइव फ़ीड",
        "land.nav.features": "विशेषताएँ",
        "land.nav.how": "कैसे काम करता है",

        "land.nav.digest": "डाइजेस्ट",
        "land.nav.hourly": "हर घंटे की सारांश",
        "hour.title": "हर घंटे की बाज़ार सारांश",
        "hour.meta": "LiqScope हर घंटे की सारांश: विंडो के लिक्विडेशन, घंटे के लीडर, टर्नओवर और ओपन इंटरेस्ट। वही पोस्ट जो चैनल में आते हैं, फ़ोटो के साथ।",
        "hour.lead": "बाज़ार का सारांश दिन में कई बार छपता है।",
        "hour.fresh": "नई सारांश",
        "hour.pick": "दिन चुनें",
        "hour.total": "संग्रह में सारांश",
        "hour.days": "संग्रह में दिन",
        "hour.channel": "Telegram चैनल",
        "hour.every": "हर {h} घंटे",
        "hour.window": "{h} घंटे की विंडो",
        "hour.events": "{n} इवेंट",
        "hour.n_posts": "{n} पोस्ट",
        "hour.has": "सारांश उपलब्ध",
        "hour.in_archive": "संग्रह में {n}",
        "hour.more": "संग्रह में {n} सारांश: ऊपर नई, बाकी दिन से खुलती हैं।",
        "hour.tz": "दिन {tz} में",
        "hour.prev": "पिछला महीना",
        "hour.next": "अगला महीना",
        "hour.loading": "लोड हो रहा है…",
        "hour.empty": "अभी कोई सारांश नहीं — चैनल में पोस्ट के बाद पहली आएगी।",
        "hour.empty_day": "इस दिन कोई सारांश नहीं।",
        "hour.photo_alt": "सारांश की फ़ोटो",
        "hour.link": "सारांश का लिंक",
        "hour.digest": "डाइजेस्ट",
        "hour.to_terminal": "टर्मिनल पर",
        "hour.cabinet": "खाता",
        "hour.admin": "एडमिन",
        "hour.login": "खाता",
        "dig.title": "रोज़ का बाज़ार डाइजेस्ट",
        "dig.meta": "LiqScope रोज़ का डाइजेस्ट: 24 घंटे के लिक्विडेशन, एक्सचेंज, ओपन इंटरेस्ट, कीमतें और बाज़ार का मूड।",
        "dig.lead": "हर शाम लगभग 22:00 MSK — दिन का सार: सबसे बड़ा लिक्विडेशन, एक्सचेंज, टर्नओवर के मुकाबले सबसे भारी ओपन इंटरेस्ट वाले सिक्के, टॉप-5 कीमतें और बाज़ार का मूड।",
        "dig.archive": "अंक",
        "dig.fresh": "नवीनतम अंक",
        "dig.pick": "तारीख चुनें",
        "dig.more": "संग्रह में {n} अंक: ऊपर नए, बाकी तारीख से खुलते हैं।",
        "dig.has": "अंक उपलब्ध",
        "dig.in_archive": "संग्रह में {n}",
        "dig.cabinet": "खाता",
        "dig.admin": "एडमिन",
        "dig.login": "खाता",
        "dig.prev": "पिछला महीना",
        "dig.next": "अगला महीना",
        "dig.empty": "पहला अंक आज शाम आएगा — अभी संग्रह खाली है।",
        "dig.loading": "लोड हो रहा है…",
        "dig.published": "प्रकाशित",
        "dig.draft": "ड्राफ़्ट",
        "dig.mood": "मूड",
        "dig.total": "24 घंटे के लिक्विडेशन",
        "dig.count": "इवेंट",
        "dig.back": "टर्मिनल पर",
        "ad.tag": "विज्ञापन",
        "ad.open": "खोलें",
        "ad.prev": "पिछला",
        "ad.next": "अगला",
        "dig.schedule": "हर शाम लगभग 22:00 MSK पर प्रकाशित",
        "land.cta.open": "टर्मिनल खोलें",
        "land.stat.24h": "24घं में लिक्विडेशन",
        "land.stat.1h": "पिछले घंटे में",
        "land.stat.exch": "लाइव एक्सचेंज",
        "land.stat.ratio": "लॉन्ग × शॉर्ट (24घं)",
        "land.cvd.title": "CVD {sym} — कौन दबा रहा: खरीदार या विक्रेता",
        "land.cvd.hint": "टेकर खरीद घटा बिक्री · हर 4 सेकंड अपडेट",
        "land.oi.title": "OI {sym} — पोज़िशन में पैसा: आवक और निकास",
        "land.oi.hint": "सभी एक्सचेंजों का ओपन इंटरेस्ट · हर 4 सेकंड अपडेट",
        "land.stat.cvd24h": "24घं में CVD",
        "land.stat.cvd1h": "1घं में CVD",
        "land.stat.cvd5m": "5मि में CVD",
        "land.stat.oiTotal": "ओपन इंटरेस्ट",
        "land.stat.oi24h": "OI Δ 24घं में",
        "land.stat.oi1h": "OI Δ 1घं में",
        "land.stat.oi5m": "OI Δ 5मि में",
        "land.live.title": "सर्वर से सीधा लाइव फ़ीड",
        "land.live.hint": "हर 4 सेकंड अपडेट · पूर्ण संस्करण टर्मिनल में",
        "land.live.connect": "सर्वर से जुड़ रहे हैं…",
        "land.feat.title": "टर्मिनल क्या कर सकता है",
        "land.feat.lead": "कोई कुंजी या सशुल्क पंजीकरण नहीं — केवल सार्वजनिक एक्सचेंज डेटा।",
        "land.card1.noun": "एक्सचेंज",
        "land.card1.noun_one": "एक्सचेंज",
        "land.card1.noun_few": "एक्सचेंज",
        "land.card1.rest": "एक फ़ीड में",
        "land.card1.desc": "Binance, Bybit, OKX, Gate.io, Bitget, HTX के लिक्विडेशन एक लाइव फ़ीड में मिलते हैं — बिना डुप्लिकेट, सामान्य किए गए पेयर।",
        "land.card2.title": "क्लस्टर के साथ कैंडल चार्ट",
        "land.card2.desc": "लाइव 1मि–4घं कैंडल; हर लिक्विडेशन अपनी सटीक कीमत पर आयत के रूप में: मैजेंटा — लॉन्ग, सियान — शॉर्ट, $100K से व्हेल — पीले से चमकीले लाल तक हीट स्केल पर।",
        "land.card3.title": "कीमतों के अनुसार लिक्विडेशन प्रोफ़ाइल",
        "land.card3.desc": "चार्ट पर इंडिकेटर दिखाता है कि दृश्य अवधि में लिक्विडेशन वॉल्यूम किन कीमत स्तरों पर केंद्रित है। एक बटन से ऑन/ऑफ।",
        "land.card4.title": "किसी भी पेयर की खोज",
        "land.card4.desc": "सिक्का सूची में नहीं? कोड लिखें — टर्मिनल उसे एक्सचेंजों के पूर्ण कैटलॉग में ढूँढकर चार्ट पर जोड़ देगा। GRAM, नई लिस्टिंग, दुर्लभ पर्पेचुअल।",
        "land.card5.title": "लचीले फ़िल्टर",
        "land.card5.desc": "न्यूनतम लिक्विडेशन राशि हाथ से दर्ज करें, एक्सचेंज चेकबॉक्स से चालू/बंद करें। सेटिंग्स ब्राउज़र में सेव रहती हैं।",
        "land.card6.title": "मोबाइल पर भी चलता है",
        "land.card6.desc": "सिंगल-कॉलम मोबाइल लेआउट: चार्ट, क्लिक करने योग्य सिक्के वाली फ़ीड और फ़िल्टर हमेशा हाथ में।",
        "land.card7.title": "बिना देरी के टिक",
        "land.card7.desc": "खुले चार्ट की हर ट्रेड तुरंत कैंडल को हिलाती है। मिलीसेकंड का विलंब चार्ट के ठीक ऊपर दिखता है।",
        "land.card8.title": "रीयल-टाइम डायग्नोस्टिक्स",
        "land.card8.desc": "हेडर इंडिकेटर दिखाते हैं कौन सा एक्सचेंज जुड़ा है, कितनी घटनाएँ आईं और कीमतें कहाँ से आती हैं। विफलता पर स्रोत खुद बदल जाते हैं।",
        "land.card9.title": "मुफ़्त अलर्ट",
        "land.card9.desc": "रजिस्ट्रेशन के बाद Telegram में मुफ़्त लिक्विडेशन अलर्ट मिल सकते हैं: अपना कॉइन, थ्रेशोल्ड और विंडो — और इसके लिए कुछ भी देना नहीं है।",
        "land.card9.cta": "मुफ़्त रजिस्टर करें",
        "land.how.title": "यह कैसे काम करता है",
        "land.step1.title": "बात सुनते हैं एक्सचेंजों की",
        "land.step1.desc": "लिक्विडेशन और ट्रेड के पब्लिक WebSocket चैनल — सीधे, बिना बिचौलियों के।",
        "land.step2.title": "डेटा सामान्य करते हैं",
        "land.step2.desc": "पेयर, राशि और दिशा एक समान रूप में लाए जाते हैं, डुप्लिकेट हटाए जाते हैं।",
        "land.step3.title": "चार्ट पर बनाते हैं",
        "land.step3.desc": "लिक्विडेशन अपनी कीमत पर कैंडल पर बैठते हैं; प्रोफ़ाइल स्तरों के अनुसार वॉल्यूम दिखाती है।",
        "land.step4.title": "आप देखते हैं",
        "land.step4.desc": "फ़ीड, 5मि/1घं/24घं आँकड़े, टॉप सिक्के — सब कुछ रीयल-टाइम में।",
        "land.cta.title": "बाज़ार को अंदर से देखने के लिए तैयार?",
        "land.cta.note": "मुफ़्त · कोई सशुल्क पंजीकरण नहीं · कोई API कुंजी नहीं · सार्वजनिक स्ट्रीम से डेटा",
        "land.footer.copy": "© 2026 LiqScope टर्मिनल · सार्वजनिक एक्सचेंज WebSocket से डेटा",
        "cookie.title": "यह साइट cookie का उपयोग करती है",
        "cookie.text": "यह सामान्य तरीका है: cookie आपको लॉग-इन रखती हैं, चुनी हुई भाषा याद रखती हैं और विज़िट गिनने में मदद करती हैं — इनके बिना हम असली मेहमान और सेवा अनुरोध में फ़र्क़ नहीं कर पाते।",
        "cookie.ok": "समझ गया",
        "cookie.more": "क्या-क्या रखा जाता है",
        "cookie.sid": "लॉग-इन — जब तक आप ख़ुद लॉग-आउट न करें",
        "cookie.vid": "मेहमान नंबर — केवल विज़िट आँकड़ों के लिए",
        "cookie.pref": "भाषा, थीम और चार्ट सेटिंग — आपके ब्राउज़र में",
        "cookie.note": "साइट का उपयोग जारी रखने का अर्थ है कि आप इससे सहमत हैं।",
        "land.footer.race": "यह निवेश सलाह नहीं है",
        "land.footer.open": "टर्मिनल खोलें →",
            "gate.title": "मुफ़्त में रजिस्टर करें",
        "gate.text": "चार्ट लेयर सभी के लिए खुले हैं। रजिस्टर करें — Telegram सिग्नल और बिना विज्ञापन वाला टर्मिनल पाएँ।",
        "gate.f1": "Telegram सिग्नल",
        "gate.f2": "बिना विज्ञापन टर्मिनल",
        "gate.f3": "कैबिनेट और सेवाएँ",
        "gate.cta": "मुफ़्त में रजिस्टर करें",
        "gate.later": "जारी रखें",
        "gate.close_title": "विंडो बंद करें",
        "gate.trial_badge": "{min}मि",
        "gate.trial_title": "लेयर सभी के लिए खुले हैं",
};

    var ES = {
        "terminal.title": "LiqScope Terminal — stream de liquidaciones en vivo y gráficos",
        "terminal.meta": "LiqScope Terminal — stream en vivo de liquidaciones de futuros cripto de 8 exchanges, gráfico de velas, clústeres y perfil de volúmenes.",
        "brand.badge": "TERMINAL",
        "brand.home_title": "Volver al inicio — LiqScope Terminal",
        "layout.pane_split": "Altura del bloque — arrastra el divisor, doble clic para restablecer",
        "layout.feed_split": "Ancho de la lista — arrastra el divisor",
        "layout.coins_split": "Altura del bloque de monedas — arrastra el divisor",
        "layout.chart_split": "Altura del gráfico — arrastra arriba o abajo",
        "conn.connecting": "CONECTANDO…",
        "conn.live": "LIVE WS",
        "conn.reconnecting": "RECONECTANDO…",
        "stats.24h": "Liquidaciones (24h)",
        "stats.1h": "Liquidaciones (1h)",
        "stats.5m": "Liquidaciones (5m)",
        "stats.longs": "Longs",
        "stats.shorts": "Shorts",
        "stats.cvd24h": "24h",
        "stats.cvd1h": "1h",
        "stats.cvd5m": "5m",
        "stats.cvd_title": "CVD de la moneda del gráfico: compras taker menos ventas",
        "stats.oi_title": "Cambio del OI de la moneda del gráfico en el período — clic para cambiar",
        "stats.box_liq": "Liquidaciones",
        "stats.liq_title": "Liquidaciones del período elegido — clic para cambiar",
        "stats.w1m": "1 min",
        "stats.w5m": "5 min",
        "stats.w15m": "15 min",
        "stats.w30m": "30 min",
        "stats.w1h": "1 h",
        "stats.w4h": "4 h",
        "stats.w24h": "24 h",
        "ctrl.moneta": "Moneda:",
        "ctrl.tf": "Tiempo:",
        "ctrl.minvol": "Mín. importe:",
        "ctrl.exchange": "Exchange:",
        "search.placeholder": "Buscar par: BTC, SOL, GRAM…",
        "search.hint": "Busca monedas menos líquidas.",
        "filter.all_usd": "Todos ($0+) ▾",
        "filter.min_ge": "≥ ${v} ▾",
        "filter.usd_placeholder": "p. ej. 10000",
        "filter.ok": "OK",
        "filter.preset_all": "Todos",
        "filter.usd_note": "Solo se muestran liquidaciones ≥ umbral.",
        "filter.exch_header": "Exchanges activos",
        "filter.all": "Todos",
        "filter.none": "Ninguno",
        "filter.all_exchanges": "Todos los exchanges ▾",
        "filter.disabled": "Desactivados ▾",
        "filter.thresholds_title": "Umbrales de volumen: liquidaciones, CVD y OI Δ",
        "filter.exchanges_title": "Filtrar por exchanges",
        "filter.cvd_placeholder": "p. ej. 50000",
        "filter.oi_placeholder": "p. ej. 1000000",
        "chart.cvd_current_title": "Quién mueve el precio en la vela actual",
        "feed.show_all_title": "Mostrar todas las monedas",
        "lang.select_title": "Idioma / Language",

        "filter.th_liq": "Liquidaciones, $",
        "filter.th_short_liq": "Liq",
        "filter.th_ge": "≥ ${v}",
        "filter.th_cvd": "CVD por vela, $",
        "filter.th_oi": "OI Δ por vela, $",
        "filter.presets_for": "Los presets se aplican a la pestaña abierta del feed:",
        "filter.th_note": "Cada umbral filtra su propia serie — el feed y las marcas del gráfico: liquidaciones, triángulos de CVD y bolas de OI. Cero muestra todo.",
        "chart.long": "Liq. longs",
        "chart.short": "Liq. shorts",
        "chart.whale": "Ballena ($100k+)",
        "chart.whale_title": "Escala ballena: $100K amarillo … $1M+ rojo brillante",
        "chart.profile": "📊 Perfil",
        "chart.toggle_title": "Plegar/expandir gráfico",
        "chart.expand_title": "Expandir el gráfico a pantalla completa",
        "chart.draw_title": "Dibujar figuras de análisis técnico",
        "chart.layers": "☰ Capas",
        "chart.layers_title": "Mostrar/ocultar botones de capas",
        "chart.follow": "🎯 Auto",
        "chart.follow_full": "🎯 Seguimiento automático del precio",
        "chart.follow_on": "Seguimiento activo: la vela no sale del borde y el precio siempre se ve. Pulsa para desactivarlo",
        "chart.follow_off": "Seguimiento desactivado: el gráfico se queda donde lo dejaste. Pulsa para activarlo",
        "chart.liq_label": "Liquidaciones",
        "chart.profile_label": "Perfil",
        "chart.cvd_label": "CVD",
        "chart.oi_label": "OI",
        "draw.line": "Línea de tendencia",
        "draw.ray": "Rayo",
        "draw.horiz": "Línea horizontal",
        "draw.pane_hint": "Aquí también puedes dibujar figuras técnicas",
        "draw.rect": "Rectángulo",
        "draw.fib": "Retroceso de Fibonacci",
        "draw.eraser": "Borrador: clic en una figura para borrarla",
        "draw.clear": "Borrar todas las figuras",
        "draw.close": "Cerrar dibujo",
        "chart.collapse_title": "Salir de pantalla completa",
        "chart.profile_on": "Ocultar perfil de volúmenes de liquidaciones",
        "chart.profile_off": "Mostrar perfil de volúmenes de liquidaciones",
        "chart.liq": "⚡ Liquidaciones",
        "chart.liq_on": "Ocultar bloques de liquidación del gráfico",
        "chart.liq_off": "Mostrar bloques de liquidación en el gráfico",
        "chart.cvd": "🎯 CVD",
        "chart.oi": "● OI",
        "chart.live_liq": "Liquidaciones de la vela actual",
        "chart.live_profile": "Liquidaciones del rango visible",
        "chart.live_oi": "Cambio de OI de la vela actual",
        "chart.oi_on": "Ocultar bolas de interés abierto",
        "chart.oi_off": "Mostrar bolas de interés abierto: 🟢 subida, 🔴 bajada por vela",
        "chart.pane_liq": "💥 Panel LIQ",
        "chart.pane_cvd": "🌊 Panel CVD",
        "chart.pane_oi": "📈 Panel OI",
        "chart.pane_liq_head": "💥 LIQ",
        "chart.pane_cvd_head": "🌊 CVD",
        "chart.pane_oi_head": "📈 OI",
        "chart.pane_close": "Ocultar panel de indicador",
        "chart.pane_liq_on": "Ocultar el panel de liquidaciones bajo el gráfico",
        "chart.pane_liq_off": "Mostrar el panel de liquidaciones bajo el gráfico",
        "chart.pane_cvd_on": "Ocultar el panel CVD bajo el gráfico",
        "chart.pane_cvd_off": "Mostrar el panel CVD bajo el gráfico",
        "chart.pane_oi_on": "Ocultar el panel OI bajo el gráfico",
        "chart.pane_oi_off": "Mostrar el panel de interés abierto bajo el gráfico",
        "chart.cvd_on": "Ocultar triángulos CVD (desequilibrio compra/venta por vela)",
        "chart.cvd_off": "Mostrar triángulos CVD: ▲ compradores vs ▼ vendedores en cada vela",
        "chart.cvd_buy": "CVD ▲ compras",
        "chart.cvd_sell": "CVD ▼ ventas",
        "chart.error": "La librería de gráficos no se cargó (/static/lightweight-charts.js).",
        "src.no_link": "sin conexión con el exchange",
        "src.demo": "flujo demo",
        "src.waiting": "esperando ticks…",
        "src.no_ticks": "sin ticks durante {s} s",
        "src.ticks": "⚡ ticks: {n} · retardo {ms} ms",
        "feed.title": "⚡ FEED DE LIQUIDACIONES EN VIVO",
        "feed.tab_liq": "⚡ Liquidaciones",
        "feed.tab_cvd": "🎯 CVD",
        "feed.tab_oi": "● OI",
        "feed.empty_cvd": "Sin cambios CVD significativos en la moneda del gráfico…",
        "feed.empty_oi": "Sin cambios OI significativos en la moneda del gráfico…",
        "feed.tab_book": "📖 Órdenes",
        "feed.empty_book": "Aún no hay muros en el libro de esta moneda — esperamos límites de los exchanges…",
        "feed.book_off": "La capa 📖 libro de órdenes está apagada — actívala en el panel de capas",
        "feed.col_book": "MURO ($)",
        "feed.book_live": "viva",
        "feed.book_eaten": "comida",
        "feed.book_gone": "fuera",
        "feed.book_walls_n": "muros",
        "feed.book_levels_n": "niveles",
        "feed.col_cvd": "CVD ($)",
        "feed.col_oi": "OI Δ ($)",
        "feed.cvd_buy": "CVD ▲",
        "feed.cvd_sell": "CVD ▼",
        "feed.oi_up": "OI ▲",
        "feed.oi_down": "OI ▼",
        "feed.count_one": "{n} evento",
        "feed.count_few": "{n} eventos",
        "feed.count_many": "{n} eventos",
        "feed.col_time": "Hora",
        "feed.col_coin": "Moneda",
        "feed.col_exch": "Exchange",
        "feed.col_type": "Tipo",
        "feed.col_usd": "IMPORTE LIQ. ($)",
        "feed.col_price": "Precio ($)",
        "feed.empty": "Esperando eventos de los exchanges…",
        "feed.open_chart": "Feed y gráfico de {sym}",
        "feed.empty_flow": "Reuniendo el flujo de todas las monedas — esperando los primeros minutos…",
        "flow.window": "Ventana",
        "flow.window_val": "{n} min",
        "flow.window_short": "{n}m",
        "flow.cvd": "CVD (compras − ventas)",
        "flow.oi_delta": "Cambio de OI",
        "flow.share": "Parte del volumen",
        "flow.oi_level": "OI ahora",
        "flow.liq_long": "Liquidaciones de largos",
        "flow.liq_short": "Liquidaciones de cortos",
        "flow.vol": "Volumen en la ventana",
        "flow.price": "Precio",
        "flow.note": "La fila es el flujo por minuto de una moneda, no una vela del gráfico: se ven todas las monedas a la vez mientras el gráfico sigue en la tuya.",
        "feed.chip_title": "Mostrar todas las monedas",
        "feed.all_feed": "feed: todas las monedas",
        "top.title": "🔥 Top liquidaciones (24h)",
        "top.by_vol": "💥 Por volumen",
        "top.by_count": "🔢 Por cantidad",
        "top.count": "{n} liqs.",
        "sound.title": "Alerta sonora",
        "clear.title": "Limpiar",
        "clear.btn": "🧹 Limpiar",
        "clear.menu_title": "Limpiar o recuperar el historial",
        "clear.do": "🧹 Limpiar el gráfico",
        "clear.undo": "↩️ Recuperar historial",
        "clear.undone": "historial recuperado: los eventos volverán a llegar del servidor",
        "modal.title": "Detalles de la liquidación",
        "modal.liq_of": "Liquidación {sym}",
        "modal.exch": "Exchange:",
        "modal.src": "Origen:",
        "modal.src_tape": "deducido de la cinta de operaciones, confirmado vía /info",
        "modal.src_feed": "flujo del exchange",
        "feed.tape_tip": "Hyperliquid no publica liquidaciones: el evento se deduce de una ráfaga de órdenes en la cinta y se confirma vía /info",
        "modal.exchs": "Exchanges:",
        "modal.type": "Tipo:",
        "modal.long_desc": "LONG (venta forzada)",
        "modal.short_desc": "SHORT (compra forzada)",
        "modal.price": "Precio:",
        "modal.usd": "Importe:",
        "modal.qty": "Cantidad:",
        "modal.time": "Hora:",
        "modal.cvd_of": "CVD {sym}",
        "modal.oi_of": "OI {sym}",
        "modal.candle": "Vela:",
        "modal.live": "en formación",
        "modal.direction": "Dirección:",
        "modal.cvd_buy": "▲ Compradores dominan",
        "modal.cvd_sell": "▼ Vendedores dominan",
        "modal.oi_up": "🟢 Interés sube — entra dinero",
        "modal.oi_down": "🔴 Interés baja — sale dinero",
        "modal.strength": "Fuerza:",
        "modal.top10": "top 10% de velas",
        "modal.p90mult": "{r} × p90",
        "modal.price_move": "Precio en la vela:",
        "modal.tier": "Nivel:",
        "modal.tier_mini": "mini",
        "modal.cvd_about": "CVD — desbalance de compras vs ventas agresivas en la vela: quién pegó más fuerte.",
        "modal.oi_about": "OI — cambio del interés abierto en la vela: sube cuando entra dinero en posiciones, baja cuando sale.",
        "modal.book_title": "📖 Cúmulo de órdenes {sym}",
        "modal.book_zone": "Zona de precio:",
        "modal.book_walls": "Muros en la zona:",
        "modal.book_about": "Un muro es un nivel denso de órdenes límite en el libro L2. La placa queda en la vela donde se encontró el muro y permanece hasta que lo retiran o lo ejecutan; «comida» significa que queda menos del 70% del pico.",
        "modal.clust_of": "Liquidaciones {sym}",
        "modal.level": "Nivel:",
        "modal.events": "Eventos:",
        "modal.whale": "ballena",
        "modal.split": "Split:",
        "modal.clust_about": "Clúster — liquidaciones de la vela agrupadas por precio cercano. Los eventos vinculados se resaltan en el feed.",
        "sym.all": "🌐 TODAS",
        "sym.choose": "Seleccionar moneda",
        "sym.all_title": "Todas las monedas en el feed (el gráfico permanece en la actual)",
        "sym.click": "Clic — feed y gráfico de esta moneda",
        "sym.shift": "Shift+clic — cambiar solo el gráfico, mantener el feed",
        "sym.custom": "Añadido manualmente mediante búsqueda",
        "sym.empty": "Nada encontrado",
        "search.nothing": "No se encontró: \"{q}\"",
        "search.add": "＋ Añadir",
        "search.open": "📈 Abrir",
        "search.vol24": "24h {v}",
        "search.added_ok": "✓ {sym} añadido a la lista y abierto en el gráfico",
        "search.added_fail": "⚠ No se encontró el par {sym}",
        "search.network_error": "⚠ Error de conexión al buscar",
        "health.prices": "precios",
        "health.ticks": "ticks",
        "health.connected": "conectado, eventos: {n}",
        "health.noconn": "sin conexión: {err}",
        "health.open": "Estado de exchanges — pasa el cursor o haz clic",
        "health.summary": "{ok}/{n}",
        "health.gate_ref": "💠 Opera en Gate — descuento en comisiones",
        "land.title": "LiqScope — terminal de liquidaciones de futuros cripto en tiempo real",
        "land.gate.link": "💠 Opera en Gate — descuento en comisiones",
        "land.gate.note": "Enlace de socio: registrarse en Gate.io apoya el proyecto — el terminal sigue siendo gratis.",
        "land.meta": "LiqScope — terminal gratuito de liquidaciones en vivo: Binance, Bybit, OKX, Gate.io, Bitget, HTX. Gráfico de velas con clústeres, búsqueda de pares, filtros por importe y exchange.",
        "land.badge": "Flujo de liquidaciones en vivo",
        "land.h1.1": "Liquidaciones de futuros cripto",
        "land.h1.2": "en tiempo real",
        "land.sub": "LiqScope captura los cierres forzados de posiciones en Binance, Bybit, OKX, Gate.io, Bitget, HTX y más, y los dibuja en un gráfico de velas en vivo — con clústeres, perfil de volúmenes y búsqueda de cualquier par negociable.",
        "land.hero.more": "Qué ofrece",
        "land.nav.live": "Feed en vivo",
        "land.nav.features": "Funciones",
        "land.nav.how": "Cómo funciona",

        "land.nav.digest": "Resumen",
        "land.nav.hourly": "Resúmenes por horas",
        "hour.title": "Resúmenes del mercado por horas",
        "hour.meta": "Resúmenes de LiqScope: liquidaciones de la ventana, líderes de la hora, volumen e interés abierto. Las mismas publicaciones del canal, con foto.",
        "hour.lead": "Un resumen del mercado se publica varias veces al día.",
        "hour.fresh": "Resúmenes recientes",
        "hour.pick": "Elegir día",
        "hour.total": "Resúmenes en el archivo",
        "hour.days": "Días en el archivo",
        "hour.channel": "Canal de Telegram",
        "hour.every": "cada {h} h",
        "hour.window": "ventana de {h} h",
        "hour.events": "{n} eventos",
        "hour.n_posts": "{n} publicaciones",
        "hour.has": "hay resúmenes",
        "hour.in_archive": "{n} en el archivo",
        "hour.more": "{n} resúmenes en el archivo: arriba los recientes, el resto se abren por día.",
        "hour.tz": "días en {tz}",
        "hour.prev": "Mes anterior",
        "hour.next": "Mes siguiente",
        "hour.loading": "Cargando…",
        "hour.empty": "Todavía no hay resúmenes — el primero llega tras la publicación en el canal.",
        "hour.empty_day": "No hay resúmenes para este día.",
        "hour.photo_alt": "Foto del resumen",
        "hour.link": "Enlace al resumen",
        "hour.digest": "Resumen",
        "hour.to_terminal": "Al terminal",
        "hour.cabinet": "Cuenta",
        "hour.admin": "Admin",
        "hour.login": "Cuenta",
        "dig.title": "Resumen diario del mercado",
        "dig.meta": "Resumen diario de LiqScope: liquidaciones de 24 h, exchanges, interés abierto, precios y el ánimo del mercado.",
        "dig.lead": "Cada tarde alrededor de las 22:00 MSK — el día en resumen: la mayor liquidación, los exchanges, las monedas con más interés abierto frente al volumen, los precios del top 5 y el ánimo general del mercado.",
        "dig.archive": "Ediciones",
        "dig.fresh": "Últimos números",
        "dig.pick": "Elegir fecha",
        "dig.more": "{n} números en el archivo: arriba los recientes, el resto se abren por fecha.",
        "dig.has": "número disponible",
        "dig.in_archive": "{n} en el archivo",
        "dig.cabinet": "Cuenta",
        "dig.admin": "Admin",
        "dig.login": "Cuenta",
        "dig.prev": "Mes anterior",
        "dig.next": "Mes siguiente",
        "dig.empty": "La primera edición llega esta noche — el archivo aún está vacío.",
        "dig.loading": "Cargando…",
        "dig.published": "publicado",
        "dig.draft": "borrador",
        "dig.mood": "Ánimo",
        "dig.total": "Liquidaciones 24 h",
        "dig.count": "Eventos",
        "dig.back": "Al terminal",
        "ad.tag": "Publicidad",
        "ad.open": "Abrir",
        "ad.prev": "Anterior",
        "ad.next": "Siguiente",
        "dig.schedule": "Se publica cada tarde ~22:00 MSK",
        "land.cta.open": "Abrir terminal",
        "land.stat.24h": "Liquidaciones en 24h",
        "land.stat.1h": "En la última hora",
        "land.stat.exch": "Exchanges en vivo",
        "land.stat.ratio": "Longs × shorts (24h)",
        "land.cvd.title": "CVD {sym} — quién empuja: compradores o vendedores",
        "land.cvd.hint": "compras taker menos ventas · se actualiza cada 4 segundos",
        "land.oi.title": "OI {sym} — dinero en posiciones: entradas y salidas",
        "land.oi.hint": "interés abierto de todos los exchanges · se actualiza cada 4 segundos",
        "land.stat.cvd24h": "CVD en 24h",
        "land.stat.cvd1h": "CVD en 1h",
        "land.stat.cvd5m": "CVD en 5m",
        "land.stat.oiTotal": "Interés abierto",
        "land.stat.oi24h": "OI Δ en 24h",
        "land.stat.oi1h": "OI Δ en 1h",
        "land.stat.oi5m": "OI Δ en 5m",
        "land.live.title": "Feed en vivo directo del servidor",
        "land.live.hint": "se actualiza cada 4 segundos · versión completa en el terminal",
        "land.live.connect": "Conectando al servidor…",
        "land.feat.title": "Qué sabe hacer el terminal",
        "land.feat.lead": "Sin claves ni registro de pago — solo datos públicos de los exchanges.",
        "land.card1.noun": "exchanges",
        "land.card1.noun_one": "exchange",
        "land.card1.noun_few": "exchanges",
        "land.card1.rest": "en un solo feed",
        "land.card1.desc": "Las liquidaciones de Binance, Bybit, OKX, Gate.io, Bitget, HTX se unen en un feed en vivo sin duplicados y con pares normalizados.",
        "land.card2.title": "Gráfico de velas con clústeres",
        "land.card2.desc": "Velas en vivo de 1m–4h; cada liquidación se dibuja como un bloque en su precio exacto: magenta — longs, cian — shorts, ballenas desde $100K — en escala de calor de amarillo a rojo brillante.",
        "land.card3.title": "Perfil de liquidaciones por precios",
        "land.card3.desc": "Un indicador en el gráfico muestra en qué niveles de precio se concentra el volumen de liquidaciones durante el periodo visible. Se activa con un botón.",
        "land.card4.title": "Buscar cualquier par",
        "land.card4.desc": "¿El coin no está en la lista? Escribe el código — el terminal lo encuentra en el catálogo completo de los exchanges y lo añade al gráfico. GRAM, novedades, perpetuos raros.",
        "land.card5.title": "Filtros flexibles",
        "land.card5.desc": "Introduce el importe mínimo de liquidación a mano y activa o desactiva exchanges con casillas. Los ajustes se guardan en el navegador.",
        "land.card6.title": "Funciona en el móvil",
        "land.card6.desc": "Diseño móvil de una columna: gráfico, feed con moneda clicable y filtros siempre a mano.",
        "land.card7.title": "Ticks sin retardo",
        "land.card7.desc": "Cada operación en un gráfico abierto mueve la vela al instante. El retardo en milisegundos se ve justo encima del gráfico.",
        "land.card8.title": "Diagnóstico en tiempo real",
        "land.card8.desc": "Los indicadores de la cabecera muestran qué exchange está conectado, cuántos eventos llegaron y de dónde vienen los precios. Las fuentes cambian solas si fallan.",
        "land.card9.title": "Alertas gratis",
        "land.card9.desc": "Tras registrarte puedes recibir alertas gratis de liquidaciones en Telegram: tu moneda, umbral y ventana — y sin pagar nada.",
        "land.card9.cta": "Regístrate gratis",
        "land.how.title": "Cómo funciona",
        "land.step1.title": "Escuchamos a los exchanges",
        "land.step1.desc": "Canales WebSocket públicos de liquidaciones y operaciones — directamente, sin intermediarios.",
        "land.step2.title": "Normalizamos los datos",
        "land.step2.desc": "Pares, importes y lados se unifican; los duplicados se eliminan.",
        "land.step3.title": "Dibujamos en el gráfico",
        "land.step3.desc": "Las liquidaciones caen en las velas a su precio; el perfil muestra sus volúmenes por niveles.",
        "land.step4.title": "Tú observas",
        "land.step4.desc": "Feed, estadísticas 5m/1h/24h, top de monedas — todo se actualiza en tiempo real.",
        "land.cta.title": "¿Listo para ver el mercado desde dentro?",
        "land.cta.note": "Gratis · sin registro de pago · sin claves API · datos de flujos públicos de los exchanges",
        "land.footer.copy": "© 2026 LiqScope Terminal · datos de WebSocket públicos de los exchanges",
        "cookie.title": "Este sitio usa cookies",
        "cookie.text": "Es lo habitual: las cookies mantienen tu sesión, recuerdan el idioma elegido y ayudan a contar las visitas; sin ellas no podemos distinguir a un visitante real de una petición de servicio.",
        "cookie.ok": "Entendido",
        "cookie.more": "Qué se guarda exactamente",
        "cookie.sid": "la sesión — hasta que cierres sesión tú mismo",
        "cookie.vid": "un número de visitante — solo para estadísticas",
        "cookie.pref": "idioma, tema y ajustes de gráficos — en tu navegador",
        "cookie.note": "Si sigues usando el sitio, aceptas esto.",
        "land.footer.race": "No es una recomendación de inversión",
        "land.footer.open": "Abrir terminal →",
            "gate.title": "Regístrate gratis",
        "gate.text": "Las capas del gráfico están disponibles para todos. Regístrate para recibir señales en Telegram y un terminal sin anuncios.",
        "gate.f1": "Señales en Telegram",
        "gate.f2": "Terminal sin anuncios",
        "gate.f3": "Cuenta y servicios",
        "gate.cta": "Registrarse gratis",
        "gate.later": "Continuar",
        "gate.close_title": "Cerrar la ventana",
        "gate.trial_badge": "{min}m",
        "gate.trial_title": "Capas disponibles para todos",
};

    var MESSAGES = { ru: RU, en: EN, zh: ZH, hi: HI, es: ES };
    var current = "ru";
    var listeners = [];
    var PHRASES = { ru: {}, en: {}, zh: {}, hi: {}, es: {} };   // ru → перевод, по языкам

    /* =====================================================================
     * Словари страниц (i18n.pages.js)
     *
     * Кабинет, вход/регистрация, сброс пароля, дайджест, админка и то, что
     * сервисы рисуют сами, живут отдельным файлом — он большой и правится
     * чаще терминала. Формат простой и строго-JSON (его читает ещё и сервер,
     * чтобы отдать страницу сразу на нужном языке): {lang: {keys, phrases}}.
     * ===================================================================== */
    var PAGES = (global && global.LIQSCOPE_I18N_PAGES) || null;
    if (PAGES && typeof PAGES === "object") {
        Object.keys(PAGES).forEach(function (code) {
            var pack = PAGES[code];
            if (!pack || typeof pack !== "object") return;
            if (!MESSAGES[code]) MESSAGES[code] = {};
            var keys = pack.keys || {};
            Object.keys(keys).forEach(function (k) { MESSAGES[code][k] = keys[k]; });
            // фразы кладём как есть: ищем их в живом тексте от длинных к коротким
            if (pack.phrases && typeof pack.phrases === "object") {
                PHRASES[code] = pack.phrases;
            }
        });
    }

    /* =====================================================================
     * Ядро
     * ===================================================================== */
    function detect() {
        // 0) сервер пометил, что язык подобран за гостя (браузер или страна):
        //    тогда его выбор — подсказка, а не приказ, и шаги ниже решают сами
        var auto = false;
        try { auto = !!global.LIQSCOPE_LANG_AUTO; } catch (e) { /* ignore */ }
        var fromServer = "";
        try {
            fromServer = String(global.LIQSCOPE_LANG || "").toLowerCase();
        } catch (e) { /* ignore */ }
        // 1) явный выбор в ссылке: /?lang=zh — так языки индексируются
        try {
            var m = /[?&]lang=([a-zA-Z-]+)/.exec(String((global.location && global.location.search) || ""));
            var q = m ? m[1].toLowerCase().split("-")[0] : "";
            if (q && MESSAGES[q]) return q;
        } catch (e) { /* ignore */ }
        // 2) прошлый выбор посетителя
        try {
            var saved = localStorage.getItem(STORE_KEY);
            if (saved && MESSAGES[saved]) return saved;
        } catch (e) { /* ignore */ }
        // 3) выбор сервера по cookie или по ?lang= — он и решает
        if (fromServer && MESSAGES[fromServer] && !auto) return fromServer;
        // 4) язык браузера: и региональные варианты (zh-CN, zh-TW, es-419…)
        var nav = String((typeof navigator !== "undefined" && navigator.language) || "").toLowerCase();
        var all = String((typeof navigator !== "undefined" && navigator.languages
            && navigator.languages.join(",")) || nav).toLowerCase();
        var pick = null;
        all.split(",").some(function (tag) {
            var base = String(tag).trim().split("-")[0];
            if (MESSAGES[base]) { pick = base; return true; }
            return false;
        });
        if (pick) return pick;
        var base0 = nav.split("-")[0];
        if (MESSAGES[base0]) return base0;
        // 5) язык, подобранный сервером по стране, и если даже его нет —
        //    английский: это язык сайта по умолчанию
        if (fromServer && MESSAGES[fromServer]) return fromServer;
        return "en";
    }

    function localeTag() { return LOCALE_TAGS[current] || "en-US"; }
    function lang() { return current; }

    function t(key, vars) {
        var dict = MESSAGES[current] || MESSAGES.ru;
        var s = dict[key];
        if (s === undefined) {
            s = (MESSAGES.en && MESSAGES.en[key] !== undefined) ? MESSAGES.en[key] :
                (MESSAGES.ru && MESSAGES.ru[key] !== undefined) ? MESSAGES.ru[key] : key;
        }
        if (vars && typeof s === "string") {
            for (var k in vars) {
                if (Object.prototype.hasOwnProperty.call(vars, k)) {
                    s = s.split("{" + k + "}").join(String(vars[k]));
                }
            }
        }
        return s;
    }

    // Русские формы: 1 событие / 2 события / 5 событий; остальные — one/many.
    function plural(n, forms) {
        var f = Array.isArray(forms) ? forms : [forms];
        var nn = Math.abs(Number(n) || 0);
        if (current === "ru") {
            var m10 = nn % 10, m100 = nn % 100;
            if (m10 === 1 && m100 !== 11) return f[0] || f[1];
            if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return f[1] || f[2];
            return f[2] || f[1];
        }
        return nn === 1 ? (f[0] || f[1]) : (f[2] || f[1] || f[0]);
    }

    /** Перевод подписи элемента — без потери вложенных элементов.
     *
     * ``textContent = перевод`` сносил всех детей: счётчик непрочитанного
     * (``<span class="fb-dot">``) внутри кнопки «По всем вопросам» исчезал
     * вместе с цифрой, хотя по нему видно, что админ ответил. Меняем первый
     * текстовый узел, а элементы оставляем на месте; если текста не было —
     * вставляем его первым.
     */
    function setLabel(el, text) {
        var kids = el.childNodes, done = false, i;
        for (i = 0; i < kids.length; i++) {
            if (kids[i].nodeType !== 3) continue;      // 3 — текстовый узел
            if (done) { kids[i].nodeValue = ""; continue; }
            kids[i].nodeValue = text;
            done = true;
        }
        if (!done) {
            el.insertBefore(el.ownerDocument.createTextNode(text), el.firstChild);
        }
    }

    /** Реферальная ссылка Gate для текущего языка. */
    function gateRef() {
        return GATE_REFS[current] || GATE_REFS.en;
    }

    /** Все ссылки Gate на странице едут по одному адресу: размечаемся data-gate-ref. */
    function applyGateRefs(root) {
        if (!root || !root.querySelectorAll) return;
        var list = root.querySelectorAll("[data-gate-ref]");
        for (var i = 0; i < list.length; i++) {
            var a = list[i];
            if (!a.setAttribute) continue;
            a.setAttribute("href", gateRef());
            a.setAttribute("target", "_blank");
            a.setAttribute("rel", "noopener sponsored");
        }
    }

    function apply(root) {
        root = root || document;
        if (!root || !root.querySelectorAll) return;
        document.documentElement.lang = localeTag();
        var nodes = root.querySelectorAll("[data-i18n]");
        for (var i = 0; i < nodes.length; i++) {
            setLabel(nodes[i], t(nodes[i].getAttribute("data-i18n")));
        }
        nodes = root.querySelectorAll("[data-i18n-placeholder]");
        for (i = 0; i < nodes.length; i++) {
            nodes[i].setAttribute("placeholder", t(nodes[i].getAttribute("data-i18n-placeholder")));
        }
        nodes = root.querySelectorAll("[data-i18n-title]");
        for (i = 0; i < nodes.length; i++) {
            nodes[i].setAttribute("title", t(nodes[i].getAttribute("data-i18n-title")));
        }
        nodes = root.querySelectorAll("[data-i18n-meta]");
        for (i = 0; i < nodes.length; i++) {
            nodes[i].setAttribute("content", t(nodes[i].getAttribute("data-i18n-meta")));
        }
        // динамический текст (кабинет, сервисы, админка) — отдельным проходом
        localizeTree(root);
        // партнёрские ссылки — заодно: их язык тот же, что и у подписей
        applyGateRefs(root);
    }

    /* =====================================================================
     * Перевод живого текста
     *
     * Терминал собран из ключей (data-i18n), а кабинет, доски сервисов и
     * админка рисуются кодом из русских кусков: «Окно · », «пока тихо»,
     * «сохранено · монеты: …». Переводить такое ключами — значит переписать
     * половину файлов; поэтому текст переводится там, где он уже нарисован.
     *
     * Правила ровно те же, что у бота (bot_i18n.py):
     *   * ищем самый длинный известный фрагмент за один проход — короткая
     *     подпись не портит длинную фразу;
     *   * фрагмент, начинающийся или кончающийся русской буквой, ищется как
     *     целое слово: «бот» не залезает в «работает»;
     *   * числа перед хвостом («5 мин», «3 ч», «12 шт.») переводят правила.
     *
     * Текст пользователя (переписка, реклама, письма) помечен
     * ``data-i18n-skip`` и не трогается; складывается он в контейнеры со
     * своим «языком» — см. SKIP_SELECTOR.
     * ===================================================================== */

    //: куда не заглядываем: код, стили, поля ввода и текст пользователя
    var SKIP_SELECTOR = "script,style,noscript,code,pre,textarea,input," +
        // сам переключатель языка не трогаем: «Русский» человек должен узнать
        "select[data-lang-select],#lang-select," +
        "[data-i18n-skip],[contenteditable=true]";

    //: хвосты с числом: «5 мин» → «5 min». Порядок важен — длинные выше.
    var NUM_TAILS = [
        [/(\d+)\s*мин(?![\wА-Яа-яЁё])/g, function (n, l) { return n + tr(l, "min", "分钟", "मिनट", "min"); }],
        [/(\d+)\s*ч(?![\wА-Яа-яЁё])/g, function (n, l) { return n + tr(l, "h", "小时", "घं", "h"); }],
        [/(\d+)\s*сут(?![\wА-Яа-яЁё])/g, function (n, l) { return n + tr(l, "d", "天", "दिन", "d"); }],
        [/(\d+)\s*дн(?![\wА-Яа-яЁё])/g, function (n, l) { return n + tr(l, "d", "天", "दिन", "d"); }],
        [/(\d+)\s*сек(?![\wА-Яа-яЁё])/g, function (n, l) { return n + tr(l, "s", "秒", "से", "s"); }],
        [/(\d+)\s*шт\.?/g, function (n, l) { return n + tr(l, "pcs", "笔", "इकाई", "uds"); }],
        [/(\d+)\s*свеч\.?/g, function (n, l) { return n + tr(l, "candles", "根K线", "कैंडल", "velas"); }],
        [/(\d+)\s*пар(?![\wА-Яа-яЁё])/g, function (n, l) { return n + tr(l, "pairs", "对", "जोड़े", "pares"); }],
        [/(\d+)\s*знаков(?![\wА-Яа-яЁё])/g, function (n, l) { return n + tr(l, "chars", "字符", "अक्षर", "caracteres"); }],
        [/(\d+)\s*событи\w*/g, function (n, l) { return n + tr(l, "events", "个事件", "इवेंट", "eventos"); }],
        // «5м»/«2ч» — так окна пишет кабинет, пробела там нет
        [/(\d+)\s*м(?![\wА-Яа-яЁё])/g, function (n, l) { return n + tr(l, "m", "分", "मि", "m"); }],
        [/(\d+)\s*с(?![\wА-Яа-яЁё])/g, function (n, l) { return n + tr(l, "s", "秒", "से", "s"); }]
    ];

    function tr(l, en, zh, hi, es) {
        if (l === "zh") return zh;
        if (l === "hi") return hi;
        if (l === "es") return es;
        return en;
    }

    var CYR = /[А-Яа-яЁё]/;
    var WORD_CHARS = "A-Za-z0-9_";

    /** Русский фрагмент как регулярка: у краёв со словом — границы слова. */
    function phraseRe(phrase) {
        var head = phrase.charAt(0), tail = phrase.charAt(phrase.length - 1);
        var core = phrase.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        var pre = CYR.test(head) || new RegExp("[" + WORD_CHARS + "]").test(head) ? "(?<![" + WORD_CHARS + "А-Яа-яЁё])" : "";
        var post = CYR.test(tail) || new RegExp("[" + WORD_CHARS + "]").test(tail) ? "(?![" + WORD_CHARS + "А-Яа-яЁё])" : "";
        try {
            return new RegExp(pre + core + post, "g");
        } catch (e) {                                  // нет look-behind — ищем как есть
            return new RegExp(core, "g");
        }
    }

    /** Скомпилированные правила языка: длинные фразы применяются первыми. */
    var COMPILED = {};
    function rules(l) {
        if (COMPILED[l]) return COMPILED[l];
        var out = [];
        var table = PHRASES[l] || {};
        Object.keys(table).sort(function (a, b) { return b.length - a.length; })
            .forEach(function (ru) {
                out.push([phraseRe(ru), table[ru]]);
            });
        COMPILED[l] = out;
        return out;
    }

    /** Перевести одну строку: фразы, затем хвосты с числами. */
    function phrase(text, l) {
        l = l || current;
        if (l === "ru" || !text || !CYR.test(text)) return text;
        // Разметку пишут с переносами строк: в абзаце между словами стоит
        // «\n   », а в словаре — обычный пробел. Браузер всё равно сожмёт
        // их в один пробел, поэтому сравниваем и отдаём уже сжатый текст.
        var out = String(text).replace(/\s+/g, " ");
        var list = rules(l);
        for (var i = 0; i < list.length; i++) {
            out = out.replace(list[i][0], list[i][1]);
        }
        for (var j = 0; j < NUM_TAILS.length; j++) {
            out = out.replace(NUM_TAILS[j][0], function (m, n) {
                return NUM_TAILS[j][1](n, l);
            });
        }
        return out;
    }

    /** Полностью ли переводится русский текст (для тестов на полноту):
        после подстановки фраз и хвостов с числами кириллицы остаться не должно. */
    function hasPhrase(text, l) {
        var out = phrase(text, l || current);
        return !CYR.test(out);
    }

    //: «select» не глушим целиком: подписи в списках (срок удаления поста,
    //: частота сводки) — обычный текст интерфейса. Пропускаем только
    //: переключатель языка: «Русский» человек должен узнать в любом переводе.
    var SKIP_TAGS = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, CODE: 1, PRE: 1, TEXTAREA: 1, INPUT: 1 };
    var LANG_SELECTOR = "select[data-lang-select],select#lang-select,select.lang-select";

    function skipped(node) {
        for (var n = node; n && n.nodeType === 1; n = n.parentNode) {
            if (SKIP_TAGS[n.tagName]) return true;
            if (n.tagName === "SELECT" && n.matches && n.matches(LANG_SELECTOR)) return true;
            if (n.hasAttribute && n.hasAttribute("data-i18n-skip")) return true;
            if (n.getAttribute && n.getAttribute("contenteditable") === "true") return true;
        }
        return false;
    }

    /** Текстовые узлы и подписи внутри поддерева — на выбранный язык. */
    function localizeTree(root) {
        if (!root) return;
        if (current === "ru" && !root.querySelectorAll) return;
        var doc = root.ownerDocument || (root.nodeType === 9 ? root : document);
        if (!doc || !doc.createTreeWalker) return;
        var walker = doc.createTreeWalker(root, 4 /* SHOW_TEXT */, null, false);
        var nodes = [];
        var node;
        while ((node = walker.nextNode())) nodes.push(node);
        for (var i = 0; i < nodes.length; i++) {
            var n = nodes[i];
            var src = n.nodeValue;
            if (!src || !CYR.test(src)) continue;
            if (skipped(n.parentNode)) continue;
            n.nodeValue = phrase(src, current);
        }
        // подписи и подсказки: placeholder / title / aria-label / alt
        var host = root.querySelectorAll ? root : null;
        if (!host) return;
        var attrs = ["placeholder", "title", "aria-label", "alt", "data-hint"];
        for (var a = 0; a < attrs.length; a++) {
            var list = host.querySelectorAll("[" + attrs[a] + "]");
            for (var k = 0; k < list.length; k++) {
                var el = list[k];
                if (skipped(el)) continue;
                var val = el.getAttribute(attrs[a]);
                if (val && CYR.test(val)) el.setAttribute(attrs[a], phrase(val, current));
            }
        }
    }

    /* Динамика: кабинет и админка перерисовывают доски сами (автообновление,
       ответы сервера). Следим за DOM и переводим только то, что появилось, —
       целиком страницу на каждом тике терминала перебирать незачем. */
    var observer = null;
    var pending = null;

    function watch() {
        if (typeof MutationObserver === "undefined" || !document.body) return;
        if (current === "ru") { stopWatch(); return; }
        if (observer) return;
        observer = new MutationObserver(function (records) {
            // копим узлы и разбираем их одним проходом кадра
            if (!pending) pending = [];
            for (var i = 0; i < records.length; i++) {
                var r = records[i];
                if (r.type === "characterData" && r.target && r.target.parentNode) {
                    pending.push(r.target.parentNode);
                }
                for (var j = 0; j < r.addedNodes.length; j++) {
                    var n = r.addedNodes[j];
                    pending.push(n.nodeType === 1 ? n : (n.parentNode || document.body));
                }
            }
            if (pending.length > 400) pending = [document.body];
            if (pending._planned) return;
            pending._planned = true;
            (global.requestAnimationFrame || function (f) { setTimeout(f, 16); })(function () {
                var list = pending || [];
                pending = null;
                var seen = [];
                for (var k = 0; k < list.length; k++) {
                    if (seen.indexOf(list[k]) >= 0) continue;
                    seen.push(list[k]);
                    // к узлу мог прийти перевод ещё до кадра — лишняя работа не страшна
                    localizeTree(list[k]);
                }
            });
        });
        observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    }

    function stopWatch() {
        if (!observer) return;
        observer.disconnect();
        observer = null;
        pending = null;
    }

    /* =====================================================================
     * Переключатель языка
     *
     * Разметку не дублируем: <select data-lang-select> наполняется отсюда,
     * а data-i18n-* на самой странице переводят её. Смена языка не
     * перезагружает страницу — обработчики данные перерисуют сами.
     * ===================================================================== */
    function fillSelect(sel) {
        if (!sel || !sel.options) return;
        sel.innerHTML = LANGS.map(function (l) {
            return '<option value="' + l.code + '">' + l.flag + " " + l.label + "</option>";
        }).join("");
        sel.value = current;
        sel.setAttribute("aria-label", "Language / Язык");
        sel.setAttribute("title", "Language / Язык");
    }

    function bindSelect(sel) {
        if (!sel || sel._i18nBound) return;
        sel._i18nBound = true;
        sel.addEventListener("change", function (e) { set(e.target.value); });
    }

    function wireSwitchers() {
        if (!document.querySelectorAll) return;
        var list = document.querySelectorAll("[data-lang-select]");
        for (var i = 0; i < list.length; i++) {
            fillSelect(list[i]);
            bindSelect(list[i]);
        }
    }

    /** Выбранный язык уходит и на сервер (cookie) — тогда SSR отдаёт
        страницу сразу на нём, без мигания подписей. */
    function remember(code) {
        try { localStorage.setItem(STORE_KEY, code); } catch (e) { /* ignore */ }
        try {
            document.cookie = COOKIE + "=" + encodeURIComponent(code) +
                ";path=/;max-age=31536000;samesite=lax";
        } catch (e) { /* ignore */ }
    }

    function emit() {
        for (var i = 0; i < listeners.length; i++) {
            try { listeners[i](current); } catch (e) { /* ignore */ }
        }
    }

    function init() {
        started = true;
        current = detect();
        if (document.documentElement) document.documentElement.lang = localeTag();
        wireSwitchers();
        apply(document);
        if (document.body && document.body.setAttribute) {
            document.body.setAttribute("data-lang", current);
        }
        watch();
        // терминал и лендинг дорисовывают данные сами: даём им ещё один проход,
        // когда страница уже показывается целиком
        if (global.addEventListener) {
            global.addEventListener("load", function () { apply(document); });
        }
        emit();
    }

    var started = false;

    function set(code) {
        if (!MESSAGES[code] || code === current) return;
        current = code;
        remember(code);
        if (document.documentElement) document.documentElement.lang = localeTag();
        if (document.body && document.body.setAttribute) {
            document.body.setAttribute("data-lang", current);
        }
        apply(document);
        var list = document.querySelectorAll("[data-lang-select]");
        for (var i = 0; i < list.length; i++) list[i].value = current;
        if (current === "ru") stopWatch(); else watch();
        emit();
    }

    function onChange(cb) { listeners.push(cb); }

    function number(n) {
        try { return new Intl.NumberFormat(localeTag()).format(Number(n) || 0); }
        catch (e) { return String(Number(n) || 0); }
    }
    function time(ts) {
        try { return new Date((Number(ts) || 0) * 1000).toLocaleTimeString(localeTag()); }
        catch (e) { return ""; }
    }
    function dateTime(ts) {
        try { return new Date((Number(ts) || 0) * 1000).toLocaleString(localeTag()); }
        catch (e) { return ""; }
    }

    /* Странице не нужно помнить про init(): словари подключаются обычным
       <script>, а язык применяем сразу после разбора разметки. */
    if (typeof document !== "undefined" && document.addEventListener) {
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", function () {
                if (!started) init();
            });
        } else if (!started) {
            init();
        }
    }

    global.LiqScopeI18n = {
        LANGS: LANGS,
        init: init, lang: lang, set: set, t: t, plural: plural,
        apply: apply, onChange: onChange,
        number: number, time: time, dateTime: dateTime, localeTag: localeTag, detect: detect,
        // живой текст: перевод по фразам (кабинет, сервисы, админка)
        phrase: phrase, localizeTree: localizeTree, hasPhrase: hasPhrase,
        // партнёрская ссылка Gate и расстановка её в разметке
        gateRef: gateRef, applyGateRefs: applyGateRefs,
        fillSelect: fillSelect, bindSelect: bindSelect,
        messages: function () { return MESSAGES; },
        hreflang: function () {
            var one = LANGS.filter(function (l) { return l.code === current; })[0];
            return (one && one.hreflang) || current;
        },
    };
})(typeof window !== "undefined" ? window : this);
