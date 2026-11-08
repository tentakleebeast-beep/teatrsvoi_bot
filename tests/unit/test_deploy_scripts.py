"""Скрипты переезда на сервер (docs/infrastructure/75).

Проверяется то, что можно проверить без сервера: синтаксис bash, наличие всех
переменных в образце настроек, рендер юнитов и Caddyfile. Живой сервер здесь не
нужен — иначе тест перестал бы быть unit-тестом.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
SCRIPTS = [DEPLOY / "server-deploy.sh", DEPLOY / "poller-switch.sh", DEPLOY / "lib.sh",
           DEPLOY / "release.sh"]
EXAMPLE_ENV = DEPLOY / "svoi.env.example"

# Переменные, которые обязаны быть в образце: их читают скрипты и шаблоны.
# n8n выведен из контура (V-026, 01.11.2026) — ни домена, ни порта, ни имени
# контейнера n8n в настройках деплоя больше нет.
REQUIRED_VARS = {
    "SVOI_DOMAIN", "SVOI_ACME_EMAIL", "SVOI_REPO", "SVOI_REF",
    "SVOI_ROOT", "SVOI_USER", "SVOI_TZ", "SVOI_API_PORT",
    "SVOI_PG_PORT", "SVOI_PG_CONTAINER", "SVOI_DB_NAME", "SVOI_DB_USER",
    "SVOI_BACKUP_DIR", "SVOI_GIT_KEY", "SVOI_SWAP_GB",
    "SVOI_DUMP", "SVOI_SERVER", "SVOI_SSH_USER", "SVOI_SSH_PORT",
}


def _env_keys() -> set[str]:
    keys = set()
    for line in EXAMPLE_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("SVOI_") and "=" in line:
            keys.add(line.split("=", 1)[0])
    return keys


def _render_module():
    spec = importlib.util.spec_from_file_location("render_units", DEPLOY / "render_units.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_bash_синтаксис(script):
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_образец_настроек_содержит_все_переменные():
    missing = REQUIRED_VARS - _env_keys()
    assert not missing, f"нет в deploy/svoi.env.example: {sorted(missing)}"


def test_проверка_настроек_проходит_на_образце():
    """`server-deploy.sh check` — единственная команда, безопасная до всякого сервера."""
    result = subprocess.run(
        ["bash", str(DEPLOY / "server-deploy.sh"), "check"],
        capture_output=True, text=True,
        env={**os.environ, "SVOI_DEPLOY_ENV": str(EXAMPLE_ENV)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "настройки в порядке" in result.stdout


def test_рендер_подставляет_корень_и_пользователя():
    module = _render_module()
    source = (DEPLOY / "systemd" / "svoi-tool-api.service").read_text(encoding="utf-8")
    rendered = module.render_text(source, "/srv/theatre", "art")
    assert "/srv/theatre/.venv/bin/python" in rendered
    assert "WorkingDirectory=/srv/theatre" in rendered
    assert "User=art" in rendered and "Group=art" in rendered
    assert "/opt/svoi" not in rendered


def test_рендер_пишет_юниты_и_замечает_расхождение(tmp_path):
    module = _render_module()
    module.render_dir(DEPLOY / "systemd", tmp_path, "/opt/svoi", "svoi")
    assert (tmp_path / "svoi-gateway.service").is_file()
    assert (tmp_path / "svoi-tool-api.service").is_file()

    # перерендер теми же значениями расхождений не даёт
    module.render_dir(DEPLOY / "systemd", tmp_path, "/opt/svoi", "svoi", check=True)

    # а с другими — падает, чтобы старый выкат не жил с новым шаблоном
    with pytest.raises(SystemExit):
        module.render_dir(DEPLOY / "systemd", tmp_path, "/srv/svoi", "svoi", check=True)


def test_app_env_читает_значение_с_инлайн_комментарием(tmp_path):
    """`.env` проекта содержит комментарии в строке — source их разобрал бы неверно."""
    (tmp_path / ".env").write_text(
        "POSTGRES_PASSWORD=secret#1   # пароль базы\nTZ_OFFSET_HOURS=4    # Самара\n",
        encoding="utf-8",
    )
    command = (
        f'SVOI_ROOT="{tmp_path}"; source "{DEPLOY / "lib.sh"}"; app_env POSTGRES_PASSWORD'
    )
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "secret#1"


def test_клон_для_сервера_мелкий():
    """История на 4 ГБ серверу не нужна: клон только рабочего коммита."""
    script = (DEPLOY / "server-deploy.sh").read_text(encoding="utf-8")
    assert "--depth 1" in script
    assert "checkout --force FETCH_HEAD" in script
    # откат тоже дотягивает ровно один тег, а не всю историю
    rollback = script.split("cmd_rollback()", 1)[1]
    assert "fetch --depth 1 origin" in rollback


def test_архивы_источников_не_в_git():
    """sources_example — 4 ГБ рабочих файлов заказчика, на сервере не нужны."""
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "sources_example/" in ignore

    result = subprocess.run(["git", "-C", str(ROOT), "ls-files", "sources_example"],
                            capture_output=True, text=True)
    if result.returncode != 0:          # git недоступен — проверка не наша
        pytest.skip("нет git")
    assert result.stdout.strip() == "", "архивы источников снова попали в индекс"


def test_клон_использует_ключ_рабочего_пользователя():
    """git идёт через runuser, поэтому ключ деплоя должен быть у svoi, не у root."""
    script = (DEPLOY / "server-deploy.sh").read_text(encoding="utf-8")
    assert "git_as_user()" in script
    assert "GIT_SSH_COMMAND" in script and "IdentitiesOnly=yes" in script
    # клон и откат не должны обходить помощник: иначе ключ потеряется
    assert "as_user git clone" not in script
    assert "as_user git -C" not in script
    assert "нет ключа деплоя" in script          # понятная ошибка вместо Permission denied

    example = EXAMPLE_ENV.read_text(encoding="utf-8")
    assert "SVOI_GIT_KEY" in example
    lib = (DEPLOY / "lib.sh").read_text(encoding="utf-8")
    assert "/home/$SVOI_USER/.ssh/id_ed25519" in lib


def test_маленький_сервер_защищён_swap():
    """На 2 ГБ RAM пик pip install убивает процесс без страховки (docs/75 §2.6)."""
    script = (DEPLOY / "server-deploy.sh").read_text(encoding="utf-8")
    assert "SVOI_SWAP_GB" in script and "swapon" in script
    assert "/etc/fstab" in script                  # swap переживает перезагрузку
    example = EXAMPLE_ENV.read_text(encoding="utf-8")
    assert "SVOI_SWAP_GB" in example


def test_в_caddyfile_нет_неподставленных_заглушек():
    template = (DEPLOY / "templates" / "Caddyfile.template").read_text(encoding="utf-8")
    for placeholder in ("@DOMAIN@", "@API_PORT@", "@ACME_EMAIL@"):
        assert placeholder in template, f"в шаблоне нет {placeholder}"

    # ровно те же подстановки делает server-deploy.sh proxy — сверяем состав
    script = (DEPLOY / "server-deploy.sh").read_text(encoding="utf-8")
    for placeholder in ("@DOMAIN@", "@API_PORT@", "@ACME_EMAIL@"):
        assert f"s|{placeholder}|" in script, f"скрипт не подставляет {placeholder}"


def test_публичного_адреса_n8n_в_деплое_нет():
    """V-026: маршрут n8n.*, контейнер и переменные выведены из контура целиком."""
    template = (DEPLOY / "templates" / "Caddyfile.template").read_text(encoding="utf-8")
    script = (DEPLOY / "server-deploy.sh").read_text(encoding="utf-8")
    lib = (DEPLOY / "lib.sh").read_text(encoding="utf-8")
    example = EXAMPLE_ENV.read_text(encoding="utf-8")
    for text in (template, script, lib, example):
        assert "N8N_" not in text
    assert "cmd_n8n" not in script and "relink_n8n_credential" not in script
    # n8n упоминается только в пояснении; маршрута и подстановок под него нет
    assert "@N8N_DOMAIN@" not in template and "@N8N_PORT@" not in template
    # старый адрес вебхука наружу отвечает 404, а не 200 «SVOI» из заглушки
    assert "handle /webhook/*" in template and "respond 404" in template


def test_секреты_на_сервере_не_из_образца_настроек():
    """В deploy/svoi.env.example не должно быть значений секретов."""
    text = EXAMPLE_ENV.read_text(encoding="utf-8").lower()
    for word in ("password=", "token=", "secret="):
        assert word not in text, f"в образце настроек похоже на секрет: {word}"


def test_логи_caddy_принадлежат_службе_после_validate():
    """`caddy validate` от root создаёт *.log как root:root 600 — служба их не откроет.

    Проверено на живом сервере: Caddy падал с 'permission denied' на
    /var/log/caddy/<домен>.log и не выпускал сертификат.
    """
    script = (DEPLOY / "server-deploy.sh").read_text(encoding="utf-8")
    assert "chown -R caddy:caddy /var/log/caddy" in script
    # chown должен идти и после validate, иначе файлы, созданные проверкой, останутся root
    validate_at = script.index("caddy validate --config /etc/caddy/Caddyfile")
    after = script[validate_at:]
    assert "chown -R caddy:caddy /var/log/caddy" in after


def test_восстановление_дампа_не_повторяет_миграции():
    """Дамп снят с базы, где миграции уже применены: повторный прогон ломает данные."""
    script = (DEPLOY / "server-deploy.sh").read_text(encoding="utf-8")
    assert '[ "$migrate_only" = "1" ] || [ -z "$restore" ]' in script


def test_env_проставляет_адрес_витрины_из_домена():
    script = (DEPLOY / "server-deploy.sh").read_text(encoding="utf-8")
    assert "s|^WEBAPP_BASE_URL=.*|WEBAPP_BASE_URL=https://$SVOI_DOMAIN|" in script


def test_normalize_env_убирает_инлайн_комментарии(tmp_path):
    """systemd EnvironmentFile читает строку буквально: '7  # срок' ломает int()."""
    path = tmp_path / ".env"
    path.write_text(
        "INVITE_TTL_DAYS=7                # срок жизни кода регистрации\n"
        "# отдельный комментарий остаётся\n"
        "QUOTED=\"значение # с решёткой\"   # пояснение\n",
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location("normalize_env", DEPLOY / "normalize_env.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.normalize(path) == 2
    text = path.read_text(encoding="utf-8")
    assert "INVITE_TTL_DAYS=7\n" in text
    assert "# срок жизни кода регистрации" in text
    assert "QUOTED=\"значение # с решёткой\"\n" in text      # кавычки сохраняют решётку
    assert "# отдельный комментарий остаётся" in text


def test_env_прогоняется_через_normalize(tmp_path):
    script = (DEPLOY / "server-deploy.sh").read_text(encoding="utf-8")
    assert "normalize_env.py" in script


def test_pgrep_не_ловит_сам_себя():
    """ssh передаёт всю строку целиком: шаблон без [s] совпадает с самой проверкой."""
    script = (DEPLOY / "poller-switch.sh").read_text(encoding="utf-8")
    assert 'GATEWAY_MATCH="[s]ervices/tg-gateway/app/main.py"' in script
    assert "pgrep -f \"$GATEWAY_MATCH\"" in script
    assert "pgrep -f '$GATEWAY_MATCH'" in script


def test_units_не_включают_таймеры_без_флага():
    """Пока живёт launchd на маке, включённые таймеры дали бы двойные уведомления."""
    script = (DEPLOY / "server-deploy.sh").read_text(encoding="utf-8")
    assert "--with-timers" in script
    enable_at = script.index("if [ \"$with_timers\" = \"1\" ]; then")
    assert "svoi-notify-plan.timer" in script[enable_at:]


def test_check_schema_видит_колонки_из_миграций():
    """Восстановленная база отставала от кода: не было write_gender и is_plan."""
    spec = importlib.util.spec_from_file_location("check_schema", DEPLOY / "check_schema.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    tables, columns = module.expected()
    assert "participant.write_gender" in columns        # 039
    assert "budget_tx.is_plan" in columns               # 045
    assert "availability_template" in tables            # 040


def test_schema_сверяется_венвом_приложения():
    """psycopg есть только в venv: системный python3 его не знает."""
    script = (DEPLOY / "server-deploy.sh").read_text(encoding="utf-8")
    assert 'venv_python="$REPO_DIR/.venv/bin/python"' in script
    assert "  schema)     shift; cmd_schema ;;" in script


def test_переключение_не_использует_ssh_t():
    """`ssh -t` требует терминал и молча роняет скрипт при запуске агентом."""
    script = (DEPLOY / "poller-switch.sh").read_text(encoding="utf-8")
    assert '-t "$REMOTE"' not in script
    assert "sudo -n" in script


def test_pgrep_с_pipefail_не_валит_скрипт():
    """pgrep без совпадений = код 1; с pipefail это валит `left=$(local_gateways)`."""
    script = (DEPLOY / "poller-switch.sh").read_text(encoding="utf-8")
    line = next(row for row in script.splitlines() if row.startswith("local_gateways()"))
    assert "|| true" in line


def test_в_приложении_provision_идёт_раньше_ключа_деплоя():
    """Пользователь svoi создаётся в provision: до него ssh-keygen падает 'unknown user'."""
    doc = (ROOT / "docs" / "infrastructure" / "75-hosting-and-migration.md").read_text(encoding="utf-8")
    provision_at = doc.index("**2. Скрипты и подготовка сервера**")
    key_at = doc.index("**3. Ключ деплоя**")
    assert provision_at < key_at


# Таймеры: юнит, который отрендерили, но не включили, ставится и молчит.
# Так чуть не уехал сторож 409 (svoi-conflicts.timer, карта t_4aa28fe9), и так
# же молча лежал svoi-cold-rotate.timer. Проверяем не наличие файла, а включение.

def _timers() -> list[str]:
    return sorted(path.stem for path in (DEPLOY / "systemd").glob("*.timer"))


def _shell_commands(text: str, verb: str) -> str:
    """Логические строки команд `systemctl <verb> …` (с переносами через `\\`)."""
    found: list[str] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip().removeprefix("sudo ").strip()
        if not stripped.startswith(f"systemctl {verb}"):
            continue
        command = line
        while command.rstrip().endswith("\\") and index + 1 < len(lines):
            index += 1
            command += " " + lines[index]
        found.append(command)
    return "\n".join(found)


def test_каждый_таймер_включается_юнитами_деплоя():
    """`server-deploy.sh units --with-timers` обязан включать все *.timer."""
    script = (DEPLOY / "server-deploy.sh").read_text(encoding="utf-8")
    with_timers = script.split('if [ "$with_timers" = "1" ]; then', 1)[1].split("else", 1)[0]
    enabled = _shell_commands(with_timers, "enable")
    missing = [name for name in _timers() if f"{name}.timer" not in enabled]
    assert not missing, f"не включаются в server-deploy.sh: {missing}"


def test_каждый_таймер_есть_в_командах_рунбука():
    """Рунбук — второй, ручной путь включения; расхождение с script даёт мёртвый таймер."""
    doc = (ROOT / "docs" / "infrastructure" / "35-deploy-runbook.md").read_text(encoding="utf-8")
    link = _shell_commands(doc, "link")
    enable = _shell_commands(doc, "enable")
    for name in _timers():
        assert f"{name}.service" in link, f"в systemctl link нет {name}.service"
        assert f"{name}.timer" in link, f"в systemctl link нет {name}.timer"
        assert f"{name}.timer" in enable, f"в systemctl enable нет {name}.timer"


def test_локальный_сторож_конфликтов_стоит_в_расписании_мака():
    """Критерий «алерт за 5 минут» должен выполняться и на маке: лог конфликта — местный."""
    template = (DEPLOY / "launchd" / "com.svoi.conflicts.plist.template").read_text(encoding="utf-8")
    assert "conflict_watch.py" in template and "--apply" in template
    assert "--source" in template and "files" in template
    assert "<key>StartInterval</key>" in template
    assert "<integer>300</integer>" in template


WORKFLOW = ROOT / ".github" / "workflows" / "repair-schema.yml"


def test_ремонт_схемы_двигает_учёт_версии_alembic():
    """После наката SQL в psql шаг workflow обязан довести alembic_version до head.

    Дефект 08.11.2026: `repair-schema` накатил 061, psql про Alembic не знает, версия
    осталась на 060 — выкат v0.8.0 остановился на сверке версии и откатился на v0.7.0.
    Порядок шагов и наличие проверки — то, без чего дефект возвращается молча.
    Обоснование: docs/infrastructure/35 §10.2.
    """
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = data["jobs"]["repair"]["steps"]
    names = [step.get("name", "") for step in steps]

    apply_at = names.index("применить миграции")
    sync_at = next(i for i, name in enumerate(names) if "учёт версии схемы" in name)
    verify_at = next(i for i, name in enumerate(names) if "версия схемы равна head" in name)
    restart_at = next(i for i, name in enumerate(names) if name.startswith("перезапустить"))
    assert apply_at < sync_at < verify_at < restart_at, names

    sync = steps[sync_at]
    assert sync.get("if") == "inputs.files != ''"
    assert "--stamp-head" in sync["run"]
    assert "--files" in sync["run"], "версия должна ставиться по применённым файлам"
    assert "/opt/svoi/.env" in sync["run"], "на сервере DATABASE_URL лежит в .env"
    assert "$SCHEMA_DIR" in sync["run"], "путь к копии схемы задаёт шаг копирования"
    copy_run = next(step["run"] for step in steps
                    if "/tmp/svoi-schema-repair" in step.get("run", ""))
    assert "alembic.ini" in copy_run and "services/tool-api/migrations" in copy_run, (
        "alembic берётся из checkout раннера: в /opt/svoi прошлый тег без новых ревизий")

    verify = steps[verify_at]
    assert "--expect-head" in verify["run"]
    assert "alembic_version не равен head" in verify["run"], "сообщение должно называть причину"
    assert "server-deploy.sh db" in verify["run"], "сообщение должно называть лечение"
