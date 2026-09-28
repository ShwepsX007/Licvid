"""Конфиг nginx: микрокэш API и отдача статики с диска стоят на месте.

Смысл конфига — снять нагрузку с единственного воркера uvicorn (``--workers 1``
остаётся до внедрения Redis): тяжёлые GET-ручки отдаёт кэш nginx, а
``proxy_cache_lock`` оставляет один заход в Python на сотню одновременных
запросов. Статику nginx читает с диска сам, минуя приложение.

Проверяем текстом: nginx в песочнице не поднять, на сервере конфиг валидирует
``nginx -t`` внутри deploy/setup-https.sh. Тест ловит регрессии — случайно
снесённый ``proxy_cache_lock``, закэшированную админскую ручку (ответ админа
уедет другому посетителю) или потерянные заголовки безопасности.

Особенность nginx, из-за которой проверки такие дотошные: ``add_header``
внутри location ОТМЕНЯЕТ наследование серверных ``add_header`` целиком, поэтому
в каждом location со своим заголовком блок безопасности обязан быть повторён.
"""
from __future__ import annotations

import os
import re
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF_MAIN = os.path.join(HERE, "deploy", "nginx-liqscope.conf")
CONF_HTTP = os.path.join(HERE, "deploy", "nginx-liqscope.http.conf")
SETUP = os.path.join(HERE, "deploy", "setup-https.sh")

#: Заголовки безопасности, которые обязаны быть в location со своим add_header.
SECURITY_HEADERS = ("X-Content-Type-Options", "X-Frame-Options",
                    "Referrer-Policy", "Content-Security-Policy")


def strip_comments(text: str) -> str:
    """Убрать #-комментарии, не трогая решётки внутри кавычек."""
    out = []
    for line in text.splitlines():
        in_str = False
        cut = len(line)
        for i, ch in enumerate(line):
            if ch == '"':
                in_str = not in_str
            elif ch == "#" and not in_str:
                cut = i
                break
        out.append(line[:cut])
    return "\n".join(out)


def iter_blocks(text: str, keyword: str):
    """Блоки ``keyword … { … }``: (заголовок, тело). Вложенность и кавычки учтены.

    Кавычки важны дважды: в регулярке location встречаются фигурные скобки
    (``[0-9]{4}``), а в значениях директив — точки с запятой.
    """
    for m in re.finditer(rf"(?m)^[ \t]*{keyword}\b", text):
        i = m.end()
        open_at = -1
        while i < len(text):
            ch = text[i]
            if ch == '"':
                nxt = text.find('"', i + 1)
                if nxt < 0:
                    break
                i = nxt + 1
                continue
            if ch == "{":
                open_at = i
                break
            if ch == ";":
                break
            i += 1
        if open_at < 0:
            continue
        head = " ".join(text[m.start():open_at].split())
        depth, j = 0, open_at
        while j < len(text):
            ch = text[j]
            if ch == '"':
                nxt = text.find('"', j + 1)
                if nxt < 0:
                    break
                j = nxt + 1
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        yield head, text[open_at + 1:j]


def find_location(text: str, needle: str):
    """Тело location, чей заголовок содержит needle (первое совпадение)."""
    for head, body in iter_blocks(text, "location"):
        if needle in head:
            return head, body
    return "", ""


class NginxConfCaseMixin:
    path = ""

    def setUp(self):
        with open(self.path, encoding="utf-8") as fh:
            raw = fh.read()
        self.raw = raw
        self.text = strip_comments(raw)

    def test_braces_are_balanced(self):
        text = self.text
        depth = 0
        in_str = False
        for ch in text:
            if ch == '"':
                in_str = not in_str
            elif in_str:
                continue
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            self.assertGreaterEqual(depth, 0, "лишняя закрывающая скобка")
        self.assertEqual(depth, 0, "конфиг не закрыт — nginx -t упадёт")
        self.assertFalse(in_str, "незакрытая кавычка")

    def test_cache_zone_is_declared_outside_server(self):
        """proxy_cache_path живёт только в http-контексте (вне server {})."""
        first_server = self.text.index("server {")
        head = self.text[:first_server]
        self.assertIn("proxy_cache_path", head)
        self.assertRegex(head, r"proxy_cache_path\s+\S+\s+levels=1:2")
        self.assertIn("keys_zone=API_CACHE:10m", head)
        self.assertIn("max_size=100m", head)
        self.assertIn("inactive=1m", head)
        self.assertIn("use_temp_path=off", head)
        # внутри server{} объявления зоны быть не должно
        for _head, body in iter_blocks(self.text, "server"):
            self.assertNotIn("proxy_cache_path", body)

    def test_klines_microcache(self):
        head, body = find_location(self.text, "/api/klines")
        self.assertIn("= /api/klines", head, "ручка свечей должна быть точным location")
        self.assertIn("proxy_cache API_CACHE;", body)
        self.assertIn("proxy_cache_valid 200 3s;", body)
        self.assertIn("proxy_cache_lock on;", body)
        self.assertIn("proxy_cache_key", body)
        self.assertIn("updating", body, "протухшее отдаём, свежее грузим в фоне")
        self.assertRegex(body, r"proxy_cache_use_stale[^;]*http_50[0234]")
        self.assertIn("add_header X-Cache-Status $upstream_cache_status", body)
        self.assertIn("proxy_pass", body)
        # в кэш не должно лечь тело, сжатое под одного клиента
        self.assertIn('proxy_set_header Accept-Encoding "";', body)
        # чужой cookie аналитики не уезжает всем, кто получил HIT
        self.assertIn("proxy_hide_header Set-Cookie;", body)
        self.assertIn("proxy_ignore_headers", body)
        self.assertIn("Set-Cookie", body)
        # срок кэша держим своим: заголовок приложения (например no-store из
        # будущего middleware) не должен молча выключать микрокэш
        self.assertRegex(body, r"proxy_ignore_headers[^;]*Cache-Control")
        self.assertRegex(body, r"proxy_ignore_headers[^;]*Expires")

    def test_stats_and_digest_microcache(self):
        head, body = find_location(self.text, "/api/(stats")
        self.assertTrue(head, "нет location для статистики и дайджестов")
        self.assertIn("proxy_cache API_CACHE;", body)
        self.assertIn("proxy_cache_valid 200 5s;", body)
        self.assertIn("proxy_cache_lock on;", body)
        self.assertIn("add_header X-Cache-Status $upstream_cache_status", body)
        # регулярка в кавычках: без них nginx принимает «{» за начало блока
        self.assertRegex(head, r'location\s+~\s+"')
        # точный перечень: админка и настройки дайджеста кэшироваться не должны
        self.assertNotIn("settings", head)
        self.assertNotIn("admin", head)
        # якорь в конце регулярки: /api/digest/settings не должен совпасть
        self.assertRegex(head, r"\$\"?$")

    def test_no_cache_for_mutating_and_admin_routes(self):
        # /ws и запасной путь статики есть в обоих конфигах; лимит
        # /api/symbols/add — только в основном (HTTPS)
        for needle in ("= /api/symbols/add", "/ws", "@static_app"):
            head, body = find_location(self.text, needle)
            if not body:
                self.assertIn(needle, ("= /api/symbols/add",),
                              f"location {needle} пропал из конфига")
                continue
            self.assertNotIn("proxy_cache", body, f"{needle} не должен кэшироваться")
        # общий location / — страницы и остальные ручки, тоже мимо кэша
        for head, body in iter_blocks(self.text, "location"):
            if head.strip() == "location /":
                self.assertNotIn("proxy_cache", body)

    def test_static_is_served_from_disk_with_fallback(self):
        head, body = find_location(self.text, "location /static/")
        self.assertTrue(body, "нет location /static/")
        self.assertRegex(body, r"alias\s+\S+/static/;",
                         "статика должна читаться с диска, а не проксироваться")
        self.assertIn("access_log off;", body)
        self.assertIn("try_files $uri @static_app;", body,
                      "нет запасного пути в приложение")
        self.assertIn("Cache-Control", body)
        fb_head, fb_body = find_location(self.text, "@static_app")
        self.assertTrue(fb_body, "нет location @static_app")
        self.assertIn("proxy_pass", fb_body)

    def test_static_cache_control_keeps_versions_immutable(self):
        """?v= — год и immutable, прочее — 7 дней, но JS/CSS без версии — час.

        i18n.pages.{lang}.js подставляется в HTML без ?v= (seo_pages.render):
        неделя кэша заморозила бы свежие переводы, поэтому у скриптов час.
        """
        self.assertIn("map \"$arg_v|$uri\" $liq_static_cc", self.text)
        block = self.text[self.text.index("map \"$arg_v|$uri\" $liq_static_cc"):]
        block = block[:block.index("}") + 1]
        self.assertIn("max-age=31536000, immutable", block)
        self.assertIn("max-age=604800", block, "картинки и шрифты — 7 дней")
        self.assertIn("no-transform", block)
        self.assertRegex(block, r"\[\.\]js\$\"\s+\"public, max-age=3600")
        self.assertRegex(block, r"\[\.\]css\$\"\s+\"public, max-age=3600")

    def test_security_headers_survive_add_header_in_location(self):
        """В каждом location со своим add_header блок безопасности повторён."""
        checked = 0
        for head, body in iter_blocks(self.text, "location"):
            if "add_header" not in body:
                continue
            checked += 1
            for name in SECURITY_HEADERS:
                self.assertIn(f"add_header {name}", body,
                              f"{head}: потерялся заголовок {name}")
        self.assertGreaterEqual(checked, 3, "проверка не нашла кэширующих location")

    def test_websocket_is_not_buffered_or_cached(self):
        head, body = find_location(self.text, "/ws")
        self.assertIn("proxy_buffering off;", body)
        self.assertIn("upgrade", body.lower())
        self.assertNotIn("proxy_cache", body)


class MainConfTest(NginxConfCaseMixin, unittest.TestCase):
    path = CONF_MAIN

    def test_https_specifics_are_intact(self):
        self.assertIn("listen 443 ssl", self.text)
        self.assertIn("Strict-Transport-Security", self.text)
        self.assertIn("limit_conn liq_ws 20", self.text)
        self.assertIn("limit_req zone=liq_sym", self.text)
        self.assertIn("upstream liqscope_app", self.text)
        self.assertIn("keepalive 8", self.text)

    def test_symbol_add_is_rate_limited(self):
        head, body = find_location(self.text, "= /api/symbols/add")
        self.assertTrue(body, "пропал лимит добавления монет")
        self.assertIn("limit_req zone=liq_sym", body)

    def test_cache_locations_proxy_to_upstream(self):
        for needle in ("/api/klines", "/api/(stats"):
            _head, body = find_location(self.text, needle)
            self.assertIn("proxy_pass http://liqscope_app;", body,
                          f"{needle}: прокси мимо upstream теряет keepalive")


class HttpConfTest(NginxConfCaseMixin, unittest.TestCase):
    path = CONF_HTTP

    def test_http_conf_is_the_pre_certificate_one(self):
        self.assertIn("listen 80", self.text)
        self.assertNotIn("listen 443", self.text)


class CrossplaneSyntaxTest(unittest.TestCase):
    """Разбор синтаксисом nginx (пакет crossplane), если он установлен.

    Файлы подключаются из sites-enabled, то есть живут в контексте ``http{}``:
    разбираем их через обёртку, иначе ``proxy_cache_path``/``map`` на верхнем
    уровне будут объявлены «не там». ``nginx -t`` на сервере делает то же самое
    (deploy/setup-https.sh), а здесь проверка доступна без установленного nginx.
    """

    def test_configs_parse_in_http_context(self):
        try:
            import crossplane
        except ImportError:
            self.skipTest("crossplane не установлен: pip install crossplane")
        import json
        import tempfile

        tmp = tempfile.mkdtemp(prefix="nginx_conf_")
        for name, path in (("main", CONF_MAIN), ("http", CONF_HTTP)):
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
            # include внешнего файла letsencrypt в песочнице не прочитать
            body = body.replace("include /etc/letsencrypt/options-ssl-nginx.conf;",
                                "# (stub) include options-ssl-nginx.conf")
            target = os.path.join(tmp, f"{name}.conf")
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(body)
            wrapper = os.path.join(tmp, f"wrap_{name}.conf")
            with open(wrapper, "w", encoding="utf-8") as fh:
                fh.write("events { worker_connections 64; }\n"
                         f"http {{\n    include {target};\n}}\n")
            parsed = crossplane.parse(wrapper, catch_errors=True, comments=False)
            self.assertEqual(
                parsed.get("status"), "ok",
                f"{path}: {json.dumps(parsed.get('errors'), ensure_ascii=False)}")


class DeployScriptTest(unittest.TestCase):
    def setUp(self):
        with open(SETUP, encoding="utf-8") as fh:
            self.script = fh.read()

    def test_script_substitutes_real_static_path(self):
        """Путь к static в конфиге подставляется из каталога репозитория."""
        self.assertIn("alias /root/Licvid/static/", self.script)
        self.assertIn("STATIC_DIR", self.script)
        self.assertIn("nginx -t", self.script)

    def test_script_warns_about_static_permissions(self):
        """www-data должен читать static, иначе вместо файлов будет 403."""
        self.assertIn("check_static_perms", self.script)
        self.assertIn("chmod", self.script)

    def test_script_mentions_cache_check(self):
        self.assertIn("X-Cache-Status", self.script)

    def test_conf_files_are_not_enabled_together(self):
        """Оба конфига объявляют зону API_CACHE — в sites-enabled лежит один."""
        self.assertIn("install_conf", self.script)
        self.assertEqual(self.script.count("sites-available/liqscope"),
                         self.script.count("/etc/nginx/sites-available/liqscope"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
