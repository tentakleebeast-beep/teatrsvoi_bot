"""Сторож R-5: широких `except` в сервисах больше нет.

Зачем: `except Exception` в обработчике превращал опечатку в имени поля в ответ
`ok:false` «сервис недоступен», и настоящая поломка не доходила до конверта ошибок
с `request_id`. Ловим только типы из `app/external.py` (tool-api) и
`app/errors.py` (tg-gateway); всё остальное обязано дойти до 500.

Тест — по исходникам, а не по поведению: он один стережёт все 45 мест сразу
и не зависит от того, какие ветки сегодня покрыты сценариями.

Правила и требования раздела: docs/reports/100-narrow-except-and-types.md.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "services"

# `except Exception`, `except BaseException`, `except:` — всё это «поймай что угодно».
BROAD = re.compile(r"^\s*except\s*(?:\(\s*)?(?:Exception|BaseException)\b|^\s*except\s*:\s*$")


def _offenders() -> list[str]:
    found: list[str] = []
    for path in sorted(SERVICES.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if BROAD.search(line):
                found.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    return found


def test_в_сервисах_нет_широких_except():
    assert _offenders() == []


def test_main_tool_api_без_широких_except():
    """Критерий карты: `grep -c "except Exception" services/tool-api/app/main.py` == 0."""
    text = (SERVICES / "tool-api" / "app" / "main.py").read_text(encoding="utf-8")
    assert text.count("except Exception") == 0
