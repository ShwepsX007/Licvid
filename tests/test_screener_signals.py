"""🐋 Signal Screener: фильтр пользователя, совпадения событий и рассылка."""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ.setdefault("LIQSCOPE_SECRET", "test-secret-not-the-published-default")

from screener_signals import (match_signal, normalize_signal, event_key,
                              signal_chat_meta, signal_chat_text,
                              signal_exchanges, signal_html, signal_hits,
                              signal_money, signal_presets)  # noqa: E402


def ev(**over) -> dict:
    base = {"chain": "ETH", "hash": "0x" + "a" * 64, "log_index": "0x1",
            "timestamp": 1_700_000_000, "symbol": "USDT", "amount": 400_000.0,
            "usd": 400_000.0, "from": "0x" + "1" * 40, "to": "0x" + "2" * 40,
            "from_label": "Binance 14", "to_label": "", "direction": "outflow",
            "source": "realtime"}
    base.update(over)
    return base


class ConfigTests(unittest.TestCase):
    def test_defaults_when_empty(self):
        cfg = normalize_signal({})
        self.assertEqual(cfg["exchange"], "ALL")
        self.assertEqual(cfg["direction"], "all")
        self.assertEqual(cfg["chain"], "ALL")
        self.assertFalse(cfg["notify"])
        self.assertGreater(cfg["min_usd"], 0)

    def test_junk_never_breaks_config(self):
        cfg = normalize_signal({"exchange": object(), "direction": 42, "chain": "XRP",
                                "min_usd": "abc", "cooldown_min": None, "notify": 1})
        self.assertEqual(cfg["exchange"], "ALL")
        self.assertEqual(cfg["direction"], "all")
        self.assertEqual(cfg["chain"], "ALL")
        self.assertEqual(cfg["cooldown_min"], 15)   # битое значение → умолчание
        self.assertTrue(cfg["notify"])
        # битый порог не должен обнулять фильтр: берём умолчание
        self.assertEqual(cfg["min_usd"], float(normalize_signal({})["min_usd"]))

    def test_exchange_label_is_normalized_to_venue(self):
        # кабинет шлёт метку из реестра — фильтру нужен идентификатор площадки
        self.assertEqual(normalize_signal({"exchange": "Binance 14"})["exchange"], "binance")
        self.assertEqual(normalize_signal({"exchange": "gate.io 2"})["exchange"], "gate")
        self.assertEqual(normalize_signal({"exchange": "all"})["exchange"], "ALL")

    def test_manual_threshold_and_clamps(self):
        self.assertEqual(normalize_signal({"min_usd": "77777"})["min_usd"], 77777.0)
        self.assertEqual(normalize_signal({"min_usd": -5})["min_usd"], 0.0)
        self.assertLessEqual(normalize_signal({"min_usd": 1e30})["min_usd"], 1e12)
        self.assertEqual(normalize_signal({"cooldown_min": 10 ** 9})["cooldown_min"], 1440)

    def test_presets_are_lists(self):
        presets = signal_presets()
        self.assertIn(50_000, presets["volume_usd"])
        self.assertEqual(presets["directions"][0], "all")
        self.assertIn("SOLANA", presets["chains"])
        self.assertEqual(presets["default"], normalize_signal(presets["default"]))


class MatchTests(unittest.TestCase):
    def test_threshold_only(self):
        cfg = normalize_signal({"min_usd": 250_000})
        self.assertIsNone(match_signal(ev(usd=100_000), cfg))
        self.assertIsNotNone(match_signal(ev(usd=250_000), cfg))

    def test_direction_filters_both_ways(self):
        out = normalize_signal({"direction": "outflow", "min_usd": 1})
        inflow = normalize_signal({"direction": "inflow", "min_usd": 1})
        self.assertIsNotNone(match_signal(ev(direction="outflow"), out))
        self.assertIsNone(match_signal(ev(direction="inflow", from_label="",
                                           to_label="Binance 14"), out))
        self.assertIsNotNone(match_signal(ev(direction="inflow", from_label="",
                                              to_label="Binance 14"), inflow))

    def test_network_and_venue(self):
        cfg = normalize_signal({"chain": "SOLANA", "exchange": "binance", "min_usd": 1})
        self.assertIsNone(match_signal(ev(chain="ETH"), cfg))
        self.assertIsNotNone(match_signal(ev(chain="SOLANA", from_label="Binance 3"), cfg))
        other = normalize_signal({"exchange": "kraken", "min_usd": 1})
        self.assertIsNone(match_signal(ev(from_label="Binance 3"), other))

    def test_hit_shape_is_safe_and_complete(self):
        hit = match_signal(ev(from_label="Binance <script>alert(1)</script>"),
                           normalize_signal({"min_usd": 1, "exchange": "binance"}))
        self.assertEqual(hit["chain"], "ETH")
        self.assertEqual(hit["symbol"], "USDT")
        self.assertEqual(hit["venue"], "binance")
        self.assertEqual(hit["metric"], "whale")
        self.assertTrue(hit["key"])
        self.assertEqual(event_key(ev()), hit["key"])
        text = signal_chat_text(hit)
        self.assertIn("Binance", text)
        # разметка из внешних меток не должна проходить в текст и в HTML
        self.assertNotIn("<script>", signal_html(hit, "https://liqscope.online"))
        self.assertNotIn("<script>", text)
        meta = signal_chat_meta(hit)
        self.assertEqual(meta["symbol"], "USDT")
        self.assertEqual(meta["usd"], 400000.0)

    def test_non_dict_and_bad_numbers(self):
        cfg = normalize_signal({})
        self.assertIsNone(match_signal("nope", cfg))
        self.assertIsNone(match_signal(ev(usd="abc"), cfg))
        self.assertIsNone(match_signal(ev(usd=float("nan")), cfg))

    def test_money_compact(self):
        self.assertEqual(signal_money(1_250_000), "$1.25M")
        self.assertEqual(signal_money(250_000), "$250K")
        self.assertEqual(signal_money(0), "$0")


class HitsTests(unittest.TestCase):
    def test_limit_and_cooldown(self):
        cfg = normalize_signal({"min_usd": 1, "cooldown_min": 0})
        events = [ev(hash=f"0x{i:064x}", usd=1_000 + i) for i in range(10)]
        hits = signal_hits(events, cfg, limit=3)
        self.assertEqual(len(hits), 3)
        self.assertEqual(hits[0]["usd"], max(e["usd"] for e in events))
        # пауза на монету: второй проход с тем же(last_fire) молчит
        cfg_pause = normalize_signal({"min_usd": 1, "cooldown_min": 30})
        now = time.time()
        last = {"USDT": now}
        self.assertEqual(signal_hits(events, cfg_pause, now=now,
                                    last_fire=lambda sym: last.get(sym)), [])

    def test_future_events_are_ignored(self):
        cfg = normalize_signal({"min_usd": 1, "cooldown_min": 0})
        future = time.time() + 3600
        self.assertEqual(signal_hits([ev(timestamp=future)], cfg, now=time.time()), [])


class ExchangeListTests(unittest.TestCase):
    class FakeScreener:
        wallets = {"0x1": "Binance 14", "0x2": "Coinbase — Hot Wallet 10",
                   "0x3": "Binance 15", "0x4": ""}

    def test_built_from_registry(self):
        rows = signal_exchanges(self.FakeScreener())
        self.assertEqual([r["id"] for r in rows], ["binance", "coinbase"])
        self.assertEqual(rows[0]["name"], "Binance")
        self.assertEqual(rows[0]["labels"], ["Binance 14", "Binance 15"])

    def test_events_add_venues_and_bad_input_is_safe(self):
        rows = signal_exchanges(None, [ev(from_label="Bybit 7", usd=1)])
        self.assertEqual([r["id"] for r in rows], ["bybit"])
        self.assertEqual(signal_exchanges(object(), []), [])
        self.assertEqual(signal_exchanges(None, "не список"), [])


class ServiceRegistrationTests(unittest.TestCase):
    """Сервис есть в каталоге: кабинет покажет карточку без правки базы."""

    def test_default_services_include_screener_signals(self):
        from accounts import DEFAULT_SERVICES
        row = next((s for s in DEFAULT_SERVICES if s["slug"] == "screener_signals"), None)
        self.assertIsNotNone(row)
        self.assertFalse(row["coming_soon"])
        self.assertTrue(row["enabled"])
        self.assertTrue(row["title"].strip())
        self.assertTrue(row["description"].strip())


class StoreTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from accounts import Store
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="test-secret")
        self.user = self.store.upsert_telegram_user(
            {"id": 4242, "first_name": "Whale", "language_code": "ru"})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_service_row_created_by_migration(self):
        slugs = {row["slug"] for row in self.store.list_services()}
        self.assertIn("screener_signals", slugs)

    def test_config_roundtrip_and_subscriber(self):
        saved = self.store.set_user_service_config(
            self.user["id"], "screener_signals",
            {"notify": True, "exchange": "Binance 14", "direction": "outflow",
             "chain": "SOLANA", "min_usd": 900000, "cooldown_min": 5}, enabled=True)
        self.assertTrue(saved["ok"])
        row = self.store.get_user_service(self.user["id"], "screener_signals")
        # в базе лежит то, что прислал кабинет, а фильтр нормализуется на чтении
        cfg = normalize_signal(row["config"])
        self.assertEqual(cfg["exchange"], "binance")
        self.assertEqual(cfg["min_usd"], 900000.0)
        self.assertEqual(cfg["chain"], "SOLANA")
        subs = self.store.list_service_subscribers("screener_signals")
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["user_id"], self.user["id"])
        self.assertEqual(subs[0]["tg_id"], self.user["tg_id"])
        # отписка от сервиса убирает подписчика из рассылки
        self.store.set_user_service_config(self.user["id"], "screener_signals",
                                           cfg, enabled=False)
        self.assertEqual(self.store.list_service_subscribers("screener_signals"), [])

    def test_config_is_limited_like_other_services(self):
        big = {"notify": True, "min_usd": 1, "junk": {"x" * 500: "y" * 500}}
        saved = self.store.set_user_service_config(
            self.user["id"], "screener_signals", big, enabled=True)
        self.assertTrue(saved["ok"])
        self.assertLess(len(saved["config"]["junk"]["x" * 40]), 130)


class DispatchTests(unittest.TestCase):
    """Цикл рассылки: лента кабинета всегда, Telegram — по тумблеру."""

    def setUp(self):
        import server
        self.server = server
        self.sent = []
        self.tg = []

        async def fake_push(user_id, kind, text, meta=None):
            self.sent.append((user_id, kind, text, meta))

        async def fake_send(tg_id, text, markup=None):
            self.tg.append((tg_id, text))

        self._push = server.push_service_message
        self._bot = server.tg_bot
        self._state = dict(server._screener_signal_state)
        server.push_service_message = fake_push

        class Bot:
            running = True
            send = staticmethod(fake_send)
            site_url = staticmethod(lambda: "https://liqscope.online")
            site_link_kb = staticmethod(lambda label, path="": {"inline_keyboard": [[{
                "text": label, "url": "https://liqscope.online" + path}]]})
            warm_langs = staticmethod(lambda subs: None)
        server.tg_bot = Bot()
        server._screener_signal_state.update(cursor=0.0, seen={}, pauses={}, order=[])

    def tearDown(self):
        self.server.push_service_message = self._push
        self.server.tg_bot = self._bot
        self.server._screener_signal_state.clear()
        self.server._screener_signal_state.update(self._state)

    def run_dispatch(self, subs, events):
        return asyncio.run(self.server.screener_signal_dispatch(subs, events))

    def test_chat_feed_without_telegram_toggle(self):
        events = [ev()]
        subs = [{"user_id": 1, "tg_id": 77, "config": {"min_usd": 100_000}}]
        self.assertEqual(self.run_dispatch(subs, events), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][1], "screener")
        self.assertIn("Binance", self.sent[0][2])
        self.assertEqual(self.sent[0][3]["parts"]["symbol"], "USDT")
        self.assertEqual(self.tg, [], "Telegram выключен — в бот ничего не идёт")

    def test_telegram_when_notify_on(self):
        subs = [{"user_id": 1, "tg_id": 77,
                 "config": {"min_usd": 100_000, "notify": True}}]
        self.assertEqual(self.run_dispatch(subs, [ev()]), 1)
        self.assertEqual(len(self.tg), 1)
        self.assertIn("<b>Binance · отток с биржи</b>", self.tg[0][1])
        self.assertIn("$400K", self.tg[0][1])

    def test_same_event_is_not_sent_twice(self):
        subs = [{"user_id": 1, "tg_id": 0, "config": {"min_usd": 100_000}}]
        self.run_dispatch(subs, [ev()])
        self.run_dispatch(subs, [ev()])
        self.assertEqual(len(self.sent), 1)

    def test_filter_excludes_other_events(self):
        subs = [{"user_id": 1, "tg_id": 0,
                 "config": {"min_usd": 100_000, "direction": "inflow"}}]
        self.assertEqual(self.run_dispatch(subs, [ev()]), 0)
        self.assertEqual(self.sent, [])

    def test_bad_subscriber_rows_are_skipped(self):
        self.assertEqual(self.run_dispatch([{"user_id": 0}, {"user_id": None}, {}],
                                           [ev()]), 0)
        self.assertEqual(self.run_dispatch(None, [ev()]), 0)
        self.assertEqual(self.sent, [])

    def test_cooldown_between_cycles(self):
        cfg = {"min_usd": 100_000, "cooldown_min": 60, "notify": False}
        subs = [{"user_id": 1, "tg_id": 0, "config": cfg}]
        self.assertEqual(self.run_dispatch(subs, [ev(hash="0x" + "b" * 64)]), 1)
        # та же монета в паузе — второй кит того же токена не уходит
        self.assertEqual(self.run_dispatch(subs, [ev(hash="0x" + "c" * 64)]), 0)
        # а другая монета проходит
        self.assertEqual(self.run_dispatch(subs, [ev(hash="0x" + "d" * 64,
                                                    symbol="USDC")]), 1)




class CabinetStringsTests(unittest.TestCase):
    """Доска сервиса в кабинете: каждый t("sc.*") переведён на всех языках.

    Забытый ключ выглядит не падением, а сырым «sc.pause_min» посреди
    перевода — проверяем состав ключей и то, что строки не совпадают с
    русским (кроме самих сумм и тикеров).
    """

    LANGS = ("ru", "en", "zh", "hi", "es")

    def setUp(self):
        root = Path(HERE) / "static"
        self.account = (root / "account.js").read_text(encoding="utf-8")
        self.chat = (root / "terminal_chat.js").read_text(encoding="utf-8")
        self.dicts = {}
        for lang in self.LANGS:
            data = json.loads((root / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
            self.dicts[lang] = data["keys"]

    def _used(self, source, prefix):
        # и t("…") в кабинете, и T("…", «русский») в чате терминала
        return set(re.findall(r'[tT]\("(' + re.escape(prefix) + r'[A-Za-z0-9_.]+)"', source))

    def test_sc_keys_exist_in_every_language(self):
        used = self._used(self.account, "sc.")
        self.assertGreaterEqual(len(used), 20, "не нашли ключи доски в account.js")
        for lang in self.LANGS:
            missing = sorted(used - set(self.dicts[lang]))
            self.assertFalse(missing, f"{lang}: нет ключей {missing}")

    def test_chat_keys_exist_in_every_language(self):
        used = self._used(self.chat, "chat.sig.whale") | self._used(self.chat, "chat.kind.whale")
        self.assertTrue(used, "не нашли ключи ленты «Сервисы»")
        for lang in self.LANGS:
            missing = sorted(used - set(self.dicts[lang]))
            self.assertFalse(missing, f"{lang}: нет ключей {missing}")

    def test_strings_are_translated_not_copied(self):
        cyr = re.compile("[А-Яа-яЁё]")
        used = self._used(self.account, "sc.") | self._used(self.chat, "chat.sig.whale")
        for lang in self.LANGS:
            if lang == "ru":
                continue
            for key in sorted(used):
                value = self.dicts[lang].get(key)
                self.assertIsNotNone(value, f"{lang}: пусто для {key}")
                if key.endswith(("_min", "pause_min")):
                    continue        # «{min} мин» может совпасть по длине цифр
                self.assertNotEqual(
                    value, self.dicts["ru"][key], f"{lang}: {key} не переведён")
                if lang in ("en", "es"):
                    self.assertFalse(cyr.search(value), f"{lang}: русский в {key}")

    def test_pages_bundle_is_rebuilt(self):
        """Собранный словарь страниц обязан совпадать с JSON (иначе браузер
        получит старые строки)."""
        built = (Path(HERE) / "static" / "i18n.pages.js").read_text(encoding="utf-8")
        for key in sorted(self._used(self.account, "sc."))[:5]:
            self.assertIn(key, built, f"ключа {key} нет в i18n.pages.js")


class RouteTests(unittest.TestCase):
    """GET/POST настроек сервиса: приватно, нормализуется, сохраняется."""

    def setUp(self):
        import tempfile
        from accounts import Store
        import web_account
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="test-secret")
        self.user = self.store.upsert_telegram_user(
            {"id": 777, "first_name": "Whale", "language_code": "ru"})
        self.web_account = web_account
        self._store = web_account.ctx.store
        self._verify = web_account.ctx.require_email_verification
        self._snap = web_account.ctx.screener_signal_fn
        self._user = web_account.current_user
        web_account.ctx.store = self.store
        web_account.ctx.require_email_verification = False
        web_account.ctx.screener_signal_fn = lambda cfg=None: {
            "available": True,
            "exchanges": [{"id": "binance", "name": "Binance", "labels": ["Binance 14"]}],
            "recent": [],
        }
        web_account.current_user = lambda request: self.user
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        self.app = FastAPI()
        web_account.register_account_routes(self.app)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.web_account.ctx.store = self._store
        self.web_account.ctx.require_email_verification = self._verify
        self.web_account.ctx.screener_signal_fn = self._snap
        self.web_account.current_user = self._user
        self.store.close()
        self.tmp.cleanup()

    def test_get_shape(self):
        body = self.client.get("/api/account/screener/signals").json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["config"]["exchange"], "ALL")
        self.assertIn("volume_usd", body["presets"])
        self.assertEqual([row["id"] for row in body["exchanges"]], ["binance"])
        self.assertFalse(body["subscribed"])

    def test_post_roundtrip_and_garbage(self):
        saved = self.client.post("/api/account/screener/signals", json={
            "notify": True, "exchange": "Coinbase 10", "direction": "inflow",
            "chain": "BASE", "min_usd": "1500000", "cooldown_min": 30, "evil": "<x>",
        })
        self.assertEqual(saved.status_code, 200)
        cfg = saved.json()["config"]
        self.assertEqual(cfg["exchange"], "coinbase")
        self.assertEqual(cfg["min_usd"], 1500000.0)
        self.assertNotIn("evil", cfg)
        again = self.client.get("/api/account/screener/signals").json()
        self.assertTrue(again["subscribed"], "настройка фильтра подписывает сервис")
        self.assertEqual(again["config"]["direction"], "inflow")
        row = self.store.get_user_service(self.user["id"], "screener_signals")
        self.assertEqual(row["config"]["chain"], "BASE")
        # мусор вместо конфига не роняет ручку и не ломает фильтр
        junk = self.client.post("/api/account/screener/signals",
                                json={"min_usd": {"a": 1}, "exchange": ["x"]})
        self.assertEqual(junk.status_code, 200)
        self.assertEqual(junk.json()["config"]["exchange"], "ALL")

    def test_guest_gets_401(self):
        self.web_account.current_user = lambda request: None
        self.assertEqual(self.client.get("/api/account/screener/signals").status_code, 401)
        self.assertEqual(self.client.post("/api/account/screener/signals", json={}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
