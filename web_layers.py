"""☰ Слои графика: пробный доступ для гостей без регистрации.

Терминал открыт всем, но переключатели слоёв (ликвидации, профиль, CVD, OI и
индикаторные окна под графиком) — это то, за чем люди приходят на сайт.
Раньше они были доступны только зарегистрированным: гость видел график и ленту,
а кнопки слоёв у него просто не было — и уйти на регистрацию ему никто не
предлагал.

Теперь у гостя есть **30 минут** пробного доступа: кнопка на месте, слои можно
включать и смотреть. Когда время вышло, сайт показывает окно с предложением
зарегистрироваться бесплатно — крестик закрывает окно, и терминал продолжает
работать как раньше, только без слоёв.

Считает время сервер, а не браузер (``Store.layer_trial``): строка на гостя
создаётся при первом обращении и не сбрасывается ни перезагрузкой страницы, ни
чисткой localStorage. Гость без cookie определяется по связке ip + User-Agent —
иначе отключённые cookie давали бы бесконечный пробник.

Настройка: ``LIQSCOPE_LAYERS_TRIAL_MIN`` — минуты пробника (по умолчанию 30,
``0`` полностью выключает ограничение: слои открыты всем и всегда).

Ручки:

    GET /api/layers/trial     — сколько пробного времени осталось этому гостю

Ответ: ``{ok, guest, allowed, left_sec, limit_sec, expired, ended_at, stats}``.
``allowed: false`` — время вышло: страница убирает кнопку слоёв и показывает
предложение регистрации.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request

from accounts import COOKIE_VID, LAYERS_TRIAL_SEC

log = logging.getLogger("liqscope.layers")

#: Сколько минут пробного доступа к слоям (LIQSCOPE_LAYERS_TRIAL_MIN).
DEFAULT_TRIAL_MIN = LAYERS_TRIAL_SEC // 60


class Ctx:
    """Связка с сервером: база аккаунтов и секрет для хеша адреса."""

    store = None
    secret = ""
    public_url = ""


ctx = Ctx()


def trial_limit_sec() -> int:
    """Пробник в секундах: из окружения, с разумными границами.

    ``0`` — ограничения нет (слои открыты всем): удобно выключить шлюз на
    время отладки, не трогая код.
    """
    raw = os.getenv("LIQSCOPE_LAYERS_TRIAL_MIN")
    if raw is None or str(raw).strip() == "":
        minutes = DEFAULT_TRIAL_MIN
    else:
        try:
            minutes = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            minutes = DEFAULT_TRIAL_MIN
    minutes = max(0, min(minutes, 24 * 60))
    return minutes * 60


def _who(request: Request) -> str:
    """Ключ гостя: vid из cookie, а без него — связка ip + User-Agent.

    Гость с отключёнными cookie не должен получать бесконечный пробник, поэтому
    для него считаем по адресу (и UA: у одного адреса может быть несколько
    человек за NAT).
    """
    try:
        vid = str(request.cookies.get(COOKIE_VID) or "").strip()
    except Exception:                                     # noqa: BLE001
        vid = ""
    if vid:
        return "v:" + vid[:60]
    ip = ""
    try:
        from web_geo import _client_ip
        ip = _client_ip(request)
    except Exception:                                     # noqa: BLE001
        ip = ""
    ua = ""
    try:
        ua = str((getattr(request, "headers", {}) or {}).get("user-agent") or "")
    except Exception:                                     # noqa: BLE001
        ua = ""
    if not ip:
        return ""
    try:
        from geoip import hash_ip
        return "i:" + hash_ip(ctx.secret or "liqscope", ip + "|" + ua[:80])[:40]
    except Exception:                                     # noqa: BLE001
        return ""


def register_layer_routes(app) -> None:
    router = APIRouter()

    @router.get("/api/layers/trial")
    async def layers_trial(request: Request) -> Dict[str, Any]:
        """Пробный доступ гостя к слоям: остаток времени и решение шлюза."""
        limit = trial_limit_sec()
        from web_account import current_user
        user: Optional[dict] = None
        try:
            user = current_user(request)
        except Exception:                                 # noqa: BLE001
            user = None
        base: Dict[str, Any] = {
            "ok": True, "guest": not bool(user), "limit_sec": limit,
            "allowed": True, "left_sec": None, "expired": False,
            "ended_at": 0.0, "hits": 0,
        }
        # ограничение выключено — слои открыты всем
        if limit <= 0:
            base["disabled"] = True
            return base
        if user:
            return base                                    # зарегистрирован: без ограничений
        if ctx.store is None:
            # базы нет (странная конфигурация) — не выдумываем стену
            log.debug("слои: нет хранилища, шлюз пропускает")
            return base
        who = _who(request)
        try:
            res = ctx.store.layer_trial(who, limit_sec=limit)
        except Exception as e:                            # noqa: BLE001
            log.debug("слои: не удалось посчитать пробник: %s", e)
            return base
        if not res.get("tracked"):
            # cookie нет вовсе — считать не по чему, гостя не перекрываем
            return base
        base.update({
            "allowed": bool(res.get("allowed")),
            "left_sec": res.get("left"),
            "expired": bool(res.get("expired")),
            "ended_at": res.get("ended") or 0.0,
            "hits": int(res.get("hits") or 0),
        })
        return base

    app.include_router(router)
