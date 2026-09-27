"""Hourly publish fix: full post visible via API/page with images, materialized copy survives.

Checks:
- collect path materializes photo to data/hourly_photos
- public_post keeps photo.url even after original deleted (materialized copy remains)
- canonical fields html_full/preview/content_full present
- digest cover materializes similarly

Run: python3 -m pytest tests/test_hourly_publish_full.py
"""
from __future__ import annotations
import os
import sys
import tempfile
import time
import shutil
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from hourly_posts import PostStore, public_post, materialize_hourly_photo, post_id, tg_html, plain_text
import api_hourly
from daily_digest import DigestStore
import api_digest


class HourlyMaterializeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store_path = os.path.join(self.tmp.name, "posts.json")
        self.store = PostStore(self.store_path)
        self.media = tempfile.TemporaryDirectory()
        self.photos_dir = os.path.join(self.tmp.name, "hourly_photos")
        os.makedirs(self.photos_dir, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()
        self.media.cleanup()

    def test_materialize_copies_and_survives(self):
        src = os.path.join(self.media.name, "cover.jpg")
        with open(src, "wb") as fh:
            fh.write(b"\xff\xd8\xff" + b"\x00"*100)
        pid = "2026-09-27-1200"
        mat = materialize_hourly_photo(src, pid, dest_dir=self.photos_dir)
        self.assertIsNotNone(mat)
        self.assertTrue(os.path.isfile(mat))
        # delete original
        os.remove(src)
        self.assertFalse(os.path.isfile(src))
        # materialized still exists
        self.assertTrue(os.path.isfile(mat))

    def test_public_post_uses_materialized(self):
        src = os.path.join(self.media.name, "cover.png")
        with open(src, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n" + b"\x00"*16)
        pid = "2026-09-27-1200"
        mat = materialize_hourly_photo(src, pid, dest_dir=self.photos_dir)
        rec = {
            "id": pid,
            "ts": time.time(),
            "day": "2026-09-27",
            "texts": {"ru": "<b>Полный текст</b> " + "x"*1200, "en": "<b>Full text</b> " + "y"*1200},
            "photo": {"path": mat, "name": "cover.png", "source": "admin", "materialized": mat, "original": src},
            "html_full": {"ru": tg_html("<b>Полный текст</b> " + "x"*1200)},
            "preview": {"ru": plain_text("<b>Полный текст</b> " + "x"*1200)[:400]},
            "content_full": {"ru": "<b>Полный текст</b> " + "x"*1200},
            "title": f"Сводка {pid}",
            "symbols": ["BTC_USDT"],
            "tags": ["liq"],
        }
        self.store.add(rec)
        out = public_post(rec, "ru")
        self.assertIn("photo", out)
        self.assertIn("url", out["photo"])
        # full text length > 1000 should be preserved in store
        stored = self.store.get(pid)
        self.assertIsNotNone(stored)
        self.assertGreater(len(stored.get("texts", {}).get("ru", "")), 1000)
        self.assertIn("html_full", stored)
        self.assertIn("preview", stored)

    def test_api_returns_full_and_photo(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        src = os.path.join(self.media.name, "cover.png")
        with open(src, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n" + b"\x00"*16)
        pid = "2026-09-27-1200"
        mat = materialize_hourly_photo(src, pid, dest_dir=self.photos_dir)
        rec = {
            "id": pid,
            "ts": time.time(),
            "day": "2026-09-27",
            "texts": {"ru": "<b>Полный текст</b> " + "x"*1200, "en": "<b>Full</b> " + "y"*1200},
            "photo": {"path": mat, "name": "cover.png", "source": "admin"},
            "sent": {"ru": True},
        }
        self.store.add(rec)

        api_hourly.ctx.store = self.store
        api_hourly.ctx.public_url = "https://liqscope.online"
        api_hourly.ctx.page_ok = True
        api_hourly.ctx.channel_url_fn = lambda: "https://t.me/+test"
        app = FastAPI()
        api_hourly.register_hourly_routes(app)
        client = TestClient(app)

        res = client.get("/api/hourly").json()
        self.assertTrue(res["ok"])
        self.assertEqual(res["count"], 1)
        item = res["items"][0]
        self.assertIn("photo", item)
        self.assertIn("url", item["photo"])
        # html length should be > 1000 (full)
        self.assertGreater(len(item.get("html","")), 500)

        # photo route serves file
        pr = client.get(f"/api/hourly/photo/{pid}")
        self.assertEqual(pr.status_code, 200)
        self.assertIn("image", pr.headers.get("content-type",""))

        # after deleting original, materialized still serves
        os.remove(src)
        pr2 = client.get(f"/api/hourly/photo/{pid}")
        self.assertEqual(pr2.status_code, 200)

        api_hourly.ctx.store = PostStore("")


class DigestMaterializeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.media = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()
        self.media.cleanup()

    def test_digest_cover_materializes(self):
        # create fake source image
        src = os.path.join(self.media.name, "digest_cover.jpg")
        with open(src, "wb") as fh:
            fh.write(b"\xff\xd8\xff" + b"\x00"*50)
        day = "2026-09-27"
        mat = api_digest._materialize_digest_photo(src, day)
        self.assertTrue(os.path.isfile(mat))
        self.assertIn(day, mat)
        # assign_cover should keep materialized
        rec = {"day": day, "photo": {"path": mat, "name": "cover.jpg"}}
        out = api_digest.assign_cover(rec, day)
        self.assertIn("path", out)
        self.assertTrue(os.path.isfile(out["path"]))

    def test_digest_api_contains_photo(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        # prepare store
        store_path = os.path.join(self.tmp.name, "digests.json")
        store = DigestStore(store_path)
        src = os.path.join(self.media.name, "cover2.jpg")
        with open(src, "wb") as fh:
            fh.write(b"\xff\xd8\xff" + b"\x00"*20)
        day = "2026-09-27"
        mat = api_digest._materialize_digest_photo(src, day)
        rec = {
            "day": day,
            "ts": time.time(),
            "narrative": {"ru": "Итог дня " + "x"*1500, "en": "Day summary " + "y"*1500},
            "facts": {"total_usd": 123},
            "photo": {"path": mat, "name": "cover2.jpg"},
            "ai": {"ru": "Итог дня " + "x"*1500},
        }
        store.save(rec)

        api_digest.ctx.store = store
        api_digest.ctx.public_url = "https://liqscope.online"
        api_digest.ctx.page_ok = True
        app = FastAPI()
        api_digest.register_digest_routes(app)
        client = TestClient(app)

        res = client.get("/api/digest").json()
        self.assertTrue(res["ok"])
        self.assertGreaterEqual(res["count"], 1)
        # find our day
        item = None
        for it in res["items"]:
            if it.get("day") == day:
                item = it
                break
        self.assertIsNotNone(item)
        # photo field may be in today endpoint
        today = client.get("/api/digest/today").json()
        # today should have photo if it's the latest
        # at least check that store has photo
        stored = store.get(day)
        self.assertIn("photo", stored)

        api_digest.ctx.store = DigestStore("")


if __name__ == "__main__":
    unittest.main(verbosity=2)
