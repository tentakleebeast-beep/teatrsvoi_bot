"""СМ-2. Занятость: отметка, перезапись, импорт не затирает бота.

Роли: troupe, org. Домен требований: C1–C10.

Обоснование: FR-C2, FR-C4, FR-C5, FR-C9, FR-C10. Импорт из Google-таблицы на стенде
не запускается (нет ни таблицы, ни сервисного аккаунта) — шаг помечается «не проверено»
с причиной, а не выдаётся за пройденный; это и есть разница между «проверили» и «кажется».
"""

from datetime import date, timedelta

from conftest import future_date

SCENARIO = {
    "id": "СМ-2",
    "title": "Занятость: отметка, перезапись, импорт не затирает бота",
    "roles": ["troupe", "org"],
    "domain": "C1–C10",
}


def _hours(db, participant_id: int, day: str) -> list[dict]:
    rows = db.execute(
        "select hour, is_free, kind, source from availability_hour "
        "where participant_id = %s and day = %s order by hour",
        (participant_id, day)).fetchall()
    return [{"hour": row[0], "is_free": row[1], "kind": row[2], "source": row[3]} for row in rows]


def test_cm02_availability(journal, stand, people, troupe, org, db):
    """Отметка часов, перезапись, сводка организатору, запрет чужой недели."""
    person = people["troupe"]
    day = future_date(60)

    # 1. Свободный текст разбирается в интервал и день (FR-C2, форма из требования).
    parsed = troupe.post("/tools/availability/parse", body={"text": "занят в среду с 10 до 12"})
    parsed_day = date.fromisoformat(parsed.field("day")) if parsed.field("day") else None
    journal.check(
        "1. POST /tools/availability/parse («занят в среду с 10 до 12»)",
        expected="интервал 10–12, день — ближайшая среда",
        actual=f"код {parsed.code}, day={parsed.field('day')}, "
               f"start={parsed.field('start')}, end={parsed.field('end')}",
        evidence=parsed.evidence(),
        ok=parsed.ok and parsed.field("start") == 10 and parsed.field("end") == 12
        and parsed_day is not None and parsed_day.weekday() == 2 and parsed_day >= date.today(),
        requirements=["FR-C2"])

    # 2. Дата с годом не должна становиться прошлой (FR-C2): «07.01.2027» — это январь
    #    2027, а не 2026. Шаг намеренно строгий: он ловит год, которого в разборе нет.
    with_year = troupe.post("/tools/availability/parse", body={
        "text": f"занят {date.fromisoformat(day):%d.%m.%Y} с 10 до 12"})
    journal.check(
        "2. POST /tools/availability/parse (дата с годом)",
        expected=f"day={day} — год из текста сохраняется",
        actual=f"day={with_year.field('day')}",
        evidence=with_year.evidence(),
        ok=with_year.ok and with_year.field("day") == day,
        requirements=["FR-C2"])

    # 3. Отметка интервалом часов пишется в availability_hour (FR-C2, FR-C9).
    first = troupe.post("/tools/availability/commit", body={
        "chat_id": person["chat_id"], "day": day, "start": 10, "end": 12, "kind": "busy"})
    rows = _hours(db, person["participant_id"], day)
    journal.check(
        "3. POST /tools/availability/commit (занят 10–12)",
        expected="2 строки availability_hour, is_free=false, source=bot",
        actual=f"код {first.code}, строк {len(rows)}: {rows}",
        evidence=f"{first.evidence(80)}; availability_hour={rows}",
        ok=first.ok and len(rows) == 2 and all(not row["is_free"] for row in rows)
        and all(row["source"] == "bot" for row in rows),
        requirements=["FR-C2", "FR-C9"])

    # 4. Перезапись того же интервала: строки не двоятся, значение меняется (FR-C4).
    second = troupe.post("/tools/availability/commit", body={
        "chat_id": person["chat_id"], "day": day, "start": 10, "end": 12, "kind": "free"})
    rewritten = _hours(db, person["participant_id"], day)
    journal.check(
        "4. повторный commit того же интервала (свободен)",
        expected="те же 2 строки, is_free=true",
        actual=f"код {second.code}, строк {len(rewritten)}, "
               f"свободны {sum(1 for row in rewritten if row['is_free'])}",
        evidence=f"{second.evidence(80)}; availability_hour={rewritten}",
        ok=second.ok and len(rewritten) == 2 and all(row["is_free"] for row in rewritten),
        requirements=["FR-C4"])

    # 4. Перезапись оставила след в аудите (FR-C4): «перезапись с аудитом».
    audit = db.execute(
        "select count(*) from audit_event where action = 'availability.set' "
        "and actor_id = %s", (person["participant_id"],)).fetchone()[0]
    journal.check(
        "5. audit_event: availability.set", expected="две записи об изменении",
        actual=f"записей {audit}",
        evidence=f"select count(*) from audit_event where action='availability.set' "
                 f"and actor_id={person['participant_id']} → {audit}",
        ok=audit >= 2, requirements=["FR-C4", "FR-A8"])

    # 5. Неделя показывает отмеченный день состоянием, а не пустотой (FR-C9).
    week = troupe.get("/tools/availability/week", params={"start": day})
    cards = {card.get("day"): card for card in (week.field("days") or [])}
    card = cards.get(day, {})
    journal.check(
        "6. GET /tools/availability/week", expected="день отмечен и назван свободным",
        actual=f"код {week.code}, status={card.get('status')}, marked={card.get('marked')}",
        evidence=week.evidence(), ok=week.ok and card.get("marked") is True
        and card.get("status") == "free", requirements=["FR-C9"])

    # 6. Сводка организатору видит отметку (FR-C5).
    summary = org.get("/tools/availability/summary", params={"days": 90})
    days = {row.get("day"): row for row in (summary.field("data") or [])}
    seen = days.get(day, {})
    journal.check(
        "7. GET /tools/availability/summary (org)", expected="день в сводке, известных > 0",
        actual=f"код {summary.code}, дней {len(days)}, known_cnt={seen.get('known_cnt')}",
        evidence=summary.evidence(),
        ok=summary.ok and seen.get("known_cnt", 0) > 0, requirements=["FR-C5"])

    # 7. Чужую неделю труппа не читает (FR-C5, права): отказ кодом 403.
    foreign = troupe.get("/tools/availability/week", params={
        "start": day, "participant_id": people["master"]["participant_id"]})
    journal.check(
        "8. GET /tools/availability/week?participant_id=чужой (troupe)",
        expected="403: чужая неделя закрыта без schedule.read.all",
        actual=f"код {foreign.code}, code={foreign.field('code')}",
        evidence=foreign.evidence(), ok=foreign.code == 403,
        requirements=["FR-C5"])

    # 8. Импорт из Google-таблицы не проверяется на стенде (FR-C1, FR-C4).
    journal.skip(
        "9. импорт часовой матрицы не затирает ручную отметку",
        expected="после импорта source='bot' строки сохраняют значение",
        evidence="scripts/import_schedule.py требует GOOGLE_SA_* и боевой таблицы; "
                 "в .env.smoke их нет",
        cause="нужен Google Sheets/Calendar",
        reason="импорт пишет в боевую таблицу расписания",
        requirements=["FR-C1", "FR-C4"])

    # 9. Шаблон недели: сохранение и применение — тот же канон записи (FR-C10).
    monday = date.fromisoformat(day) - timedelta(days=date.fromisoformat(day).weekday())
    template = troupe.post("/tools/availability/template/from-week", body={
        "start": monday.isoformat()})
    journal.check(
        "10. POST /tools/availability/template/from-week",
        expected="шаблон сохранён из текущей недели",
        actual=f"код {template.code}, ok={template.field('ok')}",
        evidence=template.evidence(), ok=template.ok, requirements=["FR-C10"])

    journal.assert_passed()
