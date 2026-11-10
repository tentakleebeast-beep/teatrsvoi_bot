"""Общий файл вопросов: нумерация, дописывание и закрытие решения (V-035).

Вынос `next_question_id`/`append_question` из `feedback_queue` и новый `resolve_question`
проверяются на тексте файла, а не на живом документе: именно здесь ошибка формата
разваливает таблицу, а не падает с исключением.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import _questions as shared  # noqa: E402
import feedback_queue as fq  # noqa: E402

SAMPLE = """# 74. Вопросы

| ID | Дата | Вопрос | Откуда | Статус | Ответ |
|---|---|---|---|---|---|
| V-001 | 28.10.2026 | первый | Ш-35 | решено | 28.10.2026: «да» |
| V-023 | 30.10.2026 | последний | UX | открыт | — |

---

## 2. Правила ведения

1. Правило без таблицы.
"""


def test_V_035_следующий_номер_по_максимуму_а_не_по_числу_строк():
    """V-035: пропуск номера не должен давать дубль."""
    text = SAMPLE.replace("| V-023 |", "| V-057 |")
    assert shared.next_question_id(text) == "V-058"


def test_V_035_вопрос_дописывается_последним_в_таблицу():
    """V-035: строка встаёт после последнего V-NNN, до раздела правил."""
    updated, question_id = shared.append_question(
        SAMPLE, "Делать ли  экран? | Варианты: да / нет", "обратная связь", "30.10.2026")
    assert question_id == "V-024"
    lines = updated.splitlines()
    row = next(line for line in lines if line.startswith("| V-024 "))
    assert "| 30.10.2026 | Делать ли экран? / Варианты: да / нет |" in row
    assert lines.index(row) > next(i for i, x in enumerate(lines) if x.startswith("| V-023 "))
    assert lines.index(row) < lines.index("## 2. Правила ведения")
    assert updated.endswith("\n")


def test_V_035_без_таблицы_вопросов_ошибка():
    """V-035: молча писать в неизвестный формат нельзя."""
    try:
        shared.append_question("# пусто", "вопрос", "№1", "30.10.2026")
    except ValueError as exc:
        assert "нет таблицы" in str(exc)
    else:
        raise AssertionError("ожидалась ошибка")


def test_V_035_поиск_вопроса_по_маркеру_строки():
    """V-035: маркер строки живёт в ячейке «Откуда», не в тексте вопроса."""
    text = SAMPLE.replace("| UX |", "| лист «Чего не умеем», строка a1b2c3d4 |")
    question = shared.find_question(text, "a1b2c3d4")
    assert question is not None
    assert question.id == "V-023"
    assert question.status == "открыт"
    assert shared.find_question(text, "a1b2c3d5") is None


def test_V_035_решение_переписывает_одну_строку_и_не_трогает_соседние():
    """V-035: второй строки не появляется, соседи остаются байт в байт."""
    text = SAMPLE.replace("| UX |", "| лист «Чего не умеем», строка a1b2c3d4 |")
    updated, question_id = shared.resolve_question(
        text, "a1b2c3d4", "решено", "10.11.2026: „да“ — работа в карте t_1")
    assert question_id == "V-023"
    before, after = text.splitlines(), updated.splitlines()
    assert len(before) == len(after)
    changed = [index for index in range(len(before)) if before[index] != after[index]]
    assert changed == [before.index(next(line for line in before if "V-023" in line))]
    row = after[changed[0]]
    assert "| решено | 10.11.2026: „да“ — работа в карте t_1 |" in row
    assert "| открыт |" not in row


def test_V_035_вертикальная_черта_в_ответе_не_разваливает_таблицу():
    """V-035: `|` в ответе заменяется на `/` — иначе поедут колонки всей таблицы."""
    text = SAMPLE.replace("| UX |", "| лист «Чего не умеем», строка a1b2c3d4 |")
    updated, _ = shared.resolve_question(text, "a1b2c3d4", "решено", "да | нет")
    row = next(line for line in updated.splitlines() if "V-023" in line)
    assert row.count("|") == 7
    assert "да / нет" in row


def test_V_035_feedback_queue_по_прежнему_отдаёт_обе_функции():
    """V-035: старые имена доступны из `feedback_queue` — его тесты не правятся."""
    assert fq.next_question_id is shared.next_question_id
    assert fq.append_question is shared.append_question
