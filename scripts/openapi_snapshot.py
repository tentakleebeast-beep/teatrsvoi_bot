#!/usr/bin/env python3
"""Снимок контракта tool-api: генерация и проверка (INT-012, docs/development/26 §4a).

Контракт менялся молча: при разрезе tool-api на роутеры переименования полей и путей
ловились только тестами, которые кто-то не забыл обновить. Здесь снимок фактической
схемы `app.openapi()`: любое расхождение — код 1 и точечный дифф, пока снимок не
обновлён осознанно.

  env -u PYTHONPATH .venv/bin/python scripts/openapi_snapshot.py           # проверка
  env -u PYTHONPATH .venv/bin/python scripts/openapi_snapshot.py --write   # обновление

Снимок детерминирован: ключи отсортированы, выброшено всё, что не описывает проводной
контракт (описания, примеры, operationId, info/servers), поэтому смена текста справки
или адреса запуска тест не роняет. Шум вырезается по позиции: имена полей внутри
`properties` — часть контракта, даже если поле называется `title` или `description`.
Из схемы не выводятся секреты и внутренние адреса — это проверяет
`tests/integration/test_openapi_contract.py`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
TOOL_API = PROJECT / "services" / "tool-api"
SNAPSHOT_PATH = PROJECT / "contracts" / "openapi.json"

# Аннотации pydantic и OpenAPI: описания, примеры, operationId (имя функции-обработчика).
# Их правка не меняет проводной контракт, поэтому шум не должен ронять тест.
# Ключ вырезается по позиции: если он стоит рядом с `type` в подсхеме — это аннотация,
# а если это имя свойства внутри `properties` — это поле контракта, и оно остаётся.
ANNOTATION_KEYS = frozenset({
    "contact",
    "description",
    "example",
    "examples",
    "externalDocs",
    "license",
    "operationId",
    "summary",
    "termsOfService",
    "title",
})
# Корневые ключи, зависящие от окружения: адрес запуска, версия, название сервиса.
ROOT_KEYS = frozenset({"info", "openapi", "servers", "tags"})
# Отображения, где ключи — имена (полей, схем, путей, кодов ответа, медиатипов),
# а не аннотации: имя поля — часть контракта, его нельзя выбрасывать по совпадению.
NAME_MAPS = frozenset({
    "$defs",
    "callbacks",
    "content",
    "definitions",
    "headers",
    "mapping",
    "paths",
    "patternProperties",
    "properties",
    "responses",
    "schemas",
    "securitySchemes",
    "webhooks",
})


def build_schema() -> dict[str, Any]:
    """Фактическая схема приложения. Импорт без БД: маршруты регистрируются при импорте."""
    if str(TOOL_API) not in sys.path:
        sys.path.insert(0, str(TOOL_API))
    # Корень нужен пакету `services.shared` (`from services.shared import format`).
    if str(PROJECT) not in sys.path:
        sys.path.insert(0, str(PROJECT))
    from app.main import app  # noqa: PLC0415 — импорт важен после правки sys.path

    return app.openapi()


def normalise(value: Any, *, names: bool = False, root: bool = False) -> Any:
    """Убирает из схемы всё, что не является контрактом, и сортирует ключи.

    `names` — мы находимся внутри отображения имён (`properties`, `paths`, `schemas`):
    здесь любой ключ, включая `title` и `description`, это имя поля или маршрута, то есть
    часть контракта. `root` — верхний уровень схемы, где выбрасываются зависящие от
    окружения `info`/`servers`/`openapi`/`tags`.
    """
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for key in sorted(value):
            if root and key in ROOT_KEYS:
                continue
            if not names and key in ANNOTATION_KEYS:
                continue
            # Ключ `properties` (и подобные) говорит: следующий уровень — имена, не аннотации.
            # Ошибка в эту сторону безопасна: лишняя аннотация в снимке тест не роняет,
            # а пропущенное имя поля — роняет и прячет изменение контракта.
            result[key] = normalise(value[key], names=key in NAME_MAPS)
        return result
    if isinstance(value, list):
        return [normalise(item) for item in value]
    return value


def snapshot_data() -> dict[str, Any]:
    """Снимок, который должен лежать в `contracts/openapi.json`."""
    return normalise(build_schema(), root=True)


def render(schema: dict[str, Any]) -> str:
    """Текст снимка: одинаковый байт-в-байт при одинаковой схеме."""
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_snapshot(schema: dict[str, Any], path: Path | None = None) -> None:
    """Единственный путь обновления снимка — явный вызов (флаг `--write`).

    Путь по умолчанию берётся на момент вызова, а не при импорте: иначе подмена
    `SNAPSHOT_PATH` в тестах не действует и тест пишет в боевой файл.
    """
    target = path or SNAPSHOT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(schema), encoding="utf-8")


def load_snapshot(path: Path | None = None) -> dict[str, Any]:
    """Снимок с диска; отсутствие файла — не «пустая схема», а ошибка контракта."""
    target = path or SNAPSHOT_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def _escape(token: str) -> str:
    """JSON Pointer: `/` и `~` в имени ключа экранируются, иначе путь неоднозначен."""
    return token.replace("~", "~0").replace("/", "~1")


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Схема в плоский вид `путь -> значение`: так дифф остаётся точечным."""
    if isinstance(value, dict) and value:
        flat: dict[str, Any] = {}
        for key in sorted(value):
            flat.update(flatten(value[key], f"{prefix}/{_escape(str(key))}"))
        return flat
    if isinstance(value, list) and value:
        flat = {}
        for index, item in enumerate(value):
            flat.update(flatten(item, f"{prefix}/{index}"))
        return flat
    return {prefix: value}


def diff_snapshots(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Точечные строки диффа: путь, что было, что стало.

    Показываются и удалённые, и добавленные, и изменённые листья — по одному на строку.
    Порядок сортированный, поэтому вывод стабилен и его можно сравнивать в тестах.
    """
    before, after = flatten(old), flatten(new)
    lines: list[str] = []
    for path in sorted(before.keys() - after.keys()):
        lines.append(f"- {path}: {_show(before[path])} (удалено)")
    for path in sorted(after.keys() - before.keys()):
        lines.append(f"+ {path}: {_show(after[path])} (добавлено)")
    for path in sorted(before.keys() & after.keys()):
        if before[path] != after[path]:
            lines.append(f"~ {path}: {_show(before[path])} -> {_show(after[path])}")
    return lines


def _show(value: Any) -> str:
    """Значение листа в одну строку."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def describe_changes(old: dict[str, Any], new: dict[str, Any], limit: int = 0) -> str:
    """Человеческий отчёт об изменении контракта (для отчёта карты)."""
    lines = diff_snapshots(old, new)
    if limit:
        lines = lines[:limit]
    return "\n".join(lines)


def _display_path() -> str:
    """Путь снимка для сообщений: внутри проекта — короткий, иначе абсолютный."""
    if SNAPSHOT_PATH.is_relative_to(PROJECT):
        return str(SNAPSHOT_PATH.relative_to(PROJECT))
    return str(SNAPSHOT_PATH)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Снимок OpenAPI tool-api: проверка и обновление")
    parser.add_argument("--write", action="store_true",
                        help="обновить contracts/openapi.json вместо сравнения")
    args = parser.parse_args(argv)

    current = snapshot_data()
    if args.write:
        old = load_snapshot() if SNAPSHOT_PATH.exists() else {}
        write_snapshot(current)
        changes = describe_changes(old, current)
        print(f"снимок обновлён: {_display_path()}")
        if changes:
            print("изменения контракта:")
            print(changes)
        return 0

    if not SNAPSHOT_PATH.exists():
        print(f"снимка нет: {_display_path()}; "
              f"обновить явно: scripts/openapi_snapshot.py --write", file=sys.stderr)
        return 1

    changes = diff_snapshots(load_snapshot(), current)
    if not changes:
        print("контракт совпадает")
        return 0

    print(f"контракт изменился: {len(changes)} отличий", file=sys.stderr)
    for line in changes:
        print(line, file=sys.stderr)
    print("если изменение осознанное — обновить снимок: "
          "scripts/openapi_snapshot.py --write", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
