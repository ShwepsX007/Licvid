"""Публикации на сайте — только то, что реально вышло.

Раньше `/hourly` и `/digest` подмешивали «сводки» и «выпуски», собранные из
месячных свёрток: каждый час с ликвидациями превращался в пост из одной
строки цифр — без текста, без фото и с ярлыком «из архива», хотя в канал
ничего не уходило. Свежие сутки при этом выглядели как дайджест не в своё
время: свёртка собиралась в тот час, когда её прочитали, — задолго до
вечернего выпуска.

Правило теперь простое: на страницах публикаций видно только то, что сохранил
бот (JSON-архив). Свёртки месяца остаются источником цифр — лента, стенд,
боксы статистики, CVD — но не публикаций.

Тест держит это правило: полный месяц свёрток с ликвидациями не создаёт на
сайте ни одной сводки и ни одного выпуска, а сохранённые публикации видны.
"""
from __future__ import annotations

import calendar
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ.setdefault("LIQSCOPE_SECRET", "test-secret-not-the-published-default")

from fastapi.testclient import TestClient  # noqa: E402

import api_digest  # noqa: E402
import api_hourly  # noqa: E402
import server  # noqa: E402
import web_cache  # noqa: E402
from daily_digest import DigestStore  # noqa: E402
from history import HistoryStore  # noqa: E402
from hourly_posts import PostStore, post_id  # noqa: E402

MEMBER = {"id": 2, "is_admin": False}
HOUR = 3600


def event(ts: float, usd: float = 250_000.0, symbol: str = "BTC_USDT") -> dict:
    return {"id": f"e{ts}", "symbol": symbol, "exchange": "binance", "side": "SELL",
            "position": "LONG", "price": 50000.0, "qty": usd / 50000.0,
            "usd": usd, "timestamp": ts}


class PublicationsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pub_")
        root = self.tmp.name
        # Полный архив: сутки с ликвидациями в часовых свёртках.
        self.hist = HistoryStore(os.path.join(root, "liq_history.jsonl"),
                                 ttl_hours=24 * 31)
        self.now = time.time()
        for hour in range(24):
            self.hist.add(event(self.now - hour * HOUR, usd=100_000.0 + hour))
        self.posts = PostStore(os.path.join(root, "hourly.json"))
        self.digests = DigestStore(os.path.join(root, "digests.json"))
        self.client = TestClient(server.app)
        self.patches = [
            patch.object(server, "HIST", self.hist),
            patch.object(server, "hourly_ctx", api_hourly.ctx),
            patch.object(server, "digest_ctx", api_digest.ctx),
            patch.object(server, "current_user", lambda _request: MEMBER),
        ]
        for item in self.patches:
            item.start()
        api_hourly.ctx.store = self.posts
        api_digest.ctx.store = self.digests
        api_hourly.ctx.page_ok = True
        api_digest.ctx.page_ok = True
        server._ARCHIVE_CELLS["cells"] = None            # кэш свёрток — заново
        self.clear_public_caches()

    def tearDown(self):
        api_hourly.ctx.store = PostStore("")
        api_digest.ctx.store = DigestStore("")
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def clear_public_caches(self):
        """Свежие кэши списков: иначе тест увидит ответ прошлого теста."""
        api_digest._LIST_CACHE = web_cache.TTLCache(ttl=30.0, maxsize=8)
        api_digest._TODAY_CACHE = web_cache.TTLCache(ttl=30.0, maxsize=8)

    # --- месяц свёрток есть, публикаций нет --------------------------------
    def test_month_of_cells_does_not_create_publications(self):
        cells = server.month_cells(self.now)
        self.assertGreaterEqual(len(cells), 20, "свёртки месяца не прочитались")

        hourly = self.client.get("/api/hourly").json()
        self.assertEqual(hourly["items"], [], "свёртка часа не должна быть сводкой")
        self.assertEqual(hourly["days"], [], "день без публикаций не попадает в календарь")
        self.assertEqual(hourly["count"], 0)

        digest = self.client.get("/api/digest").json()
        self.assertEqual(digest["items"], [], "свёртка суток не должна быть выпуском")
        self.assertEqual(digest["days"], [], "выпуск не в своё время не появляется")
        self.assertEqual(digest["count"], 0)

        today = self.client.get("/api/digest/today").json()
        self.assertIsNone(today["item"], "«сегодняшнего» выпуска до публикации нет")

    def test_saved_publications_are_listed(self):
        ts = self.now
        rec = {"id": post_id(ts), "ts": ts, "window_h": 3, "interval_h": 3,
               "total_usd": 250_000.0, "liq_count": 3,
               "texts": {"ru": "<b>Сводка</b> · $250.0K", "en": "<b>Summary</b>"},
               "sent": {"ru": True}}
        self.posts.add(rec)
        day = time.strftime("%Y-%m-%d", time.gmtime(ts + api_hourly.tz_offset()))
        digest_rec = {"id": day, "day": day, "created": ts, "window_h": 24,
                      "facts": {"liq_total_usd": 250_000.0, "liq_count": 3},
                      "ai": {}, "published": {}}
        self.digests.save(digest_rec)
        self.clear_public_caches()

        hourly = self.client.get("/api/hourly").json()
        self.assertEqual([x["id"] for x in hourly["items"]], [rec["id"]])
        self.assertIn(day, [x["day"] for x in hourly["days"]])
        digest = self.client.get("/api/digest").json()
        self.assertIn(day, [x["day"] for x in digest["days"]])
        self.assertEqual(digest["items"][0]["day"], day)

    def test_deleted_publication_does_not_come_back(self):
        """Удалили сводку — она не «восстанавливается» из свёрток месяца."""
        ts = self.now
        rid = post_id(ts)
        self.posts.add({"id": rid, "ts": ts, "window_h": 3, "interval_h": 3,
                        "total_usd": 1000.0, "liq_count": 1,
                        "texts": {"ru": "x", "en": "x"}, "sent": {"ru": True}})
        self.posts.remove(rid)
        self.clear_public_caches()
        self.assertEqual(self.client.get("/api/hourly").json()["count"], 0)

    def test_api_context_has_no_archive_sources(self):
        for ctx in (api_hourly.ctx, api_digest.ctx):
            self.assertFalse(hasattr(ctx, "archive_fn"))
            self.assertFalse(hasattr(ctx, "archive_days_fn"))
            self.assertFalse(hasattr(ctx, "hide"))
        self.assertFalse(os.path.exists(os.path.join(HERE, "archive_hide.py")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
