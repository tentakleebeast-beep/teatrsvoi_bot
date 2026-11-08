"""Настройки агентного режима: пределы, модели, флаги (docs/development/108).

Все значения читаются из окружения в момент вызова, а не при импорте: тесты
подменяют их через `monkeypatch.setenv`, не перезагружая модуль. Пределы
приводятся к безопасному диапазону — мусор в `.env` не должен открывать
бесконечный цикл или вечный таймаут.

Переменные:
    AGENT_MAX_STEPS        шагов ReAct, по умолчанию из config/agent-models.yaml (4)
    AGENT_TIMEOUT_SECONDS  общий таймаут запроса, по умолчанию 15 (3..60)
    AGENT_TOKEN_BUDGET     бюджет токенов на запрос, по умолчанию 6000
    AGENT_MODEL            модель агентного шага (нужен tool-calling)
    AGENT_CLASSIFIER_MODEL модель классификатора (дешёвая, без инструментов)
    AGENT_CLASSIFIER       выключатель классификатора, по умолчанию включён
    AGENT_WEB_SEARCH       внешний поиск, по умолчанию выключен
    AGENT_WEB_SEARCH_URL   адрес поискового API, если флаг включён

Модели по умолчанию и лимит шагов берутся из `config/agent-models.yaml` (карта A-4):
там же записано, почему выбраны именно эти модели, и лежит дневной бюджет чата.
Переменная окружения остаётся старше файла — это явное переопределение для стенда.
"""

from __future__ import annotations

import os

from .. import llm
from . import models

DEFAULT_MAX_STEPS = models.DEFAULT_MAX_STEPS
DEFAULT_TIMEOUT = 15.0
DEFAULT_TOKEN_BUDGET = 6000
#: Шагов на задачу: решение 05.11.2026 — 6–8, обычный вопрос остаётся на 4.
DEFAULT_TASK_STEPS = 8


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _number(name: str, default: float, *, low: float, high: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = float(raw.replace(",", "."))
    except ValueError:
        return default
    return value if low <= value <= high else default


def max_steps() -> int:
    return int(_number("AGENT_MAX_STEPS", models.role_max_steps(DEFAULT_MAX_STEPS),
                       low=1, high=6))


def task_max_steps() -> int:
    """Шагов на задачу: 6–8 вместо 4 у обычного вопроса (карта «Озадачить»).

    Задача — это несколько инструментов подряд (окна → люди → событие). Четырёх
    шагов на неё не хватает, но открытого лимита нет: 9-й шаг не начинается, и
    человек получает «не разобрался», а не придуманный итог.
    """
    return int(_number("AGENT_TASK_MAX_STEPS", DEFAULT_TASK_STEPS, low=6, high=8))


def timeout() -> float:
    return _number("AGENT_TIMEOUT_SECONDS", DEFAULT_TIMEOUT, low=3, high=60)


def token_budget() -> int:
    return int(_number("AGENT_TOKEN_BUDGET", DEFAULT_TOKEN_BUDGET, low=500, high=100_000))


def agent_model() -> str:
    """Модель агентного шага: своя переменная, иначе роль из конфига.

    `LLM_TOOLS_MODEL` оставлен как второй по старшинству: он уже используется
    агентом по файлам, и держать две разные модели для двух агентов незачем.
    Порядок: AGENT_MODEL → LLM_TOOLS_MODEL → `roles.agent.model` из
    config/agent-models.yaml → общая модель проекта.
    """
    return (_env("AGENT_MODEL") or _env("LLM_TOOLS_MODEL")
            or models.role_model(models.ROLE_AGENT) or llm.model())


def classifier_model() -> str:
    """Классификатор сценария — дешёвая модель без инструментов (по замеру A-0)."""
    return (_env("AGENT_CLASSIFIER_MODEL") or models.role_model(models.ROLE_CLASSIFIER)
            or llm.model())


def classifier_enabled() -> bool:
    return _env("AGENT_CLASSIFIER").lower() not in {"0", "false", "no", "off"}


def web_search_enabled() -> bool:
    """Внешний поиск выключен по умолчанию: внешние ответы не подменяют данные студии."""
    return _env("AGENT_WEB_SEARCH").lower() in {"1", "true", "yes", "on"}


def web_search_url() -> str:
    return _env("AGENT_WEB_SEARCH_URL")
