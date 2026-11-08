"""Отмена и перенос события из карточки чата (С2, С3; FR-D18, FR-D19).

Обоснование: FR-D18, FR-D19, карта t_dfb5f169 (Ш-06), docs/reports/124 §3.

Проверяется сквозной путь, а не факт вызова: кнопки карточки → подтверждение →
тот же `POST /tools/events/cancel|move`. Поэтому тест смотрит на следствие в базе:
`event_plan`, аудит `event.cancel`/`event.move` с прежним состоянием и уведомление
со связью «было → стало». До нажатия «Да» ничего не записано — это отдельная
проверка, иначе «подтверждение до записи» остаётся словом.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
import types
from datetime import timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from tests.conftest import TOKEN, _test_dsn

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY_APP = ROOT / "services" / "tg-gateway" / "app"
if str(GATEWAY_APP) not in sys.path:
    sys.path.insert(0, str(GATEWAY_APP))

CHAT = 784001
HEADERS = {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": str(CHAT)}


class FakeMessage:
    def __init__(self, chat_id: int = CHAT) -> None:
        self.chat = types.SimpleNamespace(id=chat_id)
        self.text = ""
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text, reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup))


class FakeCallback:
    def __init__(self, data: str, message: FakeMessage) -> None:
        self.data = data
        self.message = message
        self.alerts: list[str | None] = []

    async def answer(self, text=None, **kwargs) -> None:
        self.alerts.append(text)


class FakeState:
    def __init__(self) -> None:
        self.data: dict = {}
        self.state = None

    async def get_state(self):
        return self.state

    async def set_state(self, state) -> None:
        self.state = state

    async def get_data(self) -> dict:
        return dict(self.data)

    async def update_data(self, **kwargs) -> None:
        self.data.update(kwargs)

    async def clear(self) -> None:
        self.data.clear()
        self.state = None


def _buttons(markup) -> list[str]:
    if markup is None:
        return []
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def _actor() -> int:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        pid = conn.execute(
            "insert into participant (tg_chat_id, display_name, role, status) "
            "values (%s, 'Организатор Карточки', 'org', 'active') returning id",
            (CHAT,)).fetchone()[0]
        conn.execute("insert into participant_role (participant_id, role) values (%s, 'org')",
                     (pid,))
        conn.execute("""insert into participant_capability_override
                          (participant_id, capability, allowed, reason)
                        values (%s, 'schedule.write', true, 'тест карточки события')""",
                     (pid,))
    return pid


def _assign(client, participant_id: int, *, day: str = "2026-12-10",
            from_hour: int = 18, to_hour: int = 21) -> int:
    body = client.post("/tools/events/assign", headers=HEADERS, json={"card": {
        "kind": "rehearsal", "title": "Прогон карточки", "date": day,
        "from_hour": from_hour, "to_hour": to_hour, "venue": "stage",
        "participants": [{"id": participant_id, "name": "Организатор Карточки"}],
    }}).json()
    assert body["ok"] is True, body
    return body["event_id"]


def _wire(monkeypatch, client) -> None:
    """Колбэки gateway ходят в настоящий tool-api: core.api → TestClient."""
    import core as gateway_core  # noqa: PLC0415 — модуль gateway, не app.core

    async def fake_api(path, payload=None, chat_id=None, method="POST"):
        if method == "GET":
            response = client.get(path, headers=HEADERS)
        else:
            response = client.post(path, headers=HEADERS, json=payload or {})
        return response.json()

    monkeypatch.setattr(gateway_core, "api", fake_api)


def _plan(event_id: int) -> dict:
    with psycopg.connect(_test_dsn(), autocommit=True, row_factory=dict_row) as conn:
        return conn.execute("select status, starts_at, ends_at from event_plan "
                            "where id = %s", (event_id,)).fetchone()


def _audit(action: str, event_id: int) -> dict:
    with psycopg.connect(_test_dsn(), autocommit=True, row_factory=dict_row) as conn:
        return conn.execute(
            "select before, after from audit_event where action = %s and entity_id = %s "
            "order by id desc limit 1", (action, str(event_id))).fetchone()


def test_отмена_из_карточки_снимает_событие_и_пишет_аудит(client, monkeypatch):
    participant_id = _actor()
    event_id = _assign(client, participant_id)
    _wire(monkeypatch, client)
    from routers import events as chat  # noqa: PLC0415

    message = FakeMessage()
    state = FakeState()
    asyncio.run(chat.event_card(FakeCallback(f"evcard:{event_id}", message), state))
    text, markup = message.answers[-1]
    assert "Прогон карточки" in text
    assert "18:00–21:00" in text
    assert _buttons(markup) == [f"evcancel:{event_id}", f"evmove:{event_id}"]

    asyncio.run(chat.event_cancel_ask(FakeCallback(f"evcancel:{event_id}", message), state))
    confirm_text, confirm_markup = message.answers[-1]
    assert "Отменить событие?" in confirm_text
    assert f"evcancel:yes:{event_id}" in _buttons(confirm_markup)
    assert _plan(event_id)["status"] == "confirmed", "подтверждение до записи: ещё не снято"

    asyncio.run(chat.event_cancel_ask(FakeCallback(f"evcancel:yes:{event_id}", message), state))
    assert message.answers[-1][0].startswith("Отменено.")
    assert _plan(event_id)["status"] == "cancelled"

    record = _audit("event.cancel", event_id)
    assert record is not None, "отмена без аудита"
    assert record["before"]["status"] == "confirmed"
    assert record["before"]["title"] == "Прогон карточки"
    assert record["after"]["status"] == "cancelled"


def test_перенос_из_карточки_сохраняет_связь_было_стало(client, monkeypatch):
    participant_id = _actor()
    event_id = _assign(client, participant_id)
    _wire(monkeypatch, client)
    from routers import events as chat  # noqa: PLC0415

    message = FakeMessage()
    state = FakeState()
    asyncio.run(chat.event_move_ask(FakeCallback(f"evmove:{event_id}", message), state))
    ask_text = message.answers[-1][0]
    assert "На когда перенести?" in ask_text
    assert "18:00–21:00" in ask_text

    # без даты и без времени не угадываем: вопрос повторяется, записи нет
    message.text = "19:00"
    asyncio.run(chat.event_move_text(message, state))
    assert "Не понял дату и время" in message.answers[-1][0]
    assert _plan(event_id)["starts_at"].day == 10

    message.text = "11.12 19:00"
    asyncio.run(chat.event_move_text(message, state))
    confirm_text, confirm_markup = message.answers[-1]
    assert "Было: Четверг, 10.12, 18:00–21:00" in confirm_text
    assert "Станет: Пятница, 11.12, 19:00–22:00" in confirm_text
    assert "evmove:yes" in _buttons(confirm_markup)
    assert _plan(event_id)["starts_at"].day == 10, "до подтверждения время не меняется"

    asyncio.run(chat.event_move_ask(FakeCallback("evmove:yes", message), state))
    moved = _plan(event_id)
    local_start = moved["starts_at"].astimezone(timezone(timedelta(hours=4)))
    local_end = moved["ends_at"].astimezone(timezone(timedelta(hours=4)))
    assert local_start.date().isoformat() == "2026-12-11"
    assert local_start.hour == 19
    assert local_end.hour == 22

    record = _audit("event.move", event_id)
    assert record is not None, "перенос без аудита"
    assert record["before"]["starts_at"].startswith("2026-12-10T18:00")
    assert record["after"]["starts_at"].startswith("2026-12-11T19:00")

    with psycopg.connect(_test_dsn(), autocommit=True, row_factory=dict_row) as conn:
        note = conn.execute("select payload from notification where kind = 'event_moved' "
                            "order by id desc limit 1").fetchone()
    assert note is not None, "перенос без уведомления участникам"
    assert "было" in note["payload"]["body"] and "стало" in note["payload"]["body"]
