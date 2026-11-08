"""Разбор занятости: docs/development/16 §4.5."""

from datetime import date

import pytest
from app.rules import parse_availability

# понедельник
TODAY = date(2026, 10, 26)


def test_среда_после_18():
    res = parse_availability("занят в среду после 18", today=TODAY)
    assert res["ok"] is True
    assert res["day"] == "2026-10-28"          # ближайшая среда
    assert (res["start"], res["end"]) == (18, 24)
    assert res["kind"] == "busy"


def test_свободен_с_10_до_14():
    res = parse_availability("свободен 28.10 с 10 до 14", today=TODAY)
    assert res["ok"] is True
    assert (res["start"], res["end"], res["kind"]) == (10, 14, "free")


def test_завтра_и_послезавтра():
    assert parse_availability("занят завтра", today=TODAY)["day"] == "2026-10-27"
    assert parse_availability("занят послезавтра", today=TODAY)["day"] == "2026-10-28"


def test_сегодня():
    assert parse_availability("свободен сегодня", today=TODAY)["day"] == "2026-10-26"


def test_без_даты_нужен_переспрос():
    res = parse_availability("занят после 18", today=TODAY)
    assert res["ok"] is False and res["error"] == "need_date"


def test_весь_день():
    res = parse_availability("занят в пятницу весь день", today=TODAY)
    assert (res["start"], res["end"]) == (8, 24)


def test_до_15():
    res = parse_availability("не могу в четверг до 15", today=TODAY)
    assert res["ok"] is True
    assert res["end"] == 15 and res["kind"] == "busy"


def test_некорректная_дата():
    res = parse_availability("занят 32.13", today=TODAY)
    assert res["ok"] is False


def test_метка_для_человека():
    res = parse_availability("занят в среду после 18", today=TODAY)
    assert res["label"] == "2026-10-28 18:00–23:00, занят"
