"""«Донат»: адреса кошельков, публичная ручка и сохранение из админки.

Платёжных сервисов нет — только показ адресов и копирование в буфер. Поэтому
проверяем ровно то, что видит гость и администратор:

* формат адресов: мягкая проверка по сетям (0x… для EVM, bc1…/1…/3… для
  BTC, base58 для Solana и Tron, EQ…/UQ… для TON) — опечатку сохранить
  нельзя, 400 с текстом ошибки;
* ``GET /api/donate/wallets`` — без входа, только непустые сети, кэш 60 с;
* ``GET/POST /api/admin/donate/wallets`` — 401 без входа, 403 не админу;
* сохранение через общую ручку админки (``/api/admin/settings``) и через
  отдельную ручку дают один и тот же результат, а публичный список виден
  сразу после сохранения (кэш сбрасывается, перезапуск не нужен);
* разметка: ``static/donate.js`` подключён на всех страницах с шапкой, не
  трогает ``nav-account`` (шапку рисует только ``account.js``) и не
  показывается внутри iframe графиков (``embed=1``/``mode=panel``);
* адреса подставляются только через ``textContent`` — их нельзя превратить
  в разметку даже со стороны админки.
"""
from __future__ import annotations

import json
import re
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

# Секрет для тестов — до импорта server (иначе fail-fast в PROD без секрета)
os.environ.setdefault("LIQSCOPE_SECRET", "test-secret-not-the-published-default")
os.environ.setdefault("LIQSCOPE_DEMO", "1")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app_settings  # noqa: E402
import donate  # noqa: E402
import web_account  # noqa: E402
from accounts import Store  # noqa: E402

STATIC = os.path.join(HERE, "static")

#: Настоящие адреса сетей — те же, что человек вставит из кошелька.
GOOD = {
    "ethereum": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",
    "bitcoin": "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq",
    "solana": "Vote111111111111111111111111111111111111111",
    "bnb": "0x2170Ed0880ac9A755fd29B2688956BD959F933F8",
    "base": "0x4200000000000000000000000000000000000006",
    "polygon": "0x7D1AfA7B718fb893dB30A3aBc0Cfc608AaCfeBB0",
    "tron": "TKzxdSv2FZKQrEqkKVgp5DcwEXBEKMg2Ax",
    "ton": "UQ" + "A" * 46,                      # 48 знаков, как настоящий
}
#: Что человек вставит по ошибке: обрезанный адрес, буквы вне base58,
#: чужой формат. Проверка должна поймать — и сказать, чего именно ждёт.
BAD = {
    "ethereum": "0x123",
    "bitcoin": "bc1q" + "i" * 30,               # «i» в bech32 не бывает
    "solana": "0OIl",                           # 0/O/I/l вне base58
    "bnb": "0x" + "z" * 40,
    "base": "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq",   # BTC в EVM
    "polygon": "",                              # пустое поле — не ошибка
    "tron": "XKzxdSv2FZKQrEqkKVgp5DcwEXBEKMg2Ax",
    "ton": "UQ" + "A" * 20,
}


class AddressFormatTest(unittest.TestCase):
    """Мягкая проверка формата: пусто — «сеть скрыта», мусор — ошибка."""

    def test_every_network_has_a_real_address(self) -> None:
        for net in donate.WALLETS:
            with self.subTest(net=net.id):
                self.assertEqual(donate.validate(net.id, GOOD[net.id]), "",
                                 f"{net.id}: хороший адрес отвергнут")

    def test_typos_are_rejected_with_a_hint(self) -> None:
        for net in donate.WALLETS:
            bad = BAD[net.id]
            if not bad:
                continue
            with self.subTest(net=net.id):
                err = donate.validate(net.id, bad)
                self.assertTrue(err, f"{net.id}: опечатка прошла проверку")
                self.assertIn(net.label, err, "в ошибке нет имени сети")

    def test_empty_means_hidden_not_broken(self) -> None:
        for net in donate.WALLETS:
            with self.subTest(net=net.id):
                self.assertEqual(donate.validate(net.id, ""), "")
                self.assertEqual(donate.validate(net.id, "   "), "")

    def test_addresses_from_other_networks_do_not_pass(self) -> None:
        # EVM-адрес в TON и TON в EVM — самая частая путаница в формах.
        self.assertTrue(donate.validate("ton", GOOD["ethereum"]))
        self.assertTrue(donate.validate("ethereum", GOOD["ton"]))
        self.assertTrue(donate.validate("bitcoin", GOOD["ethereum"]))
        self.assertTrue(donate.validate("solana", GOOD["ethereum"]))

    def test_unknown_network_and_long_values(self) -> None:
        self.assertIn("неизвестная", donate.validate("dogecoin", "D…"))
        long_addr = "0x" + "a" * 200
        self.assertIn("длиннее", donate.validate("ethereum", long_addr))

    def test_whitespace_and_zero_width_are_stripped(self) -> None:
        messy = "  " + GOOD["ethereum"] + "\u200b "
        self.assertEqual(donate.validate("ethereum", messy), "")
        self.assertEqual(donate.normalize(messy), GOOD["ethereum"])

    def test_validate_keys_reports_first_error(self) -> None:
        ok = {donate.KEYS["ethereum"]: GOOD["ethereum"],
              donate.KEYS["ton"]: GOOD["ton"]}
        self.assertEqual(donate.validate_keys(ok), "")
        bad = dict(ok, **{donate.KEYS["ton"]: BAD["ton"]})
        err = donate.validate_keys(bad)
        self.assertTrue(err)
        self.assertIn("TON", err)
        # чужие ключи (не про донат) проверку не ломают
        self.assertEqual(donate.validate_keys({"LIQSCOPE_SMTP_HOST": "smtp"}), "")


class SettingsSchemaTest(unittest.TestCase):
    """Кошельки заводятся через общую схему настроек — она же рисует админку."""

    def test_every_wallet_is_in_the_admin_schema(self) -> None:
        self.assertEqual(len(donate.WALLETS), 8, "сети донатов изменились")
        for net in donate.WALLETS:
            key = donate.KEYS[net.id]
            with self.subTest(net=net.id):
                self.assertIn(key, app_settings.MANAGED_SETTINGS)
                spec = app_settings.MANAGED_SETTINGS[key]
                self.assertEqual(spec["section"], app_settings.SECTION_DONATE)
                self.assertFalse(spec["secret"], "адрес кошелька — не секрет")
                self.assertFalse(spec["restart"],
                                 "смена кошелька не требует перезапуска")
                self.assertIn("Пустое поле", spec["hint"])

    def test_section_is_shown_in_admin(self) -> None:
        self.assertIn(app_settings.SECTION_DONATE, app_settings.SECTION_ORDER)
        self.assertEqual(app_settings.SECTION_ORDER[-1],
                         app_settings.SECTION_DONATE,
                         "вкладка «Донат» должна быть последней")

    def test_short_env_names_are_aliases(self) -> None:
        for net in donate.WALLETS:
            short = "DONATE_WALLET_" + net.id.upper()
            with self.subTest(net=net.id):
                self.assertEqual(app_settings.canonical_key(short),
                                 donate.KEYS[net.id])
        # привычные сокращения монет тоже ведут к тем же ключам
        self.assertEqual(app_settings.canonical_key("DONATE_WALLET_ETH"),
                         donate.KEYS["ethereum"])
        self.assertEqual(app_settings.canonical_key("DONATE_WALLET_BTC"),
                         donate.KEYS["bitcoin"])

    def test_schema_lists_wallets_in_the_same_order(self) -> None:
        """Порядок сетей в форме админки = порядок в кнопке «Донат»."""
        in_schema = [k for k, spec in app_settings.MANAGED_SETTINGS.items()
                     if spec.get("section") == app_settings.SECTION_DONATE]
        self.assertEqual(in_schema, [donate.KEYS[n.id] for n in donate.WALLETS])


class WalletApiTest(unittest.TestCase):
    """Публичная ручка и админские ручки — на живых маршрутах FastAPI."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s",
                           admin_emails=["boss@liqscope.online"])
        self.settings = app_settings.SettingsManager(
            os.path.join(self.tmp.name, "a.db"))
        web_account.ctx.store = self.store
        web_account.ctx.settings = self.settings
        web_account.ctx.secret = "s"
        web_account.ctx.public_url = "https://liqscope.online"
        web_account.ctx.cookie_secure = False
        web_account.ctx.require_email_verification = True
        web_account.ctx.mailer = None
        donate.ctx.settings = self.settings
        donate.invalidate_cache()
        for lim in (web_account._MAIL_RATE, web_account._MAIL_RATE_EMAIL,
                    web_account._LOGIN_RATE, web_account._CAPTCHA_RATE):
            lim._hits.clear()
        app = FastAPI()
        web_account.register_account_routes(app)
        donate.register_donate_routes(app)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        donate.ctx.settings = None
        donate.invalidate_cache()
        web_account.ctx.settings = None
        self.store.close()
        self.settings.close()
        self.tmp.cleanup()

    # ----- вход ------------------------------------------------------------
    def captcha(self) -> dict:
        data = self.client.get("/api/auth/captcha").json()
        a, b = [int(x) for x in str(data["question"]).split("+")]
        return {"captcha": data["token"], "answer": a + b}

    def register(self, email: str) -> None:
        body = {"email": email, "password": "Good-Pass-2026",
                "name": email.split("@")[0], "language": "ru"}
        body.update(self.captcha())
        r = self.client.post("/api/auth/email/register", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        row = self.store._db.execute(
            "SELECT token FROM email_tokens ORDER BY rowid DESC LIMIT 1").fetchone()
        self.assertTrue(row, "токен подтверждения не создан")
        r2 = self.client.get(f"/verify?token={row['token']}",
                             follow_redirects=False)
        self.assertEqual(r2.status_code, 303)

    def login_boss(self) -> None:
        self.register("boss@liqscope.online")
        me = self.client.get("/api/auth/me").json()
        self.assertTrue(me["user"]["is_admin"], me)

    # ----- публичная ручка --------------------------------------------------
    def test_public_list_is_empty_and_needs_no_login(self) -> None:
        r = self.client.get("/api/donate/wallets")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True, "wallets": []})

    def test_public_list_has_no_network_without_an_address(self) -> None:
        self.settings.set(donate.KEYS["ethereum"], GOOD["ethereum"])
        self.settings.set(donate.KEYS["tron"], GOOD["tron"])
        donate.invalidate_cache()
        d = self.client.get("/api/donate/wallets").json()
        self.assertTrue(d["ok"])
        self.assertEqual([w["id"] for w in d["wallets"]], ["ethereum", "tron"],
                         "сети идут в фиксированном порядке, пустых нет")
        first = d["wallets"][0]
        self.assertEqual(first["label"], "Ethereum")
        self.assertEqual(first["short"], "ETH")
        self.assertEqual(first["address"], GOOD["ethereum"])
        self.assertTrue(first["color"].startswith("#"))

    def test_public_list_falls_back_to_environment(self) -> None:
        with mock.patch.dict(os.environ,
                             {"DONATE_WALLET_TON": GOOD["ton"]}, clear=False):
            donate.invalidate_cache()
            d = self.client.get("/api/donate/wallets").json()
            self.assertEqual([w["id"] for w in d["wallets"]], ["ton"])
            self.assertEqual(d["wallets"][0]["address"], GOOD["ton"])

    def test_public_response_carries_no_secrets(self) -> None:
        self.settings.set("LIQSCOPE_SMTP_PASSWORD", "hunter2-secret")
        self.settings.set(donate.KEYS["bitcoin"], GOOD["bitcoin"])
        donate.invalidate_cache()
        r = self.client.get("/api/donate/wallets")
        self.assertNotIn("hunter2-secret", r.text)
        self.assertNotIn("LIQSCOPE_", r.text, "внутренние имена ключей не отдаём")

    # ----- админские ручки --------------------------------------------------
    def test_admin_routes_require_an_admin(self) -> None:
        self.assertEqual(self.client.get(
            "/api/admin/donate/wallets").status_code, 401)
        self.assertEqual(self.client.post(
            "/api/admin/donate/wallets", json={}).status_code, 401)
        self.register("guest@liqscope.online")
        self.assertEqual(self.client.get(
            "/api/admin/donate/wallets").status_code, 403)
        self.assertEqual(self.client.post(
            "/api/admin/donate/wallets", json={}).status_code, 403)

    def test_admin_get_lists_every_network_even_empty(self) -> None:
        self.login_boss()
        d = self.client.get("/api/admin/donate/wallets").json()
        self.assertTrue(d["ok"])
        self.assertEqual([w["id"] for w in d["wallets"]],
                         [n.id for n in donate.WALLETS])
        self.assertTrue(all(w["address"] == "" for w in d["wallets"]))

    def test_admin_save_and_public_read_without_restart(self) -> None:
        self.login_boss()
        body = {donate.KEYS["ethereum"]: GOOD["ethereum"],
                donate.KEYS["ton"]: GOOD["ton"]}
        r = self.client.post("/api/admin/donate/wallets", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(sorted(r.json()["changed"]), ["ethereum", "ton"])
        # та же картина в схеме админки (общая ручка настроек)
        schema = self.client.get("/api/admin/settings").json()["settings"]
        self.assertEqual(schema[donate.KEYS["ethereum"]]["value"],
                         GOOD["ethereum"])
        self.assertEqual(schema[donate.KEYS["ethereum"]]["source"], "db")
        # публичная ручка отдаёт ровно две сети — и сразу, без перезапуска
        ids = [w["id"] for w in self.client.get("/api/donate/wallets").json()["wallets"]]
        self.assertEqual(ids, ["ethereum", "ton"])

    def test_clearing_a_field_hides_the_network(self) -> None:
        self.login_boss()
        self.client.post("/api/admin/donate/wallets",
                         json={donate.KEYS["ethereum"]: GOOD["ethereum"]})
        self.client.post("/api/admin/donate/wallets",
                         json={donate.KEYS["ethereum"]: ""})
        self.assertEqual(self.client.get("/api/donate/wallets").json()["wallets"], [])
        self.assertEqual(self.settings.get(donate.KEYS["ethereum"]), "")

    def test_admin_save_rejects_a_typo(self) -> None:
        self.login_boss()
        r = self.client.post("/api/admin/donate/wallets",
                             json={donate.KEYS["ethereum"]: BAD["ethereum"]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Ethereum", r.json()["error"])
        self.assertEqual(self.settings.get(donate.KEYS["ethereum"]), "")

    def test_admin_save_accepts_short_names_and_network_ids(self) -> None:
        self.login_boss()
        # форма может прислать ключ как в .env или просто имя сети
        r = self.client.post("/api/admin/donate/wallets",
                             json={"DONATE_WALLET_TON": GOOD["ton"],
                                   "tron": GOOD["tron"]})
        self.assertEqual(r.status_code, 200, r.text)
        ids = [w["id"] for w in self.client.get("/api/donate/wallets").json()["wallets"]]
        self.assertEqual(ids, ["tron", "ton"], "порядок сетей фиксирован")

    def test_settings_route_validates_wallets_too(self) -> None:
        """Общая ручка админки (её и шлёт вкладка) тоже проверяет адреса."""
        self.login_boss()
        r = self.client.post("/api/admin/settings",
                             json={donate.KEYS["bitcoin"]: BAD["bitcoin"]})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("Bitcoin", r.json()["error"])
        r2 = self.client.post("/api/admin/settings",
                              json={donate.KEYS["bitcoin"]: GOOD["bitcoin"],
                                    donate.KEYS["solana"]: ""})
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertIn(donate.KEYS["bitcoin"], r2.json()["saved"])
        self.assertIn(donate.KEYS["bitcoin"],
                      self.client.get("/api/admin/settings").json()["settings"])
        ids = [w["id"] for w in self.client.get("/api/donate/wallets").json()["wallets"]]
        self.assertEqual(ids, ["bitcoin"])

    def test_cache_is_dropped_after_saving(self) -> None:
        """Кэш 60 с не должен скрывать только что сохранённый адрес."""
        self.settings.set(donate.KEYS["ethereum"], GOOD["ethereum"])
        donate.invalidate_cache()
        with mock.patch.dict(os.environ, {"LIQSCOPE_API_CACHE": "1"}, clear=False):
            self.assertEqual(len(donate.cached_wallets()), 1)
            self.login_boss()
            self.client.post("/api/admin/settings", json={
                donate.KEYS["ethereum"]: GOOD["base"],
                donate.KEYS["base"]: GOOD["base"]})
            ids = [w["id"] for w in self.client.get("/api/donate/wallets").json()["wallets"]]
            self.assertEqual(ids, ["ethereum", "base"],
                             "после сохранения кэш обязан обновиться")

    def test_cached_wallets_hit_the_cache(self) -> None:
        self.settings.set(donate.KEYS["ethereum"], GOOD["ethereum"])
        donate.invalidate_cache()
        with mock.patch.dict(os.environ, {"LIQSCOPE_API_CACHE": "1"}, clear=False):
            self.assertEqual(len(donate.cached_wallets()), 1)
            self.settings.set(donate.KEYS["ethereum"], "")   # кэш ещё жив
            self.assertEqual(len(donate.cached_wallets()), 1,
                             "без сброса кэша ответ берётся из кэша")
            donate.invalidate_cache()
            self.assertEqual(donate.cached_wallets(), [])


class DonateFrontendTest(unittest.TestCase):
    """Разметка: кнопка в шапке, скрипт подключён, адреса — только текстом."""

    def setUp(self) -> None:
        with open(os.path.join(STATIC, "donate.js"), encoding="utf-8") as fh:
            self.js = fh.read()
        with open(os.path.join(STATIC, "account.css"), encoding="utf-8") as fh:
            self.css = fh.read()

    def test_script_is_loaded_on_every_page_with_a_header(self) -> None:
        for name in sorted(os.listdir(STATIC)):
            if not name.endswith(".html"):
                continue
            with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
                html = fh.read()
            with self.subTest(page=name):
                self.assertIn('id="nav-account"', html)
                self.assertIn("/static/donate.js", html,
                              f"{name}: нет кнопки донатов в шапке")

    def test_donate_does_not_draw_the_shared_menu(self) -> None:
        # Шапку рисует только account.js — иначе кнопки входа продублируются.
        self.assertNotIn("nav-account", self.js)

    def test_no_button_inside_chart_iframes(self) -> None:
        head = self.js.split("var API")[0]
        self.assertIn("embed", head)
        self.assertIn("panel", head)
        self.assertIn("return", head)

    def test_addresses_are_inserted_as_text_only(self) -> None:
        self.assertIn("textContent", self.js)
        self.assertNotIn(".innerHTML", self.js,
                         "адрес из админки нельзя вставлять разметкой")

    def test_network_color_is_validated_before_use(self) -> None:
        self.assertIn("[0-9a-fA-F]", self.js,
                      "цвет из ответа подставляем только валидный")

    def test_clipboard_has_an_exec_command_fallback(self) -> None:
        self.assertIn("navigator.clipboard.writeText", self.js)
        self.assertIn("execCommand", self.js)

    def test_toast_and_no_alert(self) -> None:
        self.assertIn("donate-toast", self.js)
        self.assertIn("donate-toast", self.css)
        self.assertNotIn("alert(", self.js)

    def test_button_and_popover_are_styled_in_the_shared_css(self) -> None:
        for cls in (".nav-donate-btn", ".donate-pop", ".donate-row",
                    ".donate-addr", ".donate-toast"):
            with self.subTest(cls=cls):
                self.assertIn(cls, self.css)
        self.assertIn("#8b1515", self.css.lower())
        self.assertIn("#a61d1d", self.css.lower(), "нет цвета при наведении")

    def test_no_addresses_are_hardcoded(self) -> None:
        self.assertIsNone(
            re.search(r"0x[0-9a-fA-F]{40}|bc1[0-9a-z]{20,}", self.js),
            "адреса приходят только из ручки")

    def test_i18n_keys_exist_in_every_language(self) -> None:
        need = {"donate.btn", "donate.title", "donate.hint", "donate.copied",
                "donate.copy"}
        packs = {}
        for lang in ("ru", "en", "zh", "hi", "es"):
            with open(os.path.join(STATIC, "i18n", f"{lang}.json"),
                      encoding="utf-8") as fh:
                packs[lang] = json.load(fh)["keys"]
        for lang, keys in packs.items():
            with self.subTest(lang=lang):
                self.assertTrue(need <= set(keys), f"{lang}: нет ключей донат-кнопки")
        self.assertEqual(packs["ru"]["donate.btn"], "Донат")
        self.assertEqual(packs["en"]["donate.btn"], "Donate")
        self.assertEqual(packs["ru"]["donate.copied"], "Адрес скопирован")
        self.assertEqual(packs["en"]["donate.copied"], "Address copied")
        for lang in ("en", "zh", "hi", "es"):
            for key, value in packs[lang].items():
                if key.startswith("donate."):
                    with self.subTest(lang=lang, key=key):
                        self.assertFalse(
                            any("а" <= c.lower() <= "я" for c in value),
                            f"{lang}.{key}: русский текст в переводе")


if __name__ == "__main__":
    unittest.main(verbosity=2)
