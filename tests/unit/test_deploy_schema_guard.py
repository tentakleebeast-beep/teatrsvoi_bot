"""Сверка наката схемы с головой выкаченного кода (дефект выката v0.10.0).

Обоснование: docs/infrastructure/35 §10.2 (порядок «тег → миграции из тега → выкат»).

10.11.2026 `repair-schema` накатил 063 из `main`, а на сервере стоял v0.9.2 с головой
062: `tool-api` при старте отказался подниматься (`RuntimeError: схема не применена`),
systemd ушёл в `activating (auto-restart)`, сайт отдавал ошибку около трёх минут.
Опасны оба расхождения — версия впереди кода и позади кода, — поэтому проверяются
обе стороны.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import shutil
import subprocess
import sys
import types

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "check_schema.py"
WORKFLOW = ROOT / ".github" / "workflows" / "repair-schema.yml"
RUNBOOK = ROOT / "docs" / "infrastructure" / "35-deploy-runbook.md"

DEAD_DB = "postgresql://svoi:no@127.0.0.1:9/svoi"   # закрытый порт: соединения не будет


def _module():
    spec = importlib.util.spec_from_file_location("check_schema", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(*args: str, database_url: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items()
           if key not in {"DATABASE_URL", "PYTHONPATH"}}
    if database_url:
        env["DATABASE_URL"] = database_url
    return subprocess.run([os.sys.executable, str(SCRIPT), *args],
                          cwd=ROOT, capture_output=True, text=True, env=env)


def _code_tree(tmp_path: pathlib.Path, drop: int) -> pathlib.Path:
    """Дерево «выкаченного кода»: цепочка ревизий без `drop` последних ревизий."""
    tree = tmp_path / "code"
    shutil.copytree(ROOT / "services" / "tool-api" / "migrations",
                    tree / "services" / "tool-api" / "migrations")
    shutil.copy(ROOT / "alembic.ini", tree / "alembic.ini")
    versions = tree / "services" / "tool-api" / "migrations" / "versions"
    for _ in range(drop):
        revisions, downs = {}, set()
        for path in sorted(versions.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            revision = re.search(r'^revision = "([^"]+)"', text, re.M)
            assert revision is not None, path
            revisions[revision.group(1)] = path
            down = re.search(r'^down_revision = "([^"]+)"', text, re.M)
            if down:
                downs.add(down.group(1))
        heads = [rev for rev in revisions if rev not in downs]
        assert len(heads) == 1, heads
        revisions[heads[0]].unlink()
    return tree


def _heads(tree: pathlib.Path) -> tuple[str, list[str]]:
    module = _module()
    chain_list = module.chain(tree)
    return chain_list[0], chain_list


def _head_revision() -> str:
    return _heads(ROOT)[0]


def _previous_revision() -> str:
    """Ревизия перед головой цепочки: `--files` с ней = накат не до конца."""
    return _heads(ROOT)[1][1]


def test_отношение_цели_к_коду_различает_вперёд_и_назад():
    """Обе стороны расхождения лечатся по-разному — их нельзя путать.

    `ahead` — миграция новее кода (v0.10.0: 063 против 062) роняет старт сервиса.
    `behind` — код новее схемы (v0.8.0), сервис тоже не поднимется.
    """
    module = _module()
    code_chain = ["062_files_studio_access", "061_master_role", "060_participant_role_revoked"]
    assert module.code_relation("062_files_studio_access", code_chain) == "equal"
    assert module.code_relation("063_feedback_attachment", code_chain) == "ahead"
    assert module.code_relation("061_master_role", code_chain) == "behind"


def test_текст_сверки_называет_нужный_тег_и_обе_стороны():
    """Дежурный читает отказ, а не ищет причину: тег, ревизии и путь лечения."""
    module = _module()
    ahead = module.code_note("ahead", "063_feedback_attachment", "062_files_studio_access",
                             "v0.9.2", pathlib.Path("/opt/svoi"))
    assert "063_feedback_attachment" in ahead and "062_files_studio_access" in ahead
    assert "тег v0.9.2" in ahead
    assert "Нужен тег" in ahead, "отказ обязан называть, какой тег выкатывать первым"
    assert "server-deploy.sh db" in ahead and "35 §10.2" in ahead

    behind = module.code_note("behind", "061_master_role", "062_files_studio_access",
                              "v0.9.2", pathlib.Path("/opt/svoi"))
    assert "отстаёт" in behind.lower()
    assert "server-deploy.sh db" in behind and "35 §10.2" in behind


def test_накат_вперёд_выкаченного_кода_отклоняется_до_касания_базы(tmp_path):
    """Файл новее головы кода на диске: отказ, база не тронута, версия не поставлена.

    Это ровно случай v0.10.0: 063 из `main` при выкаченном теге с головой 062.
    Проверка обязана сработать до подключения: иначе структура уедет вперёд учёта.
    """
    old_code = _code_tree(tmp_path, drop=1)
    head = _head_revision()

    result = _run("--stamp-head", "--files", f"db/init/{head}.sql",
                  "--code-root", str(old_code), database_url=DEAD_DB)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "НОВАЯ МИГРАЦИЯ ВПЕРЁД ВЫКАЧЕННОГО КОДА" in result.stdout, result.stdout
    assert head in result.stdout and _previous_revision() in result.stdout
    assert "Нужен тег" in result.stdout
    assert "Traceback" not in result.stderr, "отказ должен быть текстом, а не падением psycopg"
    assert "connection" not in result.stderr.lower(), "до базы дело доходить не должно"


def test_read_only_сверка_отклоняет_накат_вперёд_кода(tmp_path):
    """`--check-code` — предпроверка workflow: отказ до apply, DATABASE_URL не нужен."""
    old_code = _code_tree(tmp_path, drop=1)
    head = _head_revision()

    result = _run("--check-code", "--files", f"db/init/{head}.sql",
                  "--code-root", str(old_code))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "НОВАЯ МИГРАЦИЯ ВПЕРЁД ВЫКАЧЕННОГО КОДА" in result.stdout
    assert "Нужен тег" in result.stdout
    assert "DATABASE_URL" not in result.stdout, "предпроверка не ходит в базу"


def test_накат_файлов_выкаченного_тега_разрешён(tmp_path):
    """Голова кода равна накатываемому файлу: накат безопасен, отказ не мешает работе."""
    tree = _code_tree(tmp_path, drop=0)
    head = _head_revision()

    result = _run("--check-code", "--files", f"db/init/{head}.sql", "--code-root", str(tree))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "накат безопасен" in result.stdout


def test_накат_позади_кода_предупреждает_но_не_блокирует(tmp_path):
    """Частичный накат — законный шаг (v0.8.0): сообщение есть, отказ не блокирует."""
    tree = _code_tree(tmp_path, drop=0)
    older = _previous_revision()

    result = _run("--check-code", "--files", f"db/init/{older}.sql", "--code-root", str(tree))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "СХЕМА ОТСТАЁТ ОТ ВЫКАЧЕННОГО КОДА" in result.stdout
    assert "server-deploy.sh db" in result.stdout


def test_явный_несуществующий_код_корень_останавливает_накат(tmp_path):
    """`--code-root` назван, а кода там нет: сверять не с чем — отказ, не «пропущено».

    Молчаливый пропуск проверки вернул бы дефект v0.10.0 через опечатку в пути.
    """
    missing = tmp_path / "no-such-code"
    head = _head_revision()

    result = _run("--check-code", "--files", f"db/init/{head}.sql", "--code-root", str(missing))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "сверить" in result.stdout and "--code-root" in result.stdout


def test_workflow_сверяет_файлы_с_выкаченным_кодом_до_наката():
    """Предпроверка идёт до apply: отказ не оставляет базу впереди учёта версии."""
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = data["jobs"]["repair"]["steps"]
    names = [step.get("name", "") for step in steps]

    copy_at = next(i for i, step in enumerate(steps)
                   if "/tmp/svoi-schema-repair" in step.get("run", ""))
    check_at = next(i for i, name in enumerate(names) if "не новее выкаченного кода" in name)
    apply_at = names.index("применить миграции")
    sync_at = next(i for i, name in enumerate(names) if "учёт версии схемы" in name)
    assert copy_at < check_at < apply_at < sync_at, names

    check = steps[check_at]
    assert check.get("if") == "inputs.files != ''"
    assert "--check-code" in check["run"] and "--files" in check["run"]
    assert "--code-root /opt/svoi" in check["run"], "сверять надо с выкаченным кодом"
    assert "$SCHEMA_DIR" in check["run"], "alembic берётся из копии схемы раннера"

    sync = steps[sync_at]
    assert "--code-root /opt/svoi" in sync["run"], (
        "и --stamp-head обязан сверяться с головой кода на диске, а не только с checkout")


def test_runbook_фиксирует_порядок_тег_миграции_выкат():
    """Порядок в runbook — то, что дежурный выполнит; накат из `main` запрещён."""
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert "## 10.2. Порядок «тег → миграции из тега → выкат»" in runbook
    section = runbook.split("## 10.2.", 1)[1].split("## 10.3.", 1)[0]

    assert "git checkout" in section, "миграции накатываются из дерева тега"
    step_migrations = section.split("2. **миграции из тега**", 1)[1].split("3. **выкат**", 1)[0]
    step_release = section.split("3. **выкат**", 1)[1].split("**Запрещено**", 1)[0]
    assert "server-deploy.sh db" in step_migrations, "миграции — из дерева тега"
    assert "release.sh <тег> --apply" in step_release, "выкат идёт после миграций"
    assert "запрещ" in section.lower()
    assert "`main`" in section
    assert "--check-code" in section or "выкаченного кода" in section


def test_докстринг_скрипта_знает_новые_флаги():
    """Флаги живут в докстринге: по нему дежурный выбирает команду."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "--check-code" in text and "--code-root" in text


def test_итоговая_версия_учёта_не_уходит_назад():
    """Учёт не понижается: цель из `--files` старше записанной версии — остаётся версия.

    Именно поэтому сверять с кодом надо итог, а не запрошенную цель: старый файл в
    списке `repair-schema` не отменяет уже уехавшую вперёд `alembic_version`.
    """
    module = _module()
    chain_list = ["063_feedback_attachment", "062_files_studio_access", "061_master_role"]
    order = {rev: index for index, rev in enumerate(chain_list)}

    assert module.resulting_version("062_files_studio_access",
                                    "063_feedback_attachment", order) == "063_feedback_attachment"
    assert module.resulting_version("062_files_studio_access",
                                    "061_master_role", order) == "062_files_studio_access"
    assert module.resulting_version("062_files_studio_access", None, order) == "062_files_studio_access"


def test_залипший_учёт_впереди_кода_отклоняется_и_повторным_накатом(tmp_path, capsys):
    """Учёт уже впереди кода, оператор повторяет накат со старым файлом — отказ, не exit 0.

    Состояние после инцидента v0.10.0: `alembic_version` = 063 (голова раннера), на диске
    выкачен код с головой 062, в `--files` назван 062. Без сверки итоговой версии скрипт
    видел «цель позади кода», молчал и выходил по раннему «версия равна head» — зелёный
    workflow при службе в `activating (auto-restart)`. База не нужна: подменяем чтение
    версии и проверку структуры.
    """
    old_code = _code_tree(tmp_path, drop=1)
    head = _head_revision()
    older = _previous_revision()
    module = _module()

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    module.structure_gaps = lambda url: ([], [])  # type: ignore[method-assign]
    module.current_version = lambda conn: head  # type: ignore[assignment]
    module.psycopg = types.SimpleNamespace(connect=lambda url: _Conn())  # type: ignore[assignment]

    code = module.stamp_head(DEAD_DB, ROOT, [f"db/init/{older}.sql"], old_code, True)
    out = capsys.readouterr().out

    assert code == 1, out
    assert "НОВАЯ МИГРАЦИЯ ВПЕРЁД ВЫКАЧЕННОГО КОДА" in out, out
    assert head in out and older in out, "отказ обязан назвать и учёт, и голову кода"
    assert "Нужен тег" in out
    assert "учёт актуален" not in out, "ранний выход не должен срабатывать раньше сверки"

