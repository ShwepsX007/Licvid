"""Загрузка фото сводки с сайта: multipart без python-multipart и JSON data-URL."""
from __future__ import annotations

import base64
import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from web_upload import decode_json_photo, parse_multipart_file  # noqa: E402


JPEG = b"\xff\xd8\xff\xe0" + b"fake-jpeg-payload" * 4 + b"\xff\xd9"


def _multipart(blob: bytes, filename: str = "phone.jpg", field: str = "file") -> tuple[bytes, str]:
    bound = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    body = (
        f"--{bound}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        "Content-Type: image/jpeg\r\n"
        "\r\n"
    ).encode("utf-8") + blob + f"\r\n--{bound}--\r\n".encode("ascii")
    return body, f"multipart/form-data; boundary={bound}"


class MultipartPhotoTest(unittest.TestCase):
    def test_parse_browser_multipart(self):
        body, ctype = _multipart(JPEG, "кофе.jpg")
        data, name = parse_multipart_file(body, ctype)
        self.assertEqual(data, JPEG)
        self.assertEqual(name, "кофе.jpg")

    def test_parse_quoted_boundary(self):
        bound = "----X"
        body = (
            f"--{bound}\r\n"
            'Content-Disposition: form-data; name="file"; filename="a.png"\r\n'
            "\r\n"
        ).encode("ascii") + JPEG + f"\r\n--{bound}--\r\n".encode("ascii")
        data, name = parse_multipart_file(body, f'multipart/form-data; boundary="{bound}"')
        self.assertEqual(data, JPEG)
        self.assertEqual(name, "a.png")

    def test_parse_skips_non_file_fields(self):
        bound = "----B"
        extra = (
            f"--{bound}\r\n"
            'Content-Disposition: form-data; name="note"\r\n'
            "\r\n"
            "hello\r\n"
            f"--{bound}\r\n"
            'Content-Disposition: form-data; name="file"; filename="x.jpg"\r\n'
            "\r\n"
        ).encode("ascii") + JPEG + f"\r\n--{bound}--\r\n".encode("ascii")
        data, name = parse_multipart_file(extra, f"multipart/form-data; boundary={bound}")
        self.assertEqual(data, JPEG)
        self.assertEqual(name, "x.jpg")

    def test_json_data_url(self):
        raw = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode("ascii")
        data, name = decode_json_photo({"filename": "old.jpg", "data": raw})
        self.assertEqual(data, JPEG)
        self.assertEqual(name, "old.jpg")

    def test_json_bare_base64(self):
        data, name = decode_json_photo({
            "filename": "bare.jpg",
            "data": base64.b64encode(JPEG).decode("ascii"),
        })
        self.assertEqual(data, JPEG)
        self.assertEqual(name, "bare.jpg")


if __name__ == "__main__":
    unittest.main()
