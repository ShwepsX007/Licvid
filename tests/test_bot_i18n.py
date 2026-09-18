"""Двуязычный бот: кнопка «🌐 RU/ENG» и перевод всех подписей.

Проверяем три вещи:

    * кнопка языка стоит там, где была вторая кнопка алертов (алерты
      остались в «🛠 Сервисы»), и переключение сохраняется в аккаунт;
    * выбор языка не затирается языком клиента Telegram;
    * ни одна русская подпись бота, алертов и корреляций не остаётся
      русской в английском интерфейсе — список подписей собирается из
      исходников, поэтому забытая фраза сразу валит тест.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

# tg_bot тянет aiohttp только для HTTP: в офлайн-тесте подменяем модуль
if "aiohttp" not in sys.modules:
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        import types
        fake = types.ModuleType("aiohttp")

        class _TO:
            def __init__(self, total=None, **kw):
                self.total = total
                for k, v in kw.items():
                    setattr(self, k, v)

        class _CS:
            def __init__(self, *a, **k):
                pass

            async def close(self):
                return None

        class _TCP:
            def __init__(self, *a, **k):
                pass

        fake.ClientTimeout = _TO
        fake.ClientSession = _CS
        fake.TCPConnector = _TCP
        sys.modules["aiohttp"] = fake

import bot_i18n  # noqa: E402
from accounts import Store  # noqa: E402
from tg_bot import TelegramBot  # noqa: E402
from tools.bot_i18n_check import literals  # noqa: E402


def _texts(markup: dict, inline: bool = False) -> list:
    rows = (markup or {}).get("inline_keyboard" if inline else "keyboard") or []
    return [b.get("text") for row in rows for b in row]


def _datas(markup: dict) -> list:
    rows = (markup or {}).get("inline_keyboard") or []
    return [b.get("callback_data") for row in rows for b in row]


class TranslateTest(unittest.TestCase):
    def test_lang_normalization(self):
        self.assertEqual(bot_i18n.normalize_lang("en-US"), "en")
        self.assertEqual(bot_i18n.normalize_lang("EN"), "en")
        self.assertEqual(bot_i18n.normalize_lang("ru"), "ru")
        self.assertEqual(bot_i18n.normalize_lang("de"), "ru")
        self.assertEqual(bot_i18n.normalize_lang(None), "ru")
        self.assertEqual(bot_i18n.other_lang("en"), "ru")
        self.assertEqual(bot_i18n.lang_label("en"), "🇬🇧 English")

    def test_russian_stays_untouched(self):
        text = "Сводка ушла в канал.\n💥 812 событий"
        self.assertEqual(bot_i18n.translate(text, "ru"), text)

    def test_whole_screens_translate(self):
        text = ("<b>📰 Лента ликвидаций</b>\n💥 <code>5</code> событий в памяти · "
                "касса $1.20M\n🔴 лонги $900.0K · 🟢 шорты $300.0K")
        out = bot_i18n.translate(text, "en")
        self.assertIn("Liquidation feed", out)
        self.assertIn("events in memory", out)
        self.assertIn("longs", out)
        self.assertIn("shorts", out)
        self.assertFalse(bot_i18n._CYR.search(out))

    def test_words_are_not_replaced_inside_other_words(self):
        """«бот» не должен лезть в «работает», а «да» — в «ударов»."""
        for word in ("работает", "ударов", "победа", "выход", "задача"):
            self.assertEqual(bot_i18n.translate(word, "en"), word)

    def test_number_tails(self):
        self.assertEqual(bot_i18n.translate("5 мин", "en"), "5 min")
        self.assertEqual(bot_i18n.translate("3ч", "en"), "3 h")
        self.assertEqual(bot_i18n.translate("30д", "en"), "30 d")
        self.assertEqual(bot_i18n.translate("8ч назад", "en"), "8h ago")
        self.assertEqual(bot_i18n.translate("812 событий", "en"), "812 events")
        self.assertEqual(bot_i18n.translate("2.5 млн", "en"), "2.5 M")

    def test_language_labels_stay_native(self):
        """В кнопках выбора языка каждая подпись — на своём языке."""
        self.assertEqual(bot_i18n.translate("🇷🇺 Русский", "en"), "🇷🇺 Русский")
        self.assertEqual(bot_i18n.translate("🇬🇧 English", "en"), "🇬🇧 English")

    def test_translate_markup_and_payload(self):
        kb = {"keyboard": [[{"text": "👤 Кабинет"}, {"text": "🩺 Биржи"}]],
              "input_field_placeholder": "меню внизу экрана"}
        out = bot_i18n.translate_markup(kb, "en")
        self.assertEqual(_texts(out), ["👤 Account", "🩺 Exchanges"])
        self.assertEqual(out["input_field_placeholder"], "menu at the bottom of the screen")
        payload = {"chat_id": 1, "text": "Онлайн WS: 3", "reply_markup": kb}
        done = bot_i18n.translate_payload("sendMessage", payload, "en")
        self.assertEqual(done["text"], "WS online: 3")
        self.assertEqual(_texts(done["reply_markup"]), ["👤 Account", "🩺 Exchanges"])
        # русскому языку ничего не подменяем — объект остаётся тем же
        self.assertIs(bot_i18n.translate_payload("sendMessage", payload, "ru"), payload)

    def test_every_user_visible_string_translates(self):
        """Главный страж языка: ни одной русской подписи в английском боте."""
        for name in ("tg_bot.py", "alerts.py", "correlations.py"):
            path = os.path.join(HERE, name)
            if not os.path.exists(path):
                continue
            missing = bot_i18n.strings_missing_en(literals(path))
            self.assertEqual(missing, [], f"{name}: без перевода {missing}")


class LangButtonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"),
                           secret="s", admin_ids=[1001])
        self.bot = TelegramBot("0:x", self.store)
        self.user = self.store.upsert_telegram_user({
            "id": 2002, "username": "bob", "first_name": "Bob",
        })
        self.chat_id = 2002

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_panel_button_replaces_alerts(self):
        kb = self.bot._reply_kb(self.user)
        texts = _texts(kb)
        self.assertIn(bot_i18n.SWITCH_TEXT, texts)
        self.assertNotIn("🔔 Алерты", texts)
        # алерты по-прежнему открываются из сервисов
        self.assertIn("🛠 Сервисы", texts)
        svc = self.bot._services_kb(self.user)
        self.assertTrue(any((b.get("callback_data") or "").startswith("svc:alerts")
                            for row in svc["inline_keyboard"] for b in row))

    def test_commands_menu_button_switches_language(self):
        kb = self.bot._commands_kb(self.user)
        self.assertIn(bot_i18n.SWITCH_TEXT, _texts(kb, inline=True))
        self.assertIn("lang", _datas(kb))
        self.assertNotIn("🔔 Алерты", _texts(kb, inline=True))

    def test_panel_button_and_command_lead_to_language_screen(self):
        self.assertEqual(self.bot._reply_cmd(bot_i18n.SWITCH_TEXT), "lang")
        self.assertEqual(self.bot._reply_cmd("🌐 ru / eng"), "lang")
        text, kb = self.bot._screen(self.user, "lang")
        self.assertIn("Язык бота", text)
        self.assertEqual(_datas(kb), ["setlang:ru", "setlang:en", "nav:home"])

    def test_english_panel_labels_work_too(self):
        """После переключения на английский кнопки панели должны работать."""
        for label, want in (("👤 Account", "cabinet"), ("📊 Stats", "stats"),
                            ("🩺 Exchanges", "health"), ("🛠 Services", "services"),
                            ("📰 Feed", "liq"), ("🔔 Alerts", "al"),
                            ("📣 Channel", "channel"), ("★ Admin", "admin"),
                            ("✉️ Verify email", "mail")):
            self.assertEqual(self.bot._reply_cmd(label), want, label)

    def test_switch_saves_language_to_account(self):
        import asyncio
        asyncio.get_event_loop_policy().new_event_loop()
        calls = []

        async def fake_show(chat_id, text, markup=None, **kw):
            calls.append((chat_id, text, markup))
            return True

        self.bot.show_menu = fake_show
        asyncio.run(self.bot.set_lang(self.chat_id, self.user, "en"))
        self.assertEqual(self.bot.lang_of(self.chat_id), "en")
        fresh = self.store.get_user(self.user["id"])
        self.assertEqual(fresh["language"], "en")
        self.assertEqual(fresh["lang_manual"], 1)
        # подтверждение уходит на новом языке (клиент переводит на отправке)
        self.assertEqual(calls[-1][0], self.chat_id)

    def test_chosen_language_survives_telegram_locale(self):
        """Выбор в боте главнее языка клиента: upsert его не перебивает."""
        self.store.set_user_language(self.user["id"], "en")
        again = self.store.upsert_telegram_user({
            "id": 2002, "username": "bob", "first_name": "Bob",
            "language_code": "ru",
        })
        self.assertEqual(again["language"], "en")
        # а у тех, кто язык не выбирал, локаль клиента по-прежнему решает
        other = self.store.upsert_telegram_user({
            "id": 3003, "first_name": "Ann", "language_code": "en",
        })
        self.assertEqual(other["language"], "en")

    def test_alert_to_english_subscriber_goes_out_in_english(self):
        """Сигнал алерта переводится на язык подписчика, а не админа.

        Подписчик мог ни разу не написать боту после рестарта: язык ему в кэш
        кладёт прогрев из подписок (list_alert_subscribers отдаёт language).
        """
        import asyncio
        from alerts import format_alert_html
        self.store.set_user_language(self.user["id"], "en")
        self.bot.warm_langs([{"tg_id": 2002, "language": "en"}])
        self.assertEqual(self.bot.lang_of(2002), "en")
        hit = {"metric": "liq", "symbol": "BTC_USDT", "value": 5_000_000,
               "threshold": 1_000_000, "window_min": 5, "count": 12,
               "longs": 4_000_000, "shorts": 1_000_000}
        text = format_alert_html(hit, self.bot.site_url())
        seen = {}

        class _Sess:
            def post(self, url, json=None, timeout=None):
                seen.update(json or {})

                class _R:
                    async def json(self, content_type=None):
                        return {"ok": True, "result": {"message_id": 7}}

                    async def __aenter__(self):
                        return self

                    async def __aexit__(self, *a):
                        return False

                return _R()

        self.bot._session = _Sess()
        asyncio.run(self.bot.send(2002, text,
                                  markup=self.bot.site_link_kb("посмотреть в терминале")))
        self.bot._session = None
        self.assertIn("Alert", seen["text"])
        self.assertIn("threshold", seen["text"])
        self.assertFalse(bot_i18n._CYR.search(seen["text"]), seen["text"])

    def test_outgoing_payload_translated_but_channels_untouched(self):
        self.store.set_setting("channel_bindings",
                               '{"ru": "-100123", "en": "-100456"}')
        self.bot.remember_lang(self.chat_id, "en")
        self.bot.remember_lang("-100123", "en")     # канал как получатель
        seen = {}

        class _Sess:
            def post(self, url, json=None, timeout=None):
                seen["payload"] = json

                class _R:
                    async def json(self, content_type=None):
                        return {"ok": True, "result": {"message_id": 1}}

                    async def __aenter__(self):
                        return self

                    async def __aexit__(self, *a):
                        return False

                return _R()

        import asyncio
        self.bot._session = _Sess()
        asyncio.run(self.bot._call("sendMessage", {"chat_id": self.chat_id,
                                                   "text": "Онлайн WS: 3"}))
        self.assertEqual(seen["payload"]["text"], "WS online: 3")
        asyncio.run(self.bot._call("sendMessage", {"chat_id": "-100123",
                                                   "text": "Сводка ушла в канал."}))
        self.assertEqual(seen["payload"]["text"], "Сводка ушла в канал.")
        self.bot._session = None


class BotI18nSourceTest(unittest.TestCase):
    """Русские подписи не размножились в коде без перевода."""

    def test_table_has_no_duplicate_keys(self):
        """Дублей в таблице быть не должно: иначе перевод «зависает» на глаз."""
        import ast
        src = open(os.path.join(HERE, "bot_i18n.py"), encoding="utf-8").read()
        tree = ast.parse(src)
        keys = []
        for node in ast.walk(tree):
            # только таблица перевода: у LANGS свои словари с ключами label/flag
            if isinstance(node, ast.Dict) and len(node.keys) > 20:
                keys += [k.value for k in node.keys
                         if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        dupes = {k for k in keys if keys.count(k) > 1}
        self.assertEqual(dupes, set(), f"дубли в таблице переводов: {dupes}")

    def test_check_tool_reports_clean_tree(self):
        """Инструмент проверки видит тот же чистый список — без «--all»."""
        from tools.bot_i18n_check import main
        self.assertEqual(main([], quiet=True), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
