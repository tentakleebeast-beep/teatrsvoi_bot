#!/usr/bin/env bash
# Проверка выката после обновления версии (R-5, docs/infrastructure/35 §10a).
#
#   bash scripts/deploy-check.sh
#   bash scripts/deploy-check.sh --url http://127.0.0.1:8000 --root /opt/svoi
#
# Отвечает на вопрос «та ли сборка работает и живы ли оба сервиса», а не только
# «порт отвечает». Код выхода 1 при любой проблеме — годится для ручного выката.
set -uo pipefail

URL="${TOOL_API_URL:-http://127.0.0.1:8000}"
ROOT="${SVOI_ROOT:-/opt/svoi}"

while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL="${2:?--url требует значение}"; shift 2 ;;
    --root) ROOT="${2:?--root требует значение}"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "неизвестный аргумент: $1" >&2; exit 2 ;;
  esac
done

PYTHON="$ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="python3"
problems=0
ok() { echo "  ok: $1"; }
bad() { echo "  ПРОБЛЕМА: $1"; problems=$((problems + 1)); }

echo "проверка выката: $ROOT, $URL"

# 1. /health отвечает, ok=true, живая сборка совпадает с HEAD.
if health=$("$PYTHON" "$ROOT/scripts/health_agent.py" --url "$URL" --root "$ROOT" 2>&1); then
  ok "/health, git_sha = HEAD"
  echo "$health" | sed 's/^/    /'
else
  bad "/health или версия сборки"
  echo "$health" | sed 's/^/    /'
fi

# 2. Оба сервиса живы (tool-api проверен выше, gateway — по процессу).
if pgrep -f "services/tg-gateway/app/main.py" > /dev/null; then
  ok "tg-gateway запущен"
else
  bad "процесс tg-gateway не найден (systemctl status svoi-gateway)"
fi

# 3. Юниты планировщика в порядке (статическая проверка файлов репозитория).
if units=$("$PYTHON" "$ROOT/scripts/check_services.py" 2>&1); then
  ok "юниты systemd"
else
  bad "юниты systemd"
  echo "$units" | sed 's/^/    /'
fi

if [ "$problems" -eq 0 ]; then
  echo "выкат в порядке"
  exit 0
fi
echo "проблем: $problems"
exit 1
