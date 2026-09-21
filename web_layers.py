"""☰ Слои графика: пробный доступ для гостей без регистрации.

Терминал открыт всем, но переключатели слоёв (ликвидации, профиль, CVD, OI и
индикаторные окна под графиком) — это то, за чем люди приходят на сайт.
Раньше они были доступны только зарегистрированным: гость видел график и ленту,
а кнопки слоёв у него просто не было — и уйти на регистрацию ему никто не
предлагал.

Теперь у гостя есть **пробный доступ** (по умолчанию 30 минут): кнопка на
месте, слои можно включать и смотреть. Когда время вышло, сайт показывает окно
с предложением зарегистрироваться бесплатно — крестик закрывает окно, и
терминал продолжает работать как раньше, только без слоёв.

Считает время сервер, а не браузер (``Store.layer_trial``): строка на гостя
создаётся при первом обращении и не сбрасывается ни перезагрузкой страницы, ни
чисткой localStorage. Гость без cookie определяется по связке ip + User-Agent —
иначе отключённые cookie давали бы бесконечный пробник.

Сколько минут давать — настройка, и живёт она в двух местах (как время
дайджеста):

* ``LIQSCOPE_LAYERS_TRIAL_MIN`` — переменная окружения. Если она задана, она
  главная: админка показывает, что значение зафиксировано на сервере;
* настройка сайта ``layers_trial_min`` — её правит админка
  (``POST /api/admin/layers/settings``).

``0`` полностью выключает ограничение: слои открыты всем и всегда. Лимит
считается на каждый запрос от ``started_at``, поэтому его правка сразу меняет
остаток времени и у тех, кто уже в терминале: подняли 30 → 60, и гости
получили ещё полчаса с момента своего первого захода.

Ручки:

    GET  /api/layers/trial          — сколько пробного времени осталось гостю
    GET  /api/admin/layers/settings — текущий лимит, откуда он, воронка
    POST /api/admin/layers/settings — сохранить лимит из админки
    POST /api/admin/layers/reset    — сбросить таймер гостю или всем сразу

Ответ ``GET /api/layers/trial``: ``{ok, guest, allowed, left_sec, limit_sec,
expired, ended_at, stats}``. ``allowed: false`` — время вышло: страница убирает
кнопку слоёв и показывает предложение регистрации.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from accounts import COOKIE_VID, LAYERS_TRIAL_SEC

log = logging.getLogger("liqscope.layers")

#: Сколько минут пробного доступа к слоям по умолчанию.
DEFAULT_TRIAL_MIN = LAYERS_TRIAL_SEC // 60
#: Настройка сайта (база): минуты пробника. Правится из админки.
TRIAL_SETTING = "layers_trial_min"
#: Переменная окружения: если задана — значение с сайта не меняется.
TRIAL_ENV = "LIQSCOPE_LAYERS_TRIAL_MIN"
#: Границы: 0 — ограничения нет, больше суток давать бессмысленно.
MIN_TRIAL_MIN, MAX_TRIAL_MIN = 0, 24 * 60

#: Включение пробного периода: гостю — N минут, потом предложение регистрации.
#: Выключено — слои открыты всем и всегда (как сейчас).
TRIAL_ENABLED_SETTING = "layers_trial_enabled"
TRIAL_ENABLED_ENV = "LIQSCOPE_LAYERS_TRIAL_ENABLED"
#: По умолчанию — выключено (слои свободны для всех): так сейчас работает сайт,
#: а включить ограничение админ может в один клик в админке.
DEFAULT_ENABLED = False

#: Метка «аргумент не передан»: пустое значение значит «взять хранилище из ctx».
_DEFAULT = object()


class Ctx:
    """Связка с сервером: база аккаунтов и секрет для хеша адреса."""

    store = None
    secret = ""
    public_url = ""


ctx = Ctx()


def _store_of(store: Any = _DEFAULT):
    """Хранилище: явно переданное (тесты) или то, что подключил сервер."""
    return ctx.store if store is _DEFAULT else store


def _parse_bool(value: Any) -> Optional[bool]:
    """Булево из строки/числа: None — значения нет или оно нечитаемое."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    if s in ("1", "true", "yes", "on", "enabled", "enable"):
        return True
    if s in ("0", "false", "no", "off", "disabled", "disable"):
        return False
    return None


def _minutes(value: Any) -> Optional[int]:
    """Минуты из строки: None — значения нет или оно нечитаемое."""
    if value is None or str(value).strip() == "":
        return None
    try:
        minutes = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return max(MIN_TRIAL_MIN, min(minutes, MAX_TRIAL_MIN))


def env_minutes() -> Optional[int]:
    """Минуты из переменной окружения. None — переменная не задана.

    Мусор в переменной считаем незаданной переменной: сервер должен подняться
    и работать на значении по умолчанию, а не падать из-за опечатки.
    """
    return _minutes(os.getenv(TRIAL_ENV))


def env_enabled() -> Optional[bool]:
    """Включён ли пробный период из переменной окружения. None — не задана."""
    return _parse_bool(os.getenv(TRIAL_ENABLED_ENV))


def setting_minutes(store: Any = _DEFAULT) -> Optional[int]:
    """Минуты из настроек сайта (админка). None — в базе пусто."""
    store = _store_of(store)
    if store is None:
        return None
    try:
        raw = store.get_setting(TRIAL_SETTING, "")
    except Exception as e:                                # noqa: BLE001
        log.debug("слои: настройка недоступна: %s", e)
        return None
    return _minutes(raw)


def setting_enabled(store: Any = _DEFAULT) -> Optional[bool]:
    """Включён ли пробник из настроек сайта. None — в базе пусто."""
    store = _store_of(store)
    if store is None:
        return None
    try:
        raw = store.get_setting(TRIAL_ENABLED_SETTING, "")
    except Exception as e:                                # noqa: BLE001
        log.debug("слои: настройка enabled недоступна: %s", e)
        return None
    return _parse_bool(raw)


def trial_source(store: Any = _DEFAULT) -> str:
    """Откуда взялся лимит: ``env`` — переменная, ``site`` — админка, ``default``."""
    if env_minutes() is not None:
        return "env"
    if setting_minutes(store) is not None:
        return "site"
    return "default"


def trial_enabled_source(store: Any = _DEFAULT) -> str:
    """Откуда взялся флаг enabled: env / site / default."""
    if env_enabled() is not None:
        return "env"
    if setting_enabled(store) is not None:
        return "site"
    return "default"


def trial_locked() -> bool:
    """Лимит задан переменной окружения — с сайта его не поменять."""
    return env_minutes() is not None


def trial_enabled_locked() -> bool:
    """Флаг enabled задан переменной окружения — с сайта его не поменять."""
    return env_enabled() is not None


def trial_enabled(store: Any = _DEFAULT) -> bool:
    """Включён ли пробный период: env → site → DEFAULT_ENABLED (False = свободный доступ)."""
    env = env_enabled()
    if env is not None:
        return bool(env)
    site = setting_enabled(store)
    if site is not None:
        return bool(site)
    return bool(DEFAULT_ENABLED)


def trial_limit_minutes(store: Any = _DEFAULT) -> int:
    """Итоговый лимит в минутах: окружение → настройка сайта → 30 минут."""
    env = env_minutes()
    if env is not None:
        return env
    site = setting_minutes(store)
    if site is not None:
        return site
    return DEFAULT_TRIAL_MIN


def trial_limit_sec(store: Any = _DEFAULT) -> int:
    """Пробник в секундах. ``0`` — ограничения нет (слои открыты всем).

    Если пробный период выключен (enabled=False) — возвращаем 0 независимо от минут:
    слои свободны для всех как сейчас.
    """
    if not trial_enabled(store):
        return 0
    return trial_limit_minutes(store) * 60


def trial_limit_sec_configured(store: Any = _DEFAULT) -> int:
    """Настроенный лимит в секундах без учёта enabled (что было бы если включить)."""
    return trial_limit_minutes(store) * 60


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


def _stats(limit_sec: int) -> Dict[str, Any]:
    """Воронка пробника: сколько гостей знакомятся и сколько уже упёрлось."""
    if ctx.store is None:
        return {"total": 0, "active": 0, "expired": 0, "limit_sec": limit_sec}
    try:
        return ctx.store.layer_trial_stats(limit_sec=limit_sec)
    except Exception as e:                                # noqa: BLE001
        log.debug("слои: сводка недоступна: %s", e)
        return {"total": 0, "active": 0, "expired": 0, "limit_sec": limit_sec}


def admin_payload() -> Dict[str, Any]:
    """Что админке нужно знать о пробном доступе: лимит, источник, воронка."""
    # настроенный лимит (даже если выключен) и фактический (0 если выключен)
    configured_min = trial_limit_minutes()
    configured_sec = configured_min * 60
    limit_sec = trial_limit_sec()
    source = trial_source()
    enabled = trial_enabled()
    enabled_src = trial_enabled_source()
    return {
        "ok": True,
        "minutes": configured_min,
        "limit_sec": limit_sec,
        "configured_sec": configured_sec,
        "configured_min": configured_min,
        "default_min": DEFAULT_TRIAL_MIN,
        "min": MIN_TRIAL_MIN,
        "max": MAX_TRIAL_MIN,
        "source": source,
        "locked": source == "env",
        "env_var": TRIAL_ENV,
        "setting": TRIAL_SETTING,
        "enabled": bool(enabled),
        "enabled_source": enabled_src,
        "enabled_locked": enabled_src == "env",
        "enabled_env_var": TRIAL_ENABLED_ENV,
        "enabled_setting": TRIAL_ENABLED_SETTING,
        "stats": _stats(limit_sec if limit_sec > 0 else configured_sec),
    }


def register_layer_routes(app) -> None:
    router = APIRouter()

    @router.get("/api/layers/trial")
    async def layers_trial(request: Request) -> Dict[str, Any]:
        """Пробный доступ гостя к слоям: остаток времени и решение шлюза."""
        enabled = trial_enabled()
        configured_min = trial_limit_minutes()
        configured_sec = configured_min * 60
        limit = trial_limit_sec()
        from web_account import current_user
        user: Optional[dict] = None
        try:
            user = current_user(request)
        except Exception:                                 # noqa: BLE001
            user = None
        base: Dict[str, Any] = {
            "ok": True,
            "guest": not bool(user),
            "enabled": bool(enabled),
            "limit_sec": limit,
            "configured_sec": configured_sec,
            "configured_min": configured_min,
            "allowed": True,
            "left_sec": None,
            "expired": False,
            "ended_at": 0.0,
            "hits": 0,
        }
        # пробный период выключен — слои открыты всем и всегда
        if not enabled or limit <= 0:
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

    @router.get("/api/admin/layers/settings")
    async def admin_layers_get(request: Request) -> Any:
        """Текущий лимит слоёв: значение, откуда оно и что с гостями."""
        from feedback import _admin
        user, err = _admin(request)
        if err:
            return err
        if ctx.store is None:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        return admin_payload()

    @router.post("/api/admin/layers/settings")
    async def admin_layers_save(request: Request,
                                payload: Optional[dict] = Body(default=None)) -> Any:
        """Сохранить настройки пробника из админки: вкл/выкл + минуты.

        Правка действует сразу и на тех, кто уже в терминале: остаток времени
        считается от первого захода гостя, поэтому новый лимит меняет его
        немедленно. Значение из переменной окружения главнее — тогда честно
        говорим, что с сайта менять нечего.

        Тело: ``{enabled: bool, minutes: int}`` — можно передавать по одному.
        """
        from feedback import _admin
        user, err = _admin(request)
        if err:
            return err
        if ctx.store is None:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        body = payload or {}
        has_minutes = "minutes" in body
        has_enabled = "enabled" in body

        if not has_minutes and not has_enabled:
            return JSONResponse({"ok": False, "error": "bad_value", "key": "minutes"},
                                status_code=400)

        # проверка блокировок окружением
        if has_minutes and trial_locked():
            return JSONResponse({"ok": False, "error": "locked",
                                 "env_var": TRIAL_ENV}, status_code=409)
        if has_enabled and trial_enabled_locked():
            return JSONResponse({"ok": False, "error": "locked",
                                 "env_var": TRIAL_ENABLED_ENV}, status_code=409)

        saved_minutes = None
        saved_enabled = None

        if has_minutes:
            if body.get("minutes") in (None, ""):
                return JSONResponse({"ok": False, "error": "bad_value", "key": "minutes"},
                                    status_code=400)
            minutes = _minutes(body.get("minutes"))
            if minutes is None:
                return JSONResponse({"ok": False, "error": "bad_value", "key": "minutes"},
                                    status_code=400)
            try:
                ctx.store.set_setting(TRIAL_SETTING, str(minutes),
                                      int((user or {}).get("id") or 0))
            except Exception as e:                        # noqa: BLE001
                log.warning("слои: настройка минут не сохранилась: %s", e)
                return JSONResponse({"ok": False, "error": "save"}, status_code=500)
            saved_minutes = minutes
            log.info("слои: пробник теперь %s мин (админ %s)", minutes,
                     (user or {}).get("id"))

        if has_enabled:
            en = _parse_bool(body.get("enabled"))
            if en is None:
                return JSONResponse({"ok": False, "error": "bad_value", "key": "enabled"},
                                    status_code=400)
            try:
                ctx.store.set_setting(TRIAL_ENABLED_SETTING, "1" if en else "0",
                                      int((user or {}).get("id") or 0))
            except Exception as e:                        # noqa: BLE001
                log.warning("слои: настройка enabled не сохранилась: %s", e)
                return JSONResponse({"ok": False, "error": "save"}, status_code=500)
            saved_enabled = bool(en)
            log.info("слои: пробник %s (админ %s)",
                     "включён" if en else "выключен", (user or {}).get("id"))

        out = admin_payload()
        if saved_minutes is not None:
            out["saved"] = saved_minutes
        if saved_enabled is not None:
            out["saved_enabled"] = saved_enabled

        # человекочитаемая подсказка
        en_now = out.get("enabled")
        mins_now = out.get("minutes", 0)
        if not en_now:
            out["note"] = "Пробный доступ выключен: слои открыты всем."
        else:
            if mins_now <= 0:
                out["note"] = "Пробный период включён, но лимит 0 — слои всё ещё открыты всем."
            else:
                out["note"] = f"Пробный доступ включён: гостям {mins_now} мин."
        return out

    @router.post("/api/admin/layers/reset")
    async def admin_layers_reset(request: Request,
                                 payload: Optional[dict] = Body(default=None)) -> Any:
        """Сбросить таймер пробника: одному гостю (``who``) или всем (``all``).

        Сброс не выдаёт бессрочный доступ: строка гостя удаляется, и его
        следующий запрос начинает те же минуты с нуля — это способ дать
        человеку ещё времени, не меняя лимит для всех.
        """
        from feedback import _admin
        user, err = _admin(request)
        if err:
            return err
        if ctx.store is None:
            return JSONResponse({"ok": False, "error": "no_store"}, status_code=503)
        body = payload or {}
        who = str(body.get("who") or "").strip()[:80]
        everybody = bool(body.get("all")) or not who
        try:
            n = ctx.store.layer_trial_reset("" if everybody else who)
        except Exception as e:                            # noqa: BLE001
            log.warning("слои: таймер не сбросился: %s", e)
            return JSONResponse({"ok": False, "error": "reset"}, status_code=500)
        try:
            ctx.store.audit(int((user or {}).get("id") or 0), "layers_reset",
                            ("all=%d" % n) if everybody else f"{who} rows={n}")
        except Exception as e:                            # noqa: BLE001
            log.debug("слои: след в журнале не записался: %s", e)
        log.info("слои: таймер сброшен (%s): строк %s", "всем" if everybody else who, n)
        out = admin_payload()
        out.update({"reset": n, "all": everybody, "who": "" if everybody else who})
        return out

    app.include_router(router)
