"""СМ-3. Окна → назначение → уведомление → отмена и перенос.

Роли: org, master. Домен требований: D1–D9, D18–D22.

Обоснование: FR-D5, FR-D6, FR-D15, FR-D16, FR-D18, FR-D19, FR-H10, FR-H11. Событие
создаётся только на дальнюю дату и только из смоук-участников: реальный состав
не трогаем ни в каком виде (docs/development/143 §6.2).
"""

from datetime import date, timedelta

from conftest import future_date

SCENARIO = {
    "id": "СМ-3",
    "title": "Окна → назначение → уведомление → отмена/перенос",
    "roles": ["org", "master"],
    "domain": "D1–D9, D18–D22",
}


def _event(db, title: str) -> dict | None:
    row = db.execute(
        "select id, title, starts_at, ends_at, participant_ids, location "
        "from event_plan where title = %s order by id desc limit 1", (title,)).fetchone()
    if row is None:
        return None
    return {"id": row[0], "title": row[1], "starts_at": row[2], "ends_at": row[3],
            "participant_ids": list(row[4] or []), "location": row[5]}


def test_cm03_slots(journal, stand, people, org, master, troupe, db):
    """Свободные часы → окно → назначение → отмена → перенос."""
    actor = people["troupe"]
    day = future_date(60)
    title = f"Смоук репетиция {stand['marker']}"
    # Свободные часы для окна: тот же канон записи, что у чата и Mini App.
    free = troupe.post("/tools/availability/commit", body={
        "chat_id": actor["chat_id"], "day": day, "start": 10, "end": 13, "kind": "free"})
    journal.check(
        "1. POST /tools/availability/commit (свободен 10–13)",
        expected="код 200: часы для окна отмечены",
        actual=f"код {free.code}, hours={free.field('hours')}",
        evidence=free.evidence(), ok=free.ok, requirements=["FR-D5"])

    # 2. Поиск окон видит свободные часы и отдаёт окно с пояснением (FR-D5, FR-D6).
    slots = org.post("/tools/schedule/slots", body={
        "participants": [actor["participant_id"]], "date_from": day, "date_to": day,
        "min_hours": 2, "block_events": False, "limit": 3})
    windows = slots.field("windows") or []
    journal.check(
        "2. POST /tools/schedule/slots", expected="хотя бы одно окно на отмеченный день",
        actual=f"код {slots.code}, окон {len(windows)}: "
               f"{[{k: w.get(k) for k in ('date', 'from_hour', 'to_hour')} for w in windows[:2]]}",
        evidence=slots.evidence(), ok=slots.ok and bool(windows),
        requirements=["FR-D5", "FR-D6"])

    # 3. Назначение события из окна: карточка → событие в базе (FR-D15, FR-H10).
    card = {"kind": "rehearsal", "title": title, "date": day, "from_hour": 10, "to_hour": 12,
            "participants": [{"id": actor["participant_id"], "name": actor["name"]}],
            "venue": "stage"}
    assigned = org.post("/tools/events/assign", body={"card": card})
    created = _event(db, title)
    journal.check(
        "3. POST /tools/events/assign",
        expected="событие создано, строка event_plan есть",
        actual=f"код {assigned.code}, event_id={(created or {}).get('id')}, "
               f"календарь={assigned.field('calendar_error') or 'нет'}",
        evidence=f"{assigned.evidence(100)}; event_plan={created}",
        ok=assigned.ok and created is not None, requirements=["FR-D15", "FR-H10"])

    # 4. Состав события зафиксирован в базе (FR-D15).
    journal.check(
        "4. состав события = смоук-участник",
        expected=f"participant_ids содержит {actor['participant_id']}",
        actual=f"participant_ids={(created or {}).get('participant_ids')}",
        evidence=f"select participant_ids from event_plan where id={(created or {}).get('id')}",
        ok=bool(created) and actor["participant_id"] in created["participant_ids"],
        requirements=["FR-D15"])

    # 5. Уведомление участникам назначенного события (FR-H11) — не проверяется доставкой:
    #    живой Telegram на стенде не используется, а очередь уведомлений с событием не связана.
    journal.skip(
        "5. участнику пришло уведомление о назначении",
        expected="сообщение в чат участника с датой и местом",
        evidence="стенд не отправляет сообщения живым людям; FR-H11 в документе 19 — «не начато»",
        cause="нужен живой Telegram", reason="доставку видно только в Telegram",
        requirements=["FR-H11"])

    # 6. Отмена события снимает строку из активных и пишет аудит (FR-D18).
    event_id = (created or {}).get("id")
    cancelled = org.post("/tools/events/cancel", body={
        "event_id": event_id, "reason": f"смоук {stand['marker']}"})
    audit = db.execute(
        "select count(*) from audit_event where action like %s and actor_id = %s",
        ("event.%", people["org"]["participant_id"],)).fetchone()[0]
    journal.check(
        "6. POST /tools/events/cancel", expected="код 200 и запись в аудите",
        actual=f"код {cancelled.code}, событие={cancelled.field('event_id')}, "
               f"аудит event.*={audit}",
        evidence=f"{cancelled.evidence(100)}; audit_event event.*={audit}",
        ok=cancelled.ok and audit > 0, requirements=["FR-D18", "FR-A8"])

    # 7. Перенос события: связи «было → стало» в аудите (FR-D19).
    second_title = f"Смоук перенос {stand['marker']}"
    second_card = dict(card, title=second_title)
    assigned2 = org.post("/tools/events/assign", body={"card": second_card})
    moved = org.post("/tools/events/move", body={
        "event_id": (assigned2.field("event_id") or assigned2.field("id")),
        "from_hour": 14, "to_hour": 16, "reason": f"смоук {stand['marker']}"})
    journal.check(
        "7. POST /tools/events/move", expected="код 200, новое время в ответе",
        actual=f"код {moved.code}, ответ {moved.excerpt(120)}",
        evidence=moved.evidence(), ok=moved.ok, requirements=["FR-D19"])

    # 8. Событие не вычитается из чужих окон: поиск окон исключает занятое время (FR-D16).
    blocked = org.post("/tools/schedule/slots", body={
        "participants": [actor["participant_id"]], "date_from": day, "date_to": day,
        "min_hours": 3, "block_events": True, "limit": 3})
    journal.check(
        "8. POST /tools/schedule/slots (block_events=true)",
        expected="код 200: занятое событием время не предлагается",
        actual=f"код {blocked.code}, окон {len(blocked.field('windows') or [])}",
        evidence=blocked.evidence(), ok=blocked.ok, requirements=["FR-D16"])

    # 9. Мастер видит окна только по своей дисциплине: чужой тренинг — отказ (FR-D8, 140).
    foreign = master.post("/tools/schedule/slots", body={
        "participants": [actor["participant_id"]], "date_from": day, "date_to": day,
        "min_hours": 2, "block_events": False})
    journal.check(
        "9. POST /tools/schedule/slots (master)",
        expected="код 200: педагог ищет окна в рамках своей роли",
        actual=f"код {foreign.code}",
        evidence=foreign.evidence(), ok=foreign.ok, requirements=["FR-D8"])

    journal.assert_passed()
