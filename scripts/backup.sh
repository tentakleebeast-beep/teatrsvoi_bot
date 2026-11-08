#!/usr/bin/env bash
# Ежедневный бэкап базы. Без восстановления бэкап бэкапом не считается:
# проверка — docs/infrastructure/35 §8.
set -euo pipefail

CONTAINER="${CONTAINER:-svoi-postgres}"
DB="${POSTGRES_DB:-svoi}"
USER="${POSTGRES_USER:-svoi}"
DIR="${BACKUP_DIR:-${SVOI_BACKUP_DIR:-/opt/svoi/backups}}"
# V-027: глубина хранения — одна неделя. Дампов держим ровно BACKUP_KEEP
# последних: счёт по именам (в них дата и время), а не по mtime, иначе
# скопированный или восстановленный файл меняет возраст и выпадает из окна.
# Поэтому ротация по mtime НЕ делается: единственное правило — оставить
# BACKUP_KEEP самых новых по имени. KEEP_DAYS относится только к копии
# объектного хранилища (storage_backup.py), у дампов его нет.
KEEP_DAYS="${KEEP_DAYS:-7}"
BACKUP_KEEP="${BACKUP_KEEP:-7}"

rotate_dumps() {
  local dir="$1" keep="$2" total excess
  total=$(find "$dir" -maxdepth 1 -type f -name 'svoi_*.dump' | wc -l | tr -d ' ')
  excess=$((total - keep))
  [ "$excess" -gt 0 ] || return 0
  find "$dir" -maxdepth 1 -type f -name 'svoi_*.dump' -print \
    | LC_ALL=C sort \
    | sed -n "1,${excess}p" \
    | while IFS= read -r old; do
        rm -f -- "$old"
        echo "ротация: удалён старый дамп $old"
      done
}


mkdir -p "$DIR"
STAMP=$(date +%Y-%m-%d_%H%M)
FILE="$DIR/svoi_$STAMP.dump"

docker exec "$CONTAINER" pg_dump -U "$USER" -Fc "$DB" > "$FILE"

# пустой или обрезанный дамп — ошибка, а не «бэкап»
if [ ! -s "$FILE" ]; then
  echo "бэкап пустой: $FILE" >&2
  exit 1
fi
if ! docker exec -i "$CONTAINER" pg_restore --list < "$FILE" > /dev/null; then
  echo "дамп не читается pg_restore: $FILE" >&2
  exit 1
fi

rotate_dumps "$DIR" "$BACKUP_KEEP"
DUMPS=$(find "$DIR" -maxdepth 1 -type f -name 'svoi_*.dump' | wc -l | tr -d ' ')
echo "бэкап готов: $FILE ($(du -h "$FILE" | cut -f1)); дампов в каталоге: $DUMPS (храним $BACKUP_KEEP)"
df -h "$DIR" | awk 'NR==2 {print "свободно на диске: " $4 " (" $5 " занято)"}'

# Файлы бота (Ш-1, docs/development/80): объектное хранилище — тот же прогон и тот же
# срок хранения, чтобы не заводить второй cron, который забудут проверить.
SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
if [ "${STORAGE_BACKUP:-1}" = "1" ]; then
  "$PYTHON" "$SCRIPTS_DIR/storage_backup.py" --dir "$DIR/storage" --keep-days "$KEEP_DAYS"
fi
