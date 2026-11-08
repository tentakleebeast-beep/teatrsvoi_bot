"""Один агент: второй цикл с инструментами удалён, файлы живут в общем реестре.

Критерий карты «Озадачить»: `grep` не находит второго цикла с инструментами,
токены считаются один раз. Проверяем исходники, а не обещание в документе.

Обоснование: docs/development/108 §4, критерий «один агент» карты t_cb7ccc24.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOL_API = ROOT / "services" / "tool-api" / "app"


def _source(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def test_файловый_агент_не_содержит_своего_цикла() -> None:
    source = _source(TOOL_API / "agent.py")

    assert "tool_calls" not in source, "файловый агент не должен разбирать tool_calls сам"
    assert "def _call_model" not in source, "второй вызов модели — это второй цикл"
    assert "agent_loop.run" in source, "файловый маршрут обязан идти общим циклом"


def test_второго_цикла_с_инструментами_нет_во_всём_tool_api() -> None:
    owners = [path for path in TOOL_API.rglob("*.py") if "tool_calls" in _source(path)]

    assert owners == [TOOL_API / "agently" / "loop.py"], \
        f"tool_calls разбирают не только в общем цикле: {owners}"


def test_лимит_шагов_объявлен_в_общем_цикле_один_раз() -> None:
    source = _source(TOOL_API / "agently" / "loop.py")

    assert len(re.findall(r"for index in range\(1, limit_steps \+ 1\)", source)) == 1


def test_файловые_инструменты_в_реестре_а_не_в_отдельном_списке() -> None:
    registry = _source(TOOL_API / "agently" / "registry.py")

    for name in ('"list_files"', '"read_file"', '"propose_move"'):
        assert name in registry, f"инструмент {name} не попал в общий реестр"
