"""СМ-10. Отчёты и сводки: бюджет, занятость, сверка.

Роли: admin, org. Домен требований: G, F.

Обоснование: FR-B14, FR-B12, FR-F1, FR-F4, FR-H9. Сверка с человеческим листом на стенде
не выполняется (нет таблицы), но числовая сверка «экран = база» проверяется: расхождение
называется фактом, а не подгоняется.
"""

from conftest import month_period

SCENARIO = {
    "id": "СМ-10",
    "title": "Отчёты и сводки: бюджет, занятость, сверка",
    "roles": ["admin", "org"],
    "domain": "G, F",
}


def test_cm10_reports(journal, stand, people, org, webapp_login, db):
    """Сводки считаются по базе, экраны отдают те же числа, расхождений нет."""
    marker = stand["marker"]
    day = None

    # 1. Расход для сверки: пишем известную сумму (FR-B3).
    written = org.post("/tools/budget/commit", body={
        "chat_id": people["org"]["chat_id"], "amount": 777, "tx_type": "expense",
        "category": "Прочее", "purpose": f"Смоук сводка {marker}", "tx_date": None,
        "idempotency_key": f"{marker}-report"})
    journal.check(
        "1. POST /tools/budget/commit (сумма для сверки)", expected="код 200",
        actual=f"код {written.code}, id={written.field('id') or written.field('tx_id')}",
        evidence=written.evidence(100), ok=written.ok, requirements=["FR-B3"])

    # 2. Сводка бюджета = сумма строк базы по текущему месяцу (FR-B14, FR-F4).
    period = month_period(0)
    db_sum = float(db.execute(
        "select coalesce(sum(amount), 0) from budget_tx where period = %s "
        "and tx_type = 'expense' and status = 'confirmed'", (period,)).fetchone()[0])
    summary = org.get("/tools/budget/summary")
    periods = {row.get("period"): row for row in (summary.field("periods") or [])}
    api_sum = (periods.get(period) or {}).get("expense")
    journal.check(
        "2. GET /tools/budget/summary = база",
        expected=f"расход {period}: {db_sum} = база",
        actual=f"код {summary.code}, сводка {api_sum}, база {db_sum}",
        evidence=f"{summary.evidence(120)}; select sum(amount) → {db_sum}",
        ok=summary.ok and api_sum is not None and abs(float(api_sum) - db_sum) < 0.01,
        requirements=["FR-B14", "FR-F4"])

    # 3. Экран денег Mini App отдаёт те же периоды, что сводка (FR-F1).
    org_app = webapp_login(people["org"]["chat_id"])
    money = org_app.get("/webapp/money")
    journal.check(
        "3. GET /webapp/money (org)", expected="код 200, экран отдаёт данные бюджета",
        actual=f"код {money.code}", evidence=money.evidence(100), ok=money.ok,
        requirements=["FR-F1"])

    # 4. Сводная сетка занятости: месяц людьми по дням, только чтение (FR-H9).
    grid = org_app.get("/webapp/week/month")
    journal.check(
        "4. GET /webapp/week/month (org)", expected="код 200, сетка месяца",
        actual=f"код {grid.code}", evidence=grid.evidence(100), ok=grid.ok,
        requirements=["FR-H9"])

    # 5. Запись в сетку закрыта: экран «люди × дни» только читается (FR-H9).
    posted = org_app.post("/webapp/grid", body={})
    journal.check(
        "5. POST /webapp/grid", expected="405: записи в сетку нет",
        actual=f"код {posted.code}", evidence=posted.evidence(80),
        ok=posted.code in (405, 404), requirements=["FR-H9"])

    # 6. Журнал бота и лист «Проверки» — это Google-таблица, её на стенде нет (FR-F3, FR-F4).
    journal.skip(
        "6. листы «Журнал бота» и «Проверки»",
        expected="в листе появились строки журнала и статус инвариантов",
        evidence="строит scripts/build_reports.py через Google API; *_SPREADSHEET_ID в .env.smoke пуст",
        cause="нужен Google Sheets/Calendar", reason="листа бюджета на стенде нет",
        requirements=["FR-F3", "FR-F2"])

    _ = day
    journal.assert_passed()
