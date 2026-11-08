"""Агент по файлам — оболочка общего цикла `agently.loop` (карта «Озадачить»).

Раньше здесь жил второй цикл «модель → инструмент → модель» со своим лимитом шагов,
своим учётом токенов и своим набором инструментов. Два цикла означали две трассировки
и два места, где можно забыть права. Теперь файловые инструменты (`list_files`,
`read_file`, `propose_move`) входят в общий реестр `agently`, и здесь остаётся:

  * тонкая обёртка `run` для маршрута `/tools/agent/files` (входа из чата у него нет:
    свободный текст идёт в `/tools/agent/ask`, где те же инструменты);
  * применение и отклонение предложенного перемещения — только по кнопке человека
    (FR-L2): это действие, а не чтение, и живёт оно рядом с маршрутом.

Правила прежние и держатся кодом: удаления файлов нет ни в одном инструменте (FR-L8),
ответ по документу — только с цитатой (`agently/citations.py`), лимит шагов — 5.
"""

from __future__ import annotations

from typing import Any

from . import files_store, folders
from .agently import config as agent_config
from .agently import loop as agent_loop
from .agently.citations import verify_citations

__all__ = ["MAX_STEPS", "ModelUnavailable", "apply_action", "reject_action", "run",
           "tools_model", "verify_citations"]

MAX_STEPS = 5

#: Дополнение к общему промпту: в файловом маршруте нет данных студии, только файлы.
FILE_HINT = "\n".join([
    "Сейчас вопрос про файлы человека. Работай инструментами list_files и read_file.",
    "Не выдумывай содержимое: цитируй дословно фрагмент и имя файла,",
    "а если нужного в файле нет — так и скажи.",
])


def tools_model() -> str:
    """Модель для вызовов инструментов: та же, что у общего цикла агента."""
    return agent_config.agent_model()


#: Отказ провайдера: вызывающий отличает его от поломки кода (503, не 500).
ModelUnavailable = agent_loop.ModelUnavailable


async def run(conn, who: dict, question: str, *, max_steps: int = MAX_STEPS,
              transport=None) -> dict:
    """Вопрос по файлам через общий цикл. Форма ответа прежняя: answer/steps/tokens."""
    result = await agent_loop.run(conn, who, question, transport=transport,
                                  max_steps=max_steps, features_on=False,
                                  prompt_extra=FILE_HINT)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error") or "step_limit",
                "message": result.get("message") or
                "Не разобрался за отведённые шаги. Уточните вопрос или пришлите файл.",
                "steps": result.get("steps") or [], "tokens": result.get("tokens") or 0,
                "model": tools_model()}
    return {"ok": True, "answer": result.get("answer") or "",
            "verified": bool(result.get("verified", True)),
            "steps": result.get("steps") or [], "tokens": result.get("tokens") or 0,
            "model": tools_model()}


def apply_action(conn, who: dict, action_id: int) -> dict:
    """Применить предложенное перемещение — только по кнопке человека (FR-L2)."""
    row = conn.execute(
        "select id, participant_id, file_id, payload, status from agent_action where id = %s",
        (action_id,)).fetchone()
    if row is None:
        return {"ok": False, "error": "not_found", "message": "такого предложения нет"}
    if int(row["participant_id"]) != int(who.get("id") or 0):
        return {"ok": False, "error": "forbidden", "message": "это предложение не для вас"}
    if row["status"] != "proposed":
        return {"ok": False, "error": "already_resolved", "message": "уже разобрано"}
    payload: Any = row["payload"] or {}
    file_id = int(row["file_id"] or payload.get("file_id") or 0)
    folder_id = int(payload.get("folder_id") or 0)
    folder = folders.get(conn, folder_id) if folder_id else None
    if folder is None or not folders.can_manage(who, folder):
        return {"ok": False, "error": "forbidden", "message": "папка недоступна"}
    conn.execute("delete from file_folder where file_id = %s", (file_id,))
    files_store.add_to_folder(conn, file_id, folder_id)
    conn.execute("update agent_action set status = 'applied', resolved_at = now() where id = %s",
                 (action_id,))
    return {"ok": True, "file_id": file_id, "folder_path": folder["path"]}


def reject_action(conn, who: dict, action_id: int) -> dict:
    row = conn.execute("select participant_id, status from agent_action where id = %s",
                       (action_id,)).fetchone()
    if row is None or int(row["participant_id"]) != int(who.get("id") or 0):
        return {"ok": False, "error": "not_found", "message": "такого предложения нет"}
    conn.execute("update agent_action set status = 'rejected', resolved_at = now()"
                 " where id = %s and status = 'proposed'", (action_id,))
    return {"ok": True}
