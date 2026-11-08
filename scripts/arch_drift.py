#!/usr/bin/env python3
"""Замер дрейфа архитектуры: числа для техлида, отчёт, а не гейт.

Зачем (docs/development/26 §7c, профиль `architect`): класс дефектов, который не ловит
ни один юнит-тест, копится незаметно — `api/schedule.py` вырос до 1443 строк, чтения
окружения живут вне settings, две миграции получили номер 047, открытый вебхук n8n
принимал чужой POST. Всё это обнаруживалось на разборе. Скрипт мерит дрейф заранее и
приносит владельцу числа; решения принимает человек, код здесь не правится.

Метрики и пороги:

| id | что считает | порог |
|---|---|---|
| `files_over_500` | файлы `services/**` длиннее 500 строк | 0 |
| `files_over_800` | из них длиннее 800 строк — правка требует ADR | 0 |
| `duplicate_functions` | одна и та же функция в 2+ файлах одного сервиса | 0 |
| `cross_service_imports` | модуль одного сервиса импортирует другой напрямую | 0 |
| `getenv_outside_settings` | чтения окружения вне `settings.py` сверх allowlist | 0 |
| `migration_duplicates` | повторяющиеся номера в `db/init` | 0 |
| `migration_gaps` | пропущенные номера миграций (кроме задокументированных) | 0 |
| `public_routes_unmarked` | публичный маршрут без пометки в реестре | 0 |
| `contract_differences` | расхождений снимка OpenAPI с кодом | 0 |
| `tests_without_assert` | тест без единой проверки | 0 |

Публичные маршруты помечаются в `config/public-routes.txt` (путь и причина): маршрут
считается защищённым, если в его подписи есть `Depends(...)`/`Header(...)`, иначе он
обязан быть в реестре. Мёртвая запись реестра — тоже находка.

Запуск:
    env -u PYTHONPATH .venv/bin/python scripts/arch_drift.py
    env -u PYTHONPATH .venv/bin/python scripts/arch_drift.py --json
    env -u PYTHONPATH .venv/bin/python scripts/arch_drift.py --compare docs/reports/111-arch-drift-2026-11-02.md
    env -u PYTHONPATH .venv/bin/python scripts/arch_drift.py --write-report --proposals /tmp/p.md

Код выхода: 0 — замер собран (даже если метрика за порогом: это отчёт, а не гейт),
2 — неверный аргумент. `--threshold id=значение` понижает порог для сухого прогона.

Тест: tests/unit/test_arch_drift.py.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import json
import pathlib
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

PROJECT = pathlib.Path(__file__).resolve().parents[1]

SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__",
                       ".worktrees", ".ruff_cache", ".pytest_cache", "screens"})
FILE_WARN_LINES = 500
FILE_ADR_LINES = 800
SETTINGS_FILENAME = "settings.py"
SHARED_DIR = "shared"
SETTINGS_ALLOWLIST = pathlib.Path("config") / "settings-allowlist.txt"
PUBLIC_ROUTES = pathlib.Path("config") / "public-routes.txt"
MIN_DUP_STATEMENTS = 3
SAMARA = timezone(timedelta(hours=4))

#: Пропуски номеров миграций, признанные безопасными: номер и где это объяснено.
DOCUMENTED_GAPS = {
    18: "docs/reports/63-release-readiness.md: «номера 018 нет» — миграция не создавалась",
    # 059 — буфер под параллельные ветки (на момент Ш-07 в main заняты 057 и 058).
    # Удалить тем же коммитом, который займёт 059.
    59: "db/init/060_participant_role_revoked.sql: 059 оставлен буфером под "
        "параллельные ветки, пропуск только до первой миграции с номером 059",
}

ASSERT_CALLS = frozenset({"pytest.raises", "pytest.warns", "pytest.fail", "pytest.xfail",
                          "pytest.skip"})
NO_RAISE_MARKERS = ("не должно бросить", "не должен бросить", "no raise")

#: Все метрики: ими проверяется `--threshold`, чтобы опечатка в id не прошла молча.
METRIC_IDS = frozenset({
    "files_over_500", "files_over_800", "duplicate_functions", "cross_service_imports",
    "getenv_outside_settings", "migration_duplicates", "migration_gaps",
    "public_routes_unmarked", "contract_differences", "tests_without_assert",
})


class MetricError(RuntimeError):
    """Метрику измерить не удалось: в отчёт уходит причина, а не тишина."""


def _rel(path: pathlib.Path, root: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _py_files(root: pathlib.Path, *parts: str) -> list[pathlib.Path]:
    """Python-файлы подкаталога без служебных каталогов, стабильно отсортированные."""
    base = root.joinpath(*parts)
    if not base.is_dir():
        return []
    # Служебные каталоги ищем в пути ОТ `base`, а не в абсолютном: иначе прогон из
    # worktree внутри `.worktrees/` не видит ни одного файла и метрика даёт ложный ноль.
    return sorted(path for path in base.rglob("*.py")
                  if not (set(path.relative_to(base).parts) & SKIP_DIRS))


def _parse(path: pathlib.Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        raise MetricError(f"{path}: не разбирается как Python: {exc}") from None


def _metric(metric_id: str, title: str, value: int, threshold: int,
            details: tuple[str, ...] = (), note: str = "") -> dict[str, Any]:
    return {
        "id": metric_id,
        "title": title,
        "value": value,
        "threshold": threshold,
        "status": "ok" if value <= threshold else "warn",
        "details": list(details),
        "note": note,
    }


def measure_file_sizes(root: pathlib.Path) -> list[dict[str, Any]]:
    """Размеры файлов сервисов: >500 — повод, >800 — правка требует ADR.

    Мерятся только `services/**`: инструменты в `scripts/` — не продуктовая
    архитектура, и порог для них ничего не говорит о долге продукта.
    """
    sizes: dict[str, int] = {}
    for path in _py_files(root, "services"):
        sizes[_rel(path, root)] = len(path.read_text(encoding="utf-8").splitlines())
    over_500 = sorted(((name, lines) for name, lines in sizes.items()
                       if lines > FILE_WARN_LINES), key=lambda item: (-item[1], item[0]))
    over_800 = [(name, lines) for name, lines in over_500 if lines > FILE_ADR_LINES]
    detail_500 = tuple(f"{name}: {lines} строк" for name, lines in over_500)
    return [
        _metric("files_over_500", "Файлы длиннее 500 строк", len(over_500), 0, detail_500,
                "повод назвать цену следующей правки в файле"),
        _metric("files_over_800", "Файлы длиннее 800 строк", len(over_800), 0,
                tuple(f"{name}: {lines} строк" for name, lines in over_800),
                "правка такого файла требует ADR"),
    ]


def _meaningful_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return body


def _statements(node: ast.AST) -> int:
    return sum(1 for child in ast.walk(node) if isinstance(child, ast.stmt))


def measure_duplicate_functions(root: pathlib.Path) -> dict[str, Any]:
    """Дубли помощников внутри одного сервиса.

    Сервисы `tool-api` и `tg-gateway` — независимые деплои (прямой импорт запрещён
    отдельной метрикой), поэтому одинаковый код в двух сервисах не считается дублем:
    это вынужденная плата за независимость. Дубли считаются внутри одного сервиса.
    """
    groups: dict[tuple[str, int, str], list[str]] = {}
    for service_dir in sorted((root / "services").glob("*")) if (root / "services").is_dir() else []:
        if not service_dir.is_dir() or service_dir.name in SKIP_DIRS:
            continue
        for path in _py_files(service_dir):
            for node in ast.walk(_parse(path)):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name.startswith("__") or node.name.endswith("__"):
                    continue
                body = _meaningful_body(node)
                if not body or _statements(ast.Module(body=body, type_ignores=[])) \
                        < MIN_DUP_STATEMENTS:
                    continue
                dump = "|".join(ast.dump(statement) for statement in body)
                groups.setdefault((service_dir.name, len(node.args.args), dump), []).append(
                    f"{_rel(path, root)}:{node.name}")
    duplicates = {key: names for key, names in groups.items()
                  if len(names) > 1 and len({name.split(":")[0] for name in names}) > 1}
    details = tuple(f"{names[0].split(':')[1]}: " + ", ".join(names) for names in duplicates.values())
    return _metric("duplicate_functions", "Дубли функций внутри сервиса", len(duplicates), 0,
                   details, "одна функция в 2+ файлах — повод вынести в общий помощник")


def _service_aliases(root: pathlib.Path) -> dict[str, str]:
    """Имена сервисов для поиска импортов. `shared` — общая библиотека, не сервис."""
    aliases: dict[str, str] = {}
    services = root / "services"
    if not services.is_dir():
        return aliases
    for directory in sorted(services.glob("*")):
        if directory.is_dir() and directory.name not in SKIP_DIRS \
                and directory.name != SHARED_DIR:
            aliases[directory.name] = directory.name
            aliases[directory.name.replace("-", "_")] = directory.name
    return aliases


def measure_cross_service_imports(root: pathlib.Path) -> dict[str, Any]:
    """Прямые импорты и подкладки sys.path между сервисами."""
    aliases = _service_aliases(root)
    hits: list[str] = []
    for service_dir in sorted((root / "services").glob("*")) if (root / "services").is_dir() else []:
        if not service_dir.is_dir() or service_dir.name in SKIP_DIRS:
            continue
        others = {alias: name for alias, name in aliases.items() if name != service_dir.name}
        for path in _py_files(service_dir):
            for node in ast.walk(_parse(path)):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    modules = [node.module]
                for module in modules:
                    token = module.split(".")[0]
                    if token == "services" and module.count(".") >= 1:
                        token = module.split(".")[1]
                    if token in others:
                        hits.append(f"{_rel(path, root)}:{node.lineno} -> {module} "
                                    f"(сервис {others[token]})")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and node.func.attr in ("insert", "append") \
                        and node.func.value is not None:
                    text = ast.unparse(node)
                    for alias, name in others.items():
                        if f'"{alias}"' in text or f"'{alias}'" in text:
                            hits.append(f"{_rel(path, root)}:{node.lineno} -> sys.path в {name}")
    return _metric("cross_service_imports", "Прямые импорты между сервисами", len(hits), 0,
                   tuple(hits), "сервисы превращаются в один без выгоды")


def _is_environ(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute):
        return node.attr == "environ"
    return isinstance(node, ast.Name) and node.id == "environ"


def _env_reads(tree: ast.AST) -> list[tuple[int, str]]:
    """Чтения окружения: `getenv`, `os.environ.get`, `os.environ[...]`, `os.environ` целиком."""
    hits: list[tuple[int, str]] = []
    counted: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "getenv":
                hits.append((node.lineno, "os.getenv"))
            elif isinstance(func, ast.Attribute) and func.attr == "getenv":
                hits.append((node.lineno, "os.getenv"))
            elif isinstance(func, ast.Attribute) and func.attr == "get" and _is_environ(func.value):
                hits.append((node.lineno, "os.environ.get"))
                counted.add(id(func.value))
        elif isinstance(node, ast.Subscript) and _is_environ(node.value):
            hits.append((node.lineno, "os.environ[...]"))
            counted.add(id(node.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "environ" and id(node) not in counted:
            hits.append((node.lineno, "os.environ"))
    return sorted(hits)


def read_settings_allowlist(root: pathlib.Path) -> dict[str, int]:
    """Allowlist settings-карты: `путь число # причина`. Нет файла — пустой список."""
    path = root / SETTINGS_ALLOWLIST
    if not path.is_file():
        return {}
    allowed: dict[str, int] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        body = line.partition("#")[0].split()
        if len(body) == 2 and body[1].isdigit():
            allowed[body[0]] = int(body[1])
    return allowed


def measure_getenv(root: pathlib.Path) -> dict[str, Any]:
    """Чтения окружения вне settings.py сверх allowlist."""
    allowed = read_settings_allowlist(root)
    violations: list[str] = []
    total = 0
    for path in _py_files(root, "services"):
        if path.name == SETTINGS_FILENAME:
            continue
        hits = _env_reads(_parse(path))
        if not hits:
            continue
        total += len(hits)
        rel = _rel(path, root)
        permitted = allowed.get(rel, 0)
        for line, what in hits[permitted:]:
            violations.append(f"{rel}:{line} {what}")
    note = (f"allowlist {SETTINGS_ALLOWLIST.as_posix()}: "
            f"{len(allowed)} файлов, {sum(allowed.values())} разрешённых чтений")
    return _metric("getenv_outside_settings", "Чтения окружения вне settings", len(violations), 0,
                   tuple(violations), note + f"; всего чтений вне settings.py: {total}")


def measure_migrations(root: pathlib.Path) -> list[dict[str, Any]]:
    """Номера миграций: дубли и пропуски."""
    init = root / "db" / "init"
    files = sorted(init.glob("*.sql")) if init.is_dir() else []
    numbers: dict[int, list[str]] = {}
    for path in files:
        match = re.match(r"(\d+)", path.name)
        if match:
            numbers.setdefault(int(match.group(1)), []).append(path.name)
    duplicates = {number: names for number, names in numbers.items() if len(names) > 1}
    top = max(numbers, default=0)
    gaps = [number for number in range(1, top + 1)
            if number not in numbers and number not in DOCUMENTED_GAPS]
    duplicate_details = tuple(f"{number:03d}: " + ", ".join(names)
                              for number, names in sorted(duplicates.items()))
    gap_details = tuple(f"{number:03d}" for number in gaps)
    documented = tuple(f"{number:03d}: {DOCUMENTED_GAPS[number]}"
                       for number in sorted(DOCUMENTED_GAPS) if top >= number)
    return [
        _metric("migration_duplicates", "Повторяющиеся номера миграций", len(duplicates), 0,
                duplicate_details, "два файла с одним номером ломают порядок применения"),
        _metric("migration_gaps", "Пропущенные номера миграций", len(gaps), 0, gap_details,
                "задокументированные пропуски не считаются: " + "; ".join(documented)
                if documented else "задокументированных пропусков нет"),
    ]


def read_public_routes(root: pathlib.Path) -> tuple[dict[str, str], list[str]]:
    """Реестр публичных маршрутов: путь -> причина. Возвращает реестр и записи без причины."""
    path = root / PUBLIC_ROUTES
    registry: dict[str, str] = {}
    without_reason: list[str] = []
    if not path.is_file():
        return registry, without_reason
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        route, _, reason = line.partition("#")
        route = route.strip()
        if not route:
            continue
        if reason.strip():
            registry[route] = reason.strip()
        else:
            without_reason.append(route)
    return registry, without_reason


ROUTE_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
PROTECTION_MARKERS = ("Depends(", "Header(", "authorization")


def _declared_routes(root: pathlib.Path) -> list[tuple[str, str, int, bool]]:
    """Маршруты tool-api: путь, имя функции, строка, есть ли признак защиты.

    Сканируется весь пакет `services/tool-api/app/**`, а не только `api/` и `pages/`:
    маршруты объявляются и в корневых модулях (`/health` в `main.py`), и пропуск такого
    файла давал ложный ноль в отчёте.
    """
    app_dir = root / "services" / "tool-api" / "app"
    files = _py_files(app_dir)
    routes: list[tuple[str, str, int, bool]] = []
    for path in files:
        for node in ast.walk(_parse(path)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            declared = False
            route_path: str | None = None
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                if decorator.func.attr not in ROUTE_METHODS:
                    continue
                declared = True
                if decorator.args and isinstance(decorator.args[0], ast.Constant) \
                        and isinstance(decorator.args[0].value, str):
                    route_path = decorator.args[0].value
            if not declared:
                continue
            signature = ast.unparse(node)
            protected = any(marker in signature for marker in PROTECTION_MARKERS)
            routes.append((route_path or f"<{_rel(path, root)}:{node.name}>",
                           node.name, node.lineno, protected))
    return routes


def measure_public_routes(root: pathlib.Path) -> dict[str, Any]:
    """Публичные маршруты без пометки в реестре и мёртвые записи реестра."""
    registry, without_reason = read_public_routes(root)
    routes = _declared_routes(root)
    unmarked: list[str] = []
    for route_path, name, line, protected in routes:
        if protected or route_path in registry:
            continue
        unmarked.append(f"{route_path} ({name}, строка {line}) — нет Depends/Header и нет "
                        f"пометки в {PUBLIC_ROUTES.as_posix()}")
    known = {route_path for route_path, _name, _line, _protected in routes}
    dead = [f"{route_path} — маршрута нет, запись мёртвая" for route_path in registry
            if route_path not in known]
    missing_reason = [f"{route_path} — в реестре без причины" for route_path in without_reason]
    issues = unmarked + dead + missing_reason
    return _metric("public_routes_unmarked", "Публичные маршруты без пометки", len(issues), 0,
                   tuple(issues), f"реестр: {PUBLIC_ROUTES.as_posix()}; "
                                  f"маршрутов всего {len(routes)}, публичных {len(registry)}")


def openapi_differences(root: pathlib.Path) -> list[str]:
    """Расхождения снимка OpenAPI с фактической схемой (scripts/openapi_snapshot.py)."""
    scripts_dir = root / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        import openapi_snapshot  # noqa: PLC0415 — импорт после правки sys.path
        return openapi_snapshot.diff_snapshots(openapi_snapshot.load_snapshot(),
                                               openapi_snapshot.snapshot_data())
    except Exception as exc:                                  # noqa: BLE001 — причина в отчёт
        raise MetricError(f"снимок OpenAPI не сверен: {exc}") from None


def measure_openapi(root: pathlib.Path) -> dict[str, Any]:
    try:
        differences = openapi_differences(root)
    except MetricError as exc:
        return {"id": "contract_differences", "title": "Расхождение снимка OpenAPI",
                "value": None, "threshold": 0, "status": "error", "details": [],
                "note": str(exc)}
    return _metric("contract_differences", "Расхождение снимка OpenAPI", len(differences), 0,
                   tuple(differences[:20]),
                   "снимок отстал от кода: обновлять осознанно, scripts/openapi_snapshot.py --write")


def _test_functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    found: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            found.append(node)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            found.extend(child for child in node.body
                         if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and child.name.startswith("test"))
    return found


def _has_check(node: ast.AST, source_lines: list[str]) -> bool:
    """Есть ли в тесте проверка: assert, pytest.raises/warns/fail или `raise AssertionError`.

    Тест на «не бросило» проверки не содержит по определению. Такой тест принимается,
    только если в нём есть комментарий, объясняющий, что он доказывает: строка с `#`
    внутри тела. Молчаливый `pass` — находка.
    """
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            return True
        if isinstance(child, ast.Call) and ast.unparse(child.func) in ASSERT_CALLS:
            return True
        if isinstance(child, ast.Raise) and child.exc is not None \
                and "AssertionError" in ast.unparse(child.exc):
            return True
    start = getattr(node, "lineno", 1) - 1
    end = getattr(node, "end_lineno", start + 1)
    text = "\n".join(source_lines[start:end])
    if any(marker in text.lower() for marker in NO_RAISE_MARKERS):
        return True
    return "#" in text                       # комментарий-объяснение вместо assert


def measure_tests_without_assert(root: pathlib.Path) -> dict[str, Any]:
    empty: list[str] = []
    for path in _py_files(root, "tests"):
        if path.name == "conftest.py":
            continue                      # test_db — фикстура, а не тест
        source_lines = path.read_text(encoding="utf-8").splitlines()
        for node in _test_functions(_parse(path)):
            if not _has_check(node, source_lines):
                empty.append(f"{_rel(path, root)}:{node.lineno} {node.name}")
    return _metric("tests_without_assert", "Тесты без assert", len(empty), 0, tuple(empty),
                   "тест без проверки не падает и ничего не доказывает")


def measure(root: pathlib.Path = PROJECT, thresholds: dict[str, int] | None = None) -> dict[str, Any]:
    """Все метрики одним словарём: то, что печатает --json и из чего собирается отчёт."""
    metrics: list[dict[str, Any]] = []
    metrics += measure_file_sizes(root)
    metrics.append(measure_duplicate_functions(root))
    metrics.append(measure_cross_service_imports(root))
    metrics.append(measure_getenv(root))
    metrics += measure_migrations(root)
    metrics.append(measure_public_routes(root))
    metrics.append(measure_openapi(root))
    metrics.append(measure_tests_without_assert(root))
    overrides = thresholds or {}
    for metric in metrics:
        if metric["id"] in overrides:
            metric["threshold"] = overrides[metric["id"]]
            if metric["value"] is not None:
                metric["status"] = "ok" if metric["value"] <= metric["threshold"] else "warn"
    return {
        "generated_at": datetime.now(SAMARA).isoformat(timespec="seconds"),
        "date": datetime.now(SAMARA).strftime("%Y-%m-%d"),
        "root": str(root),
        "commit": _commit(root),
        "metrics": metrics,
        "over_threshold": [metric["id"] for metric in metrics if metric["status"] != "ok"],
    }


def _commit(root: pathlib.Path) -> str:
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def render_table(data: dict[str, Any], previous: dict[str, int] | None = None) -> str:
    """Таблица «метрика → значение → порог → статус»; с --compare добавляется «было»."""
    head = "| Метрика | Значение | Порог | Статус |" if not previous else \
        "| Метрика | Значение | Было | Порог | Статус |"
    lines = [f"Дрейф архитектуры: {data['date']}, коммит {data['commit']}", "", head,
             "|---|---|---|---|" if not previous else "|---|---|---|---|---|"]
    for metric in data["metrics"]:
        value = "ошибка" if metric["value"] is None else metric["value"]
        title = f"{metric['title']} (`{metric['id']}`)"
        if previous is None:
            lines.append(f"| {title} | {value} | {metric['threshold']} | {metric['status']} |")
        else:
            before = previous.get(metric["id"], "—")
            lines.append(f"| {title} | {value} | {before} | {metric['threshold']} "
                         f"| {metric['status']} |")
    over = data["over_threshold"]
    lines += ["", f"За порогом: {len(over)} из {len(data['metrics'])} метрик."
              if over else "За порогом: ничего."]
    return "\n".join(lines)


def render_details(data: dict[str, Any], limit: int = 12) -> str:
    lines: list[str] = []
    for metric in data["metrics"]:
        if not metric["details"]:
            continue
        lines.append(f"\n{metric['title']}:")
        lines.extend(f"- {item}" for item in metric["details"][:limit])
        if len(metric["details"]) > limit:
            lines.append(f"- и ещё {len(metric['details']) - limit}")
    return "\n".join(lines)


REPORT_ROW = re.compile(r"^\|\s*[^|]*`([a-z0-9_]+)`[^|]*\|\s*([-\d]+|ошибка)\s*\|", re.MULTILINE)


def parse_report_values(text: str) -> dict[str, int]:
    """Значения метрик из прошлого отчёта: строка таблицы ищется по `id` в обратных кавычках."""
    values: dict[str, int] = {}
    for metric_id, raw in REPORT_ROW.findall(text):
        if raw.lstrip("-").isdigit():
            values[metric_id] = int(raw)
    return values


def render_report(data: dict[str, Any], previous: dict[str, int] | None = None,
                  proposals: str = "", details_limit: int = 12) -> str:
    """Текст отчёта по форме из скиллы architect-drift-review."""
    lines = [
        f"# Дрейф архитектуры: {data['date']}",
        "",
        "## Снимок",
        "",
        f"- коммит: {data['commit']}",
        f"- дата замера: {data['generated_at']}",
        "- команда: `env -u PYTHONPATH .venv/bin/python scripts/arch_drift.py`",
        "",
        "## Числа",
        "",
        "| Метрика | Сейчас | Прошлый замер | Порог | Статус |",
        "|---|---|---|---|---|",
    ]
    for metric in data["metrics"]:
        value = "ошибка" if metric["value"] is None else metric["value"]
        before = previous.get(metric["id"], "—") if previous is not None else "—"
        lines.append(f"| {metric['title']} (`{metric['id']}`) | {value} | {before} "
                     f"| {metric['threshold']} | {metric['status']} |")
    lines += ["", "## Что изменилось с прошлого раза", ""]
    if previous is None:
        lines.append("Прошлого замера нет: это первый отчёт, дельта появится со следующего.")
    else:
        deltas = [f"- {metric['id']}: {previous.get(metric['id'], '—')} -> "
                  f"{'ошибка' if metric['value'] is None else metric['value']}"
                  for metric in data["metrics"]
                  if previous.get(metric["id"]) != metric["value"]]
        lines.extend(deltas or ["Изменений нет."])
    lines += ["", "## Предложения", ""]
    lines.extend([proposals.strip() or "Предложения не заполнены: отчёт без них — только числа.",
                  ""])
    lines += ["## Что техлид не трогал и почему", "",
              "Код не правился: у роли нет задачи «сделать». Крупные файлы, дубли, миграции и",
              "маршруты названы числами; решение и работа — отдельные карты, мерж — "
              "`worker-review`.", ""]
    details = render_details(data, limit=details_limit)
    if details:
        lines += ["## Подробности", "", details.strip(), ""]
    return "\n".join(lines)


def next_doc_number(root: pathlib.Path) -> int:
    """Следующий свободный сквозной номер документа: максимум по всем группам docs/."""
    numbers = []
    docs = root / "docs"
    if docs.is_dir():
        for path in docs.rglob("*.md"):
            match = re.match(r"(\d+)-", path.name)
            if match:
                numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def write_report(root: pathlib.Path, text: str, date: str) -> pathlib.Path:
    """Пишет отчёт следующим свободным номером и пересобирает индекс документов."""
    reports = root / "docs" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    existing = sorted(reports.glob(f"*-arch-drift-{date}.md"))
    target = existing[0] if existing else reports / f"{next_doc_number(root):03d}-arch-drift-{date}.md"
    target.write_text(text, encoding="utf-8")
    _rebuild_index(root)
    return target


def _rebuild_index(root: pathlib.Path) -> None:
    scripts_dir = root / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import build_docs_index  # noqa: PLC0415 — импорт после правки sys.path
    index = root / "docs" / "README.md"
    if index.parent.is_dir():
        index.write_text(build_docs_index.build(), encoding="utf-8")


def parse_thresholds(items: list[str] | None) -> dict[str, int]:
    """`--threshold id=значение`; чужой id — код 2, чтобы опечатка не прошла молча."""
    thresholds: dict[str, int] = {}
    for item in items or []:
        metric_id, _, raw = item.partition("=")
        metric_id = metric_id.strip()
        if metric_id not in METRIC_IDS:
            raise ValueError(f"--threshold {item}: неизвестная метрика {metric_id}; "
                             f"известны: {', '.join(sorted(METRIC_IDS))}")
        if not raw.lstrip("-").isdigit():
            raise ValueError(f"--threshold {item}: ожидалось id=число")
        thresholds[metric_id] = int(raw)
    return thresholds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Замер дрейфа архитектуры (отчёт, не гейт)")
    parser.add_argument("--root", default=str(PROJECT), help="корень репозитория")
    parser.add_argument("--json", action="store_true", help="машинный вывод одним JSON")
    parser.add_argument("--compare", default=None,
                        help="предыдущий отчёт: путь к markdown для дельты")
    parser.add_argument("--write-report", action="store_true",
                        help="записать отчёт в docs/reports и пересобрать индекс")
    parser.add_argument("--proposals", default=None,
                        help="файл с текстом раздела «Предложения» для отчёта")
    parser.add_argument("--threshold", action="append", default=[],
                        help="понизить порог для сухого прогона: id=число (можно повторять)")
    args = parser.parse_args(argv)

    root = pathlib.Path(args.root)
    try:
        thresholds = parse_thresholds(args.threshold)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    # Приложение при импорте печатает предупреждения окружения: уводим их в stderr,
    # иначе `--json` перестаёт быть JSON (cron его разбирает).
    with contextlib.redirect_stdout(sys.stderr):
        data = measure(root, thresholds)
    previous = None
    if args.compare:
        previous = parse_report_values(pathlib.Path(args.compare).read_text(encoding="utf-8"))

    if args.write_report:
        proposals = pathlib.Path(args.proposals).read_text(encoding="utf-8") \
            if args.proposals else ""
        target = write_report(root, render_report(data, previous, proposals), data["date"])
        print(f"отчёт записан: {_rel(target, root)}")

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print(render_table(data, previous))
    details = render_details(data)
    if details:
        print(details)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
