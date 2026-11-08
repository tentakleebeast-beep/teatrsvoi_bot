"""Задача агента: план, вход в приложение, отмена и лимит шагов (карта «Озадачить»).

Проверяем поведение кода, а не модели: провайдер подменён транспортом с готовыми
ответами `tool_calls`, база — тестовая.

  * карточка плана собирается из нескольких предложений, исполнение пишет строки
    в БД по порядку и останавливается на первой ошибке, называя шаг;
  * нехватка данных возвращает вход в Mini App (`kind: open`), а не догадку;
  * «Обнулить» прекращает задачу между шагами;
  * девятый шаг не начинается: задача говорит «не разобрался», а не выдумывает итог;
  * итог несёт строку шагов «дата → занятость».

Обоснование: FR-L2, FR-L5, AC «Озадачить» (docs/development/108 §4, §8).
"""

from __future__ import annotations

import asyncio
import json

import psycopg
import pytest
from app.agently import loop, plans, proposals, tasks
from conftest import ADMIN_CHAT
from psycopg.rows import dict_row


@pytest.fixture()
def conn(test_db: str):
    with psycopg.connect(test_db, row_factory=dict_row) as connection:
        yield connection


@pytest.fixture(autouse=True)
def _clean_tasks():
    tasks.clear_all()
    yield
    tasks.clear_all()


@pytest.fixture(autouse=True)
def _no_classifier(monkeypatch):
    monkeypatch.setenv("AGENT_CLASSIFIER", "0")


def _person(conn, chat_id: int, name: str, role: str = "troupe") -> dict:
    row = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status)"
        " values (%s,%s,%s,%s,'active') returning id, display_name, role, status",
        (chat_id, name, f"person-{chat_id}", role)).fetchone()
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)"
                 " on conflict do nothing", (row["id"], role))
    # Своё соединение видно только после commit: обработчики агента открывают
    # другое соединение из пула и иначе «не видят» людей (skill: грабли тестов).
    conn.commit()
    return dict(row) | {"roles": [role], "id": row["id"]}


def _admin(conn) -> dict:
    row = conn.execute("select id, display_name, role from participant"
                       " where tg_chat_id = %s", (ADMIN_CHAT,)).fetchone()
    return dict(row) | {"roles": ["admin"], "caps": ["*"]}


def _caps(who: dict) -> dict:
    return who | {"caps": who.get("caps") or ["*"]}


def _tool_call(name: str, args: dict, call_id: str = "call-1") -> dict:
    return {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": call_id, "type": "function",
         "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}}],
        "usage": {"total_tokens": 10}}


def _answer(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}], "usage": {"total_tokens": 5}}


def _transport(*answers, on_call=None):
    state = {"index": 0}

    def transport(payload):
        index = min(state["index"], len(answers) - 1)
        state["index"] += 1
        if on_call is not None:
            on_call(state["index"], payload)
        return answers[index]

    return transport


def _event_args(day: str, from_hour: int, people: list[str]) -> dict:
    return {"kind": "rehearsal", "date": day, "from_hour": from_hour,
            "duration_hours": 2, "people": people}


def test_план_из_двух_событий_исполняется_и_пишет_в_базу(client, auth, conn) -> None:
    who = _admin(conn)
    _person(conn, 570001, "Марк")
    _person(conn, 570002, "Маша")
    day = "2026-11-19"
    transport = _transport(
        _tool_call("propose_event", _event_args(day, 18, ["Марк", "Маша"]), "c1"),
        _tool_call("propose_event", _event_args(day, 20, ["Марк"]), "c2"),
        _answer("Собрал план: две репетиции."))

    result = asyncio.run(loop.run(conn, who, "собери две репетиции", transport=transport,
                                  task=True))

    assert result["ok"] is True
    assert result["plan"], [(s["tool"], s["error"], s.get("message")) for s in result["steps"]]
    assert result["propose"] is None, "несколько предложений — это план, не одна карточка"

    created = client.post("/tools/agent/plan",
                          json={"chat_id": ADMIN_CHAT,
                                "steps": result["plan"]["steps"]},
                          headers=auth)
    assert created.status_code == 200 and created.json()["ok"] is True
    token = created.json()["token"]
    assert "Назначить" in created.json()["plan"]["human_text"]

    done = client.post("/tools/agent/plan/confirm",
                       json={"chat_id": ADMIN_CHAT, "token": token}, headers=auth)
    body = done.json()

    assert body["ok"] is True and body["done"] == 2 and body["total"] == 2
    events = conn.execute("select count(*) as n from event_plan").fetchone()["n"]
    assert events == 2, "после подтверждения события обязаны быть в базе"


def test_план_останавливается_на_первой_ошибке(client, auth, conn, monkeypatch) -> None:
    who = _admin(conn)
    transport = _transport(
        _tool_call("propose_note", {"body": "Позвонить Марку"}, "c1"),
        _tool_call("propose_note", {"body": "Купить реквизит"}, "c2"),
        _answer("Записал две заметки."))
    result = asyncio.run(loop.run(conn, who, "запиши две заметки", transport=transport,
                                  task=True))
    assert len(result["plan"]["steps"]) == 2

    from app.api import notes as notes_api
    from app.errors import ApiError

    original = notes_api.create_note
    calls = {"n": 0}

    def flaky(conn_, who_, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ApiError(500, "вторая заметка не сохранилась", code="note_failed")
        return original(conn_, who_, **kwargs)

    monkeypatch.setattr(notes_api, "create_note", flaky)

    created = client.post("/tools/agent/plan",
                          json={"chat_id": ADMIN_CHAT, "steps": result["plan"]["steps"]},
                          headers=auth).json()
    body = client.post("/tools/agent/plan/confirm",
                       json={"chat_id": ADMIN_CHAT, "token": created["token"]},
                       headers=auth).json()

    assert body["ok"] is False
    assert body["done"] == 1 and body["total"] == 2
    assert body["failed_step"]["step"] == 2
    assert "шаге 2" in body["message"]
    notes = conn.execute("select count(*) as n from note").fetchone()["n"]
    assert notes == 1, "первый шаг остаётся, второй не доделывается молча"


def test_нехватка_даты_ведёт_в_приложение_а_не_в_догадку(conn) -> None:
    who = _caps(_person(conn, 570101, "Марк"))
    transport = _transport(
        _tool_call("propose_event", {"kind": "rehearsal", "people": ["Марк"]}),
        _answer("Нужна дата."))

    result = asyncio.run(loop.run(conn, who, "поставь встречу с Марком",
                                  transport=transport, task=True))

    assert result["propose"]["kind"] == "open"
    assert result["propose"]["screen"] == "/webapp/assign"
    assert result["plan"] is None
    assert result["propose"]["for"] == "event"


def test_обнулить_прекращает_задачу_между_шагами(conn) -> None:
    who = _caps(_person(conn, 570102, "Виктор"))
    chat_id = 570102
    transport = _transport(
        _tool_call("now", {}),
        _tool_call("now", {}),
        _answer("Готово."),
        on_call=lambda index, payload: tasks.request(chat_id) if index == 2 else None)

    result = asyncio.run(loop.run(conn, who, "проверь что-нибудь ещё раз",
                                  transport=transport, task=True, chat_id=chat_id))

    assert result["ok"] is False and result["error"] == "cancelled"
    assert result["step_count"] == 1, "шаг после отмены не начинается"


def test_девятый_шаг_не_начинается_и_итог_не_выдумывается(conn) -> None:
    who = _caps(_person(conn, 570103, "Пётр"))
    transport = _transport(_tool_call("now", {}))

    result = asyncio.run(loop.run(conn, who, "разбери длинную задачу",
                                  transport=transport, task=True))

    assert result["ok"] is False and result["error"] == "step_limit"
    assert result["step_count"] == 8, "задача идёт 8 шагов, а не 4 и не бесконечно"
    assert "Не разобрался" in result["message"]
    assert "answer" not in result, "итог при лимите не достраивается"


def test_обычный_вопрос_остаётся_на_четырёх_шагах(conn) -> None:
    who = _caps(_person(conn, 570104, "Анна"))
    transport = _transport(_tool_call("now", {}))

    result = asyncio.run(loop.run(conn, who, "какой сегодня день", transport=transport))

    assert result["error"] == "step_limit" and result["step_count"] == 4


def test_строка_шагов_называет_данные_а_не_функции(conn) -> None:
    who = _caps(_person(conn, 570105, "Олег"))
    transport = _transport(_tool_call("now", {}),
                           _tool_call("read_availability", {}),
                           _answer("Свободен."))

    result = asyncio.run(loop.run(conn, who, "что у меня сегодня", transport=transport))

    assert result["step_line"] == "дата → занятость"


def test_системная_подсказка_задачи_требует_план(conn) -> None:
    who = _caps(_person(conn, 570106, "Лена"))
    captured: list[dict] = []
    transport = _transport(_tool_call("now", {}), _answer("Готово."),
                           on_call=lambda index, payload: captured.append(payload))

    asyncio.run(loop.run(conn, who, "сделай две вещи", transport=transport, task=True))

    system = captured[0]["messages"][0]["content"]
    assert "Это задача" in system and "план" in system
    assert "не придумывай" in system


def test_план_без_шагов_не_создаётся(conn) -> None:
    who = _caps(_person(conn, 570107, "Ира"))

    result = plans.create(conn, who, 570107, [])

    assert result["ok"] is False and result["error"] == "empty_plan"


def test_каждый_экран_входа_открывается(client) -> None:
    """Экран из OPEN_SCREENS обязан отдавать страницу, а не 404.

    Красивая константа может вести в пустоту: `/webapp/people` отдавал 404, и
    человек после «не хватает участников» попадал в пустую страницу (ревью
    05.11.2026). Поэтому проверяется живой GET, а не сверка словаря с самим собой.
    """
    assert proposals.OPEN_SCREENS == {
        "event": "/webapp/assign", "availability": "/webapp/week",
        "note": "/webapp/notes", "budget": "/webapp/tx",
        "files": "/webapp/files", "slots": "/webapp/slots",
        "people": "/webapp/people",
    }
    for key, path in proposals.OPEN_SCREENS.items():
        response = client.get(path)
        assert response.status_code == 200, f"{key}: {path} -> {response.status_code}"
        assert response.text.strip(), f"{key}: {path} отдал пустую страницу"
    assert proposals.open_proposal("event")["screen"] == "/webapp/assign"
    assert proposals.open_proposal("нет-такого")["screen"] == "/webapp/home"
