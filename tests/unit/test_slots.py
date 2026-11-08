"""Поиск окон в расписании (docs/architecture/17 §3.2, docs/architecture/40).

Ключевая проверка семантики: отсутствие отметки — «не знаю», а не «занят».
"""

from datetime import date

from app.slots import Window, coverage, describe, find_windows, longest_window, week_days

MON = date(2026, 11, 23)
TUE = date(2026, 11, 24)
WED = date(2026, 11, 25)


def free(day, hours) -> dict:
    """Отметки «свободен» на перечисленные часы."""
    return {(day, h): True for h in hours}


def busy(day, hours) -> dict:
    return {(day, h): False for h in hours}


def merge(*tables) -> dict:
    out: dict = {}
    for table in tables:
        out.update(table)
    return out


def test_все_свободны_весь_день():
    marks = {1: free(MON, range(8, 23)), 2: free(MON, range(8, 23))}
    found = find_windows(marks, [MON], min_hours=2)
    assert len(found) == 1
    assert (found[0].from_hour, found[0].to_hour) == (8, 23)
    assert found[0].hours == 15


def test_занятость_режет_окно():
    marks = {
        1: merge(free(MON, list(range(8, 14))), busy(MON, range(14, 16)),
                 free(MON, range(16, 23))),
        2: free(MON, range(8, 23)),
    }
    found = find_windows(marks, [MON], min_hours=2, limit=10)
    assert [(w.from_hour, w.to_hour) for w in found] == [(16, 23), (8, 14)]


def test_короткий_блок_отбрасывается():
    marks = {1: merge(free(MON, [8, 9]), busy(MON, [10, 11]), free(MON, range(12, 23))),
             2: free(MON, range(8, 23))}
    found = find_windows(marks, [MON], min_hours=3, limit=10)
    assert [(w.from_hour, w.to_hour) for w in found] == [(12, 23)]


def test_нет_общих_окон():
    marks = {1: merge(free(MON, range(8, 15)), busy(MON, range(15, 23))),
             2: merge(busy(MON, range(8, 15)), free(MON, range(15, 23)))}
    assert find_windows(marks, [MON], min_hours=1) == []


def test_пустая_клетка_не_блокирует_окно():
    """Главное правило: не отмечено ≠ занят."""
    marks = {
        1: free(MON, range(18, 23)),
        2: free(MON, range(18, 23)),
        3: busy(MON, [9, 10]),           # отметился только утром
    }
    found = find_windows(marks, [MON], min_hours=2)
    assert found, "человек без вечерних отметок не должен убивать окно"
    assert 3 in found[0].unknown


def test_окно_без_неизвестных_приоритетнее():
    marks = {
        1: merge(free(MON, range(18, 23)), free(TUE, range(18, 22))),
        2: merge(free(MON, range(18, 23)), free(TUE, range(18, 22))),
        3: free(TUE, range(18, 22)),     # в понедельник отметок нет, во вторник есть
    }
    found = find_windows(marks, [MON, TUE], min_hours=2, limit=5)
    assert found[0].day == TUE and found[0].unknown == ()


def test_нет_данных_вообще():
    assert find_windows({}, [MON]) == []
    assert find_windows({1: None}, [MON]) == []
    assert find_windows({1: {}}, [MON]) == []
    assert find_windows({1: free(MON, range(8, 23))}, []) == []


def test_вечер_приоритетнее_утра():
    marks = {1: merge(free(MON, range(8, 12)), busy(MON, range(12, 18)),
                      free(MON, range(18, 23)))}
    found = find_windows(marks, [MON], min_hours=2)
    assert found[0].from_hour == 18


def test_границы_дня():
    marks = {1: free(MON, range(0, 24))}
    found = find_windows(marks, [MON], min_hours=2, day_start=10, day_end=20)
    assert (found[0].from_hour, found[0].to_hour) == (10, 20)


def test_окно_до_конца_дня_закрывается():
    marks = {1: merge(busy(MON, range(8, 20)), free(MON, range(20, 23)))}
    found = find_windows(marks, [MON], min_hours=2)
    assert (found[0].from_hour, found[0].to_hour) == (20, 23)


def test_лимит_и_порядок():
    all_days = week_days(MON)
    everyone = {}
    for day in all_days:
        everyone.update(free(day, range(18, 22)))
    found = find_windows({1: everyone, 2: everyone}, all_days, min_hours=2, limit=3)
    assert len(found) == 3
    assert found[0].day == MON


def test_самое_длинное_окно():
    marks = {
        1: merge(free(MON, range(18, 21)), busy(TUE, range(8, 10)), free(TUE, range(10, 20))),
        2: merge(free(MON, range(18, 21)), busy(TUE, range(8, 10)), free(TUE, range(10, 20))),
    }
    best = longest_window(marks, [MON, TUE])
    assert best.day == TUE and best.hours == 10


def test_описание_читаемое():
    window = Window(day=TUE, from_hour=19, to_hour=21, unknown=(7,))
    text = describe(window, {7: "Лебедева"})
    assert "вторник" in text and "19:00–21:00" in text and "Лебедева" in text


def test_покрытие_часа():
    marks = {1: free(MON, [10]), 2: busy(MON, [10]), 3: None}
    assert coverage(marks, MON, 10) == (1, 1, 1)


def test_неуверенность_считается_занятостью():
    """«Не уверен» в файле хранится как is_free=false — час с ним в окно не попадает."""
    marks = {1: free(MON, range(18, 23)), 2: merge(free(MON, range(18, 20)),
                                                   busy(MON, [20, 21, 22]))}
    found = find_windows(marks, [MON], min_hours=2)
    assert [(w.from_hour, w.to_hour) for w in found] == [(18, 20)]
    assert all(not (w.from_hour <= 20 < w.to_hour) for w in found)


def test_время_без_единого_подтверждения_не_предлагается():
    """Нельзя предлагать 15:00, если никто про него ничего не говорил."""
    marks = {1: free(MON, range(18, 21)), 2: free(MON, range(18, 21))}
    found = find_windows(marks, [MON], min_hours=1, limit=10)
    assert all(w.from_hour >= 18 for w in found), "утро не подтверждено — не предлагаем"


def test_неизвестный_исчезает_если_подтвердил():
    marks = {1: free(MON, range(18, 22)), 2: busy(MON, [18]), }
    marks[2].update({(MON, h): True for h in range(19, 22)})
    found = find_windows(marks, [MON], min_hours=2)
    assert found[0].unknown == ()


def test_лучшие_часы_по_числу_свободных():
    from app.slots import rank_hours
    marks = {
        1: free(MON, range(10, 22)),
        2: free(MON, range(10, 22)),
        3: merge(free(MON, range(18, 22)), busy(MON, range(10, 18))),
    }
    best = rank_hours(marks, [MON], min_free=1, limit=12)
    # вечером свободны все трое, днём — только двое
    assert best[0].free_cnt == 3 and best[0].hour == 18
    assert [h.hour for h in best[:4]] == [18, 19, 20, 21]
    assert best[4].free_cnt == 2 and best[4].hour == 10


def test_максимум_свободных_учитывает_дату():
    from app.slots import rank_hours
    marks = {1: merge(free(MON, [19]), free(TUE, [19])),
             2: merge(free(MON, [19]), free(TUE, [19]))}
    best = rank_hours(marks, [TUE, MON], min_free=1, limit=2)
    assert best[0].day == MON, "при равном числе свободных раньше — лучше"


def test_никто_не_свободен_пусто():
    from app.slots import rank_hours
    assert rank_hours({1: busy(MON, range(8, 23))}, [MON]) == []
    assert rank_hours({}, [MON]) == []


def test_шестнадцатая_клетка_дня_участвует_в_окне():
    """Последняя клетка листа — 23:00–24:00 (docs/22 §1, docs/architecture/40 §2).

    Раньше поиск у людей кончался на 23:00 и отметка «свободен в 23:00»
    не влияла ни на что, хотя её можно поставить и она хранится.
    """
    marks = {1: free(MON, range(18, 24)), 2: free(MON, range(18, 24))}
    found = find_windows(marks, [MON], min_hours=1, limit=10)
    assert [(w.from_hour, w.to_hour) for w in found] == [(18, 24)]
    assert found[0].hours == 6


def test_занятость_в_23_рвёт_окно():
    marks = {1: free(MON, range(18, 24)), 2: merge(free(MON, range(18, 23)),
                                                   busy(MON, [23]))}
    found = find_windows(marks, [MON], min_hours=1, limit=10)
    assert [(w.from_hour, w.to_hour) for w in found] == [(18, 23)]


def test_молчание_в_одном_часе_окна_не_прячется():
    """Человек, ответивший про один час окна и промолчавший про другой,
    окно целиком не подтвердил: его видно в «нет данных» (docs/architecture/40 §3)."""
    marks = {1: free(MON, range(19, 22)), 2: {(MON, 19): True, (MON, 21): True}}
    found = find_windows(marks, [MON], min_hours=2, limit=5)
    assert found and (found[0].from_hour, found[0].to_hour) == (19, 22)
    assert 2 in found[0].unknown, "молчание про 20-й час не отменяется ответом про 21-й"


def test_минимум_часов_меньше_часа_это_ошибка():
    """Тихий пустой ответ на неверный критерий выглядел как «времени нет»."""
    import pytest

    with pytest.raises(ValueError):
        find_windows({1: free(MON, range(18, 22))}, [MON], min_hours=0)

