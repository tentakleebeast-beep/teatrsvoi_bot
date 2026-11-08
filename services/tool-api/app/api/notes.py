"""Заметки: личные и общие (FR-F7, docs/development/19).

Зачем отдельный модуль. Таблица `note` и право `notes.write` были с самого начала,
но пути к ним не было ни из чата, ни из Mini App — требование FR-F7 стояло «не начато»
именно из-за отсутствия маршрутов.

Два входа — один код записи, как у уведомлений и бюджета:

    чат           POST /tools/notes, POST /tools/notes/{id}
    Mini App      POST /webapp/notes/save, GET /webapp/notes/list

Оба входа зовут одни и те же `create_note` / `update_note` / `list_notes`, поэтому
правила видимости и прав не могут разъехаться между экраном и карточкой в чате.

Правила (решение по П-9 и docs/reports/124 §3):

* писать может участник с `notes.write` — это право есть у всех действующих ролей;
* `personal` видит и правит только автор; чужая личная заметка не приходит в список
  и не открывается по номеру (404, а не 403: чужого существования не подтверждаем);
* `shared` видят все с `notes.write`; правит автор, а администратор — любую;
* менять область (`personal` ↔ `shared`) может только автор: поделиться можно своей
  заметкой, но не чужой;
* удаление в этой карте не делается: мягкое удаление (`deleted_at`) уже есть в схеме,
  но требования «удалить заметку» в FR-F7 нет.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from .. import core
from ..errors import ApiError

router = APIRouter()

SCOPES = ("personal", "shared")
SCOPE_TITLES = {"personal": "личная", "shared": "общая"}
MAX_TAGS = 10
MAX_TAG_LEN = 30
MAX_TITLE_LEN = 120
MAX_BODY_LEN = 4000
LIST_LIMIT = 100

NOTE_COLUMNS = """n.id, n.participant_id, n.title, n.body, n.tags, n.scope,
                  n.created_at, n.updated_at,
                  coalesce(p.display_name, 'участник') as author"""

NOTE_JOIN = """from note n left join participant p on p.id = n.participant_id"""


def _clean_tags(raw: list[str] | None) -> list[str]:
    """Теги в одном виде: без решётки, без пустых, без повторов, не больше десяти.

    Список закрыт по длине и количеству: тег — метка для поиска, а не второй текст
    заметки; иначе в поле можно прислать килобайт.
    """
    tags: list[str] = []
    for item in raw or []:
        tag = str(item or "").strip().lstrip("#").strip()
        if not tag:
            continue
        if len(tag) > MAX_TAG_LEN:
            raise ApiError(status.HTTP_400_BAD_REQUEST,
                           f"Тег длиннее {MAX_TAG_LEN} символов.", code="note_tag_too_long")
        if tag not in tags:
            tags.append(tag)
    if len(tags) > MAX_TAGS:
        raise ApiError(status.HTTP_400_BAD_REQUEST,
                       f"Тегов больше {MAX_TAGS}.", code="note_tags_too_many")
    return tags


def _clean_scope(scope: str | None) -> str:
    value = (scope or "personal").strip().lower()
    if value not in SCOPES:
        raise ApiError(status.HTTP_400_BAD_REQUEST,
                       "Заметка бывает личной или общей.", code="note_scope_invalid")
    return value


def _clean_title(title: str | None, body: str) -> str:
    """Заголовок: сказанный человеком, иначе первая строка текста.

    Пустой заголовок не ошибка: в чате человек пишет заметку одной фразой, и требовать
    от него отдельного заголовка — лишний шаг.
    """
    value = " ".join((title or "").split())
    if not value:
        value = " ".join((body or "").strip().splitlines()[0].split()) if body.strip() else ""
    if not value:
        raise ApiError(status.HTTP_400_BAD_REQUEST,
                       "Заметка пустая — напишите текст.", code="note_empty")
    return value[:MAX_TITLE_LEN]


def _clean_body(body: str | None) -> str:
    value = (body or "").strip()
    if not value:
        raise ApiError(status.HTTP_400_BAD_REQUEST,
                       "Заметка пустая — напишите текст.", code="note_empty")
    if len(value) > MAX_BODY_LEN:
        raise ApiError(status.HTTP_400_BAD_REQUEST,
                       f"Текст длиннее {MAX_BODY_LEN} символов.", code="note_body_too_long")
    return value


def _is_admin(caps: set[str] | list[str]) -> bool:
    current = set(caps or ())
    return "*" in current or "admin.panel" in current


def _can_edit(row: dict, participant_id: int, caps: set[str] | list[str]) -> bool:
    """Править можно своё всегда, общее — автору и администратору."""
    if int(row["participant_id"]) == int(participant_id):
        return True
    return row["scope"] == "shared" and _is_admin(caps)


def note_out(row: dict, participant_id: int, caps: set[str] | list[str]) -> dict:
    """Заметка для клиента: без чужих номеров людей и без служебных полей.

    `mine` и `editable` считает сервер: страница лишь не рисует кнопку, решение
    остаётся за эндпоинтом.
    """
    mine = int(row["participant_id"]) == int(participant_id)
    return {
        "id": row["id"],
        "title": row["title"],
        "body": row["body"],
        "tags": list(row["tags"] or []),
        "scope": row["scope"],
        "scope_title": SCOPE_TITLES.get(row["scope"], row["scope"]),
        "author": row["author"],
        "mine": mine,
        "editable": _can_edit(row, participant_id, caps),
        "created_at": row["created_at"].isoformat(timespec="seconds")
                      if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat(timespec="seconds")
                      if row.get("updated_at") else None,
    }


def _caps(conn, participant_id: int) -> set[str]:
    return set(core._caps_for(conn, participant_id))


def list_notes(conn, participant_id: int) -> list[dict]:
    """Свои личные и все общие. Чужая личная заметка сюда не попадает вовсе."""
    caps = _caps(conn, participant_id)
    rows = conn.execute(
        f"""select {NOTE_COLUMNS}
            {NOTE_JOIN}
            where n.deleted_at is null
              and (n.scope = 'shared' or n.participant_id = %s)
            order by n.updated_at desc, n.id desc
            limit %s""",
        (participant_id, LIST_LIMIT)).fetchall()
    return [note_out(row, participant_id, caps) for row in rows]


def _row_by_id(conn, note_id: int) -> dict | None:
    return conn.execute(
        f"select {NOTE_COLUMNS} {NOTE_JOIN} "
        "where n.id = %s and n.deleted_at is null", (note_id,)).fetchone()


def create_note(conn, who: dict, *, title: str | None, body: str | None,
                tags: list[str] | None, scope: str | None) -> dict:
    """Создать заметку: одна проверка входа, одна запись, один аудит."""
    participant_id = int(who["id"])
    text = _clean_body(body)
    clean_title = _clean_title(title, text)
    clean_tags = _clean_tags(tags)
    clean_scope = _clean_scope(scope)
    row = conn.execute(
        """insert into note (participant_id, title, body, tags, scope)
            values (%s, %s, %s, %s, %s)
            returning id""",
        (participant_id, clean_title, text, clean_tags, clean_scope)).fetchone()
    core.audit(conn, "human", participant_id, "note.create", "note", str(row["id"]),
               after={"title": clean_title, "scope": clean_scope, "tags": clean_tags})
    saved = _row_by_id(conn, row["id"])
    if saved is None:      # строка только что вставлена: None здесь — сломанная транзакция
        raise ApiError(status.HTTP_500_INTERNAL_SERVER_ERROR,
                       "Заметка не сохранилась.", code="note_not_saved")
    return note_out(saved, participant_id, _caps(conn, participant_id))


def update_note(conn, who: dict, note_id: int, *, title: str | None, body: str | None,
                tags: list[str] | None, scope: str | None) -> dict:
    """Правка заметки. Чужая личная — 404: её существование не подтверждаем."""
    participant_id = int(who["id"])
    caps = _caps(conn, participant_id)
    row = _row_by_id(conn, note_id)
    if row is None or not _can_edit(row, participant_id, caps):
        raise ApiError(status.HTTP_404_NOT_FOUND, "Заметка не найдена.", code="note_not_found")

    text = _clean_body(body) if body is not None else row["body"]
    clean_title = _clean_title(title, text)
    clean_tags = _clean_tags(tags) if tags is not None else list(row["tags"] or [])
    # Область меняет только автор: администратор правит чужую общую, но не превращает
    # её в личную и не открывает чужую личную всем.
    if scope is not None and row["participant_id"] == participant_id:
        clean_scope = _clean_scope(scope)
    else:
        clean_scope = row["scope"]
    conn.execute(
        """update note set title = %s, body = %s, tags = %s, scope = %s, updated_at = now()
           where id = %s""",
        (clean_title, text, clean_tags, clean_scope, note_id))
    core.audit(conn, "human", participant_id, "note.update", "note", str(note_id),
               before={"title": row["title"], "scope": row["scope"]},
               after={"title": clean_title, "scope": clean_scope, "tags": clean_tags})
    saved = _row_by_id(conn, note_id)
    if saved is None:
        raise ApiError(status.HTTP_500_INTERNAL_SERVER_ERROR,
                       "Заметка не сохранилась.", code="note_not_saved")
    return note_out(saved, participant_id, caps)


class NoteIn(core.ApiIn):
    """Общая форма создания и правки: правка приходит с `id`.

    Одно тело на оба входа (чат и Mini App) — так «тем же путём» проверяется кодом,
    а не обещанием.
    """

    id: int | None = None
    title: str = ""
    body: str = ""
    tags: list[str] = []
    scope: str = "personal"


@router.get("/tools/notes")
def notes_list(who: dict = Depends(core.require("notes.write")),
               limit: int = Query(default=LIST_LIMIT, ge=1, le=200)) -> dict:
    """Список: свои личные и все общие, свежие сверху."""
    with core.pool.connection() as conn:
        rows = list_notes(conn, int(who["id"]))
    return {"ok": True, "count": len(rows), "notes": rows[:limit]}


@router.post("/tools/notes")
def notes_create(body: NoteIn, who: dict = Depends(core.require("notes.write"))) -> dict:
    """Создать заметку из чата или с экрана."""
    with core.pool.connection() as conn:
        note = create_note(conn, who, title=body.title, body=body.body,
                           tags=body.tags, scope=body.scope)
    return {"ok": True, "note": note}


@router.post("/tools/notes/{note_id}")
def notes_update(note_id: int, body: NoteIn,
                 who: dict = Depends(core.require("notes.write"))) -> dict:
    """Править заметку. Номер в пути, тело — то же, что у создания."""
    with core.pool.connection() as conn:
        note = update_note(conn, who, note_id, title=body.title, body=body.body,
                           tags=body.tags, scope=body.scope)
    return {"ok": True, "note": note}


@router.get("/webapp/notes/list")
def webapp_notes_list(who: dict = Depends(core.webapp_actor)) -> dict:
    """Экран «Ещё → Заметки»: тот же список, что у чата."""
    core._webapp_require(who, "notes.write")
    with core.pool.connection() as conn:
        rows = list_notes(conn, int(who["id"]))
    return {"ok": True, "count": len(rows), "notes": rows}


@router.post("/webapp/notes/save")
def webapp_notes_save(body: NoteIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Создание и правка с экрана: `id` есть — правим, нет — создаём."""
    core._webapp_require(who, "notes.write")
    with core.pool.connection() as conn:
        if body.id:
            note = update_note(conn, who, int(body.id), title=body.title, body=body.body,
                               tags=body.tags, scope=body.scope)
        else:
            note = create_note(conn, who, title=body.title, body=body.body,
                               tags=body.tags, scope=body.scope)
    return {"ok": True, "note": note}
