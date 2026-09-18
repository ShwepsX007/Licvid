"""Тесты ИИ-шапки: цепочка сервисов, проверка ответа, фолбэк на шаблоны.

Сети нет: HTTP-функция подменяется, поэтому проверяем ровно нашу логику —
порядок сервисов, разбор ответов Google и OpenAI-совместимых, отказ от
негодного текста (цифры, отказ модели, markdown) и то, что при полном
молчании всех сервисов шапка берётся из шаблонов.
"""
from __future__ import annotations

import os
import re
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import ai_text  # noqa: E402
from ai_text import (ANGLES, AiWriter, build_providers, clean_head,  # noqa: E402
                     fit_head, head_problem, missing_specifics, parse_reply,
                     pick_model, summarize, too_similar)

SNAP = {
    "window_h": 4,
    "total_usd": 12_400_000, "longs_usd": 9_000_000, "shorts_usd": 3_400_000,
    "count": 812,
    "top_coins": [{"symbol": "BTC", "usd": 5_000_000, "longs": 4_200_000, "shorts": 800_000},
                  {"symbol": "ETH", "usd": 3_100_000, "longs": 2_600_000, "shorts": 500_000}],
    "exchanges": {"binance": 7_000_000, "bybit": 3_000_000},
    "biggest": {"symbol": "SOL", "usd": 900_000, "exchange": "binance"},
    "oi": {"BTC": {"changes": {"h1": {"pct": 2.4}}}},
    "cvd": {"BTC": 3_100_000.0},
}

KEYS = ("LIQSCOPE_AI_GEMINI_KEY", "LIQSCOPE_AI_GROQ_KEY", "LIQSCOPE_AI_OPENROUTER_KEY",
        "LIQSCOPE_AI_DEEPSEEK_KEY", "LIQSCOPE_AI_KEY", "LIQSCOPE_AI_URL",
        "LIQSCOPE_AI_MODEL", "LIQSCOPE_AI_ORDER", "LIQSCOPE_AI_DISABLED",
        "LIQSCOPE_AI_GEMINI_MODEL", "LIQSCOPE_AI_GROQ_MODEL",
        "LIQSCOPE_AI_GEMINI_KEYS", "LIQSCOPE_AI_GROQ_KEYS",
        "LIQSCOPE_AI_OPENROUTER_KEYS", "LIQSCOPE_AI_DEEPSEEK_KEYS",
        "LIQSCOPE_AI_KEYS")


class EnvMixin(unittest.TestCase):
    def setUp(self):
        self.keep = {k: os.environ.get(k) for k in KEYS}
        for k in KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k in KEYS:
            os.environ.pop(k, None)
            if self.keep[k] is not None:
                os.environ[k] = self.keep[k]


class ProvidersTest(EnvMixin):
    def test_without_keys_ai_is_off(self):
        self.assertEqual(build_providers(), [])
        self.assertIsNone(ai_text.build_ai())

    def test_chain_follows_order_and_skips_missing_keys(self):
        os.environ["LIQSCOPE_AI_GEMINI_KEY"] = "g-key"
        os.environ["LIQSCOPE_AI_OPENROUTER_KEY"] = "or-key"
        names = [p.name for p in build_providers()]
        self.assertEqual(names, ["gemini", "openrouter"])   # без groq: ключа нет
        os.environ["LIQSCOPE_AI_ORDER"] = "openrouter,gemini"
        self.assertEqual([p.name for p in build_providers()], ["openrouter", "gemini"])

    def test_models_and_custom_endpoint(self):
        os.environ["LIQSCOPE_AI_GROQ_KEY"] = "k"
        os.environ["LIQSCOPE_AI_GROQ_MODEL"] = "llama-3.1-8b-instant"
        os.environ["LIQSCOPE_AI_URL"] = "http://127.0.0.1:11434/v1/chat/completions"
        os.environ["LIQSCOPE_AI_KEY"] = "local"
        os.environ["LIQSCOPE_AI_MODEL"] = "qwen3:8b"
        pr = {p.name: p for p in build_providers()}
        self.assertEqual(pr["groq"].model, "llama-3.1-8b-instant")
        self.assertEqual(pr["custom"].url, "http://127.0.0.1:11434/v1/chat/completions")
        self.assertEqual(pr["custom"].model, "qwen3:8b")

    def test_disabled_flag_turns_everything_off(self):
        os.environ["LIQSCOPE_AI_GEMINI_KEY"] = "g"
        os.environ["LIQSCOPE_AI_DISABLED"] = "1"
        self.assertEqual(build_providers(), [])


class PromptTest(unittest.TestCase):
    def test_summary_has_market_facts(self):
        text = "\n".join(summarize(SNAP))
        self.assertIn("$12.40M", text)          # сумма окна
        self.assertIn("BTC", text)
        self.assertIn("binance", text.lower())
        self.assertIn("Открытый интерес", text)  # OI
        self.assertIn("CVD", text)

    def test_prompt_asks_for_figures_and_angle(self):
        prompt = ai_text.build_prompt(SNAP, recent=["Прошлая шапка"], variant=2)
        self.assertIn("Прошлая шапка", prompt)           # просим не повторяться
        self.assertIn("монет", prompt.lower())
        self.assertIn("числ", prompt.lower())
        self.assertIn(ANGLES[2 % len(ANGLES)], prompt)    # акцент чередуется
        self.assertNotIn("без цифр", prompt.lower())

    def test_angles_rotate_between_posts(self):
        self.assertNotEqual(ai_text.build_prompt(SNAP, variant=0),
                            ai_text.build_prompt(SNAP, variant=1))


class ReplyTest(unittest.TestCase):
    def test_gemini_reply(self):
        p = ai_text.Provider("gemini", "gemini", "k", "https://x", "m")
        text = parse_reply(p, {"candidates": [{"content": {"parts": [{"text": "А"},
                                                                {"text": "Б"}]}}]})
        self.assertEqual(text, "А Б")

    def test_openai_reply(self):
        p = ai_text.Provider("groq", "openai", "k", "https://x", "m")
        self.assertEqual(parse_reply(p, {"choices": [{"message": {"content": "Готово"}}]}),
                         "Готово")

    def test_empty_reply_is_error(self):
        p = ai_text.Provider("groq", "openai", "k", "https://x", "m")
        with self.assertRaises(RuntimeError):
            parse_reply(p, {"choices": []})
        g = ai_text.Provider("gemini", "gemini", "k", "https://x", "m")
        with self.assertRaises(RuntimeError):
            parse_reply(g, {"promptFeedback": {"blockReason": "SAFETY"}})


class CleanTest(unittest.TestCase):
    def test_markdown_and_quotes_are_stripped(self):
        self.assertEqual(clean_head('**«Лонги BTC на $9.00M»**'),
                         "Лонги BTC на $9.00M")
        self.assertEqual(clean_head("> - Текст   с   пробелами"), "Текст с пробелами")

    def test_figures_are_allowed(self):
        """Цифры в шапке разрешены: раньше запрещались, теперь нужны."""
        self.assertEqual(head_problem("Лонги BTC на $9.00M против $3.40M шортов"), "")
        self.assertEqual(head_problem("Перевес лонгов 2.6 к 1 за окно"), "")

    def test_hashtag_and_refusal_are_rejected(self):
        self.assertIn("хештег", head_problem("Лонги BTC $9.00M #крипта"))
        self.assertIn("отказ", head_problem("Извините, я не могу помочь"))

    def test_specifics_are_required(self):
        """Шапка без монеты и без числа — «вода», её не берём."""
        self.assertIn("ни монеты, ни числа", missing_specifics(
            "Рынок снова показал, кто здесь главный", SNAP))
        self.assertIn("нет ни одного числа", missing_specifics(
            "Лонги биткоина снова пострадали сильнее шортов", SNAP))
        self.assertEqual(missing_specifics("Лонги BTC на $9.00M", SNAP), "")
        self.assertEqual(missing_specifics("Лонги биткоина выросли к $9.00M", SNAP), "")

    def test_similar_head_is_detected(self):
        recent = ["За окно лонги BTC сняли на девять миллионов долларов"]
        self.assertTrue(too_similar("За окно сняли лонги BTC на девять миллионов", recent))
        self.assertFalse(too_similar("Шорты ETH сняли на $500.0K", recent))

    def test_refusal_is_rejected(self):
        self.assertIn("отказ", head_problem("Извините, я не могу помочь с этим запросом"))

    def test_long_head_is_trimmed_to_limit(self):
        long = "Длинная шапка " * 40
        self.assertEqual(len(fit_head(long)) <= ai_text.HEAD_MAX_LEN, True)
        self.assertTrue(fit_head(long))


class WriterTest(EnvMixin):
    def writer(self) -> AiWriter:
        os.environ["LIQSCOPE_AI_GEMINI_KEY"] = "g-key"
        os.environ["LIQSCOPE_AI_GROQ_KEY"] = "q-key"
        return AiWriter(build_providers())

    def test_first_working_service_wins(self):
        w = self.writer()
        calls = []

        def fake_post(url, payload, headers, timeout=12.0):
            calls.append(url)
            if "generativelanguage" in url:
                raise RuntimeError("HTTP 429: rate limit")
            return {"choices": [{"message": {"content": "Лонги BTC на $9.00M против $3.40M шортов"}}]}

        with mock.patch.object(ai_text, "post_json", fake_post):
            head = w.headline_sync(SNAP)
        self.assertEqual(head, "Лонги BTC на $9.00M против $3.40M шортов")
        self.assertEqual(len(calls), 2)                      # gemini → groq
        self.assertEqual(w.status()["last"]["provider"], "groq")
        self.assertTrue(w.status()["last"]["ok"])

    def test_bad_answer_falls_through_to_next_service(self):
        w = self.writer()
        answers = ["Извините, я не могу помочь с этим запросом",   # отказ модели
                   "Лонги BTC на $9.00M против $3.40M шортов"]

        def fake_post(url, payload, headers, timeout=12.0):
            if "generativelanguage" in url:
                return {"candidates": [{"content": {"parts": [{"text": answers[0]}]}}]}
            return {"choices": [{"message": {"content": answers[1]}}]}

        with mock.patch.object(ai_text, "post_json", fake_post):
            head = w.headline_sync(SNAP)
        self.assertEqual(head, answers[1])
        self.assertIn("отказ", w.status()["providers"][0]["reason"])

    def test_all_services_down_gives_none(self):
        w = self.writer()

        def fake_post(*a, **kw):
            raise RuntimeError("ConnectionError: нет сети")

        with mock.patch.object(ai_text, "post_json", fake_post):
            self.assertIsNone(w.headline_sync(SNAP))
        st = w.status()
        self.assertFalse(st["last"]["ok"])
        self.assertEqual(st["fails"], 2)
        self.assertIn("нет сети", st["last"]["reason"])

    def test_bad_key_stops_asking_that_service(self):
        """401 у Gemini: он помечается мёртвым и больше не опрашивается."""
        w = self.writer()
        calls = []

        def fake_post(url, payload, headers, timeout=12.0):
            calls.append("gemini" if "generativelanguage" in url else "groq")
            if "generativelanguage" in url:
                raise RuntimeError("HTTP 401: invalid api key")
            return {"choices": [{"message": {"content": "ETH потерял $3.10M на ликвидациях, перевес у лонгов"}}]}

        with mock.patch.object(ai_text, "post_json", fake_post):
            self.assertEqual(w.headline_sync(SNAP), "ETH потерял $3.10M на ликвидациях, перевес у лонгов")
        self.assertEqual(calls, ["gemini", "groq"])
        self.assertTrue(w.status()["providers"][0]["dead"])
        calls.clear()
        with mock.patch.object(ai_text, "post_json", fake_post):
            head = w.headline_sync(SNAP)
        self.assertEqual(calls, ["groq"])          # gemini больше не дёргаем
        self.assertEqual(head, "ETH потерял $3.10M на ликвидациях, перевес у лонгов")

    def test_requests_have_right_shape(self):
        w = self.writer()
        seen = []

        def fake_post(url, payload, headers, timeout=12.0):
            seen.append((url, payload, headers))
            raise RuntimeError("stop")

        with mock.patch.object(ai_text, "post_json", fake_post):
            w.headline_sync(SNAP)
        g_url, g_body, g_head = seen[0]
        self.assertIn("generateContent", g_url)
        self.assertIn("key=g-key", g_url)
        self.assertIn("contents", g_body)
        q_url, q_body, q_head = seen[1]
        self.assertEqual(q_head["Authorization"], "Bearer q-key")
        self.assertEqual(q_body["model"], "qwen/qwen3.8-27b")
        self.assertEqual(q_body["messages"][0]["role"], "system")

    def test_async_wrapper_returns_head(self):
        import asyncio
        w = self.writer()
        with mock.patch.object(ai_text, "post_json",
                               lambda *a, **kw: {"choices": [
                                   {"message": {"content": "Лонги SOL на $900.0K — крупнейшая ликвидация окна"}}]}):
            head = asyncio.run(w.headline(SNAP))
        self.assertEqual(head, "Лонги SOL на $900.0K — крупнейшая ликвидация окна")


class ModelRepairTest(EnvMixin):
    """Сервисы переименовывают модели — код должен это переживать сам."""

    def test_user_agent_is_not_python_urllib(self):
        """Cloudflare у Groq отвечает 403/1010 на Python-urllib — шлём свой UA."""
        import urllib.request
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def read(self):
                return b'{"ok": true}'

        def fake_urlopen(req, timeout=None):
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            return FakeResponse()

        real = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            ai_text.post_json("https://api.example.com/x", {"a": 1}, {})
        finally:
            urllib.request.urlopen = real
        ua = captured["headers"].get("user-agent", "")
        self.assertTrue(ua.startswith("LiqScope/"), ua)
        self.assertNotIn("urllib", ua.lower())
        self.assertIn("application/json", captured["headers"].get("accept", ""))

    def test_hint_for_cloudflare_1010_and_rate_limits(self):
        self.assertIn("1010", ai_text.ai_error_hint("HTTP 403: error code: 1010"))
        self.assertIn("лимит", ai_text.ai_error_hint("HTTP 429: rate limit reached"))
        self.assertIn("ключ", ai_text.ai_error_hint("HTTP 401: invalid api key"))

    def test_google_404_replacement_is_used(self):
        """Google пишет замену прямо в ошибке — берём её и повторяем запрос."""
        os.environ["LIQSCOPE_AI_GEMINI_KEY"] = "g-key"
        # админ прописал устаревшую модель — сервис ответит 404 с заменой
        os.environ["LIQSCOPE_AI_GEMINI_MODEL"] = "gemini-2.5-flash-lite"
        w = AiWriter(build_providers())
        self.assertEqual(w.providers[0].model, "gemini-2.5-flash-lite")
        urls = []

        def fake_post(url, payload, headers, timeout=12.0):
            urls.append(url)
            if "gemini-2.5-flash-lite" in url:      # старый дефолт: 404 + подсказка
                raise RuntimeError(
                    'HTTP 404: {"message": "This model models/gemini-2.5-flash-lite is '
                    'no longer available to new users. Please update your code to use '
                    'models/gemini-3.5-flash-lite for the latest"}')
            return {"candidates": [{"content": {"parts": [
                {"text": "Лонги BTC на $9.00M против $3.40M шортов — без паники"}]}}]}

        with mock.patch.object(ai_text, "post_json", fake_post):
            head = w.headline_sync(SNAP)
        self.assertEqual(head, "Лонги BTC на $9.00M против $3.40M шортов — без паники")
        self.assertEqual(len(urls), 2)
        self.assertIn("gemini-3.5-flash-lite", urls[1])
        self.assertEqual(w.status()["providers"][0]["model"], "gemini-3.5-flash-lite")

    def test_model_is_discovered_when_hint_is_missing(self):
        """Нет подсказки в ошибке — спрашиваем список моделей у сервиса."""
        os.environ["LIQSCOPE_AI_GROQ_KEY"] = "q-key"
        # админ прописал модель, которой у проекта нет
        os.environ["LIQSCOPE_AI_GROQ_MODEL"] = "llama-3.3-70b-versatile"
        w = AiWriter(build_providers())
        self.assertEqual(w.providers[0].model, "llama-3.3-70b-versatile")
        asked = []

        def fake_post(url, payload, headers, timeout=12.0):
            if payload.get("model") == "llama-3.3-70b-versatile":
                raise RuntimeError('HTTP 404: model "llama-3.3-70b-versatile" not found')
            return {"choices": [{"message": {"content": "Шорты ETH сняли на $500.0K при окне в $12.40M"}}]}

        def fake_get(url, headers, timeout=12.0):
            asked.append(url)
            return {"data": [{"id": "whisper-large-v3"}, {"id": "llama-4-scout-17b"},
                             {"id": "openai/gpt-oss-120b"}]}

        with mock.patch.object(ai_text, "post_json", fake_post), \
                mock.patch.object(ai_text, "get_json", fake_get):
            head = w.headline_sync(SNAP)
        self.assertEqual(head, "Шорты ETH сняли на $500.0K при окне в $12.40M")
        self.assertTrue(asked and asked[0].endswith("/models"))
        self.assertEqual(w.status()["providers"][0]["model"], "openai/gpt-oss-120b")

    def test_suggested_model_takes_replacement_not_old_name(self):
        err = ('HTTP 404: {"message": "This model models/gemini-2.5-flash-lite is no '
               'longer available to new users. Please update your code to use '
               'models/gemini-3.5-flash-lite for the latest"}')
        self.assertEqual(ai_text.suggested_model(err, "gemini-2.5-flash-lite"),
                         "gemini-3.5-flash-lite")
        self.assertEqual(ai_text.suggested_model("HTTP 500: поломка"), "")

    def test_pick_model_prefers_lite_and_free(self):
        gem = ["gemini-2.5-flash", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite",
               "gemini-3.5-flash", "text-embedding-004", "gemini-2.5-flash-image"]
        self.assertEqual(pick_model("gemini", gem), "gemini-3.5-flash-lite")
        self.assertEqual(pick_model("openrouter", ["a/paid", "b/c:free"]), "b/c:free")
        self.assertEqual(pick_model("openrouter", ["a/paid"]), "")   # бесплатной нет
        self.assertEqual(pick_model("groq", ["whisper-large-v3", "llama-3.3-70b-versatile"]),
                         "llama-3.3-70b-versatile")


class GroqProjectTest(EnvMixin):
    """Groq: модели можно выключать в настройках проекта — код должен подбирать."""

    def test_rank_prefers_qwen_and_drops_non_text_models(self):
        """Набор моделей проекта: берём те, что пишут текст по-русски."""
        ids = ["allam-2-7b", "whisper-large-v3-turbo", "canopylabs/orpheus-v1-russian",
               "meta-llama/llama-prompt-guard-2-22m", "openai/gpt-oss-safeguard-20b",
               "openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b",
               "groq/compound-mini"]
        ranked = ai_text.rank_models("groq", ids)
        self.assertEqual(ranked[0], "qwen/qwen3.8-27b")
        for bad in ("whisper-large-v3-turbo", "canopylabs/orpheus-v1-russian",
                    "meta-llama/llama-prompt-guard-2-22m",
                    "openai/gpt-oss-safeguard-20b"):
            self.assertNotIn(bad, ranked)
        self.assertIn("openai/gpt-oss-120b", ranked)
        self.assertIn("groq/compound-mini", ranked)

    def test_hint_explains_project_block(self):
        hint = ai_text.ai_error_hint('HTTP 403: {"error":{"message":"The model `openai/gpt-oss-120b` '
                             'is blocked at the project level. Please have a project admin '
                             'enable this model in the project settings"}}')
        self.assertIn("проект", hint.lower())
        self.assertIn("MODEL", hint)

    def test_writer_skips_blocked_models_until_one_works(self):
        os.environ["LIQSCOPE_AI_GROQ_KEY"] = "q-key"
        w = AiWriter(build_providers())
        asked = []

        def fake_post(url, payload, headers, timeout=12.0):
            model = payload.get("model")
            asked.append(model)
            if model != "allam-2-7b":
                raise RuntimeError(f'HTTP 403: The model `{model}` is blocked at the '
                                   'project level. Please have a project admin enable it')
            return {"choices": [{"message": {"content": "Лонги BTC на $9.00M — втрое больше шортов за окно"}}]}

        def fake_get(url, headers, timeout=12.0):
            return {"data": [{"id": "allam-2-7b"}, {"id": "llama-3.3-70b-versatile"},
                             {"id": "openai/gpt-oss-120b"},
                             {"id": "whisper-large-v3"}]}

        with mock.patch.object(ai_text, "post_json", fake_post), \
                mock.patch.object(ai_text, "get_json", fake_get):
            head = w.headline_sync(SNAP)
        self.assertEqual(head, "Лонги BTC на $9.00M — втрое больше шортов за окно")
        self.assertEqual(asked[-1], "allam-2-7b")          # дошли до рабочей модели
        self.assertGreaterEqual(len(asked), 3)
        self.assertEqual(w.status()["providers"][0]["model"], "allam-2-7b")
        self.assertFalse(w.status()["providers"][0]["dead"])

    def test_writer_gives_up_when_all_models_blocked(self):
        os.environ["LIQSCOPE_AI_GROQ_KEY"] = "q-key"
        w = AiWriter(build_providers())

        def fake_post(url, payload, headers, timeout=12.0):
            raise RuntimeError('HTTP 403: The model `%s` is blocked at the project level'
                               % payload.get("model"))

        def fake_get(url, headers, timeout=12.0):
            return {"data": [{"id": "allam-2-7b"}, {"id": "llama-3.3-70b-versatile"}]}

        with mock.patch.object(ai_text, "post_json", fake_post), \
                mock.patch.object(ai_text, "get_json", fake_get):
            self.assertIsNone(w.headline_sync(SNAP))
        st = w.status()["providers"][0]
        self.assertTrue(st["dead"])
        self.assertIn("проект", st["reason"].lower())

    def test_repeated_head_triggers_new_angle(self):
        """Похожая на прошлую шапка не уходит в канал — меняем акцент."""
        os.environ["LIQSCOPE_AI_GEMINI_KEY"] = "g-key"
        w = AiWriter(build_providers())
        prompts, answers = [], [
            "Лонги BTC сняли на $9.00M против $3.40M шортов",
            "Лонги BTC сняли на $9.00M против $3.40M шортов",
            "Шорты ETH забрали $3.10M, перевес на стороне лонгов"]

        def fake_post(url, payload, headers, timeout=12.0):
            prompts.append(payload["contents"][0]["parts"][0]["text"])
            return {"candidates": [{"content": {"parts": [{"text": answers.pop(0)}]}}]}

        with mock.patch.object(ai_text, "post_json", fake_post):
            head = w.headline_sync(
                SNAP, recent=["Лонги BTC сняли на $9.00M против $3.40M шортов"])
        self.assertEqual(head, "Шорты ETH забрали $3.10M, перевес на стороне лонгов")
        # трижды пришла одна и та же формулировка — код переспрашивал с новым акцентом
        self.assertEqual(len(prompts), 3)
        self.assertEqual(len(set(prompts)), 3)           # акцент каждый раз другой

    def test_empty_reply_retries_with_bigger_limit(self):
        """gpt-oss и подобные сперва отдают пустой текст — повторяем с запасом."""
        os.environ["LIQSCOPE_AI_GROQ_KEY"] = "q-key"
        w = AiWriter(build_providers(), max_tokens=120)
        limits = []

        def fake_post(url, payload, headers, timeout=12.0):
            limits.append(payload.get("max_tokens"))
            if len(limits) == 1:
                return {"choices": [{"message": {"content": ""},
                                     "finish_reason": "length"}]}
            return {"choices": [{"message": {"content": "Окно на $12.40M: лонги BTC $9.00M перевесили шорты"}}]}

        with mock.patch.object(ai_text, "post_json", fake_post):
            head = w.headline_sync(SNAP)
        self.assertEqual(head, "Окно на $12.40M: лонги BTC $9.00M перевесили шорты")
        self.assertEqual(limits[0], 120)
        self.assertGreaterEqual(limits[1], 400)


class MultiKeyTest(EnvMixin):
    """Несколько ключей на сервис: лимит одного не останавливает работу.

    Ключи задаются списком (LIQSCOPE_AI_<СЕРВИС>_KEYS), и когда сервис
    отвечает 429/quota, запрос уходит со следующего ключа того же сервиса.
    Раньше лимит первого ключа ронял весь сервис, и шапка шла из шаблона.
    """

    def test_keys_parse_from_list_and_single_name(self):
        os.environ["LIQSCOPE_AI_GEMINI_KEYS"] = "k1, k2 ;k3\nk1"
        os.environ["LIQSCOPE_AI_GEMINI_KEY"] = "k4"
        keys = ai_text.keys_for("gemini", "LIQSCOPE_AI_GEMINI_KEY")
        self.assertEqual(keys, ["k1", "k2", "k3", "k4"])   # дубли не повторяем
        os.environ.pop("LIQSCOPE_AI_GEMINI_KEYS")
        self.assertEqual(ai_text.keys_for("gemini", "LIQSCOPE_AI_GEMINI_KEY"), ["k4"])

    def test_provider_keeps_all_keys(self):
        os.environ["LIQSCOPE_AI_GROQ_KEYS"] = "q1,q2,q3"
        provs = {p.name: p for p in build_providers()}
        self.assertEqual(provs["groq"].keys, ["q1", "q2", "q3"])
        self.assertEqual(provs["groq"].key, "q1")
        self.assertEqual(provs["groq"].public()["keys"], "3")

    def test_quota_on_first_key_moves_to_second(self):
        os.environ["LIQSCOPE_AI_GEMINI_KEYS"] = "g1,g2"
        w = AiWriter(build_providers())
        seen = []

        def fake_post(url, payload, headers, timeout=12.0):
            key = re.search(r"key=(\w+)", url)
            seen.append(key.group(1) if key else "")
            if "key=g1" in url:
                raise RuntimeError("HTTP 429: quota exceeded")
            return {"candidates": [{"content": {"parts": [
                {"text": "Лонги BTC на $9.00M против $3.40M шортов"}]}}]}

        with mock.patch.object(ai_text, "post_json", fake_post):
            head = w.headline_sync(SNAP)
        self.assertEqual(seen, ["g1", "g2"])               # тот же сервис, второй ключ
        self.assertEqual(head, "Лонги BTC на $9.00M против $3.40M шортов")
        st = w.status()["providers"][0]
        self.assertFalse(st["dead"])                       # сервис жив, ключ исчерпан
        self.assertEqual(st["key_index"], 2)

    def test_all_keys_limited_falls_to_next_service(self):
        os.environ["LIQSCOPE_AI_GEMINI_KEYS"] = "g1,g2"
        os.environ["LIQSCOPE_AI_GROQ_KEY"] = "q-key"

        def fake_post(url, payload, headers, timeout=12.0):
            if "generativelanguage" in url:
                raise RuntimeError("HTTP 429: rate limit exceeded")
            return {"choices": [{"message": {"content": "ETH потерял $3.10M, перевес у лонгов"}}]}

        w = AiWriter(build_providers())
        with mock.patch.object(ai_text, "post_json", fake_post):
            head = w.headline_sync(SNAP)
        self.assertEqual(head, "ETH потерял $3.10M, перевес у лонгов")
        self.assertEqual(w.status()["last"]["provider"], "groq")

    def test_single_key_with_quota_is_not_marked_dead(self):
        os.environ["LIQSCOPE_AI_GEMINI_KEY"] = "g-key"
        w = AiWriter(build_providers())

        def fake_post(url, payload, headers, timeout=12.0):
            raise RuntimeError("HTTP 429: rate limit")

        with mock.patch.object(ai_text, "post_json", fake_post):
            self.assertIsNone(w.headline_sync(SNAP))
        self.assertFalse(w.status()["providers"][0]["dead"])   # лимит обновится

    def test_auth_error_is_still_dead(self):
        os.environ["LIQSCOPE_AI_GEMINI_KEY"] = "g-key"
        w = AiWriter(build_providers())

        def fake_post(url, payload, headers, timeout=12.0):
            raise RuntimeError("HTTP 401: invalid api key")

        with mock.patch.object(ai_text, "post_json", fake_post):
            self.assertIsNone(w.headline_sync(SNAP))
        self.assertTrue(w.status()["providers"][0]["dead"])

    def test_quota_matcher(self):
        self.assertTrue(ai_text.quota_error("HTTP 429: rate limit"))
        self.assertTrue(ai_text.quota_error("resource_exhausted: quota"))
        self.assertTrue(ai_text.quota_error("insufficient credits"))
        self.assertFalse(ai_text.quota_error("HTTP 404: model not found"))
        self.assertFalse(ai_text.auth_error("HTTP 429: rate limit"))
        self.assertTrue(ai_text.auth_error("HTTP 401: invalid api key"))

    def test_custom_provider_keys_list(self):
        os.environ["LIQSCOPE_AI_URL"] = "http://127.0.0.1:11434/v1/chat/completions"
        os.environ["LIQSCOPE_AI_KEYS"] = "c1,c2"
        provs = {p.name: p for p in build_providers()}
        self.assertEqual(provs["custom"].keys, ["c1", "c2"])

    def test_digest_narrative_switches_key(self):
        os.environ["LIQSCOPE_AI_GEMINI_KEYS"] = "g1,g2"
        w = AiWriter(build_providers())
        seen = []
        text = ("Часовые данные показывают: ликвидации BTC за окно превысили три "
                "миллиона долларов, перевес на стороне лонгов, и это заметно на "
                "фоне остальных монет. Объём открытого интереса вырос за тот же "
                "период, а поток CVD перекошен в сторону покупателей.")

        def fake_post(url, payload, headers, timeout=12.0):
            seen.append("g1" if "key=g1" in url else "g2")
            if "key=g1" in url:
                raise RuntimeError("HTTP 429: quota")
            return {"candidates": [{"content": {"parts": [{"text": text}]}}]}

        facts = {"lang": "ru", "hours": 24}
        with mock.patch.object(ai_text, "post_json", fake_post), \
                mock.patch.object(ai_text, "fit_body", lambda t, limit=0: t), \
                mock.patch.object(ai_text, "body_problem", lambda t, lang="ru": ""):
            out = w.narrative_sync(facts)
        self.assertEqual(seen, ["g1", "g2"])
        self.assertEqual(out, text)


class RenderTest(unittest.TestCase):
    def test_ai_head_replaces_template_and_keeps_blocks(self):
        from channel_digest import render_post
        plain = render_post(SNAP, variant=0)
        with_ai = render_post(SNAP, variant=0, head_override="Лонги BTC на $9.00M против $3.40M шортов")
        self.assertIn("Лонги BTC на $9.00M против $3.40M шортов", with_ai)
        self.assertIn("liqscope.online", with_ai)          # хвост на месте
        self.assertNotEqual(plain.splitlines()[0], with_ai.splitlines()[0])
        # «сухие» цифры никуда не делись
        self.assertIn("12.40M", with_ai)

    def test_ai_head_is_escaped(self):
        from channel_digest import render_post
        out = render_post(SNAP, head_override="Рынок & лента <внимание>")
        self.assertIn("Рынок &amp; лента &lt;внимание&gt;", out)


if __name__ == "__main__":
    unittest.main()
