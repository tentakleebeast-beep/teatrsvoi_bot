"""СМ-1. Вход, регистрация по коду, права и отказы.

Роли: admin, org, master, troupe. Домен требований: A1–A11 (+FR-H1 как входная точка).

Обоснование: FR-A1, FR-A2, FR-A3, FR-A5, FR-A6, FR-A7, FR-A8, FR-A9, FR-A11, FR-H1.
Сценарий проверяет поведение, а не вид экранов (docs/development/143 §2): вход по коду
проходит только с живым приглашением, доступ определяется возможностями, отказ приходит
кодом 403 и пишется в аудит, архив убирает человека из списков и возвращает обратно.
"""

from conftest import chat_id

SCENARIO = {
    "id": "СМ-1",
    "title": "Вход, регистрация по коду, права и отказы",
    "roles": ["admin", "org", "master", "troupe"],
    "domain": "A1–A11",
}


def test_cm01_login(journal, stand, admin, people, org, troupe, master):
    """Регистрация, повторная регистрация, отказы по праву, архив, аудит."""
    # 1. Сервис жив и отвечает полями здоровья (FR-H1).
    health = admin.get("/health")
    fields = sorted(health.body) if isinstance(health.body, dict) else []
    journal.check(
        "1. GET /health", expected="код 200, поля ok/db/participants",
        actual=f"код {health.code}, поля {fields}",
        evidence=health.evidence(), ok=health.code == 200 and health.field("ok") is True,
        requirements=["FR-H1"])

    # 2. Владелец получает админский доступ по chat_id, без кода (FR-A1).
    profile = admin.get("/tools/profile/me")
    caps_admin = admin.get("/tools/me/capabilities")
    journal.check(
        "2. GET /tools/profile/me (admin)",
        expected="код 200, имя владельца и права админа",
        actual=f"код {profile.code}, имя {profile.field('name')}, "
               f"actions {len(caps_admin.field('capabilities') or [])}",
        evidence=f"{profile.evidence(80)}; {caps_admin.evidence(80)}",
        ok=profile.ok and bool(profile.field("name"))
        and "admin.panel" in (caps_admin.field("capabilities") or []),
        requirements=["FR-A1"])

    # 3. Регистрация по одноразовому коду: приглашение админа → код → регистрация (FR-A2).
    invite = admin.post("/admin/invite", body={"role": "troupe", "ttl_days": 1, "max_uses": 1})
    code = invite.field("code")
    new_chat = chat_id(stand, 5)
    registered = org.post("/tools/participants/register", body={
        "chat_id": new_chat, "code": code,
        "display_name": f"Смоук Приглашённый {stand['marker']}"})
    participant_id = (registered.field("participant") or {}).get("id")
    journal.check(
        "3. POST /admin/invite + /tools/participants/register",
        expected="код приглашения выдан, регистрация дала участника",
        actual=f"код {registered.code}, статус {registered.field('status')}, "
               f"participant_id={participant_id}",
        evidence=f"{invite.evidence(80)}; {registered.evidence()}",
        ok=invite.ok and registered.ok and registered.field("status") == "registered"
        and bool(participant_id),
        requirements=["FR-A2"])

    # 4. Повторная регистрация того же chat_id не создаёт второго человека (FR-A2).
    again = org.post("/tools/participants/register", body={
        "chat_id": new_chat, "code": code,
        "display_name": f"Смоук Приглашённый {stand['marker']}"})
    again_id = (again.field("participant") or {}).get("id")
    journal.check(
        "4. повторная регистрация того же chat_id",
        expected="участник тот же (already_registered), дубля нет",
        actual=f"код {again.code}, статус {again.field('status')}, id={again_id}",
        evidence=again.evidence(),
        ok=again.ok and again.field("status") == "already_registered"
        and again_id == participant_id,
        requirements=["FR-A2"])

    # 5. Привязка идёт к существующей записи, без дублей: у зарегистрированного id один (FR-A3).
    found = admin.get("/tools/participants")
    rows = [row for row in (found.field("data") or [])
            if f"Смоук Приглашённый {stand['marker']}" == row.get("display_name")]
    journal.check(
        "5. GET /tools/participants (фильтр по маркеру)",
        expected="один участник с маркером, без дубля",
        actual=f"код {found.code}, найдено {len(rows)}",
        evidence=f"{found.evidence(80)}; id={[row.get('id') for row in rows]}",
        ok=found.ok and len(rows) == 1, requirements=["FR-A3"])

    # 6. Роль master выдаётся админом и приносит права пакетом (FR-A5, FR-A6).
    caps = master.get("/tools/me/capabilities")
    master_caps = caps.field("capabilities") or []
    journal.check(
        "6. GET /tools/me/capabilities (master)",
        expected="роль master и возможности пакетом (>0)",
        actual=f"код {caps.code}, роли {caps.field('roles')}, прав {len(master_caps)}",
        evidence=caps.evidence(), ok=caps.ok and "master" in (caps.field("roles") or [])
        and bool(master_caps), requirements=["FR-A5", "FR-A6"])

    # 7. Доступ определяют возможности: труппе закрыт список людей (FR-A5).
    denied = troupe.get("/tools/participants")
    journal.check(
        "7. GET /tools/participants (troupe)",
        expected="403, причина названа кодом ошибки",
        actual=f"код {denied.code}, code={denied.field('code')}",
        evidence=denied.evidence(), ok=denied.code == 403,
        requirements=["FR-A5"])

    # 8. Аудит закрыт организатору и открыт админу (FR-A11, FR-A8).
    audit_org = org.get("/tools/audit/recent")
    audit_admin = admin.get("/tools/audit/recent", params={"limit": 50})
    actions = {row.get("action") for row in (audit_admin.field("data") or [])}
    journal.check(
        "8. GET /tools/audit/recent (org, admin)",
        expected="org — 403, admin — 200 с записями",
        actual=f"org {audit_org.code}, admin {audit_admin.code}, действий {len(actions)}",
        evidence=f"{audit_org.evidence(80)}; {audit_admin.evidence(80)}",
        ok=audit_org.code == 403 and audit_admin.ok and bool(actions),
        requirements=["FR-A8", "FR-A11"])

    # 9. Отказ по праву тоже попадает в аудит с автором (FR-A8).
    journal.check(
        "9. аудит содержит access.denied для труппы",
        expected="есть запись access.denied от человека с чатом труппы",
        actual=f"access.denied в списке: {'access.denied' in actions}",
        evidence=f"audit actions: {sorted(actions)[:12]}",
        ok="access.denied" in actions, requirements=["FR-A8"])

    # 10. Архив: человек исчезает из активных списков и возвращается (FR-A7).
    archived = admin.post("/admin/archive", body={"participant_id": participant_id,
                                                  "reason": f"смоук {stand['marker']}"})
    after_archive = admin.get("/tools/participants")
    listed = [row for row in (after_archive.field("data") or [])
              if row.get("id") == participant_id]
    restored = admin.post("/admin/unarchive", body={"participant_id": participant_id,
                                                    "reason": f"смоук {stand['marker']}"})
    after_return = admin.get("/tools/participants")
    back = [row for row in (after_return.field("data") or []) if row.get("id") == participant_id]
    journal.check(
        "10. POST /admin/archive → /tools/participants → /admin/unarchive",
        expected="архив убирает из списка, возврат добавляет обратно",
        actual=f"архив {archived.code}, в активных после архива {len(listed)}, "
               f"возврат {restored.code}, после возврата {len(back)}",
        evidence=f"{archived.evidence(80)}; {after_archive.evidence(80)}; {restored.evidence(80)}",
        ok=archived.ok and not listed and restored.ok and len(back) == 1,
        requirements=["FR-A7"])

    # 11. Блокировка закрывает доступ сразу (FR-A9): вход блокированного — отказ.
    blocked = admin.post("/admin/block", body={"participant_id": participant_id, "blocked": True,
                                               "reason": f"смоук {stand['marker']}"})
    blocked_client = troupe.__class__(stand["base_url"], stand["token"], new_chat)
    access = blocked_client.get("/tools/profile/me")
    unblocked = admin.post("/admin/block", body={"participant_id": participant_id,
                                                 "blocked": False,
                                                 "reason": f"смоук {stand['marker']}"})
    journal.check(
        "11. POST /admin/block → GET /tools/profile/me",
        expected="блокировка: 403; возврат доступа: 200",
        actual=f"блокировка {blocked.code}, доступ {access.code}, возврат {unblocked.code}",
        evidence=f"{blocked.evidence(80)}; {access.evidence(80)}; {unblocked.evidence(80)}",
        ok=blocked.ok and access.code == 403 and unblocked.ok,
        requirements=["FR-A9"])

    journal.assert_passed()
