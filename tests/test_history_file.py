"""
Офлайн-тесты дисковой истории ликвидаций (JSONL).

Запуск:  python3 tests/test_history_file.py
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import (append_history_event, load_history_file,
                    trim_history_file)

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


print("запись/чтение")
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "hist.jsonl")
    now = time.time()
    for i in range(10):
        append_history_event(ev(i, now - 100 + i), path)
    rows = load_history_file(path, maxlen=1000, ttl_hours=24)
    check("файл создан", os.path.exists(path))
    check("прочитали все 10", len(rows) == 10, len(rows))
    check("порядок по времени", [r["id"] for r in rows] == [f"id-{i}" for i in range(10)])
    check("поля на месте", rows[0]["usd"] == 1000.0 and rows[0]["symbol"] == "BTC_USDT")

    # TTL: событие старше 24 ч отсекается
    rows = load_history_file(path, maxlen=1000, ttl_hours=24)
    old_path = os.path.join(d, "old.jsonl")
    append_history_event(ev(99, now - 25 * 3600), old_path)
    rows = load_history_file(old_path, maxlen=1000, ttl_hours=24)
    check("TTL отсекает старое", rows == [], rows)

    # maxlen: держим только хвост
    rows = load_history_file(path, maxlen=3, ttl_hours=24)
    check("maxlen=3 хвост", [r["id"] for r in rows] == ["id-7", "id-8", "id-9"], rows)

    # битые строки не роняют чтение
    with open(path, "a", encoding="utf-8") as f:
        f.write("{ мусор\n")
        f.write('{"id": "broken-no-fields"}\n')
    rows = load_history_file(path, maxlen=1000, ttl_hours=24)
    check("битые строки пропущены", len(rows) == 10, len(rows))

    # отсутствующий файл → []
    check("нет файла → []", load_history_file(os.path.join(d, "no.jsonl"), 10, 24) == [])

print("append не пишет при пустом пути")
append_history_event(ev(1, time.time()), "")
check("пустой путь молча", True)

print("урезание файла")
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "big.jsonl")
    now = time.time()
    # маленький файл не трогаем
    for i in range(5):
        append_history_event(ev(i, now - 100 + i), path)
    trim_history_file(path, maxlen=100, ttl_hours=24)
    rows = load_history_file(path, maxlen=1000, ttl_hours=24)
    check("маленький файл цел", len(rows) == 5, len(rows))

print(f"\nитог: {ok} ок, {fail} ошибок")
sys.exit(1 if fail else 0)
