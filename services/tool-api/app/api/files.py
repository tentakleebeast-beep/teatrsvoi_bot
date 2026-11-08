"""Файлы, папки, теги."""

from __future__ import annotations

from typing import Literal
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import Field

from .. import cold, core, file_access, files_store, folders, tags
from ..errors import ApiError

router = APIRouter()

@router.post("/files/upload")
async def files_upload(request: Request,
                       who: dict = Depends(core.current),
                       x_file_name: str = Header(default="", alias="X-File-Name"),
                       x_file_mime: str = Header(default="", alias="X-File-Mime")) -> dict:
    """Принять файл от бота: тело запроса — байты, имя и тип — в заголовках.

    Это обёртка над тем же путём, которым грузит Mini App: байты попадают в хранилище,
    запись в БД делает `save_committed` (`files_store.upload_stream`). Второго пути
    загрузки в коде нет — иначе у одного файла было бы два поведения.

    Не multipart: не тянем python-multipart и не держим файл в памяти — `read_request`
    пишет его на диск потоком, считая размер и sha256 по дороге. Лимит 20 МБ — это
    предел Bot API на скачивание, а не наше решение (docs/development/86 §2).
    """
    if not who.get("id"):
        raise ApiError(status.HTTP_403_FORBIDDEN, "Участник не зарегистрирован.",
                       code="unknown_participant")
    name = unquote(x_file_name or "").strip()[:200] or "файл"
    mime = (x_file_mime or "").strip().lower()[:120] or "application/octet-stream"

    handle = None
    try:
        handle, size, sha256, head = await files_store.read_request(request)
        if size == 0:
            raise ApiError(status.HTTP_400_BAD_REQUEST, "Файл пустой — такой не приму.", code="file_empty")
        with core.pool.connection() as conn:
            saved = files_store.upload_stream(conn, int(who["id"]), name, mime,
                                              handle, size, sha256, head)
            core.audit(conn, "human", who["id"], "file.upload", "file_object", str(saved.id),
                  after={"name": saved.name, "kind": saved.kind, "size": saved.size_bytes,
                         "folder": saved.folder_path, "duplicate": saved.duplicate})
    except files_store.FileRejected:
        # лимит размера и типа проверяется ДО записи в БД: отказ приходит тем же контрактом
        raise
    finally:
        if handle is not None:
            handle.close()

    return {"ok": True, "id": saved.id, "name": saved.name, "kind": saved.kind,
            "size_bytes": saved.size_bytes, "folder": saved.folder_path,
            "duplicate": saved.duplicate,
            "duration_sec": saved.duration_sec,
            "warning": saved.warning,
            "quota_left_mb": round(saved.quota_left / (1024 * 1024))}



@router.get("/files")
def files_list(who: dict = Depends(core.current),
               limit: int = Query(default=20, ge=1, le=100),
               q: str = Query(default="", max_length=200)) -> dict:
    """Файлы человека: без `q` — последние, с `q` — поиск по имени, тегам и тексту."""
    with core.pool.connection() as conn:
        if q.strip():
            rows = files_store.search(conn, int(who["id"]), q, limit)
            return {"ok": True, "data": rows, "count": len(rows), "query": q.strip()}
        rows = files_store.list_files(conn, int(who["id"]), limit)
    return {"ok": True, "data": rows, "count": len(rows)}



@router.get("/files/{file_id}")
def file_meta(file_id: int, who: dict = Depends(core.current)) -> dict:
    """Метаданные своего файла. Чужой отдаёт 404 — не подтверждаем существование (FR-K8)."""
    with core.pool.connection() as conn:
        row, as_admin = files_store.file_for_viewer(conn, file_id, who)
        if row is not None and as_admin:
            core.audit(conn, "human", who["id"], "file.view", "file_object", str(file_id),
                  after={"as_admin": True})
    if row is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "Файл не найден.", code="file_not_found")
    return {"ok": True, **row, "as_admin": as_admin}



@router.delete("/files/{file_id}")
def file_delete(file_id: int, who: dict = Depends(core.current)) -> dict:
    """Удалить свой файл: мягкая метка, ответ — как у `folders/{file_id}/delete` (V-045).

    Содержимое и имя в событии аудита — метаданные записи, байтов там нет никогда.
    После удаления файл не отдаётся ни `GET /files/{id}`, ни `…/content`, не виден в
    списках и в архивах папок — все чтения фильтруют `deleted_at is null`.
    """
    with core.pool.connection() as conn:
        result = files_store.remove(conn, who, file_id)
        core.audit(conn, "human", who.get("id"), "file.delete", "file_object", str(file_id),
                   after={"name": result["name"], "folder_links": result["folders_detached"],
                          "soft": True})
    return {"ok": True, **result}


@router.get("/files/{file_id}/content")
def file_content(file_id: int, who: dict = Depends(core.current)) -> StreamingResponse:
    """Байты файла для отправки в чат. Только владельцу, и только по проверке на запросе.

    Проверка владельца не кэшируется и не выводится из ролей: `file_for` спрашивает БД
    каждый раз, иначе смена владельца или удаление файла не действовали бы сразу.
    """
    with core.pool.connection() as conn:
        row, as_admin = files_store.file_for_viewer(conn, file_id, who)
        if row is None:
            raise ApiError(status.HTTP_404_NOT_FOUND, "Файл не найден.", code="file_not_found")
        restored = False
        if row.get("cold_at"):
            # файл лежит на холодном томе: возвращаем в бакет, иначе читать нечего
            try:
                restored = cold.restore(conn, file_id)
            except FileNotFoundError as exc:
                raise ApiError(status.HTTP_410_GONE, "Файл не найден ни в хранилище, ни в архиве.", code="file_cold_missing") from exc
        conn.execute("update file_object set last_access_at = now() where id = %s", (file_id,))
        core.audit(conn, "human", who["id"], "file.download", "file_object", str(file_id),
              after={"name": row["name"], "via": "chat", "size": row["size_bytes"],
                     "as_admin": as_admin, "restored": restored})

    body, mime, size = files_store.open_stream(row["object_key"])

    def chunks():
        while True:
            chunk = body.read(64 * 1024)
            if not chunk:
                break
            yield chunk

    return StreamingResponse(
        chunks(), media_type=mime,
        headers={"Content-Length": str(size),
                 "Content-Disposition": files_store.attachment_header(row["name"]),
                 "X-File-Id": str(file_id)})



@router.post("/files/{file_id}/link")
def file_link(file_id: int, who: dict = Depends(core.current)) -> dict:
    """Ссылка на файл на 15 минут — для того, что в чат не влезает (> 50 МБ)."""
    with core.pool.connection() as conn:
        row, as_admin = files_store.file_for_viewer(conn, file_id, who)
        if row is None:
            raise ApiError(status.HTTP_404_NOT_FOUND, "Файл не найден.", code="file_not_found")
        if not row["object_key"]:
            raise ApiError(status.HTTP_404_NOT_FOUND, "Файл не долетел до хранилища — загрузите заново.",
                       code="file_not_uploaded")
        if row.get("cold_at"):
            try:
                cold.restore(conn, file_id)
            except FileNotFoundError as exc:
                raise ApiError(status.HTTP_410_GONE, "Файл не найден ни в хранилище, ни в архиве.", code="file_cold_missing") from exc
        conn.execute("update file_object set last_access_at = now() where id = %s", (file_id,))
        url = files_store.presigned_url(row["object_key"], row["name"] or "file")
        core.audit(conn, "human", who["id"], "file.link", "file_object", str(file_id),
              after={"name": row["name"], "ttl": 900, "as_admin": as_admin})
    return {"ok": True, "url": url, "expires_in": 900,
            "name": row["name"], "size_bytes": row["size_bytes"]}



class FolderIn(core.ApiIn):
    """Наследуем ApiIn: NaN, NUL и перебор длины отбиваются общим контрактом входа."""

    name: str = Field(min_length=1, max_length=200)
    parent_id: int | None = None
    scope: Literal["shared", "personal"] = "shared"



class FolderRenameIn(core.ApiIn):
    name: str = Field(min_length=1, max_length=200)



@router.get("/folders")
def folders_tree(scope: Literal["shared", "personal"] = Query(default="shared"),
                 who: dict = Depends(core.current)) -> dict:
    """Дерево общих папок — всем, личных — только владельцу."""
    with core.pool.connection() as conn:
        try:
            rows = folders.tree(conn, scope, int(who.get("id") or 0))
        except folders.FolderError:
            raise
    return {"ok": True, "data": rows, "count": len(rows)}



@router.post("/folders")
def folders_create(body: FolderIn, who: dict = Depends(core.current)) -> dict:
    """Создать папку. Общие — admin и org, личные — сам участник (FR-K10)."""
    with core.pool.connection() as conn:
        try:
            row = folders.create(conn, who, body.name, body.scope, body.parent_id)
        except folders.FolderError:
            raise
        core.audit(conn, "human", who.get("id"), "folder.create", "folder", str(row["id"]),
              after={"name": row["name"], "path": row["path"], "scope": body.scope})
    return {"ok": True, **row}



@router.post("/folders/{folder_id}/rename")
def folders_rename(folder_id: int, body: FolderRenameIn,
                   who: dict = Depends(core.current)) -> dict:
    """Переименовать папку: путь поддерева пересчитывается, дети не повисают."""
    with core.pool.connection() as conn:
        try:
            result = folders.rename(conn, who, folder_id, body.name)
        except folders.FolderError:
            raise
        core.audit(conn, "human", who.get("id"), "folder.rename", "folder", str(folder_id),
              after=result)
    return {"ok": True, **result}



@router.post("/folders/{folder_id}/delete")
def folders_delete(folder_id: int, who: dict = Depends(core.current)) -> dict:
    """Закрыть папку. Файлы не удаляются — они остаются в «Без папки» (FR-M8)."""
    with core.pool.connection() as conn:
        try:
            result = folders.remove(conn, who, folder_id)
        except folders.FolderError:
            raise
        core.audit(conn, "human", who.get("id"), "folder.delete", "folder", str(folder_id),
              after=result)
    return {"ok": True, **result}



@router.post("/files/{file_id}/access/revoke")
def file_access_revoke(file_id: int, who: dict = Depends(core.current)) -> dict:
    """Отозвать доступ: владелец — всегда, админ — если владелец попросил."""
    with core.pool.connection() as conn:
        try:
            result = file_access.revoke(conn, who, file_id)
        except file_access.AccessError:
            raise
        core.audit(conn, "human", who.get("id"), "file.access.revoke", "file_object", str(file_id),
              after=result)
    return {"ok": True, **result}



@router.get("/files/{file_id}/access")
def file_access_list(file_id: int, who: dict = Depends(core.current)) -> dict:
    """Кто и зачем открывал доступ к файлу — видит владелец и админ."""
    with core.pool.connection() as conn:
        file_access.file_for_grants(conn, who, file_id)
        grants = file_access.grants_of(conn, file_id)
    return {"ok": True, "data": grants, "count": len(grants)}



class WebAppPresignIn(core.ApiIn):
    name: str = Field(min_length=1, max_length=200)
    mime: str = Field(default="application/octet-stream", max_length=120)
    size: int = Field(default=0, ge=0)



class WebAppCommitIn(core.ApiIn):
    object_key: str = Field(min_length=3, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    mime: str = Field(default="application/octet-stream", max_length=120)
    sha256: str | None = Field(default=None, max_length=64)



@router.get("/webapp/files/list")
def webapp_files_list(who: dict = Depends(core.webapp_actor)) -> dict:
    core._webapp_require(who, "files.access")  # V-039 (в): файлы — admin, org, master
    participant_id = int(who["id"])
    with core.pool.connection() as conn:
        rows = files_store.list_files(conn, participant_id, 100)
        used = files_store.used_bytes(conn, participant_id)
    return {"ok": True, "data": rows,
            "quota": {"used": used, "total": files_store.QUOTA_BYTES,
                      "left": max(files_store.QUOTA_BYTES - used, 0)}}



@router.post("/webapp/files/presign")
def webapp_files_presign(body: WebAppPresignIn,
                         who: dict = Depends(core.webapp_actor)) -> dict:
    """Ссылка на прямую загрузку в хранилище: браузер кладёт файл сам, минуя сервер.

    Тип проверяем сразу, до загрузки мегабайтов: если это видео или архив, человек узнает
    об этом мгновенно, а не после того, как телефон потратит трафик.
    """
    core._webapp_require(who, "files.access")  # V-039 (в): файлы — admin, org, master
    participant_id = int(who["id"])
    try:
        files_store.detect_kind(body.name, body.mime, b"")
    except files_store.FileRejected:
        raise

    if body.size and body.size > files_store.MAX_MINIAPP_BYTES:
        limit_mb = files_store.MAX_MINIAPP_BYTES // (1024 * 1024)
        raise ApiError(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                       f"Файл больше {limit_mb} МБ — такой не приму.", code="file_too_large")
    with core.pool.connection() as conn:
        left = files_store.QUOTA_BYTES - files_store.used_bytes(conn, participant_id)
    if left <= 0:
        raise ApiError(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Личное место закончилось: удалите что-нибудь.", code="file_quota_exceeded")
    try:
        data = files_store.presign_put(participant_id, body.name, body.mime)
    except files_store.FileRejected:
        raise
    return {"ok": True, **data, "quota_left_mb": round(left / (1024 * 1024))}



@router.post("/webapp/files/commit")
def webapp_files_commit(body: WebAppCommitIn,
                        who: dict = Depends(core.webapp_actor)) -> dict:
    """Подтвердить загрузку: проверить, что объект на месте, и записать файл в БД.

    Ключ обязан принадлежать человеку: иначе presigned-ссылка станет способом перезаписать
    чужой файл, подставив чужой ключ.
    """
    core._webapp_require(who, "files.access")  # V-039 (в): файлы — admin, org, master
    participant_id = int(who["id"])
    prefix = f"p{participant_id}/"
    if not body.object_key.startswith(prefix) or "/" in body.object_key[len(prefix):]:
        raise ApiError(status.HTTP_403_FORBIDDEN, "Некорректный ключ загрузки.", code="file_key_foreign")

    head = files_store.head_of(body.object_key)
    if head is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "Файл не долетел до хранилища — попробуйте ещё раз.", code="file_not_uploaded")
    size, content_type = head
    if size <= 0:
        files_store.delete_object(body.object_key)
        raise ApiError(status.HTTP_400_BAD_REQUEST, "Файл пустой.", code="file_empty")
    if size > files_store.MAX_MINIAPP_BYTES:
        files_store.delete_object(body.object_key)
        raise ApiError(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Файл больше допустимого — не принял.", code="file_too_large")

    head16 = files_store.head_bytes(body.object_key)
    with core.pool.connection() as conn:
        try:
            saved = files_store.save_committed(
                conn, participant_id, body.name, body.mime or content_type,
                size, body.sha256, body.object_key, head16)
        except files_store.FileRejected:
            raise
        core.audit(conn, "human", participant_id, "file.upload", "file_object", str(saved.id),
              after={"name": saved.name, "kind": saved.kind, "size": saved.size_bytes,
                     "via": "webapp", "duplicate": saved.duplicate})
    return {"ok": True, "file": {
        "id": saved.id, "name": saved.name, "kind": saved.kind,
        "size_bytes": saved.size_bytes, "folder": saved.folder_path,
        "duration_sec": saved.duration_sec, "duplicate": saved.duplicate,
        "quota_left_mb": round(saved.quota_left / (1024 * 1024))}}



@router.delete("/webapp/files/{file_id}")
def webapp_file_delete(file_id: int, who: dict = Depends(core.webapp_actor)) -> dict:
    """Удалить свой файл с экрана: тот же `files_store.remove`, что у `DELETE /files/{id}`.

    Право входа — `files.access`; кого именно пускает удаление, решает домен: только
    владелец, чужой личный файл отвечает 404. Кнопка «Удалить» на экране показывается
    всегда, когда список файлов открыт: он и так содержит лишь свои файлы.
    """
    viewer = core._webapp_require(who, "files.access")
    with core.pool.connection() as conn:
        result = files_store.remove(conn, viewer, file_id)
        core.audit(conn, "human", viewer.get("id"), "file.delete", "file_object", str(file_id),
                   after={"name": result["name"], "folder_links": result["folders_detached"],
                          "soft": True, "via": "webapp"})
    return {"ok": True, **result}


class FolderOrderIn(core.ApiIn):
    file_ids: list[int] = Field(default_factory=list, max_length=500)



class FileToFolderIn(core.ApiIn):
    folder_id: int | None = None



def _folder_readable(folder: dict, who: dict) -> bool:
    """Общую папку читают все роли, личную — только владелец (FR-K10)."""
    if folder["scope"] == "shared":
        return True
    return int(folder["participant_id"] or 0) == int(who.get("id") or 0)



def _folder_or_404(conn, folder_id: int) -> dict:
    try:
        return folders.get(conn, folder_id)
    except folders.FolderError:
        raise



@router.get("/folders/{folder_id}/files")
def folder_files(folder_id: int, who: dict = Depends(core.current)) -> dict:
    """Содержимое папки в порядке воспроизведения: `position`, затем числовой префикс."""
    with core.pool.connection() as conn:
        folder = _folder_or_404(conn, folder_id)
        if not _folder_readable(folder, who):
            raise ApiError(status.HTTP_404_NOT_FOUND, "Папка не найдена.", code="folder_not_found")
        items = files_store.folder_files(conn, folder_id)
    for item in items:
        item.pop("object_key", None)          # ключ в хранилище наружу не отдаём
    return {"ok": True, "folder": {key: folder[key] for key in
                                  ("id", "name", "path", "scope", "project_id")},
            "data": items, "count": len(items)}



@router.post("/folders/{folder_id}/order")
def folder_order(folder_id: int, body: FolderOrderIn,
                 who: dict = Depends(core.current)) -> dict:
    """Порядок треков перетаскиванием. Меняет только папку — файлы не трогает."""
    with core.pool.connection() as conn:
        result = folders.set_order(conn, who, folder_id, body.file_ids)
        core.audit(conn, "human", who.get("id"), "folder.order", "folder", str(folder_id),
              after={"file_ids": body.file_ids[:50], "updated": result["updated"]})
    return {"ok": True, "updated": result["updated"]}



@router.post("/files/{file_id}/folder")
def file_to_folder(file_id: int, body: FileToFolderIn,
                   who: dict = Depends(core.current)) -> dict:
    """Положить свой файл в папку (или вынуть из всех, если folder_id не задан)."""
    with core.pool.connection() as conn:
        row, _as_admin = files_store.file_for_viewer(conn, file_id, who)
        if row is None or int(row["participant_id"] or 0) != int(who.get("id") or 0):
            raise ApiError(status.HTTP_404_NOT_FOUND, "Файл не найден.", code="file_not_found")
        if body.folder_id is None:
            conn.execute("delete from file_folder where file_id = %s", (file_id,))
            return {"ok": True, "folder_id": None}
        folder = _folder_or_404(conn, body.folder_id)
        if not _folder_readable(folder, who) or not folders.can_manage(who, folder):
            raise ApiError(status.HTTP_403_FORBIDDEN, "В эту папку класть нельзя.", code="folder_forbidden")
        conn.execute("delete from file_folder where file_id = %s", (file_id,))
        files_store.add_to_folder(conn, file_id, int(body.folder_id))
        core.audit(conn, "human", who.get("id"), "file.move", "file_object", str(file_id),
              after={"folder_id": body.folder_id, "path": folder["path"]})
    return {"ok": True, "folder_id": body.folder_id, "path": folder["path"]}



@router.get("/folders/{folder_id}/zip")
def folder_zip(folder_id: int, who: dict = Depends(core.current)):
    """Архив папки целиком: порядок внутри — как на экране (FR-M6)."""
    with core.pool.connection() as conn:
        folder = _folder_or_404(conn, folder_id)
        if not _folder_readable(folder, who):
            raise ApiError(status.HTTP_404_NOT_FOUND, "Папка не найдена.", code="folder_not_found")
        handle, skipped = files_store.stream_folder_zip(conn, folder,
                                                        max_bytes=core.ZIP_MAX_BYTES)
        core.audit(conn, "human", who.get("id"), "folder.zip", "folder", str(folder_id),
              after={"path": folder["path"], "skipped": skipped[:20]})
    size = files_store.stream_zip_size(handle)
    name = files_store.folder_zip_name(folder)

    def chunks():
        try:
            while True:
                chunk = handle.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            handle.close()

    return StreamingResponse(
        chunks(), media_type="application/zip",
        headers={"Content-Length": str(size),
                 "Content-Disposition": files_store.attachment_header(name)})



def _webapp_folder(conn, folder_id: int, who: dict) -> dict:
    folder = _folder_or_404(conn, folder_id)
    if not _folder_readable(folder, who):
        raise ApiError(status.HTTP_404_NOT_FOUND, "Папка не найдена.", code="folder_not_found")
    return folder



@router.get("/webapp/folder/{folder_id}/files")
def webapp_folder_files(folder_id: int, who: dict = Depends(core.webapp_actor)) -> dict:
    """Треки папки с готовыми ссылками на прослушивание (по 15 минут каждая)."""
    core._webapp_require(who, "files.access")  # V-039 (в): файлы — admin, org, master
    with core.pool.connection() as conn:
        folder = _webapp_folder(conn, folder_id, who)
        items = files_store.folder_files(conn, folder_id)
    for item in items:
        key = item.pop("object_key", None)
        item["url"] = files_store.presigned_url(key, item["name"] or "track") if key else ""
    return {"ok": True,
            "folder": {"id": folder["id"], "path": folder["path"], "scope": folder["scope"]},
            "data": items, "count": len(items)}



@router.post("/webapp/folder/{folder_id}/order")
def webapp_folder_order(folder_id: int, body: FolderOrderIn,
                        who: dict = Depends(core.webapp_actor)) -> dict:
    """Порядок задаёт владелец папки; доска объявлений для всех остальных — только чтение."""
    viewer = core._webapp_require(who, "files.access")
    with core.pool.connection() as conn:
        result = folders.set_order(conn, viewer, folder_id, body.file_ids)
        core.audit(conn, "human", viewer.get("id"), "folder.order", "folder", str(folder_id),
              after={"updated": result["updated"], "via": "webapp"})
    return {"ok": True, "updated": result["updated"]}



@router.get("/webapp/folder/{folder_id}/zip")
def webapp_folder_zip(folder_id: int, who: dict = Depends(core.webapp_actor)):
    """Архив папки для скачивания из Mini App."""
    core._webapp_require(who, "files.access")  # V-039 (в): файлы — admin, org, master
    with core.pool.connection() as conn:
        folder = _webapp_folder(conn, folder_id, who)
        handle, skipped = files_store.stream_folder_zip(conn, folder,
                                                        max_bytes=core.ZIP_MAX_BYTES)
        core.audit(conn, "human", who.get("id"), "folder.zip", "folder", str(folder_id),
              after={"path": folder["path"], "via": "webapp", "skipped": skipped[:20]})
    size = files_store.stream_zip_size(handle)
    name = files_store.folder_zip_name(folder)

    def chunks():
        try:
            while True:
                chunk = handle.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            handle.close()

    return StreamingResponse(
        chunks(), media_type="application/zip",
        headers={"Content-Length": str(size),
                 "Content-Disposition": files_store.attachment_header(name)})



class TagIn(core.ApiIn):
    title: str = Field(min_length=1, max_length=60)



class FileTagsIn(core.ApiIn):
    tag_ids: list[int] = Field(default_factory=list, max_length=20)



@router.get("/tags")
def tags_list(who: dict = Depends(core.current)) -> dict:
    """Словарь тегов: утверждённые — всем, свои черновики — автору и админу."""
    with core.pool.connection() as conn:
        rows = tags.list_tags(conn, who)
    return {"ok": True, "data": rows, "count": len(rows)}



@router.post("/tags")
def tags_create(body: TagIn, who: dict = Depends(core.current)) -> dict:
    """Новый тег: у админа сразу утверждён, у остальных — черновик на утверждение."""
    with core.pool.connection() as conn:
        try:
            row = tags.create_tag(conn, who, body.title)
        except tags.TagError:
            raise
        if row.get("created"):
            core.audit(conn, "human", who.get("id"), "tag.create", "tag", str(row["id"]),
                  after={"title": row["title"], "status": row["status"]})
    return {"ok": True, **row}



@router.post("/tags/{tag_id}/approve")
def tags_approve(tag_id: int, who: dict = Depends(core.current)) -> dict:
    """Утвердить тег: только админ, кнопкой или командой в боте."""
    with core.pool.connection() as conn:
        try:
            row = tags.approve_tag(conn, who, tag_id)
        except tags.TagError:
            raise
        core.audit(conn, "human", who.get("id"), "tag.approve", "tag", str(tag_id),
              after={"title": row["title"]})
    return {"ok": True, **row}



@router.get("/files/{file_id}/tags")
def file_tags(file_id: int, who: dict = Depends(core.current)) -> dict:
    with core.pool.connection() as conn:
        row, _as_admin = files_store.file_for_viewer(conn, file_id, who)
        if row is None:
            raise ApiError(status.HTTP_404_NOT_FOUND, "Файл не найден.", code="file_not_found")
        rows = tags.tags_of(conn, file_id)
    return {"ok": True, "data": rows, "count": len(rows)}



@router.post("/files/{file_id}/tags")
def file_tags_set(file_id: int, body: FileTagsIn, who: dict = Depends(core.current)) -> dict:
    """Полная замена тегов файла. Чужой черновик прицепить нельзя."""
    with core.pool.connection() as conn:
        row, _as_admin = files_store.file_for_viewer(conn, file_id, who)
        if row is None or int(row["participant_id"] or 0) != int(who.get("id") or 0):
            raise ApiError(status.HTTP_404_NOT_FOUND, "Файл не найден.", code="file_not_found")
        try:
            rows = tags.set_file_tags(conn, who, file_id, body.tag_ids)
        except tags.TagError:
            raise
        core.audit(conn, "human", who.get("id"), "file.tags", "file_object", str(file_id),
              after={"tag_ids": body.tag_ids})
    return {"ok": True, "data": rows, "count": len(rows)}


# --- экран «Файлы»: папки, доступ, теги (Ш-08) -------------------------------
#
# Ф2, Ф3 и Ф4 жили только в чате: у доступа и тега экран не имел входа, а rename,
# delete и order вызывал лишь тест — то есть маршруты были сиротами. Здесь у каждого
# действия появляется вход на экране, и он идёт в тот же обработчик, что и чат:
# `file_access`, `tags` и `folders`. Право на вход — `files.access` (V-039 (в)): экран
# открыт admin, org и master, а кого именно пускает действие, решает сам домен
# (`folders` — admin и org, `tags` и `file_access` — только admin) и отвечает словами.

class WebAppAccessIn(core.ApiIn):
    """Причина обязательна: доступ без причины — это «админ видит всё» с лишним шагом."""

    reason: str = Field(min_length=3, max_length=500)
    ttl_hours: int = Field(default=file_access.DEFAULT_TTL_HOURS, ge=1, le=file_access.MAX_TTL_HOURS)


@router.get("/webapp/folders")
def webapp_folders_tree(scope: Literal["shared", "personal"] = Query(default="shared"),
                        who: dict = Depends(core.webapp_actor)) -> dict:
    """Дерево папок на экране — тот же `folders.tree`, что отдаёт чат."""
    viewer = core._webapp_require(who, "files.access")
    with core.pool.connection() as conn:
        rows = folders.tree(conn, scope, int(viewer.get("id") or 0))
    return {"ok": True, "data": rows, "count": len(rows)}


@router.post("/webapp/folders")
def webapp_folders_create(body: FolderIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Создать папку с экрана: обработчик тот же, что у `/folders` в чате."""
    viewer = core._webapp_require(who, "files.access")
    with core.pool.connection() as conn:
        row = folders.create(conn, viewer, body.name, body.scope, body.parent_id)
        core.audit(conn, "human", viewer.get("id"), "folder.create", "folder", str(row["id"]),
              after={"name": row["name"], "path": row["path"], "scope": body.scope,
                     "via": "webapp"})
    return {"ok": True, **row}


@router.post("/webapp/folders/{folder_id}/rename")
def webapp_folders_rename(folder_id: int, body: FolderRenameIn,
                          who: dict = Depends(core.webapp_actor)) -> dict:
    """Переименовать папку с экрана; поддерево пересчитывается тем же кодом, что в чате."""
    viewer = core._webapp_require(who, "files.access")
    with core.pool.connection() as conn:
        result = folders.rename(conn, viewer, folder_id, body.name)
        core.audit(conn, "human", viewer.get("id"), "folder.rename", "folder", str(folder_id),
              after={**result, "via": "webapp"})
    return {"ok": True, **result}


@router.post("/webapp/folders/{folder_id}/delete")
def webapp_folders_delete(folder_id: int, who: dict = Depends(core.webapp_actor)) -> dict:
    """Закрыть папку с экрана. Файлы остаются в «Без папки» (FR-M8)."""
    viewer = core._webapp_require(who, "files.access")
    with core.pool.connection() as conn:
        result = folders.remove(conn, viewer, folder_id)
        core.audit(conn, "human", viewer.get("id"), "folder.delete", "folder", str(folder_id),
              after={**result, "via": "webapp"})
    return {"ok": True, **result}


@router.post("/webapp/files/{file_id}/access")
def webapp_file_access_grant(file_id: int, body: WebAppAccessIn,
                             who: dict = Depends(core.webapp_actor)) -> dict:
    """Открыть доступ к личному файлу с экрана — тот же `file_access.request`, что у `/access`."""
    viewer = core._webapp_require(who, "files.access")
    with core.pool.connection() as conn:
        result = file_access.request(conn, viewer, file_id, body.reason, body.ttl_hours)
        core.audit(conn, "human", viewer.get("id"), "file.access.grant", "file_object",
              str(file_id),
              after={"reason": body.reason, "ttl_hours": result["ttl_hours"],
                     "owner_id": result["owner_id"], "via": "webapp"})
    return {"ok": True, **result}


@router.post("/webapp/files/{file_id}/access/revoke")
def webapp_file_access_revoke(file_id: int, who: dict = Depends(core.webapp_actor)) -> dict:
    """Отозвать доступ с экрана — тот же `file_access.revoke`, что у `/revoke` в чате."""
    viewer = core._webapp_require(who, "files.access")
    with core.pool.connection() as conn:
        result = file_access.revoke(conn, viewer, file_id)
        core.audit(conn, "human", viewer.get("id"), "file.access.revoke", "file_object",
              str(file_id), after={**result, "via": "webapp"})
    return {"ok": True, **result}


@router.get("/webapp/files/{file_id}/access")
def webapp_file_access_list(file_id: int, who: dict = Depends(core.webapp_actor)) -> dict:
    """Кто открывал доступ к файлу — на экране, по тем же правилам, что в чате.

    История доступов — не «данные раздела»: её читает владелец и админ, а не всякий,
    кому открыт раздел. Проверка та же, что у `GET /files/{id}/access` — общий
    `file_access.file_for_grants`; иначе org и master перебором `file_id` читали бы
    причину и срок доступа к чужим личным файлам (OWASP API1:2023).
    """
    viewer = core._webapp_require(who, "files.access")
    with core.pool.connection() as conn:
        file_access.file_for_grants(conn, viewer, file_id)
        grants = file_access.grants_of(conn, file_id)
    return {"ok": True, "data": grants, "count": len(grants)}


@router.get("/webapp/tags/pending")
def webapp_tags_pending(who: dict = Depends(core.webapp_actor)) -> dict:
    """Черновики тегов, ждущие утверждения: словарь экрана — тот же, что в чате."""
    viewer = core._webapp_require(who, "files.access")
    with core.pool.connection() as conn:
        rows = [row for row in tags.list_tags(conn, viewer) if row["status"] != "approved"]
    return {"ok": True, "data": rows, "count": len(rows)}


@router.post("/webapp/tags/{tag_id}/approve")
def webapp_tags_approve(tag_id: int, who: dict = Depends(core.webapp_actor)) -> dict:
    """Утвердить тег с экрана. Право то же, что у `/тег-ок`: только администратор."""
    viewer = core._webapp_require(who, "files.access")
    with core.pool.connection() as conn:
        row = tags.approve_tag(conn, viewer, tag_id)
        core.audit(conn, "human", viewer.get("id"), "tag.approve", "tag", str(tag_id),
              after={"title": row["title"], "via": "webapp"})
    return {"ok": True, **row}

