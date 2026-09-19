"""Разбор загруженного фото: multipart без python-multipart и JSON data-URL.

Загрузка пачкой: админ выбирает в проводнике сразу десятки фото, и в одном
multipart-теле приходит несколько частей с ``filename``. Раньше разбор
возвращал только первую часть — из пачки сохранялось одно фото, и это
выглядело как «лимит в 7 фото»: приходилось добавлять по одному.
"""
from __future__ import annotations

import base64
import re
from typing import Any, Dict, List, Tuple

#: Сколько файлов берём из одного запроса. Больше — режем молча: остальное
#: админ догрузит следующей пачкой (кап самой базы всё равно больше).
MAX_FILES = 60

PHOTO_ERR = {
    "empty": "пустой файл",
    "too_big": "файл больше 12 МБ",
    "not_image": "нужен jpg, png или webp",
    "limit": "фото больше лимита",
    "bad_data": "не удалось прочитать файл",
}

#: Имя файла из заголовка части: ``filename="photo.jpg"`` или ``filename*=UTF-8''…``
_FILENAME_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";\r\n]+)"?', re.I)


def _boundary(content_type: str) -> str:
    for part in (content_type or "").split(";"):
        part = part.strip()
        if part.lower().startswith("boundary="):
            return part.split("=", 1)[1].strip().strip('"')
    return ""


def parse_multipart_files(body: bytes, content_type: str) -> List[Tuple[bytes, str]]:
    """Все файлы из multipart/form-data: список ``(данные, имя)``.

    Порядок сохраняем — админ видит плитки в том же порядке, в каком выбирал
    файлы. Части без ``filename`` (обычные поля формы) пропускаем.
    """
    bound = _boundary(content_type)
    if not bound or not body:
        return []
    sep = b"--" + bound.encode("ascii", "replace")
    out: List[Tuple[bytes, str]] = []
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
        m = _FILENAME_RE.search(hs)
        name = m.group(1).strip().strip('"') if m else ""
        out.append((data, name))
        if len(out) >= MAX_FILES:
            break
    return out


def parse_multipart_file(body: bytes, content_type: str) -> Tuple[bytes, str]:
    """Первый файл из multipart/form-data — для одиночных загрузок."""
    files = parse_multipart_files(body, content_type)
    return files[0] if files else (b"", "")


def _decode_data_url(raw: str) -> bytes:
    if "," in raw and raw.strip().lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        return base64.b64decode(raw, validate=False)
    except Exception:
        return b""


def decode_json_photo(body: Dict[str, Any] | None) -> Tuple[bytes, str]:
    """Одно фото из JSON: ``{"data": "data:image/png;base64,…"}``."""
    items = decode_json_photos(body)
    return items[0] if items else (b"", "")


def decode_json_photos(body: Dict[str, Any] | None) -> List[Tuple[bytes, str]]:
    """Пачка фото из JSON.

    Понимаем и старый одиночный формат (``data`` + ``filename``), и пачку
    (``files``: список словарей ``{data, filename}``) — так фото можно
    отправлять не только формой, но и скриптом.
    """
    body = body or {}
    items: List[Tuple[bytes, str]] = []
    files = body.get("files")
    if isinstance(files, list):
        for it in files[:MAX_FILES]:
            if isinstance(it, dict):
                items.append((_decode_data_url(str(it.get("data") or "")),
                              str(it.get("filename") or "")))
            elif isinstance(it, str):
                items.append((_decode_data_url(it), ""))
    if not items:
        items.append((_decode_data_url(str(body.get("data") or "")),
                      str(body.get("filename") or "")))
    return [(blob, name) for blob, name in items if blob]
