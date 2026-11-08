"""Реестр инструментов агента: права, схемы и отсутствие пишущих (A-2, docs/development/108 §4).

Тест юнит-уровня: база и сеть не нужны. Проверяем ровно то, что должно держаться кодом,
а не просьбой в промпте:

  * у каждого инструмента есть `json_schema`, описание и флаг `read_only`;
  * закрытый список: состав реестра совпадает с ожидаемым, имена не дублируются;
  * пишущих инструментов нет — все объявлены `read_only`, обработчики ходят в базу
    только запросами `select` (разбор файла `tools.py`), пишущий инструмент, даже
    подсунутый, не выполняется;
  * право проверяется дважды: без `budget.read` инструмента нет в схеме для модели
    и `execute` отвечает `forbidden`, не доходя до обработчика;
  * `web_search` выключен по умолчанию и появляется только по флагу конфигурации;
  * неизвестный инструмент — честный отказ, а не тишина.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest
from app.agently import config, file_tools, registry, tools

EXPECTED_TOOLS = {
    "now", "read_schedule", "read_availability", "read_budget", "read_dues",
    "list_files", "read_file", "find_slots", "propose_event", "propose_note",
    "propose_move", "propose_budget_tx", "web_search",
}

ALL_CAPS = {"caps": ["*"]}
NO_CAPS = {"caps": []}
ROLE_CAPS = {"caps": ["budget.read.self", "schedule.availability.self"]}

WRITE_VERBS = re.compile(r"^\s*(insert|update|delete|truncate|drop|alter|grant)\b", re.IGNORECASE)
SQL_START = re.compile(r"^\s*(select|insert|update|delete|truncate|drop|alter|grant)\b",
                       re.IGNORECASE)


def _names(who: dict) -> list[str]:
    return [tool.name for tool in registry.available_tools(who)]


def _schema_names(who: dict) -> list[str]:
    return [item["function"]["name"] for item in registry.tool_schemas(who)]


# ------------------------------------------------------------------ состав и схемы
def test_состав_реестра_закрытый_и_без_дублей() -> None:
    names = [tool.name for tool in registry.all_tools()]

    assert set(names) == EXPECTED_TOOLS, "реестр изменился — правьте ожидание осознанно"
    assert len(names) == len(set(names)), "имена инструментов не должны повторяться"


def test_у_каждого_инструмента_есть_схема_и_флаг_только_чтения() -> None:
    for tool in registry.all_tools():
        assert tool.name and tool.name.isascii(), f"имя «{tool.name}» не ASCII"
        assert tool.description.strip(), f"у {tool.name} нет описания"
        assert tool.read_only is True, f"{tool.name} объявлен пишущим"
        assert callable(tool.handler), f"у {tool.name} нет обработчика"
        schema = tool.json_schema
        assert schema["type"] == "object", f"{tool.name}: схема не объект"
        assert isinstance(schema["properties"], dict)
        assert schema.get("additionalProperties") is False, f"{tool.name}: лишние аргументы"
        required = schema.get("required") or []
        assert set(required) <= set(schema["properties"]), f"{tool.name}: required вне properties"
        for prop, spec in schema["properties"].items():
            assert spec.get("type") or spec.get("enum"), f"{tool.name}.{prop}: нет типа"


def test_схема_отдаётся_модели_в_формате_функции() -> None:
    schemas = registry.tool_schemas(ALL_CAPS)

    assert schemas, "человеку с полными правами должен быть виден реестр"
    for item in schemas:
        assert item["type"] == "function"
        assert item["function"]["name"] in EXPECTED_TOOLS
        assert item["function"]["parameters"]["type"] == "object"


# ------------------------------------------------------------------ только чтение
def test_в_реестре_нет_пишущих_инструментов() -> None:
    assert [tool.name for tool in registry.all_tools() if not tool.read_only] == []


def test_пишущий_инструмент_не_выполняется_даже_если_попал_в_реестр(monkeypatch) -> None:
    """Страховка на будущее: объявленный пишущим инструмент не проходит `execute`."""
    called: list[str] = []

    def _handler(conn, who, args):
        called.append("вызван")
        return {"ok": True}

    evil = registry.Tool("evil_write", "подставной пишущий инструмент",
                         {"type": "object", "properties": {}}, (), False, _handler)
    monkeypatch.setitem(registry._BY_NAME, "evil_write", evil)

    result = registry.execute(None, NO_CAPS, "evil_write", {})

    assert result["ok"] is False and result["error"] == "write_tool_disabled"
    assert called == [], "обработчик пишущего инструмента не должен вызываться"


def _tools_module_source() -> str:
    return pathlib.Path(tools.__file__).read_text(encoding="utf-8")


def test_обработчики_ходят_в_базу_только_на_чтение() -> None:
    """Разбор `tools.py`: любой SQL-литерал в модуле — команда чтения.

    SQL у `read_schedule` собирается по частям (права решают, добавлять ли условие),
    поэтому проверяем не аргумент каждого вызова, а все строковые литералы модуля:
    записывающая команда обязана быть видна как литерал, иначе её просто нет.
    """
    tree = ast.parse(_tools_module_source())
    literals = [" ".join(node.value.split()) for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    statements = [item for item in literals if SQL_START.match(item)]

    assert len([item for item in statements if item.lower().startswith("select")]) >= 6, \
        "разбор не нашёл запросов чтения — проверка потеряла смысл"
    for sql in statements:
        assert not WRITE_VERBS.match(sql), f"пишущий запрос в инструменте: {sql[:80]}"


def test_в_tools_py_нет_слов_записи_и_функций_записи() -> None:
    """Критерий карты: `grep -rn "def .*write\\|insert\\|update" .../agent/tools.py` — пусто."""
    source = _tools_module_source()

    assert re.search(r"def .*write|insert|update", source, re.IGNORECASE) is None


def test_файлы_читаются_через_общий_модуль_и_проверку_прав() -> None:
    """Файловые инструменты живут в `file_tools.py` и ходят через `file_for_viewer`.

    `tools.py` — только чтение данных студии; файловое чтение вынесено отдельно,
    потому что там же лежит черновик перемещения (`agent_action`). Содержимое
    файла отдаётся лишь после проверки прав — это видно по модулю, а не по слову.
    """
    source = pathlib.Path(file_tools.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    used = {node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "files_store"}

    assert "file_for_viewer" in used, "чтение файла обязано проверять права"
    assert used <= {"search", "list_files", "folder_files", "file_for_viewer"}, \
        f"файловые инструменты зовут {sorted(used)}"


def test_в_file_tools_нет_удаления_и_обхода_очереди_подтверждений() -> None:
    """Черновик перемещения — единственная запись: удалений и правок нет."""
    source = pathlib.Path(file_tools.__file__).read_text(encoding="utf-8")
    words = re.findall(r"\b(delete|drop|truncate|alter)\b", source, re.IGNORECASE)
    assert words == [], f"файловые инструменты содержат запрещённую команду: {words}"
    assert "insert into agent_action" in source


# ------------------------------------------------------------------ права
def test_без_права_budget_read_инструмента_нет_в_схеме() -> None:
    assert "read_budget" not in _schema_names(ROLE_CAPS), "схема показала запрещённый инструмент"
    assert "read_budget" in _schema_names(ALL_CAPS)


def test_без_права_сервер_отказывает_до_обработчика() -> None:
    """`conn=None`: дойди выполнение до обработчика — был бы AttributeError, а не отказ."""
    result = registry.execute(None, ROLE_CAPS, "read_budget", {"period": "2026-11"})

    assert result["ok"] is False
    assert result["error"] == "forbidden"
    assert "read_budget" not in _names(ROLE_CAPS)


def test_взносы_доступны_с_правом_на_свои_взносы() -> None:
    assert "read_dues" in _names(ROLE_CAPS)
    assert "read_dues" not in _names(NO_CAPS), "без budget.read.self взносы не показываем"


def test_запись_предлагается_только_с_правом_записи() -> None:
    assert "propose_budget_tx" in _names(ALL_CAPS)
    assert "propose_budget_tx" not in _names(ROLE_CAPS)


def test_права_описаны_в_реестре_а_не_в_промпте() -> None:
    by_name = {tool.name: tool for tool in registry.all_tools()}

    assert by_name["read_budget"].requires == ("budget.read",)
    assert set(by_name["read_dues"].requires) == {"budget.read.self", "budget.read"}
    assert by_name["propose_budget_tx"].requires == ("budget.write",)
    assert by_name["propose_event"].requires == ("schedule.write",)
    assert by_name["propose_note"].requires == ("notes.write",)
    assert by_name["find_slots"].requires == ("schedule.slots",)
    for name in ("now", "read_schedule", "read_availability", "list_files",
                 "read_file", "propose_move"):
        assert by_name[name].requires == (), f"{name} должен быть доступен любому активному"


# ------------------------------------------------------------------ web_search
def test_web_search_выключен_по_умолчанию(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_WEB_SEARCH", raising=False)
    monkeypatch.delenv("AGENT_WEB_SEARCH_URL", raising=False)

    assert config.web_search_enabled() is False
    assert "web_search" not in _schema_names(ALL_CAPS)
    result = registry.execute(None, ALL_CAPS, "web_search", {"query": "что-нибудь"})
    assert result["ok"] is False and result["error"] == "forbidden"


def test_web_search_появляется_только_по_флагу(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_WEB_SEARCH", "1")
    monkeypatch.delenv("AGENT_WEB_SEARCH_URL", raising=False)

    assert "web_search" in _schema_names(ALL_CAPS)
    result = registry.execute(None, ALL_CAPS, "web_search", {"query": "что-нибудь"})
    assert result["ok"] is False and result["error"] == "web_search_unavailable"


# ------------------------------------------------------------------ прочее
def test_неизвестный_инструмент_это_отказ() -> None:
    result = registry.execute(None, ALL_CAPS, "rm_rf", {})

    assert result["ok"] is False and result["error"] == "unknown_tool"


class _RecordingConn:
    """Соединение-шпион: запоминает SQL и отдаёт заранее заданную строку."""

    def __init__(self, row: dict | None = None) -> None:
        self.sql: list[str] = []
        self._row = row

    def execute(self, sql: str, params=None) -> _RecordingConn:  # noqa: ANN001
        self.sql.append(" ".join(str(sql).split()))
        return self

    def fetchone(self) -> dict | None:
        return self._row

    def fetchall(self) -> list[dict]:
        return [self._row] if self._row else []


def test_предложение_записи_ничего_не_меняет_в_базе() -> None:
    """`propose_budget_tx` — намерение для карточки, а не запись (запись — карта A-3)."""
    conn = _RecordingConn({"name": "Ткани и материалы", "direction": "expense"})

    result = registry.execute(conn, {"caps": ["budget.write"]}, "propose_budget_tx",
                              {"tx_type": "expense", "amount": 3000, "category":
                               "Ткани и материалы", "purpose": "костюмы"})

    assert result["ok"] is True
    assert result["proposal"]["tool"] == "budget.tx"
    assert result["proposal"]["args"]["amount"] == 3000
    assert result["proposal"]["human_text"].startswith("Записать расход")
    assert conn.sql, "обработчик обязан проверить категорию по базе"
    for sql in conn.sql:
        assert not WRITE_VERBS.match(sql), f"предложение записи что-то записало: {sql[:80]}"


def test_предложение_с_чужой_категорией_отклоняется() -> None:
    conn = _RecordingConn({"name": "Ткани и материалы", "direction": "income"})

    result = registry.execute(conn, {"caps": ["budget.write"]}, "propose_budget_tx",
                              {"tx_type": "expense", "amount": 1000,
                               "category": "Ткани и материалы"})

    assert result["ok"] is False and result["error"] == "bad_category"
    assert result["allowed_categories"]
