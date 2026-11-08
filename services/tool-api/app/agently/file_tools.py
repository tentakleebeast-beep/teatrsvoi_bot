"""Файловые инструменты агента: список, чтение с цитатой и черновик перемещения.

Переехали из `app/agent.py` в общий реестр `agently` (карта «Озадачить»,
docs/development/108 §4): у агента один цикл, один лимит шагов, один учёт токенов
и одна трассировка. Отдельного «агента по файлам» со своим циклом больше нет —
`app/agent.py` остался тонкой оболочкой для маршрута `/tools/agent/files`.

Границы держит код:
  * права проверяет `files_store.file_for_viewer` — свои файлы и общие, чужие
    личные не видны (FR-K10);
  * чтение отдаёт текст с именем файла: модель обязана цитировать, иначе ответ
    получает пометку (`agently/citations.py`);
  * перемещение — только черновик в `agent_action`; применяет человек кнопкой
    (FR-L2), удаления нет ни в одном инструменте (FR-L8).

Почему запись `agent_action` живёт здесь, а не в `tools.py`: `tools.py` — только
чтение данных студии, и его разбирает тест на записывающие SQL-литералы. Черновик
действия — это не запись предметных данных, а очередь на подтверждение, поэтому
он отделён от читающих обработчиков.
"""

from __future__ import annotations

import json

from .. import files_store, folders

#: Сколько символов текста отдаём модели. Больше — не влезет в бюджет токенов.
MAX_READ_CHARS = 4000
#: Сколько файлов показываем в одном ответе.
MAX_LIST = 50


def folder_by_path(conn, path: str, who: dict) -> dict | None:
    """Папка по пути: общая или своя личная. Чужая личная не находится (FR-K10)."""
    clean = "/".join(part.strip() for part in (path or "").split("/") if part.strip())
    if not clean:
        return None
    shared = conn.execute(
        "select id, scope, participant_id, path from folder"
        " where scope = 'shared' and path = %s and deleted_at is null", (clean,)).fetchone()
    if shared:
        return shared
    return conn.execute(
        "select id, scope, participant_id, path from folder"
        " where scope = 'personal' and participant_id = %s and path = %s and deleted_at is null",
        (int(who.get("id") or 0), clean)).fetchone()


def run_list_files(conn, who: dict, args: dict) -> dict:
    """Список своих файлов или поиск по слову в имени, тегах и тексте."""
    query = str(args.get("query") or "").strip()
    path = str(args.get("folder_path") or "").strip()
    if path:
        folder = folder_by_path(conn, path, who)
        if folder is None:
            return {"ok": False, "error": "folder_not_found",
                    "message": f"папки «{path}» нет. Посмотрите список без папки."}
        items = files_store.folder_files(conn, int(folder["id"]))
        return {"ok": True, "folder": folder["path"], "files": [
            {"id": item["id"], "name": item["name"], "size_bytes": item["size_bytes"],
             "duration_sec": item.get("duration_sec")} for item in items[:MAX_LIST]]}
    if query:
        rows = files_store.search(conn, int(who.get("id") or 0), query, MAX_LIST)
        return {"ok": True, "query": query, "files": [
            {"id": row["id"], "name": row["name"], "size_bytes": row["size_bytes"],
             "kind": row.get("kind"), "folder": row.get("folder")} for row in rows]}
    rows = files_store.list_files(conn, int(who.get("id") or 0), MAX_LIST)
    return {"ok": True, "files": [{"id": row["id"], "name": row["name"],
                                   "size_bytes": row["size_bytes"],
                                   "kind": row.get("kind"),
                                   "folder": row.get("folder")} for row in rows]}


def run_read_file(conn, who: dict, args: dict) -> dict:
    """Текст файла с именем. Нет текста — честный статус, а не выдуманное содержание."""
    try:
        file_id = int(args.get("file_id") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "needs_input", "kind": "files",
                "message": "нужен номер файла: сначала list_files"}
    if not file_id:
        return {"ok": False, "error": "needs_input", "kind": "files",
                "message": "нужен номер файла: сначала list_files"}
    row, as_admin = files_store.file_for_viewer(conn, file_id, who)
    if row is None:
        return {"ok": False, "error": "not_found",
                "message": "файла нет или он не ваш; список доступных — list_files"}
    text_row = conn.execute("select body, status from file_text where file_id = %s",
                            (file_id,)).fetchone()
    body = (text_row["body"] if text_row else "") or ""
    status = (text_row["status"] if text_row else "text_unavailable")
    if not body.strip():
        return {"ok": True, "id": file_id, "name": row["name"], "kind": row["kind"],
                "status": status, "as_admin": as_admin, "text": "",
                "quote": None, "truncated": False,
                "message": "в файле нет текста: цитировать нечего"}
    return {"ok": True, "id": file_id, "name": row["name"], "kind": row["kind"],
            "status": status, "as_admin": as_admin,
            "text": body[:MAX_READ_CHARS],
            "quote": {"file": row["name"], "excerpt": body[:400]},
            "truncated": len(body) > MAX_READ_CHARS}


def run_propose_move(conn, who: dict, args: dict) -> dict:
    """Предложить переложить файл в папку. Применяет человек, не агент."""
    try:
        file_id = int(args.get("file_id") or 0)
    except (TypeError, ValueError):
        file_id = 0
    path = str(args.get("folder_path") or "").strip()
    row, _as_admin = files_store.file_for_viewer(conn, file_id, who)
    if row is None or int(row["participant_id"] or 0) != int(who.get("id") or 0):
        return {"ok": False, "error": "not_found",
                "message": "файл не ваш — перемещать можно только свои"}
    folder = folder_by_path(conn, path, who)
    if folder is None:
        return {"ok": False, "error": "folder_not_found",
                "message": f"папки «{path}» нет — предложите существующую"}
    if not folders.can_manage(who, folder):
        return {"ok": False, "error": "forbidden", "message": "в эту папку класть нельзя"}
    payload = {"file_id": file_id, "file_name": row["name"],
               "folder_id": int(folder["id"]), "folder_path": folder["path"]}
    action = conn.execute(
        "insert into agent_action (participant_id, file_id, kind, payload, status)"
        " values (%s,%s,'move',%s,'proposed') returning id",
        (int(who.get("id") or 0), file_id,
         json.dumps(payload, ensure_ascii=False))).fetchone()
    return {"ok": True, "action_id": int(action["id"]), "proposed": True,
            "folder_path": folder["path"],
            "human_text": f"Переложить «{row['name']}» в «{folder['path']}».",
            "note": "перемещение применит человек кнопкой"}
