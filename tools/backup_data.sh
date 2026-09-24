#!/usr/bin/env bash
# Ежедневный снимок data/: аккаунты, статьи, дайджесты, история.
# Восстановление — в deploy/BACKUP.md.
#
#   sudo bash tools/backup_data.sh
#   sudo bash tools/backup_data.sh /var/backups/liqscope
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${LIQSCOPE_DATA_DIR:-$ROOT/data}"
DEST="${1:-/var/backups/liqscope}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$DEST"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [[ -f "$DATA/accounts.db" ]]; then
    sqlite3 "$DATA/accounts.db" ".backup '$WORK/accounts.db'"
fi
# остальное — файлы. sqlite копируем отдельно, чтобы не взять полусобранный WAL
tar -C "$DATA" -cf - . \
    --exclude './accounts.db' \
    --exclude './accounts.db-wal' \
    --exclude './accounts.db-shm' \
    | tar -C "$WORK" -xf -
tar -C "$WORK" -czf "$DEST/liqscope-$STAMP.tar.gz" .
ln -sfn "liqscope-$STAMP.tar.gz" "$DEST/liqscope-latest.tar.gz"
# две недели снимков
find "$DEST" -name 'liqscope-*.tar.gz' -mtime +14 -delete
echo "backup: $DEST/liqscope-$STAMP.tar.gz"
