"""Теги: общий словарь с черновиками (FR-K17, Ш-10).

Почему черновики, а не свободный ввод: через месяц «реквизит», «Реквизит» и «реквезит»
разъезжаются по трём тегам, и поиск перестаёт находить. Свободный ввод падает в `draft`,
утверждает админ; до утверждения тег видит только автор.
"""

from __future__ import annotations

import re

from .errors import ApiError

ADMIN_ROLES = {"admin"}
SLUG_BAD = re.compile(r"[^\w\- ]+", re.UNICODE)


class TagError(ApiError):
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


def clean_title(raw: str) -> str:
    title = " ".join((raw or "").split())
    if not title:
        raise TagError("tag_title_empty", "Название тега пустое.")
    if len(title) > 40:
        raise TagError("tag_title_long", "Название тега длиннее 40 символов.")
    return title


def slugify(title: str) -> str:
    slug = SLUG_BAD.sub("", title.lower()).strip()
    slug = "-".join(slug.split())
    if not slug:
        raise TagError("tag_title_bad", "Из такого названия тег не собрать.")
    return slug[:60]


def list_tags(conn, who: dict) -> list[dict]:
    """Утверждённые теги видят все; свои черновики — только автор и админ."""
    admin = bool(roles_of(who) & ADMIN_ROLES)
    rows = conn.execute(
        "select t.id, t.slug, t.title, t.kind, t.status, count(ft.file_id) as uses"
        "  from tag t left join file_tag ft on ft.tag_id = t.id"
        " where t.status = 'approved' or %s or t.created_by = %s"
        " group by t.id order by t.status, uses desc, t.title",
        (admin, who.get("id"))).fetchall()
    return [dict(row) for row in rows]


def create_tag(conn, who: dict, title: str) -> dict:
    """Создать тег: админ сразу получает утверждённый, остальные — черновик."""
    title = clean_title(title)
    slug = slugify(title)
    existing = conn.execute("select id, title, status from tag where slug = %s or title = %s",
                            (slug, title)).fetchone()
    if existing:
        return {"id": existing["id"], "title": existing["title"],
                "status": existing["status"], "created": False}
    approved = bool(roles_of(who) & ADMIN_ROLES)
    row = conn.execute(
        "insert into tag (slug, title, status, created_by, approved_by, approved_at)"
        " values (%s, %s, %s, %s, %s, case when %s then now() end)"
        " returning id, title, status",
        (slug, title, "approved" if approved else "draft", who.get("id"),
         who.get("id") if approved else None, approved)).fetchone()
    return {"id": row["id"], "title": row["title"], "status": row["status"], "created": True}


def approve_tag(conn, who: dict, tag_id: int) -> dict:
    if not roles_of(who) & ADMIN_ROLES:
        raise TagError("tag_forbidden", "Теги утверждает администратор.", 403)
    row = conn.execute(
        "update tag set status = 'approved', approved_by = %s, approved_at = now()"
        " where id = %s returning id, title, status", (who.get("id"), tag_id)).fetchone()
    if row is None:
        raise TagError("tag_not_found", "Тег не найден.", 404)
    return dict(row)


def set_file_tags(conn, who: dict, file_id: int, tag_ids: list[int]) -> list[dict]:
    """Полная замена тегов файла. Чужой черновик прицепить нельзя — только свой."""
    admin = bool(roles_of(who) & ADMIN_ROLES)
    allowed: list[int] = []
    for tag_id in dict.fromkeys(int(value) for value in tag_ids):
        row = conn.execute("select status, created_by from tag where id = %s",
                           (tag_id,)).fetchone()
        if row is None:
            raise TagError("tag_not_found", f"Тег {tag_id} не найден.", 404)
        if row["status"] == "draft" and not admin and row["created_by"] != who.get("id"):
            raise TagError("draft_tag", "Этот тег ещё не утверждён.", 409)
        allowed.append(tag_id)
    conn.execute("delete from file_tag where file_id = %s", (file_id,))
    for tag_id in allowed:
        conn.execute("insert into file_tag (file_id, tag_id) values (%s,%s)"
                     " on conflict do nothing", (file_id, tag_id))
    return tags_of(conn, file_id)


def tags_of(conn, file_id: int) -> list[dict]:
    rows = conn.execute(
        "select t.id, t.title, t.status from file_tag ft join tag t on t.id = ft.tag_id"
        " where ft.file_id = %s order by t.title", (file_id,)).fetchall()
    return [dict(row) for row in rows]
