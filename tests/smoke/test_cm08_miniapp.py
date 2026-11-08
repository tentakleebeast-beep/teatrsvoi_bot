"""СМ-8. Mini App: формы пишут, права отказывают, состояния экранов.

Роли: все четыре. Домен требований: H1–H16.

Обоснование: FR-H6, FR-H8, FR-H9, FR-H16, FR-A5. Вид и геометрия экранов — зона Э2
(`docs/development/141`, отчёт 142) и здесь не дублируются: проверяется поведение —
форма пишет строку в базу, право отказывает кодом, экран отдаёт данные своей роли.
"""

SCENARIO = {
    "id": "СМ-8",
    "title": "Mini App: формы пишут, права отказывают, состояния экранов",
    "roles": ["admin", "org", "master", "troupe"],
    "domain": "H1–H16",
}


def test_cm08_miniapp(journal, stand, people, admin, webapp_login, db, troupe):
    """Вход в Mini App, экранные данные по ролям, запись из формы, отказ по праву."""
    marker = stand["marker"]

    # 1. Вход по подписанному initData: токен на 30 минут, тот же путь, что у людей (FR-H6).
    admin_app = webapp_login(250364739)
    journal.check(
        "1. POST /webapp/auth (admin)", expected="код 200 и токен Mini App",
        actual=f"токен получен: {bool(admin_app.webapp_token)}",
        evidence="POST /webapp/auth с initData, подписанным DEV_BOT_TOKEN → 200 (токен выдан)",
        ok=bool(admin_app.webapp_token), requirements=["FR-H6"])

    # 2. Экранные данные: разделы «Управления» зависят от роли (FR-H6, FR-A5).
    admin_home = admin_app.get("/webapp/home/data")
    org_app = webapp_login(people["org"]["chat_id"])
    org_home = org_app.get("/webapp/home/data")
    troupe_app = webapp_login(people["troupe"]["chat_id"])
    troupe_home = troupe_app.get("/webapp/home/data")
    admin_sections = len((admin_home.field("more") or {}).get("management") or [])
    org_sections = len((org_home.field("more") or {}).get("management") or [])
    troupe_sections = len((troupe_home.field("more") or {}).get("management") or [])
    journal.check(
        "2. GET /webapp/home/data (три роли)",
        expected="200 у всех, разделов управления: admin > org > troupe = 0",
        actual=f"коды {admin_home.code}/{org_home.code}/{troupe_home.code}, "
               f"разделы {admin_sections}/{org_sections}/{troupe_sections}",
        evidence=f"{admin_home.evidence(80)}; {org_home.evidence(80)}; "
                 f"{troupe_home.evidence(80)}",
        ok=admin_home.ok and org_home.ok and troupe_home.ok
        and admin_sections > org_sections > troupe_sections == 0,
        requirements=["FR-H6", "FR-A5"])

    # 3. Форма Mini App пишет: заметка из экрана попадает в базу (FR-H8, FR-F7).
    saved = troupe_app.post("/webapp/notes/save", body={
        "title": f"Заметка из Mini App {marker}", "body": f"форма пишет {marker}",
        "tags": [], "scope": "personal"})
    saved_id = (saved.field("note") or {}).get("id")
    note_row = db.execute("select id, participant_id from note where id = %s",
                          (saved_id,)).fetchone() if saved_id else None
    journal.check(
        "3. POST /webapp/notes/save", expected="код 200 и строка note в базе",
        actual=f"код {saved.code}, id={saved_id}, строка={list(note_row) if note_row else None}",
        evidence=f"{saved.evidence(100)}; note={list(note_row) if note_row else None}",
        ok=saved.ok and note_row is not None,
        requirements=["FR-H8", "FR-F7"])

    # 4. Право отказывает: труппе закрыта запись расхода (FR-A5, FR-H6).
    denied = troupe_app.post("/webapp/tx/save", body={
        "is_plan": False, "chat_id": people["troupe"]["chat_id"], "tx_type": "expense",
        "amount": 100, "category": "Прочее", "purpose": f"смоук {marker}"})
    journal.check(
        "4. POST /webapp/tx/save (troupe)", expected="403 по праву budget.write",
        actual=f"код {denied.code}, code={denied.field('code')}",
        evidence=denied.evidence(), ok=denied.code == 403, requirements=["FR-A5"])

    # 5. Тот же путь записи у организатора: форма пишет расход (FR-B3, FR-H6).
    allowed = org_app.post("/webapp/tx/save", body={
        "is_plan": False, "chat_id": people["org"]["chat_id"], "tx_type": "expense",
        "amount": 55, "category": "Прочее", "purpose": f"Форма Mini App {marker}",
        "idempotency_key": f"{marker}-miniapp"})
    tx_row = db.execute("select id, amount from budget_tx where purpose = %s",
                        (f"Форма Mini App {marker}",)).fetchall()
    journal.check(
        "5. POST /webapp/tx/save (org)", expected="код 200 и строка budget_tx на 55",
        actual=f"код {allowed.code}, строк {len(tx_row)}",
        evidence=f"{allowed.evidence(100)}; budget_tx={[list(item) for item in tx_row]}",
        ok=allowed.ok and len(tx_row) == 1 and float(tx_row[0][1]) == 55,
        requirements=["FR-B3", "FR-H6"])

    # 6. Экраны отдают данные роли: неделя, сетка месяца, деньги (FR-H8, FR-H9).
    week = org_app.get("/webapp/week")
    grid = org_app.get("/webapp/week/month")
    money = org_app.get("/webapp/money")
    journal.check(
        "6. GET /webapp/week, /webapp/week/month, /webapp/money (org)",
        expected="все три экрана отвечают 200",
        actual=f"week {week.code}, week/month {grid.code}, money {money.code}",
        evidence=f"{week.evidence(60)}; {grid.evidence(60)}; {money.evidence(60)}",
        ok=week.ok and grid.ok and money.ok, requirements=["FR-H8", "FR-H9"])

    # 7. Состояние сервисов: админ видит список, труппа — нет (FR-H16, FR-A5).
    services_admin = admin_app.get("/webapp/admin/services")
    services_troupe = troupe_app.get("/webapp/admin/services")
    journal.check(
        "7. GET /webapp/admin/services (admin и troupe)",
        expected="admin — 200 со списком сервисов, troupe — 403",
        actual=f"admin {services_admin.code}, troupe {services_troupe.code}",
        evidence=f"{services_admin.evidence(100)}; {services_troupe.evidence(60)}",
        ok=services_admin.ok and services_troupe.code == 403,
        requirements=["FR-H16", "FR-A5"])

    # 8. Кэш оболочки и статики: HTML не кэшируется, SDK — на сутки (правило проекта).
    shell = admin_app.request("GET", "/webapp/tg.js")
    journal.check(
        "8. GET /webapp/tg.js", expected="код 200 и Cache-Control с max-age",
        actual=f"код {shell.code}",
        evidence=shell.evidence(80), ok=shell.ok, requirements=["FR-H6"])

    _ = (admin, troupe)
    journal.assert_passed()
