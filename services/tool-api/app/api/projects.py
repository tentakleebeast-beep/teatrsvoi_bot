"""Проекты: список, карточка, форма, план и событие (E6-2, docs/development/196 §4–5).

Проект связывает событие, деньги и арендатора. Здесь четыре действия:
список и карточка (чтение по `budget.read`), сохранение проекта и план
(`budget.write`), создание события проекта (обёртка над `events_assign`).

Числа карточки не хранятся: план — `budget_plan`, факт — `budget_tx` (§2, S-6).
Второго места, где лежит «бюджет проекта», нет.
"""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi import status as http_status
from pydantic import Field

from .. import core, event_types, project_money
from ..api import budget as budget_api
from ..api import events as events_api
from ..errors import ApiError

router = APIRouter()

KIND_TITLES = {"show": "Спектакль", "event": "Мероприятие", "rental": "Арендатор",
               "course": "Курс", "training": "Тренинг", "other": "Прочее"}
STATUS_TITLES = {"active": "активен", "planned": "запланирован", "archive": "в архиве"}
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
CREATE_KINDS = ("show", "event", "rental")
PLAN_BLOCKS = ("dues", "expense", "show_income", "show_cost", "sublease")
NO_ACCESS_TEXT = "Проекты видят администратор и организатор"


def _reader(who: dict) -> dict:
    return core._webapp_require(who, "budget.read", deny_code="no_access",
                                deny_text=NO_ACCESS_TEXT)


def _writer(who: dict) -> dict:
    return core._webapp_require(who, "budget.write", deny_code="no_access",
                                deny_text=NO_ACCESS_TEXT)


def _money(value) -> float | None:
    return None if value is None else float(value)


def _moment(value) -> dict:
    local = value.astimezone(core.TZ)
    return {"date": local.date().isoformat(), "weekday": WEEKDAYS[local.weekday()],
            "time_from": local.strftime("%H:%M"), "time_to": local.strftime("%H:%M")}


def _project_block(row) -> dict:
    return {
        "id": row["id"], "name": row["name"], "kind": row["kind"],
        "kind_title": KIND_TITLES.get(row["kind"], row["kind"]),
        "status": row["status"],
        "status_title": STATUS_TITLES.get(row["status"], row["status"]),
        "starts_on": row["starts_on"].isoformat() if row["starts_on"] else None,
        "ends_on": row["ends_on"].isoformat() if row["ends_on"] else None,
        "description": row["description"], "counterparty": row["counterparty"],
        "lead": ({"id": row["lead_participant_id"], "name": row["lead_name"]}
                 if row["lead_participant_id"] else None),
    }


@router.get("/webapp/projects/data")
def projects_data(kind: str = Query(default="all"), status: str = Query(default="active"),
                  q: str | None = Query(default=None),
                  who: dict = Depends(core.webapp_actor)) -> dict:
    """Список проектов: активные по ближайшему событию, архив — в конце (§4.1)."""
    _reader(who)
    if kind != "all" and kind not in KIND_TITLES:
        raise ApiError(http_status.HTTP_400_BAD_REQUEST, "bad_kind", code="bad_kind")
    if status not in ("active", "archive", "all"):
        raise ApiError(http_status.HTTP_400_BAD_REQUEST, "bad_status", code="bad_status")
    text = (q or "").strip()[:100]
    if status == "active":
        status_sql = "p.status in ('planned', 'active')"
    elif status == "archive":
        status_sql = "p.status = 'archive'"
    else:
        status_sql = "true"
    kind_sql = "" if kind == "all" else "and p.kind = %s"
    params: list = []
    if kind != "all":
        params.append(kind)
    if text:
        params.append(f"%{text}%")

    sql = f"""
        select p.id, p.name, p.kind, p.status, p.starts_on, p.ends_on,
               (select coalesce(sum(bp.amount_planned), 0)
                  from budget_plan bp where bp.project_id = p.id and bp.status = 'planned')
                 as plan_amount,
               (select coalesce(sum(t.amount), 0)
                  from budget_tx t where t.project_id = p.id and t.status = 'confirmed'
                    and not t.is_plan) as fact_amount,
               ev.starts_at as next_start, ev.kind as next_kind
          from project p
          left join lateral (
              select e.starts_at, e.kind from event_plan e
               where e.project_id = p.id and e.status <> 'cancelled' and e.ends_at >= now()
               order by e.starts_at limit 1) ev on true
         where {status_sql}
           {(kind_sql) if kind_sql else ''}
           {("and p.name ilike %s") if text else ''}
         order by (p.status = 'archive'), ev.starts_at nulls last, p.name
    """
    with core.pool.connection() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        # E5 (190): подписи типов событий — из настройки `calendar_event_type`,
        # второго словаря в коде нет (AC-4, I-1).
        titles = event_types.titles(conn)

    items = []
    for row in rows:
        plan = _money(row["plan_amount"])
        fact = _money(row["fact_amount"])
        # «план не задан» и «запланировали ноль» — разные ответы, поэтому null, не 0
        items.append({
            "id": row["id"], "name": row["name"], "kind": row["kind"],
            "kind_title": KIND_TITLES.get(row["kind"], row["kind"]),
            "status": row["status"],
            "status_title": STATUS_TITLES.get(row["status"], row["status"]),
            "starts_on": row["starts_on"].isoformat() if row["starts_on"] else None,
            "ends_on": row["ends_on"].isoformat() if row["ends_on"] else None,
            "plan": plan if row["plan_amount"] is not None else None,
            "fact": fact if row["fact_amount"] is not None else None,
            "nearest": ({"date": row["next_start"].astimezone(core.TZ).date().isoformat(),
                         "weekday": WEEKDAYS[row["next_start"].astimezone(core.TZ).weekday()],
                         "time_from": row["next_start"].astimezone(core.TZ).strftime("%H:%M"),
                         "time_to": row["next_start"].astimezone(core.TZ).strftime("%H:%M"),
                         "kind": row["next_kind"],
                         "kind_title": titles.get(row["next_kind"], "Событие")}
                        if row["next_start"] else None),
        })
    return {"ok": True, "count": len(items), "items": items}


@router.get("/webapp/project/data")
def project_data(id: int | None = Query(default=None),
                 who: dict = Depends(core.webapp_actor)) -> dict:
    """Карточка проекта: шапка, деньги, события, люди, права (§4.2)."""
    viewer = _reader(who)
    if id is None:
        raise ApiError(http_status.HTTP_400_BAD_REQUEST, "id_required", code="id_required")
    with core.pool.connection() as conn:
        row = conn.execute(
            """select p.*, lead.display_name as lead_name
                 from project p left join participant lead on lead.id = p.lead_participant_id
                where p.id = %s""", (id,)).fetchone()
        if row is None:
            raise ApiError(http_status.HTTP_404_NOT_FOUND, "Проект не найден — возможно, удалён",
                           code="project_not_found")

        plan_row = conn.execute(
            "select sum(amount_planned) as s from budget_plan where project_id = %s "
            "and status = 'planned'", (id,)).fetchone()
        fact_row = conn.execute(
            """select sum(amount) as total,
                      sum(case when tx_type = 'expense' then amount else 0 end) as expense,
                      sum(case when tx_type = 'income' then amount else 0 end) as income
                 from budget_tx where project_id = %s and status = 'confirmed'
                  and not is_plan""", (id,)).fetchone()
        unlinked = None
        if row["kind"] == "rental":
            unlinked = conn.execute(
                """select coalesce(sum(amount), 0) as s from budget_tx
                    where project_id = %s and block = 'sublease' and event_id is null
                      and status = 'confirmed'""", (id,)).fetchone()["s"]

        total_events = conn.execute(
            "select count(*) as n from event_plan where project_id = %s "
            "and status <> 'cancelled'", (id,)).fetchone()["n"]
        events = conn.execute(
            """select id, starts_at, ends_at, kind, location, revenue_amount
                 from event_plan where project_id = %s and status <> 'cancelled'
                order by starts_at limit 5""", (id,)).fetchall()
        roles = conn.execute(
            """select a.alias, a.participant_id, p.display_name as name
                 from person_alias a join participant p on p.id = a.participant_id
                where a.project_id = %s and a.alias_kind = 'role'
                order by a.alias""", (id,)).fetchall()
        participants_count = conn.execute(
            """select count(distinct pid) as n from event_plan e,
                      unnest(e.participant_ids) as pid
                where e.project_id = %s and e.status <> 'cancelled'""",
            (id,)).fetchone()["n"]
        titles = event_types.titles(conn)

    plan = _money(plan_row["s"])
    fact = _money(fact_row["total"])
    rest = None if plan is None or fact is None else plan - fact
    caps = set(viewer.get("caps") or [])
    rights = {
        "can_edit": "budget.write" in caps or "*" in caps,
        "can_plan": "budget.write" in caps or "*" in caps,
        "can_create_event": "schedule.write" in caps or "*" in caps,
        "can_create_rental_event": ("*" in caps
                                    or {"schedule.write", "calendar.write"} <= caps),
    }
    return {
        "ok": True,
        "project": _project_block(row),
        "money": {"plan": plan, "fact": fact, "rest": rest,
                  "expenses": _money(fact_row["expense"]) or 0.0,
                  "income": _money(fact_row["income"]) or 0.0,
                  "has_plan": plan is not None, "has_ops": fact is not None,
                  "unlinked_income": _money(unlinked)},
        "events": {"total": total_events, "items": [
            {"id": e["id"], **_moment(e["starts_at"]), "kind": e["kind"],
             "kind_title": titles.get(e["kind"], "Событие"),
             "location": e["location"], "revenue_amount": _money(e["revenue_amount"])}
            for e in events]},
        "people": {"roles": [{"alias": r["alias"], "participant_id": r["participant_id"],
                              "name": r["name"]} for r in roles],
                   "roles_count": len(roles), "participants_count": participants_count},
        "rights": rights,
    }


class ProjectSaveIn(core.ApiIn):
    id: int | None = None
    name: str
    kind: str | None = None
    status: str = "active"
    description: str | None = Field(default=None, max_length=500)
    counterparty: str | None = Field(default=None, max_length=200)
    lead_participant_id: int | None = None
    starts_on: str | None = None
    ends_on: str | None = None


def _iso_date(value: str | None, code: str):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ApiError(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "bad_dates",
                       code=code) from None


def _project_is_empty(conn, project_id: int) -> bool:
    events = conn.execute("select count(*) as n from event_plan where project_id = %s",
                          (project_id,)).fetchone()["n"]
    ops = conn.execute("select count(*) as n from budget_tx where project_id = %s",
                       (project_id,)).fetchone()["n"]
    return events == 0 and ops == 0


@router.post("/webapp/project/save")
def project_save(body: ProjectSaveIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Создание и правка проекта — одно действие, различие по `id` (§4.3)."""
    viewer = _writer(who)
    name = (body.name or "").strip()
    if not name:
        raise ApiError(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "name_required",
                       code="name_required")
    if len(name) > 120:
        raise ApiError(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "too_long", code="too_long")
    if body.status not in STATUS_TITLES:
        raise ApiError(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "bad_status", code="bad_status")
    starts = _iso_date(body.starts_on, "bad_dates")
    ends = _iso_date(body.ends_on, "bad_dates")
    if starts and ends and ends < starts:
        raise ApiError(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "bad_dates", code="bad_dates")

    with core.pool.connection() as conn:
        if body.id is not None:
            current = conn.execute("select * from project where id = %s",
                                   (body.id,)).fetchone()
            if current is None:
                raise ApiError(http_status.HTTP_404_NOT_FOUND, "project_not_found",
                               code="project_not_found")
        else:
            current = None

        taken = conn.execute(
            "select id from project where lower(name) = lower(%s) and id is distinct from %s",
            (name, body.id)).fetchone()
        if taken is not None:
            raise ApiError(http_status.HTTP_409_CONFLICT, "Проект с таким названием уже есть",
                           code="project_name_taken")

        if body.lead_participant_id is not None:
            lead = conn.execute(
                "select id from participant where id = %s and status = 'active'",
                (body.lead_participant_id,)).fetchone()
            if lead is None:
                raise ApiError(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "lead_not_found",
                               code="lead_not_found")

        if current is None:
            if body.kind not in CREATE_KINDS:
                raise ApiError(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "bad_kind",
                               code="bad_kind")
            row = conn.execute(
                """insert into project (name, kind, status, starts_on, ends_on,
                       description, counterparty, lead_participant_id)
                   values (%s,%s,%s,%s,%s,%s,%s,%s) returning *""",
                (name, body.kind, body.status, starts, ends, body.description,
                 body.counterparty, body.lead_participant_id)).fetchone()
            before = None
        else:
            kind = current["kind"]
            if body.kind is not None and body.kind != current["kind"]:
                if not _project_is_empty(conn, current["id"]) or body.kind not in KIND_TITLES:
                    raise ApiError(
                        http_status.HTTP_409_CONFLICT,
                        "У проекта уже есть события или операции — тип не меняется",
                        code="project_kind_locked")
                kind = body.kind
            row = conn.execute(
                """update project set name = %s, kind = %s, status = %s, starts_on = %s,
                       ends_on = %s, description = %s, counterparty = %s,
                       lead_participant_id = %s where id = %s returning *""",
                (name, kind, body.status, starts, ends, body.description,
                 body.counterparty, body.lead_participant_id, current["id"])).fetchone()
            before = {"name": current["name"], "kind": current["kind"],
                      "status": current["status"]}

        lead_name = None
        if row["lead_participant_id"]:
            lead_name = conn.execute("select display_name from participant where id = %s",
                                     (row["lead_participant_id"],)).fetchone()["display_name"]
        core.audit(conn, "human", viewer.get("id"), "project.save", "project", str(row["id"]),
                   before=before,
                   after={"name": name, "kind": row["kind"], "status": body.status})
    row = dict(row)
    row["lead_name"] = lead_name
    return {"ok": True, "project": _project_block(row)}


class ProjectPlanIn(core.ApiIn):
    project_id: int
    period: str
    block: str = "expense"
    category: str = "Прочее"
    amount: float
    note: str = ""


@router.post("/webapp/project/plan")
def project_plan(body: ProjectPlanIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """План проекта на месяц: строка `budget_plan` с `project_id` (§4.4)."""
    viewer = _writer(who)
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", body.period or ""):
        raise ApiError(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "bad_period", code="bad_period")
    if body.block not in PLAN_BLOCKS:
        raise ApiError(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "bad_block", code="bad_block")
    if body.amount <= 0:
        raise ApiError(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "amount_not_positive",
                       code="amount_not_positive")
    direction = "expense" if body.block == "expense" else "income"

    with core.pool.connection() as conn:
        project = conn.execute("select id from project where id = %s",
                               (body.project_id,)).fetchone()
        if project is None:
            raise ApiError(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown_project",
                           code="unknown_project")
        project_money.ensure_period_open(conn, body.period, viewer, "project.plan")
        category_id = budget_api._ensure_category(conn, body.category, direction)
        conn.execute("insert into budget_period (period, opened_at) values (%s, %s) "
                     "on conflict (period) do nothing",
                     (body.period, f"{body.period}-01"))
        row = conn.execute(
            """insert into budget_plan (period, block, category_id, project_id,
                   amount_planned, note, status)
               values (%s,%s,%s,%s,%s,%s,'planned')
               on conflict (period, block, category_id, project_id)
               do update set amount_planned = excluded.amount_planned,
                             note = excluded.note, status = 'planned'
               returning *""",
            (body.period, body.block, category_id, body.project_id, body.amount,
             body.note)).fetchone()
        core.audit(conn, "human", viewer.get("id"), "project.plan", "budget_plan",
                   str(row["id"]),
                   after={"project_id": body.project_id, "period": body.period,
                          "block": body.block, "amount": float(body.amount)})
    return {"ok": True, "plan": {"id": row["id"], "period": row["period"],
                                 "block": row["block"],
                                 "amount": float(row["amount_planned"]),
                                 "project_id": row["project_id"]}}


class ProjectAmountIn(core.ApiIn):
    event_id: int
    revenue_amount: float


@router.post("/webapp/project/event/amount")
def project_event_amount(body: ProjectAmountIn,
                         who: dict = Depends(core.webapp_actor)) -> dict:
    """Правка суммы события: `revenue_amount` и производная операция (§7.1, AC-19)."""
    viewer = core._webapp_require(who, "schedule.write", deny_code="no_access",
                                  deny_text=NO_ACCESS_TEXT)
    if body.revenue_amount <= 0:
        raise ApiError(http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                       "Укажите сумму аренды — из неё считается доход",
                       code="revenue_amount_required")
    with core.pool.connection() as conn:
        event = conn.execute("select id, project_id from event_plan where id = %s",
                             (body.event_id,)).fetchone()
        if event is None:
            raise ApiError(http_status.HTTP_404_NOT_FOUND, "event_not_found",
                           code="event_not_found")
        conn.execute("update event_plan set revenue_amount = %s where id = %s",
                     (body.revenue_amount, body.event_id))
        operation = project_money.update_amount(conn, body.event_id, body.revenue_amount,
                                                viewer)
        core.audit(conn, "human", viewer.get("id"), "event.amount", "event_plan",
                   str(body.event_id),
                   after={"revenue_amount": float(body.revenue_amount)})
    return {"ok": True, "event_id": body.event_id,
            "operation_id": operation["id"] if operation is not None else None}


EVENT_KINDS_BY_PROJECT = {
    "rental": ("rental", "show"),
    "show": ("rehearsal", "show", "training"),
}


class ProjectEventIn(core.ApiIn):
    project_id: int
    kind: str
    title: str
    date: str
    from_hour: int
    to_hour: int
    venue: str | None = None
    revenue_amount: float | None = None
    participant_ids: list[int] = Field(default_factory=list)
    discipline: str | None = None


@router.post("/webapp/project/event")
def project_event(body: ProjectEventIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Создать событие проекта — обёртка над `events_assign` (§4.5).

    Логика назначения не дублируется: карточка пополняется `project_id` и
    `revenue_amount`, остальное делает существующий путь.
    """
    with core.pool.connection() as conn:
        project = conn.execute("select id, kind, status, name from project where id = %s",
                               (body.project_id,)).fetchone()
    if project is None or project["status"] == "archive":
        raise ApiError(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown_project",
                       code="unknown_project")
    allowed = EVENT_KINDS_BY_PROJECT.get(project["kind"])
    if allowed is not None and body.kind not in allowed:
        raise ApiError(http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                       "Этот проект не принимает такие события",
                       code="event_kind_not_allowed")

    viewer = core._webapp_require(who, "schedule.write", deny_code="no_access",
                                  deny_text=NO_ACCESS_TEXT)
    if project["kind"] == "rental":
        viewer = core._webapp_require(viewer, "calendar.write", deny_code="no_access",
                                      deny_text="Аренду создаёт администратор и организатор")

    card = {"date": body.date, "from_hour": body.from_hour, "to_hour": body.to_hour,
            "kind": body.kind, "title": body.title or project["name"],
            "venue": body.venue, "project_id": body.project_id,
            "participants": [{"id": pid} for pid in body.participant_ids],
            "discipline": body.discipline}
    if body.revenue_amount is not None:
        card["revenue_amount"] = body.revenue_amount
    return events_api.events_assign(payload={"card": card}, who=viewer)
