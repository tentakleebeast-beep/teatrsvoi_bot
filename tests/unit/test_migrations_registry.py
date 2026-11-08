"""Реестр ревизий Alembic: 1:1 с файлами db/init, цепочка линейная, хэши сходятся.

Единственный источник схемы — `db/init/*.sql` (docs/reports/132, решение 518).
Ревизия — обёртка над одним файлом, поэтому проверяются три обещания:
  * число ревизий равно числу файлов, имена совпадают (пропуск = файл без наката);
  * цепочка линейная с одной головой (ветвление Alembic отвергнет на накате);
  * sha256 в ревизии совпадает с файлом (правка применённого файла — стоп, а не
    тихая смена истории).

Отдельно проверяется, что второго описания схемы в коде нет: `core.DDL`/`init_db`
и mount `db/init` в compose у postgres.

Обоснование: docs/reports/132-db-schema-single-source-plan.md; дефект 30.10.2026 —
на проде не хватало `participant.write_gender` и `budget_tx.is_plan`, Mini App падал с 500.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
INIT_DIR = ROOT / "db" / "init"
VERSIONS_DIR = ROOT / "services" / "tool-api" / "migrations" / "versions"
CORE = ROOT / "services" / "tool-api" / "app" / "core.py"
COMPOSE = ROOT / "docker-compose.yml"


def load_revision(path: pathlib.Path):
    """Импорт файла ревизии по пути: Alembic грузит их так же, без пакета."""
    spec = importlib.util.spec_from_file_location(f"revision_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REVISION_PATHS = sorted(VERSIONS_DIR.glob("*.py"))
REVISIONS = [load_revision(path) for path in REVISION_PATHS]
SQL_FILES = sorted(INIT_DIR.glob("*.sql"))


def test_one_revision_per_sql_file() -> None:
    """58 файлов → 58 ревизий с теми же именами: ни лишних, ни забытых."""
    assert len(SQL_FILES) == 58, f"файлов db/init: {len(SQL_FILES)}, ожидалось 58"
    assert [path.stem for path in REVISION_PATHS] == [path.stem for path in SQL_FILES]


def test_chain_is_linear_with_one_head() -> None:
    """down_revision каждого файла — предыдущий по имени; у первого None."""
    down = [module.down_revision for module in REVISIONS]
    assert down[0] is None
    assert down[1:] == [path.stem for path in REVISION_PATHS[:-1]]
    heads = [module.revision for module in REVISIONS
             if module.revision not in set(down)]
    assert heads == ["061_master_role"]


def test_revision_points_to_its_sql_file() -> None:
    """Имя файла в ревизии совпадает с её собственным именем."""
    for path, module in zip(REVISION_PATHS, REVISIONS, strict=True):
        assert module.revision == path.stem
        assert module.SQL_FILE == f"{path.stem}.sql"


def test_sql_sha256_matches_file() -> None:
    """Хэш в ревизии — от текущего файла; правка применённого SQL видна здесь."""
    for module in REVISIONS:
        data = (INIT_DIR / module.SQL_FILE).read_bytes()
        assert hashlib.sha256(data).hexdigest() == module.SQL_SHA256, module.SQL_FILE


def test_downgrade_is_disabled() -> None:
    """Откат не поддерживается (решение 518): явный NotImplementedError."""
    for module in REVISIONS:
        with pytest.raises(NotImplementedError):
            module.downgrade()


def test_core_has_no_schema_description() -> None:
    """`core.DDL` и `core.init_db` удалены: схема описывается только в db/init."""
    tree = ast.parse(CORE.read_text(encoding="utf-8"))
    assigned = {target.id for node in tree.body if isinstance(node, ast.Assign)
                for target in node.targets if isinstance(target, ast.Name)}
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert "DDL" not in assigned
    assert "init_db" not in functions


def test_compose_migrates_and_has_no_initdb_mount() -> None:
    """Схему накатывает сервис migrate; initdb.d у postgres не возвращается."""
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = compose["services"]
    assert "migrate" in services
    assert "upgrade" in services["migrate"]["command"]
    volumes = services["postgres"].get("volumes", [])
    assert not any("db/init" in str(volume) for volume in volumes)
    assert services["tool-api"]["depends_on"]["migrate"]["condition"] == \
        "service_completed_successfully"


def test_generator_reports_no_drift() -> None:
    """Генератор сверяет ревизии с db/init: расхождение — код 1."""
    from gen_alembic_revisions import main as generator_main  # noqa: PLC0415

    assert generator_main(["--check"]) == 0


def test_revision_template_yields_importable_revision(tmp_path) -> None:
    """Шаблон даёт ревизию, которую Alembic читает без env.py.

    Импорт `migrations.sql` на уровне модуля ломал `alembic heads`: каталог
    `migrations` попадает в sys.path только из env.py. Шаблон рендерится с теми же
    переменными, что у генератора (`sql_file`, `sql_sha256`), затем Alembic ищет
    голову — то есть импортирует файл ревизии.
    """
    from alembic.script import ScriptDirectory  # noqa: PLC0415
    from alembic.util import format_as_comma  # noqa: PLC0415
    from mako.template import Template  # noqa: PLC0415

    versions = tmp_path / "versions"
    versions.mkdir()
    rendered = Template(filename=str(VERSIONS_DIR.parent / "script.py.mako")).render(
        up_revision="001_probe",
        down_revision=None,
        branch_labels=None,
        depends_on=None,
        create_date="2026-01-01 00:00:00",
        comma=format_as_comma,
        message="probe",
        sql_file="001_schema.sql",
        sql_sha256="0" * 64,
    )
    (versions / "001_probe.py").write_text(rendered, encoding="utf-8")

    script = ScriptDirectory(str(tmp_path))
    assert script.get_heads() == ["001_probe"]
