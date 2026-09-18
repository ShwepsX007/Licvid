"""

Спецификации контрактов Gate: пути загрузки, кэш и честная причина.

Что проверяем (сеть не нужна — сессия поддельная):
  * листинг `/contracts` разбирается, множители попадают в карту;
  * первый адрес лежит — берётся второй (запасной домен);
  * листинг недоступен, но точечные `/contracts/{name}` отвечают — подписка
    собирается из них;
  * REST лежит совсем — спецификации берутся из кэша на диске, и это видно в
    статусе («взяты из кэша»), а не выглядит как молчание;
  * ошибка тела ответа (Gate отвечает 200 с `{"label": ...}`) не считается
    успехом;
  * при полном провале наружу уходит настоящая причина (HTTP 403/502, DNS),
    а не общее «нет связи».

Запуск: python3 tests/test_gate_specs.py
"""

import asyncio
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import market_feed as mf  # noqa: E402
from market_feed import MarketFeed, _gate_parse_specs  # noqa: E402

ok = 0
fail = 0

# Повторы внутри загрузчика в тесте не нужны: проверяем логику, а не паузы.
mf.GATE_SPECS_RETRY_SLEEP = 0.0
mf.GATE_SPECS_BACKOFF = 0.0

TMP = tempfile.mkdtemp(prefix="gate-specs-")


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  ok   " + name)
    else:
        fail += 1
        print("  FAIL " + name + (("  [" + str(extra) + "]") if extra != "" else ""))


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeSession:
    """Сессия с заранее заданными ответами: (подстрока URL, ответ|исключение).

    Первое совпадение выигрывает, поэтому порядок важен. Ничего не подошло —
    404 (как «такого адреса нет»).
    """

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        for part, resp in self.routes:
            if part in url:
                if isinstance(resp, Exception):
                    raise resp
                return FakeResp(resp)
        return FakeResp({"message": "not found"}, status=404)


def make_feed(session):
    feed = MarketFeed(on_liquidation=lambda ev: None, on_price=lambda *a: None,
                      exchanges=["gate"])
    feed._session = session
    feed.symbols = ["BTC_USDT", "ETH_USDT", "SOL_USDT"]
    return feed


# Кэш спецификаций — всегда во временном файле, чтобы не трогать рабочий data/
class specs_file:
    def __init__(self, name="gate_contracts.json", content=None):
        self.path = os.path.join(TMP, name)
        self.content = content

    def __enter__(self):
        self.old = mf.GATE_SPECS_FILE
        mf.GATE_SPECS_FILE = self.path
        if self.content is not None:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.content, f)
        return self.path

    def __exit__(self, *a):
        mf.GATE_SPECS_FILE = self.old
        return False


print("разбор ответа REST")
check("листинг", _gate_parse_specs([
    {"name": "BTC_USDT", "quanto_multiplier": "0.0001"},
    {"name": "ETH_USDT", "quanto_multiplier": 0.01},
]) == {"BTC_USDT": 0.0001, "ETH_USDT": 0.01})
check("один контракт", _gate_parse_specs(
    {"name": "SOL_USDT", "quanto_multiplier": 1}) == {"SOL_USDT": 1.0})
check("нулевой множитель отбрасывается",
      _gate_parse_specs([{"name": "X_USDT", "quanto_multiplier": 0}]) == {})
check("ошибка в теле не считается успехом",
      _gate_parse_specs({"label": "INVALID", "message": "bad request"}) == {})
check("мусор не падает", _gate_parse_specs("nope") == {})
check("адресов минимум два (есть запасной домен)", len(mf.GATE_RESTS) >= 2,
      mf.GATE_RESTS)


print("1) листинг по основному адресу")
with specs_file("main.json") as path:
    feed = make_feed(FakeSession([
        ("/contracts", [{"name": "BTC_USDT", "quanto_multiplier": "0.0001"}]),
    ]))
    res = asyncio.run(feed._gate_load_specs())
    check("множители загружены", res and feed.gate_multipliers == {"BTC_USDT": 0.0001},
          (res, feed.gate_multipliers))
    check("источник записан в статус",
          feed.status["gate"].extra.get("specs_source") == mf.GATE_RESTS[0],
          feed.status["gate"].extra)
    check("кэш на диск сложен", os.path.exists(path) and
          json.load(open(path, encoding="utf-8"))["multipliers"]["BTC_USDT"] == 0.0001)


print("2) основной домен лежит — берём запасной")
with specs_file("fallback.json"):
    feed = make_feed(FakeSession([
        (mf.GATE_RESTS[1] + "/contracts",
         [{"name": "ETH_USDT", "quanto_multiplier": "0.01"}]),
    ]))
    res = asyncio.run(feed._gate_load_specs())
    check("запасной адрес спас положение",
          res and "ETH_USDT" in feed.gate_multipliers, (res, feed.gate_multipliers))
    check("в статусе виден рабочий адрес",
          feed.status["gate"].extra.get("specs_source") == mf.GATE_RESTS[1],
          feed.status["gate"].extra)


print("3) листинг недоступен — догружаем по контрактам")
with specs_file("per_contract.json"):
    feed = make_feed(FakeSession([
        ("/contracts/BTC_USDT", {"name": "BTC_USDT", "quanto_multiplier": "0.0001"}),
        ("/contracts/ETH_USDT", {"name": "ETH_USDT", "quanto_multiplier": "0.01"}),
        ("/contracts/SOL_USDT", {"name": "SOL_USDT", "quanto_multiplier": "1"}),
    ]))
    res = asyncio.run(feed._gate_load_specs(needed=["BTC_USDT", "ETH_USDT",
                                                   "SOL_USDT"]))
    check("точечная догрузка собрала все три контракта",
          res and len(feed.gate_multipliers) == 3, (res, feed.gate_multipliers))
    check("источник — «по контрактам»",
          feed.status["gate"].extra.get("specs_source") == "по контрактам",
          feed.status["gate"].extra)


print("4) REST лежит совсем — спасает кэш на диске")
with specs_file("cached.json", {"ts": 1, "multipliers": {"BTC_USDT": 0.0001}}):
    feed = make_feed(FakeSession([("/contracts", RuntimeError("HTTP 403 Forbidden"))]))
    res = asyncio.run(feed._gate_load_specs(needed=["BTC_USDT"]))
    check("кэш подхвачен", res and feed.gate_multipliers == {"BTC_USDT": 0.0001},
          (res, feed.gate_multipliers))
    check("в статусе честно написано, что это кэш",
          feed.status["gate"].extra.get("specs_source") == "кэш"
          and "кэш" in (feed.status["gate"].last_error or ""),
          feed.status["gate"].last_error)


print("5) совсем ничего — наружу уходит настоящая причина")
with specs_file("нет-такого.json"):
    feed = make_feed(FakeSession([("/contracts", RuntimeError("HTTP 403 Forbidden"))]))
    res = asyncio.run(feed._gate_load_specs(needed=["BTC_USDT"]))
    err = feed.status["gate"].extra.get("specs_error") or ""
    check("загрузка честно провалилась", res is False)
    check("в причине виден HTTP-код, а не «нет связи»", "403" in err, err)
    check("last_error тоже объясняет",
          "REST Gate" in (feed.status["gate"].last_error or ""),
          feed.status["gate"].last_error)
    check("причина читается, а не обрезана посреди слова",
          not err.startswith("|") and "ect to host" not in err, err)


print("6) точечная догрузка не может тянуться бесконечно")
mf.GATE_SPECS_PER_CONTRACT_SEC = 0.05
with specs_file("slow.json"):
    feed = make_feed(FakeSession([("/contracts/", RuntimeError("HTTP 502 Bad Gateway"))]))
    t0 = time.monotonic()
    res = asyncio.run(feed._gate_load_specs(needed=["A_USDT", "B_USDT", "C_USDT"]))
    dt = time.monotonic() - t0
    check("предел времени соблюдён (не минуты)",
          res is False and dt < 1.0, f"{dt:.2f}с")
mf.GATE_SPECS_PER_CONTRACT_SEC = 20.0


async def _listener_error():
    feed = make_feed(FakeSession([("/contracts", RuntimeError("HTTP 502 Bad Gateway"))]))
    try:
        await feed._gate_liquidations()
    except RuntimeError as e:
        return str(e)
    return ""


with specs_file("нет-такого-2.json"):
    err_text = asyncio.run(_listener_error())
    check("в ошибке слушателя видна причина", "502" in err_text, err_text)

print("\nитог: %d ок, %d ошибок" % (ok, fail))
sys.exit(1 if fail else 0)
