#!/usr/bin/env bash
# Выпуск версии и выкат на прод — одной командой, но в два шага.
#
#   deploy/release.sh v0.4.0            # prepare: гейты, CHANGELOG, тег. Прод не трогает.
#   deploy/release.sh v0.4.0 --apply    # только по команде владельца: push тега, workflow, сверка
#   deploy/release.sh --status          # что сейчас в проде (dry_run=true, прод не меняется)
#
# Правило проекта (docs/development/26 §6): прод обновляется только по команде владельца.
# Подготовка версии безопасна и локальна; всё, что видит прод, живёт за флагом --apply.
#
# Скрипт не читает .env, секреты и токен бота. GitHub Actions берёт DEPLOY_SSH_KEY из
# своих secrets, а здесь печатается только тег, sha и состав релиза.
set -euo pipefail

WORKFLOW="deploy.yml"
# Прод-workflow живут в публичном репозитории (docs/development/26 §10).
# Переопределяется окружением, если репозиторий переедет.
WORKFLOW_REPO="${WORKFLOW_REPO:-tentakleebeast-beep/teatrsvoi_bot}"
export GH_REPO="$WORKFLOW_REPO"
REPORT="docs/reports/78-release-state.md"
CHANGELOG="CHANGELOG.md"
# Факт после выката берётся из процесса, а не из лога run: лог говорит, что workflow
# дошёл до конца, /api/health — что в памяти именно этот коммит.
PROD_HEALTH_URL="${PROD_HEALTH_URL:-https://svoi-theater-samara.online/api/health}"

# GitHub регистрирует новый run не мгновенно, и `gh run list --limit 1` в эти секунды
# отдаёт предыдущий, уже успешный прогон — так v0.13.2 и v0.13.4 «подтвердились» без
# выката (docs/reports/78). Поэтому ждём run со своим headSha, созданный не раньше запуска.
RUN_POLL_ATTEMPTS="${RELEASE_RUN_POLL_ATTEMPTS:-60}"
RUN_POLL_SECONDS="${RELEASE_RUN_POLL_SECONDS:-5}"

TAG=""
MODE="prepare"
SKIP_TESTS=0

usage() {
    cat <<'TXT'
deploy/release.sh — выпуск версии театра-студии «СВОИ»

Использование:
  deploy/release.sh <версия>              prepare: проверки, CHANGELOG, аннотированный тег.
                                          Ничего не публикует: ни push, ни workflow.
  deploy/release.sh <версия> --apply      выкат на прод. Только по команде владельца:
                                          push тега, gh workflow run deploy.yml,
                                          ожидание run, запись состояния в отчёт 78.
  deploy/release.sh --status              что в проде: dry_run=true того же workflow,
                                          печатает версию и юниты из лога.
  deploy/release.sh --help                эта справка.

Версия: X.Y.Z или vX.Y.Z. Тег ставится только на main, при чистом дереве
и совпадении с origin/main, если тег уже существует — ошибка (переставлять нельзя).

Флаги:
  --skip-tests    не прогонять гейты (ruff, test_audit, pytest) — только для черновой
                  проверки механики; печатает предупреждение.
TXT
}

say()  { printf '%s\n' "$*"; }
warn() { printf 'ВНИМАНИЕ: %s\n' "$*" >&2; }
die()  { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }

root() {
    git rev-parse --show-toplevel 2>/dev/null \
        || die "не git-репозиторий: запускать из клона проекта"
}

python_bin() {
    local top
    top="$(root)"
    if [ -x "$top/.venv/bin/python" ]; then
        printf '%s' "$top/.venv/bin/python"
    else
        printf '%s' "python3"
    fi
}

find_run_id() {
    local sha since attempt id
    sha="$1"
    since="$2"
    for ((attempt = 1; attempt <= RUN_POLL_ATTEMPTS; attempt++)); do
        if [ "$attempt" -gt 1 ]; then
            sleep "$RUN_POLL_SECONDS"
        fi
        id="$(gh run list --workflow="$WORKFLOW" --limit 50 \
            --json databaseId,headSha,createdAt \
            --jq "[.[] | select(.headSha == \"$sha\" and .createdAt >= \"$since\")] | .[0].databaseId // empty" \
            2>/dev/null || true)"
        if [ -n "$id" ]; then
            printf '%s' "$id"
            return 0
        fi
    done
    return 1
}

health_sha() {
    local body sha
    body="$(curl -fsS --max-time 25 "$PROD_HEALTH_URL" 2>/dev/null || true)"
    [ -n "$body" ] || return 1
    printf '%s' "$body" | grep -q '"ok":true' || return 1
    sha="$(printf '%s' "$body" \
        | sed -nE 's/.*"git_sha"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p')"
    [ -n "$sha" ] || return 1
    printf '%s' "$sha"
}

normalize_version() {
    local value="$1"
    case "$value" in
        v*) TAG="$value" ;;
        *)  TAG="v$value" ;;
    esac
    [[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] \
        || die "версия должна быть X.Y.Z или vX.Y.Z, получено: $value"
}

require_release_state() {
    local top branch
    top="$(root)"
    cd "$top"

    branch="$(git branch --show-current)"
    [ "$branch" = "main" ] || die "релиз ставится только на ветке main, сейчас: ${branch:-detached}"

    [ -z "$(git status --porcelain)" ] \
        || die "дерево грязное: сначала закоммитьте или уберите изменения (git status --porcelain)"

    if git remote get-url origin >/dev/null 2>&1; then
        git fetch --tags origin >/dev/null 2>&1 || warn "git fetch --tags origin не прошёл — сверяю с тем, что есть"
        if git rev-parse -q --verify refs/remotes/origin/main >/dev/null; then
            [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] \
                || die "HEAD не совпадает с origin/main: сначала git pull --ff-only"
        fi
    else
        warn "remote origin не настроен — сверка с origin/main пропущена (локальный прогон)"
    fi

    if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
        die "тег $TAG уже существует: переставлять существующий тег запрещено (docs/development/26 §6)"
    fi
}

run_gates() {
    local top py
    top="$(root)"
    cd "$top"
    py="$(python_bin)"

    if [ "$SKIP_TESTS" = "1" ]; then
        warn "--skip-tests: гейты (ruff, test_audit, pytest) не прогонялись — версия не проверена"
        return 0
    fi

    say "Гейты: ruff, test_audit --strict, pytest"
    if [ -x "$top/.venv/bin/ruff" ]; then
        "$top/.venv/bin/ruff" check . || die "ruff check . не прошёл"
    elif command -v ruff >/dev/null 2>&1; then
        ruff check . || die "ruff check . не прошёл"
    else
        die "ruff не найден: нужен .venv/bin/ruff (requirements-dev.txt)"
    fi
    "$py" scripts/test_audit.py --strict || die "scripts/test_audit.py --strict не прошёл"
    "$py" -m pytest -q || die "pytest не прошёл"
}

changelog_section() {
    local top version
    top="$(root)"
    version="${TAG#v}"
    [ -f "$top/$CHANGELOG" ] || die "нет $CHANGELOG: раздел версии обязателен"
    awk -v tag="$TAG" -v ver="$version" '
        $0 ~ "^##[[:space:]]+" tag "([[:space:]]|$)" { inside = 1; next }
        $0 ~ "^##[[:space:]]+" ver "([[:space:]]|$)" { inside = 1; next }
        inside && /^##[[:space:]]/ { exit }
        inside { print }
    ' "$top/$CHANGELOG"
}

prepare() {
    local top sha prev commits subject body message
    top="$(root)"
    cd "$top"

    require_release_state
    run_gates

    body="$(changelog_section)"
    [ -n "$body" ] || die "нет раздела CHANGELOG для $TAG"

    sha="$(git rev-parse HEAD)"
    prev="$(git describe --tags --abbrev=0 HEAD^ 2>/dev/null || true)"
    if [ -n "$prev" ]; then
        commits="$(git log --oneline "$prev..HEAD")"
    else
        prev="(предыдущего тега нет)"
        commits="$(git log --oneline HEAD)"
    fi

    subject="Релиз $TAG"
    message="$subject"$'\n\n'"$(printf '%s\n' "$body" | sed '/^[[:space:]]*$/d')"

    git tag -a "$TAG" -m "$message"

    say ""
    say "План релиза $TAG (prepare)"
    say "  тег:               $TAG"
    say "  sha:               $sha"
    say "  от тега:           $prev"
    if [ -n "$commits" ]; then
        say "  коммиты:"
        printf '%s\n' "$commits" | sed 's/^/    /'
    else
        say "  коммиты:           нет (тег на текущем HEAD)"
    fi
    say ""
    say "--apply сделает (только по команде владельца):"
    say "  1. git push origin $TAG"
    say "  2. gh workflow run $WORKFLOW -f tag=$TAG -f dry_run=false"
    say "  3. дождётся run и запишет состояние в $REPORT"
    say ""
    say "Тег создан локально. Прод не тронут: ни push, ни workflow не выполнялись."
}

apply() {
    local top sha prev tag_sha run_id message rc units since health
    top="$(root)"
    cd "$top"

    sha="$(git rev-parse HEAD)"
    prev="$(git describe --tags --abbrev=0 "$TAG^" 2>/dev/null || true)"
    git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || die "нет локального тега $TAG: сначала prepare"
    tag_sha="$(git rev-parse "$TAG^{}")"
    [ "$tag_sha" = "$sha" ] || warn "тег $TAG указывает не на HEAD — выкатывается именно тег"

    command -v gh >/dev/null 2>&1 || die "gh не найден: выкат идёт через GitHub Actions"

    say "Выкат $TAG на прод по команде владельца"
    say "  1/3 git push origin $TAG"
    git push origin "$TAG" || die "не удалось запушить тег $TAG"

    say "  2/3 gh workflow run $WORKFLOW -f tag=$TAG -f dry_run=false"
    # Метка времени до запуска: run, созданный раньше неё, — не наш (Д-5).
    since="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    gh workflow run "$WORKFLOW" -f "tag=$TAG" -f "dry_run=false" \
        || die "не удалось запустить workflow $WORKFLOW"

    say "  3/3 ожидание run для коммита $sha, созданного не раньше $since"
    if ! run_id="$(find_run_id "$sha" "$since")"; then
        die "run для коммита $sha после $since не появился в gh run list за $RUN_POLL_ATTEMPTS проверок: выкат $TAG не подтверждён, в $REPORT ничего не записано"
    fi

    say "  3/3 ожидание run $run_id"
    rc=0
    gh run watch "$run_id" --exit-status || rc=$?

    if [ "$rc" -ne 0 ]; then
        message="Выкат $TAG на прод не прошёл (run $run_id, код $rc). Проверяю откат в логе $WORKFLOW; если откат не виден — выкатываю предыдущий тег ${prev:-нет}."
        "$(python_bin)" scripts/notify_admin.py "$message" \
            || warn "сообщение админу не отправлено (scripts/notify_admin.py)"

        units="$(gh run view "$run_id" --log 2>/dev/null \
            | grep -iE "откат|rollback" || true)"
        if [ -z "$units" ]; then
            if [ -n "$prev" ]; then
                warn "откат в логе run не виден — выкатываю предыдущий тег $prev"
                gh workflow run "$WORKFLOW" -f "tag=$prev" -f "dry_run=false" \
                    || warn "откат на $prev не запустился — нужен ручной разбор"
            else
                warn "предыдущего тега нет — откат вручную"
            fi
        else
            say "в логе виден откат:"
            printf '%s\n' "$units" | sed 's/^/  /'
        fi
        warn "выкат $TAG не прошёл: run $run_id, код $rc — в $REPORT ничего не записано"
        exit 1
    fi

    health="$(health_sha || true)"
    [ -n "$health" ] \
        || die "прод не подтвердил себя: $PROD_HEALTH_URL не вернул ok:true с git_sha (run $run_id успешен) — в $REPORT ничего не записано"
    case "$tag_sha" in
        "$health"*) ;;
        *) die "прод работает на $health, тег $TAG указывает на $tag_sha — состояние в $REPORT не записано" ;;
    esac

    say "Run $run_id успешен: /api/health подтвердил $TAG (git_sha $health)"
    record_state "$TAG" "$tag_sha" "$health" "$run_id"
}

record_state() {
    local tag sha health run_id date block
    tag="$1"
    sha="$2"
    health="$3"
    run_id="$4"
    date="$(date '+%d.%m.%Y %H:%M %z')"
    mkdir -p "$(dirname "$REPORT")"
    block="$(printf '%s\n' \
        "## Состояние на $date" \
        "" \
        "- версия: \`$tag\`" \
        "- тег: \`$tag\`" \
        "- git_sha: \`$health\` (совпадает с коммитом тега \`${sha:0:12}\`)" \
        "- env=prod" \
        "- источник: \`/api/health\` после run $run_id (dry_run=false)" \
        "- состав: см. \`CHANGELOG.md\`, раздел \`$tag\`" \
        "")"
    printf '\n%s\n' "$block" >> "$REPORT"
    git add "$REPORT"
    git commit -m "docs(release): $tag" \
        || warn "не удалось закоммитить $REPORT (проверьте git status)"
    say "Состояние записано в $REPORT и закоммичено: docs(release): $tag"
    say "Отправить отчёт в main: git push origin main"
}

status() {
    local top tag run_id rc log sha since
    top="$(root)"
    cd "$top"
    command -v gh >/dev/null 2>&1 || die "gh не найден: состояние прода смотрится через GitHub Actions"

    tag="$(git describe --tags --abbrev=0 2>/dev/null || true)"
    [ -n "$tag" ] || die "в репозитории нет тегов — нечего проверять"

    say "Проверка прода без изменений: gh workflow run $WORKFLOW -f tag=$tag -f dry_run=true"
    sha="$(git rev-parse HEAD)"
    since="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    gh workflow run "$WORKFLOW" -f "tag=$tag" -f "dry_run=true" \
        || die "не удалось запустить проверочный run"

    if ! run_id="$(find_run_id "$sha" "$since")"; then
        die "проверочный run для коммита $sha после $since не появился в gh run list"
    fi
    rc=0
    gh run watch "$run_id" --exit-status || rc=$?

    log="$(gh run view "$run_id" --log 2>/dev/null || true)"
    say "Версия в проде (по логу $WORKFLOW, dry_run=true):"
    printf '%s\n' "$log" \
        | grep -E "сейчас:|цель:|коммитов:|миграций:|systemctl is-active|на диске" \
        | sed -E 's/^[^\t]*\t[^\t]*\t//; s/^[[:space:]]+//' \
        | sed 's/^/  /' || true
    [ "$rc" -eq 0 ] || die "проверочный run завершился с кодом $rc"
    say "Готово: прод не менялся (dry_run=true)."
}

main() {
    local arg
    for arg in "$@"; do
        case "$arg" in
            --help|-h) usage; return 0 ;;
            --apply) MODE="apply" ;;
            --status) MODE="status" ;;
            --skip-tests) SKIP_TESTS=1 ;;
            -*) die "неизвестный флаг: $arg (см. --help)" ;;
            *)
                [ -z "$TAG" ] || die "лишний аргумент: $arg"
                normalize_version "$arg"
                ;;
        esac
    done

    case "$MODE" in
        status)
            [ -z "$TAG" ] || die "--status не принимает версию"
            status
            ;;
        apply)
            [ -n "$TAG" ] || { usage >&2; die "для --apply нужна версия"; }
            apply
            ;;
        prepare)
            [ -n "$TAG" ] || { usage >&2; die "нужна версия: deploy/release.sh vX.Y.Z"; }
            prepare
            ;;
    esac
}

main "$@"
