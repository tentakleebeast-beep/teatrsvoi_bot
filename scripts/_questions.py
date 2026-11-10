#!/usr/bin/env python3
"""Общие операции с файлом вопросов `docs/reports/74-questions.md` (V-035).

Зачем отдельный модуль. У файла два писателя: задание разбора обратной связи
дописывает вопросы (`scripts/feedback_queue.py`), а конвейер листа «Чего не умеем»
закрывает их решением владельца (`scripts/intent_sheet.py`). Пока нумерация и разбор
строки жили в одном месте, второй писатель держал бы свою копию — и копии разошлись
бы молча: первый же пропуск номера дал бы дубль `V-NNN`.

Имена прежние: `next_question_id` и `append_question` импортируются обратно
в `feedback_queue`, поэтому `tests/unit/test_feedback_queue.py` не правится.

  from _questions import QUESTIONS, append_question, find_question, resolve_question

Формат строки не меняется: `| V-NNN | дата | вопрос | откуда | статус | ответ |`.
`|` внутри ячейки заменяется на `/`, иначе таблица разваливается.

Тест: tests/unit/test_shared_questions.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
QUESTIONS = PROJECT / "docs" / "reports" / "74-questions.md"

# В файле встречаются оба написания статуса: V-001 — «решено», V-028 — «решён».
RESOLVED = ("решено", "решён")
# Номер вопроса в начале строки таблицы. Только он и считается строкой вопроса:
# в тексте вопросов бывают упоминания «V-035», но не в начале ячейки.
QUESTION_ROW = re.compile(r"^\|\s*V-(\d+)\s*\|", re.MULTILINE)
# Маркер строки листа «Чего не умеем» в ячейке «Откуда».
ROW_MARKER = re.compile(r"строка ([0-9a-f]{8})(?![0-9a-f])")


@dataclass(frozen=True)
class Question:
    """Строка таблицы вопросов: номер, статус, откуда пришёл и что ответили."""

    id: str
    status: str
    source: str
    answer: str
    cells: tuple[str, ...]
    line: int


def row_marker(row_id: str) -> re.Pattern[str]:
    """Маркер строки листа: строгий, иначе `a1b2c3d4` совпадёт с `a1b2c3d4e`."""
    return re.compile(rf"строка {re.escape(row_id)}(?![0-9a-f])")


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def questions(text: str) -> list[Question]:
    """Все строки `V-NNN` файла в порядке следования; кривые строки пропускаются."""
    found: list[Question] = []
    for index, line in enumerate(text.splitlines()):
        if not QUESTION_ROW.match(line):
            continue
        cells = _cells(line)
        if len(cells) != 6:
            continue
        found.append(Question(id=cells[0], status=cells[4], source=cells[3],
                              answer=cells[5], cells=tuple(cells), line=index))
    return found


def find_question(text: str, row_id: str) -> Question | None:
    """Вопрос по маркеру строки: свежий номер выигрывает, если строк несколько."""
    pattern = row_marker(row_id)
    match: Question | None = None
    for question in questions(text):
        if pattern.search(question.source):
            match = question
    return match


def next_question_id(text: str) -> str:
    """Следующий V-NNN: максимум существующих + 1, а не число строк."""
    ids = [int(match.group(1)) for match in QUESTION_ROW.finditer(text)]
    return f"V-{max(ids) + 1:03d}" if ids else "V-001"


def append_question(text: str, question: str, source: str, today: str) -> tuple[str, str]:
    """Дописывает строку вопроса последней в таблицу раздела «Открытые»."""
    clean = " ".join(question.split()).replace("|", "/")
    if not clean:
        raise ValueError("пустой текст вопроса")
    question_id = next_question_id(text)
    row = f"| {question_id} | {today} | {clean} | {source} | открыт | — |"
    lines = text.splitlines()
    last = None
    for index, line in enumerate(lines):
        if QUESTION_ROW.match(line):
            last = index
    if last is None:
        raise ValueError(f"в {QUESTIONS.name} нет таблицы вопросов V-NNN")
    lines.insert(last + 1, row)
    updated = "\n".join(lines)
    if text.endswith("\n"):
        updated += "\n"
    return updated, question_id


def resolve_question(text: str, row_id: str, status: str, answer: str) -> tuple[str, str]:
    """Переписывает ту же строку V-NNN: статус и ответ, — не трогая соседние.

    Второй строки не дописывает: история решения живёт в самой строке, иначе
    один вопрос заводил бы новый номер на каждый ответ.
    """
    question = find_question(text, row_id)
    if question is None:
        raise ValueError(f"вопрос по строке {row_id} не найден")
    cells = list(question.cells)
    cells[4] = " ".join(status.split()).replace("|", "/")
    cells[5] = " ".join(answer.split()).replace("|", "/")
    lines = text.splitlines()
    lines[question.line] = "| " + " | ".join(cells) + " |"
    updated = "\n".join(lines)
    if text.endswith("\n"):
        updated += "\n"
    return updated, question.id
