"""Разбор фразы назначения события (docs/architecture/51)."""

from datetime import date

from app.assign import describe, hour_span, parse_assignment

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница",
            "суббота", "воскресенье"]


def test_полная_фраза():
    a = parse_assignment("назначь репетицию с Витей и Людой в четверг в 20:00")
    assert a.kind == "rehearsal"
    assert a.weekday == 3
    assert a.hour == 20
    assert a.names == ["витей", "людой"]
    assert a.complete is True


def test_показ_и_тренинг():
    assert parse_assignment("поставь показ в пятницу в 19:00").kind == "show"
    assert parse_assignment("запланируй тренинг во вторник в 18:00").kind == "training"
    assert parse_assignment("создай аренду в субботу в 12:00").kind == "rental"


def test_длительность_числом():
    a = parse_assignment("назначь репетицию в среду в 18:00 на 3 часа")
    assert a.duration_hours == 3


def test_длительность_через_до():
    a = parse_assignment("назначь репетицию в среду в 18:00 до 22:00")
    assert a.duration_hours == 4


def test_короткие_дни_недели():
    a = parse_assignment("назначь репетицию в чт в 20")
    assert a.weekday == 3 and a.hour == 20


def test_помещение():
    assert parse_assignment("назначь репетицию в гримёрке в чт в 19:00").venue == "dressing"
    assert parse_assignment("назначь показ на сцене в пт в 19:00").venue == "stage"


def test_без_времени_отмечает_пробел():
    a = parse_assignment("назначь репетицию с Витей")
    assert "weekday" in a.problems and "hour" in a.problems
    assert a.complete is False


def test_описание_для_карточки():
    a = parse_assignment("назначь репетицию с Витей в четверг в 20:00")
    text = describe(a, WEEKDAYS)
    assert "Репетиция" in text and "четверг" in text and "20:00" in text


def test_интервал_времени_через_дефис():
    """«с 15-00 до 18-00» — часы работы, а не сумма: дефис в часах значит двоеточие."""
    a = parse_assignment("поставь встречу на завтра с Машей и Марком с 15-00 до 18-00")
    assert (a.hour, a.duration_hours) == (15, 3)
    assert a.relative_days == 1
    assert a.names == ["машей", "марком"]


def test_короткий_интервал_двумя_часами():
    """«15-18» — интервал из двух часов: правая часть больше 9, значит это тоже час."""
    a = parse_assignment("поставь встречу завтра 15-18")
    assert (a.hour, a.duration_hours) == (15, 3)


def test_интервал_словами():
    """«с трёх до шести» — часы словами; рабочий день проверяет сервер, не разбор."""
    a = parse_assignment("назначь встречу завтра с трёх до шести")
    assert (a.hour, a.duration_hours) == (3, 3)


def test_минуты_после_дефиса_не_становятся_интервалом():
    """«19-30» — это 19:30, а не интервал 19–30 часов: правая часть больше 23."""
    assert hour_span("в 19-30") is None
    assert parse_assignment("назначь репетицию завтра в 19-30").hour == 19


def test_явная_дата_числом():
    """Диалог обещает «5.11» — значит разбор обязан понимать дату числом."""
    a = parse_assignment("поставь встречу с Марком 5.11 в 15:00", today=date(2026, 11, 5))
    assert a.day == "2026-11-05"
    assert "weekday" not in a.problems
    assert a.hour == 15


def test_дата_с_годом_и_несуществующая():
    assert parse_assignment("встреча 31.12.2027 в 12:00").day == "2027-12-31"
    assert parse_assignment("встреча 31.02 в 12:00").day is None


def test_дата_числом_не_отнимает_участников():
    """«с Марком и Машей 5.11» — дата числом стоит после имён и не рвёт их захват.

    Раньше класс `[^,.;]` обрывался на точке даты, имена терялись, и диалог
    заканчивался отказом «Не понял, кто участвует» вместо карточки.
    """
    a = parse_assignment("поставь встречу с Марком и Машей 5.11 с 15:00 до 18:00",
                         today=date(2026, 11, 5))
    assert a.names == ["марком", "машей"]
    assert a.day == "2026-11-05"
    assert (a.hour, a.duration_hours) == (15, 3)
    assert a.complete is True


def test_дробное_число_часов_не_становится_датой():
    """«1.5 часа» — половина часа, а не 1 мая: «завтра» остаётся завтрашним днём."""
    a = parse_assignment("назначь репетицию завтра на 1.5 часа", today=date(2026, 11, 5))
    assert a.day is None
    assert a.relative_days == 1
    assert 1 <= a.duration_hours <= 2

    b = parse_assignment("назначь репетицию завтра на 2.5 часа с Витей",
                         today=date(2026, 11, 5))
    assert b.day is None and b.relative_days == 1
    assert b.names == ["витей"]


def test_явная_дата_сильнее_относительной():
    """Названы и «завтра», и «5.11» — точная дата главнее, день недели не подменяет."""
    a = parse_assignment("поставь встречу завтра 5.11 в 15:00", today=date(2026, 11, 5))
    assert a.day == "2026-11-05"
    assert a.relative_days == 1
