"""Политики окон: аренда, показ, репетиция, тренинг + дорога (docs/architecture/42)."""

from datetime import date

from app.window_policy import POLICIES, available_from, find_policy_windows, policy_for

MON = date(2026, 11, 23)


def free(hours):
    return {(MON, h): True for h in hours}


def busy(hours):
    return {(MON, h): False for h in hours}


def marks(**people):
    """marks(a=[18, 22], b=[8, 22]) — часы, когда человек свободен."""
    return {int(pid.lstrip("p")): free(range(start, end)) for pid, (start, end) in
            ((pid, value) for pid, value in people.items())}


def test_дорога_сдвигает_начало():
    person = {1: {**busy(range(8, 19)), **free(range(19, 23))}}
    # занят до 19:00, дорога час → реально может начать в 20:00
    assert available_from(person, 1, MON, travel_minutes=60) == 20
    assert available_from(person, 1, MON, travel_minutes=0) == 19


def test_дорога_без_времени_не_даёт_окна():
    person = {1: {**busy(range(8, 22)), **free(range(22, 23))}}
    assert available_from(person, 1, MON, travel_minutes=60) is None


def test_аренда_не_смотрит_на_людей():
    """Аренда без персонала людей не спрашивает вовсе (docs/architecture/40 §4).

    Политика людей на аренде не считает: раньше она вместо этого выдавала
    окно 08:00–10:00 при полностью занятой труппе, то есть «свободно» без
    единого события календаря. Теперь окно аренды считает зал (`app/venue.py`),
    и пустой ответ политики — правильный: людей тут нет.
    """
    everyone_busy = {1: busy(range(8, 23)), 2: busy(range(8, 23))}
    windows = find_policy_windows(everyone_busy, [MON], required=[1, 2],
                                  policy=POLICIES["rental"], min_hours=2)
    assert windows == [], "политика аренды не выдумывает окно из ничего"


def test_спектакль_требует_всех_с_начала():
    people = {
        1: {**busy(range(8, 19)), **free(range(19, 23))},
        2: {**busy(range(8, 20)), **free(range(20, 23))},
        3: free(range(8, 23)),
    }
    windows = find_policy_windows(people, [MON], required=[1, 2, 3],
                                  policy=POLICIES["show"], min_hours=2)
    assert windows is None or True
    # все трое вместе только с 21:00 (дорога: 1-й с 20, 2-й с 21)
    assert windows and windows[0].from_hour == 21


def test_репетиция_позволяет_подойти_позже():
    # двое свободны с 18:00, третий занят до 20:00 и сможет быть к 21:00
    people = {
        1: free(range(18, 23)),
        2: free(range(18, 23)),
        3: {**busy(range(8, 20)), **free(range(20, 23))},
    }
    # двое свободны с 18:00, но с дорогой реально с 19:00; третьему быть к 21:00.
    # Нужен блок 4 часа — начинаем в 19:00, третий подойдёт позже.
    windows = find_policy_windows(people, [MON], required=[1, 2], optional=[3],
                                  policy=POLICIES["rehearsal"], min_hours=4)
    assert windows
    first = windows[0]
    assert first.from_hour == 19
    assert dict(first.joining)[3] == 21, "третий подходит к 21:00 — это нормально"


def test_дорога_нужна_даже_если_свободен_с_утра():
    people = {1: free(range(8, 23)), 2: free(range(8, 23))}
    windows = find_policy_windows(people, [MON], required=[1, 2],
                                  policy=POLICIES["rehearsal"], min_hours=8)
    assert windows and windows[0].from_hour == 9, "оба едут, значит раньше 9 не начать"


def test_уже_в_театре_дороги_нет():
    people = {1: free(range(18, 23)), 2: free(range(18, 23))}
    theatre = {1: {(MON, 17)}, 2: {(MON, 17)}}     # оба были в театре в 17:00
    windows = find_policy_windows(people, [MON], required=[1, 2],
                                  policy=POLICIES["rehearsal"], min_hours=2,
                                  theatre_hours=theatre)
    assert windows and windows[0].from_hour == 18


def test_разное_время_в_пути():
    people = {1: free(range(18, 23)), 2: free(range(18, 23))}
    windows = find_policy_windows(people, [MON], required=[1, 2],
                                  policy=POLICIES["rehearsal"], min_hours=2,
                                  travel={1: 60, 2: 15})
    assert windows and windows[0].from_hour == 19, "дольше всех едет первый"


def test_аренда_с_персоналом_театра():
    people = {1: free(range(18, 23)), 2: busy(range(8, 23))}
    windows = find_policy_windows(people, [MON], required=[1, 2],
                                  policy=POLICIES["rental_staff"], min_hours=2)
    assert windows == [], "администратор события занят — без него нельзя"


def test_полное_окно_лучше_опоздавшего():
    people = {
        1: free(range(18, 23)),
        2: free(range(18, 23)),
        3: {**busy(range(8, 20)), **free(range(20, 23))},
    }
    windows = find_policy_windows(people, [MON], required=[1, 2], optional=[3],
                                  policy=POLICIES["rehearsal"], min_hours=2)
    assert windows[0].from_hour == 21 and not windows[0].joining


def test_тренинг_можно_потерять_человека():
    people = {
        1: free(range(18, 23)),
        2: free(range(18, 23)),
        3: free(range(18, 23)),
        4: busy(range(8, 23)),
        5: busy(range(8, 23)),
    }
    windows = find_policy_windows(people, [MON],
                                  required=[1, 2, 3, 4, 5],
                                  policy=POLICIES["training"], min_hours=2)
    assert windows, "тренинг можно провести без двух человек из пяти"
    assert len(windows[0].absent) == 2


def test_тренинг_не_теряет_больше_доли():
    people = {1: free(range(18, 23)), 4: busy(range(8, 23)),
              5: busy(range(8, 23)), 6: busy(range(8, 23))}
    windows = find_policy_windows(people, [MON], required=[1, 4, 5, 6],
                                  policy=POLICIES["training"], min_hours=2)
    assert windows == [], "1 из 4 меньше допустимых 60 %"


def test_политика_по_типу_события():
    assert policy_for("rental").ignores_people
    assert not policy_for("show").allow_late
    assert policy_for("training").allow_absent
    assert policy_for(None).kind == "rehearsal"


def test_приоритет_полного_состава():
    people = {
        1: free(range(18, 23)),
        2: free(range(18, 23)),
        3: {**busy(range(8, 19)), **free(range(19, 23))},
    }
    windows = find_policy_windows(people, [MON], required=[1, 2], optional=[3],
                                  policy=POLICIES["rehearsal"], min_hours=2, limit=3)
    assert windows[0].from_hour >= 20, "окно без опоздавших лучше"


def test_вложенные_окна_убираются_если_не_лучше():  # noqa: D103
    people = {1: free(range(18, 22)), 2: free(range(18, 22)),
              3: free(range(18, 22))}
    windows = find_policy_windows(people, [MON], required=[1, 2, 3],
                                  policy=POLICIES["rehearsal"], min_hours=2, limit=10)
    # все свободны весь вечер: одно окно (с дорогой с 19:00), а не набор 19–22 / 20–22
    assert [(w.from_hour, w.to_hour) for w in windows] == [(19, 22)]


def test_вложенное_окно_с_лучшим_составом_остаётся():
    people = {1: free(range(18, 23)), 2: free(range(18, 23)),
              3: {**busy(range(8, 20)), **free(range(20, 23))}}
    windows = find_policy_windows(people, [MON], required=[1, 2], optional=[3],
                                  policy=POLICIES["rehearsal"], min_hours=2, limit=10)
    spans = {(w.from_hour, w.to_hour): len(w.joining) for w in windows}
    assert any(join == 0 for join in spans.values()), "вариант без опоздавших нужен"
    assert any(join > 0 for join in spans.values()), "вариант с опоздавшим тоже полезен"


def test_дорога_два_часа_сдвигает_окно():
    people = {1: free(range(18, 23)), 2: free(range(18, 23))}
    windows = find_policy_windows(people, [MON], required=[1, 2],
                                  policy=POLICIES["rehearsal"], min_hours=2,
                                  travel={1: 0, 2: 120})
    assert windows and windows[0].from_hour == 20, "120 минут — два часа дороги"
