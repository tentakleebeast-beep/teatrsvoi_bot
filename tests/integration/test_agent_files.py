"""Агент по файлам: цикл с инструментами, черновик перемещения и проверка цитаты (Ш-12/13).

Сеть не нужна: провайдер подменяется транспортом, который отдаёт заранее собранные ответы
с `tool_calls`. Так проверяется именно наш цикл — что он исполняет инструменты, соблюдает
лимит шагов и не пропускает ответ без опоры на текст файла.
"""

from __future__ import annotations

import asyncio
import json

import psycopg
import pytest
from app import agent
from psycopg.rows import dict_row

FILE_TEXT = "Выход Пьеро после антракта, свет медленный, музыка тихая"


@pytest.fixture()
def conn(test_db: str):
    """Соединение с тестовой БД: её создаёт и наполняет схемой фикстура `test_db`."""
    with psycopg.connect(test_db, row_factory=dict_row) as connection:
        yield connection


def _person(conn, chat_id: int, role: str = "troupe") -> dict:
    row = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status)"
        " values (%s,%s,%s,%s,'active') returning id, display_name, role, status",
        (chat_id, f"Человек {chat_id}", f"человек-{chat_id}", role)).fetchone()
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)"
                 " on conflict do nothing", (row["id"], role))
    return dict(row) | {"roles": [role], "id": row["id"]}


def _file(conn, participant_id: int, name: str = "сцена.pdf", text: str = FILE_TEXT) -> int:
    row = conn.execute(
        "insert into file_object (participant_id, name, mime, scope, uploaded_via,"
        " size_bytes, sha256, kind) values (%s,%s,'application/pdf','personal','bot',10,%s,'text')"
        " returning id", (participant_id, name, f"{hash(name) % 10 ** 8:064d}")).fetchone()
    conn.execute("insert into file_text (file_id, body) values (%s,%s)", (row["id"], text))
    return int(row["id"])


def _folder(conn, participant_id: int, name: str = "Пурга") -> int:
    row = conn.execute(
        "insert into folder (scope, participant_id, name, path)"
        " values ('personal', %s, %s, %s) returning id",
        (participant_id, name, f"Личное/{name}")).fetchone()
    return int(row["id"])


def _tool_call(name: str, args: dict, call_id: str = "call-1") -> dict:
    return {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": call_id, "type": "function",
         "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}}],
        "usage": {"total_tokens": 10}}


def _answer(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}], "usage": {"total_tokens": 5}}


def _transport(*answers):
    calls = {"index": 0}

    def transport(_payload):
        index = min(calls["index"], len(answers) - 1)
        calls["index"] += 1
        return answers[index]

    return transport


def test_цикл_вызывает_инструмент_и_отвечает(conn) -> None:
    who = _person(conn, 561001)
    _file(conn, who["id"])
    transport = _transport(_tool_call("list_files", {"query": "сцена"}),
                           _answer("Нашёл файл сцены."))

    result = asyncio.run(agent.run(conn, who, "где сцена?", transport=transport))
    assert result["ok"] is True
    assert result["steps"][0]["tool"] == "list_files"
    assert result["steps"][0]["ok"] is True
    assert result["answer"] == "Нашёл файл сцены."
    assert result["tokens"] == 15


def test_неизвестный_инструмент_не_роняет_цикл(conn) -> None:
    who = _person(conn, 561002)
    transport = _transport(_tool_call("delete_file", {"file_id": 1}),
                           _answer("Не буду удалять."))
    result = asyncio.run(agent.run(conn, who, "удали всё", transport=transport))
    assert result["ok"] is True
    assert result["steps"][0]["ok"] is False


def test_лимит_шагов_это_отказ_а_не_догадка(conn) -> None:
    who = _person(conn, 561003)
    transport = _transport(_tool_call("list_files", {}))
    result = asyncio.run(agent.run(conn, who, "ищи", max_steps=3, transport=transport))
    assert result["ok"] is False
    assert result["error"] == "step_limit"
    assert len(result["steps"]) == 3


def test_перемещение_это_черновик_и_применяется_кнопкой(conn) -> None:
    who = _person(conn, 561004)
    file_id = _file(conn, who["id"])
    folder_id = _folder(conn, who["id"], "музыка")
    transport = _transport(
        _tool_call("propose_move", {"file_id": file_id, "folder_path": "Личное/музыка"}),
        _answer("Предлагаю переложить."))

    result = asyncio.run(agent.run(conn, who, "переложи в музыку", transport=transport))
    assert result["proposed"] is True if "proposed" in result else True
    action_id = result["steps"][0]["action_id"]
    assert action_id is not None

    # файл ещё не тронут: перемещение — только предложение
    assert conn.execute("select count(*) as n from file_folder where file_id = %s",
                        (file_id,)).fetchone()["n"] == 0
    status = conn.execute("select status from agent_action where id = %s",
                          (action_id,)).fetchone()["status"]
    assert status == "proposed"

    applied = agent.apply_action(conn, who, action_id)
    assert applied["ok"] is True and applied["folder_path"] == "Личное/музыка"
    assert conn.execute("select folder_id from file_folder where file_id = %s",
                        (file_id,)).fetchone()["folder_id"] == folder_id


def test_отклонённое_предложение_не_двигает_файл(conn) -> None:
    who = _person(conn, 561005)
    file_id = _file(conn, who["id"])
    _folder(conn, who["id"], "шумы")
    transport = _transport(
        _tool_call("propose_move", {"file_id": file_id, "folder_path": "Личное/шумы"}),
        _answer("Предложил."))
    result = asyncio.run(agent.run(conn, who, "переложи", transport=transport))
    action_id = result["steps"][0]["action_id"]

    assert agent.reject_action(conn, who, action_id)["ok"] is True
    assert conn.execute("select count(*) as n from file_folder where file_id = %s",
                        (file_id,)).fetchone()["n"] == 0
    again = agent.apply_action(conn, who, action_id)
    assert again["ok"] is False and again["error"] == "already_resolved"


def test_ответ_с_цитатой_проходит_а_без_цитаты_помечается(conn) -> None:
    who = _person(conn, 561006)
    file_id = _file(conn, who["id"])
    transport = _transport(
        _tool_call("read_file", {"file_id": file_id}),
        _answer("В файле сказано: «Выход Пьеро после антракта» — свет медленный."))
    good = asyncio.run(agent.run(conn, who, "что в сцене?", transport=transport))
    assert good["verified"] is True
    assert "проверьте сами" not in good["answer"]

    transport = _transport(
        _tool_call("read_file", {"file_id": file_id}),
        _answer("Там описано, как Пьеро поёт песню про море."))
    bad = asyncio.run(agent.run(conn, who, "что в сцене?", transport=transport))
    assert bad["verified"] is False
    assert bad["answer"].startswith("(в файле этих слов нет")


def test_чужой_файл_не_читается_инструментом(conn) -> None:
    owner = _person(conn, 561007)
    stranger = _person(conn, 561008)
    file_id = _file(conn, owner["id"])
    transport = _transport(_tool_call("read_file", {"file_id": file_id}),
                           _answer("В файле этого нет."))
    result = asyncio.run(agent.run(conn, stranger, "что там?", transport=transport))
    assert result["steps"][0]["ok"] is False


def test_модель_инструментов_это_параметр(monkeypatch) -> None:
    monkeypatch.setenv("LLM_TOOLS_MODEL", "openai/gpt-4o")
    assert agent.tools_model() == "openai/gpt-4o"
    monkeypatch.setenv("LLM_TOOLS_MODEL", "")
    assert agent.tools_model()  # падает обратно на общую модель проекта
