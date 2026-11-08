"""События: карточка, выбор, назначение, отмена, перенос, календарь."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import Field

from .. import core
from ..assign import parse_assignment
from ..calendar_blocks import PROP_KIND, PROP_PEOPLE, PROP_PROJECT, PROP_SOURCE
from ..errors import ApiError
from ..external import GOOGLE_API_ERRORS
from ..person_match import normalize as normalize_name
from ..person_match import resolve
from ..rules import WORK_DAY_END, WORK_DAY_START

router = APIRouter()

class AssignIn(core.ApiIn):
    text: str | None = None                    # «назначь репетицию с Витей в четверг в 20:00»
    kind: str | None = None
    title: str | None = None
    date: str | None = None                    # YYYY-MM-DD
    from_hour: int | None = None
    to_hour: int | None = None
    duration_hours: int | None = Field(default=None, ge=1, le=16)
    participants: list[int] | None = None
    chosen: dict[str, int] | None = None       # «витя» → 15, если имён несколько
    discipline: str | None = None              # код дисциплины тренинга
    project_id: int | None = None
    venue: str | None = None
    persist: bool = False                      # сохранить заявку для подтверждения



def _next_date(weekday: int, today: date | None = None) -> date:
    """Ближайший день недели, начиная с сегодняшнего."""
    current = today or datetime.now(core.TZ).date()
    delta = (weekday - current.weekday()) % 7
    return current + timedelta(days=delta)



def _people_by_names(conn, names: list[str],
                     chosen: dict[str, int] | None = None
                     ) -> tuple[list[dict], dict[str, list[dict]], list[str]]:
    """
    Имена → участники. Ищем по имени, ключу и псевдонимам, роли — только резерв:
    «Витя» человек, «Сэм» роль (docs/architecture/41).

    Совпадение — не значит выбор: если под имя подходят двое, это `ambiguous`,
    и организатор выбирает сам (`chosen`), а не первый по алфавиту.
    """
    people = core._people_with_aliases(conn)
    found: dict[int, dict] = {}
    ambiguous: dict[str, list[dict]] = {}
    missed: list[str] = []
    picked = {normalize_name(key): value for key, value in (chosen or {}).items()}

    for name in names:
        result = resolve(people, name)
        status = result["status"]
        if status == "not_found" or status == "empty":
            missed.append(name)
            continue
        if status == "resolved":
            person = result["person"]
            found[person["id"]] = {"id": person["id"], "name": person["display_name"],
                                   "via": person.get("person_key") or ""}
            continue
        options = {person["id"]: {"id": person["id"], "name": person["display_name"],
                                  "via": person.get("person_key") or ""}
                   for person in result["candidates"]}
        choice = picked.get(normalize_name(name))
        if choice and choice in options:
            found[choice] = options[choice]
        else:
            ambiguous[name] = list(options.values())
    return list(found.values()), ambiguous, missed



def _staff_by_rules(conn, kind: str, participant_ids: list[int],
                    discipline: str | None = None
                    ) -> tuple[list[dict], list[dict], str | None]:
    """
    Кто обязателен по правилам студии (27.10.2026, уточнение о дисциплинах).

    тренинг   — входят педагоги **этой** дисциплины: тренинг по музыке ведёт
                Попова, Крылов там не нужен;
    репетиция — режиссёр не добавляется, фиксируется факт присутствия:
                «по возможности пришёл / не пришёл».
    """
    required: list[dict] = []
    directors: list[dict] = []
    note: str | None = None

    if kind == "training":
        if discipline:
            rows = conn.execute("""
                select p.id, p.display_name, d.name as discipline
                from participant_discipline pd
                join participant p on p.id = pd.participant_id
                join discipline d on d.id = pd.discipline_id
                where d.code = %s and pd.relation = 'teacher'
                  and p.status <> 'archived'
                order by p.display_name""", (discipline,)).fetchall()
        else:
            # если дисциплина не названа — берём всех, кто отмечен педагогом:
            # и по дисциплинам, и по старому признаку is_teacher
            rows = conn.execute("""
                select p.id, p.display_name,
                       coalesce(string_agg(d.name, ', '), p.specialty, 'педагог') as discipline
                from participant p
                left join participant_discipline pd
                       on pd.participant_id = p.id and pd.relation = 'teacher'
                left join discipline d on d.id = pd.discipline_id
                where p.status <> 'archived'
                  and (p.is_teacher or pd.relation = 'teacher')
                group by p.id, p.display_name, p.specialty
                order by p.display_name""").fetchall()
            note = "Дисциплина тренинга не указана — добавлены все педагоги."
        required = [{"id": r["id"], "name": r["display_name"], "duty": "педагог",
                     "specialty": r["discipline"]} for r in rows]

    if kind in ("rehearsal", "show"):
        rows = conn.execute("""
            select id, display_name, specialty from participant
            where is_director and status <> 'archived' order by display_name""").fetchall()
        directors = [{"id": r["id"], "name": r["display_name"], "duty": "режиссёр",
                      "specialty": r["specialty"],
                      "will_attend": r["id"] in participant_ids} for r in rows]
    return required, directors, note



def _busy_people(conn, participant_ids: list[int], day: date,
                 from_hour: int, to_hour: int) -> list[int]:
    """Кто занят в эти часы по своим отметкам."""
    if not participant_ids:
        return []
    hours = list(range(from_hour, to_hour))
    rows = conn.execute("""
        select distinct participant_id from availability_hour
        where participant_id = any(%s) and day = %s and hour = any(%s)
          and not is_free""", (participant_ids, day, hours)).fetchall()
    return [row["participant_id"] for row in rows]



def _prepare_assignment(body: AssignIn) -> tuple[dict | None, list[dict], list[dict]]:
    """
    Собирает карточку события: разбирает текст, находит людей и дату,
    проверяет занятость. Возвращает (карточка, ошибки, предупреждения).
    """
    parsed = parse_assignment(body.text) if body.text else None
    kind = body.kind or (parsed.kind if parsed else None)
    if not kind or kind not in core.KIND_TITLES_RU:
        return None, [{"code": "no_kind",
                       "text": "Не понял тип события: репетиция, показ, тренинг или аренда?"}], []

    if body.date:
        try:
            day = datetime.strptime(body.date, "%Y-%m-%d").date()
        except ValueError:
            return None, [{"code": "bad_date", "text": "Дата должна быть в формате ГГГГ-ММ-ДД."}], []
    elif parsed and parsed.day:
        day = date.fromisoformat(parsed.day)
    elif parsed and parsed.relative_days is not None:
        day = datetime.now(core.TZ).date() + timedelta(days=parsed.relative_days)
    elif parsed and parsed.weekday is not None:
        day = _next_date(parsed.weekday)
    else:
        return None, [{"code": "no_date", "text": "Не понял день: назовите дату или день недели."}], []

    from_hour = body.from_hour if body.from_hour is not None else (parsed.hour if parsed else None)
    if from_hour is None:
        return None, [{"code": "no_time", "text": "Не понял время начала."}], []
    # 0 — не «по умолчанию», а ошибка ввода: её ловит Field(ge=1) (smoke S-10)
    duration = (body.duration_hours if body.duration_hours is not None
                else (parsed.duration_hours if parsed else 2))
    to_hour = body.to_hour if body.to_hour is not None else from_hour + duration
    if not WORK_DAY_START <= from_hour < to_hour <= WORK_DAY_END:
        return None, [{"code": "bad_time",
                       "text": f"Рабочий день {WORK_DAY_START:02d}:00–{WORK_DAY_END - 1:02d}:00, "
                               "время вне него не назначаем."}], []

    with core.pool.connection() as conn:
        people: list[dict] = []
        ambiguous: dict[str, list[dict]] = {}
        missed: list[str] = []
        if body.participants:
            rows = conn.execute("select id, display_name from participant where id = any(%s)",
                                (body.participants,)).fetchall()
            people = [{"id": r["id"], "name": r["display_name"], "via": "выбран"} for r in rows]
        elif parsed and parsed.names:
            people, ambiguous, missed = _people_by_names(conn, parsed.names, body.chosen)

        venue = (body.venue or (parsed.venue if parsed else None) or core.default_venue_code()).lower()
        venue_row = conn.execute("select code, name from venue where code = %s",
                                 (venue,)).fetchone()
        venue_name = venue_row["name"] if venue_row else venue
        # правила состава: педагоги на тренинге обязательны, режиссёр — по желанию
        teachers, directors, staff_note = _staff_by_rules(
            conn, kind, [p["id"] for p in people], body.discipline)
        already = {person["id"] for person in people}
        added_teachers = [t for t in teachers if t["id"] not in already]
        people = people + [{"id": t["id"], "name": t["name"], "via": "педагог"}
                           for t in added_teachers]

        busy = _busy_people(conn, [person["id"] for person in people], day, from_hour, to_hour)

    warnings = []
    if staff_note:
        warnings.append({"code": "discipline_missing", "text": staff_note})
    if ambiguous:
        variants = "; ".join(f"{name}: " + ", ".join(p["name"] for p in options)
                             for name, options in ambiguous.items())
        warnings.append({"code": "ambiguous_names",
                         "text": "Уточните, кого имели в виду — " + variants})
        warnings.append({"code": "unknown_names",
                         "text": "Не нашёл: " + ", ".join(missed) + ". Назначьте без них или уточните."})
    if busy:
        names = [person["name"] for person in people if person["id"] in busy]
        warnings.append({"code": "participant_busy",
                         "text": "По отметкам заняты: " + ", ".join(names) + "."})

    busy_venue, venue_info = core.venue_busy_for(venue, day, day)
    occupied = any(
        not (item.ends_at.hour <= from_hour and item.ends_at.date() == day)
        and not (item.starts_at.hour >= to_hour and item.starts_at.date() == day)
        and item.starts_at.date() == day
        for item in busy_venue)

    default_title = (body.title
                     or (" / ".join(person["name"] for person in people) or None)
                     or core.KIND_TITLES_RU[kind])
    card = {"kind": kind, "kind_title": core.KIND_TITLES_RU[kind],
            "title": default_title,
            "date": day.isoformat(),
            "weekday": core.WEEKDAY_TITLES[day.weekday()],
            "from_hour": from_hour, "to_hour": to_hour,
            "duration_hours": to_hour - from_hour,
            "venue": venue, "venue_name": venue_name,
            "participants": [{"id": person["id"], "name": person["name"]} for person in people],
            "project_id": body.project_id,
            "missed_names": missed, "busy_people": busy,
            "ambiguity": ambiguous,
            "staff_required": added_teachers,
            "directors": directors,          # присутствие фиксируется, а не «по желанию»
            "discipline": body.discipline,
            "staff_note": staff_note,
            "venue_source": venue_info.get("source", "unknown")}

    errors = []
    if ambiguous:
        errors.append({"code": "ambiguous_names",
                       "text": "Не понял, кого из однофамильцев или тёзок вы имели в виду."})
    if occupied:
        errors.append({"code": "venue_busy",
                       "text": f"Помещение «{venue_name}» занято в это время. "
                               f"Выберите другое время."})
    if not people and not ambiguous:
        errors.append({"code": "no_people", "text": "Не понял, кто участвует."})
    return card, errors, warnings



@router.post("/tools/events/preview")
def events_preview(body: AssignIn,
                   who: dict = Depends(core.require("schedule.write"))) -> dict:
    """
    Карточка назначения без записи: что получится, если назначить.
    Ошибки помещения блокируют, занятость людей — только предупреждает (docs/architecture/51).
    """
    card, errors, warnings = _prepare_assignment(body)
    if card is None:
        # карточки нет — ключ всё равно присутствует: контракт один для всех исходов (S-8)
        return {"ok": False, "errors": errors, "input_error": True, "card": None,
                "warnings": [], "can_assign": False, "proposal_id": None}

    proposal_id = None
    if body.persist:
        with core.pool.connection() as conn:
            proposal = conn.execute("""
                insert into slot_proposal (request, candidates, created_by)
                values (%s, %s, %s) returning id""",
                (json.dumps(body.model_dump(), ensure_ascii=False),
                 json.dumps([card], ensure_ascii=False), who["id"])).fetchone()
        proposal_id = proposal["id"]

    input_error = any(item["code"] in ("no_kind", "bad_date", "no_date", "no_time", "bad_time")
                      for item in errors)
    return {"ok": not errors, "proposal_id": proposal_id, "card": card,
            "warnings": warnings, "errors": errors, "input_error": input_error,
            "can_assign": not errors}



@router.post("/tools/events/choose")
def events_choose(payload: dict,
                  who: dict = Depends(core.require("schedule.write"))) -> dict:
    """
    Разрешение неоднозначности: организатор выбрал одного из тёзок.

    Заявка помнит исходный текст и уже сделанный выбор, поэтому повторный
    предпросмотр не требует заново объяснять боту, о ком речь.
    """
    proposal_id = payload.get("proposal_id")
    participant_id = payload.get("participant_id")
    if not proposal_id or not participant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "need_proposal_and_person")

    with core.pool.connection() as conn:
        row = conn.execute("select request from slot_proposal where id = %s",
                           (proposal_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "proposal_not_found")

    request = row["request"] or {}
    chosen = dict(request.get("chosen") or {})
    text = request.get("text") or ""
    parsed = parse_assignment(text) if text else None

    query = None
    if parsed:
        for name in parsed.names:
            with core.pool.connection() as conn:
                _, ambiguous, _ = _people_by_names(conn, [name], chosen)
            for key, options in ambiguous.items():
                if any(option["id"] == int(participant_id) for option in options):
                    query = key
                    break
            if query:
                break

    if query is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "person_not_among_candidates")

    chosen[query] = int(participant_id)
    request.update({"chosen": chosen})
    return events_preview(AssignIn(**request), who)



@router.post("/tools/events/assign")
def events_assign(payload: dict,
                  who: dict = Depends(core.require("schedule.write"))) -> dict:
    """
    Создаёт событие: календарь → расписание → уведомления участникам.
    """
    proposal_id = payload.get("proposal_id")
    card = payload.get("card")

    with core.pool.connection() as conn:
        if proposal_id:
            row = conn.execute("select candidates, event_plan_id from slot_proposal "
                               "where id = %s", (proposal_id,)).fetchone()
            if row is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "proposal_not_found")
            if row["event_plan_id"]:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "already_assigned")
            card = (row["candidates"] or [None])[0]
        if not card:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "no_card")

        day = datetime.strptime(card["date"], "%Y-%m-%d").date()
        from_hour, to_hour = int(card["from_hour"]), int(card["to_hour"])
        # проверки бывшего POST /tools/calendar/event живут здесь: путь записи один,
        # и карточку можно прислать руками — тип и границы проверяются до записи
        if card.get("kind") not in core.KIND_TITLES_RU:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown_kind")
        if not 0 <= from_hour < to_hour <= 24:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_time_range")
        venue = card.get("venue") or core.default_venue_code()

        # повторная проверка: между предпросмотром и подтверждением могли пройти минуты
        busy_venue, _ = core.venue_busy_for(venue, day, day)
        for item in busy_venue:
            if item.starts_at.date() == day and item.starts_at.hour < to_hour                     and item.ends_at.hour > from_hour:
                raise HTTPException(status.HTTP_409_CONFLICT, "slot_taken")

        start = datetime.combine(day, datetime.min.time(), core.TZ) + timedelta(hours=from_hour)
        end = datetime.combine(day, datetime.min.time(), core.TZ) + timedelta(hours=min(to_hour, 23),
                                                                        minutes=55 if to_hour >= 24 else 0)
        private = {PROP_KIND: card["kind"], PROP_SOURCE: "tool-api",
                   "svoi_venue": venue}
        if card.get("project_id"):
            private[PROP_PROJECT] = str(card["project_id"])
        people = [person["id"] for person in card.get("participants") or []]
        if people:
            # порядок id в свойстве не значим, но должен быть стабильным: список
            # приходит из расчёта со множествами, и без сортировки одно и то же
            # событие давало «280,279» или «279,280» от прогона к прогону
            private[PROP_PEOPLE] = ",".join(str(pid) for pid in sorted(people))

        gcal_id = None
        if core.CALENDAR_ID_DRAFT:
            try:
                created = core._calendar().events().insert(calendarId=core.CALENDAR_ID_DRAFT, body={
                    "summary": f"{core.KIND_TITLES_RU.get(card['kind'], 'Событие')} - {card['title']}",
                    "start": {"dateTime": start.isoformat(), "timeZone": "Europe/Samara"},
                    "end": {"dateTime": end.isoformat(), "timeZone": "Europe/Samara"},
                    "colorId": str(core.EVENT_COLORS.get(card["kind"], 8)),
                    "extendedProperties": {"private": private},
                }).execute()
                gcal_id = created.get("id")
            except GOOGLE_API_ERRORS as exc:
                # адаптер календаря: событие уже в БД, запись в календарь — best effort
                core.log.warning("событие не записано в календарь: %s", str(exc)[:150])

        plan = conn.execute("""
            insert into event_plan (title, kind, project_id, starts_at, ends_at,
                                    location, participant_ids, status, gcal_event_id,
                                    created_by, discipline_id)
            values (%s, %s, %s, %s, %s, %s, %s, 'confirmed', %s, %s,
                    (select id from discipline where code = %s)) returning id""",
            (card["title"], card["kind"], card.get("project_id"), start, end,
             card.get("venue") or card.get("venue_name"), people, gcal_id,
             who["id"], card.get("discipline"))).fetchone()

        if proposal_id:
            conn.execute("update slot_proposal set event_plan_id = %s, chosen_index = 0 "
                         "where id = %s", (plan["id"], proposal_id))

        notified = 0
        for pid in people:
            inserted = conn.execute("""
                insert into notification (recipient_id, kind, payload, dedupe_key, scheduled_at)
                values (%s, 'event_assigned', %s, %s, now())
                on conflict (dedupe_key, recipient_id) do nothing
                returning id""",
                (pid, json.dumps({"event_id": plan["id"], "title": card["title"],
                                  "kind": card["kind"], "date": card["date"],
                                  "from_hour": from_hour, "to_hour": to_hour,
                                  "venue": card.get("venue_name")}, ensure_ascii=False),
                 f"event:{plan['id']}:assigned")).fetchone()
            notified += 1 if inserted else 0

        core.audit(conn, "human", who["id"], "event.assign", "event_plan", str(plan["id"]),
              after={"title": card["title"], "kind": card["kind"], "date": card["date"],
                     "people": people, "gcal": bool(gcal_id)})

    return {"ok": True, "event_id": plan["id"], "gcal_event_id": gcal_id,
            "in_calendar": bool(gcal_id), "notified": notified,
            "card": card}



class EventCancelIn(core.ApiIn):
    event_id: int
    reason: str | None = Field(default=None, max_length=500)



def _delete_calendar_event(gcal_id: str) -> tuple[bool, str | None]:
    """Снимает событие из календаря: сначала черновой, потом боевой.

    Отмена в БД важнее календаря: если Google недоступен, событие всё равно
    считается отменённым, а причина возвращается человеку.
    """
    errors = []
    for calendar_id in (core.CALENDAR_ID_DRAFT, core.CALENDAR_ID_READ):
        if not calendar_id:
            continue
        try:
            core._calendar().events().delete(calendarId=calendar_id,
                                        eventId=gcal_id).execute()
            return True, None
        except GOOGLE_API_ERRORS as exc:
            # адаптер календаря: пробуем черновой, потом боевой
            errors.append(str(exc)[:150])
    return False, "; ".join(errors) or "calendar_id_missing"



@router.post("/tools/events/cancel")
def events_cancel(body: EventCancelIn,
                  who: dict = Depends(core.require("schedule.write"))) -> dict:
    """
    Отмена события (FR-D18): организатор — своё, администратор — любое.

    Участники и организатор получают уведомление в очередь, в аудит пишется
    'event.cancel' с прежним состоянием. Повторная отмена идемпотентна.
    """
    with core.pool.connection() as conn:
        plan = conn.execute("select * from event_plan where id = %s",
                            (body.event_id,)).fetchone()
        if plan is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "event_not_found")

        caps = who.get("caps") or []
        roles = who.get("roles") or []
        is_admin = "*" in caps or "admin" in roles
        if plan["created_by"] != who["id"] and not is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not_your_event")

        if plan["status"] == "cancelled":
            return {"ok": True, "event_id": plan["id"], "already_cancelled": True,
                    "status": "cancelled", "in_calendar": False, "notified": 0}

        before = {"title": plan["title"], "kind": plan["kind"],
                  "starts_at": plan["starts_at"], "status": plan["status"],
                  "people": list(plan["participant_ids"] or [])}

        calendar_deleted, calendar_error = False, None
        if plan["gcal_event_id"]:
            calendar_deleted, calendar_error = _delete_calendar_event(plan["gcal_event_id"])

        conn.execute("update event_plan set status = 'cancelled' where id = %s",
                     (plan["id"],))

        recipients = set(plan["participant_ids"] or [])
        if plan["created_by"]:
            recipients.add(plan["created_by"])
        title_ru = core.KIND_TITLES_RU.get(plan["kind"], "Событие")
        moment = plan["starts_at"].astimezone(core.TZ)
        body_text = (f"{title_ru} «{plan['title']}» {moment.strftime('%d.%m.%Y %H:%M')} "
                     "отменяется.")
        if body.reason:
            body_text += f" Причина: {body.reason}"
        notified = 0
        for pid in sorted(recipients):
            inserted = conn.execute("""
                insert into notification (recipient_id, kind, payload, dedupe_key, scheduled_at)
                values (%s, 'event_cancelled', %s, %s, now())
                on conflict (dedupe_key, recipient_id) do nothing
                returning id""",
                (pid, json.dumps({"event_id": plan["id"], "title": "Событие отменено",
                                  "body": body_text, "kind": plan["kind"],
                                  "date": moment.date().isoformat(),
                                  "from_hour": moment.hour, "reason": body.reason},
                                 ensure_ascii=False),
                 f"event:{plan['id']}:cancelled")).fetchone()
            notified += 1 if inserted else 0

        core.audit(conn, "human", who["id"], "event.cancel", "event_plan", str(plan["id"]),
              before=before,
              after={"status": "cancelled", "reason": body.reason,
                     "calendar_deleted": calendar_deleted,
                     "calendar_error": calendar_error})

    return {"ok": True, "event_id": plan["id"], "already_cancelled": False,
            "status": "cancelled", "in_calendar": calendar_deleted,
            "calendar_error": calendar_error, "notified": notified}



class EventMoveIn(core.ApiIn):
    event_id: int
    date: str | None = None                    # YYYY-MM-DD; без даты — только время
    from_hour: int | None = Field(default=None, ge=0, le=23)
    to_hour: int | None = Field(default=None, ge=1, le=24)
    reason: str | None = Field(default=None, max_length=500)
    allow_outside_hours: bool = False



def _update_calendar_event(gcal_id: str, start: datetime, end: datetime,
                           title: str | None = None) -> tuple[bool, str | None]:
    """Правит время события в календаре: сначала черновой, затем боевой.

    Как и отмена, перенос в БД важнее календаря: при недоступности Google
    время в базе всё равно меняется, а причина возвращается человеку.
    """
    errors = []
    patch: dict = {"start": {"dateTime": start.isoformat(), "timeZone": "Europe/Samara"},
                   "end": {"dateTime": end.isoformat(), "timeZone": "Europe/Samara"}}
    if title:
        patch["summary"] = title
    for calendar_id in (core.CALENDAR_ID_DRAFT, core.CALENDAR_ID_READ):
        if not calendar_id:
            continue
        try:
            core._calendar().events().update(calendarId=calendar_id, eventId=gcal_id,
                                        body=patch).execute()
            return True, None
        except GOOGLE_API_ERRORS as exc:
            # адаптер календаря: пробуем черновой, потом боевой
            errors.append(str(exc)[:150])
    return False, "; ".join(errors) or "calendar_id_missing"



@router.post("/tools/events/move")
def events_move(body: EventMoveIn,
                who: dict = Depends(core.require("schedule.write"))) -> dict:
    """Перенос события (FR-D19): организатор — своё, администратор — любое.

    Меняются дата и/или часы, длительность сохраняется. Помещение проверяется
    заново (занято — 409), занятость людей возвращается предупреждением.
    Календарь правится, участники и организатор получают уведомление со связью
    «было → стало», в аудит пишется 'event.move'.
    """
    with core.pool.connection() as conn:
        plan = conn.execute("select * from event_plan where id = %s",
                            (body.event_id,)).fetchone()
        if plan is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "event_not_found")

        caps = who.get("caps") or []
        roles = who.get("roles") or []
        is_admin = "*" in caps or "admin" in roles
        if plan["created_by"] != who["id"] and not is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not_your_event")
        if plan["status"] == "cancelled":
            raise HTTPException(status.HTTP_409_CONFLICT, "event_cancelled")

        old_start = plan["starts_at"].astimezone(core.TZ)
        old_end = plan["ends_at"].astimezone(core.TZ)
        duration = old_end - old_start

        if body.date:
            try:
                day = datetime.strptime(body.date, "%Y-%m-%d").date()
            except ValueError:
                raise ApiError(status.HTTP_400_BAD_REQUEST,
                           "Дата должна быть в формате ГГГГ-ММ-ДД", code="bad_date") from None
        else:
            day = old_start.date()

        if body.from_hour is None:
            clock = old_start.time()
        else:
            clock = time(body.from_hour, 0)
        start = datetime.combine(day, clock, core.TZ)
        if body.to_hour is None:
            end = start + duration
        elif body.to_hour >= 24:
            end = datetime.combine(day, time(23, 55), core.TZ)
        else:
            end = datetime.combine(day, time(body.to_hour, 0), core.TZ)

        if end <= start:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_interval")
        if not body.allow_outside_hours:
            if start.time() < time(8, 0) or end.time() > time(23, 0):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "outside_working_hours")

        venue = plan["location"] or core.default_venue_code()
        from_hour, to_hour = start.hour, end.hour + (1 if end.minute else 0)
        busy_people = _busy_people(conn, list(plan["participant_ids"] or []),
                                   day, from_hour, to_hour)

    if start == old_start and end == old_end:
        return {"ok": True, "event_id": plan["id"], "changed": False,
                "starts_at": start.isoformat(), "ends_at": end.isoformat(),
                "in_calendar": bool(plan["gcal_event_id"]), "calendar_error": None,
                "notified": 0, "busy_people": busy_people}

    busy_venue, _ = core.venue_busy_for(venue, day, day)
    for item in busy_venue:
        if (item.starts_at.date() == day and item.starts_at < end
                and item.ends_at > start):
            raise HTTPException(status.HTTP_409_CONFLICT, "slot_taken")

    title_ru = core.KIND_TITLES_RU.get(plan["kind"], "Событие")
    calendar_updated, calendar_error = False, None
    if plan["gcal_event_id"]:
        calendar_updated, calendar_error = _update_calendar_event(
            plan["gcal_event_id"], start, end,
            f"{title_ru} - {plan['title']}")

    with core.pool.connection() as conn:
        conn.execute("update event_plan set starts_at = %s, ends_at = %s where id = %s",
                     (start, end, plan["id"]))

        recipients = set(plan["participant_ids"] or [])
        if plan["created_by"]:
            recipients.add(plan["created_by"])
        was = old_start.strftime("%d.%m.%Y %H:%M")
        now_text = start.strftime("%d.%m.%Y %H:%M")
        body_text = (f"{title_ru} «{plan['title']}» переносится: было {was}, "
                     f"стало {now_text}.")
        if body.reason:
            body_text += f" Причина: {body.reason}"
        notified = 0
        for pid in sorted(recipients):
            inserted = conn.execute("""
                insert into notification (recipient_id, kind, payload, dedupe_key, scheduled_at)
                values (%s, 'event_moved', %s, %s, now())
                on conflict (dedupe_key, recipient_id) do nothing
                returning id""",
                (pid, json.dumps({"event_id": plan["id"], "title": "Событие перенесено",
                                  "body": body_text, "kind": plan["kind"],
                                  "date": start.date().isoformat(), "from_hour": start.hour,
                                  "to_hour": to_hour, "was": old_start.isoformat(),
                                  "now": start.isoformat(), "reason": body.reason},
                                 ensure_ascii=False),
                 f"event:{plan['id']}:moved:{start.isoformat()}")).fetchone()
            notified += 1 if inserted else 0

        core.audit(conn, "human", who["id"], "event.move", "event_plan", str(plan["id"]),
              before={"starts_at": old_start.isoformat(), "ends_at": old_end.isoformat(),
                      "status": plan["status"]},
              after={"starts_at": start.isoformat(), "ends_at": end.isoformat(),
                     "calendar_updated": calendar_updated,
                     "calendar_error": calendar_error, "reason": body.reason,
                     "busy_people": busy_people})

    return {"ok": True, "event_id": plan["id"], "changed": True,
            "starts_at": start.isoformat(), "ends_at": end.isoformat(),
            "in_calendar": calendar_updated, "calendar_error": calendar_error,
            "notified": notified, "busy_people": busy_people,
            "venue": venue}



@router.get("/tools/events/{event_id}")
def events_card(event_id: int,
                who: dict = Depends(core.require("schedule.write"))) -> dict:
    """Карточка события из расписания: то, что человек отменяет или переносит.

    Нужна чату (С2, С3): кнопки в карточке несут только id, а показать до записи
    надо текущие название, день и часы. Источник — `event_plan`, а не календарь:
    событие бота живёт в базе, даже когда в календарь его записать не удалось.
    """
    with core.pool.connection() as conn:
        plan = conn.execute("""
            select id, title, kind, status, starts_at, ends_at, location,
                   participant_ids, gcal_event_id
            from event_plan where id = %s""", (event_id,)).fetchone()
        if plan is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "event_not_found")
        venue = conn.execute("select name from venue where code = %s",
                             (plan["location"],)).fetchone()
        ids = list(plan["participant_ids"] or [])
        rows = conn.execute("select id, display_name from participant where id = any(%s) "
                            "order by display_name", (ids,)).fetchall() if ids else []

    start = plan["starts_at"].astimezone(core.TZ)
    end = plan["ends_at"].astimezone(core.TZ)
    return {"ok": True, "event": {
        "event_id": plan["id"], "title": plan["title"], "kind": plan["kind"],
        "kind_title": core.KIND_TITLES_RU.get(plan["kind"], "Событие"),
        "status": plan["status"],
        "date": start.date().isoformat(), "weekday": core.WEEKDAY_TITLES[start.weekday()],
        "from_hour": start.hour,
        "to_hour": end.hour + (1 if end.minute else 0),
        "starts_at": start.isoformat(), "ends_at": end.isoformat(),
        "venue": plan["location"], "venue_name": venue["name"] if venue else plan["location"],
        "participants": [{"id": r["id"], "name": r["display_name"]} for r in rows],
        "in_calendar": bool(plan["gcal_event_id"])}}


def venues_list() -> list[dict]:
    """Активные пространства: сцена, гримёрка, подсобки."""
    with core.pool.connection() as conn:
        rows = conn.execute("select code, name, kind, calendar_id from venue "
                            "where is_active order by sort_order, name").fetchall()
    return [dict(row) for row in rows]



@router.get("/tools/disciplines")
def disciplines(who: dict = Depends(core.current)) -> dict:
    """Дисциплины для формы поиска окон и назначения."""
    with core.pool.connection() as conn:
        rows = conn.execute("select code, name from discipline order by sort_order, name").fetchall()
    return {"ok": True, "disciplines": [dict(row) for row in rows]}



@router.get("/tools/venues")
def venues(who: dict = Depends(core.current)) -> dict:
    """Пространства: сцена, гримёрка, подсобки."""
    return {"ok": True, "venues": venues_list(), "default": core.default_venue_code()}



@router.get("/tools/calendar/health")
def calendar_health(who: dict = Depends(core.current)) -> dict:
    """Проверка доступа сервисной учётки к календарям из .env."""
    if not core.CALENDAR_ID_READ and not core.CALENDAR_ID_DRAFT:
        return {"ok": False, "error": "calendar_id_missing",
                "message": "CALENDAR_ID_READ и CALENDAR_ID_DRAFT не заданы в .env"}
    result = {}
    try:
        svc = core._calendar()
        for name, cid in (("read", core.CALENDAR_ID_READ), ("draft", core.CALENDAR_ID_DRAFT)):
            if not cid:
                result[name] = {"ok": False, "error": "not_configured"}
                continue
            try:
                cal = svc.calendars().get(calendarId=cid).execute()
                result[name] = {"ok": True, "summary": cal.get("summary"),
                                "timezone": cal.get("timeZone")}
            except GOOGLE_API_ERRORS as e:
                result[name] = {"ok": False, "error": str(e)[:200]}
    except GOOGLE_API_ERRORS as e:
        core.log.warning("календарь недоступен: %s", e)
        return {"ok": False, "error": "calendar_unavailable",
                "message": "Календарь недоступен: повторите позже"}
    return {"ok": all(v.get("ok") for v in result.values()), "calendars": result}



@router.get("/tools/calendar/events")
def calendar_events(days: int = Query(default=7, ge=1, le=90), draft: bool = False,
                    who: dict = Depends(core.require("schedule.read.all"))) -> dict:
    """События из календаря. draft=true — черновой календарь для обкатки."""
    cid = core.CALENDAR_ID_DRAFT if draft else core.CALENDAR_ID_READ
    if not cid:
        return {"ok": False, "error": "calendar_id_missing",
                "message": "CALENDAR_ID_READ/CALENDAR_ID_DRAFT не заданы в .env"}
    now = datetime.now(core.TZ)
    try:
        svc = core._calendar()
        data = svc.events().list(
            calendarId=cid, timeMin=now.isoformat(),
            timeMax=(now + timedelta(days=days)).isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=100).execute()
    except GOOGLE_API_ERRORS as e:
        core.log.warning("календарь недоступен: %s", e)
        return {"ok": False, "error": "calendar_unavailable",
                "message": "Календарь недоступен: повторите позже"}

    items = []
    for ev in data.get("items", []):
        start = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date")
        end = ev.get("end", {}).get("dateTime") or ev.get("end", {}).get("date")
        private = (ev.get("extendedProperties") or {}).get("private") or {}
        items.append({"id": ev.get("id"), "title": ev.get("summary"),
                      "start": start, "end": end,
                      "location": ev.get("location"), "status": ev.get("status"),
                      "kind": private.get("svoi_kind"),
                      "link": ev.get("htmlLink")})
    return {"ok": True, "calendar": "draft" if draft else "read", "count": len(items),
            "data": items}



@router.post("/webapp/preview")
def webapp_preview(body: AssignIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Предпросмотр назначения из Mini App."""
    return events_preview(body, who)



@router.post("/webapp/assign")
def webapp_assign(payload: dict, who: dict = Depends(core.webapp_actor)) -> dict:
    """Назначение события из Mini App."""
    return events_assign(payload, who)

