"""Экспорт результатов: CSV с `;` и BOM, Markdown-блоки «## Вопрос N» (AC-11, AC-12).

CSV должен открываться в LibreOffice без пляски с кодировкой, а Markdown — вставляться
в журнал исследования `docs/development/161` §7 без правки. Проверка — парсинг
обратно, а не сравнение глазами.

Обоснование: FR-G18, AC-11, AC-12, `docs/development/162-bot-polls.md` §14.2, §14.7.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timezone

import pytest
from app import poll_store
from app.webapp import InitDataError, issue_export_link, issue_token, verify_export_link

NOW = datetime(2026, 11, 15, 10, 30, tzinfo=UTC)

QUESTIONS = [
    {"id": 1, "position": 1, "text": "Какие файлы открываете?", "kind": "single",
     "required": True, "free_text": False,
     "options": [{"code": "a1", "label": "Расписание"}, {"code": "a2", "label": "Звонки"}]},
    {"id": 2, "position": 2, "text": "Сколько часов?", "kind": "number", "required": True,
     "free_text": False, "options": []},
]


def _answers():
    return [
        {"question_id": 1, "participant_id": 7, "display_name": "Аня", "run_id": 1,
         "option_codes": ["a1"], "text_value": None, "number_value": None,
         "skipped": False, "answered_at": NOW},
        {"question_id": 1, "participant_id": 8, "display_name": "Боря", "run_id": 2,
         "option_codes": ["a2"], "text_value": None, "number_value": None,
         "skipped": False, "answered_at": NOW},
        {"question_id": 2, "participant_id": 7, "display_name": "Аня", "run_id": 1,
         "option_codes": [], "text_value": None, "number_value": 4,
         "skipped": False, "answered_at": NOW},
    ]


def test_csv_четыре_строки_точка_с_запятой_и_bom():
    people = {7: "Аня", 8: "Боря"}
    data = poll_store.export_csv(
        poll_store.export_rows({"id": 1}, QUESTIONS, _answers(), people))
    assert data.startswith("\ufeff")
    rows = list(csv.reader(io.StringIO(data[1:]), delimiter=";"))
    assert len(rows) == 4
    assert rows[0] == ["вопрос", "тип", "человек", "ответ", "когда"]
    assert rows[1][2] == "Аня" and rows[1][3] == "Расписание"
    assert rows[3][3] == "4"


def test_csv_экранирует_поле_с_разделителем():
    rows = [("1. Вопрос; с точкой с запятой", "text", "Аня", 'ответ "в кавычках"', "")]
    data = poll_store.export_csv(rows)
    parsed = list(csv.reader(io.StringIO(data[1:]), delimiter=";"))
    assert parsed[1][0] == "1. Вопрос; с точкой с запятой"
    assert parsed[1][3] == 'ответ "в кавычках"'


def test_markdown_даёт_блоки_вопросов_и_вставляется_в_журнал():
    summaries = {question["id"]: poll_store.summarize(
        question, [row for row in _answers() if row["question_id"] == question["id"]])
        for question in QUESTIONS}
    data = poll_store.export_markdown({"title": "Анкета", "intro": "Общий блок"}, QUESTIONS,
                                      summaries)
    assert data.startswith("## Анкета")
    assert "## Вопрос 1" in data and "## Вопрос 2" in data
    assert "- Расписание: 1" in data
    assert "- минимум 4, в среднем 4, максимум 4" in data
    # Блоки отделены пустой строкой: вставка в docs/development/161 не ломает разметку.
    assert "\n## Вопрос 2" in data


def test_markdown_анонимный_не_называет_автора():
    question = {"id": 9, "position": 1, "text": "Расскажите", "kind": "text",
                "required": False, "free_text": False, "options": []}
    rows = [{"question_id": 9, "participant_id": None, "display_name": None, "run_id": 1,
             "option_codes": [], "text_value": "Ок", "number_value": None,
             "skipped": False, "answered_at": NOW}]
    data = poll_store.export_markdown({"title": "Т", "intro": None}, [question],
                                      {9: poll_store.summarize(question, rows)},
                                      anonymous=True)
    assert "- Ок" in data and "Аня" not in data


# --- ссылка на скачивание: срок и подпись (обращение №44) ---------------------
def test_ссылка_на_экспорт_годна_и_несёт_опрос_с_форматом():
    token = issue_export_link(44, "csv", "secret-key", now=1_000)
    assert verify_export_link(token, "secret-key", now=1_100) == {"poll_id": 44, "fmt": "csv"}


def test_ссылка_на_экспорт_живёт_15_минут():
    token = issue_export_link(44, "md", "secret-key", now=1_000)
    assert verify_export_link(token, "secret-key", now=1_000 + 900)["fmt"] == "md"
    with pytest.raises(InitDataError) as expired:
        verify_export_link(token, "secret-key", now=1_000 + 901)
    assert str(expired.value) == "token_expired"


def test_ссылка_с_чужой_подписью_не_пускает():
    token = issue_export_link(44, "csv", "secret-key", now=1_000)
    with pytest.raises(InitDataError) as foreign:
        verify_export_link(token, "other-secret", now=1_100)
    assert str(foreign.value) == "bad_token_signature"


def test_токен_mini_app_не_подходит_вместо_ссылки_на_экспорт():
    """Оси разные: токен человека — пропуск в Mini App, а не ссылка на файл."""
    with pytest.raises(InitDataError):
        verify_export_link(issue_token(7, "secret-key", now=1_000), "secret-key", now=1_000)
