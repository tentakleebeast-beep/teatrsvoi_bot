"""
Поиск человека в интерфейсах: один анализ, три честных ответа.

`/tools/participants/find` — единственный вход для бота и админ-панели: «нашёл»,
«не нашёл» или «выберите». Первого из списка не отдаёт, даже если он первый
по алфавиту.

Правила и требования раздела: docs/architecture/52-twins-teachers-director.md.
"""

from __future__ import annotations

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn

PEOPLE = [
    (741001, "Смирнова Дарья", ["дарья", "даша"]),
    (741002, "Новиков Виктор", ["витя"]),
    (741003, "Иванов Виктор", ["витя"]),
]


def _seed() -> None:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        for chat_id, name, aliases in PEOPLE:
            pid = conn.execute(
                "insert into participant (tg_chat_id, display_name, person_key, role, status) "
                "values (%s,%s,%s,'troupe','active') returning id",
                (chat_id, name, make_key(name))).fetchone()[0]
            for alias in aliases:
                conn.execute("insert into person_alias (participant_id, alias, alias_kind) "
                             "values (%s,%s,'short')", (pid, alias))


def test_фамилия_даёт_одного_человека(client, auth) -> None:
    _seed()
    body = client.get("/tools/participants/find", params={"q": "смирнова"},
                      headers=auth).json()
    assert body["status"] == "resolved"
    assert body["person"]["display_name"] == "Смирнова Дарья"


def test_псевдоним_двух_людей_даёт_выбор(client, auth) -> None:
    _seed()
    body = client.get("/tools/participants/find", params={"q": "витя"},
                      headers=auth).json()
    assert body["status"] == "ambiguous"
    assert body["person"] is None
    names = {c["display_name"] for c in body["candidates"]}
    assert names == {"Новиков Виктор", "Иванов Виктор"}
    assert all(c["person_key"] for c in body["candidates"]), "кандидат без ключа не выбирается"


def test_имя_и_фамилия_снимают_неоднозначность(client, auth) -> None:
    _seed()
    body = client.get("/tools/participants/find", params={"q": "новиков виктор"},
                      headers=auth).json()
    assert body["status"] == "resolved" and body["person"]["display_name"] == "Новиков Виктор"


def test_ключ_находит_человека(client, auth) -> None:
    _seed()
    body = client.get("/tools/participants/find", params={"q": "иванов-виктор"},
                      headers=auth).json()
    assert body["status"] == "resolved" and body["person"]["display_name"] == "Иванов Виктор"


def test_неизвестное_имя_не_угадывается(client, auth) -> None:
    _seed()
    body = client.get("/tools/participants/find", params={"q": "семенов"},
                      headers=auth).json()
    assert body["status"] == "not_found" and body["person"] is None


def test_без_права_список_не_отдаётся(client) -> None:
    """Чужой человек не должен уметь перебирать участников запросами."""
    body = client.get("/tools/participants/find", params={"q": "смирнова"},
                      headers={"Authorization": "Bearer test-token",
                               "X-Actor-Chat-Id": "741001"})
    assert body.status_code in (403, 401)
