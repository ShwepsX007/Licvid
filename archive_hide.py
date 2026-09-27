"""Скрытые выпуски: админ снял черновик, и архив суток его больше не возвращает.

После перезагрузки страница может показать день или час, собранный из
месячных свёрток. В JSON такого выпуска нет, в Telegram он не уходил.
Обычное удаление из JSON его не держит: следующий запрос снова соберёт
ту же свёртку. Этот файл запоминает, что админ уже убрал день или пост.
Запись в JSON (настоящий выпуск) скрытие не прячет — её удаляет свой архив.
"""
from __future__ import annotations

import json
import os
from typing import Iterable, Optional


class ArchiveHide:
    def __init__(self, path: str = "") -> None:
        self.path = path or ""
        self.error = ""
        self.digest_days: set = set()
        self.hourly_ids: set = set()
        self.hourly_days: set = set()
        self._load()

    def _load(self) -> None:
        if not self.path or not os.path.isfile(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:                           # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            return
        if not isinstance(data, dict):
            return
        self.digest_days = _ids(data.get("digest_days"))
        self.hourly_ids = _ids(data.get("hourly_ids"))
        self.hourly_days = _ids(data.get("hourly_days"))

    def _save(self) -> None:
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            payload = {
                "digest_days": sorted(self.digest_days),
                "hourly_ids": sorted(self.hourly_ids),
                "hourly_days": sorted(self.hourly_days),
            }
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            self.error = ""
        except Exception as e:                           # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"

    def digest_hidden(self, day: str) -> bool:
        return str(day or "") in self.digest_days

    def hourly_hidden(self, rec: Optional[dict]) -> bool:
        rec = rec or {}
        rid = str(rec.get("id") or "")
        day = str(rec.get("day") or "")
        return bool(rid and rid in self.hourly_ids) or bool(day and day in self.hourly_days)

    def hide_digest(self, day: str) -> None:
        day = str(day or "")
        if not day or day in self.digest_days:
            return
        self.digest_days.add(day)
        self._save()

    def hide_hourly(self, pid: str) -> None:
        pid = str(pid or "")
        if not pid or pid in self.hourly_ids:
            return
        self.hourly_ids.add(pid)
        self._save()

    def hide_hourly_day(self, day: str, ids: Optional[Iterable[str]] = None) -> None:
        day = str(day or "")
        changed = False
        if day and day not in self.hourly_days:
            self.hourly_days.add(day)
            changed = True
        for pid in ids or []:
            pid = str(pid or "")
            if pid and pid not in self.hourly_ids:
                self.hourly_ids.add(pid)
                changed = True
        if changed:
            self._save()


def _ids(raw) -> set:
    if not isinstance(raw, list):
        return set()
    return {str(x) for x in raw if str(x or "")}
