"""
Выбор человека по имени: до 100% уверенности, иначе «выберите».

Запрос может быть псевдонимом, именем, фамилией, куском слова или ключом.
Ответ бывает только трёх видов: найден, не найден, нужно выбрать.

Правила и требования раздела: docs/architecture/52-twins-teachers-director.md.
"""

from __future__ import annotations

from app.person_match import LEVEL_ROLE, normalize, rank, resolve, score


def person(pid, name, key=None, aliases=(), role_aliases=()):
    return {"id": pid, "display_name": name,
            "person_key": key or normalize(name).replace(" ", "-"),
            "aliases": list(aliases), "role_aliases": list(role_aliases)}


PEOPLE = [
    person(24, "Смирнова Дарья", aliases=["дарья", "даша"]),
    person(23, "Новиков Виктор", aliases=["витя"]),
    person(30, "Иванов Виктор", aliases=["витя", "виктор"]),
    person(16, "Лебедева Мария", role_aliases=["сэм"]),
]


def test_фамилия_находит_человека() -> None:
    result = resolve(PEOPLE, "смирнова")
    assert result["status"] == "resolved" and result["person"]["id"] == 24


def test_имя_находит_человека() -> None:
    assert resolve(PEOPLE, "дарья")["person"]["id"] == 24


def test_псевдоним_находит_человека() -> None:
    assert resolve(PEOPLE, "даша")["person"]["id"] == 24


def test_ключ_находит_человека() -> None:
    assert resolve(PEOPLE, "иванов-виктор")["person"]["id"] == 30


def test_кусок_слова_находит_человека() -> None:
    assert resolve(PEOPLE, "смирн")["person"]["id"] == 24


def test_полное_имя_точнее_одного_слова() -> None:
    """«смирнова дарья» — тот же человек, но уверенность выше, чем у «смирнова»."""
    assert score("смирнова дарья", PEOPLE[0]) > score("смирнова", PEOPLE[0])


def test_два_одинаковых_псевдонима_это_выбор() -> None:
    """«Витя» — два человека: не берём первого, показываем кандидатов."""
    result = resolve(PEOPLE, "витя")
    assert result["status"] == "ambiguous"
    assert {c["id"] for c in result["candidates"]} == {23, 30}
    assert result["person"] is None


def test_фамилия_разводит_одинаковые_имена() -> None:
    """«новиков виктор» снимает неоднозначность «витя»."""
    assert resolve(PEOPLE, "новиков виктор")["person"]["id"] == 23
    assert resolve(PEOPLE, "иванов")["person"]["id"] == 30


def test_неизвестное_имя_не_угадывается() -> None:
    assert resolve(PEOPLE, "семенов")["status"] == "not_found"
    assert resolve(PEOPLE, "   ")["status"] == "empty"


def test_роль_ниже_человека_и_не_перебивает_имя() -> None:
    """«Сэм» — роль; если есть человек с таким именем, выигрывает человек."""
    people = PEOPLE + [person(99, "Сэм Смирнов")]
    role_only = resolve([PEOPLE[3]], "сэм")
    assert role_only["status"] == "resolved"
    assert role_only["score"] <= LEVEL_ROLE
    assert resolve(people, "сэм")["person"]["id"] == 99


def test_ранжирование_лучшие_первыми() -> None:
    ranked = rank(PEOPLE, "виктор")
    assert ranked[0]["id"] == 30            # псевдоним «виктор» — точное слово
    assert all(ranked[i]["score"] >= ranked[i + 1]["score"] for i in range(len(ranked) - 1))
