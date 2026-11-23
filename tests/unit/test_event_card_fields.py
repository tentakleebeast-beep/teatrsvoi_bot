"""Поля карточки события: «пусто — значит null» (docs/development/190 §3.2).

Обоснование: FR-D3, `docs/development/190` §3.2 (таблица полей), AC-8.
Экран не рисует подпись без значения, поэтому пустое поле обязано прийти `null`
или пустой строкой — но не строкой `""` в месте, где экран ждёт отсутствие, и не
`"None"`. Проверяются правила разбора, на которых это держится.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.api.webapp_events import _clean, _color_id, _private, _to_dt


def test_пустое_поле_отдаётся_null() -> None:
    """`location`, `description`, `color_id` пустые — `None`, а не пустая строка."""
    assert _clean("") is None
    assert _clean("   ") is None
    assert _clean(None) is None
    assert _clean(" Сцена ") == "Сцена"
    assert _color_id({}) is None
    assert _color_id({"colorId": ""}) is None
    assert _color_id({"colorId": None}) is None
    assert _color_id({"colorId": "8"}) == 8


def test_событие_на_весь_день_не_имеет_времени() -> None:
    """При `start.date` время `null`, а `all_day` истинно (§3.2)."""
    start, all_day = _to_dt("2026-11-25")
    assert all_day is True
    assert start is not None and start.hour == 0 and start.minute == 0
    end, _ = _to_dt("2026-11-26")
    assert end is not None and (end - start).days == 1


def test_время_события_разбирается_с_зоной() -> None:
    """`dateTime` с `Z` приводится к зоне сервиса, а не остаётся текстом."""
    moment, all_day = _to_dt("2026-11-24T15:00:00Z")
    assert all_day is False
    assert moment is not None and moment.astimezone(UTC).hour == 15
    local = moment.astimezone(zone())
    assert (local.hour, local.minute) == (19, 0)


def test_мусор_во_времени_не_роняет_карточку() -> None:
    """Битое поле даёт `None`: карточка без времени лучше падения экрана."""
    assert _to_dt("") == (None, False)
    assert _to_dt("завтра") == (None, False)


def test_маркер_бота_читается_из_private() -> None:
    """`svoi_event_id` живёт в `extendedProperties.private` (186 §5.1)."""
    event = {"extendedProperties": {"private": {"svoi_event_id": "412"}}}
    assert _private(event) == {"svoi_event_id": "412"}
    assert _private({}) == {}


def zone():
    """Зона сервиса: Самара, UTC+4 (docs/development/190 §3.2)."""
    from datetime import timedelta, timezone

    return timezone(timedelta(hours=4))


def test_дата_карточки_в_зоне_сервиса() -> None:
    """`date`, `weekday`, `time_from` считаются в `Europe/Samara`, а не в UTC."""
    moment, _ = _to_dt("2026-11-24T15:00:00Z")
    local = moment.astimezone(zone())
    assert local.date().isoformat() == "2026-11-24"
    assert local.strftime("%H:%M") == "19:00"
    assert isinstance(datetime.now(zone()), datetime)
