"""❤️ Донат: адреса крипто-кошельков в шапке сайта.

Платёжных сервисов нет: показываем адреса и копируем их в буфер обмена.
Адреса — публичные данные блокчейна, секретов здесь не бывает, поэтому
публичная ручка отдаётся без входа.

    GET  /api/donate/wallets        — непустые сети для кнопки «Донат» (кэш 60 с)
    GET  /api/admin/donate/wallets  — все сети, включая пустые (админ)
    POST /api/admin/donate/wallets  — сохранить кошельки (только администратор)

Хранение — системные настройки (``app_settings``, вкладка «Донат»):
``LIQSCOPE_DONATE_WALLET_ETHEREUM`` и так далее по таблице :data:`WALLETS`.
Пустое значение = сеть скрыта в кнопке. Значения читаются из базы настроек
с откатом на переменные окружения, поэтому правка из админки применяется
сразу — перезапускать сервер для смены кошелька не нужно.

Список сетей фиксирован и упорядочен: интерфейс показывает их в этом же
порядке. Новые сети добавляются одной строкой в :data:`WALLETS` — ключ
настройки, подпись и проверка формата берутся из неё.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import app_settings
import web_cache

log = logging.getLogger("liqscope.donate")

#: Сколько держим публичный ответ: адреса меняются руками и редко.
PUBLIC_TTL = 60.0
#: Префикс ключей настроек: LIQSCOPE_DONATE_WALLET_<СЕТЬ>.
KEY_PREFIX = "LIQSCOPE_DONATE_WALLET_"
#: Ручной сброс кэша после сохранения — админка должна видеть своё сразу.
_CACHE = web_cache.TTLCache(ttl=PUBLIC_TTL, maxsize=4)
_CACHE_KEY = "wallets"

#: Предел длины адреса: у всех сетей он короче, а мусор резать надо.
MAX_LEN = 128


class Network(NamedTuple):
    """Сеть для донатов: id в API, подпись, ключ настройки и формат адреса."""

    id: str
    label: str
    short: str
    #: ``evm`` | ``btc`` | ``sol`` | ``tron`` | ``ton`` — какая проверка формата
    kind: str
    #: Точка цвета сети в списке (цвета официальные, из брендбуков сетей)
    color: str


#: Порядок — как в интерфейсе кнопки «Донат».
WALLETS: Tuple[Network, ...] = (
    Network("ethereum", "Ethereum", "ETH", "evm", "#627EEA"),
    Network("bitcoin", "Bitcoin", "BTC", "btc", "#F7931A"),
    Network("solana", "Solana", "SOL", "sol", "#14F195"),
    Network("bnb", "BNB Chain", "BNB", "evm", "#F3BA2F"),
    Network("base", "Base", "BASE", "evm", "#0052FF"),
    Network("polygon", "Polygon", "POL", "evm", "#8247E5"),
    Network("tron", "Tron", "TRX", "tron", "#EF0027"),
    Network("ton", "TON (GRAM)", "TON", "ton", "#0098EA"),
)

BY_ID: Dict[str, Network] = {w.id: w for w in WALLETS}

#: Ключ настройки сети: LIQSCOPE_DONATE_WALLET_ETHEREUM и т. д.
KEYS: Dict[str, str] = {w.id: KEY_PREFIX + w.id.upper() for w in WALLETS}

# --- форматы адресов -------------------------------------------------------
# Проверка мягкая: отсекаем опечатки и чужие сети, но не придираемся к
# контрольным суммам (их считает сам кошелёк).
_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_BTC_BECH32_RE = re.compile(r"^bc1[023456789acdefghjklmnpqrstuvwxyz]{11,71}$", re.I)
_BTC_LEGACY_RE = re.compile(r"^[13][1-9A-HJ-NP-Za-km-z]{25,34}$")
_SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_TRON_RE = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")
#: TON: «дружелюбный» адрес (EQ…/UQ…, 48 символов) или сырой workchain:hex
_TON_FRIENDLY_RE = re.compile(r"^(?:EQ|UQ|0Q|kQ|Ef|Uf)[A-Za-z0-9_-]{46}$")
_TON_RAW_RE = re.compile(r"^-?[0-9]:[0-9a-fA-F]{64}$")

#: Что подсказать человеку, если формат не сошёлся.
FORMAT_HINT: Dict[str, str] = {
    "evm": "нужен адрес вида 0x + 40 шестнадцатеричных знаков",
    "btc": "нужен адрес bc1… (или старый 1… / 3…)",
    "sol": "нужен адрес Solana: 32–44 знака из base58",
    "tron": "нужен адрес вида T… (34 знака)",
    "ton": "нужен адрес TON: EQ…/UQ… (48 знаков) или workchain:hex",
}


class Ctx:
    """Связка с сервером: менеджер настроек — заполняет ``server.py``."""

    settings = None


ctx = Ctx()


def normalize(address: Any) -> str:
    """Обрезка пробелов и невидимых символов — адрес приходит из формы."""
    return str(address or "").strip().strip("\u200b").replace(" ", "")


def validate(net_id: str, address: str) -> str:
    """Проверить адрес сети. Возвращает пустую строку, если всё в порядке.

    Мягкая проверка: пустое значение — это «сеть скрыта», а не ошибка.
    """
    net = BY_ID.get(net_id)
    if net is None:
        return f"неизвестная сеть «{net_id}»"
    addr = normalize(address)
    if not addr:
        return ""
    if len(addr) > MAX_LEN:
        return f"{net.label}: адрес длиннее {MAX_LEN} знаков"
    ok = {
        "evm": lambda a: bool(_EVM_RE.match(a)),
        "btc": lambda a: bool(_BTC_BECH32_RE.match(a) or _BTC_LEGACY_RE.match(a)),
        "sol": lambda a: bool(_SOL_RE.match(a)),
        "tron": lambda a: bool(_TRON_RE.match(a)),
        "ton": lambda a: bool(_TON_FRIENDLY_RE.match(a) or _TON_RAW_RE.match(a)),
    }[net.kind](addr)
    if ok:
        return ""
    return f"{net.label}: {FORMAT_HINT[net.kind]}"


def validate_keys(mapping: Dict[str, str]) -> str:
    """Проверить пачку ключей настроек (канонические имена → значения).

    Возвращает первое сообщение об ошибке или пустую строку. Нужна обоим
    путям сохранения: общей ручке админки и отдельной ``/api/admin/donate``.
    """
    for net in WALLETS:
        key = KEYS[net.id]
        if key not in mapping:
            continue
        err = validate(net.id, mapping[key])
        if err:
            return err
    return ""


def _raw_value(key: str) -> str:
    """Значение настройки: база → окружение (как у остальных настроек)."""
    mgr = ctx.settings
    if mgr is not None:
        try:
            return normalize(mgr.get(key))
        except Exception:  # noqa: BLE001 — настройки не должны ронять ручку
            log.warning("donate: не удалось прочитать настройку %s", key)
    for name in (key, key[len("LIQSCOPE_"):]):
        val = normalize(os.getenv(name))
        if val:
            return val
    return ""


def wallets(public: bool = True) -> List[Dict[str, Any]]:
    """Список сетей для интерфейса.

    ``public=True`` — только непустые адреса (то, что видит гость);
    ``public=False`` — все сети, пустые с пустым ``address`` (для админки).
    """
    out: List[Dict[str, Any]] = []
    for net in WALLETS:
        addr = _raw_value(KEYS[net.id])
        if not addr and public:
            continue
        out.append({"id": net.id, "label": net.label, "short": net.short,
                    "color": net.color, "address": addr})
    return out


def cached_wallets() -> List[Dict[str, Any]]:
    """Публичный список с коротким кэшем: шапка спрашивает его на каждой странице."""
    hit = _CACHE.get(_CACHE_KEY)
    if hit is None:
        hit = wallets(public=True)
        _CACHE.set(_CACHE_KEY, hit)
    return hit


def invalidate_cache() -> None:
    """Сбросить кэш: после сохранения админка должна видеть новое сразу."""
    _CACHE.invalidate(_CACHE_KEY)


def save(mapping: Dict[str, str], actor_id: Optional[int] = None) -> List[str]:
    """Сохранить кошельки в настройках. Возвращает список изменённых сетей."""
    mgr = ctx.settings
    if mgr is None:
        return []
    changed: List[str] = []
    for net in WALLETS:
        key = KEYS[net.id]
        if key not in mapping:
            continue
        value = normalize(mapping[key])
        if value == normalize(mgr.get(key)):
            continue
        if value:
            mgr.set(key, value)
        else:
            mgr.delete(key)          # пусто — сеть скрыта в кнопке
        changed.append(net.id)
    if changed:
        invalidate_cache()
        # В журнал — только сети: адреса и так публичные, но лишнего не пишем.
        log.info("donate: кошельки обновлены (%s)", ", ".join(changed))
    return changed


def admin_user(request: Request) -> Optional[dict]:
    """Текущий пользователь для админских ручек (или None).

    Импорт внутри функции: ``web_account`` сам подключает этот модуль, на
    уровне модуля получился бы цикл.
    """
    try:
        from web_account import current_user
    except Exception:  # noqa: BLE001 — без сайта-аккаунтов ручки просто закрыты
        return None
    return current_user(request)


def register_donate_routes(app) -> None:
    router = APIRouter()

    @router.get("/api/donate/wallets")
    async def api_donate_wallets():
        """Публичный список кошельков: только сети с заполненным адресом."""
        return {"ok": True, "wallets": cached_wallets()}

    @router.get("/api/admin/donate/wallets")
    async def api_admin_donate_wallets(request: Request):
        """Те же данные для админки, но со всеми сетями (пустые — с '')."""
        user = admin_user(request)
        if not user:
            return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
        if not user.get("is_admin"):
            return JSONResponse({"ok": False, "error": "admin"}, status_code=403)
        return {"ok": True, "wallets": wallets(public=False)}

    @router.post("/api/admin/donate/wallets")
    async def api_admin_donate_save(request: Request):
        """Сохранение кошельков отдельной ручкой (та же проверка, что в админке)."""
        user = admin_user(request)
        if not user:
            return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
        if not user.get("is_admin"):
            return JSONResponse({"ok": False, "error": "admin"}, status_code=403)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — пустое тело = ничего не сохраняем
            body = {}
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "Нужен объект с адресами"},
                                status_code=400)
        # Значения берём и по каноническому имени, и по короткому алиасу,
        # и по имени сети («ethereum»): форма админки шлёт ключи настроек.
        items = {name: value for name, value in body.items()
                 if isinstance(value, str)}
        mapping: Dict[str, str] = {}
        for net in WALLETS:
            key = KEYS[net.id]
            # Форма присылает ключ настройки, короткое имя из .env
            # (DONATE_WALLET_BTC) или просто имя сети («ton»).
            if key in items:
                mapping[key] = items[key]
                continue
            for name, value in items.items():
                if name == net.id or app_settings.canonical_key(name) == key:
                    mapping[key] = value
                    break
        err = validate_keys(mapping)
        if err:
            return JSONResponse({"ok": False, "error": err}, status_code=400)
        changed = save(mapping, actor_id=user.get("id"))
        return {"ok": True, "changed": changed, "wallets": wallets(public=False)}

    app.include_router(router)
