"""Двуязычный бот: русский и английский без второго набора экранов.

Бот показывается одним и тем же кодом, а язык выбирает пользователь кнопкой
«🌐 RU/ENG»: выбор лежит в аккаунте (users.language) и переживает рестарт
сервиса. Переводится ВСЁ, что человек видит в боте — меню, экраны, сигналы
алертов, отчёты, подписи кнопок, — а не только заголовки: иначе русские слова
вылезали бы в английском интерфейсе.

Как это устроено
----------------
Русские подписи остаются в коде как есть (их просто править), а перед
отправкой в Telegram текст переводится таблицей соответствий ``RU_EN``.
Таблица — про фрагменты, а не про ключи: экраны бота собраны из кусков
(f-строки с числами и ссылками), поэтому переводим самые длинные известные
фрагменты за один проход. Замена идёт по одной копии текста слева направо,
поэтому переведённый кусок не может быть переведён второй раз.

Фрагмент, начинающийся или заканчивающийся русской буквой, ищется как целое
слово: иначе «бот» заменился бы внутри «работает», а «да» — внутри «ударов».

Числовые хвосты («5 мин», «3 ч», «30д») переводит не таблица, а правила с
оглядкой на цифру перед ними — см. ``RULES`` и ``NUM_TAILS``.

Список того, что обязано переводиться, не выдуман: тест
``tests/test_bot_i18n.py`` сам собирает русские подписи из tg_bot.py, alerts.py
и correlations.py (кроме докстрингов и логов) и требует, чтобы после перевода
в них не осталось кириллицы. Забытая фраза валит тест — язык не разъедется
со временем.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

#: языки бота: ключ → флажок, название на своём языке и короткая подпись
LANGS: Dict[str, Dict[str, str]] = {
    "ru": {"flag": "🇷🇺", "label": "Русский", "short": "RU"},
    "en": {"flag": "🇬🇧", "label": "English", "short": "ENG"},
}
DEFAULT_LANG = "ru"
#: подпись кнопки переключения языка: видно оба языка сразу
SWITCH_TEXT = "🌐 RU / ENG"


def normalize_lang(value: Any) -> str:
    """«en-US», «EN», «en» → «en»; всё остальное → «ru».

    Бот двуязычный: и русский, и любой третий язык (например, украинский код
    Telegram) показываем по-русски — русского текста в таблице больше, и
    подставлять вместо него английский было бы внезапно.
    """
    text = str(value or "").strip().lower()
    return "en" if text.startswith("en") else "ru"


def other_lang(lang: str) -> str:
    return "ru" if normalize_lang(lang) == "en" else "en"


def lang_label(lang: str) -> str:
    """«🇬🇧 English» — так язык выглядит в кнопках и в подтверждении."""
    info = LANGS[normalize_lang(lang)]
    return f"{info['flag']} {info['label']}"


# ---------------------------------------------------------------------------
#  Таблица соответствий: русский фрагмент → английский
#
#  Порядок не важен: перевод выбирается по длине фрагмента (самый длинный
#  побеждает), поэтому короткая подпись не портит длинный экран.
# ---------------------------------------------------------------------------
RU_EN: Dict[str, str] = {
    # --- панель внизу и кнопки -------------------------------------------
    "☰ Меню": "☰ Menu",
    "☰ меню": "☰ menu",
    "☰ Меню — кнопки разделов вместо ручного ввода":
        "☰ Menu — section buttons instead of typing commands",
    "👤 Кабинет": "👤 Account",
    "⚡ Терминал": "⚡ Terminal",
    "📊 Статистика": "📊 Stats",
    "🩺 Биржи": "🩺 Exchanges",
    "🛠 Сервисы": "🛠 Services",
    "📰 Лента": "📰 Feed",
    "🔔 Алерты": "🔔 Alerts",
    "📣 Канал": "📣 Channel",
    "📣 Каналы": "📣 Channels",
    "★ Админка": "★ Admin",
    "✉️ Подтвердить почту": "✉️ Verify email",
    "✅ Я подтвердил почту": "✅ I verified my email",
    "✅ Я подписался": "✅ I subscribed",
    "← В меню": "← Back to menu",
    "← Назад": "← Back",
    "← К алертам": "← Back to alerts",
    "← К шаблонам": "← Back to templates",
    "✖️ Отмена": "✖️ Cancel",
    "➕ Фото": "➕ Photo",
    "➕ Шапка": "➕ Header",
    "🎨 Шаблоны": "🎨 Templates",
    "🕒 Частота": "🕒 Frequency",
    "🕒 Частота сводки": "🕒 Digest frequency",
    "🗞 Дайджест за сутки": "🗞 Daily digest",
    "🔄 Перегенерировать": "🔄 Regenerate",
    "🔄 Пересчитать": "🔄 Recalculate",
    "🔄 Поменять местами": "🔄 Swap",
    "🔄 Проверить": "🔄 Check",
    "✅ Опубликовать": "✅ Publish",
    "🗑 Удалить фото": "🗑 Delete photo",
    "🤖 Проверить ИИ": "🤖 Test AI",
    "⚡ Открыть терминал": "⚡ Open terminal",
    "💠 Торговать на Gate": "💠 Trade on Gate",
    "💠 Торговать на Gate — скидка на комиссию":
        "💠 Trade on Gate — commission discount",
    "📣 Рассылка": "📣 Broadcast",
    "📈 Визиты": "📈 Visits",
    "👥 Пользователи": "👥 Users",
    "🩺 Здоровье": "🩺 Health",
    "🚀 Пампы": "🚀 Pumps",
    "🩸 Дампы": "🩸 Dumps",
    "📦 Объём": "📦 Volume",
    "🌐 Тепловая карта": "🌐 Heatmap",
    "🤖 Бот": "🤖 Bot",
    "🔔 Сигнал ВКЛ": "🔔 Alert ON",
    "🔕 Сигнал выкл": "🔕 Alert off",
    "🔔 Следить ВКЛ": "🔔 Watching ON",
    "🔕 Следить выкл": "🔕 Watching off",
    "⚖️ Оба": "⚖️ Both",
    "📰 Сводка в канал": "📰 Digest to channel",

    # --- переключатель языка ---------------------------------------------
    "<b>🌐 Язык бота</b>": "<b>🌐 Bot language</b>",
    "Сейчас выбран": "Currently selected",
    "Язык переключён: всё в боте — меню, сигналы и сводки — теперь на этом языке.":
        "Language switched: menus, alerts and digests in this bot now use this language.",

    # --- старт, меню, справка --------------------------------------------
    "Это бот <b>LiqScope</b> — живой терминал ликвидаций крипто-фьючерсов.\nЗдесь тот же кабинет, что и на сайте: статистика рынка, биржи, сервисы.":
        "This is the <b>LiqScope</b> bot — a live crypto-futures liquidation terminal.\nHere you get the same account as on the site: market stats, exchanges, services.",
    "<b>LiqScope</b>\nПривет, ": "<b>LiqScope</b>\nHi, ",
    "Привет, ": "Hi, ",
    "<b>Команды LiqScope</b>": "<b>LiqScope commands</b>",
    "<b>☰ Меню LiqScope</b>": "<b>☰ LiqScope menu</b>",
    "Всё то же, что в панели внизу, — кнопками. Ничего набирать руками не нужно.":
        "Everything from the bottom panel, as buttons. No need to type anything.",
    "/start — эта панель заново, /help — полный список команд.":
        "/start — this panel again, /help — the full command list.",
    "!\nВыберите раздел — кнопки внизу экрана.\n":
        "!\nPick a section — the buttons are at the bottom of the screen.\n",
    "/start — регистрация / вход на сайт": "/start — sign up / log into the site",
    "/cabinet — профиль": "/cabinet — profile",
    "/terminal — ссылка на терминал": "/terminal — link to the terminal",
    "/stats — ликвидации 24ч": "/stats — liquidations, 24h",
    "/status — какие биржи в эфире": "/status — which exchanges are live",
    "/liq — последние события": "/liq — latest events",
    "/services — сервисы кабинета": "/services — account services",
    "/correlations — корреляции валют": "/correlations — coin correlations",
    "/pumps — сторож монет: пампы и дампы": "/pumps — coin watcher: pumps and dumps",
    "/alerts — алерты по объёму": "/alerts — volume alerts",
    "/digest — сводка ликвидаций в канал": "/digest — liquidation digest to the channel",
    "/mail — привязать и подтвердить почту (основной вход на сайт)":
        "/mail — link and verify email (the main way into the site)",
    "/broadcast текст — рассылка всем": "/broadcast text — message everyone",
    "/bot — состояние опроса бота": "/bot — bot polling status",
    "/lang — язык бота: RU / ENG": "/lang — bot language: RU / ENG",
    "меню внизу экрана · язык кнопкой RU/ENG":
        "menu at the bottom · switch the language with the RU/ENG button",
    "<b>Выберите канал</b>": "<b>Pick a channel</b>",
    "Чтобы пользоваться ботом, подпишитесь на любой из двух каналов —":
        "To use the bot, subscribe to either of the two channels —",
    "содержание одно и то же, отличается язык:":
        "the content is the same, only the language differs:",
    "Подписки на любой из них достаточно — бот откроется полностью.":
        "A subscription to either one is enough — the bot unlocks fully.",
    "\n\nПодписки на любой из них достаточно.":
        "\n\nA subscription to either one is enough.",
    "Telegram ещё не видит подписку. Откройте любой из каналов (LiqScopeRUS или LiqScopeEng), затем нажмите «Я подписался» ещё раз.":
        "Telegram does not see the subscription yet. Open either channel (LiqScopeRUS or LiqScopeEng), then press «I subscribed» again.",
    "Недостаточно прав.": "Not enough permissions.",
    "Доступ закрыт.": "Access denied.",
    "Доступ закрыт": "Access denied",
    "Не понял.": "Did not get that.",
    "Не понял. Нажмите кнопку или /help.":
        "Did not get that. Press a button or /help.",
    "Отмена.": "Cancelled.",
    ".\nВыберите язык — меню, сигналы и сводки будут на нём.":
        ".\nPick a language — menus, alerts and digests will use it.",
    "Выберите язык — меню, сигналы и сводки будут на нём.":
        "Pick a language — menus, alerts and digests will use it.",

    # --- сводка в канал и шаблоны ----------------------------------------
    "<b>🤖 Состояние бота</b>": "<b>🤖 Bot status</b>",
    "<b>Шаблоны сводки</b>": "<b>Digest templates</b>",
    "<b>🕒 Частота сводки в канал</b>\nСейчас: ":
        "<b>🕒 Channel digest frequency</b>\nCurrent: ",
    "\n\nПост выходит раз в N часов, окно поста — те же N часов, а блок анализа внутри поста — четверть окна:\n• раз в 4 ч → разбор по часу;\n• раз в 2 ч → по 30 минут;\n• раз в 1 ч → по 15 минут.\n\nВыберите частоту (":
        "\n\nThe post goes out every N hours, the post window is those N hours, and the analysis block inside the post is a quarter of the window:\n• every 4 h → hourly breakdown;\n• every 2 h → 30-minute breakdown;\n• every 1 h → 15-minute breakdown.\n\nPick the frequency (",
    " ч) — применяется сразу, перезапуск не нужен.\nТекущий блок анализа: <b>":
        " h) — applied immediately, no restart needed.\nCurrent analysis block: <b>",
    "Раз в ": "Every ",
    "раз в ": "every ",
    "Пришлите текст шапки следующим сообщением.":
        "Send the header text in your next message.",
    "Пришлите текст шапки следующим сообщением.\nМожно {h} — подставится число часов.\n/cancel — отмена.":
        "Send the header text in the next message.\nYou can use {h} — the number of hours is inserted.\n/cancel — cancel.",
    "Нужен текст шапки. /cancel — отмена.":
        "The header text is needed. /cancel — cancel.",
    "Шапка добавлена.": "Header added.",
    "Шапки: ": "Headers: ",
    "Шапки пусты — в постах дефолтные из кода.":
        "No custom headers — posts use the built-in defaults.",
    "Какую шапку убрать?": "Which header to remove?",
    "🗑 Удалить шапку": "🗑 Delete header",
    "Какое фото убрать?": "Which photo to remove?",
    "Фото добавлено.\n\n": "Photo added.\n\n",
    "Пришлите картинку jpg/png (как фото или файл). /cancel — отмена.":
        "Send a jpg/png image (as a photo or a file). /cancel — cancel.",
    "Пришлите картинку jpg/png (как фото или файл).\n/cancel — отмена.":
        "Send a jpg/png image (as a photo or a file).\n/cancel — cancel.",
    "Обложка поста — как уйдёт в канал":
        "Post cover — exactly as it goes to the channel",
    "Черновик отменён, в канал ничего не ушло.":
        "Draft cancelled, nothing was sent to the channel.",
    "Черновик отменён, в каналы ничего не ушло.":
        "Draft cancelled, nothing was sent to the channels.",
    "Сводка ушла в канал.\n": "Digest sent to the channel.\n",
    "Дневной дайджест ушёл в каналы.\n": "Daily digest sent to the channels.\n",
    "<b>Не удалось отправить сводку</b>\n": "<b>Could not send the digest</b>\n",
    "<b>Не удалось отправить дневной дайджест</b>\n":
        "<b>Could not send the daily digest</b>\n",
    "Не вышло: ": "Failed: ",
    "Готово: ": "Done: ",
    "Готово: доставлено <b>": "Done: delivered to <b>",
    "⚠️ Не сохранил частоту: ": "⚠️ Could not save the frequency: ",
    "Нужно число минут, например 5.": "A number of minutes is needed, e.g. 5.",
    "Нужна сумма в долларах, например 250000 или 250k.":
        "A dollar amount is needed, e.g. 250000 or 250k.",
    "Пришлите сумму в $ — 250000 или 250k.\n/cancel — отмена.":
        "Send the amount in $ — 250000 or 250k.\n/cancel — cancel.",
    "Собираю новый дайджест за сутки…": "Building a new daily digest…",
    "Готовлю новый вариант…": "Preparing a new variant…",
    "Собираю новый вариант…": "Preparing a new variant…",
    "Рассылаю…": "Broadcasting…",
    "Пришлите текст рассылки следующим сообщением.\n/cancel — отмена.":
        "Send the broadcast text in your next message.\n/cancel — cancel.",
    "Черновик уже неактуален — нажмите «Сводка в канал» ещё раз.":
        "The draft is no longer valid — press «Digest to channel» again.",
    "Черновик уже неактуален — нажмите «🗞 Дайджест за сутки» ещё раз.":
        "The draft is no longer valid — press «🗞 Daily digest» again.",
    "окно ": "window ",
    "Черновик дневного дайджеста — выше в чате. Проверьте текст и нажмите «✅ Опубликовать».":
        "The daily digest draft is above in the chat. Check the text and press «✅ Publish».",
    "<b>Черновик сводки</b>\n<i>": "<b>Digest draft</b>\n<i>",
    "<b>Черновик дневного дайджеста</b> · ": "<b>Daily digest draft</b> · ",
    "</i>\nВ каналы уйдёт только после «Опубликовать».":
        "</i>\nIt goes to the channels only after «Publish».",
    "\nЭто суточный выпуск (не сводка за окно поста). В каналы он уйдёт только после «Опубликовать».":
        "\nThis is the daily edition (not the post-window digest). It goes to the channels only after «Publish».",
    "\n\nПрошлые выпуски — на сайте в разделе «Дайджест».":
        "\n\nPast editions are on the site under «Digest».",
    "<b>📣 Каналы бота</b>\nРусская сводка — в русский канал, английская — в английский.\n\n":
        "<b>📣 Bot channels</b>\nThe Russian digest goes to the Russian channel, the English one to the English channel.\n\n",
    "<b>📣 Каналы LiqScope</b>\nСводки ликвидаций, OI и CVD раз в ":
        "<b>📣 LiqScope channels</b>\nLiquidation, OI and CVD digests every ",
    " ч — тот же разбор, что в боте.\nСодержание одинаковое, отличается язык: русский и английский.\n":
        " h — the same recap as in the bot.\nThe content is identical, only the language differs: Russian and English.\n",
    " ч туда уходит разбор рынка: кто кого вынес, на каких биржах, что с открытым интересом.":
        " h the market recap goes there: who got liquidated, on which exchanges, what OI did.",
    " В канале у бота должно быть право «Публикация сообщений» (и «Прикрепление файлов», если шлём картинку).":
        " The bot must have the «Post messages» right in the channel (and «Attach files» when an image is sent).",
    "Бот ещё не знает id канала (инвайт-ссылки недостаточно). Перешлите сюда любой пост из канала — так он запомнит адрес. В правах админа включите «Публикация сообщений».":
        "The bot does not know the channel id yet (an invite link is not enough). Forward any post from the channel here — that is how it remembers the address. Enable the «Post messages» right for the admin.",
    "Перешлите боту любой пост из канала, чтобы привязать id.":
        "Forward any post from the channel to the bot to link its id.",
    "каналы были перепутаны: поменяли местами": "the channels were swapped: fixed",
    "русская роль стоит на английском канале":
        "the Russian role points at the English channel",
    "Привязывать каналы может только администратор.":
        "Only an administrator can link channels.",
    "Посты крутят их по очереди. В шапке можно {h} — это часы окна.":
        "Posts rotate through them. The header may contain {h} — the window hours.",
    "включён — черновик приходит сюда": "on — the draft arrives here",
    "выключен — пост уходит сразу в канал": "off — the post goes straight to the channel",
    "Контроль публикации включён, но у бота нет админа с Telegram. Выключите контроль в «Шаблоны канала» или привяжите Telegram админу":
        "Publishing control is on, but the bot has no admin with Telegram. Turn the control off in «Channel templates» or link Telegram to an admin",
    "🧪 Контроль постов: ": "🧪 Post control: ",
    "🧪 Контроль: ": "🧪 Control: ",

    # --- кабинет, почта, вход --------------------------------------------
    "<b>✉️ Подтверждение почты</b>\n": "<b>✉️ Email verification</b>\n",
    "<b>👤 Кабинет</b>": "<b>👤 Account</b>",
    "Почта — основной вход на сайт: по ней приходит ссылка для входа и сброс пароля.":
        "Email is the main way into the site: it delivers the login link and password reset.",
    "Сейчас почта к аккаунту не привязана.":
        "No email is linked to the account right now.",
    "Почта ещё не привязана.": "Email is not linked yet.",
    "Почта: <b>не привязана</b> — входить на сайт нечем, кроме Telegram":
        "Email: <b>not linked</b> — nothing but Telegram to log into the site with",
    "Пришлите адрес следующим сообщением — отправлю письмо со ссылкой. /cancel — отмена.":
        "Send the address in your next message — I will mail you a link. /cancel — cancel.",
    "Пришлите адрес почты следующим сообщением.":
        "Send your email address in the next message.",
    "Пришлите адрес почты следующим сообщением.\nНа него уйдёт письмо со ссылкой — по ней почта привяжется к этому аккаунту, и вы сможете входить на сайт.\n\n/cancel — отмена.":
        "Send your email address in the next message.\nA link will arrive at it — that links the email to this account and lets you log into the site.\n\n/cancel — cancel.",
    "Это не похоже на адрес. Пришлите почту ещё раз, например <code>name@mail.ru</code>.\n/cancel — отмена.":
        "That does not look like an address. Send the email once more, e.g. <code>name@mail.ru</code>.\n/cancel — cancel.",
    "Не получилось привязать адрес — попробуйте позже.":
        "Could not link the address — try again later.",
    "Почта не ушла: отправка писем на сервере не настроена.":
        "The email did not go out: mail sending is not configured on the server.",
    "Почта не ушла: отправка писем на сервере не настроена. Адрес сохранил, но подтвердить его пока нельзя.":
        "The email did not go out: mail sending is not configured on the server. I kept the address, but it cannot be verified yet.",
    "Письмо уже отправил — подождите ": "The email is already sent — wait ",
    "Письмо ушло на <code>": "The email went to <code>",
    "Вход подтверждён, ": "Login confirmed, ",
    "Код входа недействителен или устарел. Нажмите «Войти» на сайте ещё раз.":
        "The login code is invalid or expired. Press «Log in» on the site again.",
    "Ссылка привязки устарела. Нажмите «Привязать Telegram» в кабинете ещё раз.":
        "The link request expired. Press «Link Telegram» in your account again.",
    "Не получилось привязать Telegram. Попробуйте ещё раз из кабинета.":
        "Could not link Telegram. Try again from your account page.",
    "Не помню, из какого канала был пост. Перешлите его ещё раз.":
        "I do not know which channel that post came from. Forward it again.",
    "Пришлите тикер, например BTC или ETHUSDT.\n/cancel — отмена.":
        "Send a ticker, e.g. BTC or ETHUSDT.\n/cancel — cancel.",
    "Какую монету смотреть?": "Which coin to watch?",
    "Монета: ": "Coin: ",
    "Сколько минут в окне? Число, например 7.\n/cancel — отмена.":
        "How many minutes in the window? A number, e.g. 7.\n/cancel — cancel.",
    "Мин. удар": "Minimum hit",
    "Мин. удар какой метрики?": "Minimum hit of which metric?",
    "Порог": "Threshold",
    "Порог какой метрики?": "Threshold of which metric?",
    "Окно": "Window",
    "Монета": "Coin",
    "Окно агрегации:": "Aggregation window:",
    "Окно: ": "Window: ",
    "\nПочта аккаунта: <code>": "\nAccount email: <code>",
    "Почта: <code>": "Email: <code>",
    "Почта <code>": "Email <code>",
    "Сейчас привязана <code>": "Currently linked <code>",
    "с и проверьте ящик (и папку «Спам»).": "s and check your inbox (and the Spam folder).",
    "</code> уже подтверждена ✅": "</code> is already verified ✅",
    "</code> уже подтверждена ✅\nОна работает и на сайте, и здесь.":
        "</code> is already verified ✅\nIt works on the site and here.",
    "</code> подтверждена ✅\nТеперь можно входить на сайт и этим адресом, и Telegram.":
        "</code> is verified ✅\nNow you can log into the site with this address or with Telegram.",
    "</code>, но она <b>не подтверждена</b> — вход на сайт закрыт.":
        "</code>, but it is <b>not verified</b> — logins to the site are closed.",
    "</code> пока не подтверждена.\nОткройте письмо от LiqScope и нажмите в нём кнопку — ссылка живёт 24 часа.":
        "</code> is not verified yet.\nOpen the LiqScope email and press the button in it — the link lives 24 hours.",
    "Пришлите адрес ещё раз, если нужно письмо с новой ссылкой.":
        "Send the address again if you need a new link.",
    "</code> отправлено письмо с подтверждением.\nНажмите в нём «Привязать Telegram» — и этот Telegram станет вашим входом в кабинет на сайте.\nСсылка живёт 2 часа.":
        "</code>: a confirmation email has been sent.\nPress «Link Telegram» in it — and this Telegram becomes your way into the account on the site.\nThe link lives 2 hours.",
    "</code>.\nОткройте ссылку из письма — и почта станет входом на сайт.\nПока адрес не подтверждён, вход на сайте закрыт.":
        "</code>.\nOpen the link from the email — and the email becomes your way into the site.\nUntil the address is verified, logins to the site are closed.",
    "\nТеперь сигналы алертов придут сюда, а вход в кабинет — по почте или этим же Telegram.\n🌍 ":
        "\nNow alert signals will arrive here, and you can log into the account with the email or this same Telegram.\n🌍 ",
    "\nВесь профиль из Telegram перенесён в этот аккаунт — история алертов и подписки на месте.":
        "\nYour whole Telegram profile moved into this account — alert history and subscriptions are intact.",
    "✅ Telegram привязан к аккаунту LiqScope, ":
        "✅ Telegram is linked to the LiqScope account, ",
    "Этот Telegram уже привязан к аккаунту с почтой — сначала отвяжите его в кабинете того аккаунта.":
        "This Telegram is already linked to an account with an email — unlink it in that account first.",
    ".\nВернитесь во вкладку браузера — кабинет откроется сам.\n🌍 ":
        ".\nReturn to the browser tab — the account will open by itself.\n🌍 ",
    "\n\nКнопка ниже откроет его сразу, без звука.":
        "\n\nThe button below opens it right away, silently.",

    # --- сервисы, алерты, корреляции, сторож ------------------------------
    "<b>Сервисы кабинета</b>": "<b>Account services</b>",
    "Те же, что на сайте: 🔔 алерты, 🔗 корреляции валют и 👁 сторож монет уже работают.":
        "The same as on the site: 🔔 alerts, 🔗 coin correlations and 👁 the coin watcher are already running.",
    "\nРаботающие сервисы открывают свои настройки, остальные — лист ожидания, пока «скоро».":
        "\nWorking services open their settings, the rest are a waiting list while marked «soon».",
    "Сервисы: ": "Services: ",
    "\nКнопки ниже — метрика, монета, окно, порог. Сигнал приходит отдельным сообщением.":
        "\nThe buttons below set metric, coin, window, threshold. Signals arrive as separate messages.",
    "Кнопки ниже — режим, порог, период и число свечей. Сигналы приходят отдельными сообщениями со ссылкой на Gate.":
        "The buttons below set the mode, threshold, period and number of candles. Signals arrive as separate messages with a Gate link.",
    "<b>👁 Сторож монет: пампы и дампы</b>": "<b>👁 Coin watcher: pumps and dumps</b>",
    "<b>🔗 Корреляции валют</b> · ": "<b>🔗 Coin correlations</b> · ",
    "<b>🔗 Корреляции валют</b>\nИстория ещё собирается: сервис считает связи монет по часовым свёрткам ликвидаций, объёма, CVD и OI. Загляните позже — или посмотрите тепловую карту на сайте.":
        "<b>🔗 Coin correlations</b>\nHistory is still building up: the service computes coin links from hourly aggregates of liquidations, volume, CVD and OI. Look in later — or open the heatmap on the site.",
    "<b>📊 Рынок · 24ч</b>\n💥 Ликвидации: <code>":
        "<b>📊 Market · 24h</b>\n💥 Liquidations: <code>",
    "<b>Самые резкие движения сейчас</b>": "<b>Sharpest moves right now</b>",
    "<b>Уже за порогом</b>": "<b>Already past the threshold</b>",
    "Цены Gate ещё не подтянулись — сторож начнёт считать минутную историю с первого удачного опроса.":
        "Gate prices have not arrived yet — the watcher will start building minute history from the first successful poll.",
    "под наблюдением монет: <b>": "coins under watch: <b>",
    "пока не выбраны": "not chosen yet",
    "все монеты": "all coins",
    "смотрю: ": "watching: ",
    "монета: <b>": "coin: <b>",
    "сигнал: <b>": "signal: <b>",
    "порог <code>": "threshold <code>",
    "порог ": "threshold ",
    "окно: ": "window: ",
    "изменение OI ": "OI change ",
    "покупки": "buys",
    "продажи": "sells",
    "ничего": "nothing",
    "<b>Алерт · ": "<b>Alert · ",
    " <b>Алерт · ": " <b>Alert · ",
    " ударов · 🔴 ": " hits · 🔴 ",
    '"\">LiqScope</a> — живой поток ликвидаций"':
        '"\">LiqScope</a> — a live liquidation stream"',
    "</code>  за ": "</code>  over ",
    "ликвидации": "liquidations",
    "Алерты по объёму": "Volume alerts",
    "Корреляции валют": "Coin correlations",
    "Сторож монет": "Coin watcher",

    # --- статистика, биржи, здоровье --------------------------------------
    "<b>📰 Лента ликвидаций</b>\n💥 <code>": "<b>📰 Liquidation feed</b>\n💥 <code>",
    "</code> событий в памяти · касса ": "</code> events in memory · total ",
    "\n🔴 лонги ": "\n🔴 longs ",
    " · 🟢 шорты ": " · 🟢 shorts ",
    "\n⏱ время UTC · новые сверху\n": "\n⏱ UTC time · newest first\n",
    "\n… и ещё ": "\n… and ",
    "… и ещё ": "… and ",
    "<b>📰 Лента</b>\nПока нет событий в памяти — биржевые потоки прогреваются.":
        "<b>📰 Feed</b>\nNo events in memory yet — the exchange streams are warming up.",
    "<b>⚡ Терминал</b>\nЖивой поток ликвидаций с бирж: лента, свечи, кластеры.\n":
        "<b>⚡ Terminal</b>\nA live stream of exchange liquidations: feed, candles, clusters.\n",
    " — живой поток ликвидаций": " — a live liquidation stream",
    "<b>🩺 Биржи · эфир</b>": "<b>🩺 Exchanges · live</b>",
    "📡 В эфире <b>": "📡 Live <b>",
    "из ": "of ",
    "</b> из ": "</b> of ",
    "</b> · событий в памяти: ": "</b> · events in memory: ",
    "</b> · слушатель не запущен — поднимает сторож":
        "</b> · listener not running — the watchdog is starting it",
    "</code>\n⏱ За час: <code>": "</code>\n⏱ Last hour: <code>",
    "</code>\n🔴 Лонги <code>": "</code>\n🔴 Longs <code>",
    "</code>\n🏆 Лидеры: ": "</code>\n🏆 Leaders: ",
    "</code>\nОшибка записана в журнал сервиса.":
        "</code>\nThe error is written to the service log.",
    "Онлайн WS: ": "WS online: ",
    " уник.\n📡 Онлайн WS: ": " unique\n📡 WS online: ",
    "<b>Визиты</b>": "<b>Visits</b>",
    "Сегодня: ": "Today: ",
    "</b> · всего ": "</b> · total ",
    "👑 администратор": "👑 administrator",
    "👤 пользователь": "👤 user",
    "Роль: ": "Role: ",
    ")\n👁 Визиты сегодня: ": ")\n👁 Visits today: ",

    # --- состояние бота и админка ----------------------------------------
    "🤖 ИИ-шапка: включена · ": "🤖 AI header: on · ",
    "🤖 ИИ-шапка: выключена (нет ключей) — шапки из шаблонов":
        "🤖 AI header: off (no keys) — headers come from templates",
    "ИИ не настроен — шапка из шаблонов":
        "AI is not configured — the header comes from a template",
    "ИИ не настроен": "AI is not configured",
    "ИИ не ответил (": "AI did not answer (",
    "ИИ: ": "AI: ",
    ") — шапка из шаблонов": ") — the header comes from a template",
    "Шапка: <i>не получилось, будет из шаблонов</i>":
        "Header: <i>failed, the template one will be used</i>",
    "🤖 <b>Проверка ИИ</b>\n<i>": "🤖 <b>AI check</b>\n<i>",
    "🤖 Бот: токен не задан": "🤖 Bot: no token configured",
    "🤖 Бот: ⚠️ опрос не идёт (running=": "🤖 Bot: ⚠️ polling is not running (running=",
    ") — кнопки не отвечают, нужен перезапуск сервиса":
        ") — buttons do not respond, the service needs a restart",
    "♻️ <b>Опрос Telegram перезапущен</b>\n": "♻️ <b>Telegram polling restarted</b>\n",
    "⚠️ Экран не открылся: <code>": "⚠️ The screen did not open: <code>",
    "🚨 <b>Конфликт бота</b>\nЯ отправляю сигналы, но не вижу кнопки и команды: ":
        "🚨 <b>Bot conflict</b>\nI send signals but cannot see buttons or commands: ",
    "этот токен параллельно опрашивает другой процесс — старый бот или вторая копия сервиса":
        "another process is polling the same token — an old bot or a second copy of the service",
    ".\n\nПризнак призрачного процесса: сигналы приходят даже после того, как на сайте выключены ВСЕ уведомления — шлёт их старая копия бота, которая всё ещё висит на сервере.\nОдин токен может опрашивать только один процесс.\nНа сервере:\n• <code>ps aux | grep -E \"main\\.py|tg_bot|uvicorn|server:app\"</code> — ищем дубли (особенно запущенные давно);\n• <code>systemctl list-units | grep -i liq</code> — нет ли второго сервиса (старый liqscope из /root/LiqScope занимает тот же порт 8000);\n• убили лишнее — <code>systemctl restart licvid</code>, меню оживёт.\nНапоминаю каждые ~30 минут, пока конфликт не исчезнет.":
        ".\n\nA ghost process looks like this: signals keep coming even after ALL notifications are switched off on the site — they are sent by an old copy of the bot still running on the server.\nOnly one process may poll a token.\nOn the server:\n• <code>ps aux | grep -E \"main\\.py|tg_bot|uvicorn|server:app\"</code> — look for duplicates (especially long-running ones);\n• <code>systemctl list-units | grep -i liq</code> — is there a second service (the old liqscope from /root/LiqScope takes the same port 8000);\n• killed the extra one — <code>systemctl restart licvid</code>, the menu comes back.\nI remind you every ~30 minutes until the conflict is gone.",
    ".\nКнопки и команды снова должны отвечать. Если это повторяется, смотрите журнал: <code>journalctl -u licvid -n 200</code>":
        ".\nButtons and commands should respond again. If it keeps happening, check the log: <code>journalctl -u licvid -n 200</code>",
    "<b>Админ</b>": "<b>Admin</b>",
    "<b>Пользователи</b> · всего ": "<b>Users</b> · total ",
    "· новых ": "· new ",
    "(за сутки ": "(over 24h ",
    " · за 24ч ": " · over 24h ",
    "· за 24ч ": "· over 24h ",
    ", новых ": ", new ",
    ", ошибок ": ", errors ",
    "· оборот ": "· turnover ",
    "апдейт ": "update ",
    "опрос ок (": "polling ok (",
    "опрос: ": "polling: ",
    "⚠️ опрос завис ": "⚠️ polling stuck ",
    "задача опроса остановилась (": "the polling task stopped (",
    "409: токен тянет кто-то ещё": "409: someone else is polling the token",
    "успешный getUpdates: ": "successful getUpdates: ",
    "· обработано апдейтов: ": "· updates processed: ",
    "· ожиданий ввода: ": "· pending inputs: ",
    "· чатов с меню: ": "· chats with the menu: ",
    "· попыток: ": "· attempts: ",
    "· завис ": "· stuck ",
    "· меню не внизу: ": "· menu not at the bottom: ",
    "· зрителей WS: ": "· WS viewers: ",
    "· задача опроса ": "· polling task ",
    "· 409-конфликт: ": "· 409 conflict: ",
    "· из переменной окружения": "· from the environment variable",
    "· выбрано в боте": "· selected in the bot",
    "· данные ": "· data ",
    "· открыть": "· open",
    "· скоро": "· soon",
    "(скоро)": "(soon)",
    "(подъёмов: ": "(restarts: ",
    "перезапусков сторожем ": "watchdog restarts ",
    "перезапусков сторожем: ": "watchdog restarts: ",
    ". Перезапусков сторожем: ": ". Watchdog restarts: ",
    "сбоев getUpdates подряд: ": "getUpdates failures in a row: ",
    "последний апдейт: ": "last update: ",
    "последняя: ": "latest: ",
    "последняя: ошибка — ": "latest: error — ",
    "от Telegram не было успешных getUpdates ": "no successful getUpdates from Telegram for ",
    "отказов Telegram (отправка/правка): ": "Telegram rejections (send/edit): ",
    "отказов Telegram ": "Telegram rejections ",
    "сейчас: ": "now: ",
    ", задача=": ", task=",
    "Канал <b>": "Channel <b>",
    "· за ": "· over ",

    # --- служебные слова и статусы ---------------------------------------
    "жива": "up",
    "мертва": "down",
    "идёт": "running",
    "НЕ идёт": "NOT running",
    "нет связи": "no connection",
    "список пуст": "the list is empty",
    "неизвестная ошибка": "unknown error",
    "да": "yes",
    "нет": "no",
    "вкл": "on",
    "выкл": "off",
    "бот": "bot",
    "друг": "other",
    "английский": "English",
    "русский": "Russian",
    "русс": "RU",
    "рус": "RU",
    "админка": "admin",
    "алерты по объёму": "volume alerts",
    "алерты": "alerts",
    "биржи": "exchanges",
    "все сервисы": "all services",
    "кабинет": "account",
    "канал": "channel",
    "каналы": "channels",
    "команды": "commands",
    "лента liq": "feed liq",
    "лента": "feed",
    "меню": "menu",
    "почта": "email",
    "подтвердить почту": "verify email",
    "сервисы": "services",
    "статистика": "stats",
    "терминал": "terminal",
    "свои минуты": "custom minutes",
    "своя монета": "custom coin",
    "своё число": "custom number",
    "памп": "pump",
    "дамп": "dump",
    "меню внизу экрана": "menu at the bottom of the screen",
    "открыть кабинет на сайте": "open the account on the site",
    "кабинет на сайте": "account on the site",
    "панель на сайте": "panel on the site",
    "посмотреть в терминале": "view in the terminal",
    "смотреть в терминале": "view in the terminal",
    "сервер ещё собирает источники": "the server is still collecting sources",
    "у бота включён вебхук": "the bot has a webhook enabled",
    "английский канал не привязан": "the English channel is not linked",
    "канал не привязан — перешлите боту пост из канала":
        "the channel is not linked — forward any post from it to the bot",
    "канал не привязан": "the channel is not linked",
    "каналы не привязаны": "channels are not linked",
    "дневной дайджест не подключён на сервере":
        "the daily digest is not wired up on the server",
    "дайджест не собрался: пустой ответ":
        "the digest was not built: empty response",
    "дайджест не собрался: ": "the digest was not built: ",
    "черновик не ушёл": "the draft did not go out",
    "Telegram отклонил пост": "Telegram rejected the post",

    # --- предупреждения ---------------------------------------------------
    "⚠️ Почта не подтверждена — вход на сайт закрыт, пока не перейдёте по ссылке из письма":
        "⚠️ Email is not verified — site logins stay closed until you follow the link from the letter",
    "\n\nЕсли бот уже админ — перешлите сюда любой пост из канала и попробуйте снова.":
        "\n\nIf the bot is already an admin — forward any post from the channel here and try again.",
    "\n\nЕсли бот уже админ — перешлите сюда любой пост из канала, затем снова /digest.":
        "\n\nIf the bot is already an admin — forward any post from the channel here, then run /digest again.",
    "\n\n⚠️ Оба языка указывают на один канал — перешлите пост из второго.":
        "\n\n⚠️ Both languages point to the same channel — forward a post from the second one.",
    "\n\n⚠️ Пока привязан один канал — сводка уходит только в него.\nДобавьте бота админом во второй канал и перешлите сюда его пост.":
        "\n\n⚠️ Only one channel is linked so far — the digest goes there only.\nAdd the bot as an admin to the second channel and forward its post here.",
    "\n\nЕсли адрес не тот — меню «📣 Каналы» → «🔄 Поменять местами», либо перешлите боту пост из нужного канала и выберите язык.":
        "\n\nIf the address is wrong — menu «📣 Channels» → «🔄 Swap», or forward a post from the right channel and pick the language.",
    "\n\nЧтобы сменить канал: перешлите боту любой пост из него и выберите язык.":
        "\n\nTo change the channel: forward any post from it to the bot and pick the language.",
}

# ---------------------------------------------------------------------------
#  Вторая порция: сигналы алертов и экраны корреляций (alerts.py,
#  correlations.py) — их тексты уходят в тот же чат, поэтому переводятся здесь.
# ---------------------------------------------------------------------------
RU_EN.update({
    # alerts.py
    "\u00a0": "\u00a0",
    "\u00b7": "·",
    # correlations.py
    "<b>🔗 Корреляции валют · окно ": "<b>🔗 Coin correlations · window ",
    "Шли вместе:": "Moved together:",
    "В противофазе:": "In opposite phase:",
    "Крупнейшие ликвидации окна:": "Biggest liquidations of the window:",
    "Карточки и тепловая карта": "Cards and the heatmap",
    "Точек по часам: ": "Hourly points: ",
    "· монет в расчёте: ": "· coins in the calculation: ",
    "Устойчивых связей пока нет: окно слишком тихое.":
        "No stable links yet: the window is too quiet.",
    "\n<b>Где выносило лонги:</b> ": "\n<b>Where longs got liquidated:</b> ",
    "\n<b>Где выносило шорты:</b> ": "\n<b>Where shorts got liquidated:</b> ",
    "<b>CVD на сторону покупателей:</b> ": "<b>CVD skewed to buyers:</b> ",
    "<b>CVD на сторону продавцов:</b> ": "<b>CVD skewed to sellers:</b> ",
    "<b>OI растёт:</b> ": "<b>OI rising:</b> ",
    "<b>OI падает:</b> ": "<b>OI falling:</b> ",
    " всего · лонги ": " total · longs ",
    " · шорты ": " · shorts ",
    "Ликвидации": "Liquidations",
    "Объём": "Volume",
    "сумма ликвидаций за час, $": "liquidation sum per hour, $",
    "оборот монеты за час, $": "coin turnover per hour, $",
    "перекос покупок и продаж за час, $": "buy/sell skew per hour, $",
    "изменение открытого интереса за час, $": "open interest change per hour, $",
    "</code>\n\nКуда слать сводки — на русском или на английском?\nПосты пойдут в оба канала: русский в русский, английский в английский.\nУ бота должно быть право «Публикация сообщений».":
        "</code>\n\nWhere should digests go — in Russian or English?\nPosts go to both channels: Russian to the Russian one, English to the English one.\nThe bot needs the «Post messages» right.",
    "\n🇷🇺 Канал: ": "\n🇷🇺 Channel: ",
    "\n🇬🇧 Канал: ": "\n🇬🇧 Channel: ",
    "\n🕒 Сводка: ": "\n🕒 Digest: ",
    "\n🩺 Биржи в эфире: ": "\n🩺 Exchanges live: ",
    " (не подтверждена)": " (not verified)",
    " · фото: ": " · photos: ",
    " уник.": " unique",
    " уникальных": " unique",
    "% за ": "% over ",
    " ч · анализ по ": " h · analysis by ",
    " ч · окно ": " h · window ",
    " окно ": " window ",
    "На <code>": "On <code>",
    "включён": "on",
    "выключен": "off",
    "</code> · мин. <code>": "</code> · min. <code>",
    " дн": " d",
    " тыс": " K",
    " млн": " M",
    " млрд": " B",
    "<b>Где выносило лонги:</b> ": "<b>Where longs got liquidated:</b> ",
    "<b>🔔 Алерты по объёму</b>": "<b>🔔 Volume alerts</b>",

    # --- названия и описания сервисов (лежат в базе, приходят как данные) --
    # Иконка и название склеиваются в кнопке (f"{icon} {title}"), поэтому
    # переводим и пару «иконка + название»: короткое «🔔 Алерты» перехватило бы
    # начало длинного названия и оставило «по объёму» необработанным.
    "🔔 Алерты по объёму": "🔔 Volume alerts",
    "🔗 Корреляции валют": "🔗 Coin correlations",
    "👁 Сторож монет": "👁 Coin watcher",
    "📰 Дневной дайджест": "📰 Daily digest",
    "Дневной дайджест": "Daily digest",
    "Ликвидации, CVD и OI: порог, окно, монета — сигнал в кабинет и в Telegram.":
        "Liquidations, CVD and OI: threshold, window, coin — a signal to your account and to Telegram.",
    "Какие монеты ходят вместе за час-неделю: ликвидации, объём, CVD и OI. Где выносило лонги, а где шорты.":
        "Which coins move together over an hour to a week: liquidations, volume, CVD and OI. Where longs and where shorts got liquidated.",
    "Пампы и дампы всех монет Gate: порог в %, период свечей и их число. Сигнал в Telegram со ссылкой на Gate.":
        "Pumps and dumps across all Gate coins: percentage threshold, candle period and count. The signal arrives in Telegram with a Gate link.",
    "Сводка рынка за сутки в кабинет и в Telegram.":
        "A 24-hour market recap to your account and to Telegram.",
})

# ---------------------------------------------------------------------------
#  Фрагменты, которые остаются русскими и в английском интерфейсе
#
#  Это не забытые переводы: подписи языков показываются каждый на своём языке —
#  иначе «Русский» в английском меню было бы странно искать.
#
#  Числовые хвосты («5 мин», «3 ч», «30д») переводит не таблица, а правило с
#  оглядкой на цифру перед ними (см. RULES): сами по себе они не переводятся,
#  поэтому тест считает их служебными и не требует перевода.
# ---------------------------------------------------------------------------
NUM_TAILS = ("мин", "мс", "ч", "д", "с", "м", "свеч", "событий",
             "с назад", "м назад", "ч назад")
KEEP_RU = ("🇷🇺 Русский", "🇬🇧 English")

# ---------------------------------------------------------------------------
#  Правила для чисел: «5 мин», «3 ч», «30д», «812 событий»
# ---------------------------------------------------------------------------
RULES: List[tuple] = [
    (re.compile(r"(?<=\d)\s?свеч\."), " candles"),
    # «5 уник.» — сокращение из экрана визитов; «5 уникальных» — то же слово
    # целиком. Точка обязательна: без неё правило съедало «уник» из
    # «уникальных» и получалось «uniqueальных».
    (re.compile(r"(?<=\d)\s?уник\.(?=\s|$)"), " unique"),
    (re.compile(r"(?<=\d)\s?уникальных\b"), " unique"),
    (re.compile(r"(?<=\d)\s?с назад"), "s ago"),
    (re.compile(r"(?<=\d)\s?м назад"), "m ago"),
    (re.compile(r"(?<=\d)\s?ч назад"), "h ago"),
    (re.compile(r"(?<=\d)\s?м\b"), " m"),
    (re.compile(r"(?<=\d)\s?событий\b"), " events"),
    (re.compile(r"(?<=\d)\s?ударов\b"), " hits"),
    (re.compile(r"(?<=\d)\s?мин\b"), " min"),
    (re.compile(r"(?<=\d)\s?мс\b"), " ms"),
    (re.compile(r"(?<=\d)\s?ч\b"), " h"),
    (re.compile(r"(?<=\d)\s?д\b"), " d"),
    (re.compile(r"(?<=\d)\s?с\b"), " s"),
    (re.compile(r"монет: (?=\d)"), "coins: "),
    (re.compile(r"пар: (?=\d)"), "pairs: "),
    (re.compile(r"сильных: (?=\d)"), "strong: "),
    (re.compile(r"точек в окне: (?=\d)"), "points in window: "),
    (re.compile(r"просмотров\b"), "views"),
    (re.compile(r"уникальных\b"), "unique"),
    (re.compile(r"зрителей\b"), "viewers"),
]

_CYR = re.compile(r"[А-Яа-яЁё]")
_LOOKUP: Optional[re.Pattern] = None
_ORDER: List[str] = []


def _alternatives() -> str:
    """Все фрагменты таблицы, самые длинные — первыми.

    Длинные идут первыми, поэтому «ИИ не настроен — шапка из шаблонов»
    побеждает короткое «ИИ не настроен», а фрагмент, начинающийся или
    заканчивающийся русской буквой, ищется как целое слово: иначе «бот»
    заменился бы внутри «работает», а «да» — внутри «ударов».
    """
    out = []
    for key in sorted(RU_EN, key=len, reverse=True):
        if len(key) < 2:
            continue
        left = r"(?<![А-Яа-яЁё])" if _CYR.match(key[0]) else ""
        right = r"(?![А-Яа-яЁё])" if _CYR.match(key[-1]) else ""
        out.append(left + re.escape(key) + right)
    return "|".join(out)


def _compile() -> Optional[re.Pattern]:
    global _LOOKUP, _ORDER
    if _LOOKUP is None:
        _ORDER = sorted(RU_EN, key=len, reverse=True)
        pattern = _alternatives()
        _LOOKUP = re.compile(pattern) if pattern else None
    return _LOOKUP


def refresh() -> None:
    """Сбросить собранный шаблон (нужно тестам и правкам таблицы на ходу)."""
    global _LOOKUP
    _LOOKUP = None


def translate(text: Any, lang: str = DEFAULT_LANG) -> Any:
    """Перевести сообщение на язык пользователя.

    Переводим только в английский: русский — исходный язык кода. Один проход
    по тексту, поэтому английские слова из таблицы не могут попасть под
    замену ещё раз.
    """
    if not isinstance(text, str) or not text:
        return text
    if normalize_lang(lang) != "en":
        return text
    # Названия языков переводу не подлежат: прячем их под метку, чтобы таблица
    # не тронула «Русский» в кнопке выбора языка. Метка — в области для
    # частного использования: цифр в ней нет, поэтому правила для чисел её
    # не путают с числом.
    kept: List[str] = []
    for keep in sorted(set(KEEP_RU), key=len, reverse=True):
        while keep in text:
            kept.append(keep)
            text = text.replace(keep, "\ue000" + chr(0xE001 + len(kept) - 1) + "\ue000", 1)
    pattern = _compile()
    if pattern is not None:
        text = pattern.sub(lambda m: RU_EN.get(m.group(0), m.group(0)), text)
    for rx, repl in RULES:
        text = rx.sub(repl, text)
    for i, keep in enumerate(kept):
        text = text.replace("\ue000" + chr(0xE001 + i) + "\ue000", keep)
    return text


def translate_markup(markup: Optional[dict], lang: str) -> Optional[dict]:
    """Подписи кнопок в клавиатуре — тем же переводом, что и текст."""
    if normalize_lang(lang) != "en" or not isinstance(markup, dict):
        return markup
    out = dict(markup)
    for key in ("keyboard", "inline_keyboard"):
        rows = out.get(key)
        if not isinstance(rows, list):
            continue
        new_rows = []
        for row in rows:
            if isinstance(row, list):
                new_row = []
                for btn in row:
                    if isinstance(btn, dict):
                        btn = dict(btn)
                        if "text" in btn:
                            btn["text"] = translate(btn["text"], lang)
                    new_row.append(btn)
                new_rows.append(new_row)
            else:
                new_rows.append(row)
        out[key] = new_rows
    if "input_field_placeholder" in out:
        out["input_field_placeholder"] = translate(out["input_field_placeholder"], lang)
    return out


#: методы Telegram, у которых есть текст, видимый человеку
TEXT_METHODS = {
    "sendMessage": ("text",),
    "editMessageText": ("text",),
    "sendPhoto": ("caption",),
    "editMessageCaption": ("caption",),
    "sendDocument": ("caption",),
    "answerCallbackQuery": ("text",),
    "sendMediaGroup": ("caption",),
    "answerWebAppQuery": ("text",),
}


def translate_payload(method: str, payload: Optional[dict],
                      lang: str) -> Optional[dict]:
    """Перевести исходящий запрос к Telegram целиком.

    Всё видимое человеку уходит через ``_call``: текст сообщения, подпись фото,
    всплывающая подсказка, подписи кнопок. Переводим здесь — и язык
    соблюдается в меню, в сигналах алертов и в отчётах, где бы они ни
    собирались: отдельные экраны нечего забывать.
    """
    if normalize_lang(lang) != "en" or not isinstance(payload, dict):
        return payload
    out = dict(payload)
    for field in TEXT_METHODS.get(method, ()):
        if field in out:
            out[field] = translate(out[field], lang)
    if isinstance(out.get("reply_markup"), dict):
        out["reply_markup"] = translate_markup(out["reply_markup"], lang)
    return out


def strings_missing_en(texts: List[str]) -> List[str]:
    """Какие русские подписи останутся русскими после перевода — для теста.

    Числовые хвосты (« мин», « ч») и названия языков — не пропуск: первые
    переводит правило по цифре перед ними, вторые и должны остаться на своём
    языке. Их из проверки убираем, всё остальное — нет.
    """
    out = []
    for text in texts:
        if text.strip().rstrip(".") in NUM_TAILS or text in KEEP_RU:
            continue
        translated = translate(text, "en")
        if _CYR.search(translated):
            out.append(text)
    return out
