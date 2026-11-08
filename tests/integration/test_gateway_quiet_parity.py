"""Паритет чата и экрана на живой базе: тихие часы, «обычная неделя», день (карта AUD-5).

Юнит-тесты гейтвея проверяют, что чат зовёт те же пути, что экран; здесь проверяем
то, ради чего это делалось, — данные: записали из чата, прочитали экранным путём,
и наоборот. Источник значений один на две оболочки (`docs/architecture/25` §9.2 п.2).

Экранный путь — маршруты `/webapp/*`: в тестах подпись Mini App выдаётся тем же
`issue_token`, каким её выдаёт боевой `/webapp/session`, поэтому проверяется вся
цепочка «токен → участник → значение», а не только тело ответа.

Обоснование: FR-G3, FR-C9, FR-C10, карта t_0c0d023d (AUD-5), docs/architecture/25 §9.4.
"""

from __future__ import annotations

import datetime as dt

import psycopg
from app import core
from app.person_key import make_key
from app.webapp import issue_token

from tests.conftest import _test_dsn, service_today

CHAT = 793001


def _actor(conn, chat_id: int, name: str) -> int:
    """Участник с правом отмечать свою занятость и настраивать доставку."""
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,'troupe','active') returning id",
        (chat_id, name, make_key(name))).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')",
                 (pid,))
    conn.commit()
    return pid


def _chat_headers(chat_id: int = CHAT) -> dict:
    return {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(chat_id)}


def _screen_headers(chat_id: int = CHAT) -> dict:
    return {"Authorization": f"Bearer {issue_token(chat_id, core.WEBAPP_SECRET)}"}


def _monday() -> dt.date:
    today = service_today()
    return today - dt.timedelta(days=today.weekday())


# ------------------------------------------------------------------ тихие часы (О3)
def test_fr_g3_тихие_часы_чата_читаются_экраном(client):
    """FR-G3: сохранили в чате — экран видит тот же набор значений."""
    with psycopg.connect(_test_dsn()) as conn:
        _actor(conn, CHAT, "Тихие Часы")

    saved = client.post("/tools/notifications/settings", headers=_chat_headers(), json={
        "rehearsals": True, "dues": False, "schedule_changes": True,
        "quiet_from": "21:30", "quiet_to": "09:15"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["settings"]["quiet_from"] == "21:30"

    screen = client.get("/webapp/notifications/settings", headers=_screen_headers())
    assert screen.status_code == 200, screen.text
    assert screen.json()["settings"] == saved.json()["settings"], (
        "чат и экран разошлись в тихих часах")


def test_fr_g3_настройка_с_экрана_видна_чату(client):
    """FR-G3: обратное направление — экран сохранил, чат прочитал то же самое."""
    with psycopg.connect(_test_dsn()) as conn:
        _actor(conn, CHAT, "Тихие Обратно")

    posted = client.post("/webapp/notifications/settings", headers=_screen_headers(), json={
        "rehearsals": False, "dues": True, "schedule_changes": False,
        "quiet_from": "22:00", "quiet_to": "07:30"})
    assert posted.status_code == 200, posted.text

    chat = client.get("/tools/notifications/settings", headers=_chat_headers())
    assert chat.status_code == 200, chat.text
    # `source` — новый ключ ответа (FR-G17): чат и экран зовут один обработчик,
    # поэтому он приходит обеим оболочкам одинаково.
    assert chat.json()["settings"] == {"rehearsals": False, "dues": True,
                                       "schedule_changes": False,
                                       "quiet_from": "22:00", "quiet_to": "07:30",
                                       "source": "personal"}


def test_fr_g3_тихие_часы_личные_а_не_общие(client):
    """FR-G3: настройка одного человека не превращается в общую строку."""
    with psycopg.connect(_test_dsn()) as conn:
        first = _actor(conn, CHAT, "Первый")
        _actor(conn, CHAT + 1, "Второй")

    client.post("/tools/notifications/settings", headers=_chat_headers(), json={
        "rehearsals": True, "dues": True, "schedule_changes": True,
        "quiet_from": "20:00", "quiet_to": "08:00"})
    with psycopg.connect(_test_dsn()) as conn:
        rows = conn.execute("select participant_id from notification_pref").fetchall()
    assert [row[0] for row in rows] == [first], "настройка легла одному человеку"


# ------------------------------------------------- состояния дня и комментарий (З1)
def test_fr_c9_день_из_чата_читается_экраном(client):
    """FR-C9: «не уверен» и комментарий, записанные путём чата, экран видит карточкой."""
    monday = _monday()
    day = (monday + dt.timedelta(days=2)).isoformat()
    with psycopg.connect(_test_dsn()) as conn:
        _actor(conn, CHAT, "День Из Чата")

    written = client.post("/tools/availability/week", headers=_chat_headers(), json={
        "start": monday.isoformat(),
        "days": [{"day": day, "status": "uncertain", "note": "может, смогу"}]})
    assert written.status_code == 200, written.text

    screen = client.get(f"/webapp/availability/week?start={monday.isoformat()}",
                        headers=_screen_headers())
    assert screen.status_code == 200, screen.text
    card = next(d for d in screen.json()["days"] if d["day"] == day)
    assert card["status"] == "uncertain"
    assert card["note"] == "может, смогу"


def test_fr_c9_свободен_до_часа_из_чата_виден_экраном(client):
    """FR-C9: «свободен до 15:00» из формы чата — та же карточка на экране."""
    monday = _monday()
    day = (monday + dt.timedelta(days=1)).isoformat()
    with psycopg.connect(_test_dsn()) as conn:
        _actor(conn, CHAT, "Свободен Из Чата")

    client.post("/tools/availability/week", headers=_chat_headers(), json={
        "start": monday.isoformat(),
        "days": [{"day": day, "status": "free", "to_time": "15:00"}]})
    screen = client.get(f"/webapp/availability/week?start={monday.isoformat()}",
                        headers=_screen_headers()).json()
    card = next(d for d in screen["days"] if d["day"] == day)
    assert card["status"] == "free" and card["to"] == "15:00"


# ------------------------------------------------------ «обычная неделя» (З5, FR-C10)
def test_fr_c10_обычная_неделя_запомненная_из_чата_видна_экрану(client):
    """FR-C10: шаблон, запомненный из чата, экран читает тем же путём."""
    monday = _monday()
    with psycopg.connect(_test_dsn()) as conn:
        _actor(conn, CHAT, "Обычная Неделя")

    client.post("/tools/availability/week", headers=_chat_headers(), json={
        "start": monday.isoformat(),
        "days": [{"day": (monday + dt.timedelta(days=0)).isoformat(),
                  "status": "busy", "to_time": "18:00"},
                 {"day": (monday + dt.timedelta(days=2)).isoformat(),
                  "status": "free", "from_time": "10:00"}]})

    remembered = client.post("/tools/availability/template/from-week", headers=_chat_headers(),
                             json={"start": monday.isoformat()})
    assert remembered.status_code == 200, remembered.text
    assert remembered.json()["filled"] == 2

    screen = client.get("/webapp/availability/template", headers=_screen_headers())
    assert screen.status_code == 200, screen.text
    days = {d["weekday"]: d for d in screen.json()["days"]}
    assert days[0]["status"] == "busy" and days[0]["to"] == "18:00"
    assert days[2]["status"] == "free" and days[2]["from"] == "10:00"
