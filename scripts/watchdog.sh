#!/usr/bin/env bash
# Сторож: проверяет сервисы, базу, диск и свежесть бэкапа.
# Молчит, когда всё хорошо; пишет админу в Telegram, когда плохо.
# Запуск: svoi-watchdog.timer каждые 10 минут (deploy/systemd), вручную —
#   /opt/svoi/scripts/watchdog.sh
# Версию живой сборки против HEAD сверяет scripts/health_agent.py (FR-H12/H13):
# здесь только короткая проверка, чтобы сторож не молчал о старом выкате.
set -uo pipefail

ENV_FILE="${ENV_FILE:-/opt/svoi/.env}"
[ -f "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a

CONTAINER="${CONTAINER:-svoi-postgres}"
USER="${POSTGRES_USER:-svoi}"
DIR="${BACKUP_DIR:-/opt/svoi/backups}"
API_URL="${TOOL_API_URL:-http://127.0.0.1:8000}"
ROOT="${SVOI_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

ALERT=""
check() { curl -sf -m 10 "$1" >/dev/null 2>&1; }

check "$API_URL/health" || ALERT+="· tool-api не отвечает"$'\n'
# L-005: живой процесс должен быть собран из HEAD, иначе дефекты пишут на старую сборку
LIVE_SHA=$(curl -sf -m 10 "$API_URL/health" 2>/dev/null \
  | sed -n 's/.*"git_sha"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
HEAD_SHA=$(git -C "$ROOT" rev-parse --short=12 HEAD 2>/dev/null || true)
if [ -n "$HEAD_SHA" ] && [ -n "$LIVE_SHA" ] && [ "$LIVE_SHA" != "unknown" ] \
   && [ "$LIVE_SHA" != "$HEAD_SHA" ]; then
  ALERT+="· сборка не совпадает с HEAD: живая ${LIVE_SHA}, HEAD ${HEAD_SHA}"$'\n'
fi
docker exec "$CONTAINER" pg_isready -U "$USER" >/dev/null 2>&1 \
  || ALERT+="· postgres недоступен"$'\n'

DISK=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
[ "${DISK:-0}" -gt 85 ] && ALERT+="· диск занят ${DISK}%"$'\n'

BACKUP=$(find "$DIR" -name 'svoi_*.dump' -mtime -1 2>/dev/null | head -1)
[ -z "$BACKUP" ] && ALERT+="· нет свежего бэкапа"$'\n'

if [ -n "$ALERT" ]; then
  echo "Проблемы на сервере SVOI:"
  printf '%s' "$ALERT"
  # Один путь алертов (Ш-30): запись в notification с дедупликацией «проблема + час»,
  # оттуда её заберёт svoi-notify-send с тихими часами и лимитом. Прямая отправка
  # в Telegram осталась только резервом внутри скрипта, если база недоступна.
  PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
  [ -x "$PYTHON_BIN" ] || PYTHON_BIN=python3
  printf '%s' "$ALERT" | "$PYTHON_BIN" "$ROOT/scripts/watchdog_alert.py" \
    || echo "алерт не доставлен: смотри сообщение выше" >&2
  exit 1
fi

exit 0
