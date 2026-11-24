"""События: карточка, выбор, назначение, отмена, перенос, календарь."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import Field

from .. import core, event_notify, event_types, google_targets, project_money
from ..assign import parse_assignment
from ..calendar_blocks import (
    PROP_EVENT_ID,
    PROP_KIND,
    PROP_PEOPLE,
    PROP_PROJECT,
    PROP_SOURCE,
    PROP_SOURCE_MARK,
    SOURCE_VALUE,
)
from ..errors import ApiError
from ..external import GOOGLE_API_ERRORS
from ..person_match import normalize as normalize_name
from ..person_match import resolve
from ..rules import WORK_DAY_END, WORK_DAY_START

router = APIRouter()


class ParticipationIn(core.ApiIn):
    """Личное окно участия одного человека (V-134).

    Пустые поля значат «весь интервал события»: время участия необязательно,
    и организатор не должен упираться в форму. Время — «ЧЧ:ММ» того же дня, что
    и событие, с минутами: актёр просит «19:30–21:00», и форма не имеет права
    молча округлить его до 19:00.
    """

    participant_id: int
    time_from: str | None = Field(default=None, max_length=5)
    time_to: str | None = Field(default=None, max_length=5)


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
    description: str | None = Field(default=None, max_length=2000)   # поле Google, не база
    windows: list[ParticipationIn] | None = None   # «с / до» на каждого (V-134), пусто — всё событие
    persist: bool = False                      # сохранить заявку для подтверждения



def _next_date(weekday: int, today: date | None = None) -> date:
    """Ближайший день недели, начиная с сегодняшнего."""
    current = today or datetime.now(core.TZ).date()
    delta = (weekday - current.weekday()) % 7
    return current + timedelta(days=delta)


def _can_move(plan: dict, who: dict) -> bool:
    """Можно ли переносить событие: создатель или администратор (FR-D19).

    Одна функция на два ответа — `POST /tools/events/move` и карточку события
    (190 §3.3): кнопка «Перенести» не может появиться там, где перенос получит 403.
    Ролевая правка (`t_4d081225`) меняет это правило в одном месте.
    """
    caps = who.get("caps") or []
    roles = who.get("roles") or []
    is_admin = "*" in caps or "admin" in roles
    return plan.get("created_by") == who.get("id") or is_admin



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



def _discipline_denied(conn, who: dict, kind: str | None,
                       discipline: str | None) -> str | None:
    """Мастер назначает тренинг только по своей дисциплине (140 §1, §4.3).

    Возвращает текст отказа или `None`. Правило про дисциплину относится только к
    тренингу: репетиция, показ и аренда дисциплины не имеют. Администратора и
    организатора оно не касается — у них нет роли `master` либо есть `*`.
    `who` из Mini App приходит без `caps`/`roles` (`/webapp/preview` зовёт обработчик
    напрямую, а не через `Depends`), поэтому недостающее читается из базы, а не
    считается отсутствующим: иначе мастер терял бы проверку только в приложении.

    Тренинг **без** дисциплины разрешён (решение владельца V-062, 10.11.2026):
    мастер без единой дисциплины назначает тренинг, состав подтягивает всех
    педагогов, а карточка несёт предупреждение `discipline_missing`. Отказ остаётся
    один — названа чужая дисциплина.
    """
    if kind != "training" or not discipline:
        return None
    # 0 — владелец до bootstrap: у него нет ни ролей, ни дисциплин, проверка не про него
    actor_id = int(who.get("id") or 0)
    caps = who.get("caps")
    if caps is None:
        caps = core.resolve_caps(conn, actor_id)
    if "*" in caps:
        return None
    roles = who.get("roles")
    if roles is None:
        roles = core.resolve_roles(conn, actor_id)
    if "master" not in roles:
        return None
    taught = core.teacher_disciplines(conn, actor_id)
    if discipline in taught:
        return None
    names = ", ".join(item["name"] for item in core.disciplines_out(conn, actor_id))
    return f"Вы не ведёте эту дисциплину: доступны {names or 'ни одной'}."


def _minutes_of(value) -> int | None:
    """«19:30» → 1170 минут от полуночи; пусто — None (весь интервал события).

    Минуты, а не часы: актёр в анкете просит «со скольки задействован», и окно
    19:30–21:00 обязано остаться 19:30–21:00, а не превратиться в 19:00.
    """
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value * 60
    text = str(value).strip()
    parts = text.split(":")
    try:
        hours, minutes = int(parts[0]), int(parts[1])
        total = hours * 60 + minutes
    except (IndexError, ValueError):
        hours, minutes, total = 0, 0, -1
    if not 0 <= total <= 24 * 60 or minutes >= 60 or hours >= 24:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY,
                       f"Время участия «{text}» не понял: нужно ЧЧ:ММ, например 19:30.",
                       code="participation_bad_time")
    return total


def _clock(minutes: int | None) -> str | None:
    """Минуты от полуночи → «ЧЧ:ММ» для ответа и карточки."""
    if minutes is None:
        return None
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _local_minutes(moment) -> int:
    """Момент в поясе студии → минуты от полуночи (сравнение окон, V-134).

    Сутки в сравнении не участвуют: личное время — это часы того дня, на
    который стоит событие, и после переноса на другой день оно остаётся теми же
    часами. Иначе окно 19:00 при переносе на завтра «выходило» бы за событие
    только из-за смены даты.
    """
    local = moment.astimezone(core.TZ)
    return local.hour * 60 + local.minute


def _windows_map(items) -> dict[int, tuple[int | None, int | None]]:
    """Личные окна из запроса: человек → (минуты «с», минуты «до») (V-134).

    Пустая пара значит «весь интервал события»: поле необязательное, и
    организатор не должен упираться в форму. Обратная пара отвергается 422
    здесь же: база проверит то же ограничением `event_participant_window_check`,
    но человеку нужен ответ словами, а не ошибка драйвера.
    """
    result: dict[int, tuple[int | None, int | None]] = {}
    for item in items or []:
        if isinstance(item, dict):
            pid = item.get("participant_id", item.get("id"))
            from_min = item.get("time_from", item.get("from_hour"))
            to_min = item.get("time_to", item.get("to_hour"))
        else:
            pid = item.participant_id
            from_min, to_min = item.time_from, item.time_to
        if pid is None:
            raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY,
                           "Не понял, чьё это время участия: нужен человек.",
                           code="participation_person_required")
        start_min, end_min = _minutes_of(from_min), _minutes_of(to_min)
        if start_min is not None and end_min is not None and end_min <= start_min:
            raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY,
                           "Время участия должно идти вперёд: «с» раньше «до».",
                           code="participation_bad_window")
        result[int(pid)] = (start_min, end_min)
    return result


def _windows_outside(windows: dict[int, tuple[int | None, int | None]],
                     event_from: int, event_to: int) -> list[int]:
    """Люди, чьё личное окно не помещается в интервал события.

    Границы события и окна — в минутах от полуночи. Пустое поле берётся из
    интервала события, поэтому «с 19:00 до пустого» при событии 19:00–21:00 —
    норма, а «до 22:00» — уже ошибка.
    """
    outside = []
    for pid, (from_min, to_min) in windows.items():
        start_min = event_from if from_min is None else from_min
        end_min = event_to if to_min is None else to_min
        if start_min < event_from or end_min > event_to or end_min <= start_min:
            outside.append(pid)
    return outside


def _upsert_windows(conn, event_id: int, day: date,
                    windows: dict[int, tuple[int | None, int | None]]) -> None:
    """Записать личные окна перечисленных людей (V-134).

    Пишется строка на каждого, даже с пустым окном: состав события тогда живёт
    в одном месте, а «пусто = весь интервал» решает чтение. Пустая граница
    остаётся `null` — это и значит «край следует за событием»; подставлять сюда
    края прежнего интервала нельзя, иначе после переноса окно «весь интервал»
    превращается в чужое личное время (так и вышло в первом заходе карты).
    """
    base = datetime.combine(day, datetime.min.time(), core.TZ)
    for pid, (from_min, to_min) in windows.items():
        from_at = base + timedelta(minutes=from_min) if from_min is not None else None
        to_at = base + timedelta(minutes=to_min) if to_min is not None else None
        conn.execute("""
            insert into event_participant (event_id, participant_id, from_at, to_at)
            values (%s, %s, %s, %s)
            on conflict (event_id, participant_id)
            do update set from_at = excluded.from_at, to_at = excluded.to_at""",
            (event_id, pid, from_at, to_at))


def _carry_windows(conn, event_id: int, day: date, rows) -> None:
    """Перенести сохранённые личные окна на новый день события (V-134).

    Личное окно — это часы того дня, на который стоит событие (`_local_minutes`),
    поэтому при переносе на другой день меняется только дата: часы и минуты
    остаются прежними, иначе окно указывало бы на день, которого у события уже
    нет. `null` остаётся `null` — это значит «край следует за событием», и
    подставлять сюда края прежнего интервала нельзя.

    Сюда попадают только строки, не присланные явно: явная замена уже записана
    `_upsert_windows` с новым днём.
    """
    for row in rows:
        from_at, to_at = row["from_at"], row["to_at"]
        if from_at is not None:
            from_at = datetime.combine(day, from_at.astimezone(core.TZ).time(), core.TZ)
        if to_at is not None:
            to_at = datetime.combine(day, to_at.astimezone(core.TZ).time(), core.TZ)
        conn.execute("update event_participant set from_at = %s, to_at = %s "
                     "where event_id = %s and participant_id = %s",
                     (from_at, to_at, event_id, row["participant_id"]))


def _write_participation(conn, event_id: int, day: date, people: list[int],
                         windows: dict[int, tuple[int | None, int | None]]) -> None:
    """Записать состав события целиком: у кого окна нет — «весь интервал»."""
    _upsert_windows(conn, event_id, day,
                    {pid: windows.get(pid, (None, None)) for pid in people})


def _participation(conn, event_id: int, starts_at: datetime,
                   ends_at: datetime) -> list[dict]:
    """Состав события с личным временем участия (V-134).

    Строки `event_participant` может не быть — тогда человек задействован весь
    интервал события: карточка показывает интервал, а не пустоту, иначе нельзя
    понять, занят он или нет.
    """
    rows = conn.execute("""
        select pid as participant_id, p.display_name as name, ep.from_at, ep.to_at
        from event_plan pl
        cross join lateral unnest(pl.participant_ids) as pid
        join participant p on p.id = pid
        left join event_participant ep
               on ep.event_id = pl.id and ep.participant_id = pid
        where pl.id = %s
        order by p.display_name""", (event_id,)).fetchall()
    out = []
    for row in rows:
        from_at = (row["from_at"] or starts_at).astimezone(core.TZ)
        to_at = (row["to_at"] or ends_at).astimezone(core.TZ)
        out.append({"participant_id": row["participant_id"], "name": row["name"],
                    "from_at": from_at.isoformat(), "to_at": to_at.isoformat(),
                    "time_from": from_at.strftime("%H:%M"),
                    "time_to": to_at.strftime("%H:%M")})
    return out


def _split_participation(rows: list[dict], caps: list[str] | None,
                         viewer_id: int | None) -> tuple[list[dict], dict | None]:
    """Своё время — всем, чужое — только тому, у кого есть запись (V-134).

    Карточка отдаёт строку текущего человека даже роли без `schedule.write`
    (V-132: актёр читает расписание студии). Чужое личное время — не расписание,
    а данные другого человека, поэтому без права записи их нет.
    """
    mine = next((row for row in rows if row["participant_id"] == viewer_id), None)
    caps = caps or []
    if "*" in caps or "schedule.write" in caps:
        return rows, mine
    return ([mine] if mine else []), mine


def _prepare_assignment(body: AssignIn, who: dict | None = None
                        ) -> tuple[dict | None, list[dict], list[dict]]:
    """
    Собирает карточку события: разбирает текст, находит людей и дату,
    проверяет занятость. Возвращает (карточка, ошибки, предупреждения).
    """
    parsed = parse_assignment(body.text) if body.text else None
    kind = body.kind or (parsed.kind if parsed else None)
    # Словарь типов — таблица `calendar_event_type` (190 §7.4): второй копии в коде нет.
    with core.pool.connection() as conn:
        kind_titles = event_types.titles(conn)
    if not kind or kind not in kind_titles:
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

        # Личные окна участия (V-134): необязательные «с / до» на человека.
        # Пустое поле — весь интервал события, поэтому карточка строится и без них.
        windows = _windows_map(body.windows)
        known = {person["id"] for person in people}
        unknown_windows = [pid for pid in windows if pid not in known]
        outside_windows = _windows_outside(
            {pid: window for pid, window in windows.items() if pid in known},
            from_hour * 60, to_hour * 60)

        # Мастер — только по своей дисциплине (140 §1). Проверка после разбора
        # `discipline` и до записи: иначе карточка строилась бы, а отказ приходил
        # на подтверждении, когда событие уже в календаре.
        denied = _discipline_denied(conn, who, kind, body.discipline) if who else None

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
                     or kind_titles[kind])
    card = {"kind": kind, "kind_title": kind_titles[kind],
            "title": default_title,
            "date": day.isoformat(),
            "weekday": core.WEEKDAY_TITLES[day.weekday()],
            "from_hour": from_hour, "to_hour": to_hour,
            "duration_hours": to_hour - from_hour,
            "venue": venue, "venue_name": venue_name,
            "participants": [{"id": person["id"], "name": person["name"],
                              "time_from": _clock(windows.get(person["id"], (None, None))[0]),
                              "time_to": _clock(windows.get(person["id"], (None, None))[1])}
                             for person in people],
            "project_id": body.project_id,
            "missed_names": missed, "busy_people": busy,
            "ambiguity": ambiguous,
            "staff_required": added_teachers,
            "directors": directors,          # присутствие фиксируется, а не «по желанию»
            "discipline": body.discipline,
            "description": body.description,
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
    if denied:
        errors.append({"code": "discipline_not_taught", "text": denied})
    if unknown_windows:
        errors.append({"code": "participation_unknown_person",
                       "text": "Время участия указано для человека вне состава события."})
    if outside_windows:
        names = [person["name"] for person in people if person["id"] in outside_windows]
        errors.append({"code": "participation_outside_event",
                       "text": "Личное время выходит за интервал события: "
                               + ", ".join(names) + "."})
    return card, errors, warnings



@router.post("/tools/events/preview")
def events_preview(body: AssignIn,
                   who: dict = Depends(core.require("schedule.write"))) -> dict:
    """
    Карточка назначения без записи: что получится, если назначить.
    Ошибки помещения блокируют, занятость людей — только предупреждает (docs/architecture/51).
    """
    card, errors, warnings = _prepare_assignment(body, who)
    if card is None:
        # карточки нет — ключ всё равно присутствует: контракт один для всех исходов (S-8)
        return {"ok": False, "errors": errors, "input_error": True, "card": None,
                "warnings": [], "can_assign": False, "proposal_id": None}

    # Отказ по дисциплине не сохраняет заявку: иначе предложение легло бы в базу и
    # подтвердилось уже без проверки, которая стоит только на входе в карточку.
    denied = any(item["code"] == "discipline_not_taught" for item in errors)

    proposal_id = None
    if body.persist and not denied:
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
        if card.get("kind") not in event_types.titles(conn):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown_kind")
        if not 0 <= from_hour < to_hour <= 24:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_time_range")
        # Мастер назначает тренинг только по своей дисциплине (140 §1): отказ до записи,
        # в том числе для карточки, присланной руками, и для заявки из Mini App.
        if _discipline_denied(conn, who, card.get("kind"), card.get("discipline")):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "discipline_not_taught")
        venue = card.get("venue") or core.default_venue_code()

        # повторная проверка: между предпросмотром и подтверждением могли пройти минуты
        busy_venue, _ = core.venue_busy_for(venue, day, day)
        for item in busy_venue:
            if item.starts_at.date() == day and item.starts_at.hour < to_hour                     and item.ends_at.hour > from_hour:
                raise HTTPException(status.HTTP_409_CONFLICT, "slot_taken")

        start = datetime.combine(day, datetime.min.time(), core.TZ) + timedelta(hours=from_hour)
        end = datetime.combine(day, datetime.min.time(), core.TZ) + timedelta(hours=min(to_hour, 23),
                                                                        minutes=55 if to_hour >= 24 else 0)
        # Доходное событие — только у арендатора (196 §4.5). Без суммы оно нарушило бы
        # I11, поэтому отказ до записи; у проекта другого типа сумма не принимается.
        project_id = card.get("project_id")
        revenue_amount = card.get("revenue_amount")
        project = None
        if project_id:
            project = conn.execute("select id, kind from project where id = %s",
                                   (project_id,)).fetchone()
        if project is not None and project["kind"] == "rental":
            if revenue_amount is None or float(revenue_amount) <= 0:
                raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY,
                               "Укажите сумму аренды — из неё считается доход",
                               code="revenue_amount_required")
            if card.get("kind") not in ("rental", "show"):
                raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY,
                               "Этот проект не принимает такие события",
                               code="event_kind_not_allowed")
        else:
            revenue_amount = None

        private = {PROP_KIND: card["kind"], PROP_SOURCE: SOURCE_VALUE,
                   PROP_SOURCE_MARK: SOURCE_VALUE, PROP_EVENT_ID: "", "svoi_venue": venue}
        if project_id:
            private[PROP_PROJECT] = str(project_id)
        if revenue_amount is not None:
            # Зеркало «лучшее усилие»: правда в базе (196 §3.2, R-11).
            private["svoi_revenue_amount"] = str(revenue_amount)
        people = [person["id"] for person in card.get("participants") or []]
        # Личные окна участия (V-134): из карточки (Mini App, предпросмотр) и из
        # самого запроса (бот присылает их на подтверждении). Пустое поле — весь
        # интервал события, поэтому отсутствие окон ничего не ломает.
        windows = _windows_map(card.get("participants"))
        if payload.get("windows"):
            windows.update(_windows_map(payload["windows"]))
        windows = {pid: window for pid, window in windows.items() if pid in people}
        outside = _windows_outside(windows, from_hour * 60, to_hour * 60)
        if outside:
            raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY,
                           "Личное время участия выходит за интервал события.",
                           code="participation_outside_event")
        if people:
            # порядок id в свойстве не значим, но должен быть стабильным: список
            # приходит из расчёта со множествами, и без сортировки одно и то же
            # событие давало «280,279» или «279,280» от прогона к прогону
            private[PROP_PEOPLE] = ",".join(str(pid) for pid in sorted(people))

        # Сначала строка БД, потом календарь: маркер `svoi_event_id` — это id события
        # в базе (186 §5.1), а без строки его неоткуда взять. Раньше было наоборот,
        # и событие в календаре жило без связи с базой.
        plan = conn.execute("""
            insert into event_plan (title, kind, project_id, starts_at, ends_at,
                                    location, participant_ids, status, gcal_event_id,
                                    gcal_calendar_id, created_by, discipline_id,
                                    revenue_amount)
            values (%s, %s, %s, %s, %s, %s, %s, 'confirmed', null, null, %s,
                    (select id from discipline where code = %s), %s) returning id""",
            (card["title"], card["kind"], project_id, start, end,
             card.get("venue") or card.get("venue_name"), people,
             who["id"], card.get("discipline"), revenue_amount)).fetchone()

        # Состав события и личные окна — в той же транзакции, что и строка
        # события: иначе событие жило бы без состава, а карточка показывала
        # «задействован весь интервал» вместо заданного окна (V-134).
        _write_participation(conn, plan["id"], day, people, windows)

        gcal_id, gcal_calendar_id = _create_draft_event(
            plan["id"], private, start, end, card,
            letter=event_types.letter_of(conn, card["kind"]),
            color_id=event_types.color_of(conn, card["kind"]))
        if gcal_id:
            conn.execute("update event_plan set gcal_event_id = %s, gcal_calendar_id = %s "
                         "where id = %s", (gcal_id, gcal_calendar_id, plan["id"]))

        # Производная операция в той же транзакции: доходное событие без неё нарушило
        # бы I11, и оставлять его нельзя (§4.5). Откат события при сбое операции —
        # свойство транзакции, а не отдельный путь.
        operation = project_money.create_for_event(conn, plan["id"], who)

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
            "operation_id": operation["id"] if operation is not None else None,
            "card": card}



class EventCancelIn(core.ApiIn):
    event_id: int
    reason: str | None = Field(default=None, max_length=500)



def _create_draft_event(event_id: int, private: dict, start: datetime, end: datetime,
                        card: dict, *, letter: str | None = None,
                        color_id: int | None = None) -> tuple[str | None, str | None]:
    """Создать событие **только** в черновом календаре.

    Пишущий вызов возможен лишь с ключом `calendar.draft` из реестра (186 §2.2):
    боевой календарь в реестре записи отсутствует, поэтому «создать сразу в боевом»
    здесь невозможно даже случайно. Best effort: отказ Google не отменяет строку БД.

    Заголовок — `[X] Название` (метка типа, I-4 документа 190), слово типа в текст
    не дублируется: «Спектакль - Жизнь насекомых» осталось в прошлом. Цвет берётся
    из настройки типа, описание — поле Google (базы у него нет, §2).
    """
    private = dict(private)
    private[PROP_EVENT_ID] = str(event_id)
    summary = f"[{letter}] {card['title']}" if letter else str(card.get("title") or "")
    body: dict = {
        "summary": summary,
        "start": {"dateTime": start.isoformat(), "timeZone": "Europe/Samara"},
        "end": {"dateTime": end.isoformat(), "timeZone": "Europe/Samara"},
        "extendedProperties": {"private": private},
    }
    if color_id is not None:
        body["colorId"] = str(color_id)
    description = (card.get("description") or "").strip()
    if description:
        body["description"] = description
    try:
        calendar_id = google_targets.resolve("calendar.draft")
        created = core._calendar_write().events().insert(calendarId=calendar_id,
                                                         body=body).execute()
    except GOOGLE_API_ERRORS as exc:
        core.log.warning("событие не записано в календарь: %s", str(exc)[:150])
        return None, None
    except ApiError as exc:
        # объект записи не настроен: событие живёт в БД, календарь не тронут
        core.log.warning("черновой календарь не настроен: %s", exc.code)
        return None, None
    return created.get("id"), calendar_id


#: Статусы Google Calendar, при которых событие живым не считается.
#: `cancelled` — единственный такой: удалённое через `events.delete` событие Google
#: продолжает отдавать по id, но с этим статусом (живой прогон 23.11.2026).
DEAD_EVENT_STATUSES = frozenset({"cancelled"})


def _event_alive(event: dict | None) -> bool:
    """Живо ли событие из ответа `events.get`.

    Успешный `get` сам по себе ничего не доказывает: после удаления Google отдаёт
    событие по `id` со `status="cancelled"`, и проверка «событие есть» отвечала `True`
    там, где события уже нет (карта Д-2). `tentative` — событие существует, человек
    лишь не подтвердил участие, поэтому править и удалять его можно; отсутствие
    статуса в ответе тоже считается живым (старые подменённые клиенты и частичные
    ответы API).
    """
    if not isinstance(event, dict):
        return False
    status = str(event.get("status") or "").strip().lower()
    return status not in DEAD_EVENT_STATUSES


def _draft_event_exists(gcal_id: str) -> bool:
    """Событие живо в черновом календаре — только такие бот правит и удаляет.

    Боевой календарь здесь не участвует: событие, которого нет в черновом, считается
    чужим (`not_bot_event`), даже если оно создано ботом когда-то давно. Отменённое
    (`status="cancelled"`) событие тоже чужое: `events.delete` не убирает его из
    выдачи `events.get`, и без проверки статуса бот шёл бы удалять его повторно.
    """
    try:
        calendar_id = google_targets.resolve("calendar.draft")
        event = core._calendar_write().events().get(calendarId=calendar_id,
                                                    eventId=gcal_id).execute()
    except GOOGLE_API_ERRORS:
        return False
    except ApiError:
        return False
    return _event_alive(event)


def _delete_calendar_event(gcal_id: str) -> tuple[bool, str | None]:
    """Снимает событие из **чернового** календаря.

    Отмена в БД важнее календаря: если Google недоступен, событие всё равно
    считается отменённым, а причина возвращается человеку. В боевой календарь
    бот не ходит ни при каком статусе (дизайн D10, 186 §5.2).
    """
    if not _draft_event_exists(gcal_id):
        return False, "not_bot_event"
    try:
        calendar_id = google_targets.resolve("calendar.draft")
        core._calendar_write().events().delete(calendarId=calendar_id,
                                               eventId=gcal_id).execute()
    except GOOGLE_API_ERRORS as exc:
        return False, str(exc)[:150]
    except ApiError as exc:
        return False, exc.code
    return True, None



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

        if not _can_move(plan, who):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not_your_event")

        if plan["status"] == "cancelled":
            return {"ok": True, "event_id": plan["id"], "already_cancelled": True,
                    "status": "cancelled", "in_calendar": False,
                    "calendar_updated": False, "notified": 0}

        before = {"title": plan["title"], "kind": plan["kind"],
                  "starts_at": plan["starts_at"], "status": plan["status"],
                  "people": list(plan["participant_ids"] or [])}

        calendar_deleted, calendar_error = False, None
        if plan["gcal_event_id"]:
            calendar_deleted, calendar_error = _delete_calendar_event(plan["gcal_event_id"])

        conn.execute("update event_plan set status = 'cancelled' where id = %s",
                     (plan["id"],))
        # Производная операция остаётся, но перестаёт быть доходом (196 §7.1): I11
        # требует, чтобы у отменённого события не было `confirmed` операции.
        project_money.cancel(conn, plan["id"], who)

        title_ru = event_types.titles(conn).get(plan["kind"], "Событие")
        notified = event_notify.notify_cancelled(conn, plan, plan["starts_at"],
                                                 body.reason, title_ru)

        core.audit(conn, "human", who["id"], "event.cancel", "event_plan", str(plan["id"]),
              before=before,
              after={"status": "cancelled", "reason": body.reason,
                     "calendar_deleted": calendar_deleted,
                     "calendar_error": calendar_error})

    return {"ok": True, "event_id": plan["id"], "already_cancelled": False,
            "status": "cancelled", "in_calendar": calendar_deleted,
            "calendar_updated": calendar_deleted,
            "calendar_error": calendar_error, "notified": notified}



class EventMoveIn(core.ApiIn):
    event_id: int
    date: str | None = None                    # YYYY-MM-DD; без даты — только время
    from_hour: int | None = Field(default=None, ge=0, le=23)
    to_hour: int | None = Field(default=None, ge=1, le=24)
    reason: str | None = Field(default=None, max_length=500)
    allow_outside_hours: bool = False
    windows: list[ParticipationIn] | None = None   # правка личных окон вместе с переносом (V-134)



def _update_calendar_event(gcal_id: str, start: datetime, end: datetime,
                           title: str | None = None) -> tuple[bool, str | None]:
    """Правит время события **только в черновом** календаре.

    Как и отмена, перенос в БД важнее календаря: при недоступности Google
    время в базе всё равно меняется, а причина возвращается человеку. Событие,
    которого нет в черновом, — `not_bot_event`: в боевом его правит человек.
    """
    if not _draft_event_exists(gcal_id):
        return False, "not_bot_event"
    patch: dict = {"start": {"dateTime": start.isoformat(), "timeZone": "Europe/Samara"},
                   "end": {"dateTime": end.isoformat(), "timeZone": "Europe/Samara"}}
    if title:
        patch["summary"] = title
    try:
        calendar_id = google_targets.resolve("calendar.draft")
        core._calendar_write().events().update(calendarId=calendar_id, eventId=gcal_id,
                                               body=patch).execute()
    except GOOGLE_API_ERRORS as exc:
        return False, str(exc)[:150]
    except ApiError as exc:
        return False, exc.code
    return True, None



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

        if not _can_move(plan, who):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not_your_event")
        if plan["status"] == "cancelled":
            raise HTTPException(status.HTTP_409_CONFLICT, "event_cancelled")

        titles = event_types.titles(conn)
        letter = event_types.letter_of(conn, plan["kind"])

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

        # Границы нового интервала в минутах от полуночи — в тех же единицах, в
        # которых считают `_windows_outside` и проверка непокрытых окон ниже.
        # Часы (`from_hour`/`to_hour`) округлены до целого часа и годятся только
        # для занятости людей и уведомления.
        start_min = start.hour * 60 + start.minute
        end_min = end.hour * 60 + end.minute

        # Личные окна участия (V-134): перенос их **не сдвигает**. Новые окна
        # принимаются только явно присланные; иначе считаем, кого новый интервал
        # перестал покрывать, и говорим об этом словами, а не правим молча.
        roster = set(plan["participant_ids"] or [])
        new_windows = _windows_map(body.windows) if body.windows else {}
        if any(pid not in roster for pid in new_windows):
            raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY,
                           "Время участия указано для человека вне состава события.",
                           code="participation_unknown_person")
        # Минуты, а не часы: `_windows_outside` ждёт «минуты от полуночи», иначе
        # событие 18–22 проверялось бы как интервал 18–22 минуты и любое явное
        # окно объявлялось бы внешним (V-134-баг, 422 всегда).
        if _windows_outside(new_windows, start_min, end_min):
            raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY,
                           "Личное время участия выходит за интервал события.",
                           code="participation_outside_event")
        saved = conn.execute("select participant_id, from_at, to_at from event_participant "
                             "where event_id = %s", (plan["id"],)).fetchall()
        # `null` в строке значит «край следует за событием», а не «край старого
        # интервала»: сравниваем только явно заданные границы, иначе человек без
        # личного времени выглядел бы вышедшим за событие на каждом переносе.
        uncovered = [row["participant_id"] for row in saved
                     if row["participant_id"] not in new_windows
                     and ((row["from_at"] is not None
                           and _local_minutes(row["from_at"]) < start_min)
                          or (row["to_at"] is not None
                              and _local_minutes(row["to_at"]) > end_min))]
        names_by_id = {r["id"]: r["display_name"] for r in conn.execute(
            "select id, display_name from participant where id = any(%s)",
            (sorted(set(uncovered) | roster),)).fetchall()}

    if start == old_start and end == old_end and not new_windows:
        return {"ok": True, "event_id": plan["id"], "changed": False,
                "starts_at": start.isoformat(), "ends_at": end.isoformat(),
                "in_calendar": bool(plan["gcal_event_id"]), "calendar_error": None,
                "notified": 0, "busy_people": busy_people}

    busy_venue, _ = core.venue_busy_for(venue, day, day)
    for item in busy_venue:
        if (item.starts_at.date() == day and item.starts_at < end
                and item.ends_at > start):
            raise HTTPException(status.HTTP_409_CONFLICT, "slot_taken")

    # Закрытый месяц проверяется до календаря и до записи: иначе событие уже сдвинуто,
    # а доход остался в прежнем периоде (196 §7.5).
    if plan["revenue_amount"] is not None and float(plan["revenue_amount"]) > 0:
        with core.pool.connection() as conn:
            project_money.ensure_period_open(conn, start.strftime("%Y-%m"), who,
                                             "event.move")

    title_ru = titles.get(plan["kind"], "Событие")
    calendar_updated, calendar_error = False, None
    if plan["gcal_event_id"]:
        # заголовок в Google остаётся в формате «[X] Название» (I-4): метку типа
        # при переносе не теряем, иначе событие перестанет узнаваться по букве
        summary = f"[{letter}] {plan['title']}" if letter else plan["title"]
        calendar_updated, calendar_error = _update_calendar_event(
            plan["gcal_event_id"], start, end, summary)

    with core.pool.connection() as conn:
        conn.execute("update event_plan set starts_at = %s, ends_at = %s where id = %s",
                     (start, end, plan["id"]))
        # Личные окна при переносе не сдвигаются по часам, но их дата следует за
        # событием: правится только то, что организатор прислал явно (V-134),
        # остальные окна переезжают на новый день теми же часами.
        if new_windows:
            _upsert_windows(conn, plan["id"], day, new_windows)
        _carry_windows(conn, plan["id"], day,
                       [row for row in saved if row["participant_id"] not in new_windows])
        # Дата и период производной операции следуют за событием (196 §7.1).
        project_money.move_date(conn, plan["id"], start, who)

        warnings = []
        if uncovered:
            names = [names_by_id.get(pid, str(pid)) for pid in uncovered]
            warnings.append({"code": "personal_windows_outside",
                             "text": "Новый интервал не покрывает личное время: "
                                     + ", ".join(names) + ". Окна не сдвинуты."})

        notified = event_notify.notify_moved(conn, plan, old_start, start, to_hour,
                                             body.reason, title_ru)

        core.audit(conn, "human", who["id"], "event.move", "event_plan", str(plan["id"]),
              before={"starts_at": old_start.isoformat(), "ends_at": old_end.isoformat(),
                      "status": plan["status"]},
              after={"starts_at": start.isoformat(), "ends_at": end.isoformat(),
                     "calendar_updated": calendar_updated,
                     "calendar_error": calendar_error, "reason": body.reason,
                     "busy_people": busy_people})

    return {"ok": True, "event_id": plan["id"], "changed": True,
            "starts_at": start.isoformat(), "ends_at": end.isoformat(),
            "in_calendar": calendar_updated, "calendar_updated": calendar_updated,
            "calendar_error": calendar_error,
            "notified": notified, "busy_people": busy_people,
            "warnings": warnings,
            "venue": venue}



@router.get("/tools/events/{event_id}")
def events_card(event_id: int,
                who: dict = Depends(core.require_any("schedule.write", "schedule.read.all"))) -> dict:
    """Карточка события из расписания: то, что человек отменяет или переносит.

    Нужна чату (С2, С3): кнопки в карточке несут только id, а показать до записи
    надо текущие название, день и часы. Источник — `event_plan`, а не календарь:
    событие бота живёт в базе, даже когда в календарь его записать не удалось.

    Права два, а не одно (V-132): запись (`schedule.write`) — как раньше, и чтение
    расписания студии (`schedule.read.all`) — чтобы актёр видел своё участие.
    Запись карточка не делает; отмена и перенос остаются за `schedule.write`.

    Личных данных человека здесь нет: участник — только id и имя. Личное время
    участия (V-134) — не персональные данные, а расписание: своё окно получает
    каждый, чужие окна — только при `schedule.write`. Телефон, взносы, дорога и
    день рождения живут в карточке человека и в бюджетных ответах, а не в
    расписании; отдавать их пришлось бы только тому, у кого есть
    `participants.contact` (`app/privacy.py`). Структуру ответа сторожит
    `tests/integration/test_troupe_schedule.py`: она сверяет набор ключей, а не их значения.
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
        kind_title = event_types.titles(conn).get(plan["kind"], "Событие")
        participation = _participation(conn, plan["id"], plan["starts_at"], plan["ends_at"])

    shared, mine = _split_participation(participation, who.get("caps"), who.get("id"))
    start = plan["starts_at"].astimezone(core.TZ)
    end = plan["ends_at"].astimezone(core.TZ)
    return {"ok": True, "event": {
        "event_id": plan["id"], "title": plan["title"], "kind": plan["kind"],
        "kind_title": kind_title,
        "status": plan["status"],
        "date": start.date().isoformat(), "weekday": core.WEEKDAY_TITLES[start.weekday()],
        "from_hour": start.hour,
        "to_hour": end.hour + (1 if end.minute else 0),
        "starts_at": start.isoformat(), "ends_at": end.isoformat(),
        "venue": plan["location"], "venue_name": venue["name"] if venue else plan["location"],
        "participants": [{"id": r["id"], "name": r["display_name"]} for r in rows],
        "participation": shared,
        "my_time": mine,
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
    # `events_preview` объявлен с `Depends(require("schedule.write"))`, но здесь он
    # зовётся напрямую: зависимость FastAPI при таком вызове не срабатывает, и без
    # этой строки предпросмотр открывался бы без права (V-132).
    core._webapp_require(who, "schedule.write")
    return events_preview(body, who)



@router.post("/webapp/assign")
def webapp_assign(payload: dict, who: dict = Depends(core.webapp_actor)) -> dict:
    """Назначение события из Mini App."""
    # то же, что у предпросмотра: создание события требует `schedule.write`,
    # актёру доступно только чтение расписания (решение V-132).
    core._webapp_require(who, "schedule.write")
    return events_assign(payload, who)

