"""Реестр инструментов агента: закрытый список, только чтение (A-2, docs/development/108 §4).

Модель не выбирает, что вызвать, из всего API: она видит ровно те инструменты, которые
разрешены конкретному человеку. Схема собирается на сервере по правам, а `execute`
проверяет те же права ещё раз — «спрятать в промпте» недостаточно: инструмент без права
не попадает в схему для модели **и** отвергается на сервере (`forbidden`).

Правила модуля:
  * у каждого инструмента есть `json_schema`, `requires`, `read_only` и `handler`;
  * ни один обработчик не меняет данные: реестр только читает (`tools.py`);
  * `propose_budget_tx` ничего не записывает, он возвращает намерение для карточки
    подтверждения — запись делает обычный детерминированный эндпоинт по кнопке (A-3);
  * неизвестный или запрещённый инструмент — честный отказ, а не тишина;
  * `web_search` есть в реестре только при включённом флаге конфигурации
    (по умолчанию выключен: внешние ответы не подменяют данные студии);
  * объявить пишущий инструмент (`read_only=False`) можно, но `execute` его не выполнит —
    в этом реестре таких нет, а тест это проверяет.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .. import core
from ..external import TOOL_ERRORS
from . import config, file_tools, tools
from .tools import MAX_RESULT_CHARS, WEEKDAYS_RU, has_cap, proposal_text

__all__ = [
    "MAX_RESULT_CHARS", "WEEKDAYS_RU", "Tool", "TOOLS", "all_tools", "available_tools",
    "execute", "has_cap", "proposal_text", "tool_schemas",
]


@dataclass(frozen=True)
class Tool:
    """Инструмент агента: схема для модели, права и обработчик.

    `requires` — список «любое из»: пустой означает «доступен любому активному».
    `read_only` — обещание, что вызов не меняет данные; тест реестра проверяет,
    что ни один инструмент не объявлен пишущим и что обработчики делают только `select`.
    """

    name: str
    description: str
    json_schema: dict
    requires: tuple[str, ...] = ()
    read_only: bool = True
    handler: Callable[[Any, dict, dict], dict] | None = None


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties,
            "required": required or [], "additionalProperties": False}


TOOLS: tuple[Tool, ...] = (
    Tool("now", "Текущая дата, время и день недели по Самаре. Единственный источник даты.",
         _schema({}), (), True, tools.run_now),
    Tool("read_schedule", "Ближайшие события: репетиции, показы, занятия. Свои, "
         "а с правом schedule.read.all — все.",
         _schema({"days": {"type": "integer", "minimum": 1, "maximum": 30,
                           "description": "на сколько дней вперёд, по умолчанию 7"}}),
         (), True, tools.run_schedule),
    Tool("read_availability", "Занятость людей по часам: кто свободен и когда. "
         "Без schedule.read.all — только свои отметки.",
         _schema({"day": {"type": "string",
                          "description": "день YYYY-MM-DD, по умолчанию сегодня"}}),
         (), True, tools.run_availability),
    Tool("read_budget", "Бюджет студии за месяц: сколько денег пришло (доход), сколько "
         "потратили (расход), баланс и последние операции. Период — поле period_human.",
         _schema({"period": {"type": "string", "description": "месяц YYYY-MM"}}),
         ("budget.read",), True, tools.run_budget),
    Tool("read_dues", "Взносы: свои начисления, тариф, оплата и долг; с правом budget.read — "
         "сводка по студии. Вопросы «тариф», «сколько платить», «оплачено ли», «долг» — сюда.",
         _schema({"period": {"type": "string", "description": "месяц YYYY-MM"}}),
         ("budget.read.self", "budget.read"), True, tools.run_dues),
    Tool("list_files", "Список своих файлов: имя, размер, папка; поиск по слову в имени, "
         "тегах и тексте. Содержимое отдаёт read_file.",
         _schema({"query": {"type": "string",
                            "description": "слово для поиска по имени и тегам"},
                  "folder_path": {"type": "string",
                                  "description": "путь папки, например «Спектакли/Пурга/музыка»"}}),
         (), True, file_tools.run_list_files),
    Tool("read_file", "Текст файла по номеру из list_files: имя и цитата. Если нужного нет "
         "в тексте — так и скажи «в файле этого нет», не додумывай.",
         _schema({"file_id": {"type": "integer",
                              "description": "номер файла из list_files"}}, ["file_id"]),
         (), True, file_tools.run_read_file),
    Tool("find_slots", "Окна, когда нужные люди свободны: репетиция, тренинг, показ, встреча, "
         "аренда зала. Считает код по занятости; отвечай по полю text, а не по памяти.",
         _schema({"participants": {"type": "array", "items": {"type": "string"},
                                   "description": "имена или номера людей; пусто — вся труппа"},
                  "policy": {"type": "string",
                             "enum": ["rehearsal", "training", "show", "meeting",
                                      "rental", "rental_staff"],
                             "description": "для чего ищем окно"},
                  "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                  "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                  "min_hours": {"type": "integer", "minimum": 1, "maximum": 12},
                  "venue": {"type": "string", "description": "код зала для аренды"},
                  "limit": {"type": "integer", "minimum": 1, "maximum": 10}}),
         ("schedule.slots",), True, tools.run_find_slots),
    Tool("propose_event", "Предложить событие: тип, люди, дата, время, зал. Ничего не пишет — "
         "человек подтвердит карточкой. Без даты, времени или участников вернёт needs_input: "
         "не додумывай их, а предложи заполнить в приложении.",
         _schema({"kind": {"type": "string",
                           "enum": ["rehearsal", "show", "training", "rental", "meeting"]},
                  "people": {"type": "array", "items": {"type": "string"},
                             "description": "имена участников"},
                  "date": {"type": "string", "description": "YYYY-MM-DD"},
                  "from_hour": {"type": "integer", "minimum": 8, "maximum": 23},
                  "to_hour": {"type": "integer", "minimum": 9, "maximum": 24},
                  "venue": {"type": "string", "description": "код зала"},
                  "title": {"type": "string"},
                  "discipline": {"type": "string", "description": "код дисциплины тренинга"},
                  "card": {"type": "object", "description": "готовая карточка события"}}),
         ("schedule.write",), True, tools.run_propose_event),
    Tool("propose_note", "Предложить заметку: личную или общую. Ничего не пишет — человек "
         "подтвердит карточкой. Без текста вернёт needs_input.",
         _schema({"title": {"type": "string"}, "body": {"type": "string"},
                  "scope": {"type": "string", "enum": ["personal", "shared"]},
                  "tags": {"type": "array", "items": {"type": "string"}}}),
         ("notes.write",), True, tools.run_propose_note),
    Tool("propose_move", "Предложить переложить свой файл в папку. Применяет человек кнопкой; "
         "удаления файлов нет ни в одном инструменте.",
         _schema({"file_id": {"type": "integer"}, "folder_path": {"type": "string"}},
                 ["file_id", "folder_path"]),
         (), True, file_tools.run_propose_move),
    Tool("propose_budget_tx", "Предложить записать расход или доход. Сам ничего не пишет: "
         "человек подтвердит кнопкой. Категорию выбирай сам ровно из закрытого списка "
         "(поле category), не спрашивай её у человека; если подходящей нет — «Прочее» "
         "для расхода, для дохода — ближайшая по смыслу.",
         _schema({"tx_type": {"type": "string", "enum": ["expense", "income"]},
                  "amount": {"type": "number", "exclusiveMinimum": 0},
                  "category": {"type": "string"},
                  "purpose": {"type": "string"},
                  "tx_date": {"type": "string", "description": "YYYY-MM-DD, по умолчанию сегодня"},
                  "counterparty": {"type": "string"}},
                 ["tx_type", "amount", "category"]),
         ("budget.write",), True, tools.run_propose_budget),
    Tool("web_search", "Внешний поиск в интернете. Только при включённом флаге; "
         "данные студии им не заменяются.",
         _schema({"query": {"type": "string"}}, ["query"]),
         (), True, tools.run_web_search),
)

_BY_NAME = {tool.name: tool for tool in TOOLS}


def all_tools() -> tuple[Tool, ...]:
    """Весь закрытый список, независимо от прав. Для тестов и разбора, не для модели."""
    return TOOLS


def available_tools(who: dict) -> list[Tool]:
    """Инструменты, которые этот человек вправе видеть. Реестр, а не свободный выбор."""
    result = []
    for tool in TOOLS:
        if tool.name == "web_search" and not config.web_search_enabled():
            continue
        if tool.requires and not has_cap(who, *tool.requires):
            continue
        result.append(tool)
    return result


def tool_schemas(who: dict) -> list[dict]:
    """Схема `tools` для провайдера модели: только разрешённое."""
    return [{"type": "function",
             "function": {"name": tool.name, "description": tool.description,
                          "parameters": tool.json_schema}}
            for tool in available_tools(who)]


def execute(conn, who: dict, name: str, args: dict | None = None) -> dict:
    """Выполнить инструмент с повторной проверкой прав на сервере."""
    tool = _BY_NAME.get(name)
    if tool is None:
        return {"ok": False, "error": "unknown_tool", "message": f"инструмента «{name}» нет"}
    # Порядок проверок важен: объявленный пишущим инструмент не выполняется никогда —
    # ни по правам, ни вопреки им.
    if not tool.read_only:
        return {"ok": False, "error": "write_tool_disabled",
                "message": "пишущие инструменты агенту на этом этапе недоступны"}
    if tool not in available_tools(who):
        return {"ok": False, "error": "forbidden",
                "message": "инструмент недоступен этому человеку"}
    if tool.handler is None:
        return {"ok": False, "error": "tool_failed", "message": "у инструмента нет обработчика"}
    if not isinstance(args, dict):
        args = {}
    try:
        result = tool.handler(conn, who, args)
    except TOOL_ERRORS as exc:          # граница «код ↔ данные»: модель видит отказ
        core.log.warning("инструмент агента %s упал: %s", name, exc)
        return {"ok": False, "error": "tool_failed", "message": str(exc)[:200]}
    if not isinstance(result, dict):
        return {"ok": False, "error": "tool_failed", "message": "инструмент ничего не вернул"}
    return result
