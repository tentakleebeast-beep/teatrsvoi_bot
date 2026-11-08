"""СМ-5. Уведомления и рассылка: заголовок, «уйдёт N из M», дайджест.

Роли: admin, org. Домен требований: G1–G17.

Обоснование: FR-G2, FR-G3, FR-G17. Доставка в Telegram на стенде не проверяется:
адресаты — тестовые chat_id, а отправку видно только в живом чате. Очередь уведомлений
и тихие часы проверяются настройками, а не отправкой сообщения.
"""

from conftest import chat_id

SCENARIO = {
    "id": "СМ-5",
    "title": "Уведомления и рассылка: заголовок, «уйдёт N из M», дайджест",
    "roles": ["admin", "org"],
    "domain": "G1–G17",
}


def test_cm05_notifications(journal, stand, people, org, webapp_login):
    """Настройки уведомлений, предпросмотр рассылки, «уйдёт N из M»."""
    # 1. Настройки уведомлений по умолчанию: тихие часы 20:00–08:00 (FR-G3).
    settings = org.get("/tools/notifications/settings")
    data = settings.field("settings") or {}
    journal.check(
        "1. GET /tools/notifications/settings", expected="код 200, тихие часы 20:00–08:00",
        actual=f"код {settings.code}, quiet={data.get('quiet_from')}–{data.get('quiet_to')}, "
               f"source={data.get('source')}",
        evidence=settings.evidence(),
        ok=settings.ok and data.get("quiet_from") == "20:00" and data.get("quiet_to") == "08:00",
        requirements=["FR-G3"])

    # 2. Частные настройки важнее общих и сохраняются (FR-G17).
    saved = org.post("/tools/notifications/settings", body={
        "rehearsals": True, "dues": False, "schedule_changes": True,
        "quiet_from": "21:00", "quiet_to": "07:00"})
    after = org.get("/tools/notifications/settings").field("settings") or {}
    journal.check(
        "2. POST /tools/notifications/settings", expected="dues=false и тихие часы 21:00–07:00",
        actual=f"код {saved.code}, dues={after.get('dues')}, "
               f"quiet={after.get('quiet_from')}–{after.get('quiet_to')}, "
               f"source={after.get('source')}",
        evidence=saved.evidence(),
        ok=saved.ok and after.get("dues") is False and after.get("quiet_from") == "21:00",
        requirements=["FR-G17"])

    # 3. Предпросмотр рассылки считает аудиторию: «уйдёт N из M» (FR-G2).
    admin_app = webapp_login(250364739)
    preview = admin_app.get("/webapp/admin/broadcast/preview")
    total, reachable = preview.field("total"), preview.field("reachable")
    journal.check(
        "3. GET /webapp/admin/broadcast/preview",
        expected="код 200, total и reachable посчитаны, reachable ≤ total",
        actual=f"код {preview.code}, total={total}, reachable={reachable}, "
               f"без Telegram {preview.field('no_telegram')}",
        evidence=preview.evidence(), ok=preview.ok and isinstance(total, int)
        and isinstance(reachable, int) and 0 <= reachable <= total,
        requirements=["FR-G2"])

    # 4. Рассылка в режиме проверки не отправляет сообщений (FR-G2).
    dry = admin_app.post("/webapp/admin/broadcast", body={
        "text": f"Смоук рассылка {stand['marker']}", "dry_run": True})
    journal.check(
        "4. POST /webapp/admin/broadcast (dry_run)", expected="код 200, отправки нет",
        actual=f"код {dry.code}, получателей {dry.field('sent') or dry.field('reachable') or 0}",
        evidence=dry.evidence(), ok=dry.ok and not dry.field("sent"),
        requirements=["FR-G2"])

    # 5. Организатору рассылка закрыта правом notify.send (FR-G2).
    org_app = webapp_login(people["org"]["chat_id"])
    denied = org_app.get("/webapp/admin/broadcast/preview")
    journal.check(
        "5. GET /webapp/admin/broadcast/preview (org)", expected="403 по праву notify.send",
        actual=f"код {denied.code}, code={denied.field('code')}",
        evidence=denied.evidence(), ok=denied.code == 403, requirements=["FR-G2"])

    # 6. Доставка и дайджест в живом чате не проверяются (FR-G15, FR-G5).
    journal.skip(
        "6. доставка сообщения и недельный дайджест",
        expected="сообщение в чате адресата, дайджест организатору в понедельник",
        evidence=f"адресаты стенда — тестовые chat_id {chat_id(stand, 1)}…; "
                 f"FR-G15 в документе 19 — «не начато»",
        cause="нужен живой Telegram", reason="отправку видно только в живом чате",
        requirements=["FR-G5", "FR-G15"])

    journal.assert_passed()
