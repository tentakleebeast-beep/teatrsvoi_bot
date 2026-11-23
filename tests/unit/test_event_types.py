"""Типы событий: засев, палитра, транслитерация кода (docs/development/190 §6.3, §7.2).

Обоснование: FR-D3, `docs/development/190` §7 (инварианты I-1, I-2), AC-2, AC-18.
Проверяется не «функция вернула строку», а три обещания:

* засев миграции и палитра `config/calendar-palette.yaml` не разъезжаются молча;
* код нового типа собирается из названия и не зависит от его правки;
* порядок и буквы берутся из таблицы, а не из литерального словаря в коде.
"""

from __future__ import annotations

import pathlib
import re

import yaml
from app import event_types

ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATION = (ROOT / "db" / "init" / "074_calendar_event_types.sql").read_text(encoding="utf-8")
PALETTE = yaml.safe_load(
    (ROOT / "config" / "calendar-palette.yaml").read_text(encoding="utf-8"))["types"]


class FakeConn:
    """Соединение без БД: словарь типов из засева миграции 074."""

    SEED = (
        {"code": "rental", "letter": "А", "title": "Аренда", "color_id": 10,
         "sort_order": 10, "is_active": True},
        {"code": "show", "letter": "С", "title": "Спектакль", "color_id": 11,
         "sort_order": 20, "is_active": True},
        {"code": "rehearsal", "letter": "Р", "title": "Репетиция", "color_id": 9,
         "sort_order": 30, "is_active": True},
        {"code": "training", "letter": "Т", "title": "Тренинг", "color_id": 5,
         "sort_order": 40, "is_active": True},
        {"code": "project", "letter": "П", "title": "Проект", "color_id": None,
         "sort_order": 50, "is_active": False},
        {"code": "meeting", "letter": None, "title": "Встреча", "color_id": 8,
         "sort_order": 90, "is_active": False},
        {"code": "other", "letter": None, "title": "Другое", "color_id": 8,
         "sort_order": 91, "is_active": False},
    )

    def __init__(self, rows=None) -> None:
        self.rows = [dict(item) for item in (rows or self.SEED)]

    def execute(self, sql, params=None):
        return self

    def fetchall(self):
        return list(self.rows)


def _seed_rows() -> list[tuple[str, str | None, str, int | None, int, bool]]:
    """Строки засева из файла миграции: ровно то, что накатится на пустую базу."""
    block = MIGRATION.split("insert into calendar_event_type", 1)[1].split("on conflict", 1)[0]
    rows = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("('"):
            continue
        parts = [part.strip() for part in line.strip("(),").split(",")]
        code, letter, title = parts[0].strip("'"), parts[1], parts[2].strip("'")
        color = parts[3]
        rows.append((code,
                     None if letter == "null" else letter.strip("'"),
                     title,
                     None if color == "null" else int(color),
                     int(parts[4]),
                     parts[5] == "true"))
    return rows


def test_засев_семь_типов_по_таблице() -> None:
    """§7.2: четыре активных с буквой и цветом, project без цвета, meeting/other без буквы.

    Заголовок AC-2 говорит «5 активных с буквами и цветами», а таблица §7.2 даёт
    четыре активных (`rental`, `show`, `rehearsal`, `training`) и пятый тип с буквой —
    `project`, он неактивен, пока организатор не назначит цвет. Таблица конкретнее
    заголовка, поэтому проверяется она: букв пять, активных четыре.
    """
    rows = _seed_rows()
    assert [row[0] for row in rows] == [
        "rental", "show", "rehearsal", "training", "project", "meeting", "other"]
    active = [row for row in rows if row[5]]
    assert len(active) == 4
    assert all(row[1] and row[3] for row in active), "активный тип без буквы или цвета"
    assert len([row for row in rows if row[1]]) == 5, "букв у типов пять"
    project = next(row for row in rows if row[0] == "project")
    assert project[5] is False and project[3] is None and project[1] == "П"
    for code in ("meeting", "other"):
        row = next(item for item in rows if item[0] == code)
        assert row[5] is False and row[1] is None


def test_засев_совпадает_с_палитрой() -> None:
    """I-1: цвета засева и `config/calendar-palette.yaml` — одни и те же числа.

    Палитра остаётся входом скрипта заполнения черновика; продуктовый код её не
    читает, но значения обязаны совпадать, иначе после перехода на таблицу события
    в календаре поменяли бы цвет.
    """
    rows = {row[0]: row for row in _seed_rows()}
    pairs = {"rental": "Аренда", "show": "Показ", "rehearsal": "Репетиция",
             "training": "Тренинг", "meeting": "Служебное"}
    for code, name in pairs.items():
        assert rows[code][3] == PALETTE[name]["color_id"], code


def test_порядок_и_буквы_читаются_из_таблицы() -> None:
    """`types` отдаёт `sort_order, title`; `by_letter` не знает типов без буквы."""
    conn = FakeConn()
    items = event_types.types(conn)
    assert [item["code"] for item in items] == [
        "rental", "show", "rehearsal", "training", "project", "meeting", "other"]
    letters = event_types.by_letter(conn)
    assert letters["С"]["code"] == "show"
    assert None not in letters and "meeting" not in {item["code"] for item in letters.values()}
    assert event_types.titles(conn)["show"] == "Спектакль"
    assert event_types.colors(conn)["project"] is None


def test_транслитерация_названия_в_код() -> None:
    """§6.3: «Концерт» → koncert, «Репетиция» → repeticia, «Проект» → proekt."""
    assert event_types.slug("Концерт") == "koncert"
    assert event_types.slug("Репетиция") == "repeticia"
    assert event_types.slug("Проект") == "proekt"
    assert event_types.slug("Аренда зала") == "arenda_zala"
    assert event_types.slug("Щёлочь") == "scheloch"
    # всё, что не латиница и не цифра, сжимается в одно подчёркивание
    assert event_types.slug("Шоу!!! 2026") == "shou_2026"
    assert len(event_types.slug("Очень длинное название типа события")) <= 32


def test_повтор_названия_получает_суффикс() -> None:
    """Код нового типа уникален: второй «Концерт» становится `koncert_2`."""
    conn = FakeConn()
    assert event_types.next_code(conn, "Концерт") == "koncert"
    busy = FakeConn(FakeConn.SEED + ({"code": "koncert", "letter": None,
                                     "title": "Концерт", "color_id": 3,
                                     "sort_order": 60, "is_active": False},))
    assert event_types.next_code(busy, "Концерт") == "koncert_2"


def test_переименование_названия_не_меняет_код() -> None:
    """I-2: код — стабильный ключ, название — оформление.

    Строка таблицы с кодом `show` и новым названием остаётся кодом `show`:
    в `event_plan.kind` и `summary` метка зависит от буквы, а не от названия.
    """
    renamed = FakeConn(({"code": "show", "letter": "С", "title": "Показ",
                         "color_id": 11, "sort_order": 20, "is_active": True},))
    assert event_types.by_code(renamed)["show"]["title"] == "Показ"
    assert list(event_types.by_code(renamed)) == ["show"]


def test_маркер_главнее_метки_а_метка_главнее_текста() -> None:
    """I-3: порядок «`svoi_kind` → метка `[X]` → без типа»."""
    conn = FakeConn()
    assert event_types.resolve(conn, "show", "Р")["kind"] == "show"
    assert event_types.resolve(conn, None, "Р")["kind"] == "rehearsal"
    unknown = event_types.resolve(conn, None, "Я")
    assert unknown["kind"] is None and unknown["kind_title"] == "Без типа"
    nothing = event_types.resolve(conn, None, None)
    assert nothing["letter"] is None and nothing["kind_title"] == "Без типа"
