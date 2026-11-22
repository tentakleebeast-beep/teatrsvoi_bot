"""«Новый участник»: запись без Telegram заводится там же, где выпускается код.

V-117 а (docs/development/182 §4.3.2): после персонального кода запись человека
нужна **до** кода, а заводить её было негде. Экран «Приглашения» делает оба шага:
`POST /webapp/admin/person-new` создаёт запись (`tg_chat_id is null`,
`status='active'`, `person_key` из имени, строка `participant_role`), затем
`POST /webapp/admin/invite` выписывает на неё код; вход этим кодом дописывает
`tg_chat_id` и оставляет запись активной.

Обоснование: FR-A2 (код приглашения), FR-A5 (роли), V-117 а,
docs/development/182 §4.3.2, §6.2 п. 7.
"""

from __future__ import annotations

import psycopg
from app.person_key import make_key
from psycopg.rows import dict_row

from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import _api, init_data


def _admin(client) -> dict:
    _api()
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        admin_id = conn.execute("select id from participant where tg_chat_id = %s",
                                (123456789,)).fetchone()["id"]
        conn.execute("insert into participant_role (participant_id, role) values (%s, 'admin') "
                     "on conflict do nothing", (admin_id,))
        conn.commit()
    token = client.post("/webapp/auth", json={"init_data": init_data(123456789)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _person(participant_id: int) -> dict | None:
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        return conn.execute("select * from participant where id = %s",
                            (participant_id,)).fetchone()


def test_person_new_создаёт_запись_без_telegram(client) -> None:
    headers = _admin(client)
    created = client.post("/webapp/admin/person-new",
                          json={"display_name": "Новиков Артём"}, headers=headers)
    assert created.status_code == 200, created.text
    body = created.json()
    pid = body["participant"]["id"]

    person = _person(pid)
    assert person["tg_chat_id"] is None
    assert person["status"] == "active"
    assert person["display_name"] == "Новиков Артём"
    assert person["person_key"] == make_key("Новиков Артём")
    with psycopg.connect(_test_dsn()) as conn:
        roles = [row[0] for row in conn.execute(
            "select role from participant_role where participant_id = %s", (pid,))]
    assert roles == ["troupe"], "роль по умолчанию — актёр"


def test_person_new_нормализует_пробелы(client) -> None:
    headers = _admin(client)
    created = client.post("/webapp/admin/person-new",
                          json={"display_name": "  Новикова   Анна  "}, headers=headers)
    assert created.status_code == 200, created.text
    assert created.json()["participant"]["display_name"] == "Новикова Анна"


def test_одно_слово_в_имени_отказ(client) -> None:
    headers = _admin(client)
    response = client.post("/webapp/admin/person-new", json={"display_name": "Артём"},
                           headers=headers)
    assert response.status_code == 400
    assert response.json()["code"] == "need_full_name"
    assert "Фамилия Имя" in response.json()["detail"]
    with psycopg.connect(_test_dsn()) as conn:
        assert conn.execute("select 1 from participant where person_key = %s",
                            (make_key("Артём"),)).fetchone() is None


def test_дубль_ключа_отказ_и_записи_нет(client) -> None:
    headers = _admin(client)
    first = client.post("/webapp/admin/person-new",
                        json={"display_name": "Сидоров Пётр"}, headers=headers)
    assert first.status_code == 200, first.text
    with psycopg.connect(_test_dsn()) as conn:
        before = conn.execute("select count(*) from participant").fetchone()[0]

    second = client.post("/webapp/admin/person-new",
                         json={"display_name": "Сидоров Пётр"}, headers=headers)
    assert second.status_code == 409
    assert second.json()["code"] == "person_key_taken"
    assert "найдите его поиском" in second.json()["detail"]
    with psycopg.connect(_test_dsn()) as conn:
        after = conn.execute("select count(*) from participant").fetchone()[0]
    assert after == before, "дубль всё-таки записался"


def test_роль_admin_валидацией_отклоняется(client) -> None:
    headers = _admin(client)
    response = client.post("/webapp/admin/person-new",
                           json={"display_name": "Новый Админ", "role": "admin"},
                           headers=headers)
    assert response.status_code == 422


def test_педагог_с_дисциплиной_пишет_связь_teacher(client) -> None:
    headers = _admin(client)
    created = client.post("/webapp/admin/person-new",
                          json={"display_name": "Педагог Музыки", "role": "master",
                                "discipline_code": "music"}, headers=headers)
    assert created.status_code == 200, created.text
    pid = created.json()["participant"]["id"]
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        row = conn.execute(
            """select d.code, pd.relation from participant_discipline pd
                 join discipline d on d.id = pd.discipline_id
                where pd.participant_id = %s""", (pid,)).fetchone()
    assert row is not None, "дисциплина не записана"
    assert row["code"] == "music" and row["relation"] == "teacher"


def test_созданный_человек_входит_кодом_и_становится_привязанным(client) -> None:
    headers = _admin(client)
    created = client.post("/webapp/admin/person-new",
                          json={"display_name": "Кузнецов Илья", "role": "org"},
                          headers=headers).json()
    pid = created["participant"]["id"]

    issued = client.post("/webapp/admin/invite", json={"participant_id": pid}, headers=headers)
    assert issued.status_code == 200, issued.text
    code = issued.json()["code"]
    assert issued.json()["display_name"] == "Кузнецов Илья"

    check = client.post("/tools/participants/check_code",
                        json={"chat_id": 795001, "code": code}).json()
    assert check["ok"] is True
    assert check["participant"]["display_name"] == "Кузнецов Илья"

    attached = client.post("/tools/participants/attach",
                           json={"chat_id": 795001, "code": code}).json()
    assert attached["ok"] is True
    person = _person(pid)
    assert person["tg_chat_id"] == 795001
    assert person["status"] == "active"

    with psycopg.connect(_test_dsn()) as conn:
        roles = [row[0] for row in conn.execute(
            "select role from participant_role where participant_id = %s", (pid,))]
    assert roles == ["org"]
