#!/usr/bin/env bash
# Развёртывание SVOI на чистом сервере — один скрипт, все адреса в deploy/svoi.env.
# Инструкция целиком: docs/infrastructure/75-hosting-and-migration.md
#
#   sudo deploy/server-deploy.sh check          # только проверить настройки, ничего не меняет
#   sudo deploy/server-deploy.sh provision      # пакеты, пользователь, ufw, docker, caddy
#   sudo deploy/server-deploy.sh app            # код, venv, зависимости
#   sudo deploy/server-deploy.sh env            # .env из .env.example + случайные секреты
#   sudo deploy/server-deploy.sh db             # postgres, схема; --restore FILE — из дампа
#   sudo deploy/server-deploy.sh storage        # Garage: контейнер, ключи, бакеты, CORS
#   sudo deploy/server-deploy.sh proxy          # Caddyfile из шаблона, TLS
#   sudo deploy/server-deploy.sh units          # юниты systemd (api + таймеры; поллер — poller)
#   sudo deploy/server-deploy.sh poller start   # включить поллер (после остановки локального!)
#   sudo deploy/server-deploy.sh smoke          # проверки после выката
#   sudo deploy/server-deploy.sh rollback v0.3.0
#
# Скрипт идемпотентен: повторный запуск не ломает уже сделанное (кроме `env`: он
# перезаписывает .env и требует FORCE=1).
set -euo pipefail

# shellcheck source=deploy/lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_env

DOCKER_NETWORK=${DOCKER_NETWORK:-svoi}
PYTHON_BIN=${PYTHON_BIN:-python3}

as_user() {
  if [ "$(id -u)" = "0" ]; then
    runuser -u "$SVOI_USER" -- "$@"
  else
    "$@"
  fi
}

# git от имени рабочего пользователя и с явным ключом деплоя. Два подводных камня:
# runuser не видит /root/.ssh, а ключ, положенный в /root, для svoi просто не существует;
# и без IdentitiesOnly ssh может подставить другой ключ из агента.
git_as_user() {
  as_user env GIT_SSH_COMMAND="ssh -i $SVOI_GIT_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" \
    git "$@"
}

# --------------------------------------------------------------------- check
cmd_check() {
  log "настройки: $ENV_FILE"
  info "домен приложения: $SVOI_DOMAIN"
  info "корень:           $SVOI_ROOT (пользователь $SVOI_USER)"
  info "часовой пояс:     $SVOI_TZ"
  info "репозиторий:      $SVOI_REPO @ $SVOI_REF"

  local problems=0
  for file in requirements.txt db/init services/tool-api/app/main.py \
              services/tg-gateway/app/main.py deploy/templates/Caddyfile.template; do
    if [ -e "$REPO_DIR/$file" ]; then
      info "есть: $file"
    else
      warn "нет: $file"
      problems=$((problems + 1))
    fi
  done

  if [ -n "${SVOI_DUMP:-}" ]; then
    [ -s "$SVOI_DUMP" ] && info "дамп: $SVOI_DUMP" || { warn "дамп не найден: $SVOI_DUMP"; problems=$((problems + 1)); }
  fi

  if command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    info "интерпретатор: $("$PYTHON_BIN" -V 2>&1)"
  else
    warn "нет $PYTHON_BIN"
    problems=$((problems + 1))
  fi
  if [ "$problems" -gt 0 ]; then
    die "проверка не пройдена: проблем $problems"
  fi
  info "настройки в порядке"
}

# ----------------------------------------------------------------- provision
cmd_provision() {
  require_root provision
  log "пакеты и часовой пояс"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y python3-venv python3-pip git curl rsync ca-certificates gnupg ufw fail2ban
  timedatectl set-timezone "$SVOI_TZ"

  log "пользователь $SVOI_USER"
  if ! id -u "$SVOI_USER" >/dev/null 2>&1; then
    adduser --disabled-password --gecos "" "$SVOI_USER"
  fi
  usermod -aG sudo "$SVOI_USER"
  # без этого членства `journalctl -u svoi-gateway` под рабочим пользователем отвечает
  # «нет доступа», и проверка конфликтов поллера молча видит пустой журнал
  usermod -aG systemd-journal "$SVOI_USER"

  log "firewall: наружу только $SVOI_SSH_PORT, 80, 443"
  ufw allow "$SVOI_SSH_PORT"/tcp
  ufw allow 80,443/tcp
  ufw --force enable

  # Swap — страховка для сервера с 2 ГБ RAM: короткий пик (pip install, сборка зависимостей)
  # иначе упирается в OOM-killer. Размер — SVOI_SWAP_GB, 0 отключает (docs/75 §2.6).
  local swap_gb=${SVOI_SWAP_GB:-2}
  if [ "$swap_gb" != "0" ] && ! swapon --show | grep -q .; then
    log "swap: ${swap_gb}G"
    fallocate -l "${swap_gb}G" /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=$((swap_gb * 1024))
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    info "swap включён: $(swapon --show=size --noheadings | tr -d ' ')"
  fi

  log "docker"
  if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sh
  fi
  usermod -aG docker "$SVOI_USER"
  systemctl enable --now docker

  log "caddy (TLS выпускается сам, порты 80/443 уже открыты)"
  if ! command -v caddy >/dev/null 2>&1; then
    curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
      | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
      > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -y
    apt-get install -y caddy
  fi
  systemctl enable --now fail2ban

  log "готово"
  info "осталось: положить ssh-ключ — ssh-copy-id -p $SVOI_SSH_PORT $SVOI_USER@\$SERVER"
  info "дальше: sudo deploy/server-deploy.sh app"
}

# ----------------------------------------------------------------------- app
cmd_app() {
  require_root app
  log "код: $SVOI_REPO → $SVOI_ROOT @ $SVOI_REF"
  mkdir -p "$SVOI_ROOT"
  chown "$SVOI_USER:$SVOI_USER" "$SVOI_ROOT"

  # Клон мелкий (`--depth 1`): в истории репозитория есть архивы источников на
  # несколько гигабайт, серверу они не нужны — ему нужен только рабочий коммит.
  # Полный клон тянет всю историю и занимает диск впустую (docs/infrastructure/75 §5.1).
  if [ ! -f "$SVOI_GIT_KEY" ]; then
    die "нет ключа деплоя $SVOI_GIT_KEY — создайте его от пользователя $SVOI_USER
  (sudo -u $SVOI_USER ssh-keygen -t ed25519 -N '' -C svoi-prod-deploy -f $SVOI_GIT_KEY)
  и добавьте $SVOI_GIT_KEY.pub в GitHub → Settings → Deploy keys (read-only, docs/75 §6.3)"
  fi

  if [ ! -d "$SVOI_ROOT/.git" ]; then
    git_as_user clone --depth 1 --branch "$SVOI_REF" "$SVOI_REPO" "$SVOI_ROOT"
  else
    git_as_user -C "$SVOI_ROOT" fetch --depth 1 origin "$SVOI_REF"
    git_as_user -C "$SVOI_ROOT" checkout --force FETCH_HEAD
  fi

  log "окружение Python"
  if [ ! -x "$SVOI_ROOT/.venv/bin/python" ]; then
    as_user "$PYTHON_BIN" -m venv "$SVOI_ROOT/.venv"
  fi
  as_user "$SVOI_ROOT/.venv/bin/pip" install -U pip wheel
  as_user "$SVOI_ROOT/.venv/bin/pip" install -r "$SVOI_ROOT/requirements.txt"

  log "каталоги"
  install -d -o "$SVOI_USER" -g "$SVOI_USER" -m 700 "$SVOI_ROOT/secrets"
  install -d -o "$SVOI_USER" -g "$SVOI_USER" -m 750 "$SVOI_BACKUP_DIR"
  chmod +x "$SVOI_ROOT"/scripts/*.sh 2>/dev/null || true

  as_user env -u PYTHONPATH "$SVOI_ROOT/.venv/bin/python" -m compileall -q \
    "$SVOI_ROOT/services" "$SVOI_ROOT/scripts"
  info "код и окружение готовы"
}

# ----------------------------------------------------------------------- env
cmd_env() {
  require_root env
  local target
  target=$(env_file)
  if [ -f "$target" ] && [ "${FORCE:-0}" != "1" ]; then
    die "$target уже есть. Перезаписать: FORCE=1 sudo deploy/server-deploy.sh env"
  fi
  [ -f "$REPO_DIR/.env.example" ] || die "нет .env.example"

  log "собираю $target из .env.example + случайные секреты"
  local tool_token webapp_secret pg_password
  tool_token=$(openssl rand -hex 24)
  webapp_secret=$(openssl rand -hex 24)
  pg_password=$(openssl rand -hex 18)

  install -m 600 -o "$SVOI_USER" -g "$SVOI_USER" /dev/null "$target"
  sed -e "s|^ENV=.*|ENV=prod|" \
      -e "s|^TZ_OFFSET_HOURS=.*|TZ_OFFSET_HOURS=4|" \
      -e "s|^TOOL_API_TOKEN=.*|TOOL_API_TOKEN=$tool_token|" \
      -e "s|^WEBAPP_SECRET=.*|WEBAPP_SECRET=$webapp_secret|" \
      -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$pg_password|" \
      -e "s|^DATABASE_URL=.*|DATABASE_URL=postgresql://$SVOI_DB_USER:$pg_password@127.0.0.1:$SVOI_PG_PORT/$SVOI_DB_NAME|" \
      -e "s|^TOOL_API_URL=.*|TOOL_API_URL=http://127.0.0.1:$SVOI_API_PORT|" \
      -e "s|^WEBAPP_BASE_URL=.*|WEBAPP_BASE_URL=https://$SVOI_DOMAIN|" \
      -e "s|^STORAGE_PUBLIC_ENDPOINT=.*|STORAGE_PUBLIC_ENDPOINT=https://$SVOI_STORAGE_DOMAIN|" \
      -e "s|^STORAGE_CORS_ORIGINS=.*|STORAGE_CORS_ORIGINS=https://$SVOI_DOMAIN|" \
      -e "s|^GARAGE_CONTAINER=.*|GARAGE_CONTAINER=$SVOI_GARAGE_CONTAINER|" \
      "$REPO_DIR/.env.example" > "$target"
  chmod 600 "$target"
  chown "$SVOI_USER:$SVOI_USER" "$target"

  # systemd EnvironmentFile читает строку буквально: инлайн-комментарии из образца
  # попадают в значение и ломают int()/float() при старте службы.
  "$PYTHON_BIN" "$REPO_DIR/deploy/normalize_env.py" "$target" >/dev/null

  info "секреты сгенерированы (в вывод не печатаются)"
  info "WEBAPP_BASE_URL=https://$SVOI_DOMAIN проставлен автоматически"
  info "заполните вручную: TELEGRAM_TOKEN, ADMIN_CHAT_ID, ID таблиц и календарей,"
  info "                   LLM_API_KEY (ключ провайдера)"
  info "команда: sudo -u $SVOI_USER nano $target"
}

# ------------------------------------------------------------------------ db
cmd_db() {
  require_root db
  require_docker
  local restore=""
  local migrate_only=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --restore) restore="${2:?--restore требует путь к дампу}"; shift 2 ;;
      --migrate) migrate_only=1; shift ;;
      *) die "неизвестный аргумент db: $1" ;;
    esac
  done

  docker network inspect "$DOCKER_NETWORK" >/dev/null 2>&1 \
    || docker network create "$DOCKER_NETWORK"

  require_pg_password
  if ! docker ps -a --format '{{.Names}}' | grep -qx "$SVOI_PG_CONTAINER"; then
    log "контейнер $SVOI_PG_CONTAINER"
    docker run -d --name "$SVOI_PG_CONTAINER" --network "$DOCKER_NETWORK" \
      -e "POSTGRES_DB=$SVOI_DB_NAME" -e "POSTGRES_USER=$SVOI_DB_USER" \
      -e "POSTGRES_PASSWORD=$POSTGRES_PASSWORD" \
      -e "TZ=$SVOI_TZ" \
      -p "127.0.0.1:$SVOI_PG_PORT:5432" \
      -v svoi_pgdata:/var/lib/postgresql/data \
      --restart unless-stopped postgres:16
  else
    log "контейнер $SVOI_PG_CONTAINER уже есть"
    docker start "$SVOI_PG_CONTAINER" >/dev/null 2>&1 || true
  fi
  wait_for_pg

  local url
  url=$(app_env DATABASE_URL)

  if [ -n "$restore" ]; then
    [ -s "$restore" ] || die "дампа нет: $restore"
    log "восстановление из $restore"
    docker cp "$restore" "$SVOI_PG_CONTAINER:/tmp/svoi.dump"
    docker exec -i "$SVOI_PG_CONTAINER" pg_restore -U "$SVOI_DB_USER" -d "$SVOI_DB_NAME" \
      --clean --if-exists --no-owner /tmp/svoi.dump || warn "pg_restore вернул предупреждения"

    # Дамп мог быть снят до перехода на Alembic: alembic_version в нём нет.
    # Baseline ставится ТОЛЬКО когда структура уже равна цепочке (read-only сверка),
    # иначе `upgrade head` накатил бы поверх чужой схемы. Расхождение — стоп.
    log "сверка схемы дампа с цепочкой ревизий"
    if DATABASE_URL="$url" "$REPO_DIR/.venv/bin/python" \
         "$REPO_DIR/deploy/check_schema.py" --expect-head; then
      ( cd "$REPO_DIR" && DATABASE_URL="$url" as_user env DATABASE_URL="$url" \
          "$REPO_DIR/.venv/bin/alembic" -c "$REPO_DIR/alembic.ini" stamp head ) \
        || die "не удалось поставить baseline (alembic stamp head)"
      info "baseline поставлен: alembic_version = head"
    else
      die "схема дампа не равна цепочке: разберите расхождения (check_schema.py --expect-head), затем alembic stamp <ревизия> и upgrade head — точечный psql оставьте аварийным путём"
    fi
  fi

  # Миграции: на чистой базе или по явному --migrate. После restore цепочка уже
  # помечена baseline-ом — повторный upgrade ничего не применит (no-op).
  if [ "$migrate_only" = "1" ] || [ -z "$restore" ]; then
    log "схема через Alembic: upgrade head (файлы db/init, учёт в alembic_version)"
    ( cd "$REPO_DIR" && DATABASE_URL="$url" as_user env DATABASE_URL="$url" \
        "$REPO_DIR/.venv/bin/alembic" -c "$REPO_DIR/alembic.ini" upgrade head ) \
      || die "миграции упали: смотрите вывод alembic выше"
  fi

  log "что в базе"
  docker exec -i "$SVOI_PG_CONTAINER" psql -U "$SVOI_DB_USER" -d "$SVOI_DB_NAME" -c \
    "select (select count(*) from budget_tx) as операций,
            (select count(*) from availability_hour) as часов,
            (select count(*) from participant where status <> 'archived') as людей;"
}

# ------------------------------------------------------------------ storage
# Хранилище файлов (Garage) — часть контура, а не «потом»: без него `presign`
# отвечает 503, и Mini App не грузит файлы (Д-03, 09.11.2026; docs/development/86 §9 п.5).
# Шаг идемпотентен: повторный запуск не трогает существующие ключи и объекты.
cmd_storage() {
  require_root storage
  require_docker
  local python="$SVOI_ROOT/.venv/bin/python"
  [ -x "$python" ] || die "нет venv: сначала deploy/server-deploy.sh app"

  # Ключи, бакеты и CORS настраивает scripts/storage_up.py. Отсюда приходят только
  # адреса: они в deploy/svoi.env, а не в .env, и скрипт сам дописывает их в .env.
  log "хранилище: контейнер $SVOI_GARAGE_CONTAINER, публичный адрес https://$SVOI_STORAGE_DOMAIN"
  ( cd "$SVOI_ROOT" && env -u PYTHONPATH \
      GARAGE_CONTAINER="$SVOI_GARAGE_CONTAINER" \
      STORAGE_PUBLIC_ENDPOINT="https://$SVOI_STORAGE_DOMAIN" \
      STORAGE_CORS_ORIGINS="https://$SVOI_DOMAIN" \
      "$python" scripts/storage_up.py ) || die "хранилище не настроилось: смотрите вывод выше"

  ( cd "$SVOI_ROOT" && env -u PYTHONPATH "$python" scripts/storage_smoke.py ) \
    || die "хранилище не отвечает: put/get и presigned-ссылки — см. вывод выше"

  # .env дополнился ключами и адресами; работающий процесс их ещё не видит.
  systemctl restart svoi-tool-api
  info "хранилище готово, tool-api перезапущен с новыми настройками"
}

# --------------------------------------------------------------------- proxy
cmd_proxy() {
  require_root proxy
  log "Caddyfile: $SVOI_DOMAIN"
  local template="$REPO_DIR/deploy/templates/Caddyfile.template"
  [ -f "$template" ] || die "нет шаблона $template"

  sed -e "s|@DOMAIN@|$SVOI_DOMAIN|g" \
      -e "s|@API_PORT@|$SVOI_API_PORT|g" \
      -e "s|@STORAGE_DOMAIN@|$SVOI_STORAGE_DOMAIN|g" \
      -e "s|@STORAGE_PORT@|$SVOI_STORAGE_PORT|g" \
      -e "s|@ACME_EMAIL@|$SVOI_ACME_EMAIL|g" \
      "$template" > /etc/caddy/Caddyfile
  install -d -m 755 /var/log/caddy
  # caddy validate выполняется от root и создаёт файлы логов root:root 600 —
  # служба под пользователем caddy их потом не открывает. Рекурсивный chown обязателен.
  chown -R caddy:caddy /var/log/caddy 2>/dev/null || true
  chmod 640 /var/log/caddy/*.log 2>/dev/null || true

  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
  chown -R caddy:caddy /var/log/caddy 2>/dev/null || true
  chmod 640 /var/log/caddy/*.log 2>/dev/null || true
  systemctl enable caddy
  systemctl reload caddy 2>/dev/null || systemctl restart caddy

  log "жду сертификаты: $SVOI_DOMAIN, $SVOI_STORAGE_DOMAIN"
  local host i problems=0
  for host in "$SVOI_DOMAIN" "$SVOI_STORAGE_DOMAIN"; do
    for i in $(seq 1 30); do
      if curl -fsSI "https://$host" >/dev/null 2>&1; then
        info "TLS $host в порядке"
        break
      fi
      if [ "$i" = "30" ]; then
        warn "сертификат $host не выпустился: journalctl -u caddy -n 50; проверьте DNS A-запись и порт 80"
        problems=$((problems + 1))
      fi
      sleep 2
    done
  done
  [ "$problems" -eq 0 ]
}

# -------------------------------------------------------------------- schema
# Восстановленная из дампа база бывает позади кода: миграции в репозитории есть,
# к базе не применялись. Полный `db --migrate` на такой базе не годится — часть
# миграций рассчитана на порядок «от чистой схемы».
cmd_verify() {
  require_root verify
  require_docker
  require_pg_password
  # Схема против кода — ДО перезапуска служб. 01.11.2026 деплой v0.4.0 прошёл
  # «успешно», а в базе не было intent_log.steps и agent_proposal: сервис поднялся
  # на старой схеме, журнал агента падал, ответы уходили в деградацию, и заметили
  # это по жалобе владельца. Теперь расхождение останавливает выкат.
  log "сверка схемы с db/init (до перезапуска служб)"
  local url venv_python="$REPO_DIR/.venv/bin/python"
  [ -x "$venv_python" ] || die "нет venv: сначала server-deploy.sh app"
  url=$(app_env DATABASE_URL)
  DATABASE_URL="$url" "$venv_python" "$REPO_DIR/deploy/check_schema.py" --files \
    || die "схема отстала от кода: накатите миграции (server-deploy.sh db --migrate) или нужную вручную"
  # Версия в alembic_version — второй, обязательный признак: структура может быть
  # на месте, а учёт отстать, и следующий `db --migrate` накатил бы ревизии повторно.
  DATABASE_URL="$url" "$venv_python" "$REPO_DIR/deploy/check_schema.py" --expect-head \
    || die "версия схемы не равна head: server-deploy.sh db --migrate"
  info "схема совпадает"
}

cmd_schema() {
  require_root schema
  require_docker
  require_pg_password
  log "сверка схемы с db/init"
  local url venv_python="$REPO_DIR/.venv/bin/python"
  [ -x "$venv_python" ] || die "нет venv: сначала server-deploy.sh app"
  url=$(app_env DATABASE_URL)
  # psycopg есть только в venv приложения, системный python его не знает
  DATABASE_URL="$url" "$venv_python" "$REPO_DIR/deploy/check_schema.py" --files
  DATABASE_URL="$url" "$venv_python" "$REPO_DIR/deploy/check_schema.py" --expect-head
}

# --------------------------------------------------------------------- units
cmd_units() {
  require_root units
  local with_timers="0"
  if [ "${1:-}" = "--with-timers" ]; then with_timers="1"; fi
  log "рендер юнитов: $REPO_DIR/deploy/systemd → /etc/systemd/system"
  "$PYTHON_BIN" "$REPO_DIR/deploy/render_units.py" \
    --root "$SVOI_ROOT" --user "$SVOI_USER" --target /etc/systemd/system

  systemctl daemon-reload
  # Поллер намеренно не включаем: до переключения (poller start) работает локальный,
  # второй экземпляр получит 409 Conflict и потеряет сообщения.
  systemctl enable svoi-tool-api.service
  if [ "$with_timers" = "1" ]; then
    # Список обязан покрывать все *.timer из deploy/systemd: юнит, который
    # отрендерили, но не включили, ставится и молчит (так чуть не уехал
    # сторож 409, карта t_4aa28fe9). Проверка — tests/unit/test_deploy_scripts.py.
    systemctl enable svoi-notify-plan.timer svoi-notify-send.timer \
                     svoi-feedback-digest.timer \
                     svoi-invariants.timer svoi-watchdog.timer svoi-health.timer \
                     svoi-geo-check.timer \
                     svoi-feedback-triage.timer \
                     svoi-backup.timer \
                     svoi-conflicts.timer svoi-cold-rotate.timer
    info "таймеры включены"
  else
    # Пока живёт локальный контур (launchd), включённые таймеры дали бы двойные
    # уведомления и двойные проверки. Включаются на переключении.
    info "таймеры установлены, но не включены — включатся на переключении: units --with-timers"
  fi
  info "юниты установлены; поллер включим отдельно: deploy/server-deploy.sh poller start"
}

# -------------------------------------------------------------------- poller
cmd_poller() {
  require_root poller
  local action="${1:-status}"
  case "$action" in
    start)
      systemctl enable --now svoi-gateway.service
      sleep 5
      local count
      count=$(pgrep -fc "services/tg-gateway/app/main.py" || true)
      info "процессов tg-gateway: ${count:-0}"
      if [ "${count:-0}" != "1" ]; then
        die "поллер не поднялся или поднялся не один раз (${count:-0})"
      fi
      if journalctl -u svoi-gateway -n 100 --no-pager 2>/dev/null | grep -qi conflict; then
        die "в логе 409 Conflict: где-то работает второй поллер — остановите его"
      fi
      info "поллер работает, конфликтов в логе нет"
      ;;
    stop)
      systemctl disable --now svoi-gateway.service
      info "поллер остановлен"
      ;;
    status)
      systemctl status svoi-gateway.service --no-pager || true
      pgrep -af "services/tg-gateway/app/main.py" || info "процессов нет"
      ;;
    *) die "poller start|stop|status" ;;
  esac
}

# --------------------------------------------------------------------- smoke
cmd_smoke() {
  log "проверка контура"
  local problems=0
  local api="http://127.0.0.1:$SVOI_API_PORT"

  # Исходящая доступность внешних API с этого сервера. Именно эта проверка ловит
  # главный риск размещения: из дата-центров РФ ТСПУ дропает api.telegram.org
  # (docs/infrastructure/75 §1.1), и бот молча перестаёт получать апдейты.
  check_reach() {
    local name=$1 url=$2 code
    code=$(curl -s -o /dev/null -m 8 -w '%{http_code}' "$url" 2>/dev/null || true)
    if [ -z "$code" ] || [ "$code" = "000" ]; then
      warn "$name недоступен с этого сервера"
      return 1
    fi
    info "$name: HTTP $code"
    return 0
  }

  local llm_url
  llm_url=$(app_env LLM_BASE_URL || true)
  check_reach "api.telegram.org" "https://api.telegram.org/" || problems=$((problems + 1))
  check_reach "api.vk.com" "https://api.vk.com/method/utils.getServerTime" \
    || problems=$((problems + 1))
  check_reach "LLM (${llm_url:-https://openrouter.ai/api/v1/models})" \
    "${llm_url:-https://openrouter.ai/api/v1/models}" || problems=$((problems + 1))

  if curl -fsS "$api/health" | grep -q '"ok":true'; then
    info "/health ok"
  else
    warn "/health не отвечает или ok != true"
    problems=$((problems + 1))
  fi

  if curl -fsS "https://$SVOI_DOMAIN/api/health" | grep -q '"ok":true'; then
    info "https://$SVOI_DOMAIN/api/health ok"
  else
    warn "публичный /api/health не отвечает"
    problems=$((problems + 1))
  fi

  if curl -fsS -o /dev/null -w '%{http_code}' "https://$SVOI_DOMAIN/webapp" | grep -q 200; then
    info "страница Mini App отдаётся"
  else
    warn "страница Mini App не отдаётся"
    problems=$((problems + 1))
  fi

  if command -v docker >/dev/null 2>&1 \
      && docker exec "$SVOI_PG_CONTAINER" pg_isready -U "$SVOI_DB_USER" \
         -d "$SVOI_DB_NAME" >/dev/null 2>&1; then
    info "Postgres отвечает"
  else
    warn "Postgres не отвечает"
    problems=$((problems + 1))
  fi

  if [ -x "$SVOI_ROOT/.venv/bin/python" ]; then
    # Хранилище — часть контура: пустой STORAGE_ENDPOINT не должен выглядеть нормой,
    # иначе выкат «зелёный», а Mini App отвечает 503 на presign (Д-03, 09.11.2026).
    if (cd "$SVOI_ROOT" && env -u PYTHONPATH .venv/bin/python scripts/storage_smoke.py \
          >/dev/null 2>&1); then
      info "хранилище отвечает (put/get, presigned)"
    else
      warn "хранилище не отвечает: deploy/server-deploy.sh storage"
      problems=$((problems + 1))
    fi
    # DATABASE_URL берём из .env: у скриптов по умолчанию порт локального контура (5433),
    # на сервере база на 5432 — без этого инварианты молча проверяли бы не ту базу.
    local db_url tz_offset
    db_url=$(app_env DATABASE_URL || true)
    tz_offset=$(app_env TZ_OFFSET_HOURS || true)
    if (cd "$SVOI_ROOT" && env -u PYTHONPATH \
        DATABASE_URL="${db_url:-$DATABASE_URL}" TZ_OFFSET_HOURS="${tz_offset:-4}" \
        .venv/bin/python scripts/check_invariants.py >/dev/null 2>&1); then
      info "инварианты пройдены"
    else
      warn "инварианты не пройдены: scripts/check_invariants.py"
      problems=$((problems + 1))
    fi
  fi

  [ "$problems" -eq 0 ] || die "проблем: $problems"
  info "контур в порядке"
}

# ------------------------------------------------------------------ rollback
cmd_rollback() {
  require_root rollback
  local ref="${1:?укажите тег или коммит: rollback v0.2.0}"
  log "откат на $ref"
  # Клон мелкий: тег или коммит нужно дотянуть отдельно, иначе checkout не найдёт его.
  git_as_user -C "$SVOI_ROOT" fetch --depth 1 origin "$ref"
  git_as_user -C "$SVOI_ROOT" checkout --force FETCH_HEAD
  as_user "$SVOI_ROOT/.venv/bin/pip" install -q -r "$SVOI_ROOT/requirements.txt"
  systemctl restart svoi-tool-api
  sleep 3
  curl -fsS "http://127.0.0.1:$SVOI_API_PORT/health" | grep -q '"ok":true' \
    || die "после отката /health не отвечает"
  info "откат выполнен, /health ok. База не откатывается: миграции добавляют, не удаляют."
}

usage() { sed -n '2,18p' "$0"; }

case "${1:-}" in
  check)      shift; cmd_check "$@" ;;
  provision)  shift; cmd_provision "$@" ;;
  app)        shift; cmd_app "$@" ;;
  env)        shift; cmd_env "$@" ;;
  db)         shift; cmd_db "$@" ;;
  storage)    shift; cmd_storage "$@" ;;
  proxy)      shift; cmd_proxy "$@" ;;
  units)      shift; cmd_units "$@" ;;
  schema)     shift; cmd_schema ;;
  poller)     shift; cmd_poller "$@" ;;
  smoke)      shift; cmd_smoke "$@" ;;
  verify)     shift; cmd_verify "$@" ;;
  rollback)   shift; cmd_rollback "$@" ;;
  -h|--help|"") usage ;;
  *) usage; exit 2 ;;
esac
