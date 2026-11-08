"""Разграничение «пусто» и «свободно» в файле занятости (docs/development/22 §7)."""

from datetime import date

from app.schedule_grid import Slot, parse_filled_until, split_by_marker, within_marker


def test_читает_пометку_с_годом():
    assert parse_filled_until("Заполнено до: 30.11.2026") == date(2026, 11, 30)


def test_читает_пометку_без_года():
    assert parse_filled_until("заполнено до 15.11", today=date(2026, 10, 26)) == date(2026, 11, 15)


def test_читает_пометку_с_точкой_и_дефисом():
    assert parse_filled_until("Заполнено по 05.12.2026") == date(2026, 12, 5)
    assert parse_filled_until("заполнено до 05-12-2026") == date(2026, 12, 5)


def test_без_пометки_нет_ограничения():
    assert parse_filled_until("") is None
    assert parse_filled_until(None) is None
    assert parse_filled_until("что-то другое") is None


def test_некорректная_дата_игнорируется():
    assert parse_filled_until("заполнено до 45.13.2026") is None


def test_январь_следующего_года():
    assert parse_filled_until("заполнено до 10.01", today=date(2026, 12, 20)) == date(2027, 1, 10)


def test_внутри_и_вне_пометки():
    assert within_marker(date(2026, 11, 30), date(2026, 11, 30))
    assert not within_marker(date(2026, 12, 1), date(2026, 11, 30))
    assert within_marker(date(2027, 1, 1), None)


def test_разделение_слотов():
    inside_days = [25, 29, 30]
    slots = [Slot(day=date(2026, 11, d), hour=10, status="free") for d in inside_days]
    slots.append(Slot(day=date(2026, 12, 1), hour=10, status="free"))
    inside, outside = split_by_marker(slots, date(2026, 11, 30))
    assert len(inside) == 3
    assert [s.day for s in outside] == [date(2026, 12, 1)]
