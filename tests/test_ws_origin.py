"""WS-01 (аудит 30.09.2026): проверка Origin при WebSocket-хендшейке.

``/ws`` принимает соединение только со своими origin (PUBLIC_URL / SITE_URL /
LIQSCOPE_CORS_ORIGINS и локальные стенды). Чужой Origin закрывает хендшейк
кодом 4003 ещё до приёма; без заголовка (не-браузерные клиенты) пускаем.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix="wsorigin_")
os.environ["LIQSCOPE_ACCOUNTS_DB"] = os.path.join(TMP, "accounts.db")
os.environ["LIQSCOPE_HISTORY_FILE"] = os.path.join(TMP, "liq.jsonl")
os.environ["LIQSCOPE_DIGEST_FILE"] = os.path.join(TMP, "digests.json")
os.environ["LIQSCOPE_MAIL_DIR"] = os.path.join(TMP, "mail")
os.environ["LIQSCOPE_SECRET"] = "test-secret-not-the-published-default"
os.environ["LIQSCOPE_DEMO"] = "0"
os.environ["LIQSCOPE_BOT_TOKEN"] = ""
os.environ["LIQSCOPE_PUBLIC_URL"] = "https://liqscope.online"
os.environ["LIQSCOPE_CORS_ORIGINS"] = "https://alt.liqscope.online"
os.environ["LIQSCOPE_API_CACHE"] = "0"

import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402


class WsOriginTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def test_foreign_origin_closed_with_4003(self):
        with self.assertRaises(WebSocketDisconnect) as cap:
            with self.client.websocket_connect(
                    "/ws", headers={"origin": "https://evil.example"}):
                pass
        self.assertEqual(cap.exception.code, 4003)

    def test_own_origin_accepted(self):
        # свой origin (LIQSCOPE_PUBLIC_URL) проходит хендшейк и получает init
        with self.client.websocket_connect(
                "/ws", headers={"origin": "https://liqscope.online"}) as ws:
            msg = ws.receive_json()
            self.assertEqual(msg.get("type"), "init")

    def test_extra_cors_origin_accepted(self):
        with self.client.websocket_connect(
                "/ws", headers={"origin": "https://alt.liqscope.online"}) as ws:
            msg = ws.receive_json()
            self.assertEqual(msg.get("type"), "init")

    def test_no_origin_header_accepted(self):
        # скрипты/боты не шлют Origin — их не ограничиваем
        with self.client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            self.assertEqual(msg.get("type"), "init")

    def test_origin_case_and_trailing_slash(self):
        with self.client.websocket_connect(
                "/ws", headers={"origin": "https://LIQSCOPE.online/"}) as ws:
            msg = ws.receive_json()
            self.assertEqual(msg.get("type"), "init")


if __name__ == "__main__":
    unittest.main()
