"""Роутер агента по файлам: подтверждение его предложений.

Команды `/agent` больше нет (Ш-12, решение П-1): переключателя режима не существует,
свободный текст всегда идёт в агент tool-api (`POST /tools/agent/ask`), у которого
есть инструменты `list_files` и `read_file`. Вопрос по файлам задаётся обычной фразой, а здесь
остаётся только то, что требует нажатия: «Переложить» / «Не надо» в карточке.
"""

from __future__ import annotations

import core
import texts
from aiogram import F, Router
from aiogram.types import CallbackQuery

router = Router()


# --- предложения агента (docs/development/87, FR-L2) -------------------------


@router.callback_query(F.data.startswith("ag:"))
async def agent_action_cb(cb: CallbackQuery) -> None:
    """Применить или отклонить предложение агента — по кнопке, не молча."""
    if cb.message is None:
        await cb.answer(texts.MESSAGE_UNAVAILABLE, show_alert=True)
        return
    chat_id = cb.message.chat.id
    parts = (cb.data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await cb.answer("Не понял действие", show_alert=True)
        return
    action_id, verb = int(parts[2]), parts[1]
    await cb.answer()
    result = await core.api(f"/agent/actions/{action_id}/{verb}", {}, chat_id)
    if not result.get("ok"):
        await cb.message.answer(str(result.get("message") or "Не получилось.")[:500])
        return
    if verb == "apply":
        await cb.message.answer(f"Переложил в «{result.get('folder_path')}».")
    else:
        await cb.message.answer("Оставил файл на месте.")
