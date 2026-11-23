"""Граница записи Google: ни одного пишущего вызова в боевой календарь и живые листы.

Обоснование: docs/development/186 §2.2 (реестр), §5.6, §12.1, AC-1/AC-3/AC-4;
дизайн 185 D10.

Две проверки, а не одна:

1. **Рантайм** — `sheet_binding.open_sheet` отказывает на ключах чтения
   (`write_target_refused`/`write_target_unset`) до любого обращения к Google;
2. **Обход AST по `services/`** — в коде нет пишущего вызова (`insert/update/delete/
   patch/move/append/batchUpdate/clear`) с идентификатором боевого календаря или живого
   листа: `calendarId`/`spreadsheetId` обязан быть выражением из
   `google_targets.resolve(...)` (или сессией, открытой им). Самопроверка: синтетический
   модуль с `calendarId=CALENDAR_ID_READ` обязан ронять проверку — иначе проверка
   проверяет саму себя, а не код.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from app import core, google_targets, sheet_binding
from app.errors import ApiError

APP_DIR = pathlib.Path(__file__).resolve().parents[2] / "services" / "tool-api" / "app"

#: Методы Google API, которые меняют объект. Чтение (`get`, `list`, `batchGet`) сюда
#: не входит: оно разрешено на всех объектах.
WRITE_METHODS = {"insert", "update", "delete", "patch", "move", "append",
                 "batchUpdate", "clear", "copy"}

#: Идентификаторы объектов чтения: их нельзя передавать в пишущие вызовы.
READ_IDS = {
    "calendarId": {"CALENDAR_ID_READ", "CALENDAR_ID_HUMAN", "calendar_id_read"},
    "spreadsheetId": {"SCHEDULE_HUMAN_SPREADSHEET_ID", "FINANCE_SOURCE_ID",
                      "BUDGET_LIVE_SPREADSHEET_ID", "schedule_human_spreadsheet_id",
                      "finance_source_id"},
}

#: Длинные литеральные идентификаторы Google — их в коде быть не должно вовсе.
GOOGLE_ID_MIN_LENGTH = 20


def _write_calls(tree: ast.AST):
    """Все пишущие вызовы Google API в дереве разбора."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in WRITE_METHODS:
            continue
        yield node


def _resolved_names(function: ast.AST) -> set[str]:
    """Имена внутри функции, которым присвоен результат `resolve(...)`/`_object_id(...)`.

    Именно так адрес объекта записи попадает в вызов: `calendar_id =
    google_targets.resolve("calendar.draft")`, затем `insert(calendarId=calendar_id)`.
    """
    names: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if called in {"resolve", "_object_id", "_target_id"}:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
    return names


def _is_allowed(node: ast.AST, function: ast.AST) -> bool:
    """Законен ли идентификатор объекта в пишущем вызове."""
    if isinstance(node, ast.Call):
        func = node.func
        called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        return called in {"resolve", "_object_id"}
    if isinstance(node, ast.Name):
        return node.id in _resolved_names(function)
    if isinstance(node, ast.Attribute):
        # `session.spreadsheet_id` — сессия открыта протоколом привязки, адрес уже сверен
        return True
    return False


def find_violations(source: str, filename: str = "<синтетика>") -> list[str]:
    """Нарушения границы записи в исходнике. Пустой список — граница держится."""
    tree = ast.parse(source, filename=filename)
    problems: list[str] = []
    for node in _write_calls(tree):
        functions = [item for item in ast.walk(tree)
                     if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and node in list(ast.walk(item))]
        function = functions[0] if functions else tree
        for keyword in node.keywords:
            if keyword.arg not in {"calendarId", "spreadsheetId"}:
                continue
            text = ast.unparse(keyword.value)
            forbidden = READ_IDS.get(keyword.arg, set())
            if any(name in text for name in forbidden):
                problems.append(f"{filename}:{node.lineno}: {keyword.arg}={text} — "
                                "объект чтения в пишущем вызове")
            elif isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                value = keyword.value.value
                if len(value) >= GOOGLE_ID_MIN_LENGTH and value.startswith("1"):
                    problems.append(f"{filename}:{node.lineno}: {keyword.arg}={text} — "
                                    "литеральный идентификатор Google")
                elif value.endswith("@group.calendar.google.com"):
                    problems.append(f"{filename}:{node.lineno}: {keyword.arg}={text} — "
                                    "литеральный идентификатор Google")
            elif not _is_allowed(keyword.value, function):
                problems.append(f"{filename}:{node.lineno}: {keyword.arg}={text} — "
                                "адрес не из реестра google_targets")
    return problems


def test_в_коде_сервиса_нет_записи_в_объекты_чтения():
    """Обход `services/tool-api/app`: пишущие вызовы идут только через реестр."""
    violations: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        violations.extend(find_violations(path.read_text(encoding="utf-8"),
                                          filename=str(path.relative_to(APP_DIR))))
    assert not violations, "граница записи нарушена:\n" + "\n".join(violations)


def test_самопроверка_ловит_запись_в_боевой_календарь():
    """Синтетический модуль с запрещённой записью обязан ронять проверку (AC-4)."""
    source = (
        "def bad():\n"
        "    core._calendar_write().events().update(\n"
        "        calendarId=core.CALENDAR_ID_READ, eventId='x', body={})\n"
    )
    problems = find_violations(source, "synthetic.py")
    assert problems and "CALENDAR_ID_READ" in problems[0]


def test_самопроверка_ловит_литеральный_идентификатор():
    source = ("def bad():\n"
              "    core._sheets().spreadsheets().values().append(\n"
              "        spreadsheetId='1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789', body={})\n")
    assert find_violations(source, "synthetic.py")


def test_самопроверка_пропускает_адрес_из_реестра():
    source = ('def good():\n'
              '    calendar_id = google_targets.resolve("calendar.draft")\n'
              '    core._calendar_write().events().delete(calendarId=calendar_id, eventId="x")\n')
    assert find_violations(source, "synthetic.py") == []


class FakeConn:
    """Соединение без БД: отдаёт пустые ответы, запоминает запросы."""

    def __init__(self, binding=None) -> None:
        self.binding = binding
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params or ()))
        return self

    def fetchone(self):
        return self.binding


class BrokenService:
    """Любое обращение к Google — провал теста: граница должна отказать раньше."""

    def spreadsheets(self):
        raise AssertionError("до Google дело дойти не должно")

    def events(self):
        raise AssertionError("до Google дело дойти не должно")


def test_живой_лист_не_пишется(monkeypatch):
    """`schedule.human` — источник чтения: попытка записи отказывает 403."""
    monkeypatch.setattr(core, "SCHEDULE_HUMAN_SPREADSHEET_ID", "live-sheet")
    with pytest.raises(ApiError) as caught:
        sheet_binding.open_sheet("schedule.human", conn=FakeConn(), service=BrokenService())
    assert caught.value.status_code == 403
    assert caught.value.code == "write_target_refused"


def test_боевой_календарь_не_пишется(monkeypatch):
    """`calendar.read` — боевой календарь: ключа записи у него не существует."""
    monkeypatch.setattr(core, "CALENDAR_ID_READ", "live-calendar")
    with pytest.raises(ApiError) as caught:
        sheet_binding.open_sheet("calendar.read", conn=FakeConn(), service=BrokenService())
    assert caught.value.status_code == 403
    assert caught.value.code == "write_target_refused"


def test_ненастроенный_объект_отказывает_503(monkeypatch):
    """Ключ есть в реестре, id пуст — `write_target_unset`, Google не вызывается."""
    monkeypatch.setattr(core, "SCHEDULE_TEST_SPREADSHEET_ID", "")
    with pytest.raises(ApiError) as caught:
        sheet_binding.open_sheet("schedule.test", conn=FakeConn(), service=BrokenService())
    assert caught.value.status_code == 503
    assert caught.value.code == "write_target_unset"


def test_без_строки_привязки_запись_остановлена(monkeypatch):
    """Объект настроен, но `sheet_binding` пуст — `binding_missing`, а не догадка."""
    monkeypatch.setattr(core, "BUDGET_SPREADSHEET_ID", "bot-budget")
    with pytest.raises(ApiError) as caught:
        sheet_binding.open_sheet("budget.transactions", conn=FakeConn(),
                                 service=BrokenService())
    assert caught.value.status_code == 409
    assert caught.value.code == "binding_missing"


def test_список_объектов_записи_не_меняется_режимом(monkeypatch):
    """Дизайн D7: набор объектов записи одинаков в `transitional` и `target`."""
    for mode in google_targets.MODES:
        monkeypatch.setattr(google_targets, "google_mode", lambda _conn=None, m=mode: m)
        for key in google_targets.WRITE_TARGETS:
            assert google_targets.allowed_for_write(key)
        assert not google_targets.allowed_for_write("calendar.read")
