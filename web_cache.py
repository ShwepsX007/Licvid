"""Короткий in-memory кэш тяжёлых GET-ответов (/api/health, /api/digest, ...).

Один процесс uvicorn — один общий словарь: для кэша на секунды этого
достаточно, Redis не нужен. Выключается целиком переменной окружения
``LIQSCOPE_API_CACHE=0`` (тесты гоняют со свежим ответом всегда).
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Optional, Tuple


def cache_enabled() -> bool:
    """Выключен ли кэш API. Читаем при каждом вызове: тесты и админка
    могут дёргать флаг без рестарта процесса."""
    return os.getenv("LIQSCOPE_API_CACHE", "1").strip() not in ("0", "false", "no", "off")


class TTLCache:
    """Потокобезопасный dict с TTL и капом размера (LRU-приблизительный:
    при переполнении выбрасываем самые старые записи)."""

    def __init__(self, ttl: float, maxsize: int = 256):
        self.ttl = max(0.0, float(ttl))
        self.maxsize = max(1, int(maxsize))
        self._d: Dict[Any, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any) -> Optional[Any]:
        if not cache_enabled() or self.ttl <= 0:
            return None
        now = time.time()
        with self._lock:
            hit = self._d.get(key)
            if not hit:
                return None
            at, val = hit
            if now - at >= self.ttl:
                self._d.pop(key, None)
                return None
            return val

    def set(self, key: Any, value: Any) -> None:
        if not cache_enabled() or self.ttl <= 0:
            return
        now = time.time()
        with self._lock:
            self._d[key] = (now, value)
            if len(self._d) > self.maxsize:
                # самые старые — первыми (вставка упорядочена по времени)
                for old in list(self._d)[: len(self._d) - self.maxsize]:
                    self._d.pop(old, None)

    def invalidate(self, key: Any = None) -> None:
        """Сбросить одну запись или весь кэш (после публикации/настроек)."""
        with self._lock:
            if key is None:
                self._d.clear()
            else:
                self._d.pop(key, None)
