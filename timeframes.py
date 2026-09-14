"""Таймфреймы терминала: минуты ↔ интервалы бирж.

Дневной ТФ — 1440 минут. У Bybit дневной интервал «D», не «1440».
parse_tf принимает и число, и строку («1440», «1d»): иначе проверка
`tf in TF_MINUTES` молча отбрасывает строку и график остаётся на 5м.
"""
from __future__ import annotations

from typing import Mapping, Optional

TF_MINUTES = [1, 5, 15, 60, 240, 1440]

TF_BINANCE = {1: "1m", 5: "5m", 15: "15m", 60: "1h", 240: "4h", 1440: "1d"}
TF_BYBIT = {1: "1", 5: "5", 15: "15", 60: "60", 240: "240", 1440: "D"}
TF_OKX = {1: "1m", 5: "5m", 15: "15m", 60: "1H", 240: "4H", 1440: "1D"}
OKX_CVD_SEC = {1: 60, 5: 300, 15: 900, 60: 3600, 240: 14400, 1440: 86400}

_TF_ALIASES = {
    "1m": 1, "5m": 5, "15m": 15,
    "1h": 60, "1H": 60, "60m": 60,
    "4h": 240, "4H": 240, "240m": 240,
    "1d": 1440, "1D": 1440, "d": 1440, "D": 1440, "day": 1440,
}


def parse_tf(tf) -> Optional[int]:
    """Минуты из TF_MINUTES или None. «1440» и 1440 — одно и то же."""
    if tf is None:
        return None
    if isinstance(tf, str):
        s = tf.strip()
        if not s:
            return None
        if s in _TF_ALIASES:
            return _TF_ALIASES[s]
        tf = s
    try:
        tf = int(tf)
    except (TypeError, ValueError):
        return None
    return tf if tf in TF_MINUTES else None


def kline_interval(mapping: Mapping[int, str], tf_min) -> Optional[str]:
    """Интервал биржи для ТФ. Неизвестный ТФ → None, а не молчаливые 5м."""
    parsed = parse_tf(tf_min)
    if parsed is None:
        return None
    return mapping.get(parsed)
