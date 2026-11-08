#!/usr/bin/env bash
# Переключение поллера Telegram с локальной машины на сервер (docs/infrastructure/75 §9).
#
#   deploy/poller-switch.sh check     # готов ли сервер принять поллер (ничего не меняет)
#   deploy/poller-switch.sh switch    # остановить локальный → запустить серверный → проверить
#   deploy/poller-switch.sh back      # вернуть поллер на локальную машину (аварийный откат)
#
# Почему отдельный скрипт: один bot token — один потребитель. Пока живы оба, Telegram
# отдаёт апдейт случайному, второй получает 409 Conflict, и бот «мигает».
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_env

: "${SVOI_SERVER:?SVOI_SERVER не задан в $ENV_FILE}"
SVOI_SSH_USER=${SVOI_SSH_USER:-$SVOI_USER}
SVOI_SSH_PORT=${SVOI_SSH_PORT:-22}
REMOTE="$SVOI_SSH_USER@$SVOI_SERVER"
SSH=(ssh -p "$SVOI_SSH_PORT" -o BatchMode=yes -o ConnectTimeout=10)
# Ключ админа задаётся в deploy/svoi.env (SVOI_SSH_KEY). Пользователь svoi нужен только
# для чтения; системные службы включаются от root, поэтому SVOI_SSH_USER=root — норма.
if [ -n "${SVOI_SSH_KEY:-}" ]; then
  SSH+=(-i "${SVOI_SSH_KEY/#\~/$HOME}" -o IdentitiesOnly=yes)
fi
GATEWAY_PATTERN="services/tg-gateway/app/main.py"
# Для pgrep: без [s] шаблон совпадает с командной строкой самой проверки (ssh передаёт
# её целиком), и скрипт видит «уже запущенный поллер» там, где его нет.
GATEWAY_MATCH="[s]ervices/tg-gateway/app/main.py"
LOCAL_LABEL="gui/$(id -u)/com.svoi.gateway"
LOCAL_PLIST="$HOME/Library/LaunchAgents/com.svoi.gateway.plist"

remote() { "${SSH[@]}" "$REMOTE" "$@"; }

remote_sudo() {
  # ssh -t требует терминал и падает при запуске из скрипта/агента. Если вход уже
  # root (SVOI_SSH_USER=root), sudo не нужен вовсе; иначе — неинтерактивный sudo -n.
  if [ "$SVOI_SSH_USER" = "root" ]; then
    remote "$@"
  else
    remote "sudo -n $*"
  fi
}

# pgrep без совпадений возвращает 1, а с `set -o pipefail` это код всей функции:
# `left=$(local_gateways)` тогда валит скрипт по set -e. Отсюда `|| true`.
local_gateways() { { pgrep -f "$GATEWAY_MATCH" 2>/dev/null || true; } | wc -l | tr -d ' '; }
remote_gateways() { remote "pgrep -f '$GATEWAY_MATCH' | wc -l || true" 2>/dev/null | tr -d '[:space:]'; }

cmd_check() {
  log "сервер $REMOTE, домен $SVOI_DOMAIN"
  remote true || die "ssh не проходит: проверьте ключ и $REMOTE"
  info "ssh ok"

  if remote "curl -fsS http://127.0.0.1:$SVOI_API_PORT/health" | grep -q '"ok":true'; then
    info "tool-api на сервере отвечает"
  else
    die "tool-api на сервере не отвечает: сначала server-deploy.sh smoke"
  fi

  if remote "systemctl cat svoi-gateway.service" >/dev/null 2>&1; then
    info "юнит svoi-gateway установлен"
  else
    die "юнита svoi-gateway нет: сначала server-deploy.sh units"
  fi

  local remote_count
  remote_count=$(remote_gateways)
  [ "$remote_count" = "0" ] || die "на сервере уже $remote_count поллер(ов) — разберитесь до переключения"
  info "на сервере поллеров нет"

  info "локальных поллеров сейчас: $(local_gateways)"
  if curl -fsS "https://$SVOI_DOMAIN/api/health" | grep -q '"ok":true'; then
    info "публичный https://$SVOI_DOMAIN/api/health отвечает"
  else
    warn "публичный /api/health не отвечает — Mini App не откроется, но polling это не блокирует"
  fi
  info "готов к переключению: deploy/poller-switch.sh switch"
}

stop_local() {
  log "останавливаю локальный поллер"
  if launchctl print "$LOCAL_LABEL" >/dev/null 2>&1; then
    launchctl bootout "$LOCAL_LABEL" 2>/dev/null || true
    info "launchd-агент $LOCAL_LABEL выгружен"
  fi
  # Процесс мог быть поднят руками мимо launchd — замок его не снимет.
  if [ "$(local_gateways)" != "0" ]; then
    pkill -f "$GATEWAY_PATTERN" || true
    sleep 2
  fi
  local left
  left=$(local_gateways)
  [ "$left" = "0" ] || die "локальный поллер не остановился (процессов: $left)"
  info "локальных поллеров: 0"
}

start_local() {
  log "поднимаю локальный поллер"
  if [ -f "$LOCAL_PLIST" ]; then
    launchctl bootstrap "gui/$(id -u)" "$LOCAL_PLIST" 2>/dev/null || true
    launchctl kickstart -k "$LOCAL_LABEL" 2>/dev/null || true
  else
    warn "plist не найден: поднимите сервисы через scripts/start_services.py"
  fi
  sleep 3
  info "локальных поллеров: $(local_gateways)"
}

cmd_switch() {
  cmd_check
  stop_local

  log "запускаю поллер на сервере"
  remote_sudo "systemctl enable --now svoi-gateway.service"
  sleep 6

  local count
  count=$(remote_gateways)
  info "поллеров на сервере: ${count:-0}"

  local conflicts
  conflicts=$(remote "journalctl -u svoi-gateway -n 100 --no-pager | grep -ci conflict" \
              | tr -d '[:space:]' || true)
  [ "${conflicts:-0}" = "0" ] || { start_local; die "409 Conflict на сервере: вернул поллер локально"; }
  [ "${count:-0}" = "1" ] || { start_local; die "на сервере ${count:-0} поллер(ов): вернул локально"; }

  log "проверка после переключения"
  if remote "curl -fsS https://$SVOI_DOMAIN/api/health" | grep -q '"ok":true'; then
    info "публичный API ok"
  else
    warn "публичный API не отвечает — смотрите server-deploy.sh smoke"
  fi
  info "поллер на сервере. Напишите боту «кто я» и проверьте ответ вручную."
  info "локальные сервисы выключены: вернуть поллер — deploy/poller-switch.sh back"
}

cmd_back() {
  log "возвращаю поллер на локальную машину"
  remote_sudo "systemctl disable --now svoi-gateway.service" || warn "не удалось остановить серверный поллер"
  local count
  count=$(remote_gateways)
  [ "$count" = "0" ] || die "на сервере ещё $count поллер(ов) — сначала остановите их"
  start_local
  info "поллер снова локальный"
}

case "${1:-}" in
  check)  cmd_check ;;
  switch) cmd_switch ;;
  back)   cmd_back ;;
  *) sed -n '2,9p' "$0"; exit 2 ;;
esac
