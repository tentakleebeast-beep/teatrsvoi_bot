"""Очередь разбора обратной связи (V-024, решение Виктора 30.10.2026).

Задание разбора идёт каждые 10 минут с чистым контекстом, поэтому нумерация
вопросов и форма строки в файле вопросов проверяются здесь, а не «на глаз»
в живом документе.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import feedback_queue as fq  # noqa: E402

SAMPLE = """# 74. Вопросы

| ID | Дата | Вопрос | Откуда | Статус | Ответ |
|---|---|---|---|---|---|
| V-001 | 28.10.2026 | первый | Ш-35 | решено | — |
| V-023 | 30.10.2026 | последний | UX | открыт | — |

---

## 2. Правила ведения

1. Правило без таблицы.
"""


def test_следующий_номер_вопроса_по_максимуму_а_не_по_числу_строк():
    """V-024: пропуск номера не должен давать дубль."""
    text = SAMPLE.replace("| V-023 |", "| V-030 |")
    assert fq.next_question_id(text) == "V-031"


def test_вопрос_дописывается_последним_в_таблицу():
    """V-024: строка встаёт после последнего V-NNN, до раздела правил."""
    updated, question_id = fq.append_question(
        SAMPLE, "Делать ли  экран? | Варианты: да / нет", "обращение №35", "30.10.2026")
    assert question_id == "V-024"
    lines = updated.splitlines()
    row = next(line for line in lines if line.startswith("| V-024 "))
    assert "| 30.10.2026 | Делать ли экран? / Варианты: да / нет |" in row
    assert lines.index(row) > next(i for i, x in enumerate(lines) if x.startswith("| V-023 "))
    assert lines.index(row) < lines.index("## 2. Правила ведения")
    assert updated.endswith("\n")


def test_без_таблицы_вопросов_ошибка():
    """V-024: молча писать в неизвестный формат нельзя."""
    try:
        fq.append_question("# пусто", "вопрос", "№1", "30.10.2026")
    except ValueError as exc:
        assert "нет таблицы" in str(exc)
    else:
        raise AssertionError("ожидалась ошибка")
