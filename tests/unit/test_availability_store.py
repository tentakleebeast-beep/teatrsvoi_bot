"""Один путь записи в `availability_hour` (З1, FR-C9, FR-H8).

Обоснование: FR-C9 (занятость карточкой дня), FR-H8 (участник заполняет неделю),
docs/reports/124 §3 (З1) и §5 — «один обработчик З1». Дефект, который тест
сторожит: таблицу писали четыре места со своими правилами `is_free`/`raw_text`,
и «не уверен», записанный формой, читался обратно как занятость.

Тест статический там, где это дешевле: SQL ищется по исходникам, а не по
поведению. Смысловые правила проверяются на поддельном соединении.
"""

from __future__ import annotations

import pathlib
import sys
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app import availability_store  # noqa: E402

TOOL_API = ROOT / "services" / "tool-api"
STORE = TOOL_API / "app" / "availability_store.py"


class FakeCursor:
    """Минимальный курсор psycopg: помнит запрос, отвечает числом строк."""

    def __init__(self, rowcount: int = 1, count: int = 0) -> None:
        self.rowcount = rowcount
        self.count = count

    def fetchone(self) -> dict:
        return {"days": self.count}


class FakeConn:
    """Соединение, которое только записывает вызовы — без базы."""

    def __init__(self, rowcount: int = 1, count: int = 0) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.rowcount = rowcount
        self.count = count

    def execute(self, sql: str, params=()) -> FakeCursor:  # noqa: ANN001 - подделка
        self.calls.append((" ".join(sql.split()), params))
        return FakeCursor(self.rowcount, self.count)

    def fetchone(self):  # noqa: ANN201 - подделка
        return {"days": self.count}


def test_з1_в_живом_коде_нет_второго_пути_записи():
    """Только `availability_store` содержит SQL к `availability_hour`."""
    needles = ("insert into availability_hour", "delete from availability_hour",
               "update availability_hour")
    offenders = []
    for path in sorted(TOOL_API.rglob("*.py")):
        if path == STORE:
            continue
        text = path.read_text(encoding="utf-8").lower()
        for needle in needles:
            if needle in text:
                offenders.append(f"{path.relative_to(TOOL_API)}: {needle}")
    assert not offenders, "второй путь записи в availability_hour: " + ", ".join(offenders)


def test_з1_входы_занятости_зовут_общий_модуль():
    """`commit`, неделя, лист и очистка пишут через `availability_store`."""
    schedule = (TOOL_API / "app" / "api" / "schedule.py").read_text(encoding="utf-8")
    for call in ("availability_store.write_range(", "availability_store.write_hours(",
                 "availability_store.delete_day(", "availability_store.delete_week(",
                 "availability_store.count_days("):
        assert call in schedule, f"вход потерял общий путь записи: {call}"

    # у самих входов своего SQL больше нет — иначе появился бы второй путь
    for name in ("def _save_week(", "def availability_commit(", "def _clear_week(",
                 "def _sheet_sync("):
        start = schedule.index(name)
        tail = schedule[start:start + 2500]
        body = tail.split("\n\n\n", 1)[0]
        for sql in ("insert into availability_hour", "delete from availability_hour",
                    "update availability_hour"):
            assert sql not in body.lower(), f"{name}: снова свой запрос к таблице"


def test_свобода_выводится_из_состояния_в_одном_месте():
    """`is_free` — следствие `kind`, а не отдельное решение каждого вызова."""
    assert availability_store.is_free_kind("free") is True
    assert availability_store.is_free_kind("preferred") is True      # «удобно» — тоже свободно
    assert availability_store.is_free_kind("busy") is False
    assert availability_store.is_free_kind("uncertain") is False


def test_запись_ставит_источник_и_не_затирает_текст_пустым():
    """`source` явный, а пустой `raw_text` не стирает уже записанный канон."""
    conn = FakeConn()
    written = availability_store.write_hours(
        conn, 7, date(2026, 11, 2), {8: "busy", 9: "free", 10: "uncertain"},
        raw_text="занят с 08:00 до 09:00")

    assert written == 3
    assert [call[1][3] for call in conn.calls] == [False, True, False], \
        "is_free разошёлся с состоянием"
    assert {call[1][5] for call in conn.calls} == {"bot"}
    assert "coalesce(excluded.raw_text" in conn.calls[0][0], \
        "пустой raw_text затёр бы текст дня"


def test_интервал_и_день_это_один_и_тот_же_путь():
    """`write_range` — тот же upsert, что `write_hours`, а не второй SQL."""
    conn = FakeConn()
    availability_store.write_range(conn, 7, date(2026, 11, 2), 10, 13, "free")
    assert [call[1][2] for call in conn.calls] == [10, 11, 12]
    assert {call[1][4] for call in conn.calls} == {"free"}
    assert all("insert into availability_hour" in call[0].lower() for call in conn.calls)


def test_удаление_часов_и_недели_уважает_источник():
    """Сетка снимает только свои отметки; очистка недели снимает всё."""
    conn = FakeConn(rowcount=4)
    assert availability_store.delete_day(conn, 7, date(2026, 11, 2),
                                         hours=[9, 10], source="bot") == 4
    sql, params = conn.calls[0]
    assert "hour = any(%s)" in sql and "source = %s" in sql
    assert params == [7, date(2026, 11, 2), [9, 10], "bot"]

    conn = FakeConn(rowcount=16)
    assert availability_store.delete_week(conn, 7, date(2026, 11, 2),
                                          date(2026, 11, 8)) == 16
    assert "day between %s and %s" in conn.calls[0][0]


def test_счёт_дней_недели_отдельный_от_часов():
    """«Снято N отметок» — это дни, а не строки часов (Ш-53)."""
    conn = FakeConn(count=3)
    assert availability_store.count_days(conn, 7, date(2026, 11, 2), date(2026, 11, 8)) == 3
    assert "count(distinct day)" in conn.calls[0][0]
