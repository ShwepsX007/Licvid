"""
Офлайн-тесты дисковой истории ликвидаций (JSONL).

Теперь история пишется дневными файлами (history.HistoryStore), но прежний
одиночный файл сервер по-прежнему умеет прочитать и перенести в дневные шарды
при первом запуске — на этом и держится обновление без потери данных.
Здесь проверяем чтение одиночного файла и переезд событий в шарды.

Запуск:  /tmp/venv/bin/python tests/test_history_file.py
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from history import HistoryStore, day_key, load_legacy_jsonl  # noqa: E402
from server import load_history_file  # noqa: E402

ok = 0
fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {name}")
    else:
        fail += 1
        print(f"  FAIL {name} {extra}")


def ev(i, ts, sym="BTC_USDT"):
    return {"id": f"id-{i}", "symbol": sym, "exchange": "binance",
            "side": "SELL", "position": "LONG", "price": 100.0 + i,
            "qty": 1.0, "usd": 1000.0 + i, "timestamp": ts}


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for row in rows:
            if isinstance(row, str):
                f.write(row + "\n")
            else:
                import json
                f.write(json.dumps(row) + "\n")


print("чтение одиночного файла прежних версий")
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "hist.jsonl")
    now = time.time()
    write_jsonl(path, [ev(i, now - 100 + i) for i in range(10)])
    rows = load_history_file(path, maxlen=1000, ttl_hours=24)
    check("файл создан", os.path.exists(path))
    check("прочитали все 10", len(rows) == 10, len(rows))
    check("порядок по времени", [r["id"] for r in rows] == [f"id-{i}" for i in range(10)])
    check("поля на месте", rows[0]["usd"] == 1000.0 and rows[0]["symbol"] == "BTC_USDT")

    old_path = os.path.join(d, "old.jsonl")
    write_jsonl(old_path, [ev(99, now - 25 * 3600)])
    check("TTL отсекает старое",
          load_history_file(old_path, maxlen=1000, ttl_hours=24) == [])
    check("месячный TTL такое событие берёт",
          len(load_history_file(old_path, maxlen=1000, ttl_hours=744)) == 1)

    rows = load_history_file(path, maxlen=3, ttl_hours=24)
    check("maxlen=3 хвост", [r["id"] for r in rows] == ["id-7", "id-8", "id-9"], rows)

    write_jsonl(path, ["{ мусор", '{"id": "broken-no-fields"}'])
    rows = load_history_file(path, maxlen=1000, ttl_hours=24)
    check("битые строки пропущены", len(rows) == 10, len(rows))
    check("нет файла → []", load_history_file(os.path.join(d, "no.jsonl"), 10, 24) == [])

    # запись с одним id тоже считается событием (так же читал прежний загрузчик),
    # а мусор без id отбрасывается: 10 валидных + 1 «одно id» = 11
    legacy = load_legacy_jsonl(path, limit=100)
    check("load_legacy_jsonl читает то же самое", len(legacy) == 11, len(legacy))

print("переезд старого файла в дневные шарды")
with tempfile.TemporaryDirectory() as d:
    now = time.time()
    path = os.path.join(d, "liq_history.jsonl")
    store = HistoryStore(path, ttl_hours=744)
    write_jsonl(path, [ev(i, now - 3600 + i) for i in range(10)])
    moved = store.import_legacy()
    check("перенесли 10 событий", moved == 10, moved)
    check("старый файл убран", not os.path.exists(path))
    check("создан дневной файл",
          os.path.exists(store.shard_path(day_key(now - 3600))))
    rows = store.query(now - 7200, now + 60, limit=100)
    check("события читаются из шарда", len(rows) == 10, len(rows))
    check("свёртки посчитались",
          store.totals(now - 7200, now + 60)["count"] == 10)

    # повторный запуск: файла уже нет — ничего не ломается
    check("повторный переезд молчит", store.import_legacy() == 0)

print(f"\nитог: {ok} ок, {fail} ошибок")
sys.exit(1 if fail else 0)
