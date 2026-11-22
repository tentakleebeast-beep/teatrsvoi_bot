"""Поиск человека: `/tools/participants/find` — один вход для бота и панели.

Раньше здесь же жили заявки и регистрация «выбери себя из списка». После
персонального кода (docs/development/182 §1.4, V-116 б) этот путь удалён:
`register` больше нет, а поиск остался только как чтение — он не привязывает
Telegram и не отдаёт личные поля.

Обоснование: FR-A3 (выбор себя), FR-A5, Ш-46 (V-006 — поиск по частям имени),
docs/development/182 §6.1.
"""

from __future__ import annotations

import psycopg
import pytest
from app.person_key import make_key

from tests.conftest import _test_dsn


def _insert_person(name: str, key: str | None = None, status: str = "active",
                   chat_id: int | None = None) -> int:
    key = make_key(name) if key is None else key
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        return conn.execute(
            """insert into participant (tg_chat_id, display_name, person_key, role,
                                        status, phone, birth_date)
               values (%s, %s, %s, 'troupe', %s, '+79000000000', '1990-01-01')
               returning id""", (chat_id, name, key, status)).fetchone()[0]


def _find(client, auth, query: str) -> dict:
    return client.get("/tools/participants/find", params={"q": query}, headers=auth).json()


def test_поиск_по_частям_имени_и_фамилии(client, auth):
    """Обращение №26: «Смирнова» должна находить «Дарья Смирнова», а не только точное имя."""
    _insert_person("Дарья Смирнова")
    body = _find(client, auth, "дарья смир")
    assert body["ok"] is True
    assert body["status"] == "resolved"
    assert body["person"]["display_name"] == "Дарья Смирнова"


def test_поиск_не_зависит_от_регистра_и_ё(client, auth):
    _insert_person("Пётр Ёлкин")
    body = _find(client, auth, "ПЕТР елкин")
    assert body["status"] == "resolved"
    assert body["person"]["display_name"] == "Пётр Ёлкин"


def test_поиск_видит_псевдонимы(client, auth):
    person_id = _insert_person("Виктория Тарасова")
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("""insert into person_alias (participant_id, alias, alias_kind, source)
                        values (%s, 'витя', 'short', 'manual')""", (person_id,))
    body = _find(client, auth, "витя")
    assert body["status"] == "resolved"
    assert body["person"]["id"] == person_id


def test_поиск_не_отдаёт_личное(client, auth):
    """В ответе нет телефона и даты рождения: поиск — не карточка человека."""
    person_id = _insert_person("Тайна Личная")
    body = _find(client, auth, "тайна личная")
    assert body["person"]["id"] == person_id
    assert "phone" not in body["person"]
    assert "birth_date" not in body["person"]


def test_поиск_не_привязывает_чужой_профиль(client, auth):
    """Поиск только показывает записи — chat_id остаётся пустым."""
    person_id = _insert_person("Дарья Смирнова")
    _find(client, auth, "смирнова")
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        assert conn.execute("select tg_chat_id from participant where id = %s",
                            (person_id,)).fetchone()[0] is None


def test_поиск_нескольких_требует_выбора(client, auth):
    """Два одинаково подходящих человека — `ambiguous`, интерфейс не берёт первого."""
    _insert_person("Иванова Анна", key="иванова-анна-1")
    _insert_person("Иванова Анна", key="иванова-анна-2")
    body = _find(client, auth, "иванова анна")
    assert body["status"] == "ambiguous"
    assert body["person"] is None
    assert len(body["candidates"]) == 2


def test_поиск_пустой_запрос_ничего_не_выбирает(client, auth):
    body = _find(client, auth, "")
    assert body["status"] == "empty"
    assert body["person"] is None and body["candidates"] == []


@pytest.mark.parametrize("query", ["нет такого человека", "ззззз"])
def test_поиск_неизвестного_не_находит(client, auth, query):
    body = _find(client, auth, query)
    assert body["status"] == "not_found"
    assert body["person"] is None
