"""Копирование опроса: новый черновик из отправленного, без адресатов и прохождений.

Критерии приёмки `docs/development/166-poll-draft-copy.md` §5.1: AC-2, AC-8, AC-10,
AC-13…AC-32, AC-37. Копия — новый `poll` со `status='draft'`, вопросами и вариантами;
`poll_run`, `poll_answer`, адресные правила и исключения, `poll_viewer`,
`started_at`/`closed_at`/`closes_at` не переносятся. Права проверяются на данных:
`poll.manage` + видимость источника (`_require_results`).

Обоснование: FR-G18, AC-2, AC-8, AC-10, AC-13…AC-32, AC-37,
`docs/development/166-poll-draft-copy.md` §2, §4, §5.1, §5.3.
"""

from __future__ import annotations

import psycopg
from app import core
from app.person_key import make_key
from app.webapp import issue_token
from psycopg.rows import dict_row

from tests.conftest import _test_dsn
from tests.integration.polls_helpers import QUESTIONS, audience, person, token

TITLE = "Анкета организатору"
COPY_TITLE = f"{TITLE} — копия"
INTRO = "Общий блок организаторам"
DENY_MANAGE = ("Опросы ведут администратор и организатор: право poll.manage есть у них. "
               "Если опрос нужен вам — попросите администратора или организатора.")
DENY_RESULTS = ("Результаты опроса видят его автор и те, кому он их открыл. "
                "Доступа к чужим результатам у вас нет.")


# --------------------------------------------------------------- помощники
def _rows(sql: str, params: tuple = ()) -> list[dict]:
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _scalar(sql: str, params: tuple = ()):
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def _poll(poll_id: int) -> dict:
    rows = _rows("select * from poll where id = %s", (poll_id,))
    assert rows, f"опроса {poll_id} нет"
    return rows[0]


def _people() -> dict[str, int]:
    """Автор (org), копирующий (org), отвечающий и двое актёров — для адресного набора."""
    with psycopg.connect(_test_dsn()) as conn:
        ids = {
            "author": person(conn, 786001, "Копия Автор", "org"),
            "copyist": person(conn, 786002, "Копия Копирует", "org"),
            "respondent": person(conn, 786003, "Копия Отвечает", "troupe"),
            "actor": person(conn, 786004, "Копия Актёр", "troupe"),
            "actor2": person(conn, 786005, "Копия Второй актёр", "troupe"),
            "master": person(conn, 786006, "Копия Педагог", "master"),
        }
    return ids


def _save(client, headers: dict, *, title: str = TITLE, questions=None,
          poll_id: int | None = None, **extra) -> int:
    body = {"title": title, "questions": questions if questions is not None else QUESTIONS,
            **extra}
    if poll_id is not None:
        body["poll_id"] = poll_id
    response = client.post("/webapp/polls/save", headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json()["poll_id"]


def _source(client, headers: dict, ids: dict, *, title: str = TITLE,
            questions=None, **extra) -> int:
    """Отправленный опрос: три вопроса, адресаты-актёры, один ответ и зритель.

    В наборе ровно одна строка `poll_audience` (роль), одна `poll_person` (галочка
    на уже включённого человека) и одна `poll_viewer` — тогда «в копии 0 строк»
    проверяется по каждой таблице отдельно (AC-17).
    """
    poll_id = _save(client, headers, title=title, questions=questions,
                    intro=INTRO, **extra)
    audience(client, headers, poll_id, rules=[{"kind": "role", "role": "troupe"}],
             include=[ids["respondent"]], viewers={"people": [ids["copyist"]], "roles": []})
    started = client.post("/webapp/polls/start", headers=headers, json={"poll_id": poll_id})
    assert started.status_code == 200, started.text
    run = _rows("select id from poll_run where poll_id = %s order by id limit 1", (poll_id,))[0]
    answered = client.post("/webapp/polls/answer", headers=token(client, 786003),
                           json={"run_id": run["id"], "action": "o", "code": "a1"})
    assert answered.status_code == 200, answered.text
    return poll_id


def _copy(client, headers: dict, poll_id: int):
    return client.post("/webapp/polls/copy", headers=headers, json={"poll_id": poll_id})


def _error(response) -> tuple[int, str, str]:
    body = response.json()
    return response.status_code, body.get("code"), body.get("detail")


def _archived_org(chat_id: int, name: str) -> int:
    """Архивный участник с ролью, дающей `poll.manage`: право есть, доступа нет."""
    with psycopg.connect(_test_dsn()) as conn:
        pid = conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status) "
            "values (%s,%s,%s,'org','archived') returning id",
            (chat_id, name, make_key(name))).fetchone()[0]
        conn.execute("insert into participant_role (participant_id, role) values (%s,'org') "
                     "on conflict do nothing", (pid,))
        conn.commit()
    return pid


# ------------------------------------------------- AC-14, AC-15, AC-17: что переносится
def test_копия_идущего_опроса_это_новый_черновик_без_адресатов_и_прохождений(client) -> None:
    """AC-14, AC-15, AC-17: копия — новый `draft` с наполнением, но без следов отправки."""
    ids = _people()
    author, copyist = token(client, 786001), token(client, 786002)
    source_id = _source(client, author, ids)
    before = _poll(source_id)
    notifications_before = _scalar("select count(*) from notification")

    response = _copy(client, copyist, source_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"ok", "poll_id", "title", "status", "url"}, body
    assert body["ok"] is True and body["status"] == "draft"
    new_id = body["poll_id"]
    assert isinstance(new_id, int) and new_id != source_id
    assert body["title"] == COPY_TITLE
    assert body["url"] == f"/webapp/polls/edit?poll_id={new_id}&note=copy"

    copy = _poll(new_id)
    assert copy["status"] == "draft"
    assert copy["created_by"] == ids["copyist"]
    assert copy["intro"] == INTRO and copy["anonymous"] is False
    assert copy["closes_at"] is None and copy["started_at"] is None
    assert copy["closed_at"] is None
    assert copy["updated_at"] >= copy["created_at"]

    source_questions = _rows(
        "select position, text, kind, required, free_text, max_choices from poll_question "
        "where poll_id = %s order by position", (source_id,))
    copy_questions = _rows(
        "select id, position, text, kind, required, free_text, max_choices from poll_question "
        "where poll_id = %s order by position", (new_id,))
    assert len(copy_questions) == 3
    assert [{key: row[key] for key in source_questions[0]} for row in copy_questions] == \
        source_questions
    first = _rows("select position, code, label from poll_option where question_id = %s "
                  "order by position", (copy_questions[0]["id"],))
    assert [(row["position"], row["code"], row["label"]) for row in first] == \
        [(1, "a1", "Расписание"), (2, "a2", "Звонки")]

    for table in ("poll_run", "poll_audience", "poll_person", "poll_viewer"):
        assert _scalar(f"select count(*) from {table} where poll_id = %s", (new_id,)) == 0, (
            f"в копию переехали строки {table}")
    assert _scalar(
        "select count(*) from poll_answer a join poll_question q on q.id = a.question_id "
        "where q.poll_id = %s", (new_id,)) == 0, "в копию переехали ответы источника"
    assert _scalar("select count(*) from notification") == notifications_before

    after_source = _poll(source_id)
    assert after_source["status"] == before["status"]
    assert after_source["started_at"] == before["started_at"]
    assert _scalar("select count(*) from poll_run where poll_id = %s", (source_id,)) == 3


# ------------------------------------------------------------------- AC-16: коды вариантов
def test_коды_вариантов_копии_пересчитываются_а_не_переезжают(client) -> None:
    """AC-16: у источника коды правлены на `c1`/`c2` — у копии снова `a1`/`a2`."""
    ids = _people()
    author, copyist = token(client, 786001), token(client, 786002)
    source_id = _source(client, author, ids)
    with psycopg.connect(_test_dsn()) as conn:
        question_id = conn.execute(
            "select id from poll_question where poll_id = %s order by position limit 1",
            (source_id,)).fetchone()[0]
        conn.execute("update poll_option set code = 'c' || position where question_id = %s",
                     (question_id,))
        conn.commit()

    new_id = _copy(client, copyist, source_id).json()["poll_id"]
    options = _rows(
        "select o.position, o.code, o.label from poll_option o "
        "join poll_question q on q.id = o.question_id "
        "where q.poll_id = %s order by o.position", (new_id,))
    assert [(row["code"], row["label"]) for row in options] == \
        [("a1", "Расписание"), ("a2", "Звонки")]
    assert _scalar("select count(*) from poll_option o join poll_question q "
                   "on q.id = o.question_id where q.poll_id = %s", (new_id,)) == 2


# ------------------------------------------------------- AC-18, AC-19: срок и анонимность
def test_копия_закрытого_опроса_без_срока_правима(client) -> None:
    """AC-18: `closes_at` в прошлом не переезжает — копия правима и не считается закрытой."""
    _people()
    author = token(client, 786001)
    source_id = _save(client, author)
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("update poll set status='closed', closes_at = now() - interval '1 day', "
                     "closed_at = now() - interval '1 day' where id = %s", (source_id,))
        conn.commit()

    response = _copy(client, author, source_id)
    assert response.status_code == 200, response.text
    new_id = response.json()["poll_id"]
    assert _poll(new_id)["closes_at"] is None

    draft = client.get(f"/webapp/polls/edit/data?poll_id={new_id}", headers=author)
    assert draft.status_code == 200, draft.text
    assert draft.json()["poll"]["status"] == "draft"
    assert draft.json()["can_edit"] is True

    saved = client.post("/webapp/polls/save", headers=author,
                        json={"poll_id": new_id, "title": COPY_TITLE, "questions": QUESTIONS})
    assert saved.status_code == 200, saved.text


def test_anonymous_переносится_как_есть(client) -> None:
    """AC-19: `anonymous=true` у источника — `true` у копии; `false` — `false`."""
    ids = _people()
    author, copyist = token(client, 786001), token(client, 786002)
    anonymous_id = _source(client, author, ids, anonymous=True)
    named_id = _source(client, author, ids, anonymous=False)

    anonymous_copy = _copy(client, copyist, anonymous_id).json()["poll_id"]
    named_copy = _copy(client, copyist, named_id).json()["poll_id"]
    assert _poll(anonymous_copy)["anonymous"] is True
    assert _poll(named_copy)["anonymous"] is False


# --------------------------------------------------------------- AC-20: длинная тема
def test_длинная_тема_копии_проходит_save(client) -> None:
    """AC-20: тема 300 знаков не даёт `400 too_long` ни у копии, ни у её сохранения."""
    ids = _people()
    author = token(client, 786001)
    title = "а" * 289 + " " + "б" * 10
    source_id = _source(client, author, ids, title=title)

    response = _copy(client, author, source_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == title[:290].rstrip() + " — копия"
    assert len(body["title"]) <= 300
    assert body["title"].endswith(" — копия") and "  — копия" not in body["title"]

    saved = client.post("/webapp/polls/save", headers=author,
                        json={"poll_id": body["poll_id"], "title": body["title"],
                              "questions": QUESTIONS})
    assert saved.status_code == 200, saved.text


# ------------------------------------------------------------ AC-21, AC-22: что не копируют
def test_черновик_не_копируют(client) -> None:
    """AC-21: свой черновик — `409 poll_draft`; строк не прибавилось, аудита нет."""
    _people()
    author = token(client, 786001)
    draft_id = _save(client, author)
    polls_before = _scalar("select count(*) from poll")

    status, code, detail = _error(_copy(client, author, draft_id))
    assert status == 409 and code == "poll_draft"
    assert detail == "Черновик не копируют: откройте его в конструкторе."
    assert _scalar("select count(*) from poll") == polls_before
    assert _scalar("select count(*) from audit_event where action = 'poll.copy'") == 0


def test_опрос_без_вопросов_не_копируется(client) -> None:
    """AC-22: у идущего опроса ноль вопросов (правка БД) — `400 no_questions`."""
    ids = _people()
    author, copyist = token(client, 786001), token(client, 786002)
    source_id = _source(client, author, ids)
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("delete from poll_option where question_id in "
                     "(select id from poll_question where poll_id = %s)", (source_id,))
        conn.execute("delete from poll_question where poll_id = %s", (source_id,))
        conn.commit()
    polls_before = _scalar("select count(*) from poll")

    status, code, detail = _error(_copy(client, copyist, source_id))
    assert status == 400 and code == "no_questions"
    assert detail == "В опросе нет вопросов: копировать нечего."
    assert _scalar("select count(*) from poll") == polls_before


# ---------------------------------------------------------------- AC-23…AC-27: права
def test_чужой_опрос_невидимому_копирующему_отказ_по_видимости(client) -> None:
    """AC-23: `org` без `poll.results.all` и без `poll_viewer` — `403 no_results` + аудит."""
    ids = _people()
    author, copyist = token(client, 786001), token(client, 786002)
    source_id = _source(client, author, ids)
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("delete from poll_viewer where poll_id = %s", (source_id,))
        conn.commit()
    polls_before = _scalar("select count(*) from poll")

    status, code, detail = _error(_copy(client, copyist, source_id))
    assert status == 403 and code == "no_results"
    assert detail == DENY_RESULTS
    denied = _rows("select entity_type, entity_id, after from audit_event "
                   "where action = 'access.denied' order by id desc limit 1")
    assert denied, "отказ по видимости не записан в аудит"
    assert denied[0]["entity_type"] == "capability"
    assert denied[0]["entity_id"] == "poll.results"
    assert denied[0]["after"]["poll_id"] == source_id
    assert _scalar("select count(*) from poll") == polls_before


def test_чужой_закрытый_опрос_открытый_через_poll_viewer_копируется(client) -> None:
    """AC-24: строка `poll_viewer` открывает копирование; копия — копировавшему."""
    ids = _people()
    author, copyist = token(client, 786001), token(client, 786002)
    source_id = _save(client, author)
    audience(client, author, source_id, rules=[], viewers={"people": [ids["copyist"]],
                                                           "roles": []})
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("update poll set status='closed', closed_at=now() where id = %s",
                     (source_id,))
        conn.commit()

    response = _copy(client, copyist, source_id)
    assert response.status_code == 200, response.text
    new_id = response.json()["poll_id"]
    assert _poll(new_id)["created_by"] == ids["copyist"]

    listing = client.get("/webapp/polls/list", headers=copyist)
    assert listing.status_code == 200, listing.text
    rows = {row["id"]: row for row in listing.json()["polls"]}
    assert new_id in rows and rows[new_id]["status"] == "draft"


def test_без_poll_manage_отказ_по_праву_даже_для_несуществующего(client) -> None:
    """AC-25: `master` — `403 no_poll_manage` и на существующем, и на выдуманном id."""
    _people()
    author = token(client, 786001)
    source_id = _save(client, author)
    master = token(client, 786006)

    for poll_id in (source_id, 999_999):
        status, code, detail = _error(_copy(client, master, poll_id))
        assert status == 403 and code == "no_poll_manage", (poll_id, status, code)
        assert detail == DENY_MANAGE
        assert "Опрос не найден." not in detail and "результат" not in detail.lower()


def test_несуществующий_опрос_отвечает_404(client) -> None:
    """AC-26: `org` с правом на выдуманный id — `404 poll_not_found` словами."""
    _people()
    copyist = token(client, 786002)
    status, code, detail = _error(_copy(client, copyist, 999_999))
    assert status == 404 and code == "poll_not_found"
    assert detail == "Опрос не найден."


def test_чужой_черновик_не_раскрывает_статус(client) -> None:
    """AC-27: чужой невидимый черновик — `403 no_results`, а не `409 poll_draft`."""
    _people()
    author, copyist = token(client, 786001), token(client, 786002)
    draft_id = _save(client, author)

    status, code, detail = _error(_copy(client, copyist, draft_id))
    assert status == 403 and code == "no_results", (status, code)
    assert detail == DENY_RESULTS


def test_архивный_участник_не_копирует(client) -> None:
    """AC-28: архивный участник — `403 participant_archived`, копии нет (предел §5.6)."""
    _people()
    author = token(client, 786001)
    source_id = _save(client, author)
    _archived_org(786007, "Копия Архив")
    archived = {"Authorization": f"Bearer {issue_token(786007, core.WEBAPP_SECRET)}"}
    polls_before = _scalar("select count(*) from poll")

    response = _copy(client, archived, source_id)
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "participant_archived"
    assert _scalar("select count(*) from poll") == polls_before


# --------------------------------------------------- AC-29, AC-30: аудит и повтор
def test_аудит_копии_одна_строка_с_источником(client) -> None:
    """AC-29: `poll.copy` — одна строка: новый опрос, источник, тема, число вопросов."""
    ids = _people()
    author, copyist = token(client, 786001), token(client, 786002)
    source_id = _source(client, author, ids)
    new_id = _copy(client, copyist, source_id).json()["poll_id"]

    rows = _rows("select actor_type, actor_id, action, entity_type, entity_id, after "
                 "from audit_event where action = 'poll.copy'")
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["actor_type"] == "human" and row["actor_id"] == ids["copyist"]
    assert row["entity_type"] == "poll" and row["entity_id"] == str(new_id)
    assert row["after"]["source_poll_id"] == source_id
    assert row["after"]["title"] == COPY_TITLE
    assert row["after"]["questions"] == 3


def test_две_копии_подряд_не_дают_второго_суффикса(client) -> None:
    """AC-30: два `POST` — две независимые копии с одинаковой темой."""
    ids = _people()
    author, copyist = token(client, 786001), token(client, 786002)
    source_id = _source(client, author, ids)
    polls_before = _scalar("select count(*) from poll")

    first = _copy(client, copyist, source_id).json()["poll_id"]
    second = _copy(client, copyist, source_id).json()["poll_id"]
    assert first != second
    assert _poll(first)["title"] == COPY_TITLE == _poll(second)["title"]
    assert _poll(first)["status"] == "draft" and _poll(second)["status"] == "draft"
    assert _scalar("select count(*) from poll") == polls_before + 2


# --------------------------------------------------- AC-31, AC-32: копия живёт как черновик
def test_копия_принадлежит_копировавшему_а_не_автору_источника(client) -> None:
    """AC-31: правит копию только копировавший; автор источника — `403 alien_poll`."""
    ids = _people()
    author, copyist = token(client, 786001), token(client, 786002)
    source_id = _source(client, author, ids)
    new_id = _copy(client, copyist, source_id).json()["poll_id"]

    draft = client.get(f"/webapp/polls/edit/data?poll_id={new_id}", headers=copyist)
    assert draft.status_code == 200, draft.text
    assert draft.json()["can_edit"] is True
    assert draft.json()["poll"]["title"] == COPY_TITLE

    mine = client.post("/webapp/polls/save", headers=copyist,
                       json={"poll_id": new_id, "title": COPY_TITLE, "questions": QUESTIONS})
    assert mine.status_code == 200, mine.text

    alien = client.post("/webapp/polls/save", headers=author,
                        json={"poll_id": new_id, "title": COPY_TITLE, "questions": QUESTIONS})
    assert alien.status_code == 403, alien.text
    assert alien.json()["code"] == "alien_poll"


def test_копию_нельзя_отправить_пока_нет_адресатов(client) -> None:
    """AC-32: у копии нет адресатов — `409 no_recipients`, прохождений ноль."""
    ids = _people()
    author, copyist = token(client, 786001), token(client, 786002)
    source_id = _source(client, author, ids)
    new_id = _copy(client, copyist, source_id).json()["poll_id"]

    started = client.post("/webapp/polls/start", headers=copyist, json={"poll_id": new_id})
    assert started.status_code == 409, started.text
    assert started.json()["code"] == "no_recipients"
    assert _scalar("select count(*) from poll_run where poll_id = %s", (new_id,)) == 0


# -------------------------------------------------------------------- AC-37: тексты отказов
def test_все_пять_отказов_копирования_отвечают_фразой_а_не_кодом(client) -> None:
    """AC-37: `detail` — русская фраза с пробелом и не равен `code`."""
    ids = _people()
    author, copyist = token(client, 786001), token(client, 786002)
    master = token(client, 786006)

    running = _source(client, author, ids)
    invisible = _save(client, author)
    draft_id = _save(client, author)
    empty = _source(client, author, ids)
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("delete from poll_option where question_id in "
                     "(select id from poll_question where poll_id = %s)", (empty,))
        conn.execute("delete from poll_question where poll_id = %s", (empty,))
        conn.commit()

    cases = {
        "no_poll_manage": _copy(client, master, running),
        "no_results": _copy(client, copyist, invisible),
        "poll_not_found": _copy(client, copyist, 999_999),
        "poll_draft": _copy(client, author, draft_id),
        "no_questions": _copy(client, copyist, empty),
    }
    assert set(cases) == {"no_poll_manage", "no_results", "poll_not_found", "poll_draft",
                          "no_questions"}
    for expected, response in cases.items():
        assert response.status_code >= 400, (expected, response.text)
        body = response.json()
        assert body.get("code") == expected, (expected, body)
        detail = body.get("detail") or ""
        assert detail != expected, f"{expected}: вместо фразы пришёл код"
        assert any("а" <= char.lower() <= "я" or char.lower() == "ё" for char in detail), detail
        assert " " in detail, detail


# --------------------------------------------- AC-2, AC-8, AC-10, AC-13: сохранение черновика
def test_второе_сохранение_правит_тот_же_черновик(client) -> None:
    """AC-2: второй `save` с `poll_id` — четыре вопроса, два аудита, второй `updated`."""
    _people()
    author = token(client, 786001)
    poll_id = _save(client, author)
    assert _scalar("select count(*) from poll where title = %s", (TITLE,)) == 1

    extra = QUESTIONS + [{"position": 4, "text": "Что оставить как есть?", "kind": "text",
                          "required": False, "free_text": False, "max_choices": None,
                          "options": []}]
    again = _save(client, author, poll_id=poll_id, questions=extra)
    assert again == poll_id
    assert _scalar("select count(*) from poll where title = %s", (TITLE,)) == 1
    assert _scalar("select count(*) from poll_question where poll_id = %s", (poll_id,)) == 4

    audits = _rows("select after from audit_event where action = 'poll.save' order by id")
    assert len(audits) == 2
    assert "updated" not in audits[0]["after"]
    assert audits[1]["after"]["updated"] is True


def test_сохранение_отправленного_опроса_запрещено(client) -> None:
    """AC-8: `save` с `poll_id` идущего опроса — `409 poll_running`, вопросы не меняются."""
    ids = _people()
    author = token(client, 786001)
    source_id = _source(client, author, ids)
    before = _rows("select id, text from poll_question where poll_id = %s order by position",
                   (source_id,))

    response = client.post("/webapp/polls/save", headers=author,
                           json={"poll_id": source_id, "title": TITLE, "questions": QUESTIONS[:1]})
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "poll_running"
    assert response.json()["detail"] == "Опрос уже отправлен: правка вопросов запрещена."
    assert _rows("select id, text from poll_question where poll_id = %s order by position",
                 (source_id,)) == before


def test_черновик_без_адресатов_виден_в_списке(client) -> None:
    """AC-10: черновик без правил, людей и зрителей — строка списка с тремя вопросами."""
    _people()
    author = token(client, 786001)
    poll_id = _save(client, author)

    rows = client.get("/webapp/polls/list", headers=author).json()["polls"]
    row = next(item for item in rows if item["id"] == poll_id)
    assert row["status"] == "draft"
    assert row["questions"] == 3
    assert row["invited"] == 0 and row["answered"] == 0


def test_черновик_без_вопросов_в_базе_в_списке_не_падает(client) -> None:
    """AC-13: `poll` без единого вопроса (SQL) — ответ 200, `questions = 0`."""
    ids = _people()
    with psycopg.connect(_test_dsn()) as conn:
        poll_id = conn.execute(
            "insert into poll (title, created_by) values (%s, %s) returning id",
            ("Пустой черновик", ids["author"])).fetchone()[0]
        conn.commit()

    response = client.get("/webapp/polls/list", headers=token(client, 786001))
    assert response.status_code == 200, response.text
    row = next(item for item in response.json()["polls"] if item["id"] == poll_id)
    assert row["status"] == "draft" and row["questions"] == 0
