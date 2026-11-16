"""Анкета 161 целиком: четыре опроса, все вопросы документа, сводка и оба экспорта (E8, 162 §9).

Ради этого прогона фича и делалась: вопросы берутся не из выдуманного списка, а из
`docs/development/161` (§3–§6), раскладываются в четыре опроса — сколько вопросов в каждом,
сказано в заголовке раздела документа, — адресаты вручную и по роли, дальше прохождение, сводка
и обе выгрузки. Счёт не зашит числом: анкету правят по вычитке владельца, и тест должен падать
только тогда, когда документ и база разошлись, а не когда изменилось число вопросов.

Проверяется то, что легко потерять на живом материале:

* в базе столько вопросов, сколько заявлено в заголовках §3–§6, и текст каждого совпадает с
  документом слово в слово (ни обрезанных, ни склеенных пунктов);
* состав адресатов совпадает с таблицей §9 (орг 3, Мария 1, педагоги 2, актёры 2);
* ответы всех вопросов доходят до сводки, `text`-сводка — списком;
* CSV разбирается обратно (`;`, `utf-8-sig`) и содержит каждый вопрос;
* Markdown — готовые блоки «## Вопрос N» для журнала 161 §9.

Обоснование: FR-G18, AC-10, AC-11, AC-12, `docs/development/162-bot-polls.md` §9.
"""

from __future__ import annotations

import csv
import io
import pathlib
import re

import psycopg
import pytest

from tests.conftest import _test_dsn
from tests.integration.polls_helpers import (
    audience,
    create_poll,
    notifications,
    person,
    token,
    tools,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC = next(ROOT.glob("docs/development/161-*.md"))

#: Разделы документа → опрос (§9). Сколько вопросов в разделе, сказано в его заголовке.
SECTIONS = {
    3: "Анкета организаторам",
    4: "Марии: деньги и соцсети",
    5: "Педагогам",
    6: "Актёрам",
}


def _declared_counts() -> dict[int, int]:
    """Число вопросов по заголовкам разделов: «## 4. Марии … — 9 вопросов»."""
    counts: dict[int, int] = {}
    for line in DOC.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^## (\d+)\..*?(\d+) вопрос", line)
        if heading and int(heading.group(1)) in SECTIONS:
            counts[int(heading.group(1))] = int(heading.group(2))
    assert sorted(counts) == sorted(SECTIONS), f"в заголовках §3–§6 нет счёта: {counts}"
    return counts


def _sections() -> dict[int, list[str]]:
    """Вопросы из документа: номер пункта — начало вопроса, отступ — его продолжение."""
    out: dict[int, list[str]] = {}
    current: int | None = None
    for line in DOC.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^## (\d+)\.", line)
        if heading:
            current = int(heading.group(1)) if int(heading.group(1)) in SECTIONS else None
            if current is not None:
                out[current] = []
            continue
        if current is None:
            continue
        item = re.match(r"^(\d+)\. (.+)$", line)
        if item:
            out[current].append(item.group(2))
        elif line.startswith("   ") and line.strip() and out[current]:
            out[current][-1] += " " + line.strip()
        elif not line.strip():
            continue
    return {number: [_clean(text) for text in texts] for number, texts in out.items()}


def _clean(text: str) -> str:
    """Пробелы схлопнуты, жирный шрифт снят: сравнение с базой, а не с разметкой."""
    return re.sub(r"\s+", " ", text.replace("**", "")).strip()


def _questions(texts: list[str]) -> list[dict]:
    return [{"position": index, "text": text, "kind": "text", "required": True,
             "free_text": False, "max_choices": None, "options": []}
            for index, text in enumerate(texts, start=1)]


def _ids(chat_ids: list[int]) -> list[int]:
    with psycopg.connect(_test_dsn()) as conn:
        return [conn.execute("select id from participant where tg_chat_id = %s",
                             (chat_id,)).fetchone()[0] for chat_id in chat_ids]


@pytest.fixture
def анкета(client) -> dict:
    """Четыре опроса из документа, адресаты по §9 — на живых участниках тестовой базы."""
    sections = _sections()
    declared = _declared_counts()
    assert {number: len(texts) for number, texts in sections.items()} == declared, (
        f"документ 161 дал не по заголовкам вопросов: "
        f"{ {number: len(texts) for number, texts in sections.items()} } против {declared}")

    with psycopg.connect(_test_dsn()) as conn:
        person(conn, 783101, "Анкета Владелец", "admin")
        person(conn, 783102, "Анкета Орг Один", "org")
        person(conn, 783103, "Анкета Орг Два", "org")
        person(conn, 783104, "Анкета Мария", "org")
        person(conn, 783105, "Анкета Педагог Один", "master")
        person(conn, 783106, "Анкета Педагог Два", "master")
        person(conn, 783107, "Анкета Актёр Один", "troupe")
        person(conn, 783108, "Анкета Актёр Два", "troupe")

    owner = token(client, 783101)
    orgs = _ids([783102, 783103, 783104])
    maria = _ids([783104])
    educators = _ids([783105, 783106])
    actors = _ids([783107, 783108])

    polls: dict[str, int] = {}
    for number, title in SECTIONS.items():
        poll_id = create_poll(client, owner, questions=_questions(sections[number]),
                              title=title)
        polls[title] = poll_id

    audience(client, owner, polls["Анкета организаторам"], rules=[],
             include=orgs, viewers={"people": orgs + _ids([783101])})
    audience(client, owner, polls["Марии: деньги и соцсети"], rules=[],
             include=maria, viewers={"people": maria + _ids([783101])})
    audience(client, owner, polls["Педагогам"], rules=[{"kind": "role", "role": "master"}],
             viewers={"people": educators + _ids([783101])})
    audience(client, owner, polls["Актёрам"], rules=[{"kind": "role", "role": "troupe"}],
             viewers={"people": _ids([783101])})

    started = {}
    for title, poll_id in polls.items():
        response = client.post("/webapp/polls/start", headers=owner, json={"poll_id": poll_id})
        assert response.status_code == 200, response.text
        started[title] = response.json()["invited"]

    return {"owner": owner, "polls": polls, "started": started, "sections": sections,
            "counts": declared,
            "people": {"orgs": orgs, "maria": maria, "educators": educators, "actors": actors}}


def _answer_all(client, run_id: int, chat_id: int, total: int, prefix: str) -> None:
    """Пройти опрос до конца свободным ответом на каждый вопрос (все 161 — `text`)."""
    headers = tools(chat_id)
    for position in range(1, total + 1):
        response = client.post("/tools/polls/answer", headers=headers,
                               json={"run_id": run_id, "position": position,
                                     "action": "text", "text": f"{prefix} {position}"})
        assert response.status_code == 200, response.text
    done = client.get("/tools/polls/step?run_id=" + str(run_id) + f"&position={total}",
                      headers=headers)
    assert done.json()["step"]["done"] is True, done.text


def test_анкета_собрана_без_потерь(анкета) -> None:
    """В базе столько же вопросов, сколько в документе, и тексты совпадают."""
    with psycopg.connect(_test_dsn()) as conn:
        rows = conn.execute(
            "select p.title, q.position, q.text from poll_question q "
            "join poll p on p.id = q.poll_id order by p.id, q.position").fetchall()
    total = sum(анкета["counts"].values())
    assert len(rows) == total, f"вопросов в базе {len(rows)}, а в документе {total}"

    по_опросам: dict[str, list[str]] = {}
    for title, _position, text in rows:
        по_опросам.setdefault(title, []).append(text)
    for number, title in SECTIONS.items():
        assert по_опросам[title] == анкета["sections"][number], f"опрос «{title}» потерял тексты"


def test_адресаты_по_таблице_9(client, анкета) -> None:
    """Вопросов — по документу, а адресаты — как в §9: 3 / 1 / 2 / 2."""
    assert анкета["started"] == {"Анкета организаторам": 3, "Марии: деньги и соцсети": 1,
                                 "Педагогам": 2, "Актёрам": 2}, анкета["started"]
    assert len(notifications()) == 8, "приглашений не по числу адресатов"


def test_прогон_до_конца_сводка_и_экспорты(client, анкета) -> None:
    """Один человек проходит организаторский опрос, другой — актёрский: сводка и файлы."""
    owner = анкета["owner"]
    runs = {(row["payload"]["poll_id"], row["tg_chat_id"]): row["payload"]["run_id"]
            for row in notifications()}
    polls = анкета["polls"]

    counts = анкета["counts"]
    _answer_all(client, runs[(polls["Анкета организаторам"], 783102)], 783102, counts[3], "орг")
    _answer_all(client, runs[(polls["Педагогам"], 783105)], 783105, counts[5], "педагог")
    _answer_all(client, runs[(polls["Актёрам"], 783108)], 783108, counts[6], "актёр")

    summary = client.get(f"/webapp/polls/results/data?poll_id={polls['Анкета организаторам']}",
                         headers=owner)
    assert summary.status_code == 200, summary.text
    data = summary.json()
    assert data["invited"] == 3 and data["answered"] == 1
    assert [item["position"] for item in data["questions"]] == list(range(1, counts[3] + 1))
    assert [item["text"] for item in data["questions"]] == анкета["sections"][3]
    первый = data["questions"][0]["summary"]
    assert [row["text"] for row in первый["answers"]] == ["орг 1"], первый
    assert len(data["not_answered"]) == 2

    csv_response = client.get(
        f"/webapp/polls/export?poll_id={polls['Анкета организаторам']}&format=csv", headers=owner)
    assert csv_response.status_code == 200, csv_response.text
    assert csv_response.content.startswith(b"\xef\xbb\xbf"), "CSV без utf-8-sig"
    text = csv_response.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    assert rows[0] == ["вопрос", "тип", "человек", "ответ", "когда"], rows[0]
    assert len(rows) == counts[3] + 1, f"строк {len(rows)} вместо шапки и {counts[3]} ответов"
    for position, question in enumerate(анкета["sections"][3], start=1):
        assert any(row[0] == f"{position}. {question}" for row in rows[1:]), (
            f"вопрос {position} пропал из CSV: {question}")

    md_response = client.get(
        f"/webapp/polls/export?poll_id={polls['Анкета организаторам']}&format=md", headers=owner)
    assert md_response.status_code == 200, md_response.text
    markdown = md_response.content.decode("utf-8")
    for position in range(1, counts[3] + 1):
        assert f"## Вопрос {position}" in markdown, f"в Markdown нет блока вопроса {position}"
    for question in анкета["sections"][3]:
        assert question in markdown, f"вопрос пропал из Markdown: {question}"
