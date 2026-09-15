"""Разбор загруженного фото: multipart без python-multipart и JSON data-URL."""
from __future__ import annotations

import base64
import re
from typing import Any, Dict, Tuple

PHOTO_ERR = {
    "empty": "пустой файл",
    "too_big": "файл больше 12 МБ",
    "not_image": "нужен jpg, png или webp",
    "limit": "слишком много фото (макс. 40)",
    "bad_data": "не удалось прочитать файл",
}


def parse_multipart_file(body: bytes, content_type: str) -> Tuple[bytes, str]:
    """Достаёт первый файл из multipart/form-data без python-multipart."""
    bound = ""
    for part in (content_type or "").split(";"):
        part = part.strip()
        if part.lower().startswith("boundary="):
            bound = part.split("=", 1)[1].strip().strip('"')
            break
    if not bound or not body:
        return b"", ""
    sep = b"--" + bound.encode("ascii", "replace")
    for chunk in body.split(sep):
        chunk = chunk.lstrip(b"\r\n")
        if not chunk or chunk.startswith(b"--"):
            continue
        header, sep2, data = chunk.partition(b"\r\n\r\n")
        if not sep2:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]
        hs = header.decode("utf-8", "replace")
        if "filename=" not in hs.lower():
            continue
        name = ""
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";\r\n]+)"?', hs, re.I)
        if m:
            name = m.group(1).strip().strip('"')
        return data, name
    return b"", ""


def decode_json_photo(body: Dict[str, Any] | None) -> Tuple[bytes, str]:
    raw = str((body or {}).get("data") or "")
    if "," in raw and raw.strip().lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        blob = base64.b64decode(raw, validate=False)
    except Exception:
        return b"", ""
    return blob, str((body or {}).get("filename") or "")
