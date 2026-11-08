"""Единственный путь записи отметок занятости в `availability_hour` (З1, FR-C9).

Зачем отдельный модуль: до Ш-03 таблицу писали четыре места — отметка по часам
(`/tools/availability/commit`), карточка недели (`_save_week`), сетка месяца и
очистка, и они расходились в мелочах: `is_free` у «не уверен», `raw_text`,
источник записи. Расхождение — не стилистика: одна и та же отметка, записанная
разными путями, читалась обратно по-разному.

Теперь весь SQL к `availability_hour` живёт здесь, а вызывающие только говорят,
что записать. Правило «один путь записи» проверяется тестом
`tests/unit/test_availability_store.py`: вне этого модуля в живом коде нет
`insert into availability_hour` / `delete from availability_hour` (скрипты
импорта — отдельные процессы, их карта не касается).

Смысл полей:
* `kind` — состояние отметки: `free`, `busy`, `uncertain`, `preferred`;
* `is_free` — «свободно-подобна» ли отметка; осознанно свободными считаются
  только `free` и `preferred`, «не уверен» — нет (docs/architecture/40);
* `source` — `bot` для всего, что ввёл человек, `sheet_import` для переноса из
  листа: импорт не должен выдавать себя за ручную запись;
* `raw_text` — канонический текст карточки дня; `None` не стирает то, что уже
  лежит в строке (`coalesce`), иначе отметка из сетки затирала бы текст дня.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date

#: Источники, которые понимает схема (`availability_hour.source`).
BOT = "bot"
SHEET_IMPORT = "sheet_import"

#: Состояния, которые считаются свободой. «Не уверен» сюда не входит.
FREE_KINDS = ("free", "preferred")


def is_free_kind(kind: str) -> bool:
    """Свободна ли отметка этого вида — единственное место с этим правилом."""
    return kind in FREE_KINDS


def write_hours(
    conn,
    participant_id: int,
    day: date,
    kinds: Mapping[int, str],
    *,
    source: str = BOT,
    raw_text: str | None = None,
) -> int:
    """Записать карту «час → состояние» за день. Возвращает число часов.

    Один запрос на час, но одно правило на все вызовы: `is_free` выводится из
    `kind`, источник и текст подставляются явно, а конфликт обновляет строку.
    """
    written = 0
    for hour, kind in sorted(kinds.items()):
        conn.execute(
            """
            insert into availability_hour
              (participant_id, day, hour, is_free, kind, source, raw_text)
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (participant_id, day, hour) do update
              set is_free = excluded.is_free,
                  kind = excluded.kind,
                  source = excluded.source,
                  raw_text = coalesce(excluded.raw_text, availability_hour.raw_text),
                  updated_at = now()
            """,
            (participant_id, day, hour, is_free_kind(kind), kind, source, raw_text),
        )
        written += 1
    return written


def write_range(
    conn,
    participant_id: int,
    day: date,
    start: int,
    end: int,
    kind: str,
    *,
    source: str = BOT,
    raw_text: str | None = None,
) -> int:
    """Отметка интервалом часов: «с 10 до 14». Тот же путь, что `write_hours`."""
    return write_hours(conn, participant_id, day, dict.fromkeys(range(start, end), kind),
                       source=source, raw_text=raw_text)


def delete_day(
    conn,
    participant_id: int,
    day: date,
    *,
    hours: Iterable[int] | None = None,
    source: str | None = None,
) -> int:
    """Стереть часы дня (или только перечисленные) — тот же путь, что запись.

    `source` ограничивает удаление: сетка снимает только свои отметки и не
    трогает перенесённые из листа.
    """
    conditions = ["participant_id = %s", "day = %s"]
    params: list = [participant_id, day]
    if hours is not None:
        conditions.append("hour = any(%s)")
        params.append(list(hours))
    if source is not None:
        conditions.append("source = %s")
        params.append(source)
    cur = conn.execute(
        f"delete from availability_hour where {' and '.join(conditions)}", params)
    return cur.rowcount or 0


def count_days(conn, participant_id: int, first: date, last: date) -> int:
    """Сколько дней недели отмечено — для «снято N отметок» (Ш-53)."""
    row = conn.execute(
        "select count(distinct day) as days from availability_hour"
        " where participant_id = %s and day between %s and %s",
        (participant_id, first, last)).fetchone()
    return int(row["days"]) if row else 0


def day_states(conn, first: date, last: date) -> dict[int, dict[str, dict]]:
    """Состояния дней из базы: что отмечено у кого и когда (FR-H9, З7, З9).

    Один расчёт на всех читателей сводки — `GET /webapp/grid` и сетку месяца
    (`GET /webapp/month-grid`). Состояние дня складывается из часов: хоть один
    «не уверен» делает день «не уверен», иначе хоть один занятый — «занят»,
    иначе «свободен». Отсутствие дня в ответе — «не отмечено», а не «свободен»
    (docs/development/22 §7).
    """
    rows = conn.execute("""
        select participant_id, day,
               count(*) filter (where is_free) as free_hours,
               count(*) filter (where not is_free) as busy_hours,
               count(*) filter (where kind = 'uncertain') as unsure_hours,
               count(*) filter (where kind = 'preferred') as preferred_hours
        from availability_hour
        where day between %s and %s
        group by 1, 2""", (first, last)).fetchall()

    out: dict[int, dict[str, dict]] = {}
    for row in rows:
        if row["unsure_hours"]:
            state = "uncertain"
        elif row["busy_hours"]:
            state = "busy"
        else:
            state = "free"
        out.setdefault(row["participant_id"], {})[row["day"].isoformat()] = {
            "state": state, "free_hours": row["free_hours"],
            "busy_hours": row["busy_hours"], "unsure_hours": row["unsure_hours"],
            "preferred_hours": row["preferred_hours"]}
    return out


def delete_week(conn, participant_id: int, first: date, last: date) -> int:
    """Стереть неделю целиком. Возвращает число снятых строк-часов."""
    cur = conn.execute(
        "delete from availability_hour where participant_id = %s and day between %s and %s",
        (participant_id, first, last))
    return cur.rowcount or 0


__all__ = [
    "BOT",
    "FREE_KINDS",
    "SHEET_IMPORT",
    "count_days",
    "delete_day",
    "delete_week",
    "day_states",
    "is_free_kind",
    "write_hours",
    "write_range",
]
