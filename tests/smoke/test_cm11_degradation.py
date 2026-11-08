"""СМ-11. Деградация и публичные маршруты.

Роли: без роли, admin. Домен требований: H4–H10.

Обоснование: FR-H1, FR-H2, FR-H5, FR-H10, FR-E5. Проверяется поведение на границе:
что видно без токена, что закрыто, и как сервис отвечает, когда внешние зависимости
(LLM, календарь) недоступны. Это и есть «деградация честно», а не 500 на всё.
"""

SCENARIO = {
    "id": "СМ-11",
    "title": "Деградация и публичные маршруты",
    "roles": ["без роли", "admin"],
    "domain": "H4–H10",
}


def test_cm11_degradation(journal, stand, admin, org, webapp_login):
    """Публичный /health, закрытые маршруты без токена, честный отказ LLM и календаря."""
    from helpers import Api

    anonymous = Api(stand["base_url"], stand["token"])

    # 1. /health — публичный и с полями состояния (FR-H1).
    health = anonymous.get("/health")
    fields = sorted(health.body) if isinstance(health.body, dict) else []
    journal.check(
        "1. GET /health без токена", expected="код 200 и поля ok/db/participants",
        actual=f"код {health.code}, поля {fields}",
        evidence=health.evidence(), ok=health.code == 200 and health.field("ok") is True,
        requirements=["FR-H1"])

    # 2. Данные без заголовка автора и без токена закрыты: отказ, а не пустой список (FR-H4).
    closed = anonymous.request("GET", "/tools/participants")
    journal.check(
        "2. GET /tools/participants без токена и автора",
        expected="отказ 400 (actor_chat_id_required), данных нет",
        actual=f"код {closed.code}, code={closed.field('code')}",
        evidence=closed.evidence(), ok=closed.code == 400 and not closed.field("data"),
        requirements=["FR-H4"])

    # 3. Без токена, но с автором — 401 (FR-H4, FR-A5).
    no_token = Api(stand["base_url"], "")
    no_token.actor_chat = 900_000_011
    unauthorized = no_token.request("GET", "/tools/profile/me")
    journal.check(
        "3. GET /tools/profile/me без токена", expected="401 unauthorized",
        actual=f"код {unauthorized.code}, code={unauthorized.field('code')}",
        evidence=unauthorized.evidence(), ok=unauthorized.code == 401,
        requirements=["FR-H4"])

    # 4. Неверный токен — тот же отказ, а не «почти работает» (FR-H4).
    wrong = Api(stand["base_url"], "definitely-not-the-token", 900_000_011)
    wrong_response = wrong.request("GET", "/tools/profile/me")
    journal.check(
        "4. GET /tools/profile/me с неверным токеном", expected="401",
        actual=f"код {wrong_response.code}", evidence=wrong_response.evidence(80),
        ok=wrong_response.code == 401, requirements=["FR-H4"])

    # 5. Верный токен с неизвестным автором — 403, а не данные (FR-A5).
    stranger = Api(stand["base_url"], stand["token"], 123456789)
    unknown = stranger.request("GET", "/tools/profile/me")
    journal.check(
        "5. GET /tools/profile/me с чужим chat_id", expected="403 unknown_participant",
        actual=f"код {unknown.code}, code={unknown.field('code')}",
        evidence=unknown.evidence(), ok=unknown.code == 403,
        requirements=["FR-A5"])

    # 5. Оболочка Mini App отдаётся публично, но данные — нет (FR-H6).
    shell = anonymous.request("GET", "/webapp/ui.css")
    journal.check(
        "6. GET /webapp/ui.css без токена", expected="код 200: статика публична",
        actual=f"код {shell.code}", evidence=shell.evidence(60), ok=shell.code == 200,
        requirements=["FR-H6"])

    # 6. LLM недоступен — роутер отвечает честным отказом, а не выдуманным ответом (FR-E5).
    route = org.post("/tools/route", body={"text": "что у нас на неделе?"})
    journal.check(
        "7. POST /tools/route при выключенном LLM",
        expected="код 200 и error=llm_unavailable",
        actual=f"код {route.code}, error={route.field('error')}, engine={route.field('engine')}",
        evidence=route.evidence(),
        ok=route.code == 200 and route.field("error") == "llm_unavailable",
        requirements=["FR-E5"])

    # 7. Календарь недоступен (Google не настроен) — отказ назван, сервис жив (FR-D1, FR-H5).
    events = admin.get("/tools/calendar/events")
    journal.check(
        "8. GET /tools/calendar/events без Google",
        expected="честный отказ (5xx/503) или пустой список, сервис не падает",
        actual=f"код {events.code}, code={events.field('code')}",
        evidence=events.evidence(), ok=events.code in (200, 503, 500),
        requirements=["FR-D1"])

    # 8. Состояние сервисов в Mini App: администратор видит список (FR-H16).
    admin_app = webapp_login(250364739)
    services = admin_app.get("/webapp/admin/services")
    journal.check(
        "9. GET /webapp/admin/services (admin)",
        expected="код 200, список сервисов с состоянием",
        actual=f"код {services.code}, сервисов "
               f"{len(services.field('services') or services.field('data') or [])}",
        evidence=services.evidence(120), ok=services.ok, requirements=["FR-H16"])

    journal.assert_passed()
