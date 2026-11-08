"""
Карточка дня занятости: состояние, две границы, комментарий (FR-C9).

Контракт один для чата, Mini App и листа: занятость дня — это интервал и состояние,
пустая граница означает край дня. Проверки держат три вещи, из-за которых ломается
доверие к занятости: потеря минут (ложное «свободен» на полчаса), дрейф при
перезаписи листа и путаница между «свободен» и «данных нет».
"""

from __future__ import annotations

import pathlib

import pytest
from app.availability_card import (
    DayCard,
    DayCardError,
    from_hours,
    parse,
    parse_clock,
    to_hours,
    to_text,
)

CORPUS = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "availability_cells.txt"


def corpus() -> list[str]:
    lines = CORPUS.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


@pytest.mark.parametrize(
    ("text", "canonical"),
    [
        ("занята до 19:00", "занят до 19:00"),
        ("занят до 18.30", "занят до 18:30"),
        ("занята до 16", "занят до 16:00"),
        ("свободно с 19:00", "свободен с 19:00"),
        ("свободен после семи", "свободен с 19:00"),
        ("занята с 16 до 18:30", "занят с 16:00 до 18:30"),
        ("свободен", "свободен"),
        ("занята весь день", "занят"),
        ("пока не понятно", "пока не понятно"),
    ],
)
def test_fr_c9_канон_для_всех_форм_записи(text: str, canonical: str) -> None:
    """FR-C9: любая человеческая формулировка пишется обратно одной канонической формой."""
    card = parse(text)
    assert card is not None
    assert to_text(card) == canonical


def test_fr_c9_женский_род_не_теряется_при_записи() -> None:
    """FR-C9: в листе пишут «занята», поэтому канон умеет женский род."""
    assert to_text(DayCard("busy", None, 19 * 60), female=True) == "занята до 19:00"
    assert to_text(DayCard("free", 18 * 60), female=True) == "свободна с 18:00"


def test_fr_c9_корпус_листа_переживает_перезапись() -> None:
    """FR-C9: 63 реальные формулировки из файла читаются и пишутся без дрейфа смысла."""
    values = corpus()
    assert len(values) == 63
    drift = []
    for value in values:
        card = parse(value)
        if card is None:
            drift.append((value, "не разобрано"))
            continue
        again = parse(to_text(card))
        if again != card:
            drift.append((value, to_text(card), card, again))
    assert drift == []


def test_fr_c9_минуты_не_теряются() -> None:
    """FR-C9: «до 18:30» — это 18:30, а не 18:00: иначе бот обещает занятому человеку слот."""
    card = parse("занята до 18:30")
    assert card is not None
    assert card.to_min == 18 * 60 + 30
    assert "18:30" in to_text(card)


def test_fr_c9_день_заполняется_целиком_без_дыр() -> None:
    """FR-C9: отмеченный день материализуется на все часы, дополнение интервала явное."""
    hours = to_hours(parse("занят до 19:00"))
    assert sorted(hours) == list(range(8, 24))
    assert [h for h, s in hours.items() if s == "busy"] == list(range(8, 19))
    assert [h for h, s in hours.items() if s == "free"] == list(range(19, 24))


def test_fr_c9_неотмеченный_день_это_не_свободен() -> None:
    """FR-C9: «нет данных» и «свободен» — разные вещи: пустая клетка даёт None, а не карточку."""
    assert parse(None) is None
    assert parse("   ") is None
    assert to_hours(parse("пока не понятно")) == {hour: "uncertain" for hour in range(8, 24)}


def test_fr_c9_занятый_интервал_расширяется_наружу() -> None:
    """FR-C9: округление в сторону занятости — лучше «занят», чем ложное «свободен»."""
    hours = to_hours(parse("занят с 18:30 до 19:30"))
    assert hours[18] == "busy"
    assert hours[19] == "busy"
    assert hours[17] == "free"


def test_fr_c9_свободное_окно_уже_часа_становится_неизвестным() -> None:
    """FR-C9: свободные полчаса не превращаются в «свободен» — статус остаётся неизвестным."""
    hours = to_hours(parse("свободен с 18:30 до 19:00"))
    assert hours[18] == "uncertain"
    assert hours[17] == "busy"
    assert hours[19] == "busy"


def test_fr_c9_перевёрнутый_интервал_отвергается() -> None:
    """FR-C9: «с 19 до 18» — ошибка ввода, а не тихая подстановка границ."""
    with pytest.raises(DayCardError, match="bad_interval"):
        DayCard("busy", 19 * 60, 18 * 60)
    with pytest.raises(DayCardError, match="bad_status"):
        DayCard("maybe", None, None)


def test_fr_c9_вопрос_это_сомнение_а_не_занятость() -> None:
    """FR-C9: «занята ?» в листе означает сомнение, а не занятость."""
    card = parse("занята ?")
    assert card is not None
    assert card.status == "uncertain"


def test_fr_c9_комментарий_уходит_в_скобки() -> None:
    """FR-C9: комментарий человека не смешивается с границей времени."""
    card = parse("занята (не будет в самаре)")
    assert card == DayCard("busy", None, None, "не будет в самаре")
    assert to_text(card) == "занят (не будет в самаре)"
    assert parse(to_text(card)) == card


@pytest.mark.parametrize(
    ("text", "minutes"),
    [("18:30", 18 * 60 + 30), ("18.30", 18 * 60 + 30), ("18-30", 18 * 60 + 30),
     ("18", 18 * 60), ("00:00", 0)],
)
def test_fr_c9_время_принимается_в_любой_записи(text: str, minutes: int) -> None:
    """FR-C9: API принимает «18:30», «18.30» и «18» — человек и клиент пишут по-разному."""
    assert parse_clock(text) == minutes


def test_fr_c9_плохое_время_это_ошибка_ввода() -> None:
    """FR-C9: «25:00» и «18:75» — отказ, а не тихая подстановка."""
    for bad in ("25:00", "18:75", "вечер", ""):
        with pytest.raises(DayCardError, match="bad_time"):
            parse_clock(bad)


def test_fr_c9_неуверенность_не_совмещается_с_границами() -> None:
    """FR-C9: «не знаю» с границей — противоречие входа, а не молчаливая потеря поля."""
    with pytest.raises(DayCardError, match="bad_bounds"):
        DayCard("uncertain", 18 * 60, None)


@pytest.mark.parametrize(
    ("text", "back"),
    [
        # через часы граница округляется в сторону занятости: 18:30 → 19:00
        ("занят до 18:30", DayCard("busy", None, 19 * 60)),
        # «занят с 19» и «свободен до 19» — одно и то же, карточка вернёт вторую форму
        ("занят с 19:00", DayCard("free", None, 19 * 60)),
        ("свободен", DayCard("free")),
        ("занят", DayCard("busy")),
        ("занят с 16:00 до 20:00", DayCard("busy", 16 * 60, 20 * 60)),
        ("пока не понятно", DayCard("uncertain")),
    ],
)
def test_fr_c9_день_читается_обратно_из_часов(text: str, back: DayCard) -> None:
    """FR-C9: часы → карточка восстанавливается, границы округлены в сторону занятости."""
    card = parse(text)
    assert card is not None
    assert from_hours(to_hours(card)) == back


def test_fr_c9_две_записи_об_одной_границе_равнозначны() -> None:
    """FR-C9: «занят до 19:00» и «свободен с 19:00» дают одинаковые часы."""
    busy_until = parse("занят до 19:00")
    free_after = parse("свободен с 19:00")
    assert busy_until is not None and free_after is not None
    assert to_hours(busy_until) == to_hours(free_after)


def test_fr_c9_пустой_день_это_none() -> None:
    """FR-C9: нет строк — нет карточки; «не отмечено» не превращается в «свободен»."""
    assert from_hours({}) is None


def test_fr_c9_два_окна_помечаются_как_приближение() -> None:
    """FR-C9: занятость, которая не выражается одной карточкой, отдаётся с exact=False."""
    hours = {hour: ("busy" if hour in (9, 10, 18, 19) else "free") for hour in range(8, 24)}
    card = from_hours(hours)
    assert card is not None
    assert card.exact is False
    # самый длинный непрерывный отрезок — свободный вечер 11:00–18:00
    assert card.status == "free"
    assert (card.from_min, card.to_min) == (11 * 60, 18 * 60)


@pytest.mark.parametrize(
    ("text", "canonical"),
    [
        ("занят с 19-30", "занят с 19:30"),
        ("занята до 18-30", "занят до 18:30"),
        ("занят с 16 до 18-30", "занят с 16:00 до 18:30"),
    ],
)
def test_fr_c9_дефис_в_времени_понимается(text: str, canonical: str) -> None:
    """FR-C9: «19-30» — то же, что «19:30», а не граница 19:00 с мусором в комментарии."""
    card = parse(text)
    assert card is not None
    assert card.note == ""
    assert to_text(card) == canonical
