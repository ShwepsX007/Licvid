# Резервная копия `data/`

В `data/` лежат аккаунты (`accounts.db`), статьи, дайджесты, часовые
свёртки и история ликвидаций. В git этого нет. Без копии рестарт диска
стирает кабинет и архив.

## Снять

```bash
sudo bash tools/backup_data.sh /var/backups/liqscope
```

Скрипт делает `sqlite3 accounts.db ".backup"` (не копирует WAL вполпути)
и упаковывает каталог. Хранится 14 дней, ссылка `liqscope-latest.tar.gz`
смотрит на свежий файл.

Строка в cron, раз в сутки:

```cron
15 3 * * * root bash /root/Licvid/tools/backup_data.sh /var/backups/liqscope
```

## Проверить, что копия живая

```bash
mkdir -p /tmp/liq-restore && tar -tzf /var/backups/liqscope/liqscope-latest.tar.gz | head
sqlite3 /tmp/liq-restore/accounts.db "SELECT count(*) FROM users;"   # после распаковки
```

## Вернуть

Остановить сервис, распаковать поверх `data/`, поднять снова:

```bash
sudo systemctl stop licvid
sudo tar -C /root/Licvid/data -xzf /var/backups/liqscope/liqscope-latest.tar.gz
sudo systemctl start licvid
curl -fsS http://127.0.0.1:8000/api/health
```

WAL при старте подхватится сам. Если после аварии `accounts.db-wal`
больше базы — перед стартом можно вызвать `PRAGMA wal_checkpoint(TRUNCATE)`
на остановленной копии; процесс также чекпойнтит WAL сам, раз в 30 минут
и при остановке.
