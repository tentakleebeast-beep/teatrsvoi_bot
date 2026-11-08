"""Роутер файлов: загрузка, папки, теги, поиск, архивы."""

from __future__ import annotations

import io
from urllib.parse import quote

import core
import errors
import httpx
import keyboards
import parsers
import texts
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from webapp_links import base_url as webapp_base_url

from config import TOOL_API_TOKEN, TOOL_API_URL, WEBAPP_BASE_URL, log

router = Router()


def _incoming_file(message: Message):
    """Что человек прислал: (объект Telegram, имя, mime). У фото берём самый крупный размер."""
    if message.document:
        doc = message.document
        return doc, doc.file_name or "документ", doc.mime_type or ""
    if message.photo:
        photo = message.photo[-1]
        return photo, f"photo_{photo.file_unique_id}.jpg", "image/jpeg"
    if message.audio:
        audio = message.audio
        return audio, audio.file_name or f"{audio.title or 'аудио'}.mp3", \
            audio.mime_type or "audio/mpeg"
    if message.voice:
        voice = message.voice
        return voice, f"voice_{voice.file_unique_id}.ogg", voice.mime_type or "audio/ogg"
    return None


async def upload_to_storage(chat_id: int, data: bytes, name: str, mime: str) -> dict:
    """Отдать байты tool-api: тело — файл, имя и тип — заголовками.

    Отдельная функция, а не общий `api()`: там JSON, здесь поток байтов до 20 МБ.
    """
    headers = {"Authorization": f"Bearer {TOOL_API_TOKEN}",
               "X-Actor-Chat-Id": str(chat_id),
               "X-File-Name": quote(name),
               "X-File-Mime": mime or "application/octet-stream"}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{TOOL_API_URL}/files/upload",
                                         content=data, headers=headers)
    except httpx.HTTPError as exc:
        log.error("tool-api /files/upload недоступен: %s", exc)
        return {"ok": False, "error": "tool_api_unavailable",
                "detail": texts.SERVICE_UNAVAILABLE}
    try:
        return response.json()
    except ValueError:
        return {"ok": False, "error": "bad_response", "detail": "Хранилище ответило непонятно."}


async def _admin_only(message: Message) -> bool:
    """Один вход в дом файлов: без `admin.panel` — отказ до запроса в tool-api (П-7).

    Правило одно на все команды и на приём файлов: иначе у дома два разных права,
    и участник снова получает файлы через тот вход, который забыли закрыть.
    Единственное исключение — `/revoke` (`cmd_revoke`): там считается не доступ
    к файлу, а закрытие чужого доступа к своему, и право проверяет tool-api.
    """
    if core.has(await core.caps_of(message.chat.id), "admin.panel"):
        return False
    await message.answer(texts.FILES_ADMIN_ONLY)
    return True


@router.message(F.video | F.video_note | F.animation)
async def on_video(message: Message) -> None:
    """Видео не принимаем: 60-гигабайтный диск и лимиты Telegram (FR-K22).

    Отказ явный и с объяснением: молчание ботом не воспринимается, человек шлёт ещё раз.
    """
    await message.answer("Видео бот не принимает — для хранилища это слишком тяжело. "
                         "Звук и картинки принимаю.")


@router.message(F.document | F.photo | F.audio | F.voice)
async def on_file(message: Message) -> None:
    chat_id = message.chat.id
    if await _admin_only(message):
        return
    found = _incoming_file(message)
    if found is None:
        return
    obj, name, mime = found
    size = getattr(obj, "file_size", 0) or 0
    if size > parsers.FILE_MAX_BYTES:
        await message.answer(texts.FILE_HINT)
        return

    buffer = io.BytesIO()
    bot = message.bot
    if bot is None:
        return
    try:
        await bot.download(obj, destination=buffer)
    except errors.TELEGRAM_ERRORS as exc:          # Telegram бывает недоступен
        log.error("не удалось скачать файл из Telegram: %s", exc)
        await message.answer("Не смог забрать файл из Telegram — попробуйте ещё раз.")
        return

    result = await upload_to_storage(chat_id, buffer.getvalue(), name, mime)
    if not result.get("ok"):
        await message.answer(errors.error_text(result) or "Не получилось сохранить файл.")
        return

    kind_text = {"text": "документ", "image": "картинка", "audio": "аудио"}.get(
        str(result.get("kind")), "файл")
    lines = []
    if result.get("duplicate"):
        lines.append("Этот файл уже был — второй раз не сохранял.")
    lines += [f"Сохранил: {result.get('name')}",
              f"Тип: {kind_text}",
              f"Размер: {parsers.human_size(int(result.get('size_bytes') or 0))}"]
    if result.get("duration_sec"):
        lines.append(f"Длительность: {parsers.human_duration(int(result['duration_sec']))}")
    lines += [f"Папка: {result.get('folder')}",
              f"Свободно: {result.get('quota_left_mb')} МБ"]
    if result.get("warning"):
        lines.append(str(result["warning"]))
    await message.answer("\n".join(lines))


class ApiRefused(Exception):
    """tool-api ответил отказом: статус и конверт `{code, detail}` для текста человеку.

    Раньше `api_bytes` глотал статус и возвращал None, поэтому `/zip` на папке больше
    потолка говорил «папка пуста или недоступна» — неправда (FR-M6).
    """

    def __init__(self, status: int, body: dict) -> None:
        super().__init__(f"tool-api -> {status}")
        self.status = status
        self.body = body


async def api_bytes(path: str, chat_id: int) -> bytes | None:
    """Скачать байты из tool-api.

    None — сервис недоступен (сеть, таймаут). Отказ по HTTP — `ApiRefused`
    со статусом и телом: вызывающий сам решает, что сказать человеку.
    """
    headers = {"Authorization": f"Bearer {TOOL_API_TOKEN}",
               "X-Actor-Chat-Id": str(chat_id)}
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.get(f"{TOOL_API_URL}{path}", headers=headers)
    except httpx.HTTPError as exc:
        log.error("tool-api %s недоступен: %s", path, exc)
        return None
    if response.status_code >= 400:
        log.warning("tool-api %s -> %s", path, response.status_code)
        try:
            body = response.json()
        except ValueError:
            body = {}
        raise ApiRefused(response.status_code, body if isinstance(body, dict) else {})
    return response.content


@router.message(Command("files", texts.TITLE_FILES))
async def cmd_files(message: Message) -> None:
    """Список последних файлов с кнопками: нажал — получил файл или ссылку."""
    chat_id = message.chat.id
    if await _admin_only(message):
        return
    result = await core.api("/files?limit=10", None, chat_id, method="GET")
    rows = result.get("data") or []
    if not rows:
        await message.answer("Файлов пока нет. Пришлите документ, картинку или аудио — сохраню.")
        return

    lines = ["Последние файлы:"]
    buttons: list[list[tuple[str, str]]] = []
    for index, row in enumerate(rows, start=1):
        size = parsers.human_size(int(row.get("size_bytes") or 0))
        lines.append(f"{index}. {row.get('name')} — {size}")
        label = f"{index}. {row.get('name')}"[:60]
        buttons.append([(label, f"fl:{row.get('id')}")])
    await message.answer("\n".join(lines), reply_markup=keyboards.kb(buttons))


@router.callback_query(F.data.startswith("fl:"))
async def file_send(cb: CallbackQuery) -> None:
    """Отдать файл: до 50 МБ — документом в чат, крупнее — ссылкой на 15 минут."""
    if cb.message is None:
        await cb.answer(texts.MESSAGE_UNAVAILABLE, show_alert=True)
        return
    chat_id = cb.message.chat.id
    if not core.has(await core.caps_of(chat_id), "admin.panel"):
        await cb.answer(texts.FILES_ADMIN_ONLY, show_alert=True)
        return
    try:
        file_id = int((cb.data or "").split(":", 1)[1])
    except (IndexError, ValueError):
        await cb.answer("Не понял, какой файл", show_alert=True)
        return
    await cb.answer()

    meta = await core.api(f"/files/{file_id}", None, chat_id, method="GET")
    if not meta.get("ok"):
        # чужой и несуществующий неотличимы намеренно (FR-K8)
        await cb.message.answer("Файл не найден.")
        return
    name = str(meta.get("name") or "файл")
    size = int(meta.get("size_bytes") or 0)

    if size > parsers.FILES_IN_CHAT_LIMIT:
        link = await core.api(f"/files/{file_id}/link", {}, chat_id)
        if not link.get("ok"):
            await cb.message.answer("Не удалось получить ссылку на файл.")
            return
        await cb.message.answer(
            f"{name}\nРазмер: {parsers.human_size(size)}\n"
            f"В чат такой не помещается. Ссылка на 15 минут:\n{link.get('url')}")
        return

    try:
        data = await api_bytes(f"/files/{file_id}/content", chat_id)
    except ApiRefused as refused:
        # Тело отказа тут не показываем: человеку важно «не вышло — повторите»,
        # а код и конверт остались в логе (`api_bytes`).
        log.warning("файл %s не отдан: %s %s", file_id, refused.status, refused.body)
        await cb.message.answer("Не удалось достать файл из хранилища — попробуйте позже.")
        return
    if data is None:
        await cb.message.answer("Не удалось достать файл из хранилища — попробуйте позже.")
        return
    await cb.message.answer_document(
        BufferedInputFile(data, filename=name),
        caption=f"{name}\n{meta.get('folder') or 'Личное'}"[:1000])


@router.message(Command("folders", "папки"))
async def cmd_folders(message: Message) -> None:
    """Показать дерево: общие папки — всем, личные — свои."""
    chat_id = message.chat.id
    if await _admin_only(message):
        return
    shared = await core.api("/folders?scope=shared", None, chat_id, method="GET")
    personal = await core.api("/folders?scope=personal", None, chat_id, method="GET")
    rows = (shared.get("data") or []) + (personal.get("data") or [])
    if not rows:
        await message.answer("Папок пока нет.")
        return
    lines = ["Папки:"]
    for row in rows:
        depth = int(row.get("depth") or 1)
        mark = "личная" if str(row.get("path", "")).startswith("Мои") else ""
        lines.append(f"{'  ' * (depth - 1)}{row.get('name')} #{row.get('id')}"
                     + (f" ({mark})" if mark else ""))
    lines.append("")
    lines.append("Создать: /mkdir Имя или /mkdir Спектакли/Пурга/реквизит")
    lines.append("Переименовать: /rename 12 Новое имя")
    lines.append("Закрыть: /rmfolder 12 (файлы останутся)")
    lines.append("Порядок: /order 12 3 1 2 (номера файлов из /files)")
    lines.append("Архив папки: /zip номер (номер — после решётки)")
    await message.answer("\n".join(lines)[:3800])


@router.message(Command("mkdir", "папка"))
async def cmd_mkdir(message: Message) -> None:
    """Создать папку по пути. Промежуточные уровни создаются сами (до 5 уровней)."""
    chat_id = message.chat.id
    if await _admin_only(message):
        return
    parts = parsers.split_folder_path((message.text or "").partition(" ")[2])
    if not parts:
        await message.answer("Напишите имя: /mkdir Реквизит или /mkdir Спектакли/Пурга/шумы")
        return

    tree = await core.api("/folders?scope=shared", None, chat_id, method="GET")
    by_path = {row["path"]: row for row in (tree.get("data") or [])}
    parent_id = None
    for index, name in enumerate(parts):
        path = "/".join(parts[: index + 1])
        existing = by_path.get(path)
        if existing:
            parent_id = existing["id"]
            continue
        created = await core.api("/folders", {"name": name, "scope": "shared",
                                         "parent_id": parent_id}, chat_id)
        if not created.get("ok"):
            detail = created.get("detail")
            text = detail if isinstance(detail, str) and detail else None
            await message.answer(text or "Не получилось создать папку.")
            return
        parent_id = created["id"]
        by_path[path] = created

    await message.answer(f"Готово: /{parts[-1]}\nПуть: {'/'.join(parts)}")


# --- папки: переименовать, закрыть, порядок (Ф4, Ш-08) -----------------------

def _folder_id_and_rest(message: Message) -> tuple[int, str]:
    """Номер папки и остаток строки. Номер — то, что человек видит после решётки в /folders."""
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        return 0, ""
    return int(parts[1]), (parts[2].strip() if len(parts) > 2 else "")


@router.message(Command("rename", "переименовать"))
async def cmd_rename(message: Message) -> None:
    """Переименовать папку: тот же маршрут, что у кнопки на экране «Файлы»."""
    chat_id = message.chat.id
    if await _admin_only(message):
        return
    folder_id, name = _folder_id_and_rest(message)
    if not folder_id or not name:
        await message.answer("Напишите: /rename 12 Новое имя — где 12 это номер папки из /folders")
        return
    result = await core.api(f"/folders/{folder_id}/rename", {"name": name}, chat_id)
    if not result.get("ok"):
        detail = result.get("detail")
        text = detail if isinstance(detail, str) and detail else None
        await message.answer(text or "Не получилось переименовать папку.")
        return
    await message.answer(f"Готово. Новый путь: {result.get('path')}")


@router.message(Command("rmfolder", "удалить-папку"))
async def cmd_rmfolder(message: Message) -> None:
    """Закрыть папку. Файлы не удаляются — они остаются в «Без папки» (FR-M8)."""
    chat_id = message.chat.id
    if await _admin_only(message):
        return
    folder_id, _rest = _folder_id_and_rest(message)
    if not folder_id:
        await message.answer("Напишите: /rmfolder 12 — где 12 это номер папки из /folders")
        return
    result = await core.api(f"/folders/{folder_id}/delete", {}, chat_id)
    if not result.get("ok"):
        detail = result.get("detail")
        text = detail if isinstance(detail, str) and detail else None
        await message.answer(text or "Не получилось закрыть папку.")
        return
    await message.answer(
        f"Папка закрыта. Файлов осталось в «Без папки»: {result.get('files_detached', 0)}.")


@router.message(Command("order", "порядок"))
async def cmd_order(message: Message) -> None:
    """Порядок треков в папке: «/order 12 3 1 2» — номера файлов в нужном порядке.

    Тот же маршрут и тот же обработчик, что у стрелок на экране папки: иначе порядок
    в чате и на экране мог бы стать разным.
    """
    chat_id = message.chat.id
    if await _admin_only(message):
        return
    folder_id, rest = _folder_id_and_rest(message)
    file_ids = [int(value) for value in rest.replace(",", " ").split() if value.isdigit()]
    if not folder_id or not file_ids:
        await message.answer("Напишите: /order 12 3 1 2 — папка и номера файлов из /files")
        return
    result = await core.api(f"/folders/{folder_id}/order", {"file_ids": file_ids}, chat_id)
    if not result.get("ok"):
        detail = result.get("detail")
        text = detail if isinstance(detail, str) and detail else None
        await message.answer(text or "Не получилось сохранить порядок.")
        return
    await message.answer(f"Порядок сохранён: {result.get('updated', 0)} треков.")


# --- доступ админа к личному файлу (Ш-7) -------------------------------------

@router.message(Command("access", "доступ"))
async def cmd_access(message: Message) -> None:
    """Админ открывает доступ к чужому файлу по номеру и причине.

    Номер файла админ берёт из аудита или из сообщения человека. Причина обязательна:
    доступ без причины — это и есть «админ видит всё», только с лишним шагом.
    """
    chat_id = message.chat.id
    if await _admin_only(message):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        # без угловых скобок: Telegram принимает их за HTML-тег и отклоняет сообщение
        await message.answer("Напишите: /access номер причина\n"
                             "Например: /access 42 сверка сметы за октябрь")
        return
    file_id, reason = int(parts[1]), parts[2].strip()
    result = await core.api(f"/admin/files/{file_id}/access", {"reason": reason}, chat_id)
    if not result.get("ok"):
        detail = result.get("detail")
        text = detail if isinstance(detail, str) and detail else None
        await message.answer(text or "Не удалось открыть доступ.")
        return
    await message.answer(
        f"Доступ к файлу {file_id} открыт на {result.get('ttl_hours')} ч.\n"
        "Владелец уведомлён и может отозвать доступ командой /revoke.")


@router.message(Command("revoke", "отозвать"))
async def cmd_revoke(message: Message) -> None:
    """Отозвать доступ к личному файлу. Право решает tool-api: владелец или админ (FR-K9).

    Здесь намеренно нет `_admin_only`: отзыв — не доступ к файлу, а закрытие чужого
    доступа к своему. Владельцу об этом обещает уведомление (`file_access.request`:
    «Отозвать: /revoke N») и ответ `/access`; проверка владельца или админа уже стоит
    в `file_access.revoke`, и дублировать её в оболочке — значит обещать одно, а делать
    другое (Ш-08, возврат ревью).
    """
    chat_id = message.chat.id
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("Напишите: /revoke 42 — где 42 это номер файла")
        return
    result = await core.api(f"/files/{int(parts[1])}/access/revoke", {}, chat_id)
    if not result.get("ok"):
        detail = result.get("detail")
        text = detail if isinstance(detail, str) and detail else None
        await message.answer(text or "Не удалось отозвать доступ.")
        return
    await message.answer("Доступ закрыт.")


def _zip_refusal_text(refused: ApiRefused, folder_id: int) -> str:
    """Объяснить отказ архива словами tool-api, а не «папка пуста».

    Потолок архива — это отдельный отказ `folder_zip_too_large` (413): текст
    «Архив больше N МБ — скачивайте по файлам» приходит в `detail` конверта
    (docs/architecture/85), и человек должен увидеть именно его.
    """
    code = errors.error_code(refused.body)
    text = errors.error_text(refused.body) if refused.body else ""
    if code == "folder_zip_too_large":
        pages = webapp_base_url(WEBAPP_BASE_URL)
        if pages:
            text += f"\nПо одному файлу: {pages}/webapp/folder?id={folder_id}"
        return text
    if text:
        return text
    if refused.status >= 500:
        return "Сервис файлов не ответил — повторите через минуту."
    return "Не получилось собрать архив: папка недоступна — проверьте номер из /folders."


@router.message(Command("zip", "архив"))
async def cmd_zip(message: Message) -> None:
    """Собрать папку архивом. До 50 МБ — документом в чат, иначе отправляем в Mini App.

    Порядок внутри архива тот же, что на экране (Ш-9): иначе распаковка перемешает треки,
    и на показе музыка пойдёт не в том порядке.
    """
    chat_id = message.chat.id
    if await _admin_only(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("Напишите: /zip 12 — где 12 это номер папки из /folders")
        return
    folder_id = int(parts[1].strip())
    try:
        data = await api_bytes(f"/folders/{folder_id}/zip", chat_id)
    except ApiRefused as refused:
        await message.answer(_zip_refusal_text(refused, folder_id))
        return
    if data is None:
        await message.answer("Не получилось собрать архив: сервис файлов не ответил, "
                             "повторите через минуту.")
        return
    if len(data) > parsers.FILES_IN_CHAT_LIMIT:
        pages = webapp_base_url(WEBAPP_BASE_URL)
        hint = (f"Откройте Mini App: {pages}/webapp/folder?id={folder_id}"
                if pages else "Скачайте архив в Mini App — целиком в чат он не влезает.")
        await message.answer(f"Архив {parsers.human_size(len(data))} — в чат столько не влезет.\n{hint}")
        return
    await message.answer_document(
        BufferedInputFile(data, filename=f"папка-{folder_id}.zip"),
        caption=parsers.human_size(len(data)))


# --- поиск и теги (Ш-10) -----------------------------------------------------

@router.message(Command("search", "найти"))
async def cmd_search(message: Message) -> None:
    """Поиск по своим файлам: имя, теги, текст документа.

    «Не нашёл» — это ответ, а не повод угадывать: лучше сказать прямо, чем показать
    случайный файл (правило проекта про галлюцинации).
    """
    chat_id = message.chat.id
    if await _admin_only(message):
        return
    query = (message.text or "").partition(" ")[2].strip()
    if not query:
        await message.answer("Напишите: /search антракт или /search Пурга смета")
        return
    result = await core.api(f"/files?q={quote(query)}", None, chat_id, method="GET")
    rows = result.get("data") or []
    if not rows:
        await message.answer(f"{texts.PREFIX_QUERY}{query}» ничего не нашёл.")
        return
    lines = [f"Нашёл по запросу «{query}»:"]
    buttons: list[list[tuple[str, str]]] = []
    for index, row in enumerate(rows[:10], start=1):
        size = parsers.human_size(int(row.get("size_bytes") or 0))
        lines.append(f"{index}. {row.get('name')} — {size}")
        buttons.append([(f"{index}. {row.get('name')}"[:60], f"fl:{row.get('id')}")])
    await message.answer("\n".join(lines), reply_markup=keyboards.kb(buttons))


@router.message(Command("tags", "теги"))
async def cmd_tags(message: Message) -> None:
    chat_id = message.chat.id
    if await _admin_only(message):
        return
    result = await core.api("/tags", None, chat_id, method="GET")
    rows = result.get("data") or []
    if not rows:
        await message.answer("Тегов пока нет. Прицепить: /tag 42 реквизит")
        return
    lines = ["Теги:"]
    for row in rows[:30]:
        mark = "" if row.get("status") == "approved" else " (черновик)"
        lines.append(f"#{row.get('id')} {row.get('title')}{mark} — {row.get('uses', 0)}")
    lines.append("")
    lines.append("Прицепить: /tag 42 реквизит, Пурга")
    await message.answer("\n".join(lines)[:3800])


@router.message(Command("tag", "тег"))
async def cmd_tag(message: Message) -> None:
    """Прицепить теги к файлу: «/tag 42 реквизит, Пурга». Незнакомые станут черновиками."""
    chat_id = message.chat.id
    if await _admin_only(message):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        await message.answer("Напишите: /tag 42 реквизит, Пурга")
        return
    file_id = int(parts[1])
    titles = [item.strip() for item in parts[2].replace(";", ",").split(",") if item.strip()]
    if not titles:
        await message.answer("Не понял названия тегов.")
        return

    tag_ids: list[int] = []
    drafts: list[str] = []
    for title in titles[:10]:
        created = await core.api("/tags", {"title": title}, chat_id)
        if not created.get("ok"):
            detail = created.get("detail")
            text = detail if isinstance(detail, str) and detail else None
            await message.answer(text or "Не получилось создать тег.")
            return
        tag_ids.append(int(created["id"]))
        if created.get("status") == "draft" and created.get("created"):
            drafts.append(str(created["title"]))

    applied = await core.api(f"/files/{file_id}/tags", {"tag_ids": tag_ids}, chat_id)
    if not applied.get("ok"):
        detail = applied.get("detail")
        text = detail if isinstance(detail, str) and detail else None
        await message.answer(text or "Не получилось прицепить теги.")
        return
    answer = f"Теги файла {file_id}: " + ", ".join(
        str(row.get("title")) for row in (applied.get("data") or []))
    if drafts:
        answer += ("\nНовые теги ждут утверждения админа: " + ", ".join(drafts)
                   + ". Пока их видите только вы.")
    await message.answer(answer)


@router.message(Command("tag-ok", "тег-ок"))
async def cmd_tag_ok(message: Message) -> None:
    """Утвердить черновик тега: только админ — иначе словарь разъедется."""
    chat_id = message.chat.id
    if await _admin_only(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("Напишите: /tag-ok 7 — где 7 это номер тега из /tags")
        return
    result = await core.api(f"/tags/{int(parts[1])}/approve", {}, chat_id)
    if not result.get("ok"):
        detail = result.get("detail")
        text = detail if isinstance(detail, str) and detail else None
        await message.answer(text or "Не получилось утвердить тег.")
        return
    await message.answer(f"Тег «{result.get('title')}» утверждён.")
