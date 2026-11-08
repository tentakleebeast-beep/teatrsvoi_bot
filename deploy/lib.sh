#!/usr/bin/env bash
# Общие функции скриптов deploy/ — подключается через `source lib.sh`.
# Здесь только чтение настроек и вывод: никаких изменений на сервере.
set -euo pipefail

DEPLOY_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(cd "$DEPLOY_DIR/.." && pwd)
ENV_FILE=${SVOI_DEPLOY_ENV:-$DEPLOY_DIR/svoi.env}

log()  { printf '\n== %s\n' "$*"; }
info() { printf '   %s\n' "$*"; }
warn() { printf '   ВНИМАНИЕ: %s\n' "$*" >&2; }
die()  { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }

# Единая точка чтения настроек: скрипты не содержат адресов внутри себя.
load_env() {
  [ -f "$ENV_FILE" ] || die "нет $ENV_FILE — скопируйте deploy/svoi.env.example в deploy/svoi.env и заполните"
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a

  : "${SVOI_DOMAIN:?SVOI_DOMAIN не задан в $ENV_FILE}"
  : "${SVOI_REPO:?SVOI_REPO не задан в $ENV_FILE}"
  : "${SVOI_ACME_EMAIL:?SVOI_ACME_EMAIL не задан в $ENV_FILE}"

  SVOI_ROOT=${SVOI_ROOT:-/opt/svoi}
  SVOI_USER=${SVOI_USER:-svoi}
  SVOI_TZ=${SVOI_TZ:-Europe/Samara}
  SVOI_REF=${SVOI_REF:-main}
  SVOI_API_PORT=${SVOI_API_PORT:-8000}
  SVOI_PG_PORT=${SVOI_PG_PORT:-5432}
  SVOI_PG_CONTAINER=${SVOI_PG_CONTAINER:-svoi-postgres}
  SVOI_DB_NAME=${SVOI_DB_NAME:-svoi}
  SVOI_DB_USER=${SVOI_DB_USER:-svoi}
  SVOI_BACKUP_DIR=${SVOI_BACKUP_DIR:-$SVOI_ROOT/backups}
  # Ключ деплоя принадлежит рабочему пользователю: git запускается через runuser,
  # и /root/.ssh для него не существует (docs/infrastructure/75 §6.3).
  SVOI_GIT_KEY=${SVOI_GIT_KEY:-/home/$SVOI_USER/.ssh/id_ed25519}
  export SVOI_ROOT SVOI_USER SVOI_TZ SVOI_REF SVOI_API_PORT SVOI_PG_PORT \
         SVOI_PG_CONTAINER SVOI_DB_NAME SVOI_DB_USER SVOI_BACKUP_DIR SVOI_GIT_KEY
}

require_root() {
  [ "$(id -u)" = "0" ] || die "нужен root: sudo $0 $*"
}

require_docker() {
  command -v docker >/dev/null 2>&1 || die "docker не установлен — сначала: sudo $0 provision"
  docker info >/dev/null 2>&1 || die "docker не отвечает: запущен ли демон, есть ли права у $USER"
}

# Ждём готовности Postgres, а не «поспим и надеемся».
wait_for_pg() {
  local tries=${1:-60}
  local i
  for i in $(seq 1 "$tries"); do
    if docker exec "$SVOI_PG_CONTAINER" pg_isready -U "$SVOI_DB_USER" -d "$SVOI_DB_NAME" \
        >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  die "Postgres не поднялся за ${tries}s: docker logs $SVOI_PG_CONTAINER"
}

# .env на сервере: 600, владелец — рабочий пользователь.
env_file() { printf '%s/.env\n' "$SVOI_ROOT"; }

# Значение из /opt/svoi/.env. Читаем построчно, а не `source`: в .env есть
# инлайн-комментарии и значения с пробелами, которые shell разобрал бы не так
# (та же причина, по которой scripts/run_service.py использует python-dotenv).
app_env() {
  local key=$1 file
  file=$(env_file)
  [ -f "$file" ] || return 1
  sed -n "s/^[[:space:]]*${key}=//p" "$file" | head -1 \
    | sed 's/[[:space:]]#.*$//' | sed 's/[[:space:]]*$//' | tr -d '"'
}

# Пароль Postgres нужен контейнерам, но в deploy/svoi.env его нет — он в .env.
require_pg_password() {
  POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-$(app_env POSTGRES_PASSWORD || true)}
  [ -n "${POSTGRES_PASSWORD:-}" ] \
    || die "нет POSTGRES_PASSWORD: сначала deploy/server-deploy.sh env"
  export POSTGRES_PASSWORD
}
