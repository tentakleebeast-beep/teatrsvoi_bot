"""
Порядок правил разбора свободного текста (docs/reports/27 §1.1, docs/development/28).

Тест держит поведение: какие ветки срабатывают и в каком порядке.
Именно ошибка порядка однажды отправила «Админ-панель» в модель.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tg-gateway" / "app"))

import routing  # noqa: E402


def test_занятость_раньше_бюджета():
    assert routing.candidates("занят в среду после 18")[0] == "availability"


def test_цифра_включает_бюджет():
    assert "budget" in routing.candidates("3000 на реквизит")
    assert routing.candidates("купили ткань 12000")[0] == "budget"


def test_кто_я_идёт_как_профиль():
    assert routing.candidates("кто я")[0] == "about_me"
    assert routing.candidates("мой профиль")[0] == "about_me"


def test_список_участников():
    assert "list_people" in routing.candidates("список зарегистрированных пользователей")
    assert "list_people" in routing.candidates("покажи всех участников")


def test_как_записать_расход_это_инструкция():
    names = routing.candidates("как записать расход")
    assert names[0] == "howto"        # вопрос-инструкция идёт раньше бюджета
    assert "budget" in names          # но слово «расход» ловит и правило бюджета
    # Удалённый is_howto различал «инструкция» и «данные» по цифре. Само различение
    # держит data_input_kind: инструкция данных не вводит, фраза с суммой — вводит.
    assert routing.data_input_kind("как записать расход") is None
    assert routing.data_input_kind("купили ткань 12000") == "budget"


def test_поиск_окон_распознаётся():
    assert "slots" in routing.candidates("найди окна на неделю")
    assert "slots" in routing.candidates("когда все свободны")
    assert "slots" in routing.candidates("когда можно собраться на репетицию")
    assert "slots" in routing.candidates("какие свободные часы есть")


def test_деньги_и_расписание_распознаются():
    assert "money" in routing.candidates("сколько у нас денег")
    assert "money" in routing.candidates("какой баланс студии")
    assert "schedule" in routing.candidates("что на неделе")
    assert "schedule" in routing.candidates("что в четверг")


def test_обычный_текст_без_правил():
    assert routing.candidates("привет") == []
    assert routing.candidates("") == []


def test_порядок_правил_фиксирован():
    # Ш-52: вопросы о существующем идут раньше ввода данных.
    # availability_q стоит перед availability: вопрос «кто сегодня занят» —
    # диалог, а не ввод своих данных (возврат ревью A-4, 02.11.2026).
    assert [name for name, _ in routing.RULES] == [
        "about_me", "list_people", "howto", "slots", "money", "schedule",
        "availability_q", "availability", "assign", "budget",
    ]


def test_назначение_не_становится_бюджетом_из_за_часа():
    """«назначь репетицию с Витей в четверг в 20» — это назначение, а не 20 ₽."""
    names = routing.candidates("назначь репетицию с Витей в четверг в 20")
    assert names[0] == "assign"
    assert "budget" not in names


def test_цифра_без_глагола_остаётся_бюджетом():
    assert "budget" in routing.candidates("3000 на реквизит")
    assert routing.candidates("потратили 12000")[0] == "budget"
