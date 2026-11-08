#!/usr/bin/env python3
"""Инвентарь действий: что реально есть в коде и кто это зовёт (карта t_08b6ae7a).

Зачем. Аудит интерфейсов спрашивает «какие действия есть у человека», а документы
отвечают по памяти. Здесь ответ берётся из кода: маршруты tool-api — из фактической
схемы `app.openapi()` (не из списка в документе), права — из зависимостей маршрута
(`Depends(core.require(...))`, `core.current`, `core.webapp_actor`), потребители — из
строк путей в JS экранов, в роутерах Telegram и в скриптах, тесты — из вызовов
`/tools/...` и `/webapp/...` в `tests/`.

Правило проекта (docs/development/86): у Mini App нет своего API — экраны зовут те же
эндпоинты, что и бот. Поэтому «интерфейс → эндпоинт» и «эндпоинт → кто зовёт» — это
одна таблица, а не две.

Запуск:

    env -u PYTHONPATH .venv/bin/python scripts/audit_actions.py           # таблица в stdout
    env -u PYTHONPATH .venv/bin/python scripts/audit_actions.py --json    # машинный вид
    env -u PYTHONPATH .venv/bin/python scripts/audit_actions.py --check   # отчёт не разъехался
    env -u PYTHONPATH .venv/bin/python scripts/audit_actions.py --write   # переписать отчёт

`--check` (он же по умолчанию) падает, если:
  * набор маршрутов в коде разошёлся с `contracts/openapi.json` — контракт правится
    снимком (`scripts/openapi_snapshot.py --write`), а не этим скриптом;
  * таблица в `docs/reports/117-actions-inventory.md` отличается от того, что даёт код:
    отчёт собирается командой, а не руками.

Данные не нужны: работает по файлам и по собранному приложению FastAPI (импорт без
подключения к БД — так же, как `scripts/openapi_snapshot.py`).

Тест: `tests/unit/test_audit_actions.py` (INT-018).
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import inspect
import io
import json
import re
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeGuard

from _cli import ROOT, TOOL_API, add_tool_api_path

# --- что и где ищем -----------------------------------------------------------

REPORT = ROOT / "docs" / "reports" / "117-actions-inventory.md"
SNAPSHOT = ROOT / "contracts" / "openapi.json"
TOOL_API_APP = TOOL_API / "app"
GATEWAY_APP = ROOT / "services" / "tg-gateway" / "app"
PAGES = TOOL_API_APP / "pages"
STATIC = TOOL_API_APP / "static"
TESTS = ROOT / "tests"
SCRIPTS = ROOT / "scripts"

METHODS = ("get", "post", "put", "patch", "delete")

# Колонки таблиц отчёта — одни на генератор и на проверку.
BEGIN = "<!-- audit-actions:begin -->"
END = "<!-- audit-actions:end -->"

# Права, которые не про конкретную возможность, а про способ входа.
RIGHT_TEXT = {
    "webapp_actor": "сессия Mini App",
    "current": "любой активный",
    "actor": "сервисный токен",
    "service": "сервисный токен",
}


# --- общие модели --------------------------------------------------------------


@dataclass(frozen=True)
class Ref:
    """Ссылка на эндпоинт из потребителя: кто, где и какой путь."""

    path: str
    where: str
    owner: str
    kind: str          # webapp | chat | script | test
    call: bool = True  # вызов (api/fetch) или ссылка в разметке (href)


@dataclass
class Route:
    """Маршрут tool-api: путь, метод, права и всё, что нашли потребители."""

    method: str
    path: str
    handler: str
    where: str
    rights: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    in_schema: bool = True
    response: str = ""
    redirect: bool = False
    wildcard: tuple[str, ...] = ()
    body_caps: tuple[str, ...] = ()      # core._webapp_require(who, "...") в теле
    service_token: bool = False          # сверка Bearer с TOOL_API_TOKEN в теле
    note: str = ""
    interfaces: dict[str, set[str]] = field(default_factory=dict)
    by_template: set[str] = field(default_factory=set)   # группы, где адрес собран шаблоном
    tests: set[str] = field(default_factory=set)
    source: str = ""          # исходник обработчика: нужен экранам, в отчёт не идёт

    @property
    def right_text(self) -> str:
        if not self.rights and not self.service_token and not self.body_caps:
            return "—"
        named = [cap for cap in self.rights if cap not in RIGHT_TEXT]
        other = [RIGHT_TEXT[cap] for cap in self.rights if cap in RIGHT_TEXT]
        parts = named + other
        if self.service_token:
            parts.append(RIGHT_TEXT["service"])
        parts.extend(self.body_caps)
        return " + ".join(parts)


def split_path(path: str) -> list[str]:
    """Путь без пустых сегментов: сравнение идёт посегментно, а не строками."""
    return [part for part in path.strip("/").split("/") if part]


def is_param(segment: str) -> bool:
    """Динамический сегмент маршрута (`{participant_id}`) или дырка в литерале."""
    return segment.startswith("{") or segment.startswith("$")


def path_match(route_path: str, ref_path: str) -> int:
    """0 — не этот маршрут, 1 — точное совпадение, 2 — совпадение по шаблону.

    Сегменты сравниваются по отдельности: в маршруте `{id}`, а в коде экрана
    `{}` (склейка строкой) или `{folder_id}` (f-string) — это одно и то же,
    а вот `/webapp/admin` и `/webapp/admin/t/people` — разные адреса, и префикс
    сам по себе совпадением не считается.

    Шаблон — когда в коде подставляется переменная на месте постоянного сегмента
    (`f"/agent/actions/{action_id}/{verb}"`): такие адреса считаются вызовом,
    но помечаются, потому что по файлу не доказать, какая ветка выбрана.
    """
    route = split_path(route_path)
    ref = split_path(ref_path)
    if not ref or len(ref) != len(route):
        return 0
    exact = True
    for want, got in zip(route, ref, strict=True):
        if is_param(want):
            continue
        if is_param(got):
            exact = False
            continue
        if want != got:
            return 0
    return 1 if exact else 2


def path_matches(route_path: str, ref_path: str) -> bool:
    """Зовёт ли потребитель этот маршрут хоть как-то (точно или по шаблону)."""
    return path_match(route_path, ref_path) > 0


# --- tool-api: маршруты, права -------------------------------------------------


def _route_contexts(routes: list[Any]):
    """Все эффективные маршруты приложения.

    FastAPI 0.162 держит подключённые роутеры ленивыми обёртками `_IncludedRouter`,
    и `app.routes` содержит пять маршрутов вместо двухсот (docs/development/26 §4a,
    грабли из отчёта 97). Эффективные контексты отдают настоящие маршруты с префиксами.
    """
    for route in routes:
        getter = getattr(route, "effective_route_contexts", None)
        if getter is not None:
            yield from getter()
        else:
            yield route


def _closure_capabilities(call: Any) -> tuple[str, ...]:
    """Возможность, зашитая в замыкание `core.require("budget.write")`."""
    cells = getattr(call, "__closure__", None) or ()
    values = []
    for cell in cells:
        try:
            value = cell.cell_contents
        except ValueError:
            continue
        if isinstance(value, str):
            values.append(value)
    return tuple(values)


def _source_of(func: Any) -> tuple[str, int, str] | None:
    """Файл, строка и исходник обработчика — без падений на встроенных функциях."""
    try:
        filename = inspect.getsourcefile(func)
        lines, first = inspect.getsourcelines(func)
    except (OSError, TypeError):
        return None
    return filename or "", first, "".join(lines)


def _wildcard_capabilities(source: str) -> tuple[str, ...]:
    """Права, которые проверяются руками через `"*" in caps` в теле обработчика.

    `core.require(...)` держит правило в одном месте; проверка `"*" in caps or
    "schedule.read.all" in caps` — второй путь того же правила, и его нужно видеть
    списком, а не искать глазами (критерий приёмки карты 4).
    """
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:                        # исходник не разобрался — не повод падать
        return ()
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.BoolOp) and not isinstance(node, ast.Compare):
            continue
        constants = {n.value for n in ast.walk(node)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        if "*" not in constants:
            continue
        found.update(value for value in constants if value != "*" and "." in value)
    return tuple(sorted(found))


def _call_uses_webapp_require(node: ast.AST) -> TypeGuard[ast.Call]:
    """`core._webapp_require(who, "…")` или `_webapp_require(...)`."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr == "_webapp_require"
    return isinstance(func, ast.Name) and func.id == "_webapp_require"


def _body_capabilities(source: str) -> tuple[str, ...]:
    """Права, проверенные в теле обработчика через `core._webapp_require(who, "…")`.

    В Mini App обработчик получает участника зависимостью `core.webapp_actor` (сессия),
    а конкретную возможность проверяет уже сам: `_webapp_require` — это `require()`
    для прямого вызова (core.py:720-733). Без разбора тела колонка «Право» показывает
    только «сессия Mini App», и по таблице нельзя понять, что `/webapp/files/list`
    админский, а `/webapp/tx/save` требует `budget.write`.
    """
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:                        # исходник не разобрался — не повод падать
        return ()
    found: set[str] = set()
    for node in ast.walk(tree):
        if not _call_uses_webapp_require(node):
            continue
        args = [*node.args, *(keyword.value for keyword in node.keywords)]
        for arg in args[1:]:                  # первый аргумент — who
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.add(arg.value)
    return tuple(sorted(found))


def _has_service_token(source: str) -> bool:
    """Проверка сервисного Bearer-токена в теле: `hmac.compare_digest(…, f"Bearer {TOOL_API_TOKEN}")`.

    Такие маршруты (`POST /admin/bootstrap`, `GET /tools/participants/search`) зовёт
    другой сервис, а не человек и не сессия Mini App. Для таблицы это проверка на входе,
    а не «—»: иначе админский эндпоинт выглядит открытым.
    """
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "compare_digest":
            continue
        dumped = ast.dump(node)
        if "Bearer" in dumped and "TOOL_API_TOKEN" in dumped:
            return True
    return False


def _import_app() -> tuple[Any, Any]:
    """Собранное приложение и `core` — без служебного вывода сервиса в stdout.

    На импорте settings честно предупреждает «TOOL_API_TOKEN не задан, используется
    dev-token»: человеку это полезно, а `--json` от такого вывода перестаёт быть JSON.
    """
    add_tool_api_path()
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        from app import core  # noqa: PLC0415 — импорт после правки sys.path
        from app.main import app  # noqa: PLC0415
    return app, core


def api_routes() -> list[Route]:
    """Маршруты из собранного приложения: путь, метод, права, файл и строка."""
    app, core = _import_app()

    named = {core.actor: "actor", core.current: "current", core.webapp_actor: "webapp_actor"}
    rows: list[Route] = []
    for context in _route_contexts(app.routes):
        original = getattr(context, "original_route", None) or context
        endpoint = getattr(original, "endpoint", None)
        if endpoint is None:
            continue
        path = getattr(context, "path_format", None) or getattr(original, "path", "")
        source = _source_of(endpoint)
        # `/docs`, `/openapi.json`, `/redoc` регистрирует сам FastAPI: это не наши
        # действия, и в контракт они не попадают (`include_in_schema=False`).
        if source is None or not Path(source[0]).resolve().is_relative_to(ROOT / "services"):
            continue
        where = f"{_short(source[0])}:{source[1]}"
        dependant = getattr(original, "dependant", None)
        rights: list[str] = []
        for dependency in (dependant.dependencies if dependant else []):
            call = dependency.call
            if call in named:
                rights.append(named[call])
            else:
                rights.extend(_closure_capabilities(call))
        capabilities = sorted(cap for cap in rights if cap not in RIGHT_TEXT)
        response_class = getattr(original, "response_class", None)
        response = getattr(response_class, "__name__", "")
        methods = sorted(method.upper() for method in (getattr(original, "methods", None) or [])
                         if method != "HEAD")
        for method in methods:
            handler_source = source[2] if source else ""
            body_caps = _body_capabilities(handler_source) if source else ()
            rows.append(Route(
                method=method,
                path=path,
                handler=getattr(endpoint, "__name__", "—"),
                where=where,
                rights=tuple(rights),
                capabilities=tuple(sorted(set(capabilities) | set(body_caps))),
                in_schema=bool(getattr(original, "include_in_schema", True)),
                response=response,
                redirect=response == "RedirectResponse",
                wildcard=_wildcard_capabilities(handler_source) if source else (),
                body_caps=body_caps,
                service_token=_has_service_token(handler_source) if source else False,
                source=handler_source,
            ))
    rows.sort(key=lambda row: (row.path, row.method))
    return rows


def openapi_ops() -> set[tuple[str, str]]:
    """Маршруты из снимка контракта: `(МЕТОД, путь)`."""
    schema = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    return {(method.upper(), path)
            for path, item in schema.get("paths", {}).items()
            for method in item if method in METHODS}


# --- потребители: экраны, бот, скрипты, тесты ---------------------------------


def _short(path: str) -> str:
    """Путь относительно корня проекта — так отчёт переносим между клонами."""
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return path


def _expr_path(node: ast.AST) -> str | None:
    """Путь из выражения: литерал, f-строка или склейка строк.

    Склейка даёт `{}` на месте переменной: `"/webapp/folder/" + str(fid)` —
    это `"/webapp/folder/{}"`, и он сравнивается с маршрутом посегментно.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append("{}")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _expr_path(node.left), _expr_path(node.right)
        left = left if left is not None else "{}"
        right = right if right is not None else "{}"
        return left + right
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str":
        return "{}"
    return None


def _looks_like_path(value: str) -> bool:
    """Похоже ли значение на адрес API: начинается с `/`, без пробелов и схемы.

    Префикс не фильтруем: у сервиса он не один (`/tools/...`, `/webapp/...`,
    `/files/...`, `/admin/...`), и фильтр по списку молча терял целые домены.
    Обрубок с хвостовым слэшем (`/webapp/` в проверке `indexOf`) адресом не считается.
    """
    return (value.startswith("/") and not value.startswith("//")
            and " " not in value and not value.endswith("/"))


def py_paths(path: Path) -> list[tuple[int, str, bool]]:
    """Все литеральные адреса API в python-файле: (строка, путь, вызов).

    Вёрстка экранов Mini App — это python-константы с целой HTML-страницей внутри,
    поэтому адреса ищутся и внутри больших строк: JS-вызовы `api("/webapp/…")`
    лежат там же, где разметка, и отдельными узлами AST не видны.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:                        # битый файл не должен ронять инвентарь
        return []
    found: list[tuple[int, str, bool]] = []
    seen: set[tuple[int, str, bool]] = set()

    def add(lineno: int, value: str, call: bool) -> None:
        value = value.split("?", 1)[0]          # запрос — не часть адреса
        item = (lineno, value, call)
        if value and item not in seen:
            seen.add(item)
            found.append(item)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp, ast.Call)):
            continue
        value = _expr_path(node)
        if not value:
            continue
        if _looks_like_path(value):
            add(getattr(node, "lineno", 0), value, True)
        elif "\n" in value or "<" in value:
            # Разметка — одна большая строка: адреса достаём построчно.
            for line in value.splitlines():
                for found_path, call in js_line_paths(line):
                    add(getattr(node, "lineno", 0), found_path, call)
    return sorted(found)


JS_STRING = re.compile(r"""(["'`])(/[^"'`\n]*)\1""")


JS_CALL = re.compile(r"(?:api|fetch|request|sendBeacon|apiJson)\s*\(\s*$")


def js_line_paths(line: str) -> list[tuple[str, bool]]:
    """Адреса одной строки JS со склейками: `"/webapp/folder/" + id + "/order"`.

    Склейка восстанавливается как `"/webapp/folder/{}/order"` — по литералам строки
    и знаку `+` между ними; переход через `)`, `;` или `(` — уже другой вызов,
    и литералы двух вызовов не склеиваются. Второе значение — это вызов `api()`
    или `fetch()`, а не ссылка в разметке и не проверка префикса.
    """
    parts: list[tuple[str, int]] = []          # (собранный путь, начало первого литерала)
    current = ""
    start = 0
    last_end = 0
    for match in JS_STRING.finditer(line):
        literal = re.sub(r"\$\{[^}]*\}", "{}", match.group(2))
        if not current:
            if literal.startswith("/"):
                current = literal
                start = match.start()
                last_end = match.end()
            continue
        gap = line[last_end:match.start()]
        if "+" in gap and not any(char in gap for char in "();"):
            current += "{}" + literal
            last_end = match.end()
        else:
            parts.append((current, start))
            current = literal if literal.startswith("/") else ""
            start = match.start()
            last_end = match.end()
    if current:
        parts.append((current, start))
    result = []
    for value, begin in parts:
        value = value.split("?", 1)[0]
        if not _looks_like_path(value):
            continue
        # `api("/webapp/x")` — вызов; `href="/webapp/x"` или `indexOf("/webapp/x")` — нет.
        call = bool(JS_CALL.search(line[:begin]))
        result.append((value, call))
    return result


def js_paths(path: Path) -> list[tuple[int, str, bool]]:
    """Литеральные адреса в JS: `api("/webapp/x")`, `fetch('/webapp/y')`, шаблоны."""
    text = path.read_text(encoding="utf-8")
    found: list[tuple[int, str, bool]] = []
    seen: set[tuple[int, str, bool]] = set()
    for number, line in enumerate(text.splitlines(), 1):
        for value, call in js_line_paths(line):
            item = (number, value, call)
            if item not in seen:
                seen.add(item)
                found.append(item)
    return sorted(found)


def _owner_of(tree: ast.AST, lineno: int) -> str:
    """Имя функции, внутри которой стоит строка: так ссылка получает владельца."""
    best, best_line = "", 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = getattr(node, "lineno", 0)
        end = getattr(node, "end_lineno", start)
        if start <= lineno <= end and start >= best_line:
            best, best_line = node.name, start
    return best


def python_refs(path: Path, owner: str, kind: str) -> list[Ref]:
    """Ссылки из python-файла с владельцем по функции и строкой."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    refs = []
    for lineno, value, call in py_paths(path):
        where = f"{_short(str(path))}:{lineno}"
        # У экранов владелец — модуль, а не функция: адрес стоит в вёрстке константы,
        # и «кто зовёт» здесь значит «какой экран», а не внутренний помощник.
        owner_name = path.stem if kind == "webapp" else (_owner_of(tree, lineno) or owner)
        refs.append(Ref(value, where, owner_name, kind, call))
    return refs


def mini_app_refs() -> list[Ref]:
    """Что зовут экраны Mini App: JS в `static/` и в модулях страниц."""
    refs: list[Ref] = []
    for path in sorted(STATIC.glob("*.js")):
        for lineno, value, call in js_paths(path):
            refs.append(Ref(value, f"{_short(str(path))}:{lineno}", path.name, "webapp", call))
    for path in sorted(PAGES.glob("*.py")):
        refs.extend(python_refs(path, path.stem, "webapp"))
    return refs


def gateway_refs() -> list[Ref]:
    """Что зовёт бот: пути tool-api в роутерах и хелперах гейтвея."""
    refs: list[Ref] = []
    for path in sorted(GATEWAY_APP.rglob("*.py")):
        refs.extend(python_refs(path, path.stem, "chat"))
    return refs


def script_refs() -> list[Ref]:
    """Что зовут обслуживающие скрипты: для них эндпоинт не «мёртвый», но и не интерфейс.

    Сам инвентарь (`scripts/audit_actions.py`) пропускается: в нём адреса лежат
    регулярками и сравнениями, и он находил «потребителя» сам у себя.
    """
    refs: list[Ref] = []
    for path in sorted(SCRIPTS.rglob("*.py")):
        if path.name == Path(__file__).name:
            continue
        refs.extend(python_refs(path, path.stem, "script"))
    return refs


def test_refs() -> list[Ref]:
    """Вызовы API в тестах: имя файла — это и есть покрытие маршрута.

    `conftest.py` — не тест, а подготовка окружения: путь в нём ничего не проверяет.
    Сам тест инвентаря тоже пропускается: в нём адреса лежат примерами для проверки
    правил сравнения, и он бы «покрыл» половину API сам себе.
    """
    refs: list[Ref] = []
    for path in sorted(TESTS.rglob("*.py")):
        if path.name in {"conftest.py", "test_audit_actions.py"}:
            continue
        refs.extend(python_refs(path, path.stem, "test"))
    return refs


def _test_texts() -> dict[str, str]:
    """Тексты тестов одним словарём: по ним ищутся команды, кнопки и адреса."""
    texts: dict[str, str] = {}
    for path in sorted(TESTS.rglob("*.py")):
        if path.name == "test_audit_actions.py":
            continue
        try:
            texts[path.name] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return texts


# --- экраны Mini App ------------------------------------------------------------


def _page_aliases(routes_module: Path) -> dict[str, str]:
    """Псевдоним модуля страниц → имя модуля (`misc` → `misc`)."""
    tree = ast.parse(routes_module.read_text(encoding="utf-8"))
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("pages"):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
    return aliases


def _page_constant(source: str) -> tuple[str, str]:
    """Модуль и константа, которые экран отдаёт: `misc.SLOTS_PAGE` → (`misc`, `SLOTS_PAGE`)."""
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return "", ""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        value = node.value
        if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
            return value.value.id, value.attr
        if isinstance(value, ast.Name):
            return "", value.id
    return "", ""


def page_constants() -> dict[str, dict[str, Any]]:
    """Что определено в модулях страниц: диапазоны строк, ссылки и импорты.

    Экран собран из частей: `HOME_PAGE = layout.page("СВОИ", _BODY_HOME, …,
    scripts=_SCRIPT_HOME)`, а `misc.PROFILE_PAGE` — вообще реэкспорт из
    `misc_profile.py`. Поэтому мало знать имя константы: нужно раскрыть её
    содержимое по ссылкам и по импортам, иначе адреса API экрана теряются.
    """
    modules: dict[str, dict[str, Any]] = {}
    for path in sorted(PAGES.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        spans: dict[str, list[tuple[int, int]]] = {}
        names: dict[str, set[str]] = {}
        imports: dict[str, tuple[str, str]] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if not isinstance(target, ast.Name):
                        continue
                    spans.setdefault(target.id, []).append(
                        (node.lineno, node.end_lineno or node.lineno))
                    names.setdefault(target.id, set()).update(
                        child.id for child in ast.walk(node.value)
                        if isinstance(child, ast.Name))
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.pages"):
                for alias in node.names:
                    imports[alias.asname or alias.name] = (
                        (node.module or "").rsplit(".", 1)[-1], alias.name)
        modules[path.stem] = {"spans": spans, "names": names, "imports": imports}
    return modules


def _resolve_constant(modules: dict[str, dict[str, Any]], module: str, name: str,
                      seen: set[tuple[str, str]] | None = None) -> set[tuple[str, int, int]]:
    """Все части экрана: константа, её вложенные константы и реэкспорты.

    Возвращает тройки `(модуль, строка начала, строка конца)` — по ним потом
    отбираются адреса API, стоящие внутри вёрстки этого экрана.
    """
    seen = set() if seen is None else seen
    if (module, name) in seen:                 # цикл в ссылках не должен зациклить разбор
        return set()
    seen.add((module, name))
    data = modules.get(module)
    if data is None:
        return set()
    if name not in data["spans"]:
        target = data["imports"].get(name)
        return _resolve_constant(modules, target[0], target[1], seen) if target else set()
    found = {(module, start, end) for start, end in data["spans"][name]}
    for child in data["names"].get(name, set()):
        if child != name:
            found |= _resolve_constant(modules, module, child, seen)
    return found


def _known(path: str, routes: list[Route]) -> bool:
    """Есть ли такой маршрут: вёрстка содержит и `/`, и `/api/`, и опечатки."""
    return any(path_match(route.path, path) for route in routes)


def _admin_screen_pages() -> dict[str, tuple[str, str]]:
    """Разделы «Управления» из реестра `app/webapp.py:SECTIONS`: адрес → экран.

    Маршруты разделов собираются в цикле (`router.add_api_route(_section.url, …)`),
    поэтому имя константы из исходника обработчика не читается — берём его из
    реестра, который этот цикл и обходит.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        add_tool_api_path()
        from app.webapp import page_sections  # noqa: PLC0415

    return {section.url: ("admin", section.page) for section in page_sections() if section.page}


def screen_endpoints(routes: list[Route], refs: list[Ref]) -> list[dict[str, Any]]:
    """Экраны Mini App → какие эндпоинты они зовут.

    Экран — HTML-маршрут `/webapp/*`; вёрстка лежит в модуле `app/pages/*.py`
    константой, и адреса API внутри этой константы — то, что экран зовёт.
    Ссылки общего слоя `static/webapp-shell.js` (вход, маяк, навигация) есть
    у всех экранов и вынесены отдельной строкой.

    В списке остаются только настоящие маршруты: `/`, `/api/` и прочие строки
    из вёрстки — не действия сервиса.
    """
    aliases = _page_aliases(PAGES / "routes.py")
    modules = page_constants()
    admin_pages = _admin_screen_pages()
    screens: list[dict[str, Any]] = []
    for route in routes:
        if not route.path.startswith("/webapp") or route.method != "GET":
            continue
        if not (route.response == "HTMLResponse" or route.redirect):
            continue
        alias, constant = _page_constant(route.source)
        module = aliases.get(alias, alias)
        if constant not in modules.get(module, {}).get("spans", {}) and route.path in admin_pages:
            alias, constant = admin_pages[route.path]
            module = alias
        parts = _resolve_constant(modules, module, constant) if module and constant else set()
        endpoints = {ref.path for ref in refs
                     if ref.kind == "webapp" and ref.call and _known(ref.path, routes)
                     and any(ref.owner == owner and start <= _ref_line(ref) <= end
                             for owner, start, end in parts)}
        screens.append({
            "path": route.path,
            "where": route.where,
            "handler": route.handler,
            "constant": f"{module}.{constant}" if module and constant else constant or "—",
            "redirect": route.redirect,
            "endpoints": sorted(endpoints),
            "tests": sorted(route.tests),
        })
    return screens


def _ref_line(ref: Ref) -> int:
    """Строка ссылки из `файл:строка`."""
    try:
        return int(ref.where.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return 0


# --- telegram-гейтвей -----------------------------------------------------------


def _filter_text(node: ast.AST) -> str:
    """Фильтр декоратора в человеческом виде: `Command("busy")`, `F.data == "…"`."""
    try:
        return ast.unparse(node)
    except Exception:                          # noqa: BLE001 — unparse не должен ронять инвентарь
        return "?"


def _decorator_kinds(decorator: ast.AST) -> tuple[str, str]:
    """Вид обработчика и то, на что он срабатывает: команда, кнопка, текст, состояние."""
    if not isinstance(decorator, ast.Call):
        return "прочее", ""
    func = decorator.func
    if not isinstance(func, ast.Attribute):
        return "прочее", ""
    kind = func.attr
    args = ", ".join(_filter_text(arg) for arg in decorator.args)
    if kind == "callback_query":
        return "кнопка", args
    if kind == "message":
        return "сообщение", args
    if kind == "errors":
        return "ошибка", ""
    return kind, args


CAPABILITY = re.compile(r"^[a-z][a-z_]*(\.[a-z_.]+)+$")


def _handler_capabilities(source: str) -> tuple[str, ...]:
    """Возможности, которые обработчик проверяет сам (`core.has(caps, "…")`, `in caps`)."""
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name in {"has", "caps_of"}:
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        if CAPABILITY.match(arg.value):
                            found.add(arg.value)
        if isinstance(node, ast.Compare):
            for side in [node.left, *node.comparators]:
                if isinstance(side, ast.Constant) and isinstance(side.value, str):
                    if CAPABILITY.match(side.value):
                        found.add(side.value)
    return tuple(sorted(found))


TRIGGER_KEY = re.compile(r"'([^']+)'")


def _trigger_patterns(trigger: str) -> list[re.Pattern[str]]:
    """Шаблоны «этот тест зовёт действие»: токен в кавычках, а не подстрока.

    Команда `Command('files')` встречается в тестах как `Command("files")` или
    `Message(text="/files")`, поэтому ищем и вызов фильтра, и строку целиком.
    Слова вроде `files=[changed]` (аргумент чужой функции) или `_files` под шаблон
    не попадают — иначе посторонний тест засчитывается как покрытие `cmd_files`.
    Для колбэков ключ — полный префикс данных (`panel:root`, `fl:`), как он приходит
    в `callback_data`; для текстовых веток — литерал в кавычках.
    """
    patterns: list[re.Pattern[str]] = []
    is_command = "Command" in trigger
    is_callback = "F.data" in trigger
    for value in TRIGGER_KEY.findall(trigger):
        if len(value) <= 2:
            continue
        escaped = re.escape(value)
        if is_command:
            bare = value.lstrip("/")
            patterns.append(re.compile(rf"Command\(\s*['\"]{re.escape(bare)}['\"]"))
            # `/files` в тесте — команда; `/files/upload` — маршрут API, не она.
            patterns.append(re.compile(rf"['\"]/{re.escape(bare)}(?=['\"\s])"))
        elif is_callback:
            patterns.append(re.compile(rf"['\"]{escaped}"))
        else:
            patterns.append(re.compile(rf"['\"]{escaped}['\"]"))
    return patterns


def _test_files_for(trigger: str, texts: dict[str, str]) -> list[str]:
    """Тесты, где действие зовётся токеном: файл как единица покрытия."""
    patterns = _trigger_patterns(trigger)
    if not patterns:
        return []
    return sorted(name for name, text in texts.items()
                  if any(pattern.search(text) for pattern in patterns))


def chat_actions(refs: list[Ref], texts: dict[str, str]) -> list[dict[str, Any]]:
    """Команды, кнопки и текстовые ветки Telegram-гейтвея."""
    actions: list[dict[str, Any]] = []
    for path in sorted((GATEWAY_APP / "routers").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        relative = _short(str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                kind, trigger = _decorator_kinds(decorator)
                if kind not in {"кнопка", "сообщение", "ошибка"}:
                    continue
                last = node.end_lineno or node.lineno
                endpoints = {ref.path for ref in refs
                             if ref.where.startswith(f"{relative}:")
                             and node.lineno <= _ref_line(ref) <= last}
                actions.append({
                    "kind": kind,
                    "trigger": trigger,
                    "name": node.name,
                    "where": f"{relative}:{node.lineno}",
                    "caps": _handler_capabilities(ast.get_source_segment(text, node) or ""),
                    "free_text": "F.text" in trigger and "Command" not in trigger
                                 and "CommandStart" not in trigger,
                    "endpoints": sorted(endpoints),
                    "tests": _test_files_for(trigger, texts),
                })
    actions.sort(key=lambda item: (item["where"], item["name"]))
    return actions


def free_text_rules() -> list[dict[str, str]]:
    """Ветки ответа на свободный текст: правила `routing.py` и их роль.

    Роль берётся из двух списков рядом (`QUESTION_RULES` — вопрос, `DATA_INPUT_RULES` —
    ввод данных), а не из порядка в кортеже: порядок применения RULES — отдельная
    величина, и по нему нельзя судить, вопрос это или ввод.
    """
    path = GATEWAY_APP / "routing.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    groups: dict[str, list[str]] = {}
    rules: list[dict[str, str]] = []
    patterns: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
            if isinstance(target, ast.Name) and isinstance(value, ast.Constant) \
                    and isinstance(value.value, str):
                patterns[target.id] = value.value
        if isinstance(node, ast.Assign):
            name = getattr(node.targets[0], "id", "")
        elif isinstance(node, ast.AnnAssign):          # `RULES: tuple[...] = (...)` — аннотация
            name = getattr(node.target, "id", "")
        else:
            continue
        if name not in {"RULES", "QUESTION_RULES", "DATA_INPUT_RULES"}:
            continue
        if not isinstance(node.value, ast.Tuple):
            continue
        if name in {"QUESTION_RULES", "DATA_INPUT_RULES"}:
            groups[name] = [getattr(item, "value", "") for item in node.value.elts]
            continue
        for item in node.value.elts:
            if isinstance(item, ast.Tuple) and len(item.elts) == 2:
                regex_node = item.elts[1]
                if isinstance(regex_node, ast.Name):   # шаблон лежит в константе рядом
                    pattern = patterns.get(regex_node.id, "")
                else:
                    pattern = str(getattr(regex_node, "value", ""))
                rules.append({"name": str(getattr(item.elts[0], "value", "")),
                              "pattern": pattern[:120],
                              "where": f"{_short(str(path))}:{item.lineno}", "role": ""})
    for rule in rules:
        if rule["name"] in groups.get("QUESTION_RULES", []):
            rule["role"] = "вопрос"
        elif rule["name"] in groups.get("DATA_INPUT_RULES", []):
            rule["role"] = "ввод данных"
        else:
            rule["role"] = "правило"
    return rules


# --- инструменты агента ---------------------------------------------------------


def agent_tools(texts: dict[str, str]) -> list[dict[str, Any]]:
    """Инструменты агента из закрытого реестра: права, обработчик, только чтение."""
    add_tool_api_path()
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        from app.agently import registry  # noqa: PLC0415

    rows = []
    for tool in registry.all_tools():
        source = _source_of(tool.handler)
        rows.append({
            "name": tool.name,
            "requires": list(tool.requires),
            "read_only": tool.read_only,
            "description": tool.description.split(".")[0][:160],
            "where": f"{_short(source[0])}:{source[1]}" if source else "—",
            "tests": sorted(name for name, text in texts.items()
                            if f'"{tool.name}"' in text or f"'{tool.name}'" in text),
        })
    return rows


# --- сборка инвентаря -----------------------------------------------------------


def build() -> dict[str, Any]:
    """Полный инвентарь: маршруты, экраны, чат, агент, пробелы."""
    routes = api_routes()
    expected = openapi_ops()
    actual = {(route.method, route.path) for route in routes if route.in_schema}
    webapp = mini_app_refs()
    chat = gateway_refs()
    scripts = script_refs()
    tests = test_refs()
    test_texts = _test_texts()
    actions = chat_actions(chat, test_texts)
    screens = screen_endpoints(routes, webapp)
    tools = agent_tools(test_texts)

    def attach(route: Route) -> None:
        for group, refs in (("Mini App", webapp), ("чат", chat), ("скрипты", scripts)):
            scores = [(path_match(route.path, ref.path), ref) for ref in refs]
            hits = {ref.owner for score, ref in scores if score}
            if hits:
                route.interfaces[group] = hits
            if any(score == 2 for score, _ in scores):
                route.by_template.add(group)
        route.tests = {Path(ref.where.split(":")[0]).name
                       for ref in tests if path_matches(route.path, ref.path)}

    for route in routes:
        attach(route)
        if route.redirect:
            route.note = "редирект 301 (старый адрес)"
        elif route.response == "HTMLResponse":
            route.note = "экран Mini App: HTML без данных"
        elif not route.in_schema:
            route.note = "нет в контракте OpenAPI"
        if route.path.startswith("/webapp/admin/t/"):
            route.note = "раздел из реестра app/webapp.py:SECTIONS"
        if route.wildcard:
            extra = "проверка права в теле обработчика (через «*»)"
            route.note = f"{route.note}; {extra}" if route.note else extra
        if route.body_caps:
            extra = ("право проверяется в теле обработчика, а не в зависимости: "
                     + ", ".join(route.body_caps))
            route.note = f"{route.note}; {extra}" if route.note else extra
        if route.service_token:
            extra = "проверка на входе — сервисный Bearer-токен, а не сессия человека"
            route.note = f"{route.note}; {extra}" if route.note else extra
        if not route.interfaces:
            route.note = f"{route.note}; никто не зовёт".strip("; ")

    no_right = [route for route in routes
                if not route.rights and not route.service_token and not route.body_caps]
    wildcard = [route for route in routes if route.wildcard]
    service_token = [route for route in routes if route.service_token]
    orphans = [route for route in routes if not route.interfaces]
    return {
        "routes": routes,
        "missing_in_code": sorted(expected - actual),
        "missing_in_snapshot": sorted(actual - expected),
        "screens": screens,
        "chat": actions,
        "free_text": free_text_rules(),
        "agent_tools": tools,
        "no_right": no_right,
        "wildcard": wildcard,
        "body_caps": [route for route in routes if route.body_caps],
        "service_token": service_token,
        "orphans": orphans,
        "counts": {
            "openapi_ops": len(expected),
            "code_ops": len(actual),
            "all_routes": len(routes),
            "no_right": len(no_right),
            "wildcards": len(wildcard),
            "body_caps": sum(1 for route in routes if route.body_caps),
            "service_token": len(service_token),
            "orphans": len(orphans),
            "chat_actions": len(actions),
            "screens": len(screens),
            "agent_tools": len(tools),
            "routes_with_tests": sum(1 for route in routes if route.tests),
        },
    }


# --- отчёт ----------------------------------------------------------------------


def _cell(value: str) -> str:
    """Значение для markdown-ячейки: `|` ломает таблицу, переводы строк тоже."""
    return value.replace("|", "\\|").replace("\n", " ").strip() or "—"


def _interfaces(route: Route) -> str:
    """Колонка «интерфейсы»: кто зовёт маршрут, по-русски и коротко."""
    if not route.interfaces:
        return "—"
    parts = []
    for group in ("Mini App", "чат", "скрипты"):
        if group in route.interfaces:
            owners = sorted(route.interfaces[group])
            mark = " (по шаблону)" if group in route.by_template else ""
            parts.append(f"{group}: {', '.join(owners)}{mark}")
    return "; ".join(parts)


def _tests(route: Route) -> str:
    """Колонка «тест»: файлы тестов, а не их число."""
    if not route.tests:
        return "нет"
    names = sorted(route.tests)
    if len(names) > 3:
        return ", ".join(names[:3]) + f" (+{len(names) - 3})"
    return ", ".join(names)


def _route_table(routes: list[Route], *, header: str) -> list[str]:
    lines = [header, "|---|---|---|---|---|---|"]
    for route in routes:
        right = route.right_text
        if route.wildcard:
            right = f"{right} (в теле — {', '.join(route.wildcard)})"
        lines.append(
            f"| {route.method} `{route.path}` | `{route.where}` | {_cell(_interfaces(route))} "
            f"| {_cell(right)} | {_cell(_tests(route))} | {_cell(route.note)} |")
    return lines


def render(data: dict[str, Any]) -> list[str]:
    """Тело отчёта между маркерами: только сгенерированные таблицы."""
    routes: list[Route] = data["routes"]
    counts = data["counts"]
    lines: list[str] = []
    lines.append("## 1. Сводка")
    lines.append("")
    lines.append("| Показатель | Значение |")
    lines.append("|---|---|")
    lines.append(f"| Маршрутов (методов) в `contracts/openapi.json` | {counts['openapi_ops']} |")
    lines.append(f"| Маршрутов в коде, которые есть в контракте | {counts['code_ops']} |")
    lines.append(f"| Всего маршрутов в коде (с редиректами и ресурсами) | {counts['all_routes']} |")
    lines.append(f"| Из них с тестом (по вызову пути в `tests/`) | {counts['routes_with_tests']} |")
    lines.append(f"| Действий без права (ни возможности, ни сессии) | {counts['no_right']} |")
    lines.append(f"| Действий с правом, проверенным в теле через `core._webapp_require(…)` | {counts['body_caps']} |")
    lines.append(f"| Действий с правом, проверенным в теле через «*» в caps | {counts['wildcards']} |")
    lines.append(f"| Маршрутов с сервисным Bearer-токеном на входе | {counts['service_token']} |")
    lines.append(f"| Эндпоинтов, которые не зовёт ни один интерфейс | {counts['orphans']} |")
    lines.append(f"| Экранов Mini App | {counts['screens']} |")
    lines.append(f"| Обработчиков чата (команды, кнопки, тексты) | {counts['chat_actions']} |")
    lines.append(f"| Инструментов агента | {counts['agent_tools']} |")
    lines.append("")

    lines.append("## 2. tool-api: маршруты и права")
    lines.append("")
    lines.append("Права проверяются тремя путями. Первый — зависимость маршрута "
                 "`Depends(core.require(\"…\"))` (конкретная возможность); вход в сессию "
                 "объявляют `core.current` (любой активный участник) и `core.webapp_actor` "
                 "(сессия Mini App, подпись `initData`). Второй — `core._webapp_require(who, \"…\")` "
                 "в теле обработчика: та же проверка, что `require()`, но вызванная напрямую "
                 "(28 маршрутов Mini App, §7a). Третий — `\"*\" in caps` в теле обработчика "
                 "(2 маршрута, §7c). Отдельно от прав, на входе, стоит проверка служебного "
                 "Bearer-токена: `hmac.compare_digest(…, f\"Bearer {TOOL_API_TOKEN}\")` у маршрутов "
                 "для другого сервиса (2, §7b). «—» — ни одного из этих путей нет.")
    lines.append("")
    lines.extend(_route_table(routes, header=(
        "| Действие | Точка входа | Интерфейсы | Право | Тест | Примечание |")))
    lines.append("")

    lines.append("## 3. Экраны Mini App и что они зовут")
    lines.append("")
    lines.append("«Зовёт эндпоинты» — вызовы `api()`/`fetch()` внутри вёрстки экрана; "
                 "навигационные ссылки сюда не входят (они видны в колонке «Интерфейсы» "
                 "у самого маршрута). Экран собран из частей (`HOME_PAGE` = тело + скрипты, "
                 "`misc.*` — реэкспорт), их содержимое раскрыто по ссылкам. "
                 "`tests/webapp/server.py` — стенд браузерных тестов, а не тест.")
    lines.append("")
    lines.append("| Экран | Точка входа | Код страницы | Зовёт эндпоинты | Тест |")
    lines.append("|---|---|---|---|---|")
    for screen in data["screens"]:
        endpoints = ", ".join(f"`{path}`" for path in screen["endpoints"]) or "—"
        tests = ", ".join(sorted({Path(name).name for name in screen["tests"]})) or "нет"
        lines.append(f"| `{screen['path']}` | `{screen['where']}` | `{screen['constant']}` "
                     f"| {_cell(endpoints)} | {_cell(tests)} |")
    lines.append("")

    lines.append("## 4. Telegram-гейтвей: команды, кнопки, текстовые ветки")
    lines.append("")
    lines.append("Право в чате — то, что проверяет сам обработчик (`core.has(caps, …)`, `in caps`). "
                 "«—» значит, что собственной проверки нет: окончательное решение принимает "
                 "tool-api по праву эндпоинта из колонки «Зовёт эндпоинты». Вызовы через общие "
                 "помощники (`send_invite`, `panel_person` и подобные) видны в строке помощника, "
                 "а не вызывающего.")
    lines.append("")
    lines.append("| Действие | Триггер | Точка входа | Право в чате | Тест | Зовёт эндпоинты |")
    lines.append("|---|---|---|---|---|---|")
    for action in data["chat"]:
        caps = ", ".join(action["caps"]) or "—"
        endpoints = ", ".join(f"`{path}`" for path in action["endpoints"]
                              if _known(path, routes)) or "—"
        trigger = action["trigger"] or "—"
        tests = ", ".join(action["tests"][:2]) + (f" (+{len(action['tests']) - 2})"
                                                 if len(action["tests"]) > 2 else "")
        lines.append(f"| {action['kind']} `{action['name']}` | {_cell(trigger)} "
                     f"| `{action['where']}` | {_cell(caps)} | {_cell(tests or 'нет')} "
                     f"| {_cell(endpoints)} |")
    lines.append("")
    lines.append("Ветки ответа на свободный текст (`app/routing.py`, порядок применения):")
    lines.append("")
    lines.append("| Правило | Роль | Что ловит | Точка входа |")
    lines.append("|---|---|---|---|")
    for rule in data["free_text"]:
        lines.append(f"| `{rule['name']}` | {rule['role']} | {_cell(rule['pattern'])} "
                     f"| `{rule['where']}` |")
    lines.append("")

    lines.append("## 5. Инструменты агента")
    lines.append("")
    lines.append("| Инструмент | Право | Только чтение | Обработчик | Тест | Что делает |")
    lines.append("|---|---|---|---|---|---|")
    for tool in data["agent_tools"]:
        require = ", ".join(tool["requires"]) or "любому активному"
        read_only = "да" if tool["read_only"] else "НЕТ"
        tests = ", ".join(tool["tests"][:2]) + (f" (+{len(tool['tests']) - 2})"
                                               if len(tool["tests"]) > 2 else "")
        lines.append(f"| `{tool['name']}` | {_cell(require)} | {read_only} "
                     f"| `{tool['where']}` | {_cell(tests or 'нет')} | {_cell(tool['description'])} |")
    lines.append("")

    lines.append("## 6. Действия без права")
    lines.append("")
    lines.append("Проверки на входе нет: ни возможности, ни сессии Mini App, ни сервисного токена. "
                 "Это не всегда дефект: экраны Mini App — публичные "
                 "константы (данные проверяются в API), SDK и CSS отдаются всем, регистрация "
                 "и вход по определению идут до сессии. Дефект — когда без права меняются данные.")
    lines.append("")
    lines.extend(_route_table(data["no_right"], header=(
        "| Действие | Точка входа | Интерфейсы | Право | Тест | Примечание |")))
    lines.append("")

    lines.append("## 7. Права, проверенные в теле обработчика")
    lines.append("")
    lines.append("Три пути проверки права — не считая зависимости `core.require(…)` в §2: "
                 "`core._webapp_require(who, \"…\")` у Mini App (7a), служебный Bearer-токен "
                 "на входе (7b) и ручная проверка `\"*\" in caps` (7c).")
    lines.append("")
    lines.append("### 7a. `core._webapp_require(who, \"…\")` — Mini App")
    lines.append("")
    lines.append("Обработчик получает участника зависимостью `core.webapp_actor` (сессия), а "
                 "возможность проверяет сам: `_webapp_require` — это `require()` для прямого "
                 "вызова (`app/core.py:720-733`). В колонке «Право» §2 такая возможность "
                 "приписана к сессии.")
    lines.append("")
    lines.append("| Действие | Точка входа | Право | Сессия | Тест |")
    lines.append("|---|---|---|---|---|")
    for route in data["body_caps"]:
        session = ", ".join(RIGHT_TEXT[cap] for cap in route.rights if cap in RIGHT_TEXT) or "—"
        lines.append(f"| {route.method} `{route.path}` | `{route.where}` "
                     f"| {_cell(', '.join(route.body_caps))} | {_cell(session)} "
                     f"| {_cell(_tests(route))} |")
    if not data["body_caps"]:
        lines.append("| — | — | — | — | — |")
    lines.append("")
    lines.append("### 7b. Служебный Bearer-токен")
    lines.append("")
    lines.append("Маршрут зовёт другой сервис, а не человек: в теле сверяется "
                 "`Authorization: Bearer {TOOL_API_TOKEN}` (`hmac.compare_digest`). "
                 "У `POST /admin/bootstrap` дополнительно сверяется `ADMIN_CHAT_ID`.")
    lines.append("")
    lines.append("| Действие | Точка входа | Тест |")
    lines.append("|---|---|---|")
    for route in data["service_token"]:
        lines.append(f"| {route.method} `{route.path}` | `{route.where}` | {_cell(_tests(route))} |")
    if not data["service_token"]:
        lines.append("| — | — | — |")
    lines.append("")
    lines.append("### 7c. Ручная проверка `\"*\" in caps`")
    lines.append("")
    lines.append("Возможность проверяется не `core.require(...)`, а руками в теле обработчика: "
                 "`\"*\" in caps or \"<право>\" in caps`. Одно правило — два пути проверки.")
    lines.append("")
    lines.append("| Действие | Точка входа | Право | В теле проверяет |")
    lines.append("|---|---|---|---|")
    for route in data["wildcard"]:
        lines.append(f"| {route.method} `{route.path}` | `{route.where}` | {_cell(route.right_text)} "
                     f"| {_cell(', '.join(route.wildcard))} |")
    if not data["wildcard"]:
        lines.append("| — | — | — | — |")
    lines.append("")

    lines.append("## 8. Эндпоинты, которые не зовёт ни один интерфейс")
    lines.append("")
    lines.append("Кандидаты на удаление: ни экран, ни бот, ни скрипт не обращаются по этому пути. "
                 "Тест упомянут отдельно — если он есть, путь живёт ради теста.")
    lines.append("")
    lines.append("| Действие | Точка входа | Право | Тест |")
    lines.append("|---|---|---|---|")
    for route in data["orphans"]:
        lines.append(f"| {route.method} `{route.path}` | `{route.where}` | {_cell(route.right_text)} "
                     f"| {_cell(_tests(route))} |")
    if not data["orphans"]:
        lines.append("| — | — | — | — |")
    lines.append("")

    lines.append("## 9. Самопроверка")
    lines.append("")
    lines.append(f"* маршрутов в контракте: {counts['openapi_ops']}, в коде: {counts['code_ops']}")
    lines.append(f"* нет в коде, но есть в контракте: {len(data['missing_in_code'])}")
    lines.append(f"* есть в коде, но нет в контракте: {len(data['missing_in_snapshot'])}")
    for method, path in data["missing_in_code"]:
        lines.append(f"  * `{method} {path}` — в контракте, в коде не найден")
    for method, path in data["missing_in_snapshot"]:
        lines.append(f"  * `{method} {path}` — в коде, в контракте нет")
    return lines


def report_text(data: dict[str, Any]) -> str:
    """Полный текст отчёта 117: шапка + сгенерированный блок."""
    head = [
        "# 117. Инвентарь действий: что есть в коде",
        "",
        "Карта: `t_08b6ae7a` (аудит 2/4). Документ собран скриптом "
        "`scripts/audit_actions.py` по файлам репозитория; таблицы между маркерами "
        "переписываются командой и руками не правятся — `--check` падает при расхождении.",
        "",
        "Источники: маршруты — `app.openapi()` собранного tool-api, сверенные с "
        "`contracts/openapi.json`; права — зависимости маршрутов и тела обработчиков "
        "(`core._webapp_require`, сверка Bearer-токена); потребители — литералы "
        "путей в `app/pages/*.py`, `app/static/*.js`, `services/tg-gateway/**`; тесты — "
        "вызовы путей в `tests/**`; инструменты агента — закрытый реестр "
        "`app/agently/registry.py`.",
        "",
        "Правило проекта (docs/development/86): у Mini App нет своего API, экраны зовут те же "
        "эндпоинты, что и бот. Поэтому «действие» — это маршрут tool-api, а интерфейсы "
        "(чат, экран, скрипт) — оболочки над ним.",
        "",
        "## Что видно из таблиц",
        "",
        "Наблюдения на 04.11.2026; их подтверждают таблицы ниже. При изменении кода таблицы "
        "обновляются командой, а этот раздел остаётся записью среза.",
        "",
        "* **Одни эндпоинты — несколько оболочек.** Экраны Mini App и бот зовут общий API: "
        "у домена занятости есть пары `/tools/availability/*` и `/webapp/availability/*`, "
        "и потребители расходятся по половинам пары. Половина без потребителя — в §8.",
        "* **Действия-сироты с тестами.** Отмена и перенос события "
        "(`/tools/events/cancel`, `/tools/events/move`) объявлены в API и покрыты тестами, "
        "но ни экрана, ни кнопки у них нет: тест проверяет то, чем никто не пользуется.",
        "* **В домене файлов два слоя.** Чат ходит в `/files/…` и `/folders/…`, экран — "
        "в `/webapp/files/*` и `/webapp/folder/*`. Часть маршрутов прежнего слоя "
        "(`/folders/{id}/…`, `/files/{id}/access`) не зовёт никто.",
        "* **Права проверяются тремя путями.** `core.require(…)` в зависимости маршрута — "
        "правило в одном месте; `core._webapp_require(who, \"…\")` в теле 28 обработчиков "
        "Mini App — та же проверка, вызванная напрямую; `\"*\" in caps or \"<право>\" in caps` "
        "в теле — третий путь. Отдельно на входе стоит сверка служебного Bearer-токена "
        "(2 маршрута для другого сервиса). Списки — §7.",
        "* **Публичные экраны — правило, а не дефект.** HTML страниц, SDK и CSS отдаются без "
        "сессии, данные проверяются в API; регистрация и вход по определению идут до неё. "
        "Дефект — действие без права, которое меняет данные.",
        "",
        "Воспроизведение:",
        "",
        "```",
        "env -u PYTHONPATH .venv/bin/python scripts/audit_actions.py --json   # машинный вид",
        "env -u PYTHONPATH .venv/bin/python scripts/audit_actions.py --check  # отчёт = код",
        "env -u PYTHONPATH .venv/bin/python scripts/audit_actions.py --write  # переписать",
        "```",
        "",
        BEGIN,
    ]
    return "\n".join([*head, *render(data), END, ""])


def merge_report(text: str, existing: Path = REPORT) -> str:
    """Новый текст отчёта: шапка и таблицы берутся из кода, хвост после маркера — из файла.

    Шапка тоже генерируется: иначе правки легенды в скрипте не доезжали бы до отчёта,
    а `--check` этого не замечал (сравнивался только блок между маркерами).
    """
    if not existing.exists():
        return text
    old = existing.read_text(encoding="utf-8")
    if BEGIN not in old or END not in old:
        return text
    after = old.split(END, 1)[1]
    return text.split(END, 1)[0] + END + after


def check(text: str, data: dict[str, Any]) -> list[str]:
    """Расхождения, из-за которых проверка падает."""
    errors = []
    if data["missing_in_code"]:
        errors.append("в контракте есть маршруты, которых нет в коде: "
                      + ", ".join(f"{m} {p}" for m, p in data["missing_in_code"]))
    if data["missing_in_snapshot"]:
        errors.append("в коде есть маршруты, которых нет в контракте (обновить снимок "
                      "`scripts/openapi_snapshot.py --write`): "
                      + ", ".join(f"{m} {p}" for m, p in data["missing_in_snapshot"]))
    if REPORT.exists():
        current = REPORT.read_text(encoding="utf-8")
        if BEGIN not in current or END not in current:
            errors.append(f"в {_short(str(REPORT))} нет маркеров {BEGIN} / {END}")
        elif current != merge_report(text):
            errors.append(f"таблица в {_short(str(REPORT))} разошлась с кодом: "
                          "переписать командой `scripts/audit_actions.py --write`")
    else:
        errors.append(f"нет отчёта {_short(str(REPORT))}: создать `--write`")
    return errors


# --- CLI ------------------------------------------------------------------------


def as_json(data: dict[str, Any]) -> dict[str, Any]:
    """Машинный вид: без классов, только словари и списки."""
    def route_view(route: Route) -> dict[str, Any]:
        return {
            "method": route.method,
            "path": route.path,
            "handler": route.handler,
            "where": route.where,
            "rights": list(route.rights),
            "capabilities": list(route.capabilities),
            "right_text": route.right_text,
            "in_schema": route.in_schema,
            "response": route.response,
            "redirect": route.redirect,
            "wildcard": list(route.wildcard),
            "body_caps": list(route.body_caps),
            "service_token": route.service_token,
            "note": route.note,
            "interfaces": {key: sorted(value) for key, value in route.interfaces.items()},
            "by_template": sorted(route.by_template),
            "tests": sorted(route.tests),
        }

    return {
        "counts": data["counts"],
        "routes": [route_view(route) for route in data["routes"]],
        "screens": data["screens"],
        "chat": data["chat"],
        "free_text": data["free_text"],
        "agent_tools": data["agent_tools"],
        "no_right": [route_view(route) for route in data["no_right"]],
        "wildcard": [route_view(route) for route in data["wildcard"]],
        "body_caps": [route_view(route) for route in data["body_caps"]],
        "service_token": [route_view(route) for route in data["service_token"]],
        "orphans": [route_view(route) for route in data["orphans"]],
        "missing_in_code": data["missing_in_code"],
        "missing_in_snapshot": data["missing_in_snapshot"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Инвентарь действий: маршруты, экраны, чат, инструменты агента",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="сверить отчёт и контракт с кодом (по умолчанию)")
    mode.add_argument("--write", action="store_true", help="переписать docs/reports/117")
    mode.add_argument("--json", action="store_true", help="машинный вид в stdout")
    args = parser.parse_args(argv)

    data = build()
    text = report_text(data)
    if args.json:
        print(json.dumps(as_json(data), ensure_ascii=False, indent=2))
        return 0
    if args.write:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(merge_report(text), encoding="utf-8")
        print(f"отчёт записан: {_short(str(REPORT))}; маршрутов {data['counts']['code_ops']}, "
              f"без права {data['counts']['no_right']}, без интерфейса "
              f"{data['counts']['orphans']}")
        return 0

    errors = check(text, data)
    if errors:
        for error in errors:
            print(f"ОШИБКА: {error}", file=sys.stderr)
        return 1
    print("отчёт совпадает с кодом: маршрутов "
          f"{data['counts']['code_ops']} (контракт {data['counts']['openapi_ops']}), "
          f"без права {data['counts']['no_right']}, без интерфейса {data['counts']['orphans']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
