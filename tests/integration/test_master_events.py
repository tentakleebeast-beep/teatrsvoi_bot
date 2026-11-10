"""Мастер и тренинг: состав по своей дисциплине и отказ по чужой (Э1, docs/architecture/140).

Г11 карты Э1 в двух частях: карточка тренинга по своей дисциплине строится и в составе
есть педагоги этой дисциплины; тот же запрос с чужой дисциплиной отклоняется, и запись
не создаётся.

Проверяется на живом API (`/tools/events/preview` и `/tools/events/assign`), потому что
правило стоит в двух местах: ошибка в списке `errors` у предпросмотра и `403` у записи.
Заявка (`slot_proposal`) при отказе не сохраняется — иначе предложение подтвердилось бы
в обход проверки.

Обоснование: FR-D3 (состав тренинга по дисциплине), FR-A1 (роль педагога),
docs/architecture/140-master-role.md §1 и §4.3, docs/architecture/51.
"""

from __future__ import annotations

import psycopg
from app.person_key import make_key

from tests.conftest import TOKEN, _test_dsn

HEADERS = {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": "701001"}
ADMIN_CHAT = 701000


class FakeEvents:
    """Календарь-заглушка: запись в Google в тестах не нужна, событие живёт в БД."""

    def insert(self, calendarId, body):
        return self

    def execute(self):
        return {"id": "gcal-master"}


class FakeService:
    def events(self):
        return FakeEvents()


def _participant(conn, chat_id: int, name: str, role: str = "troupe") -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s, %s, %s, %s, 'active') returning id",
        (chat_id, name, make_key(name), role)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s, %s)",
                 (pid, role))
    conn.commit()
    return pid


def _teach(conn, pid: int, code: str) -> None:
    conn.execute("insert into participant_discipline (participant_id, discipline_id, relation) "
                 "select %s, id, 'teacher' from discipline where code = %s "
                 "on conflict (participant_id, discipline_id) "
                 "do update set relation = 'teacher'", (pid, code))
    conn.commit()


def _setup(client, monkeypatch) -> tuple[int, int]:
    """Мастер с музыкой, педагог актёрского и участник; календарь и зал — заглушки."""
    from app import core as api

    monkeypatch.setattr(api, "_calendar", lambda: FakeService())
    monkeypatch.setattr(api, "CALENDAR_ID_DRAFT", "draft")
    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))

    with psycopg.connect(_test_dsn()) as conn:
        master = _participant(conn, 701001, "Мастер Музыки", role="master")
        _teach(conn, master, "music")
        actor = _participant(conn, 701002, "Актёр Теста")
        other = _participant(conn, 701003, "Педагог Актёрского")
        _teach(conn, other, "acting")
    return master, actor


def test_Г11_мастер_назначает_тренинг_по_своей_дисциплине(client, monkeypatch):
    """Карточка строится, в составе — педагоги музыки, запись создаётся."""
    _, actor = _setup(client, monkeypatch)

    body = client.post("/tools/events/preview", headers=HEADERS, json={
        "kind": "training", "discipline": "music", "date": "2026-12-28",
        "from_hour": 18, "to_hour": 20, "participants": [actor], "persist": True,
    }).json()

    assert body["ok"] is True, body["errors"]
    names = {person["name"] for person in body["card"]["participants"]}
    assert "Мастер Музыки" in names, "педагог своей дисциплины не попал в состав"
    assert "Педагог Актёрского" not in names
    assert body["card"]["discipline"] == "music"

    assigned = client.post("/tools/events/assign", headers=HEADERS,
                           json={"proposal_id": body["proposal_id"]})
    assert assigned.status_code == 200, assigned.text
    event_id = assigned.json()["event_id"]
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select discipline_id from event_plan where id = %s",
                           (event_id,)).fetchone()
        assert row[0] is not None, "тренинг потерял дисциплину"


def test_Г11_чужая_дисциплина_отклоняется_и_не_пишется(client, monkeypatch):
    """Предпросмотр — ошибка `discipline_not_taught`, назначение — 403, записи нет."""
    _setup(client, monkeypatch)

    body = client.post("/tools/events/preview", headers=HEADERS, json={
        "kind": "training", "discipline": "acting", "date": "2026-12-28",
        "from_hour": 18, "to_hour": 20, "participants": [], "persist": True,
    }).json()

    assert body["ok"] is False
    codes = {item["code"] for item in body["errors"]}
    assert "discipline_not_taught" in codes
    text = next(item["text"] for item in body["errors"]
                if item["code"] == "discipline_not_taught")
    assert "не ведёте" in text and "Музыка" in text, text
    assert body["proposal_id"] is None, "отказ не должен сохранять заявку"

    card = {"kind": "training", "title": "Тренинг", "date": "2026-12-28",
            "from_hour": 18, "to_hour": 20, "venue": "stage", "discipline": "acting",
            "participants": []}
    denied = client.post("/tools/events/assign", headers=HEADERS, json={"card": card})
    assert denied.status_code == 403
    assert denied.json()["code"] == "discipline_not_taught"

    with psycopg.connect(_test_dsn()) as conn:
        assert conn.execute("select count(*) from event_plan").fetchone()[0] == 0
        assert conn.execute("select count(*) from slot_proposal").fetchone()[0] == 0


def test_Г11_репетиция_мастера_дисциплины_не_требует(client, monkeypatch):
    """У репетиции дисциплины нет — правило про тренинг на неё не распространяется."""
    _, actor = _setup(client, monkeypatch)

    body = client.post("/tools/events/preview", headers=HEADERS, json={
        "kind": "rehearsal", "date": "2026-12-29", "from_hour": 10, "to_hour": 12,
        "participants": [actor],
    }).json()

    assert body["ok"] is True, body["errors"]
    assert "discipline_not_taught" not in {item["code"] for item in body["errors"]}


def test_Г11_администратор_не_ограничен_дисциплиной(client, monkeypatch):
    """Проверка про дисциплину не распространяется на администратора (140 §1)."""
    _, actor = _setup(client, monkeypatch)
    with psycopg.connect(_test_dsn()) as conn:
        _participant(conn, 701000, "Админ Полей", role="admin")

    admin = {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": str(ADMIN_CHAT)}
    body = client.post("/tools/events/preview", headers=admin, json={
        "kind": "training", "discipline": "acting", "date": "2026-12-28",
        "from_hour": 18, "to_hour": 20, "participants": [actor],
    }).json()

    assert body["ok"] is True, body["errors"]
    assert "discipline_not_taught" not in {item["code"] for item in body["errors"]}


def test_Г11_мастер_без_дисциплины_назначает_тренинг(client, monkeypatch):
    """V-062 (б), решение владельца 10.11.2026: тренинг без дисциплины разрешён.

    До ответа сервер отвечал «Укажите дисциплину тренинга: вы ведёте ни одной» и
    тренинг не назначался. Теперь мастер без единой дисциплины назначает тренинг:
    состав подтягивает всех педагогов (правило состава не менялось), карточка и
    экран несут предупреждение `discipline_missing`, а событие пишется с пустой
    дисциплиной. Отказ остаётся один — названная чужая дисциплина (тест выше).
    """
    from app import core as api

    monkeypatch.setattr(api, "_calendar", lambda: FakeService())
    monkeypatch.setattr(api, "CALENDAR_ID_DRAFT", "draft")
    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))
    with psycopg.connect(_test_dsn()) as conn:
        _participant(conn, 701004, "Мастер Без Дисциплины", role="master")
        actor = _participant(conn, 701005, "Актёр Без Дисциплины")
        teacher = _participant(conn, 701006, "Педагог Музыки")
        _teach(conn, teacher, "music")

    headers = {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": "701004"}
    body = client.post("/tools/events/preview", headers=headers, json={
        "kind": "training", "date": "2026-12-30", "from_hour": 18, "to_hour": 20,
        "participants": [actor], "persist": True,
    }).json()

    assert body["ok"] is True, body["errors"]
    assert "discipline_not_taught" not in {item["code"] for item in body["errors"]}
    assert any(w["code"] == "discipline_missing" for w in body["warnings"]), body["warnings"]
    names = {person["name"] for person in body["card"]["participants"]}
    assert "Педагог Музыки" in names, "состав без дисциплины не собрал педагогов"
    assert body["card"]["discipline"] is None
    assert body["proposal_id"], "заявка не сохранилась — назначать нечего"

    assigned = client.post("/tools/events/assign", headers=headers,
                           json={"proposal_id": body["proposal_id"]})
    assert assigned.status_code == 200, assigned.text
    event_id = assigned.json()["event_id"]
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select discipline_id, kind from event_plan where id = %s",
                           (event_id,)).fetchone()
        assert row[1] == "training"
        assert row[0] is None, "у тренинга без дисциплины появился предмет"
