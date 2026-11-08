"""
Сброс dev-данных перед релизом: ни одна таблица не остаётся без решения.

Скрипт удаляет данные, поэтому главный риск — забыть новую таблицу: появилась
в миграции, а в списках её нет, и после релиза в базе остаётся dev-мусор или,
наоборот, стирается справочник. Проверка читает `db/init/*.sql` и требует,
чтобы каждая таблица была отнесена к одной из трёх групп.

Правила и требования раздела: docs/infrastructure/08-infrastructure.md.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]

CREATE_TABLE = re.compile(r"create table if not exists\s+([a-z_]+)", re.IGNORECASE)


def _module():
    spec = importlib.util.spec_from_file_location(
        "reset_for_release", ROOT / "scripts" / "reset_for_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)       # type: ignore[union-attr]
    return module


def schema_tables() -> set[str]:
    found: set[str] = set()
    for path in sorted((ROOT / "db" / "init").glob("*.sql")):
        found.update(name.lower() for name in CREATE_TABLE.findall(path.read_text(encoding="utf-8")))
    return found


def test_каждая_таблица_схемы_отнесена_к_группе() -> None:
    """Новая таблица без решения — ошибка: сброс либо оставит мусор, либо сотрёт нужное."""
    module = _module()
    known = set(module.KEEP_REFERENCE) | set(module.KEEP_PEOPLE) | set(module.WIPE)
    unclassified = sorted(schema_tables() - known)
    assert unclassified == [], f"нет решения по таблицам: {unclassified}"


def test_группы_не_пересекаются() -> None:
    """Таблица не может одновременно пережить сброс и быть стёртой."""
    module = _module()
    keep = set(module.KEEP_REFERENCE) | set(module.KEEP_PEOPLE)
    assert keep & set(module.WIPE) == set()


def test_справочники_и_люди_не_стираются() -> None:
    """Права, категории, залы и сами люди — то, что нельзя восстанавливать импортом."""
    module = _module()
    for table in ("rbac_capability", "rbac_role_capability", "category", "venue",
                  "participant", "participant_role", "person_alias"):
        assert table not in module.WIPE, f"{table} не должен стираться"


def test_метка_защищает_от_повторного_сброса() -> None:
    """L-001: опасная операция ставит метку, иначе второй запуск стирает уже боевые данные."""
    module = _module()
    assert module.MARKER == "release_reset"
    source = (ROOT / "scripts" / "reset_for_release.py").read_text(encoding="utf-8")
    assert "marker_exists" in source and "уже применён" in source
