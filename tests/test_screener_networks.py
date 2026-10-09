"""Тумблеры сетей Скринера, последовательная работа ключей Alchemy и чистка базы кошельков.

Запускается без сети и без живой ноги:
    LIQSCOPE_SECRET=test-secret-not-the-published-default .venv/bin/python tests/test_screener_networks.py
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("LIQSCOPE_SECRET", "test-secret-not-the-published-default")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cex_wallets_updater import CEXWalletRegistry, normalize_wallet_record
from screener_networks import (DEFAULT_ENABLED, NETWORK_IDS, NetworkSwitch,
                               normalize_enabled, normalize_network, retention_days)
from whale_poller import WhalePoller
from whale_screener import WhaleHistoryStore, WhaleScreener


class SwitchTests(unittest.TestCase):
    def test_defaults_are_eth_plus_the_free_sources(self):
        # ни файла, ни значения, ни переменных окружения — заводской набор
        switch = NetworkSwitch(None, enabled=object())
        self.assertEqual(switch.enabled(), tuple(DEFAULT_ENABLED))
        self.assertEqual(set(DEFAULT_ENABLED), {"ETH", "TRON", "HYPERLIQUID"})
        # дорогие сети выключены: BNB/ARBITRUM/BASE/POLYGON/SOLANA не тратят CU
        for chain in ("BNB", "POLYGON", "ARBITRUM", "BASE", "SOLANA"):
            self.assertFalse(switch.is_enabled(chain), chain)

    def test_unknown_network_label_is_never_blocked(self):
        switch = NetworkSwitch(None, enabled=("ETH",))
        self.assertTrue(switch.is_enabled("GENERAL"))
        self.assertTrue(switch.is_enabled("EVM"))
        self.assertTrue(switch.is_enabled(""))
        self.assertFalse(switch.is_enabled("bnb chain"))

    def test_normalize_enabled_accepts_admin_shapes(self):
        self.assertEqual(normalize_enabled("ETH,TRON,HYPERLIQUID"), ("ETH", "TRON", "HYPERLIQUID"))
        # порядок всегда по NETWORK_IDS, а не по тому, как написал админ
        self.assertEqual(normalize_enabled("ethereum; bsC"), ("ETH", "BNB"))
        self.assertEqual(normalize_enabled({"ETH": True, "BASE": False}), ("ETH",))
        self.assertEqual(normalize_enabled("ALL"), tuple(NETWORK_IDS))
        self.assertEqual(normalize_enabled([]), ())          # «всё выключено» — легально
        self.assertEqual(normalize_enabled(None), tuple(DEFAULT_ENABLED))
        # мусорная строка НЕ означает «выключить всё» — страж от опечатки в env
        self.assertEqual(normalize_enabled("MARS"), tuple(DEFAULT_ENABLED))
        self.assertEqual(normalize_enabled(["MARS"]), ())   # явный список — как есть
        self.assertEqual(normalize_network("Polygon PoS"), "POLYGON")
        self.assertEqual(normalize_network(None), "")

    def test_switch_persists_across_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "screener_networks.json"
            first = NetworkSwitch(path, enabled=("ETH",))
            first.set_enabled("TRON", True)
            first.set_retention(21)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["networks"],
                             ["ETH", "TRON"])
            second = NetworkSwitch(path)
            self.assertEqual(second.enabled(), ("ETH", "TRON"))
            self.assertEqual(second.wallet_retention_days, 21)

    def test_broken_file_falls_back_and_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "screener_networks.json"
            path.write_text("{not json", encoding="utf-8")
            switch = NetworkSwitch(path, enabled=("ETH", "TRON"))
            self.assertTrue(switch.load_error)
            self.assertTrue(switch.as_dict()["load_error"])
            self.assertEqual(switch.enabled(), ("ETH", "TRON"))
            switch.set_enabled("BASE", True)      # перезапись чинит файл
            self.assertEqual(NetworkSwitch(path).enabled(), ("ETH", "BASE", "TRON"))

    def test_retention_clamped_and_zero_means_off(self):
        self.assertEqual(retention_days("0"), 0)
        self.assertEqual(retention_days("-5"), 0)
        self.assertEqual(retention_days("9999"), 365)
        self.assertEqual(retention_days("не число"), 7)

    def test_paid_flag_only_for_alchemy_sources(self):
        switch = NetworkSwitch(None, enabled="ALL")
        rows = {row["id"]: row for row in switch.status()}
        self.assertTrue(rows["ETH"]["paid"])
        self.assertTrue(rows["SOLANA"]["paid"])
        self.assertFalse(rows["TRON"]["paid"])
        self.assertFalse(rows["HYPERLIQUID"]["paid"])
        self.assertTrue(switch.any_paid_enabled())
        switch.set_enabled("ETH", False)
        for chain in ("BNB", "POLYGON", "ARBITRUM", "BASE", "SOLANA"):
            switch.set_enabled(chain, False)
        self.assertFalse(switch.any_paid_enabled())


class FakeResponse:
    def __init__(self, payload, status=200, body=""):
        self._payload, self.status, self._body = payload, status, body

    async def json(self):
        return self._payload

    async def text(self, errors=""):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class FakeSession:
    """Один хэндлер на весь тест: знаем, какие ключы реально дёргались."""

    def __init__(self, handler):
        self.handler = handler
        self.urls = []

    def post(self, url, json=None, timeout=None):
        self.url = url
        return self.handler(url, json or {})


class KeyPoolTests(unittest.TestCase):
    """Ключи используются по одному: второй включается только когда первый сдох."""

    def make_poller(self, tmp, *, keys=("k1", "k2", "k3"), monthly_cu=100_000):
        class Store:
            def keys(self_inner):
                return [(name, f"secret-{name}") for name in keys]

            def public(self_inner):
                return [{"id": name, "hint": f"…{name[-3:]}", "source": "test"}
                        for name in keys]

        screen = WhaleScreener("placeholder", lambda _: None, lambda _: None)
        poller = WhalePoller("", screen, key_store=Store(),
                             state_file=Path(tmp) / "poll.json",
                             monthly_cu=monthly_cu,
                             # префикс /v2/ — как у боевого Alchemy: ключ дописывается
                             # в конец URL, по нему и проверяем, каким ключом заплатили
                             # https + /v2/ — как у боевого Alchemy: ключ дописывается в
                             # конец URL, поэтому по нему и видно, каким ключом заплатили
                             endpoints={"ETH": "https://g.alchemy.test/v2/"})
        poller.state["month"] = time.strftime("%Y-%m", time.gmtime())
        return screen, poller

    def test_only_active_key_is_billed(self):
        import asyncio

        async def scenario(tmp):
            screen, poller = self.make_poller(tmp)
            try:
                billed = []

                def handler(url, body):
                    billed.append(url.split("secret-")[-1])
                    return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": "0x10"})

                session = FakeSession(handler)
                for _ in range(4):
                    await poller._rpc(session, "ETH", "eth_blockNumber", [])
                # три ключа в пуле, а платит один
                self.assertEqual(set(billed), {"k1"})
                usage = poller.state["key_usage"]
                self.assertEqual(usage.get("k1"), 40)
                self.assertNotIn("k2", usage)
                self.assertNotIn("k3", usage)
                states = {row["id"]: row["state"] for row in poller.key_status()}
                self.assertEqual(states, {"k1": "active", "k2": "reserve", "k3": "reserve"})
            finally:
                screen.close()

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(scenario(tmp))

    def test_exhausted_key_hands_over_once_and_is_not_touched_again(self):
        import asyncio

        async def scenario(tmp):
            screen, poller = self.make_poller(tmp, monthly_cu=25)
            try:
                billed = []

                def handler(url, body):
                    billed.append(url.split("secret-")[-1])
                    return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": "0x10"})

                session = FakeSession(handler)
                await poller._rpc(session, "ETH", "eth_blockNumber", [])   # 10 CU из 25
                await poller._rpc(session, "ETH", "eth_blockNumber", [])   # 20 CU
                # третий запрос квоту k1 уже не влезает: платит k2, и только он
                await poller._rpc(session, "ETH", "eth_blockNumber", [])
                self.assertEqual(billed, ["k1", "k1", "k2"])
                self.assertEqual(poller.state["active_key"], "k2")
                self.assertTrue(poller.key_locally_exhausted("k1"))
                self.assertEqual(poller.state["active_key"], "k2")
                # четвёртый запрос — снова k2: k1 до конца месяца не трогает
                await poller._rpc(session, "ETH", "eth_blockNumber", [])
                self.assertEqual(billed[-1], "k2")
                rows = {row["id"]: row for row in poller.key_status()}
                self.assertEqual(rows["k1"]["state"], "exhausted")
                self.assertIn("исчерпана", rows["k1"]["reason"])
                self.assertEqual(rows["k2"]["state"], "active")
                self.assertEqual(rows["k3"]["state"], "reserve")
            finally:
                screen.close()

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(scenario(tmp))

    def test_single_request_never_walks_the_whole_pool(self):
        import asyncio

        async def scenario(tmp):
            screen, poller = self.make_poller(tmp, monthly_cu=10_000)
            try:
                def handler(url, body):
                    # k1 жив, но отвечает 429 → уходим на k2; k3 не дёргаем вовсе
                    if url.endswith("secret-k1"):
                        return FakeResponse({}, status=429, body="too many requests")
                    return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": "0x1"})

                session = FakeSession(handler)
                await poller._rpc(session, "ETH", "eth_blockNumber", [])
                self.assertNotIn("secret-k3", session.url)
                self.assertTrue(session.url.endswith("secret-k2"))
                usage = poller.state["key_usage"]
                self.assertNotIn("k3", usage)
                # k1 припаркован на 429 → следующий запрос пойдёт k2; k3 не трогаем
                self.assertEqual(poller.resolve_key()[0], 1)
                self.assertEqual(sorted(usage), ["k1", "k2"])
            finally:
                screen.close()

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(scenario(tmp))

    def test_streams_share_the_one_working_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            screen, poller = self.make_poller(tmp)
            try:
                keys = poller._keys()
                first, _key, index, wait = poller._stream_key("ETH", keys)
                for chain in ("BNB", "POLYGON", "ARBITRUM", "BASE"):
                    other = poller._stream_key(chain, keys)
                    self.assertEqual(other[0], first, chain)
                    self.assertEqual(other[2], index, chain)
                self.assertEqual(index, 0)
                self.assertEqual(wait, 0.0)
                # наказание ключа уводит весь пул, а не одну сеть
                poller._mark_key_paused("ETH", first)
                advanced = poller._stream_key("BASE", keys)[0]
                self.assertNotEqual(advanced, first)
            finally:
                screen.close()

    def test_idle_watchdog_moves_to_the_next_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            screen, poller = self.make_poller(tmp)
            try:
                poller.last_data_at = time.time()
                self.assertEqual(poller.resolve_key()[0], 0)   # пока данные есть — k1
                self.assertEqual(poller.maybe_failover_idle(), "")
                self.assertEqual(poller.resolve_key()[0], 0)
                # тишина дольше порога → k2, а k1 коротко припаркован
                poller._started_at = time.time() - 10_000
                poller.last_data_at = time.time() - 10_000
                poller.last_key_switch_at = 0.0
                chosen = poller.maybe_failover_idle()
                self.assertEqual(chosen, "k2")
                self.assertEqual(poller.state["active_key"], "k2")
                self.assertEqual(poller.resolve_key()[0], 1)
                self.assertGreater(poller.key_cooldown.get("k1", 0), time.time())
                # сразу ещё раз не прыгает: ждём новое окно тишины
                self.assertEqual(poller.maybe_failover_idle(), "")
            finally:
                screen.close()

    def test_single_key_pool_never_fails_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            screen, poller = self.make_poller(tmp, keys=("only",))
            try:
                poller._started_at = 0.0
                poller.last_data_at = 0.0
                self.assertEqual(poller.maybe_failover_idle(), "")
                self.assertEqual(poller.resolve_key()[0], 0)
            finally:
                screen.close()

    def test_month_rollover_returns_to_the_first_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            screen, poller = self.make_poller(tmp, monthly_cu=25)
            try:
                poller.state["active_key"] = "k2"
                poller.state.setdefault("key_exhausted", {})["k1"] = "2000-01"
                poller._reserve("eth_blockNumber", "k1", chain="ETH")   # новый месяц → ledger чист
                self.assertEqual(poller.state["month"], time.strftime("%Y-%m", time.gmtime()))
                # счётчик месяца обнулился, «исчерпанность» снята, выбор снова с первого
                self.assertFalse(poller.key_locally_exhausted("k1"))
                self.assertEqual(poller.state["key_usage"]["k1"], 10)
                self.assertEqual(poller.resolve_key()[0], 0)
            finally:
                screen.close()

    def test_status_exposes_pool_and_network_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            screen, poller = self.make_poller(tmp)
            switch = NetworkSwitch(None, enabled=("ETH",))
            poller.networks = switch
            try:
                poller.network_status.setdefault("BNB", {"status": "waiting", "retry_at": 0.0})
                status = poller.status()
                self.assertEqual(status["enabled_networks"], ["ETH"])
                self.assertEqual(status["network_status"]["BNB"]["status"], "disabled")
                self.assertFalse(status["network_status"]["BNB"]["enabled"])
                self.assertEqual(status["network_status"]["ETH"]["enabled"], True)
                self.assertEqual(status["key_pool"]["active_key_id"], "k1")
                self.assertEqual(len(status["key_pool"]["reserve"]), 2)
                self.assertEqual([row["id"] for row in status["network_switch"]
                                  if row["enabled"]], ["ETH"])
                self.assertNotIn("secret-", json.dumps(status))
            finally:
                screen.close()

    def test_disabled_network_does_not_start_a_stream(self):
        import asyncio

        class NoSession:
            """Сеть выключена — сессию не открываем вовсе."""

            def __init__(self, *_args, **_kwargs):
                raise AssertionError("выключенная сеть не должна ни к чему подключаться")

        async def scenario(poller):
            async def no_stagger(*_args, **_kwargs):
                return None

            poller._wait_for_staggered_start = no_stagger   # иначе ждём свою очередь
            task = asyncio.create_task(poller._run_evm_ws_chain("ETH"))
            await asyncio.sleep(0.3)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return poller.evm_streams["ETH"].copy()

        with tempfile.TemporaryDirectory() as tmp:
            screen, poller = self.make_poller(tmp)
            poller.networks = NetworkSwitch(None, enabled=("BNB",))   # ETH выключена
            try:
                with patch("whale_poller.aiohttp.ClientSession", NoSession):
                    row = asyncio.run(scenario(poller))
                self.assertEqual(row["state"], "disabled")
                self.assertFalse(row["connected"])
                self.assertEqual(row["subscriptions"], 0)
            finally:
                screen.close()

    def test_poller_gate_is_permissive_without_a_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            screen, poller = self.make_poller(tmp)
            try:
                self.assertTrue(all(poller.network_enabled(chain) for chain in
                                    ("ETH", "BNB", "SOLANA", "TRON", "HYPERLIQUID")))
                self.assertEqual(poller.enabled_chains(), ("ETH",))
            finally:
                screen.close()


class ScreenerVisibilityTests(unittest.TestCase):
    def event(self, chain, usd=500_000, address=None, ts=None):
        return {"chain": chain, "hash": "0x" + "a" * 64, "log_index": "0x1",
                "timestamp": ts or time.time(), "symbol": "USDT", "amount": usd / 1.0,
                "usd": usd, "from": address or f"0x{'1' * 40}", "to": "0x" + "2" * 40,
                "from_label": "Binance 14", "to_label": "", "direction": "outflow",
                "source": "historical"}

    def store(self, tmp):
        return WhaleHistoryStore(Path(tmp) / "events.sqlite3")

    def test_query_filters_by_visible_chains(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.store(tmp)
            for chain in ("ETH", "BNB", "SOLANA"):
                store.append(self.event(chain, address=f"0x{len(chain) * 3:0>2x}" + "0" * 38))
            try:
                rows, total = store.query(chains=("ETH",))
                self.assertEqual(total, 1)
                self.assertEqual({row["chain"] for row in rows}, {"ETH"})
                rows, total = store.query(chains=("ETH", "BNB"))
                self.assertEqual(total, 2)
                rows, total = store.query(chains=())
                self.assertEqual((rows, total), ([], 0))
                # без тумблера (None) — фильтр не применяется вовсе
                rows, total = store.query()
                self.assertEqual(total, 3)
                # явный запрос выключенной сети — пустой ответ, а не «всё подряд»
                rows, total = store.query(chain="SOLANA", chains=("ETH",))
                self.assertEqual(total, 0)
            finally:
                store.close()

    def test_active_wallet_keys_match_the_registry_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.store(tmp)
            mine = "0x" + "aB" * 20
            store.append(self.event("ETH", address=mine))
            store.append(self.event("TRON", address="T" + "1" * 33, ts=time.time() - 8 * 86400))
            try:
                active = store.active_wallet_keys(time.time() - 7 * 86400)
                self.assertIn(("ETH", mine.lower()), active)
                self.assertNotIn(("TRON", "T" + "1" * 33), active)   # старше окна
                self.assertIn(("ETH", ("0x" + "2" * 40)), active)    # получатель тоже счёт
            finally:
                store.close()

    def test_screener_history_follows_the_switch(self):
        switch = NetworkSwitch(None, enabled=("TRON",))
        screen = WhaleScreener("placeholder", lambda _: None, lambda _: None,
                               networks=switch)
        try:
            screen.events.extend([self.event("ETH"), self.event("TRON")])
            self.assertEqual({ev["chain"] for ev in screen.history(0, "ALL", 50)}, {"TRON"})
            self.assertEqual({ev["chain"] for ev in screen.events_since(0)}, {"TRON"})
            rows, total = screen.query_history(since=0, chain="ALL", page_size=50)
            self.assertEqual(total, 1)
            self.assertEqual(rows[0]["chain"], "TRON")
            self.assertEqual(screen.query_history(since=0, chain="ETH")[1], 0)
            switch.set_enabled("ETH", True)
            self.assertEqual(len(screen.history(0, "ALL", 50)), 2)
        finally:
            screen.close()

    def test_hyperliquid_status_reports_the_switch(self):
        screen = WhaleScreener("placeholder", lambda _: None, lambda _: None,
                               networks=NetworkSwitch(None, enabled=("ETH",)))
        try:
            self.assertEqual(screen.native_status()["state"], "disabled")
            self.assertFalse(screen.native_status()["enabled"])
            screen.networks.set_enabled("HYPERLIQUID", True)
            self.assertNotEqual(screen.native_status()["state"], "disabled")
        finally:
            screen.close()


class WalletPruneTests(unittest.TestCase):
    def row(self, chain, address, name, updated_at, source="defillama"):
        return normalize_wallet_record({"chain": chain, "address": address, "name": name,
                                        "source": source, "updated_at": int(updated_at)})

    def registry(self, tmp, rows, manual=()):
        path = Path(tmp) / "cex_wallets.json"
        path.write_text(json.dumps({"version": 2, "updated_at": int(time.time()),
                                    "automatic": list(rows), "manual": list(manual)}),
                         encoding="utf-8")
        return CEXWalletRegistry(path)

    def test_prune_removes_only_silent_and_unpublished(self):
        now = time.time()
        week_ago = now - 8 * 86400
        fresh = self.row("ETH", "0x" + "1" * 40, "Binance 1", now)
        stale = self.row("ETH", "0x" + "2" * 40, "Old Hot Wallet", week_ago)
        stale_but_active = self.row("BNB", "0x" + "3" * 40, "Binance 9", week_ago)
        manual_row = self.row("ETH", "0x" + "4" * 40, "My own wallet", week_ago, "manual")
        with tempfile.TemporaryDirectory() as tmp:
            registry = self.registry(tmp, [fresh, stale, stale_but_active], manual=[manual_row])
            self.assertEqual(len(registry.records()), 4)
            # предпросмотр без учёта активности: два молчуна, один из них «живой»
            preview = registry.prune_stale(cutoff=now - 7 * 86400, apply=False)
            self.assertEqual(preview["removed"], 2)
            self.assertFalse(preview["applied"])
            self.assertEqual(len(registry.records()), 4)

            result = registry.prune_stale(cutoff=now - 7 * 86400,
                                          active_keys={("BNB", stale_but_active["address"])})
            self.assertEqual(result["manual_kept"], 1)
            self.assertEqual(result["removed"], 1)
            self.assertTrue(result["applied"])
            kept = {(row["chain"], row["address"]) for row in registry.records()}
            self.assertEqual(kept, {(fresh["chain"], fresh["address"]),
                                    (stale_but_active["chain"], stale_but_active["address"]),
                                    (manual_row["chain"], manual_row["address"])})
            # файл на диске тоже поправлен
            reloaded = CEXWalletRegistry(registry.path)
            self.assertEqual(len(reloaded.records()), 3)

    def test_mass_deletion_is_refused(self):
        # страховка включается только на больших потерях: до 50 строк за проход
        # можно (обычная плановая чистка), дальше — не больше половины автосписка
        week_ago = time.time() - 30 * 86400
        rows = [self.row("ETH", f"0x{index:040x}", f"Wallet {index}", week_ago)
                for index in range(1, 121)]
        rows.append(self.row("ETH", "0x" + "f" * 40, "Fresh", time.time()))
        with tempfile.TemporaryDirectory() as tmp:
            registry = self.registry(tmp, rows)
            result = registry.prune_stale(cutoff=time.time() - 7 * 86400)
            self.assertFalse(result["ok"])
            self.assertEqual(result["removed"], 0)
            self.assertIn("слишком много", result["reason"])
            self.assertEqual(len(registry.records()), 121)
            small = self.registry(tmp, rows[:20] + [rows[-1]])
            ok = small.prune_stale(cutoff=time.time() - 7 * 86400)
            self.assertTrue(ok["ok"], ok)
            self.assertEqual(ok["removed"], 20)

    def test_stale_count_and_summary_fields(self):
        rows = [self.row("ETH", f"0x{index:040x}", f"W{index}", time.time() - 30 * 86400)
                for index in range(1, 6)]
        rows.append(self.row("BNB", "0x" + "e" * 40, "Fresh", time.time()))
        with tempfile.TemporaryDirectory() as tmp:
            registry = self.registry(tmp, rows)
            self.assertEqual(registry.stale_count(cutoff=time.time() - 7 * 86400), 5)
            preview = registry.prune_stale(cutoff=time.time() - 7 * 86400, apply=False)
            self.assertEqual(preview["by_chain"], {"ETH": 5})
            self.assertTrue(preview["ok"])
            summary = registry.summary()
            self.assertEqual(summary["total"], 6)
            self.assertEqual(summary["automatic"], 6)
            self.assertLess(summary["oldest_updated_at"], time.time() - 29 * 86400)
            # предпросмотр не пишет «последняя чистка»
            self.assertEqual(summary["last_prune"], {})

    def test_prune_cex_wallets_endpoint_logic(self):
        import asyncio
        import server

        with tempfile.TemporaryDirectory() as tmp:
            rows = [self.row("ETH", "0x" + "5" * 40, "Binance 2", time.time() - 30 * 86400),
                    self.row("ETH", "0x" + "6" * 40, "Binance 3", time.time())]
            registry = self.registry(tmp, rows)
            switch = NetworkSwitch(None, enabled=("ETH",), retention=7)
            screen = WhaleScreener("placeholder", lambda _: None, lambda _: None)
            try:
                with patch.object(server, "cex_wallet_registry", registry), \
                     patch.object(server, "screener_networks", switch), \
                     patch.object(server, "whale_screener", screen):
                    dry = server.prune_cex_wallets(apply=False)
                    self.assertEqual(dry["retention_days"], 7)
                    self.assertEqual(dry["stale"], 1)
                    self.assertFalse(dry["applied"])
                    wet = server.prune_cex_wallets()
                    self.assertEqual(wet["removed"], 1)
                    self.assertEqual(len(registry.records()), 1)
                    # выключенная очистка — ничего не трогаем
                    switch.set_retention(0)
                    off = server.prune_cex_wallets()
                    self.assertEqual(off["reason"], "автоочистка выключена")
                    self.assertEqual(len(registry.records()), 1)
            finally:
                screen.close()


class RoutesTests(unittest.TestCase):
    """Живые маршруты: тумблер сети, чистка базы кошельков, пресеты кабинета."""

    def setUp(self):
        from fastapi.testclient import TestClient
        import server
        self.server = server
        self.client = TestClient(server.app)
        self._admin = patch.object(server, "current_user",
                                   lambda request: {"id": 1, "is_admin": True})

    def as_admin(self):
        return self._admin

    def test_network_toggle_roundtrip_and_unknown_chain(self):
        import server
        with tempfile.TemporaryDirectory() as tmp:
            switch = NetworkSwitch(Path(tmp) / "networks.json",
                                   enabled=("ETH", "TRON", "HYPERLIQUID"))
            screen = WhaleScreener("placeholder", lambda _: None, lambda _: None,
                                   networks=switch)
            try:
                with self.as_admin(), patch.object(server, "screener_networks", switch), \
                     patch.object(server, "whale_screener", screen):
                    payload = self.client.get("/api/screener/networks").json()
                    self.assertEqual(payload["enabled"], ["ETH", "TRON", "HYPERLIQUID"])
                    self.assertEqual(payload["wallet_retention_days"], 7)
                    res = self.client.post("/api/admin/screener/networks",
                                          json={"chain": "BNB Chain", "enabled": True})
                    self.assertEqual(res.status_code, 200, res.text)
                    self.assertEqual(res.json()["applied"],
                                     [{"chain": "BNB", "enabled": True, "changed": True}])
                    self.assertTrue(switch.is_enabled("BNB"))
                    self.assertEqual(
                        self.client.get("/api/screener/stats").json()["enabled_networks"],
                        ["ETH", "BNB", "TRON", "HYPERLIQUID"])
                    # пачкой: выключить SOLANA и включить POLYGON одним запросом
                    bulk = self.client.post("/api/admin/screener/networks", json={
                        "networks": {"SOLANA": False, "POLYGON": True}}).json()["applied"]
                    self.assertEqual({row["chain"] for row in bulk}, {"SOLANA", "POLYGON"})
                    self.assertFalse(switch.is_enabled("SOLANA"))
                    self.assertTrue(switch.is_enabled("POLYGON"))
                    self.assertEqual(self.client.post("/api/admin/screener/networks",
                                                     json={"chain": "MARS"}).status_code, 422)
                    self.assertEqual(self.client.post("/api/admin/screener/networks",
                                                     json={}).status_code, 422)
                    # значение переживает «перезапуск» процесса
                    self.assertEqual(NetworkSwitch(Path(tmp) / "networks.json").enabled(),
                                     ("ETH", "BNB", "POLYGON", "TRON", "HYPERLIQUID"))
            finally:
                screen.close()

    def test_guests_and_non_admins_cannot_mutate(self):
        for path, body in (("/api/admin/screener/networks", {"chain": "ETH", "enabled": False}),
                           ("/api/admin/screener/cex-wallets/prune", {}),
                           ("/api/admin/screener/cex-wallets/retention", {"days": 3})):
            self.assertEqual(self.client.post(path, json=body).status_code, 403, path)

    def test_admin_stats_carry_switch_and_key_pool(self):
        import server
        with tempfile.TemporaryDirectory() as tmp:
            switch = NetworkSwitch(Path(tmp) / "n.json", enabled=("TRON",))
            screen = WhaleScreener("placeholder", lambda _: None, lambda _: None,
                                   networks=switch)
            poller = WhalePoller("", screen, state_file=Path(tmp) / "p.json",
                                 networks=switch)
            try:
                with self.as_admin(), patch.object(server, "screener_networks", switch), \
                     patch.object(server, "whale_screener", screen), \
                     patch.object(server, "whale_poller", poller):
                    payload = self.client.get("/api/admin/alchemy/stats").json()
                    self.assertEqual([row["chain"] for row in payload["networks"]
                                      if row.get("enabled")], ["TRON"])
                    eth = next(row for row in payload["networks"] if row["chain"] == "ETH")
                    self.assertEqual(eth["status"], "disabled")
                    self.assertEqual(payload["network_switch"]["enabled"], ["TRON"])
                    self.assertEqual(payload["key_pool"]["keys_configured"], 0)
                    whales = self.client.get("/api/screener/whales").json()
                    self.assertEqual(whales["enabled_networks"], ["TRON"])
                    # ни одна платная сеть не включена → Alchemy не «работает»
                    self.assertFalse(whales["alchemy_enabled"])
                    self.assertTrue(whales["tron_enabled"])
            finally:
                screen.close()

    def test_wallet_prune_and_retention_routes(self):
        import server
        fixture = WalletPruneTests()
        with tempfile.TemporaryDirectory() as tmp:
            rows = [fixture.row("ETH", "0x" + "7" * 40, "Binance 4", time.time() - 40 * 86400),
                    fixture.row("ETH", "0x" + "8" * 40, "Binance 5", time.time())]
            registry = fixture.registry(tmp, rows)
            switch = NetworkSwitch(Path(tmp) / "n.json", enabled=("ETH",), retention=7)
            screen = WhaleScreener("placeholder", lambda _: None, lambda _: None,
                                   networks=switch)
            try:
                with self.as_admin(), patch.object(server, "cex_wallet_registry", registry), \
                     patch.object(server, "screener_networks", switch), \
                     patch.object(server, "whale_screener", screen), \
                     patch.object(server, "whale_poller", None):
                    listed = self.client.get("/api/admin/screener/cex-wallets").json()
                    self.assertEqual(listed["cleanup"]["retention_days"], 7)
                    self.assertEqual(listed["cleanup"]["stale"], 1)
                    preview = self.client.post("/api/admin/screener/cex-wallets/prune",
                                               json={"dry_run": True}).json()
                    self.assertTrue(preview["dry_run"])
                    self.assertEqual(preview["removed"], 1)
                    self.assertEqual(len(registry.records()), 2)
                    # «почистить сейчас» из админки летит без тела — маршрут обязан принять
                    done = self.client.post("/api/admin/screener/cex-wallets/prune").json()
                    self.assertEqual(done["removed"], 1)
                    self.assertEqual(len(registry.records()), 1)
                    self.assertEqual(self.client.post(
                        "/api/admin/screener/cex-wallets/retention",
                        json={"days": 30}).json()["wallet_retention_days"], 30)
                    self.assertEqual(switch.wallet_retention_days, 30)
                    self.assertEqual(self.client.post(
                        "/api/admin/screener/cex-wallets/retention",
                        json={"days": "сто"}).status_code, 422)
            finally:
                screen.close()

    def test_prune_loop_survives_an_exception(self):
        import asyncio
        import server

        calls = []

        def fake_prune(*, apply: bool = True):
            calls.append(apply)
            raise RuntimeError("провайдер лёг")

        async def scenario():
            original = server.prune_cex_wallets
            server.prune_cex_wallets = fake_prune
            delay = server.WALLET_PRUNE_START_DELAY_SEC
            interval = server.WALLET_PRUNE_INTERVAL_SEC
            server.WALLET_PRUNE_START_DELAY_SEC = 0.0
            server.WALLET_PRUNE_INTERVAL_SEC = 0.05
            task = asyncio.create_task(server.cex_wallet_prune_loop())
            await asyncio.sleep(0.3)
            alive = not task.done()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            server.prune_cex_wallets = original
            server.WALLET_PRUNE_START_DELAY_SEC = delay
            server.WALLET_PRUNE_INTERVAL_SEC = interval
            return alive

        # исключение внутри прогона не убивает задачу: цикл жив и спит до suivante
        self.assertTrue(asyncio.run(scenario()))
        self.assertEqual(calls[:1], [True])
        self.assertLessEqual(len(calls), 2)


class CabinetRoutesTests(unittest.TestCase):
    """Кабинет: список сетей в настройке сигналов = то, что оставил админ."""

    def setUp(self):
        import web_account
        from accounts import Store
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        self.web_account = web_account
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="test-secret")
        self.user = self.store.upsert_telegram_user(
            {"id": 888, "first_name": "Kit", "language_code": "ru"})
        self._saved = (web_account.ctx.store, web_account.ctx.require_email_verification,
                       web_account.ctx.screener_signal_fn, web_account.ctx.screener_networks_fn,
                       web_account.current_user)
        web_account.ctx.store = self.store
        web_account.ctx.require_email_verification = False
        web_account.ctx.screener_signal_fn = lambda cfg=None: {
            "available": True, "exchanges": [], "recent": []}
        web_account.current_user = lambda request: self.user
        self.app = FastAPI()
        web_account.register_account_routes(self.app)
        self.client = TestClient(self.app)

    def tearDown(self):
        (self.web_account.ctx.store, self.web_account.ctx.require_email_verification,
         self.web_account.ctx.screener_signal_fn, self.web_account.ctx.screener_networks_fn,
         self.web_account.current_user) = self._saved
        self.store.close()
        self.tmp.cleanup()

    def test_presets_follow_the_switch(self):
        from screener_signals import signal_presets
        self.web_account.ctx.screener_networks_fn = lambda: ("ETH", "TRON")
        payload = self.client.get("/api/account/screener/signals").json()
        self.assertEqual(payload["presets"]["chains"], ["ETH", "TRON"])
        self.assertEqual(payload["networks"], ["ETH", "TRON"])
        self.assertEqual(sorted(payload["presets"]["network_titles"].values()),
                         ["Ethereum", "TRON"])

        self.web_account.ctx.screener_networks_fn = lambda: None
        full = self.client.get("/api/account/screener/signals").json()
        self.assertEqual(full["presets"]["chains"], signal_presets()["chains"])
        self.assertEqual(full["networks"], [])

    def test_saved_choice_of_a_disabled_network_survives(self):
        # настройка не портится: сеть можно выключить, а потом включить — порог останется
        self.web_account.ctx.screener_networks_fn = lambda: ("ETH",)
        posted = self.client.post("/api/account/screener/signals", json={
            "notify": True, "exchange": "ALL", "direction": "all", "chain": "TRON",
            "min_usd": 900_000, "cooldown_min": 5}).json()
        self.assertEqual(posted["config"]["chain"], "TRON")
        self.assertEqual(posted["config"]["min_usd"], 900_000.0)
        got = self.client.get("/api/account/screener/signals").json()
        self.assertEqual(got["config"]["chain"], "TRON")
        self.assertNotIn("TRON", got["presets"]["chains"])


class AdminStringsTests(unittest.TestCase):
    """Каждый новый ключ админки переведён на все пять языков и не остался калёкой."""

    LANGS = ("ru", "en", "zh", "hi", "es")
    PREFIXES = ("adm.networks_switch_", "adm.alchemy_pool_", "adm.cex_wallet_retention",
                "adm.cex_wallet_prune", "adm.cex_wallet_cleanup", "adm.alchemy_reserve",
                "adm.alchemy_exhausted")

    def used_keys(self):
        used = set()
        source = Path("static/whale_admin.js").read_text(encoding="utf-8")
        for match in __import__("re").finditer(r't\("([a-z]+\.[a-z0-9_]+)"', source):
            used.add(match.group(1))
        html = Path("static/admin.html").read_text(encoding="utf-8")
        for match in __import__("re").finditer(r'data-i18n(?:-placeholder)?="([a-z]+\.[a-z0-9_]+)"', html):
            used.add(match.group(1))
        return used

    def test_new_keys_exist_and_are_translated(self):
        blocks = {lang: json.loads(Path(f"static/i18n/{lang}.json").read_text(encoding="utf-8"))["keys"]
                  for lang in self.LANGS}
        new_keys = [key for key in blocks["ru"] if key.startswith(self.PREFIXES)]
        self.assertGreaterEqual(len(new_keys), 30)
        ru = blocks["ru"]
        for key in new_keys:
            for lang in self.LANGS:
                self.assertIn(key, blocks[lang], f"{lang}: {key}")
                value = blocks[lang][key]
                self.assertTrue(value and value.strip(), f"{lang}: {key} пустая")
                if lang != "ru":
                    self.assertNotEqual(value, key, f"{lang}: {key} = сам ключ")
                self.assertEqual(sorted(__import__("re").findall(r"\{(\w+)\}", value)),
                                 sorted(__import__("re").findall(r"\{(\w+)\}", ru[key])),
                                 f"{lang}: набор подстановок в {key}")

    def test_every_used_admin_key_is_known(self):
        keys = json.loads(Path("static/i18n/ru.json").read_text(encoding="utf-8"))["keys"]
        pages = Path("static/i18n.pages.js").read_text(encoding="utf-8")
        for key in sorted(self.used_keys()):
            if not key.startswith(("adm.", "screener.")):
                continue
            self.assertIn(key, keys, f"нет перевода для {key}")
            self.assertIn(key, pages, f"{key} не попал в i18n.pages.js")


if __name__ == "__main__":
    unittest.main(verbosity=2)
