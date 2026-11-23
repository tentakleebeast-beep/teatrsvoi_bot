"""Опросы в боте: конструктор, адресаты, отправка, ответы, сводка и экспорт (E8).

Спецификация — `docs/development/162-bot-polls.md` §14.2 (контракты), §14.3 (права),
§14.4 (шаги ответа), §14.6 (доставка). Модуль делится на две части:

* `/webapp/polls/*` — Mini App: конструктор, адресаты, сводка, экспорт. Права
  проверяются на данных через `core._webapp_require`; страницы остаются публичными
  константами (docs/development/86 §5).
* `/tools/polls/*` — шаги диалога для gateway. Первый вопрос уходит push-ом через
  очередь `notification`, а шаги 2…N — ответом на действие человека, без тихих
  часов и лимита 3/час (D5): анкета из 11 вопросов иначе растягивается на четыре
  часа.

Правила, которые держит этот файл, а не страницы:
  * ответ пишется сразу и перезаписывается (`on conflict`), а не дублируется;
  * черновик прохождения живёт в `poll_run`, а не в состоянии бота (D4): перезапуск
    gateway не теряет прогресс;
  * после отправки состав `poll_run` заморожен — правка правил его не меняет (AC-3).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import HTMLResponse, Response
from pydantic import Field

from .. import core, files_store, poll_store, webapp
from ..errors import ApiError
from ..webapp import InitDataError

router = APIRouter()

DENY_MANAGE_TEXT = ("Опросы ведут администратор и организатор: право poll.manage есть у них. "
                    "Если опрос нужен вам — попросите администратора или организатора.")
DENY_RESULTS_TEXT = ("Результаты опроса видят его автор и те, кому он их открыл. "
                     "Доступа к чужим результатам у вас нет.")

ROLE_TITLES = {"admin": "администратор", "org": "организатор",
               "master": "педагог", "troupe": "актёр"}

POLL_COLUMNS = ("p.id, p.title, p.intro, p.status, p.anonymous, p.closes_at, "
                "p.created_by, p.created_at, p.started_at, p.closed_at")


# --------------------------------------------------------------------- общее
def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _poll_or_404(conn, poll_id: int) -> dict:
    row = conn.execute(f"select {POLL_COLUMNS} from poll p where p.id = %s",
                       (poll_id,)).fetchone()
    if row is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "Опрос не найден.", code="poll_not_found")
    return dict(row)


def _questions(conn, poll_id: int) -> list[dict]:
    """Вопросы с вариантами: порядок — данные (`position`), а не порядок вставки."""
    rows = conn.execute(
        "select id, position, text, kind, required, free_text, max_choices "
        "from poll_question where poll_id = %s order by position", (poll_id,)).fetchall()
    questions = [dict(row) for row in rows]
    if not questions:
        return questions
    options = conn.execute(
        "select o.question_id, o.position, o.code, o.label from poll_option o "
        "join poll_question q on q.id = o.question_id "
        "where q.poll_id = %s order by o.question_id, o.position", (poll_id,)).fetchall()
    by_question: dict[int, list[dict]] = {}
    for option in options:
        by_question.setdefault(option["question_id"], []).append(
            {"position": option["position"], "code": option["code"],
             "label": option["label"]})
    for question in questions:
        question["options"] = by_question.get(question["id"], [])
    return questions


def _can_read_results(conn, viewer: dict, poll: dict, caps: list[str]) -> bool:
    """Результаты: автор, `poll.results.all` или строка `poll_viewer` (человек/роль)."""
    if "*" in caps or "poll.results.all" in caps:
        return True
    if int(poll["created_by"]) == int(viewer["id"]):
        return True
    return conn.execute(
        "select 1 from poll_viewer where poll_id = %s and "
        "(participant_id = %s or role = any(%s)) limit 1",
        (poll["id"], viewer["id"], list(viewer.get("roles") or []))).fetchone() is not None


def _require_results(conn, viewer: dict, poll: dict) -> None:
    """Результаты: автор, `poll.results.all` или `poll_viewer`.

    `poll.manage` сам по себе чужих результатов не открывает (D8, §14.11): это две
    разные возможности, и отказ пишется в аудит — как у `_webapp_require`.
    """
    if viewer.get("status") not in ("active",):
        raise ApiError(status.HTTP_403_FORBIDDEN, "participant_archived",
                       code="participant_archived")
    if not _can_read_results(conn, viewer, poll, viewer.get("caps") or []):
        _audit_denied_results(viewer, poll)
        raise ApiError(status.HTTP_403_FORBIDDEN, DENY_RESULTS_TEXT, code="no_results")


def _audit_denied_results(viewer: dict, poll: dict) -> None:
    """Отказ в видимости — своим соединением: запрос следом падает, и запись,
    сделанная в его соединении, откатилась бы вместе с исключением. Так отказ
    терялся молча — `access.denied` не появлялся в журнале ни у сводки, ни у копии."""
    with core.pool.connection() as audit_conn:
        core.audit(audit_conn, "human", viewer.get("id"), "access.denied", "capability",
                   "poll.results", after={"poll_id": poll["id"], "via": "webapp"})


def _own_draft(conn, viewer: dict, poll: dict, caps: list[str]) -> None:
    """Правка — только черновик и только своего опроса (чужой — по `poll.results.all`)."""
    if int(poll["created_by"]) != int(viewer["id"]) and not ("*" in caps or
                                                            "poll.results.all" in caps):
        raise ApiError(status.HTTP_403_FORBIDDEN, DENY_MANAGE_TEXT, code="alien_poll")


def _closed(poll: dict) -> bool:
    if poll["status"] == "closed":
        return True
    closes_at = poll.get("closes_at")
    return bool(closes_at and closes_at <= datetime.now(UTC))


# --------------------------------------------------------------- Mini App: список
@router.get("/webapp/polls/list")
def polls_list(who: dict = Depends(core.webapp_actor)) -> dict:
    """Список опросов: свои — всем с `poll.manage`, чужие — только с `poll.results.all`."""
    viewer = core._webapp_require(who, "poll.manage", deny_code="no_poll_manage",
                                 deny_text=DENY_MANAGE_TEXT)
    caps = viewer.get("caps") or []
    with core.pool.connection() as conn:
        rows = conn.execute(
            """
            select p.id, p.title, p.status, p.created_at,
                   (select count(*) from poll_question q where q.poll_id = p.id) as questions,
                   (select count(*) from poll_run r where r.poll_id = p.id) as invited,
                   (select count(*) from poll_run r where r.poll_id = p.id
                      and r.status = 'done') as answered
              from poll p
             where p.created_by = %s or %s or %s
             order by p.created_at desc, p.id desc
            """,
            (viewer["id"], "*" in caps, "poll.results.all" in caps)).fetchall()
    return {"ok": True, "polls": [
        {**{key: row[key] for key in ("id", "title", "status", "questions",
                                      "invited", "answered")},
         "created_at": _iso(row["created_at"])} for row in rows]}


# ------------------------------------------------------------ Mini App: конструктор
class QuestionIn(core.ApiIn):
    position: int = Field(default=0, ge=0, le=500)
    text: str = ""
    kind: str = "single"
    required: bool = True
    free_text: bool = False
    max_choices: int | None = None
    options: list[dict] = Field(default_factory=list)


class PollSaveIn(core.ApiIn):
    poll_id: int | None = None
    title: str = ""
    intro: str | None = None
    anonymous: bool = False
    closes_at: str | None = None
    questions: list[QuestionIn] = Field(default_factory=list)


def _parse_closes_at(value: str | None) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "Дата закрытия не разобрана.",
                       code="bad_closes_at") from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _write_questions(conn, poll_id: int, questions: list[dict]) -> None:
    """Переписать вопросы черновика целиком: черновик, ответов ещё нет."""
    conn.execute("delete from poll_question where poll_id = %s", (poll_id,))
    for question in questions:
        row = conn.execute(
            """insert into poll_question (poll_id, position, text, kind, required,
                                          free_text, max_choices)
               values (%s,%s,%s,%s,%s,%s,%s) returning id""",
            (poll_id, question["position"], question["text"], question["kind"],
             question["required"], question["free_text"], question["max_choices"])).fetchone()
        for option in question["options"]:
            conn.execute(
                """insert into poll_option (question_id, position, code, label)
                   values (%s,%s,%s,%s)""",
                (row["id"], option["position"], option["code"], option["label"]))


@router.post("/webapp/polls/save")
def polls_save(body: PollSaveIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Создать или поправить черновик. Вопросы приходят целиком, как их видит конструктор."""
    viewer = core._webapp_require(who, "poll.manage", deny_code="no_poll_manage",
                                  deny_text=DENY_MANAGE_TEXT)
    title = poll_store.clean_text(body.title, limit=300)
    if not title:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "У опроса нет темы.", code="empty_title")
    try:
        questions = poll_store.validate_questions([item.model_dump() for item in body.questions])
    except poll_store.DraftError as exc:
        raise ApiError(status.HTTP_400_BAD_REQUEST, exc.text, code=exc.code) from exc
    closes_at = _parse_closes_at(body.closes_at)
    caps = viewer.get("caps") or []
    with core.pool.connection() as conn:
        if body.poll_id:
            poll = _poll_or_404(conn, body.poll_id)
            _own_draft(conn, viewer, poll, caps)
            if poll["status"] != "draft":
                raise ApiError(status.HTTP_409_CONFLICT,
                               "Опрос уже отправлен: правка вопросов запрещена.",
                               code="poll_running")
            poll_id = poll["id"]
            conn.execute(
                """update poll set title=%s, intro=%s, anonymous=%s, closes_at=%s,
                          updated_at=now() where id=%s""",
                (title, poll_store.clean_text(body.intro, limit=2000) or None,
                 body.anonymous, closes_at, poll_id))
            _write_questions(conn, poll_id, questions)
            core.audit(conn, "human", viewer["id"], "poll.save", "poll", str(poll_id),
                       after={"title": title, "questions": len(questions), "updated": True})
        else:
            poll_id = conn.execute(
                """insert into poll (title, intro, anonymous, closes_at, created_by)
                   values (%s,%s,%s,%s,%s) returning id""",
                (title, poll_store.clean_text(body.intro, limit=2000) or None,
                 body.anonymous, closes_at, viewer["id"])).fetchone()["id"]
            _write_questions(conn, poll_id, questions)
            core.audit(conn, "human", viewer["id"], "poll.save", "poll", str(poll_id),
                       after={"title": title, "questions": len(questions)})
    return {"ok": True, "poll_id": poll_id, "status": "draft"}


@router.post("/webapp/polls/copy")
def polls_copy(body: PollIdIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Копия отправленного опроса — новым черновиком (docs/development/166 §2, §4).

    Порядок проверок — как у `polls_start`, с архивом впереди: право → существование
    → видимость → статус источника → наличие вопросов. Проверяется право **прочитать**
    (`_require_results`), а не править: копируют то, что человек видит в сводке.
    Адресаты, зрители, прохождения и сроки не переносятся — копия не отправлена.
    """
    viewer = core._webapp_require(who, "poll.manage", deny_code="no_poll_manage",
                                  deny_text=DENY_MANAGE_TEXT)
    with core.pool.connection() as conn:
        poll = _poll_or_404(conn, body.poll_id)
        _require_results(conn, viewer, poll)
        if poll["status"] == "draft":
            raise ApiError(status.HTTP_409_CONFLICT,
                           "Черновик не копируют: откройте его в конструкторе.",
                           code="poll_draft")
        # Пустой источник проверяем до `validate_questions`: иначе тот же случай
        # ответил бы второй фразой («В опросе нет ни одного вопроса»), 166 §5.3.
        questions = _questions(conn, poll["id"])
        if not questions:
            raise ApiError(status.HTTP_400_BAD_REQUEST,
                           "В опросе нет вопросов: копировать нечего.",
                           code="no_questions")
        try:
            draft = poll_store.draft_copy(poll, questions)
        except poll_store.DraftError as exc:
            raise ApiError(status.HTTP_400_BAD_REQUEST, exc.text, code=exc.code) from exc
        new_id = conn.execute(
            """insert into poll (title, intro, anonymous, created_by)
               values (%s,%s,%s,%s) returning id""",
            (draft["title"], draft["intro"], draft["anonymous"],
             viewer["id"])).fetchone()["id"]
        _write_questions(conn, new_id, draft["questions"])
        core.audit(conn, "human", viewer["id"], "poll.copy", "poll", str(new_id),
                   after={"source_poll_id": poll["id"], "title": draft["title"],
                          "questions": len(draft["questions"])})
    return {"ok": True, "poll_id": new_id, "title": draft["title"], "status": "draft",
            "url": f"/webapp/polls/edit?poll_id={new_id}&note=copy"}


@router.post("/webapp/polls/delete")
def polls_delete(body: PollIdIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Удалить черновик опроса — V-136 (решение владельца 23.11.2026).

    Удаляется только черновик: у отправленного опроса есть прохождения и ответы,
    и «удалить» там значило бы стереть чужие ответы. Второй стоп — любое прохождение
    у черновика (в норме его быть не может, но полагаться на «в норме» нельзя).
    Дочерние строки уходят каскадом (`065_polls.sql`), поэтому одной командой.
    """
    viewer = core._webapp_require(who, "poll.manage", deny_code="no_poll_manage",
                                  deny_text=DENY_MANAGE_TEXT)
    with core.pool.connection() as conn:
        poll = _poll_or_404(conn, body.poll_id)
        _own_draft(conn, viewer, poll, viewer.get("caps") or [])
        if poll["status"] != "draft":
            raise ApiError(status.HTTP_409_CONFLICT,
                           "Отправленный опрос не удаляют: в нём есть ответы. Если он больше "
                           "не нужен — закройте его.", code="poll_not_draft")
        runs = conn.execute("select count(*) as c from poll_run where poll_id = %s",
                            (poll["id"],)).fetchone()["c"]
        if runs:
            raise ApiError(status.HTTP_409_CONFLICT,
                           "У опроса есть прохождения: удалить нельзя.", code="poll_has_runs")
        questions = len(_questions(conn, poll["id"]))
        conn.execute("delete from poll where id = %s", (poll["id"],))
        core.audit(conn, "human", viewer["id"], "poll.delete", "poll", str(poll["id"]),
                   before={"title": poll["title"], "status": poll["status"],
                           "questions": questions})
    return {"ok": True, "poll_id": body.poll_id}


@router.get("/webapp/polls/edit/data")
def polls_edit_data(poll_id: int = Query(ge=1),
                    who: dict = Depends(core.webapp_actor)) -> dict:
    """Черновик для конструктора: вопросы, варианты и признак «можно править»."""
    viewer = core._webapp_require(who, "poll.manage", deny_code="no_poll_manage",
                                  deny_text=DENY_MANAGE_TEXT)
    with core.pool.connection() as conn:
        poll = _poll_or_404(conn, poll_id)
        caps = viewer.get("caps") or []
        if int(poll["created_by"]) != int(viewer["id"]) and not ("*" in caps or
                                                                "poll.results.all" in caps):
            raise ApiError(status.HTTP_403_FORBIDDEN, DENY_MANAGE_TEXT, code="alien_poll")
        questions = _questions(conn, poll_id)
    return {"ok": True,
            "poll": {"id": poll["id"], "title": poll["title"], "intro": poll["intro"],
                     "status": poll["status"], "anonymous": poll["anonymous"],
                     "closes_at": _iso(poll["closes_at"])},
            "questions": questions,
            "can_edit": poll["status"] == "draft"}


# -------------------------------------------------------------- Mini App: адресаты
class RuleIn(core.ApiIn):
    kind: str
    role: str | None = None
    project_id: int | None = None


class AudienceIn(core.ApiIn):
    poll_id: int
    rules: list[RuleIn] = Field(default_factory=list)
    include: list[int] = Field(default_factory=list)
    exclude: list[int] = Field(default_factory=list)
    viewers: dict = Field(default_factory=dict)


def _active_people(conn) -> list[dict]:
    """Действующие участники с ролями: роли берутся из `participant_role`."""
    rows = conn.execute(
        """select p.id, p.display_name, p.tg_chat_id, p.role,
                  coalesce((select array_agg(r.role) from participant_role r
                             where r.participant_id = p.id and r.revoked_at is null),
                           array[p.role]) as roles
             from participant p where p.status = 'active'
            order by p.display_name, p.id""").fetchall()
    return [dict(row) for row in rows]


def _projects(conn) -> list[dict]:
    rows = conn.execute(
        "select id, name from project where kind = 'show' and status <> 'archive' "
        "order by name").fetchall()
    return [dict(row) for row in rows]


def _project_members(conn, project_ids: list[int]) -> dict[int, list[int]]:
    if not project_ids:
        return {}
    rows = conn.execute(
        "select distinct project_id, unnest(participant_ids) as participant_id "
        "from event_plan where project_id = any(%s) and status <> 'cancelled'",
        (list(project_ids),)).fetchall()
    out: dict[int, list[int]] = {}
    for row in rows:
        out.setdefault(row["project_id"], []).append(row["participant_id"])
    return out


def _audience(conn, poll_id: int) -> tuple[list[dict], list[int], list[int], dict]:
    """Правила, галочки и исключения опроса в том виде, в каком их сохранил автор."""
    rules = [dict(row) for row in conn.execute(
        "select kind, role, project_id from poll_audience where poll_id = %s order by id",
        (poll_id,)).fetchall()]
    people_rows = conn.execute(
        "select participant_id, mode from poll_person where poll_id = %s", (poll_id,)).fetchall()
    include = [row["participant_id"] for row in people_rows if row["mode"] == "include"]
    exclude = [row["participant_id"] for row in people_rows if row["mode"] == "exclude"]
    viewers = {"people": [], "roles": []}
    for row in conn.execute(
            "select participant_id, role from poll_viewer where poll_id = %s", (poll_id,)).fetchall():
        if row["participant_id"]:
            viewers["people"].append(row["participant_id"])
        else:
            viewers["roles"].append(row["role"])
    return rules, include, exclude, viewers


def _resolve_audience(conn, rules: list[dict], include: list[int],
                      exclude: list[int]) -> list[dict]:
    people = _active_people(conn)
    project_ids = [int(rule["project_id"]) for rule in rules if rule.get("kind") == "project"]
    members = _project_members(conn, project_ids)
    try:
        ids = poll_store.audience_ids(rules=rules, include=include, exclude=exclude,
                                      people=people, project_members=members)
    except poll_store.DraftError as exc:
        raise ApiError(status.HTTP_400_BAD_REQUEST, exc.text, code=exc.code) from exc
    by_id = {person["id"]: person for person in people}
    return [by_id[pid] for pid in ids if pid in by_id]


@router.get("/webapp/polls/audience/data")
def polls_audience_data(poll_id: int = Query(ge=1),
                        who: dict = Depends(core.webapp_actor)) -> dict:
    """Экран адресатов: наборы, люди с галочками и счётчики до отправки."""
    viewer = core._webapp_require(who, "poll.manage", deny_code="no_poll_manage",
                                  deny_text=DENY_MANAGE_TEXT)
    with core.pool.connection() as conn:
        poll = _poll_or_404(conn, poll_id)
        _own_draft(conn, viewer, poll, viewer.get("caps") or [])
        rules, include, exclude, viewers = _audience(conn, poll_id)
        people = _active_people(conn)
        recipients = _resolve_audience(conn, rules, include, exclude)
        frozen = conn.execute("select count(*) as n from poll_run where poll_id = %s",
                              (poll_id,)).fetchone()["n"]
    chosen = {person["id"] for person in recipients}
    return {"ok": True,
            "poll": {"id": poll["id"], "title": poll["title"], "status": poll["status"]},
            "rules": rules, "include": include, "exclude": exclude, "viewers": viewers,
            "people": [{"id": person["id"], "display_name": person["display_name"],
                        "roles": list(person["roles"]),
                        "has_chat": person["tg_chat_id"] is not None,
                        "checked": person["id"] in chosen} for person in people],
            "projects": _projects(conn),
            "will_send": sum(1 for person in recipients if person["tg_chat_id"] is not None),
            "without_telegram": sum(1 for person in recipients if person["tg_chat_id"] is None),
            "total": len(recipients),
            "frozen": frozen > 0}


@router.post("/webapp/polls/audience")
def polls_audience_save(body: AudienceIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Сохранить набор адресатов и вернуть счётчик — до отправки, как у рассылки."""
    viewer = core._webapp_require(who, "poll.manage", deny_code="no_poll_manage",
                                  deny_text=DENY_MANAGE_TEXT)
    with core.pool.connection() as conn:
        poll = _poll_or_404(conn, body.poll_id)
        _own_draft(conn, viewer, poll, viewer.get("caps") or [])
        if conn.execute("select 1 from poll_run where poll_id = %s limit 1",
                        (poll["id"],)).fetchone():
            raise ApiError(status.HTTP_409_CONFLICT,
                           "Опрос уже отправлен: адресаты заморожены.",
                           code="poll_running")
        rules = [rule.model_dump() for rule in body.rules]
        recipients = _resolve_audience(conn, rules, body.include, body.exclude)
        conn.execute("delete from poll_audience where poll_id = %s", (poll["id"],))
        for rule in rules:
            conn.execute(
                "insert into poll_audience (poll_id, kind, role, project_id) "
                "values (%s,%s,%s,%s)",
                (poll["id"], rule["kind"],
                 rule.get("role") if rule["kind"] == "role" else None,
                 rule.get("project_id") if rule["kind"] == "project" else None))
        conn.execute("delete from poll_person where poll_id = %s", (poll["id"],))
        for participant_id in body.include:
            conn.execute("insert into poll_person (poll_id, participant_id, mode) "
                         "values (%s,%s,'include') on conflict do nothing",
                         (poll["id"], participant_id))
        for participant_id in body.exclude:
            conn.execute("insert into poll_person (poll_id, participant_id, mode) "
                         "values (%s,%s,'exclude') on conflict do nothing",
                         (poll["id"], participant_id))
        viewers = body.viewers or {}
        conn.execute("delete from poll_viewer where poll_id = %s", (poll["id"],))
        for participant_id in viewers.get("people") or []:
            conn.execute("insert into poll_viewer (poll_id, participant_id) values (%s,%s) "
                         "on conflict do nothing", (poll["id"], participant_id))
        for role in viewers.get("roles") or []:
            if role in ROLE_TITLES:
                conn.execute("insert into poll_viewer (poll_id, role) values (%s,%s) "
                             "on conflict do nothing", (poll["id"], role))
        conn.execute("update poll set updated_at = now() where id = %s", (poll["id"],))
        core.audit(conn, "human", viewer["id"], "poll.audience", "poll", str(poll["id"]),
                   after={"rules": len(rules), "will_send": len(recipients)})
    return {"ok": True, "will_send": sum(1 for p in recipients if p["tg_chat_id"] is not None),
            "without_telegram": sum(1 for p in recipients if p["tg_chat_id"] is None),
            "total": len(recipients)}


# --------------------------------------------------------------- Mini App: отправка
class PollIdIn(core.ApiIn):
    poll_id: int


def _invitation_markup(run_id: int, question: dict) -> dict:
    """Первое сообщение — push: кнопки собираются здесь, чтобы их вёз `payload.markup`.

    У `text`/`number` вариантов нет, а приглашение идёт через очередь и состояние
    `await_text` выставить не может — поэтому вопрос свободного ввода несёт кнопку
    входа в ответ (`p:f`), иначе человек получает пустой `inline_keyboard` и ответить
    ему нечем (162 §5.2, §14.6).
    """
    rows = []
    if question["kind"] in ("single", "multi"):
        for option in question["options"]:
            rows.append([{"text": option["label"],
                          "callback_data": f"p:o:{run_id}:{question['position']}:{option['code']}"}])
    elif question["kind"] == "yesno":
        rows.append([{"text": "Да", "callback_data": f"p:y:{run_id}:{question['position']}"},
                     {"text": "Нет", "callback_data": f"p:x:{run_id}:{question['position']}"}])
    elif question["kind"] in ("text", "number"):
        rows.append([{"text": "Ответить сообщением",
                      "callback_data": f"p:f:{run_id}:{question['position']}"}])
    return {"inline_keyboard": rows}


@router.post("/webapp/polls/start")
def polls_start(body: PollIdIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Отправить приглашения: снимок адресатов в `poll_run` + записи очереди `notification`."""
    viewer = core._webapp_require(who, "poll.manage", deny_code="no_poll_manage",
                                  deny_text=DENY_MANAGE_TEXT)
    with core.pool.connection() as conn:
        poll = _poll_or_404(conn, body.poll_id)
        _own_draft(conn, viewer, poll, viewer.get("caps") or [])
        if poll["status"] != "draft":
            raise ApiError(status.HTTP_409_CONFLICT, "Опрос уже отправлен.",
                           code="already_running")
        questions = _questions(conn, poll["id"])
        if not questions:
            raise ApiError(status.HTTP_400_BAD_REQUEST, "В опросе нет вопросов.",
                           code="no_questions")
        rules, include, exclude, _viewers = _audience(conn, poll["id"])
        recipients = _resolve_audience(conn, rules, include, exclude)
        if not recipients:
            raise ApiError(status.HTTP_409_CONFLICT, "Некому отправлять: набор пуст.",
                           code="no_recipients")
        first = questions[0]
        invited = 0
        skipped_no_chat = 0
        for person in recipients:
            run_id = conn.execute(
                """insert into poll_run (poll_id, participant_id, status, position)
                   values (%s,%s,'invited',1)
                   on conflict (poll_id, participant_id) do update
                      set status = 'invited', updated_at = now()
                   returning id""", (poll["id"], person["id"])).fetchone()["id"]
            if person["tg_chat_id"] is None:
                skipped_no_chat += 1
                continue
            invited += 1
            payload = {"title": f"{poll['title']} · вопрос 1 из {len(questions)}",
                       "body": first["text"], "poll_id": poll["id"], "run_id": run_id,
                       "markup": _invitation_markup(run_id, first)}
            conn.execute(
                """insert into notification (recipient_id, kind, payload, dedupe_key,
                                             scheduled_at)
                   values (%s, 'poll', %s, %s, now())
                   on conflict (dedupe_key, recipient_id) do nothing""",
                (person["id"], json.dumps(payload, ensure_ascii=False),
                 f"poll:{poll['id']}:{person['id']}:1"))
        conn.execute("update poll set status='running', started_at=now(), updated_at=now() "
                     "where id=%s", (poll["id"],))
        core.audit(conn, "human", viewer["id"], "poll.start", "poll", str(poll["id"]),
                   after={"invited": invited, "skipped_no_chat": skipped_no_chat})
    return {"ok": True, "invited": invited, "skipped_no_chat": skipped_no_chat,
            "status": "running"}


@router.post("/webapp/polls/close")
def polls_close(body: PollIdIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Закрыть опрос: строки не удаляются, ответы больше не принимаются (D10)."""
    viewer = core._webapp_require(who, "poll.manage", deny_code="no_poll_manage",
                                  deny_text=DENY_MANAGE_TEXT)
    with core.pool.connection() as conn:
        poll = _poll_or_404(conn, body.poll_id)
        _own_draft(conn, viewer, poll, viewer.get("caps") or [])
        if poll["status"] == "closed":
            raise ApiError(status.HTTP_409_CONFLICT, "Опрос уже закрыт.", code="already_closed")
        row = conn.execute("update poll set status='closed', closed_at=now(), updated_at=now() "
                           "where id=%s returning closed_at", (poll["id"],)).fetchone()
        core.audit(conn, "human", viewer["id"], "poll.close", "poll", str(poll["id"]))
    return {"ok": True, "status": "closed", "closed_at": _iso(row["closed_at"])}


# -------------------------------------------------------------- Mini App: результаты
def _answer_rows(conn, poll_id: int) -> list[dict]:
    rows = conn.execute(
        """select a.run_id, a.question_id, a.option_codes, a.text_value, a.number_value,
                  a.skipped, a.answered_at, r.participant_id, p.display_name
             from poll_answer a
             join poll_run r on r.id = a.run_id
             join poll_question q on q.id = a.question_id
             left join participant p on p.id = r.participant_id
            where q.poll_id = %s
            order by a.question_id, a.answered_at""", (poll_id,)).fetchall()
    return [dict(row) for row in rows]


@router.get("/webapp/polls/results/data")
def polls_results_data(poll_id: int = Query(ge=1),
                       who: dict = Depends(core.webapp_actor)) -> dict:
    """Сводка по вопросам и кто не ответил (`poll_run` со статусом не `done`)."""
    viewer = core._webapp_viewer(who)
    with core.pool.connection() as conn:
        poll = _poll_or_404(conn, poll_id)
        _require_results(conn, viewer, poll)
        questions = _questions(conn, poll_id)
        answers = _answer_rows(conn, poll_id)
        by_question: dict[int, list[dict]] = {}
        for answer in answers:
            by_question.setdefault(answer["question_id"], []).append(answer)
        anonymous = bool(poll["anonymous"])
        out = []
        for question in questions:
            rows = by_question.get(question["id"], [])
            if anonymous:
                rows = [dict(row, participant_id=None, display_name=None) for row in rows]
            out.append({"id": question["id"], "position": question["position"],
                        "text": question["text"], "kind": question["kind"],
                        "required": question["required"], "free_text": question["free_text"],
                        "options": question["options"],
                        "summary": poll_store.summarize(question, rows)})
        invited = conn.execute("select count(*) as n from poll_run where poll_id = %s",
                               (poll_id,)).fetchone()["n"]
        answered = conn.execute("select count(*) as n from poll_run where poll_id = %s "
                                "and status = 'done'", (poll_id,)).fetchone()["n"]
        not_answered = [dict(row) for row in conn.execute(
            """select r.participant_id, p.display_name, r.position
                 from poll_run r join participant p on p.id = r.participant_id
                where r.poll_id = %s and r.status <> 'done'
                order by p.display_name""", (poll_id,)).fetchall()]
    return {"ok": True,
            "poll": {"id": poll["id"], "title": poll["title"], "status": poll["status"],
                     "anonymous": anonymous, "closes_at": _iso(poll["closes_at"]),
                     "closed_at": _iso(poll["closed_at"])},
            "answered": answered, "invited": invited,
            "questions": out, "not_answered": not_answered}


LINK_EXPIRED_PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ссылка устарела</title>
</head>
<body style="margin:0;padding:24px;font-family:-apple-system,system-ui,sans-serif;line-height:1.5">
<h1 style="font-size:20px;margin:0 0 12px">Ссылка устарела, откройте результаты заново</h1>
<p style="margin:0;color:#555">Файл выдаётся по короткой ссылке на 15 минут. Вернитесь в Mini App,
откройте результаты опроса и нажмите «Экспорт» ещё раз.</p>
</body>
</html>
"""


def _link_expired_response() -> HTMLResponse:
    """Отказ по ссылке — страницей со словами, а не JSON: её показывает браузер телефона."""
    return HTMLResponse(LINK_EXPIRED_PAGE, status_code=status.HTTP_401_UNAUTHORIZED,
                        headers={"Cache-Control": "no-store"})


@router.get("/webapp/polls/export/link")
def polls_export_link(poll_id: int = Query(ge=1), fmt: str = Query(default="csv", alias="format"),
                      who: dict = Depends(core.webapp_actor)) -> dict:
    """Короткая ссылка на файл результатов: право проверяется здесь, а не в браузере.

    Ссылку открывает `tg.openLink` — браузер телефона не присылает Authorization,
    поэтому `polls_export` верит подписи этой ссылки (15 минут, предмет внутри).
    """
    if fmt not in ("csv", "md"):
        raise ApiError(status.HTTP_400_BAD_REQUEST, "Формат: csv или md.",
                       code="bad_format")
    viewer = core._webapp_viewer(who)
    with core.pool.connection() as conn:
        poll = _poll_or_404(conn, poll_id)
        _require_results(conn, viewer, poll)
    token = webapp.issue_export_link(poll_id, fmt, core.WEBAPP_SECRET)
    return {"ok": True, "expires_in": webapp.LINK_TTL_SECONDS,
            "url": f"/webapp/polls/export?poll_id={poll_id}&format={fmt}&token={token}"}


@router.get("/webapp/polls/export")
def polls_export(poll_id: int = Query(ge=1), fmt: str = Query(default="csv", alias="format"),
                 token: str = Query(default=""),
                 who: dict | None = Depends(core.webapp_actor_optional)) -> Response:
    """Файл результатов: CSV для таблицы, Markdown для журнала исследования (V-090: скачивание).

    Два входа: с токеном Mini App (заголовок) и по подписанной ссылке из `export/link`
    (браузер телефона заголовков не носит). Право проверено на выдаче ссылки.
    """
    if fmt not in ("csv", "md"):
        raise ApiError(status.HTTP_400_BAD_REQUEST, "Формат: csv или md.",
                       code="bad_format")
    viewer = None
    if who is None:
        try:
            link = webapp.verify_export_link(token, core.WEBAPP_SECRET)
        except InitDataError:
            return _link_expired_response()
        if link["poll_id"] != poll_id or link["fmt"] != fmt:
            return _link_expired_response()
    else:
        viewer = core._webapp_viewer(who)
    with core.pool.connection() as conn:
        poll = _poll_or_404(conn, poll_id)
        if viewer is not None:
            _require_results(conn, viewer, poll)
        questions = _questions(conn, poll_id)
        answers = _answer_rows(conn, poll_id)
    people = {answer["participant_id"]: answer["display_name"] for answer in answers}
    if fmt == "csv":
        data = poll_store.export_csv(poll_store.export_rows(poll, questions, answers, people))
        return Response(data.encode("utf-8"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": files_store.attachment_header(
                            f"poll-{poll_id}.csv")})
    by_question: dict[int, list[dict]] = {}
    for answer in answers:
        by_question.setdefault(answer["question_id"], []).append(answer)
    summaries = {question["id"]: poll_store.summarize(question,
                                                      by_question.get(question["id"], []))
                 for question in questions}
    data = poll_store.export_markdown(poll, questions, summaries,
                                      anonymous=bool(poll["anonymous"]))
    return Response(data.encode("utf-8"), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": files_store.attachment_header(
                        f"poll-{poll_id}.md")})


# ----------------------------------------------------------------- диалог в чате
def _run_or_404(conn, run_id: int, participant_id: int) -> dict:
    row = conn.execute(
        """select r.*, p.title, p.intro, p.status as poll_status, p.closes_at, p.closed_at
             from poll_run r join poll p on p.id = r.poll_id
            where r.id = %s and r.participant_id = %s""",
        (run_id, participant_id)).fetchone()
    if row is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "Прохождение не найдено.",
                       code="run_not_found")
    return dict(row)


def _step(conn, run: dict, position: int | None = None) -> dict:
    """Состояние шага: вопрос, прогресс, прошлый ответ. Форма — для сборки сообщения в чате."""
    questions = _questions(conn, run["poll_id"])
    total = len(questions)
    position = position if position is not None else int(run["position"])
    position = max(1, min(position, max(total, 1)))
    question = next((item for item in questions if item["position"] == position),
                    questions[0] if questions else None)
    chosen: list[str] = []
    chosen_text = None
    number_value = None
    if question is not None:
        answer = conn.execute(
            "select option_codes, text_value, number_value from poll_answer "
            "where run_id = %s and question_id = %s", (run["id"], question["id"])).fetchone()
        if answer:
            chosen = list(answer["option_codes"] or [])
            chosen_text = answer["text_value"]
            number_value = None if answer["number_value"] is None else float(answer["number_value"])
    poll = {"status": run["poll_status"], "closes_at": run.get("closes_at")}
    return {"run_id": run["id"], "position": position, "total": total,
            "title": run["title"], "intro": run["intro"],
            "kind": question["kind"] if question else "text",
            "text": question["text"] if question else "",
            "required": question["required"] if question else True,
            "free_text": question["free_text"] if question else False,
            "max_choices": question["max_choices"] if question else None,
            "options": question["options"] if question else [],
            "chosen": chosen, "chosen_text": chosen_text, "number_value": number_value,
            "closed": _closed(poll),
            "closes_at": _iso(run.get("closes_at")),
            # Когда опрос закрыл организатор: срок `closes_at` у такого опроса может
            # быть пустым, а подпись «Опрос закрыт 12 ноября» — с датой (164 §12.5).
            "closed_at": _iso(run.get("closed_at")),
            "done": run["status"] == "done"}


def _set_position(conn, run: dict, position: int, *, done: bool = False) -> None:
    """Сдвинуть прохождение и отменить отложенное приглашение (V-096).

    Отмена живёт здесь, а не в обработчике ответа: шаг из чата проходит через ту же
    функцию, и для чата это пустая операция — приглашение уже `sent`. Правило одно
    независимо от входа.
    """
    conn.execute(
        """update poll_run set position = %s, status = %s, updated_at = now(),
                  started_at = coalesce(started_at, now()),
                  finished_at = case when %s then now() else finished_at end
            where id = %s""",
        (position, "done" if done else "in_progress", done, run["id"]))
    poll_store.cancel_invites(conn, run["id"], run["participant_id"])


def _save_answer(conn, run: dict, question: dict, *, codes: list[str],
                 text_value: str | None, number_value, skipped: bool) -> None:
    conn.execute(
        """insert into poll_answer (run_id, question_id, option_codes, text_value,
                                    number_value, skipped, answered_at)
           values (%s,%s,%s,%s,%s,%s,now())
           on conflict (run_id, question_id) do update
              set option_codes = excluded.option_codes,
                  text_value = excluded.text_value,
                  number_value = excluded.number_value,
                  skipped = excluded.skipped,
                  answered_at = now()""",
        (run["id"], question["id"], poll_store.answer_codes(question["kind"], codes),
         text_value, number_value, skipped))


@router.get("/tools/polls/mine")
def polls_mine(who: dict = Depends(core.current)) -> dict:
    """Незаконченные прохождения человека — для кнопки «Опросы» и «Продолжить» (D4).

    Чтение одно на три потребителя (`poll_store.my_runs`, docs/development/164 §12.2),
    а отбор здесь свой и прежний: только открытые опросы и прежний набор полей —
    иначе чат увидел бы пройденные (регресс AC-18).
    """
    with core.pool.connection() as conn:
        runs = poll_store.my_runs(conn, who["id"])
    return {"ok": True, "runs": [
        {"run_id": run["run_id"], "status": run["status"], "position": run["position"],
         "poll_id": run["poll_id"], "title": run["title"], "total": run["total"]}
        for run in runs
        if run["status"] in ("invited", "in_progress") and run["poll_status"] == "running"]}


@router.get("/tools/polls/step")
def polls_step(run_id: int = Query(ge=1), position: int | None = Query(default=None),
               who: dict = Depends(core.current)) -> dict:
    """Текущий вопрос прохождения; перезапуск gateway прогресс не теряет (AC-8)."""
    with core.pool.connection() as conn:
        run = _run_or_404(conn, run_id, who["id"])
        return {"ok": True, "step": _step(conn, run, position)}


class PollAnswerIn(core.ApiIn):
    run_id: int
    position: int | None = None
    action: str
    code: str | None = None
    codes: list[str] = Field(default_factory=list)
    text: str | None = None


@router.post("/tools/polls/answer")
def polls_answer(body: PollAnswerIn, who: dict = Depends(core.current)) -> dict:
    """Шаг ответа из чата: та же логика шага, что у приложения (D5, AC-13)."""
    with core.pool.connection() as conn:
        return _apply_answer(conn, who, body)


@router.post("/webapp/polls/answer")
def webapp_polls_answer(body: PollAnswerIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Шаг ответа из Mini App: тот же `_apply_answer`, другой вход (docs/164 §12.3).

    Адрес отдельный не по вкусу: `/tools/polls/*` закрыт сервисным токеном и
    заголовком `X-Actor-Chat-Id`, а у браузера вход другой — `webapp_actor`.
    Второй обработчик ответа означал бы два поведения на один опрос.
    """
    with core.pool.connection() as conn:
        return _apply_answer(conn, who, body)


def _apply_answer(conn, who: dict, body: PollAnswerIn) -> dict:
    """Шаг ответа: выбор, «Дальше», «Да/Нет», «Назад», «Пропустить», свободный ввод.

    Шаг не идёт через очередь уведомлений: человек только что нажал кнопку, значит
    это ответ на его действие, а не неожиданное сообщение (D5, AC-13).

    `who` — человек, чьё прохождение двигаем: и чат (`core.current`), и приложение
    (`core.webapp_actor`) отдают `id` участника, а `_run_or_404` отбирает по нему —
    поэтому чужой `run_id` получает `404 run_not_found` из обоих входов.
    """
    run = _run_or_404(conn, body.run_id, who["id"])
    step = _step(conn, run, body.position)
    if step["closed"]:
        return {"ok": True, "closed": True, "done": False, "step": step}
    if step["done"]:
        return {"ok": True, "closed": False, "done": True, "step": step}
    questions = _questions(conn, run["poll_id"])
    question = next((item for item in questions if item["position"] == step["position"]),
                    None)
    if question is None:
        raise ApiError(status.HTTP_409_CONFLICT, "Вопрос не найден.",
                       code="bad_position")
    action = (body.action or "").strip()
    codes = list(body.codes or [])
    if body.code:
        codes = [body.code]
    if action in ("o", "choose"):
        if question["kind"] not in ("single", "multi", "yesno"):
            raise ApiError(status.HTTP_400_BAD_REQUEST, "У вопроса нет вариантов.",
                           code="bad_action")
        if question["kind"] == "multi":
            current = conn.execute(
                "select option_codes from poll_answer where run_id=%s and question_id=%s",
                (run["id"], question["id"])).fetchone()
            merged = list((current["option_codes"] if current else []) or [])
            for code in codes:
                merged = poll_store.merge_choice(
                    "multi", merged, code,
                    allowed=[option["code"] for option in question["options"]],
                    max_choices=question["max_choices"])
            text_value = None
            existing = conn.execute(
                "select text_value from poll_answer where run_id=%s and question_id=%s",
                (run["id"], question["id"])).fetchone()
            if existing:
                text_value = existing["text_value"]
            _save_answer(conn, run, question, codes=merged, text_value=text_value,
                         number_value=None, skipped=False)
            return {"ok": True, "closed": False, "done": False,
                    "step": _step(conn, run, step["position"])}
        _save_answer(conn, run, question, codes=codes, text_value=None,
                     number_value=None, skipped=False)
        return _advance(conn, run, question, questions, step["position"])
    if action in ("y", "yes", "x", "no"):
        code = "y" if action in ("y", "yes") else "n"
        _save_answer(conn, run, question, codes=[code], text_value=None,
                     number_value=None, skipped=False)
        return _advance(conn, run, question, questions, step["position"])
    if action in ("s", "skip"):
        if question["required"]:
            raise ApiError(status.HTTP_409_CONFLICT,
                           "Этот вопрос обязательный: его нельзя пропустить.",
                           code="required_question")
        _save_answer(conn, run, question, codes=[], text_value=None,
                     number_value=None, skipped=True)
        return _advance(conn, run, question, questions, step["position"])
    if action in ("b", "back"):
        position = max(1, step["position"] - 1)
        _set_position(conn, run, position)
        return {"ok": True, "closed": False, "done": False,
                "step": _step(conn, run, position)}
    if action in ("n", "next"):
        existing = conn.execute(
            "select option_codes, text_value from poll_answer "
            "where run_id=%s and question_id=%s", (run["id"], question["id"])).fetchone()
        if existing is None:
            raise ApiError(status.HTTP_409_CONFLICT, "Сначала выберите варианты.",
                           code="no_choice")
        return _advance(conn, run, question, questions, step["position"])
    if action in ("text", "free"):
        text = (body.text or "").strip()
        if not text:
            raise ApiError(status.HTTP_400_BAD_REQUEST, "Пустой ответ.",
                           code="empty_text")
        if question["kind"] == "number":
            number = poll_store.parse_number(text)
            if number is None:
                return {"ok": True, "closed": False, "done": False,
                        "retry": True, "message": "Не понял число. Например: 4",
                        "step": _step(conn, run, step["position"])}
            _save_answer(conn, run, question, codes=[], text_value=None,
                         number_value=number, skipped=False)
            return _advance(conn, run, question, questions, step["position"])
        if question["kind"] in ("single", "multi") and not question["free_text"]:
            raise ApiError(status.HTTP_409_CONFLICT,
                           "У вопроса нет свободного ответа.", code="bad_action")
        existing = conn.execute(
            "select option_codes from poll_answer where run_id=%s and question_id=%s",
            (run["id"], question["id"])).fetchone()
        codes = list((existing["option_codes"] if existing else []) or [])
        _save_answer(conn, run, question, codes=codes,
                     text_value=poll_store.clean_text(text, limit=2000),
                     number_value=None, skipped=False)
        return _advance(conn, run, question, questions, step["position"])
    raise ApiError(status.HTTP_400_BAD_REQUEST, "Неизвестное действие.",
                   code="bad_action")


def _advance(conn, run: dict, question: dict, questions: list[dict], position: int) -> dict:
    """Следующий вопрос или конец: прогресс словами считает `position`, а не счётчик в памяти."""
    total = len(questions)
    if position >= total:
        _set_position(conn, run, total, done=True)
        updated = _run_or_404(conn, run["id"], run["participant_id"])
        return {"ok": True, "closed": False, "done": True,
                "step": _step(conn, updated, total)}
    _set_position(conn, run, position + 1)
    updated = _run_or_404(conn, run["id"], run["participant_id"])
    return {"ok": True, "closed": False, "done": False,
            "step": _step(conn, updated, position + 1)}


# ------------------------------------------------- «Мои опросы» в Mini App (E8-D4)
# Адреса отдельные от `/tools/polls/*`: те закрыты сервисным токеном и заголовком
# `X-Actor-Chat-Id` (core.py:331-340), а у браузера вход другой — `webapp_actor`.
# Права `poll.manage` здесь нет намеренно: экран показывает только свои прохождения,
# и отбирает их `participant_id`, а не возможность (docs/development/164 §12.1, §12.8).

def _mine_row(run: dict, group: str) -> dict:
    """Строка списка в форме ответа: у открытых есть адрес, у остальных только подпись."""
    row = {"run_id": run["run_id"], "poll_id": run["poll_id"], "title": run["title"],
           "meta": poll_store.run_meta(run)}
    if group == "open":
        row.update({"status": run["status"], "position": run["position"],
                    "total": run["total"],
                    "url": f"/webapp/polls/answer?run_id={run['run_id']}"})
    elif group == "done":
        row.update({"position": run["position"], "total": run["total"]})
    return row


@router.get("/webapp/polls/mine/data")
def webapp_polls_mine_data(who: dict = Depends(core.webapp_actor)) -> dict:
    """Три группы «Моих опросов»: ждут ответа, пройденные, закрытые (docs/164 §12.3)."""
    with core.pool.connection() as conn:
        runs = poll_store.my_runs(conn, who["id"])
    grouped = poll_store.groups(runs)
    return {"ok": True,
            **{group: [_mine_row(run, group) for run in grouped[group]]
               for group in ("open", "done", "closed")}}


@router.get("/webapp/polls/step")
def webapp_polls_step(run_id: int = Query(ge=1), position: int | None = Query(default=None),
                      who: dict = Depends(core.webapp_actor)) -> dict:
    """Текущий вопрос прохождения из приложения: форма шага та же, что у чата.

    `position` в адресе страницы нет — он живёт в `poll_run.position`: перезапуск
    Mini App возвращает человека на тот же вопрос (AC-17).
    """
    with core.pool.connection() as conn:
        run = _run_or_404(conn, run_id, who["id"])
        return {"ok": True, "step": _step(conn, run, position)}
