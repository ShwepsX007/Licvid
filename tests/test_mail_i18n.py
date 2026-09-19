"""Письма на языке сайта: регистрация, вход по ссылке, привязка, сброс.

Что проверяем (тем же приёмом, что ``tests/test_bot_i18n.py``):

    * все пять текстов писем собраны одинаково: нет языка, где забыли
      строку, кнопку или подпись под кнопкой;
    * письмо уходит на выбранном языке: для ru кириллица есть, для
      en/zh/hi/es её нет вовсе — забытый русский кусок виден сразу;
    * тема письма и ``<html lang>`` тоже на выбранном языке;
    * ``?lang=zh-CN`` и любое незнакомое значение приводятся к языку сайта;
    * язык берётся из тела запроса, ``?lang=`` и cookie — тем же порядком,
      что у страниц (``web_account._lang_code``).

Запуск:  python3 -m pytest tests/test_mail_i18n.py -q
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import mailer  # noqa: E402

CYR = re.compile(r"[А-Яа-яЁё]")
LANGS = ("ru", "en", "zh", "hi", "es")
KINDS = ("verify", "login", "attach", "reset")
FIELDS = ("subject", "title", "lines", "button", "footnote")


class MailTextsTest(unittest.TestCase):
    """Тексты: одинаковый набор полей и никакой кириллицы вне русского."""

    def test_all_languages_present(self) -> None:
        self.assertEqual(sorted(mailer.MAIL_TEXT), sorted(LANGS))
        self.assertEqual(sorted(mailer.MAIL_UI), sorted(LANGS))

    def test_same_fields_in_every_language(self) -> None:
        for kind in KINDS:
            base = mailer.MAIL_TEXT["ru"][kind]
            for lang in LANGS:
                with self.subTest(kind=kind, lang=lang):
                    block = mailer.MAIL_TEXT[lang][kind]
                    self.assertEqual(sorted(block), sorted(base))
                    for field in FIELDS + ("title_name",) * ("title_name" in base):
                        self.assertTrue(str(block[field]).strip(),
                                        f"{lang}/{kind}: пустое поле {field}")
                    if kind != "attach":       # в привязке Telegram есть {who}
                        self.assertEqual(len(block["lines"]), len(base["lines"]))
                    else:
                        self.assertIn("{who}", " ".join(block["lines"]))

    def test_no_cyrillic_outside_russian(self) -> None:
        for lang in ("en", "zh", "hi", "es"):
            for kind in KINDS:
                text = " ".join(str(v) for v in mailer.MAIL_TEXT[lang][kind].values())
                with self.subTest(lang=lang, kind=kind):
                    self.assertIsNone(CYR.search(text), f"{lang}/{kind}: русский текст")
            ui = " ".join(str(v) for v in mailer.MAIL_UI[lang].values())
            with self.subTest(lang=lang, ui=True):
                self.assertIsNone(CYR.search(ui), f"{lang}: русский текст в подписях")


class MailLangTest(unittest.TestCase):
    """Приведение языка: «zh-CN» → «zh», незнакомое → русский."""

    def test_normalize(self) -> None:
        self.assertEqual(mailer.Mailer.lang("zh-CN"), "zh")
        self.assertEqual(mailer.Mailer.lang("ES_es"), "es")
        self.assertEqual(mailer.Mailer.lang("hi-IN"), "hi")
        self.assertEqual(mailer.Mailer.lang("klingon"), "ru")
        self.assertEqual(mailer.Mailer.lang(""), "ru")
        self.assertEqual(mailer.Mailer.lang(None), "ru")

    def test_hello_uses_name(self) -> None:
        self.assertIn("Аня", mailer._hello(mailer.MAIL_TEXT["ru"]["verify"], "Аня"))
        self.assertIn("Ann", mailer._hello(mailer.MAIL_TEXT["zh"]["verify"], "Ann"))
        self.assertNotIn("{name}", mailer._hello(mailer.MAIL_TEXT["es"]["verify"], ""))
        self.assertNotIn("{name}", mailer._hello(mailer.MAIL_TEXT["es"]["verify"], "Ana"))

    def test_lang_code_from_request_body_query_and_cookie(self) -> None:
        import web_account

        class Req:
            def __init__(self, query="", cookie="", head=""):
                self.query_params = {"lang": query} if query else {}
                self.cookies = {"liqscope_lang": cookie} if cookie else {}
                self.headers = {"accept-language": head}

        self.assertEqual(web_account._lang_code(Req(), {"language": "zh-CN"}), "zh")
        self.assertEqual(web_account._lang_code(Req(query="hi")), "hi")
        self.assertEqual(web_account._lang_code(Req(cookie="es")), "es")
        self.assertEqual(web_account._lang_code(Req(head="es-ES,es;q=0.9")), "es")
        self.assertEqual(web_account._lang_code(Req(head="en-US,en;q=0.9")), "en")
        # явный выбор важнее заголовка браузера
        self.assertEqual(web_account._lang_code(Req(query="zh", head="en-US")), "zh")
        self.assertEqual(web_account._lang_code(Req()), "ru")

    def test_hints_in_every_site_language(self) -> None:
        import web_account

        for lang in LANGS:
            for code in ("bad_email", "rate", "taken", "captcha_wrong"):
                with self.subTest(lang=lang, code=code):
                    hint = web_account._email_error(lang, code)
                    self.assertTrue(hint and hint != code)
                    if lang != "ru":
                        self.assertIsNone(CYR.search(hint), f"{lang}/{code}: русский текст")
        self.assertEqual(web_account._email_error("ru", "no_such_code"), "no_such_code")
        self.assertEqual(web_account._email_error("es", "no_such_code"), "no_such_code")


class MailDeliveryTest(unittest.TestCase):
    """Письмо целиком: тема, язык разметки и текст — на выбранном языке."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.mailer = mailer.Mailer(
            public_url="https://liqscope.online",
            sender="LiqScope <no-reply@liqscope.online>",
            transport=mailer.FileTransport(self.tmp.name),
            enabled=True,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _emails(self) -> list:
        folder = self.tmp.name
        out = []
        for name in sorted(os.listdir(folder)):
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                out.append(fh.read())
        return out

    def test_letters_go_out_in_chosen_language(self) -> None:
        for i, lang in enumerate(LANGS):
            with self.subTest(lang=lang):
                self.mailer.send_verify(f"u{i}@liqscope.test", f"T{i}", name="Ann", lang=lang)
                self.mailer.send_login_link(f"u{i}@liqscope.test", f"L{i}", lang=lang)
                self.mailer.send_reset(f"u{i}@liqscope.test", f"R{i}", lang=lang)
                self.mailer.send_tg_attach(f"u{i}@liqscope.test", f"A{i}",
                                           tg_name="Kolya", name="Ann", lang=lang)
        letters = self._emails()
        self.assertEqual(len(letters), 4 * len(LANGS))
        for lang in LANGS:
            part = [txt for txt in letters if f'<html lang="{lang}"' in txt]
            with self.subTest(lang=lang):
                self.assertEqual(len(part), 4, f"писем на {lang} не четыре")
                for txt in part:
                    buttons = [mailer.MAIL_TEXT[lang][k]["button"] for k in KINDS]
                    self.assertTrue(any(btn in txt for btn in buttons),
                                    f"{lang}: в письме нет ни одной переведённой кнопки")
        for txt in letters:
            lang = re.search(r'<html lang="([\w-]+)"', txt).group(1)
            with self.subTest(lang=lang, cyr=True):
                if lang == "ru":
                    self.assertIsNotNone(CYR.search(txt))
                else:
                    self.assertIsNone(CYR.search(txt), "русский текст в письме")

    def test_subject_translated(self) -> None:
        for i, lang in enumerate(LANGS):
            self.mailer.send_verify(f"s{i}@liqscope.test", f"S{i}", lang=lang)
        for txt in self._emails():
            lang = re.search(r'<html lang="([\w-]+)"', txt).group(1)
            subject = re.search(r"Subject: (.*)", txt).group(1)
            with self.subTest(lang=lang):
                self.assertIn(mailer.MAIL_TEXT[lang]["verify"]["subject"], subject)
                self.assertTrue(subject.startswith(f"{mailer.BRAND}: "))

    def test_address_survives_bad_language(self) -> None:
        self.assertTrue(self.mailer.send_verify("x@liqscope.test", "T", lang="tlh"))
        txt = self._emails()[0]
        self.assertIn('<html lang="ru"', txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
