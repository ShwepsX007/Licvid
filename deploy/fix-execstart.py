#!/usr/bin/env python3
"""Оставить в licvid.service ровно одну ExecStart.

Две строки при Type=simple → «Unit licvid.service has a bad unit file setting».
Токены и Environment не трогает. Хост как у живого процесса (0.0.0.0:8000),
чтобы nginx/сайт не отвалились.
"""
from __future__ import annotations

from pathlib import Path

UNIT = Path("/etc/systemd/system/licvid.service")
KEEP = (
    "ExecStart=/root/Licvid/venv/bin/python3 -m uvicorn "
    "server:app --host 0.0.0.0 --port 8000\n"
)


def main() -> int:
    if not UNIT.is_file():
        print(f"нет файла {UNIT}")
        return 1
    text = UNIT.read_text()
    out = []
    kept = False
    dropped = 0
    for ln in text.splitlines(True):
        if ln.lstrip().startswith("ExecStart="):
            if kept:
                dropped += 1
                continue
            out.append(KEEP)
            kept = True
            continue
        out.append(ln)
    if not kept:
        # вставить перед Restart=, иначе в конец [Service]
        inserted = False
        rebuilt = []
        for ln in out:
            if (not inserted) and ln.lstrip().startswith("Restart="):
                rebuilt.append(KEEP)
                inserted = True
            rebuilt.append(ln)
        if not inserted:
            rebuilt.append(KEEP)
        out = rebuilt
        kept = True
    UNIT.write_text("".join(out))
    left = [ln.strip() for ln in UNIT.read_text().splitlines()
            if ln.lstrip().startswith("ExecStart=")]
    print("осталось ExecStart:")
    for ln in left:
        print(" ", ln)
    print(f"удалено дублей: {dropped}")
    if len(left) != 1:
        print("всё ещё больше одной строки — смотрите drop-in:")
        print("  grep ExecStart /etc/systemd/system/licvid.service.d/*")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
