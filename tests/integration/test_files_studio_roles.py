"""Кто видит «Файлы студии»: admin, org и master — и никто больше (V-039 (в)).

Решение владельца от 08.11.2026 (docs/reports/74-questions.md, V-039, вариант «в»):
раздел `/webapp/files` открыт администратору, организатору и мастеру. До правки экран и
данные были закрыты `admin.panel`, поэтому org и master читали «Раздел доступен
администратору», хотя право `files.access` у них уже было (docs/development/141 §8 п. 4).

Здесь проверяется не текст, а поведение на живой схеме: одно и то же право
(`files.access`) открывает строку «Личного» на «Ещё» и данные раздела, а у студийца
этого права нет — миграция 062 сняла его у роли, и раздел остаётся закрытым словами
(принцип Б3: состояние «нет права» — часть дизайна, а не пустой список).

Обоснование: V-039 (в) `docs/reports/74-questions.md`, FR-K10, `docs/development/86`.
"""

from __future__ import annotations

import psycopg
import pytest

from tests.conftest import _test_dsn
from tests.integration.test_files_actions_screen import _file, _webapp_headers
from tests.integration.test_webapp_profile import init_data

ROLES = ("admin", "org", "master", "troupe")


def _chat_headers(chat_id: int) -> dict:
    """Вход в чат: тем же человеком, что и в Mini App, — чтобы сравнить два входа."""
    return {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(chat_id)}


def _person(chat_id: int, role: str) -> None:
    """Вход человека с ролью: права считаются по `participant_role`, а не по колонке."""
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status)"
            " values (%s,%s,%s,%s,'active') on conflict (tg_chat_id) do nothing",
            (chat_id, f"Человек {chat_id}", f"человек-{chat_id}", role))
        conn.execute(
            "insert into participant_role (participant_id, role)"
            " select id, %s from participant where tg_chat_id = %s on conflict do nothing",
            (role, chat_id))
        conn.commit()


@pytest.fixture()
def people(client):
    """Четыре роли: по человеку на каждую, со своим номером чата."""
    ids = {"admin": 559001, "org": 559002, "master": 559003, "troupe": 559004}
    for role, chat_id in ids.items():
        _person(chat_id, role)
    return ids


def test_право_выдано_ровно_трём_ролям(client):
    """`files.access` в данных: admin, org, master. Студийцу снято миграцией 062."""
    with psycopg.connect(_test_dsn()) as conn:
        rows = conn.execute(
            "select role from rbac_role_capability where capability = 'files.access'"
        ).fetchall()
    holders = {row[0] for row in rows}
    assert holders == {"admin", "org", "master"}, holders


def test_раздел_открыт_трём_ролям_и_закрыт_студийцу(client, people):
    """Одно право — один ответ: 200 у admin/org/master, 403 у troupe."""
    for role, chat_id in people.items():
        headers = _webapp_headers(client, chat_id)
        listed = client.get("/webapp/files/list", headers=headers)
        if role == "troupe":
            assert listed.status_code == 403, f"{role} получил доступ к файлам"
            continue
        assert listed.status_code == 200, f"{role}: {listed.text}"


def test_строка_на_ещё_приходит_тем_же_трём_ролям(client, people):
    """Строку «Файлы студии» на «Ещё» отдаёт тот же реестр `SECTIONS` по тому же праву."""
    for role, chat_id in people.items():
        token = client.post("/webapp/auth",
                            json={"init_data": init_data(chat_id)}).json()["token"]
        body = client.get("/webapp/home/data",
                          headers={"Authorization": f"Bearer {token}"}).json()
        urls = [row["url"] for row in body["more"]["personal"]]
        if role == "troupe":
            assert "/webapp/files" not in urls, f"{role}: строка файлов без права"
            continue
        assert "/webapp/files" in urls, f"{role}: строки файлов нет"


def test_ссылка_на_просмотр_открыта_тем_же_трём_ролям(client, people, monkeypatch):
    """А3, К5: `/view` — то же право, что у раздела; у студийца его нет.

    Ссылку на свой файл получает каждая роль с `files.access` (admin, org, master),
    а `troupe` упирается в 403: иначе просмотр стал бы лазейкой в закрытый раздел.
    """
    from app import files_store

    class _Signer:
        def generate_presigned_url(self, operation: str, Params: dict, ExpiresIn: int) -> str:
            return f"https://files.example.test/{Params['Key']}?X-Amz-Signature=test"

    monkeypatch.setattr(files_store, "public_client", lambda: _Signer())
    for role, chat_id in people.items():
        file_id = _file(chat_id, f"файл {role}.pdf")
        response = client.get(f"/webapp/files/{file_id}/view",
                              headers=_webapp_headers(client, chat_id))
        if role == "troupe":
            assert response.status_code == 403, f"студиец открыл файл: {response.text}"
            continue
        assert response.status_code == 200, f"{role}: {response.text}"


def test_история_доступов_видна_владельцу_и_админу_но_не_мастеру(client, people):
    """Право на раздел не открывает историю доступов к чужому личному файлу (FR-K9).

    Экранный маршрут обязан повторять канон `GET /files/{id}/access`: владелец — 200,
    админ с открытым доступом — 200, чужой закрытый файл — 404 (не 403, иначе перебором
    `file_id` видно, какие файлы существуют — OWASP API1:2023). Иначе org и master
    читали бы имя администратора, причину и срок доступа к личной смете.
    """
    owner_chat = people["org"]
    admin_chat = people["admin"]
    file_id = _file(owner_chat, "смета организатора.pdf")
    assert client.post(f"/admin/files/{file_id}/access",
                       json={"reason": "сверка сметы"}, headers=_chat_headers(admin_chat)
                       ).status_code == 200

    owner = client.get(f"/webapp/files/{file_id}/access",
                       headers=_webapp_headers(client, owner_chat))
    assert owner.status_code == 200 and owner.json()["count"] == 1

    admin = client.get(f"/webapp/files/{file_id}/access",
                       headers=_webapp_headers(client, admin_chat))
    assert admin.status_code == 200 and admin.json()["count"] == 1

    master = client.get(f"/webapp/files/{file_id}/access",
                        headers=_webapp_headers(client, people["master"]))
    assert master.status_code == 404, f"мастер прочитал историю чужого файла: {master.text}"
    assert master.json()["code"] == "file_not_found"

    missing = client.get("/webapp/files/99999999/access",
                         headers=_webapp_headers(client, people["master"]))
    assert missing.status_code == 404, "чужой и несуществующий файл отвечают по-разному"

    troupe = client.get(f"/webapp/files/{file_id}/access",
                        headers=_webapp_headers(client, people["troupe"]))
    assert troupe.status_code == 403, "у студийца нет даже раздела — маршрут обязан отказать"


def test_история_доступов_к_общему_файлу_только_у_владельца_и_админа(client, people):
    """Общий файл виден всем с правом раздела, а история его доступов — нет: 403 (FR-K9)."""
    owner_chat, master_chat = people["org"], people["master"]
    file_id = _file(owner_chat, "общая афиша.pdf")
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("update file_object set scope = 'shared' where id = %s", (file_id,))
        conn.commit()

    master = client.get(f"/webapp/files/{file_id}/access",
                        headers=_webapp_headers(client, master_chat))
    assert master.status_code == 403, f"мастер прочитал историю общего файла: {master.text}"
    assert master.json()["code"] == "access_forbidden"

    chat = client.get(f"/files/{file_id}/access", headers=_chat_headers(master_chat))
    assert chat.status_code == 403, "экран и чат отвечают по-разному"

    owner = client.get(f"/webapp/files/{file_id}/access",
                       headers=_webapp_headers(client, owner_chat))
    assert owner.status_code == 200
