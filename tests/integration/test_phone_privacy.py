"""
Телефон человека: полный номер — только свой или с правом `participants.contact`.

До этого номер отдавался всем, у кого есть чтение карточки: маскирования в проекте
не было вовсе.

Правила и требования раздела: docs/architecture/02-access-control.md.

Номера в фикстурах вымышленные (+7 917 123-24-18, +7 900 000-00-00): маскирование
проверяется на них, живой номер в тест не попадает.
"""

from __future__ import annotations

import psycopg
from app.person_key import make_key
from app.privacy import mask_phone

from tests.conftest import TOKEN, _test_dsn


def test_маска_оставляет_опознаваемое() -> None:
    assert mask_phone("+79171232418") == "+7 917 ··· 24 18"
    assert mask_phone("89171232418") == "+8 917 ··· 24 18"


def test_короткий_номер_не_маскируется_в_кашу() -> None:
    assert mask_phone("12345") == "12345"
    assert mask_phone(None) is None


def _person(conn, chat_id: int, name: str, role: str = "troupe", phone: str | None = None) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status, phone) "
        "values (%s,%s,%s,%s,'active',%s) returning id",
        (chat_id, name, make_key(name), role, phone)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                 (pid, role))
    conn.commit()
    return pid


def _headers(chat_id: int) -> dict:
    return {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": str(chat_id)}


def test_чужой_номер_приходит_маской(client) -> None:
    """Человек с чтением карточки, но без права на контакт: номер — маской."""
    with psycopg.connect(_test_dsn()) as conn:
        owner = _person(conn, 786001, "Владелец Номера", phone="+79171232418")
        reader = _person(conn, 786002, "Читатель Карточки")
        conn.execute(
            "insert into participant_capability_override "
            "(participant_id, capability, allowed, reason) values (%s,'participants.view',true,'тест')",
            (reader,))
        conn.commit()
    body = client.get(f"/tools/participants/{owner}/card", headers=_headers(786002)).json()
    assert body["data"]["phone"] == "+7 917 ··· 24 18"


def test_организатор_с_правом_видит_номер_целиком(client) -> None:
    """Организатору номер нужен по работе — право `participants.contact` у роли есть."""
    with psycopg.connect(_test_dsn()) as conn:
        owner = _person(conn, 786003, "Номер Для Звонка", phone="+79171232418")
        _person(conn, 786004, "Орг С Правом", role="org")
    body = client.get(f"/tools/participants/{owner}/card", headers=_headers(786004)).json()
    assert body["data"]["phone"] == "+79171232418"
