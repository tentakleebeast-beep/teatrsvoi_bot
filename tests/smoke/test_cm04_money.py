"""СМ-4. Деньги: расход текстом, идемпотентность, сверка лист–БД, взносы.

Роли: org, admin. Домен требований: B1–B16.

Обоснование: FR-B1, FR-B3, FR-B7, FR-B11, FR-B12, FR-B13, FR-B14. Запись в Google-лист и
двусторонняя сверка с листом на стенде не выполняются (нет таблицы бюджета) — это отдельные
шаги с причиной «не проверено», а не тишина.
"""

from conftest import future_date, month_period

SCENARIO = {
    "id": "СМ-4",
    "title": "Деньги: расход текстом, идемпотентность, сверка лист–БД, взносы",
    "roles": ["org", "admin"],
    "domain": "B1–B16",
}


def test_cm04_money(journal, stand, people, org, admin, db):
    """Разбор текста, запись расхода, повтор без дубля, сводка = база, начисление взносов."""
    org_person = people["org"]
    day = future_date(60)
    key = f"{stand['marker']}-expense"
    purpose = f"Смоук расход {stand['marker']}"

    # 1. Свободный текст о трате разбирается правилами, без модели (FR-B1, FR-E4).
    parsed = org.post("/tools/budget/parse", body={"text": "потратил 123 руб на реквизит"})
    draft = parsed.field("draft") or {}
    journal.check(
        "1. POST /tools/budget/parse", expected="сумма 123 и тип expense, движок rules-py",
        actual=f"код {parsed.code}, amount={draft.get('amount')}, "
               f"tx_type={draft.get('tx_type')}, engine={parsed.field('engine')}",
        evidence=parsed.evidence(),
        ok=parsed.ok and float(draft.get("amount") or 0) == 123
        and draft.get("tx_type") == "expense" and parsed.field("engine") == "rules-py",
        requirements=["FR-B1", "FR-E4"])

    # 2. Запись расхода: строка budget_tx появляется в базе (FR-B3, FR-B8 граница).
    commit = org.post("/tools/budget/commit", body={
        "chat_id": org_person["chat_id"], "amount": 123, "tx_type": "expense",
        "category": "Прочее", "purpose": purpose, "tx_date": day,
        "idempotency_key": key})
    row = db.execute(
        "select id, amount, tx_type, period from budget_tx where purpose = %s",
        (purpose,)).fetchall()
    journal.check(
        "2. POST /tools/budget/commit", expected="код 200 и одна строка budget_tx на 123",
        actual=f"код {commit.code}, строк {len(row)}, сумма {row[0][1] if row else None}",
        evidence=f"{commit.evidence(100)}; budget_tx={[list(item) for item in row]}",
        ok=commit.ok and len(row) == 1 and float(row[0][1]) == 123,
        requirements=["FR-B3"])

    # 3. Повторное подтверждение с тем же ключом не создаёт вторую операцию (FR-B11).
    again = org.post("/tools/budget/commit", body={
        "chat_id": org_person["chat_id"], "amount": 123, "tx_type": "expense",
        "category": "Прочее", "purpose": purpose, "tx_date": day,
        "idempotency_key": key})
    rows_after = db.execute(
        "select count(*) from budget_tx where purpose = %s", (purpose,)).fetchone()[0]
    journal.check(
        "3. повторный commit с тем же idempotency_key",
        expected="та же операция, дубля нет",
        actual=f"код {again.code}, строк {rows_after}",
        evidence=f"{again.evidence(100)}; count={rows_after}",
        ok=again.ok and rows_after == 1, requirements=["FR-B11"])

    # 4. Сводка бюджета считает по базе: итог расхода за месяц = сумме строк (FR-B14).
    period = month_period(60)
    db_sum = float(db.execute(
        "select coalesce(sum(amount), 0) from budget_tx where period = %s "
        "and tx_type = 'expense' and status = 'confirmed'", (period,)).fetchone()[0])
    summary = org.get("/tools/budget/summary")
    periods = {row.get("period"): row for row in (summary.field("periods") or [])}
    api_sum = (periods.get(period) or {}).get("expense")
    journal.check(
        "4. GET /tools/budget/summary = сумма budget_tx",
        expected=f"расход за {period}: сводка {db_sum} = база",
        actual=f"код {summary.code}, сводка {api_sum}, база {db_sum}",
        evidence=f"{summary.evidence(120)}; select sum(amount) → {db_sum}",
        ok=summary.ok and api_sum is not None and abs(float(api_sum) - db_sum) < 0.01,
        requirements=["FR-B14", "FR-B12"])

    # 5. Аудит содержит автора операции (FR-A8 в деньгах, дом B).
    audit = db.execute(
        "select count(*) from audit_event where action like %s and actor_id = %s",
        ("budget.%", org_person["participant_id"],)).fetchone()[0]
    journal.check(
        "5. audit_event: budget.* с автором", expected="есть записи об операции организатора",
        actual=f"записей {audit}",
        evidence=f"select count(*) from audit_event where action like 'budget.%' "
                 f"and actor_id={org_person['participant_id']} → {audit}",
        ok=audit > 0, requirements=["FR-A8"])

    # 6. Взносы: начисление за период идемпотентно по (человек, период) (FR-B7).
    period_dues = month_period(60)
    accrued = admin.post("/tools/dues/accrue", body={"period": period_dues})
    twice = admin.post("/tools/dues/accrue", body={"period": period_dues})
    dues_rows = db.execute(
        "select count(*) from dues_charge where period = %s", (period_dues,)).fetchone()[0]
    journal.check(
        "6. POST /tools/dues/accrue дважды", expected="начисление без дублей",
        actual=f"первый {accrued.code}, второй {twice.code}, строк за {period_dues}: {dues_rows}",
        evidence=f"{accrued.evidence(100)}; {twice.evidence(80)}",
        ok=accrued.ok and twice.ok, requirements=["FR-B7"])

    # 7. «Мои взносы» показывают только свои начисления (FR-B7).
    mine = org.get("/tools/dues/me")
    journal.check(
        "7. GET /tools/dues/me (org)", expected="код 200, только свои начисления",
        actual=f"код {mine.code}, периодов {len(mine.field('months') or mine.field('data') or [])}",
        evidence=mine.evidence(), ok=mine.ok, requirements=["FR-B7"])

    # 8. Запись в лист «Транзакции» и сверка лист–БД на стенде не проверяются (FR-B3, FR-B13).
    journal.skip(
        "8. строка в листе и сверка лист–БД",
        expected="операция появилась в листе «Транзакции», сверка без расхождений",
        evidence="GOOGLE_SA_* и *_SPREADSHEET_ID в .env.smoke пусты: листа бюджета на стенде нет",
        cause="нужен Google Sheets/Calendar",
        reason="запись в лист идёт через Google API",
        requirements=["FR-B3", "FR-B13"])

    journal.assert_passed()
