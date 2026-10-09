"""
market_events.py — ingestion-слой рыночных данных LiqScope.

Единый внутренний формат события и шина между биржевыми коннекторами и
потребителями:

    Binance ─┐
    Bybit ───┤
    OKX ─────┤
    Gate ────┤ → Normalizer → Event Bus → consumers
    Bitget ──┤
    HTX ─────┘

* **Коннектор** знает только транспорт своей биржи (WebSocket, пинги,
  подписки) и публикует ``MarketEvent`` в шину — см. ``MarketFeed``.
* **Нормализатор** превращает кадр биржи в события: ``parse_*_msg`` в
  ``market_feed.py`` + реестр ``LIQUIDATION_NORMALIZERS`` →
  ``liquidation_events(source, payload, **ctx)``.
* **Потребители** (``MarketFeed._consume_*``) подписаны на шину и не знают,
  откуда пришло событие: живые цены, поток сделок для CVD, лента
  ликвидаций. Новый источник = коннектор + запись в реестре — потребители
  не меняются.

Шина — **не** очередь: подписчики вызываются напрямую, по порядку подписки,
в том же await-контексте, что и публикация. Горячий путь (сделки и
ликвидации идут сотнями в секунду) не должен платить аллокациями очередей
и планированием задач, а исключение в подписчике слышит сам коннектор —
как раньше, когда колбэк вызывался напрямую.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Dict, List, NamedTuple, Optional

__all__ = ["MarketEvent", "EventBus", "EVENT_TYPES"]

#: Типы событий шины. Расширять список можно, но имена — часть контракта
#: между коннекторами и потребителями: пусть живут в одном месте.
EVENT_TYPES = ("trade", "liquidation", "price")


class MarketEvent(NamedTuple):
    """Одно рыночное событие в едином внутреннем формате.

    Поля по типам:

    * ``type="trade"`` — сделка с ленты биржи: ``side`` — сторона ТЕЙКЕРА
      (``"BUY"``/``"SELL"``, может быть ``""``, если биржа не сообщает),
      ``qty`` — в базовой монете; цена и CVD — дело потребителя.
    * ``type="liquidation"`` — ликвидация: ``side`` — какая позиция вынесена
      (``"LONG"``/``"SHORT"``), ``usd`` — сумма по данным биржи (``0`` —
      потребитель посчитает из price×qty), ``kind="tape"`` — событие выведено
      из ленты сделок (hl_infer) и подтверждено /info, ``details`` — чем
      именно подтверждён вывод (адрес жертвы, markPx, method).
    * ``type="price"`` — обновление цены: ``candle`` — слепок 1m-свечи
      (``None`` для REST-тикеров).
    """

    source: str                    # binance / bybit / okx / …
    symbol: str                    # канонический "BTC_USDT"
    ts: float                      # epoch, секунды
    type: str                      # trade / liquidation / price
    price: float
    qty: float
    side: str = ""
    usd: float = 0.0
    kind: Optional[str] = None
    details: Optional[dict] = None
    candle: Optional[dict] = None


#: Подписчик — асинхронная функция одного события.
EventHandler = Callable[[MarketEvent], Awaitable[None]]


class EventBus:
    """Прямая рассылка событий подписчикам по типу.

    Без очередей и буферов: ``publish`` последовательно ``await``-ит
    подписчиков в порядке подписки. Один подписчик не задерживает чужие
    события дольше, чем длится сам вызов, а ошибка летит в коннектор,
    который опубликовал событие, — supervisors перезапустят его, как и
    раньше при ошибке в колбэке.
    """

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[EventHandler]] = {}

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Подписаться на события типа; порядок подписок = порядок вызовов."""
        self._subscribers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> bool:
        """Отписаться (в основном для тестов). True — подписка была."""
        handlers = self._subscribers.get(event_type)
        if not handlers:
            return False
        try:
            handlers.remove(handler)
            return True
        except ValueError:
            return False

    def subscriber_count(self, event_type: Optional[str] = None) -> int:
        """Сколько подписчиков у типа (или суммарно по всем типам)."""
        if event_type is not None:
            return len(self._subscribers.get(event_type) or ())
        return sum(len(v) for v in self._subscribers.values())

    async def publish(self, event: MarketEvent) -> int:
        """Разослать событие подписчикам его типа.

        Возвращает число вызванных подписчиков (0 — подписчиков нет:
        событие просто никому не нужно, это не ошибка).
        """
        handlers = self._subscribers.get(event.type)
        if not handlers:
            return 0
        for handler in handlers:
            await handler(event)
        return len(handlers)
