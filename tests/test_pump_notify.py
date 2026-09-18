"""Серверная часть «Сторожа монет»: подписчики и рассылка сигналов.

Проверяем связку server.py: кто считается подписчиком сервиса «watchlist»,
как настраивается режим/порог, что сигнал уходит в Telegram с реф-ссылкой на
Gate и что кулдаун не даёт спамить одной и той же монетой.

Запуск: /tmp/venv/bin/python tests/test_pump_notify.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

os.environ.setdefault("LIQSCOPE_ACCOUNTS_DB", "")
os.environ["LIQSCOPE_ACCOUNTS_DB"] = ""  # подменим ниже на временную базу
os.environ["LIQSCOPE_PUMPS"] = "off"

import server as srv  # noqa: E402
from accounts import Store  # noqa: E402

T0 = 1789596000.0


class PumpNotifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "a.db"), secret="s")
        self._old_store = srv.account_store
        srv.account_store = self.store
        srv.PUMP_SIGNALS.clear()
        srv.PUMP_LAST_FIRED.clear()
        srv.PUMPS = None
        self.sent: list = []
        self._old_bot = srv.tg_bot

        class FakeBot:
            running = True
            pump_snapshot_fn = None

            def __init__(self):
                # Бот знает язык получателя и переводит исходящее сам:
                # заглушка повторяет этот интерфейс, иначе проверка падает
                # не на логике сигналов, а на отсутствующем методе.
                self.langs = {}

            def remember_lang(self, tg_id, lang):
                self.langs[int(tg_id)] = "en" if str(lang or "").startswith("en") else "ru"
                return self.langs[int(tg_id)]

            def warm_langs(self, rows):
                for row in rows or []:
                    if isinstance(row, dict) and (row.get("tg_id") or row.get("chat_id")):
                        self.remember_lang(row.get("tg_id") or row.get("chat_id"),
                                           row.get("language") or row.get("lang") or "")

            async def send(self, chat_id, text, markup=None, parse=None,
                           silent=False, raw=False):
                self_outer.sent.append({"chat_id": chat_id, "text": text,
                                        "markup": markup})
                return 1

        self_outer = self
        srv.tg_bot = FakeBot()

    def tearDown(self):
        srv.account_store = self._old_store
        srv.tg_bot = self._old_bot
        srv.PUMP_SIGNALS.clear()
        srv.PUMP_LAST_FIRED.clear()
        srv.PUMPS = None
        self.tmp.cleanup()

    def _subscribe(self, tg_id=777, lang="ru", **cfg):
        user = self.store.create_email_user(f"u{tg_id}@example.com",
                                            password_hash="x")["user"]
        uid = int(user["id"])
        r = self.store.link_tg_to_user(uid, {"tg_id": tg_id, "username": "u",
                                             "first_name": "U",
                                             "language_code": lang})
        self.assertTrue(r.get("ok"), r)
        base = {"enabled": True, "mode": "both", "period": "5m", "candles": 3,
                "threshold": 10, "min_vol": 0, "cooldown_min": 10}
        base.update(cfg)
        r = self.store.set_user_service_config(uid, "watchlist", base,
                                               enabled=True)
        self.assertTrue(r.get("ok"), r)
        return uid

    def _feed_min(self, scanner, sym, minutes, start, step, volume=5_000_000.0):
        for i in range(minutes):
            scanner.add_prices({sym: start + step * i}, ts=T0 + 60 * i,
                               volume={sym: volume})

    # --- подписчики --------------------------------------------------------
    def test_subscribers_only_enabled(self):
        self._subscribe(tg_id=777)
        self._subscribe(tg_id=778, enabled=False)
        rows = srv.pump_watchers()
        self.assertEqual([r["tg_id"] for r in rows], [777])
        self.assertEqual(rows[0]["lang"], "ru")
        self.assertTrue(rows[0]["pump"]["enabled"])

    def test_subscriber_language(self):
        self._subscribe(tg_id=779, lang="en-US")
        self.assertEqual(srv.pump_watchers()[0]["lang"], "en")

    def test_snapshot_shape(self):
        from pump_scan import PumpScanner
        scan = PumpScanner()
        self._feed_min(scan, "SOL_USDT", 7, 100.0, 5.0)
        srv.PUMPS = scan
        snap = srv.pump_snapshot({"mode": "pump", "period": "1m", "candles": 3,
                                  "threshold": 20})
        self.assertTrue(snap["config"]["enabled"])
        self.assertEqual(snap["config"]["mode"], "pump")
        self.assertIn("hits", snap)
        self.assertIn("movers", snap)
        self.assertIn("status", snap)
        self.assertTrue(snap["periods"] and snap["thresholds"] and snap["candles"])
        self.assertTrue(snap["settings_text"])

    # --- рассылка ----------------------------------------------------------
    def test_signal_goes_to_telegram_with_gate_link(self):
        import pump_scan
        scan = pump_scan.PumpScanner()
        srv.PUMPS = scan
        self._subscribe(tg_id=777, mode="pump", period="5m", candles=3,
                        threshold=10)
        # рост с 100 до 140 — памп 40%
        self._feed_min(scan, "SOL_USDT", 20, 100.0, 5.0)
        with _frozen_time(T0 + 60 * 20):
            asyncio.run(srv.pump_notify())
        self.assertTrue(self.sent, "сигнал не ушёл")
        msg = self.sent[0]
        self.assertEqual(msg["chat_id"], 777)
        self.assertIn("SOL", msg["text"])
        self.assertIn("62.50%", msg["text"])
        urls = [b.get("url") for row in msg["markup"]["inline_keyboard"] for b in row]
        self.assertTrue(any("gate.com" in (u or "") for u in urls), urls)
        self.assertEqual(len(srv.PUMP_SIGNALS), 1)

    def test_cooldown_blocks_repeat(self):
        import pump_scan
        scan = pump_scan.PumpScanner()
        srv.PUMPS = scan
        self._subscribe(tg_id=777, mode="pump", period="5m", candles=3,
                        threshold=10, cooldown_min=30)
        self._feed_min(scan, "SOL_USDT", 20, 100.0, 5.0)
        with _frozen_time(T0 + 60 * 20):
            asyncio.run(srv.pump_notify())
        self.assertEqual(len(self.sent), 1)
        with _frozen_time(T0 + 420):
            asyncio.run(srv.pump_notify())
        self.assertEqual(len(self.sent), 1, "повторный сигнал должен ждать кулдаун")
        with _frozen_time(T0 + 60 * 20 + 31 * 60):
            asyncio.run(srv.pump_notify())
        self.assertEqual(len(self.sent), 2)

    def test_dump_mode_is_respected(self):
        import pump_scan
        scan = pump_scan.PumpScanner()
        srv.PUMPS = scan
        self._subscribe(tg_id=777, mode="pump", period="5m", candles=3,
                        threshold=10)
        self._feed_min(scan, "ETH_USDT", 20, 200.0, -8.0)
        with _frozen_time(T0 + 60 * 20):
            asyncio.run(srv.pump_notify())
        self.assertEqual(self.sent, [], "режим «пампы» не должен слать дампы")

    def test_no_subscribers_no_sending(self):
        import pump_scan
        scan = pump_scan.PumpScanner()
        srv.PUMPS = scan
        self._feed_min(scan, "SOL_USDT", 20, 100.0, 5.0)
        with _frozen_time(T0 + 60 * 20):
            asyncio.run(srv.pump_notify())
        self.assertEqual(self.sent, [])

    def test_bot_offline_keeps_silence(self):
        import pump_scan
        scan = pump_scan.PumpScanner()
        srv.PUMPS = scan
        self._subscribe(tg_id=777)
        srv.tg_bot.running = False
        self._feed_min(scan, "SOL_USDT", 20, 100.0, 5.0)
        with _frozen_time(T0 + 60 * 20):
            asyncio.run(srv.pump_notify())
        self.assertEqual(self.sent, [])


class _frozen_time:
    """Подменяет time.time() внутри server.py на фиксированное значение."""

    def __init__(self, ts: float):
        self.ts = ts

    def __enter__(self):
        self._orig = srv.time.time
        srv.time.time = lambda: self.ts
        return self

    def __exit__(self, *exc):
        srv.time.time = self._orig
        return False


if __name__ == "__main__":
    unittest.main(verbosity=2)
