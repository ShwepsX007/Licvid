"""API расчётных уровней ликвидаций: чтение, статус и админка.

Модуль — только сеть и разбор ответа: считает ``liq_levels.LevelsEngine``,
данные собирают фиды. Здесь:

    GET  /api/liq_levels                    — уровни, магниты, кумулятив, покрытие
    GET  /api/liq_levels/status             — состояние движка и данных
    GET  /api/admin/liq_levels/settings     — настройки (админ)
    POST /api/admin/liq_levels/settings     — правка настроек (админ)
    POST /api/admin/liq_levels/recalibrate  — пересчитать калибровку по факту (админ)

Ручки чтения отдают тот же словарь, что и движок, плюс ``ok``. Ошибка расчёта
не должна ломать страницу: клиент покажет «нет данных» и оговорку, поэтому
исключение превращается в понятный ответ с кодом 500.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("liqscore.web_levels")

#: Сколько монет прогреваем по одному ручному запросу пересчёта.
RECALIBRATE_MAX = 8


def _engine(get_engine: Optional[Callable[[], Any]], requestless: Any = None) -> Any:
    fn = get_engine or requestless
    if not callable(fn):
        return None
    try:
        return fn()
    except Exception:                           # noqa: BLE001
        return None


def register_liq_level_routes(app, get_engine: Optional[Callable[[], Any]] = None,
                              get_feed: Optional[Callable[[], Any]] = None,
                              store: Any = None,
                              snapshot: Optional[Callable[[], Dict[str, Any]]] = None) -> None:
    router = APIRouter()

    def _admin(request: Request):
        """Проверка админа — тем же способом, что в остальной админке."""
        try:
            from feedback import _admin
            return _admin(request)
        except Exception as e:                  # noqa: BLE001
            log.debug("админ-проверка недоступна: %s", e)
            return None, JSONResponse({"ok": False, "error": "forbidden"},
                                      status_code=403)

    def _feed_session() -> Any:
        if not callable(get_feed):
            return None
        try:
            feed = get_feed()
        except Exception:                       # noqa: BLE001
            return None
        return getattr(feed, "session", None) if feed is not None else None

    @router.get("/api/liq_levels")
    async def api_liq_levels(symbol: str = Query("BTC_USDT"),
                             min_usd: float = Query(0.0),
                             side: str = Query(""),
                             window_hours: float = Query(0.0),
                             price: float = Query(0.0),
                             recalibrate: int = Query(0),
                             force: int = Query(0)) -> Any:
        """Расчётные уровни ликвидаций по монете.

        ``min_usd`` — отсечка массы уровня, ``side`` — ``long``/``short``
        (вниз/вверх), ``price`` — цена, от которой считаются расстояния
        (клиент передаёт цену со своего графика). ``recalibrate=1`` —
        пересчитать калибровку по факту; ``force=1`` — мимо кэша.
        """
        engine = _engine(get_engine)
        if engine is None:
            return JSONResponse({"ok": False, "error": "off"}, status_code=503)
        session = _feed_session()
        try:
            data = await engine.payload(
                str(symbol or "").upper(), session=session,
                min_usd=float(min_usd or 0.0), side=str(side or ""),
                window_hours=(float(window_hours) if window_hours else None),
                price=(float(price) if price else None),
                force=bool(force), recalibrate=(True if recalibrate else None))
        except Exception as e:                  # noqa: BLE001
            log.warning("уровни ликвидаций %s: %s", symbol, e)
            return JSONResponse({"ok": False, "error": "calc",
                                 "detail": f"{type(e).__name__}: {e}"[:200]},
                                status_code=500)
        data["ok"] = True
        return data

    @router.get("/api/liq_levels/status")
    async def api_liq_levels_status() -> Any:
        """Что с данными инструмента: движок, фиды и слепок для алертов."""
        engine = _engine(get_engine)
        out: Dict[str, Any] = {"ok": engine is not None, "ts": time.time()}
        if engine is None:
            return JSONResponse(dict(out, error="off"), status_code=503)
        out["engine"] = engine.status()
        if callable(snapshot):
            try:
                snap = snapshot() or {}
                out["snapshot"] = {"symbols": sorted(snap),
                                   "at": max((float(v.get("ts") or 0)
                                              for v in snap.values()), default=0.0)}
            except Exception as e:              # noqa: BLE001
                out["snapshot"] = {"error": str(e)[:120]}
        for name, src in (("side", getattr(engine, "side", None)),
                          ("risk", getattr(engine, "risk", None)),
                          ("profile", getattr(engine, "profile", None))):
            fn = getattr(src, "status", None) or getattr(src, "stats", None)
            if callable(fn):
                try:
                    out[name] = fn()
                except Exception as e:          # noqa: BLE001
                    out[name] = {"error": str(e)[:120]}
        return out

    # --- админка ----------------------------------------------------------
    @router.get("/api/admin/liq_levels/settings")
    async def admin_liq_levels_get(request: Request) -> Any:
        """Настройки инструмента и калибровки — для админки."""
        user, err = _admin(request)
        if err:
            return err
        engine = _engine(get_engine)
        if engine is None:
            return JSONResponse({"ok": False, "error": "off"}, status_code=503)
        return {"ok": True, "status": engine.status(),
                "settings": dict(engine._settings),
                "fields": _fields()}

    @router.post("/api/admin/liq_levels/settings")
    async def admin_liq_levels_set(request: Request,
                                   payload: Optional[dict] = Body(default=None)) -> Any:
        """Правка настроек: неизвестные поля игнорируются, известные — числа."""
        user, err = _admin(request)
        if err:
            return err
        engine = _engine(get_engine)
        if engine is None:
            return JSONResponse({"ok": False, "error": "off"}, status_code=503)
        patch = payload if isinstance(payload, dict) else {}
        if not patch:
            return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
        saved = engine.update_settings(patch)
        if store is not None:
            try:
                store.audit(int((user or {}).get("id") or 0), "liq_levels_settings",
                            str(sorted(patch))[:300])
            except Exception:                   # noqa: BLE001
                pass
        return {"ok": True, "settings": saved, "fields": _fields()}

    @router.post("/api/admin/liq_levels/recalibrate")
    async def admin_liq_levels_recalibrate(request: Request,
                                           payload: Optional[dict] = Body(default=None)) -> Any:
        """Пересчитать калибровку по факту ликвидаций (для выбранных монет)."""
        user, err = _admin(request)
        if err:
            return err
        engine = _engine(get_engine)
        if engine is None:
            return JSONResponse({"ok": False, "error": "off"}, status_code=503)
        body = payload if isinstance(payload, dict) else {}
        symbols: List[str] = []
        given = body.get("symbols") or ([body.get("symbol")] if body.get("symbol") else [])
        if isinstance(given, str):
            given = [x for x in given.replace(",", " ").split() if x]
        for sym in given or []:
            s = str(sym or "").upper()
            if s and s not in symbols:
                symbols.append(s)
        symbols = symbols[:RECALIBRATE_MAX]
        if not symbols:
            return JSONResponse({"ok": False, "error": "no_symbols"}, status_code=400)
        session = _feed_session()
        out: Dict[str, Any] = {}
        for sym in symbols:
            try:
                data = await engine.payload(sym, session=session, force=True,
                                            recalibrate=True)
                out[sym] = data.get("calibration") or {}
            except Exception as e:              # noqa: BLE001
                out[sym] = {"applied": False, "reason": f"{type(e).__name__}: {e}"[:160]}
        if store is not None:
            try:
                store.audit(int((user or {}).get("id") or 0), "liq_levels_recalibrate",
                            ",".join(symbols)[:300])
            except Exception:                   # noqa: BLE001
                pass
        return {"ok": True, "calibration": out}

    app.include_router(router)


def _fields() -> List[dict]:
    """Описание настроек для формы в админке (ключ, заголовок, границы)."""
    from liq_levels import DEFAULT_SETTINGS
    return [
        {"key": "enabled", "title": "Инструмент включён", "type": "bool"},
        {"key": "calibrate", "title": "Калибровать по факту", "type": "bool"},
        {"key": "window_hours", "title": "Окно истории OI, ч", "type": "num",
         "min": 6, "max": 1440},
        {"key": "step_rel", "title": "Шаг сетки уровней (доля цены)", "type": "num",
         "min": 0.0002, "max": 0.01},
        {"key": "spread_rel", "title": "Ширина размазывания (доля цены)", "type": "num",
         "min": 0.002, "max": 0.05},
        {"key": "lev_scale", "title": "Множитель плеч", "type": "num",
         "min": 0.3, "max": 3},
        {"key": "spread_scale", "title": "Множитель ширины", "type": "num",
         "min": 0.3, "max": 3},
        {"key": "min_doi_rel", "title": "Порог прироста OI (доля)", "type": "num",
         "min": 0.0001, "max": 0.05},
        {"key": "min_liq_usd", "title": "Минимальное событие, USD", "type": "num",
         "min": 0, "max": 10_000_000},
        {"key": "calib_days", "title": "Дней факта для калибровки", "type": "num",
         "min": 1, "max": 30},
        {"key": "max_levels", "title": "Сколько уровней отдавать", "type": "num",
         "min": 20, "max": 2000},
        {"key": "magnet_min_share", "title": "Порог магнита (доля массы)", "type": "num",
         "min": 0.005, "max": 0.2},
        {"key": "lev_dist", "title": "Распределение плеч", "type": "dist",
         "default": DEFAULT_SETTINGS.get("lev_dist")},
    ]
