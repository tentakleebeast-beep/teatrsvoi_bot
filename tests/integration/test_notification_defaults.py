"""Общие настройки уведомлений и приоритет частных над общими (FR-G17, карта Ш-11).

П-8: общие значения задаёт администратор в админ-панели Mini App (О5), частные
остаются в профиле (О3) и важнее общих. Здесь проверяется всё, что нельзя увидеть
в диффе: право `admin.panel` на общий экран, наследование частных значений из общих
и то, что очередь отправки читает настройки по тому же правилу.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from app.person_key import make_key
from psycopg.rows import dict_row

from scripts import send_notifications as sender
from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import _api, init_data

TZ = timezone(timedelta(hours=4))            # Самара
DEFAULTS = [("notify_quiet_from", "20:00"), ("notify_quiet_to", "08:00"),
            ("notify_kinds_muted", "")]


def _connect():
    return psycopg.connect(_test_dsn(), row_factory=dict_row)


def _person(conn, chat_id: int, name: str, role: str = "troupe") -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,%s,'active') returning id",
        (chat_id, name, make_key(name), role)).fetchone()["id"]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                 (pid, role))
    conn.commit()
    return pid


def _token(client, chat_id: int) -> dict:
    token = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _queue(conn, recipient_id: int, at: datetime) -> int:
    """Одна обычная запись в очереди, готовая к отправке на момент `at`."""
    return conn.execute(
        "insert into notification (recipient_id, kind, payload, dedupe_key, scheduled_at) "
        "values (%s, 'feedback', %s, %s, %s) returning id",
        (recipient_id, psycopg.types.json.Jsonb({"title": "Обращение"}),
         f"prio:{recipient_id}", at - timedelta(minutes=5))).fetchone()["id"]


def _personal(conn, participant_id: int, quiet_from: str, quiet_to: str) -> None:
    conn.execute(
        "insert into notification_pref (participant_id, kinds_muted, quiet_from, quiet_to) "
        "values (%s, '{}', %s::time, %s::time)", (participant_id, quiet_from, quiet_to))


@pytest.fixture(autouse=True)
def _clean_defaults():
    """Общие значения — состояние «студии», а не мусор теста: возвращаем их после."""
    yield
    with _connect() as conn:
        conn.execute("delete from app_setting where key = any(%s)",
                     ([key for key, _ in DEFAULTS],))
        for key, value in DEFAULTS:
            conn.execute("insert into app_setting (key, value) values (%s, %s)", (key, value))
        conn.commit()


def test_админ_видит_и_меняет_общие_настройки(client):
    """О5: `admin.panel` открывает общий экран, значения сохраняются и попадают в аудит."""
    _api()
    with _connect() as conn:
        _person(conn, 783001, "Уведомления Админ", role="admin")
    headers = _token(client, 783001)

    first = client.get("/webapp/admin/notifications/settings", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["settings"] == {
        "rehearsals": True, "dues": True, "schedule_changes": True,
        "quiet_from": "20:00", "quiet_to": "08:00"}

    saved = client.post("/webapp/admin/notifications/settings", headers=headers,
                        json={"rehearsals": True, "dues": False, "schedule_changes": True,
                              "quiet_from": "21:30", "quiet_to": "07:15"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["settings"]["dues"] is False
    assert (saved.json()["settings"]["quiet_from"],
            saved.json()["settings"]["quiet_to"]) == ("21:30", "07:15")

    again = client.get("/webapp/admin/notifications/settings", headers=headers).json()
    assert again["settings"]["dues"] is False
    assert again["settings"]["quiet_from"] == "21:30"

    with _connect() as conn:
        events = conn.execute(
            "select action, before, after from audit_event "
            "where action = 'notify.defaults.set' order by id desc limit 1").fetchone()
    assert events is not None, "смена общих настроек не попала в аудит"
    assert events["after"]["quiet_from"] == "21:30"
    assert events["before"]["quiet_from"] == "20:00"


def test_участник_не_видит_общий_экран(client):
    """Участник без `admin.panel` получает отказ на оба запроса общего экрана."""
    _api()
    with _connect() as conn:
        _person(conn, 783002, "Уведомления Участник")
    headers = _token(client, 783002)

    body = {"rehearsals": True, "dues": True, "schedule_changes": True,
            "quiet_from": "21:00", "quiet_to": "09:00"}
    assert client.get("/webapp/admin/notifications/settings", headers=headers).status_code == 403
    assert client.post("/webapp/admin/notifications/settings", headers=headers,
                       json=body).status_code == 403


def test_частный_экран_наследует_общие_значения(client):
    """О3: пока частной настройки нет, профиль показывает общие значения студии."""
    _api()
    with _connect() as conn:
        _person(conn, 783003, "Уведомления Админ", role="admin")
        _person(conn, 783004, "Уведомления Актёр")
    admin = _token(client, 783003)
    actor = _token(client, 783004)

    client.post("/webapp/admin/notifications/settings", headers=admin,
                json={"rehearsals": False, "dues": True, "schedule_changes": True,
                      "quiet_from": "21:30", "quiet_to": "07:15"})

    inherited = client.get("/webapp/notifications/settings", headers=actor).json()["settings"]
    assert inherited["quiet_from"] == "21:30" and inherited["quiet_to"] == "07:15"
    assert inherited["rehearsals"] is False
    assert inherited["source"] == "defaults"

    saved = client.post("/webapp/notifications/settings", headers=actor,
                        json={"rehearsals": True, "dues": True, "schedule_changes": True,
                              "quiet_from": "23:00", "quiet_to": "06:00"}).json()["settings"]
    assert saved["source"] == "personal"
    assert saved["quiet_from"] == "23:00"

    # Общее значение не переписалось: смена частного — не смена студийного.
    still = client.get("/webapp/admin/notifications/settings", headers=admin).json()["settings"]
    assert still["quiet_from"] == "21:30"


def test_очередь_читает_частные_тихие_часы_вместо_общих(client, monkeypatch):
    """FR-G17: настроенные человеком тихие часы перекрывают общие при отправке."""
    _api()
    calls: list[tuple[int, str]] = []
    monkeypatch.setattr(sender, "send",
                        lambda chat_id, text: (calls.append((chat_id, text)), (True, ""))[1])
    moment = datetime(2026, 10, 28, 20, 30, tzinfo=TZ)     # общие тихие: 20:00–08:00

    with _connect() as conn:
        own = _person(conn, 783005, "Тихие Часы Свои")
        common = _person(conn, 783006, "Тихие Часы Общие")
        _personal(conn, own, "22:00", "07:00")
        own_note = _queue(conn, own, moment)
        common_note = _queue(conn, common, moment)
        conn.commit()

        result = sender.deliver(conn, moment, apply_changes=True, out=lambda *_: None)

        assert result["sent"] == 1 and result["postponed"] == 1
        assert [chat for chat, _ in calls] == [783005]          # свой интервал — можно
        statuses = {row["id"]: row["status"] for row in conn.execute(
            "select id, status from notification order by id").fetchall()}
        assert statuses[own_note] == "sent"
        assert statuses[common_note] == "scheduled"             # ждёт до 08:00
        wake = conn.execute("select scheduled_at from notification where id = %s",
                            (common_note,)).fetchone()["scheduled_at"]
        assert wake == datetime(2026, 10, 29, 8, 0, tzinfo=TZ)


def test_очередь_без_частных_применяет_общие_тихие_часы(client, monkeypatch):
    """FR-G17: без частной записи очередь берёт общие тихие часы студии, а не код."""
    _api()
    calls: list[tuple[int, str]] = []
    monkeypatch.setattr(sender, "send",
                        lambda chat_id, text: (calls.append((chat_id, text)), (True, ""))[1])
    moment = datetime(2026, 10, 28, 12, 15, tzinfo=TZ)     # 12:15 не тихо по умолчанию

    with _connect() as conn:
        # Админ сохраняет общие тихие часы 12:00–13:00 — днём, а не ночью.
        conn.execute("insert into app_setting (key, value) values (%s, %s) "
                     "on conflict (key) do update set value = excluded.value",
                     ("notify_quiet_from", "12:00"))
        conn.execute("insert into app_setting (key, value) values (%s, %s) "
                     "on conflict (key) do update set value = excluded.value",
                     ("notify_quiet_to", "13:00"))
        person = _person(conn, 783007, "Тихие Часы Студии")
        note = _queue(conn, person, moment)
        conn.commit()

        result = sender.deliver(conn, moment, apply_changes=True, out=lambda *_: None)

        assert result["sent"] == 0 and result["postponed"] == 1
        assert calls == []
        wake = conn.execute("select scheduled_at from notification where id = %s",
                            (note,)).fetchone()["scheduled_at"]
        assert wake == datetime(2026, 10, 28, 13, 0, tzinfo=TZ)
