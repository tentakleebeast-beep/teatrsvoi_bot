#!/usr/bin/env python3
"""Кто есть кто для скриптов импорта и сверки: соответствие берётся из данных (Ш-03a).

Раньше каждый скрипт держал свой словарь «имя в файле → человек» (`NICK` в
`import_schedule.py`, `aliases` в `reconcile.py`). После обезличивания имён (Ш-03 плана 129)
словари указывали на вымышленные имена, а боевая БД хранит настоящие, поэтому импорт
расписания и сверка занятости переставали находить людей.

Теперь источник один для всех скриптов:

  1. `participant` (`person_key`, `display_name`) и `person_alias` из БД;
  2. `config/participant-names.yaml` — локальный файл оператора, если он есть.

Имён в коде нет: никнейм, которого нет в БД, дописывается в конфиг, а не в скрипт.
`scripts/import_schedule.py` заодно заводит алиас на никнейм листа после первого импорта.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import _cli

_cli.add_tool_api_path()

from app.participant_names import (  # noqa: E402  (путь к app добавляет _cli)
    PersonIndex,
    build_index,
    category_hints,
    load_config,
)


def person_index(conn: Any, config: Path | str | None = None) -> PersonIndex:
    """Поиск людей по данным БД и локальному конфигу; сети и Google не касается."""
    participants = conn.execute(
        "select id, display_name, person_key from participant").fetchall()
    aliases = conn.execute(
        "select participant_id, alias from person_alias where participant_id is not null"
    ).fetchall()
    return build_index(participants, aliases, load_config(config))


def unmatched_sheets(sheetnames: list[str], index: PersonIndex) -> list[str]:
    """Листы файла, для которых в данных нет человека: они в сверке не участвуют.

    Список называется честно, без догадок «это человек» или «это свод»: в файле расписания
    рядом с людьми живут служебные листы, и отличить их по разметке нельзя. Если в списке
    оказался лист человека — никнейм дописывается в `config/participant-names.yaml`.
    """
    return [name for name in sheetnames if index.resolve(name) is None]


def hint_rules() -> tuple[tuple[str, str], ...]:
    """Подсказки категории по имени человека в виде правил импорта: (регулярка, категория)."""
    return tuple((re.escape(hint), category) for hint, category in category_hints())


def report_gaps(index: PersonIndex, missing_sheets: list[str]) -> list[str]:
    """Что сказать человеку: какие листы остались без своего человека.

    Возвращает готовые строки отчёта, чтобы `import_schedule.py` и `reconcile.py` не
    расходились в формулировках. Пустой список — все листы нашли своего человека.
    """
    lines = []
    if missing_sheets:
        lines.append("листы без человека в БД (не сверялись): " + ", ".join(missing_sheets)
                     + " — если это лист человека, добавьте никнейм"
                       " в config/participant-names.yaml")
    if index.unresolved:
        lines.append("в конфиге есть никнеймы без человека: " + ", ".join(index.unresolved))
    if index.conflicts:
        lines.append("конфиг расходится с БД (взято из БД): " + ", ".join(index.conflicts))
    return lines
