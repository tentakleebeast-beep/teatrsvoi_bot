"""Доступ администратора к личному файлу: по причине, с уведомлением владельцу (FR-K9, Ш-7).

Правило, которое нельзя обойти: личный файл по умолчанию не видит никто, кроме владельца.
Админ получает его только через запись в `access_grant` — с непустой причиной, на ограниченный
срок и с уведомлением владельцу. Владелец может отозвать доступ в любой момент, и это
действует сразу: проверка идёт в БД на каждом запросе, кэша прав нет.

Почему не «админ видит всё»: у человека должно быть место, куда админ не заходит по умолчанию.
Иначе «личная папка» — это папка с табличкой, а не с замком.
"""

from __future__ import annotations

import json

from .errors import ApiError

DEFAULT_TTL_HOURS = 24
MAX_TTL_HOURS = 24 * 30


class AccessError(ApiError):
    """Ошибка операции: несёт машинный `code` и человеческий текст в конверте API.

    Наследуется от `ApiError`, поэтому до клиента доходит тем же телом ошибки, что и
    остальные отказы (docs/architecture/85), без переупаковки в ручках.
    """

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(status, message, code=code)
        self.message = message
        self.status = status


def roles_of(who: dict) -> set[str]:
    roles = set(who.get("roles") or [])
    roles.add(who.get("role") or "")
    return {role for role in roles if role}


def _file(conn, file_id: int) -> dict:
    row = conn.execute(
        "select id, participant_id, name, scope, deleted_at from file_object where id = %s",
        (file_id,)).fetchone()
    if row is None or row["deleted_at"] is not None:
        raise AccessError("file_not_found", "Файл не найден.", 404)
    return row


def request(conn, who: dict, file_id: int, reason: str,
            ttl_hours: int = DEFAULT_TTL_HOURS) -> dict:
    """Открыть админу доступ к чужому личному файлу. Владельцу уходит уведомление."""
    if "admin" not in roles_of(who):
        raise AccessError("access_forbidden", "Доступ к личным файлам открывает администратор.", 403)
    reason = " ".join((reason or "").split())
    if len(reason) < 3:
        raise AccessError("access_reason_required", "Нужна причина: без неё доступ не открывается.")
    ttl = max(1, min(int(ttl_hours or DEFAULT_TTL_HOURS), MAX_TTL_HOURS))

    row = _file(conn, file_id)
    owner_id = int(row["participant_id"] or 0)
    if owner_id == int(who.get("id") or 0):
        raise AccessError("access_own_file", "Это ваш файл — доступ не нужен.")
    if row["scope"] != "personal":
        raise AccessError("access_not_private", "Файл уже общий: доступ не требуется.")

    grant = conn.execute(
        """
        insert into access_grant (file_id, admin_id, reason, expires_at)
        values (%s, %s, %s, now() + make_interval(hours => %s))
        on conflict (file_id, admin_id) do update
           set reason = excluded.reason,
               expires_at = excluded.expires_at,
               notified_at = null,
               created_at = now()
        returning id, expires_at
        """, (file_id, int(who["id"]), reason, ttl)).fetchone()

    admin_name = who.get("display_name") or "администратор"
    conn.execute(
        """
        insert into notification (recipient_id, kind, payload, dedupe_key, scheduled_at)
        values (%s, 'file_access', %s, %s, now())
        on conflict (dedupe_key, recipient_id) do nothing
        """,
        (owner_id,
         json.dumps({"title": "Администратор запросил доступ к вашему файлу",
                     "body": (f"Файл: {row['name']}\nКто: {admin_name}\n"
                              f"Причина: {reason}\n"
                              f"Доступ открыт на {ttl} ч. Отозвать: /revoke {file_id}"),
                     "file_id": file_id, "admin_id": int(who.get("id") or 0)},
                    ensure_ascii=False),
         f"file_access:{file_id}:{grant['id']}:{int(who.get('id') or 0)}"))
    conn.execute(
        "update access_grant set notified_at = now() where id = %s", (grant["id"],))
    return {"grant_id": int(grant["id"]), "expires_at": grant["expires_at"],
            "owner_id": owner_id, "ttl_hours": ttl}


def revoke(conn, who: dict, file_id: int) -> dict:
    """Владелец закрывает доступ. Действует сразу: следующая проверка увидит пустоту."""
    row = _file(conn, file_id)
    if int(row["participant_id"] or 0) != int(who.get("id") or 0):
        if "admin" not in roles_of(who):
            raise AccessError("access_forbidden", "Отозвать доступ может владелец файла.", 403)
    closed = conn.execute(
        "delete from access_grant where file_id = %s returning admin_id", (file_id,)).fetchall()
    return {"file_id": file_id, "revoked": len(closed)}


def active_grant(conn, file_id: int, admin_id: int) -> dict | None:
    return conn.execute(
        "select id, reason, expires_at from access_grant"
        " where file_id = %s and admin_id = %s"
        "   and (expires_at is null or expires_at > now())",
        (file_id, admin_id)).fetchone()


def grants_of(conn, file_id: int) -> list[dict]:
    rows = conn.execute(
        "select g.admin_id, p.display_name as admin_name, g.reason, g.expires_at,"
        "       g.created_at, g.notified_at"
        "  from access_grant g left join participant p on p.id = g.admin_id"
        " where g.file_id = %s order by g.created_at desc", (file_id,)).fetchall()
    return [dict(row) for row in rows]
