"""Заметки в чате: текст → карточка → подтверждение → запись (FR-F7).

Дом заметок — Mini App «Ещё → Заметки» (`/webapp/notes`); в чате остаётся то, ради
чего человек берёт телефон: записать заметку одной фразой и подтвердить её кнопкой.
Текст не пишется молча — сначала карточка с заголовком, текстом, тегами и видимостью,
потом «Сохранить личную» или «В общую».

Запись идёт тем же путём, что у экрана: `POST /tools/notes` в tool-api, те же
`create_note` и право `notes.write`. Второго пути записи нет — как у бюджета (A-3).

Триггеры детерминированные, без модели:

  /note <текст>      команда (кириллический алиас `/заметка` работает, но в
                     подсказки не попадает: клиент Telegram его не подсвечивает);
  заметка: <текст>   фраза (её ловит `note_start` из `routers/menu.py`);
  /notes             короткая справка и кнопка на экран.

Заметка с цифрами не должна превращаться в трату: `on_text` в меню передаёт фразу
сюда ДО разбора черновика бюджета.
"""

from __future__ import annotations

import re

import core
import keyboards
import texts
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from webapp_links import keyboard as webapp_keyboard

from config import WEBAPP_BASE_URL

router = Router()

#: Команда или фраза в начале текста. Дальше — сам текст заметки.
NOTE_PREFIX = re.compile(
    r"^\s*(?:/?(?:заметка|заметки|note|notes)\b[:,]?|запиши\s+заметку[:,]?)\s*",
    re.IGNORECASE)

#: Тег — слово после решётки. Кириллица входит в `\w`, поэтому «#реквизит» работает.
TAG_RE = re.compile(r"#([\w-]+)", re.UNICODE)

MAX_TITLE = 80
MAX_TAGS = 10


def note_draft(text: str) -> dict:
    """Разобрать фразу в заметку: заголовок, текст, теги.

    Заголовок — первая строка (или её начало); отдельное поле человеку в чате
    заполнять негде. Теги вынимаются из текста, но остаются и в тексте: заметка —
    запись человека, вычищать из неё куски нельзя.
    """
    raw = NOTE_PREFIX.sub("", (text or "").strip()).strip()
    tags: list[str] = []
    for tag in TAG_RE.findall(raw):
        tag = tag.strip()[:30]
        if tag and tag not in tags:
            tags.append(tag)
    first = next((line.strip() for line in raw.splitlines() if line.strip()), "")
    title = first if len(first) <= MAX_TITLE else first[:MAX_TITLE - 1].rstrip() + "…"
    return {"title": title, "body": raw, "tags": tags[:MAX_TAGS], "scope": "personal"}


def _card(draft: dict, shared: bool) -> str:
    return texts.note_card(draft, shared=shared)


def _buttons() -> InlineKeyboardMarkup:
    """Две кнопки записи (личная и общая) и отмена.

    Подписи — в `texts.py`: их видит человек, и они же в тестах. Порядок кнопок один:
    «Сохранить личную» первой, потому что по умолчанию заметка личная.
    """
    return keyboards.kb([
        [(texts.NOTE_BTN_PRIVATE, "note:personal"),
         (texts.NOTE_BTN_SHARED, "note:shared")],
        [(texts.BTN_CANCEL, "note:no")],
    ])


async def note_start(message: Message, state: FSMContext) -> None:
    """Показать карточку заметки. Пустой текст — подсказка, а не пустая карточка."""
    draft = note_draft(message.text or "")
    if not draft["body"]:
        await message.answer(texts.NOTE_HINT, reply_markup=webapp_keyboard(
            "Открыть заметки", "/webapp/notes", WEBAPP_BASE_URL))
        return
    await state.update_data(note=draft)
    await message.answer(_card(draft, shared=False), reply_markup=_buttons())


@router.message(Command("заметка", "note"))
async def cmd_note(message: Message, state: FSMContext) -> None:
    """`/note <текст>` — карточка с подтверждением."""
    await note_start(message, state)


@router.message(Command("заметки", "notes"))
async def cmd_notes(message: Message) -> None:
    """`/notes` — где живёт дом: экран Mini App, а не лента в чате."""
    await message.answer(
        texts.NOTES_HINT,
        reply_markup=webapp_keyboard("Открыть заметки", "/webapp/notes", WEBAPP_BASE_URL))


@router.callback_query(F.data.startswith("note:"))
async def note_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    """Подтверждение карточки: пишем тем же путём, что и экран Mini App."""
    if cb.message is None:
        await cb.answer(texts.MESSAGE_UNAVAILABLE, show_alert=True)
        return
    parts = (cb.data or "").split(":")
    if len(parts) != 2 or parts[1] not in ("personal", "shared", "no"):
        await cb.answer(texts.STALE_BUTTON, show_alert=True)
        return
    verb = parts[1]
    data = await state.get_data()
    draft = data.get("note")

    if verb == "no":
        await state.clear()
        await cb.message.edit_text(texts.CANCELLED)
        await cb.answer()
        return

    if not draft or not draft.get("body"):
        await cb.answer(texts.NOTE_STALE, show_alert=True)
        return

    chat_id = cb.message.chat.id
    if not core.has(await core.caps_of(chat_id), "notes.write"):
        await cb.message.edit_text(texts.NOTE_NO_WRITE)
        await cb.answer()
        await state.clear()
        return

    await cb.answer("Записываю")
    payload = {"title": draft.get("title") or "", "body": draft.get("body") or "",
               "tags": draft.get("tags") or [], "scope": verb}
    result = await core.api("/tools/notes", payload, chat_id)
    if not result.get("ok"):
        await cb.answer(texts.NOTE_SAVE_FAILED, show_alert=True)
        return
    await state.clear()
    note = result.get("note") or {}
    kind = "общая" if note.get("scope") == "shared" else "личная"
    await cb.message.edit_text(
        f"{texts.NOTE_SAVED}«{note.get('title') or draft.get('title')}» ({kind}).")
