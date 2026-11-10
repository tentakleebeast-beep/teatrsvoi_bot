"""Роутер админ-панели: участники, заявки, права, бюджет, аудит."""

from __future__ import annotations

import core
import errors
import keyboards
import periods as money_periods
import texts
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from states import PanelEdit
from webapp_links import base_url as webapp_base_url
from webapp_links import keyboard as webapp_keyboard

from config import WEBAPP_BASE_URL, log

router = Router()


async def send_people(message: Message) -> None:
    """Участники списком: нажатие открывает профиль человека (тариф и сведения)."""
    res = await core.api("/tools/participants", None, message.chat.id, method="GET")
    rows = [row for row in (res.get("data") or []) if row.get("status") != "archived"]
    if not rows:
        await message.answer("Список пуст.")
        return
    buttons = [[(texts._fit(row["display_name"], 28), f"panel:person:{row['id']}")] for row in rows[:20]]
    base = webapp_base_url(WEBAPP_BASE_URL)
    hint = ("Нажмите имя — откроется профиль." if not base else
            "Нажмите имя: в окне откроется профиль с тарифом и сведениями.")
    await message.answer(f"{hint}\nЛюдей: {len(rows)}", reply_markup=keyboards.kb(buttons))


async def start_reconcile(message: Message) -> None:
    """Сверка листов бюджета: показываем отчёт, применение — отдельной кнопкой."""
    res = await core.api("/admin/reconcile", {"apply": False, "scope": "all"}, message.chat.id)
    if not res.get("ok"):
        await message.answer("Сверка не прошла.\n\n" + str(res.get("text"))[:3000])
        return
    await message.answer(str(res.get("text") or "Сверка выполнена.")[:3800],
                         parse_mode=None,
                         reply_markup=keyboards.kb([[("Применить к базе", "panel:reconcile_apply")]]))


async def send_pending(message: Message) -> None:
    """Заявки на доступ — кнопками, без админ-панели (нужно организатору)."""
    res = await core.api("/admin/pending", None, message.chat.id, method="GET")
    rows = res.get("data") or []
    if not rows:
        await message.answer("Заявок нет.")
        return
    lines = ["Ожидают подтверждения:"]
    for row in rows[:8]:
        lines.append(f"  {row['display_name']} — {texts.ROLE_TITLES.get(row.get('role'), row.get('role'))}")
    markup = keyboards.kb([[(texts.BTN_CONFIRM, f"adm:ok:{rows[0]['id']}"),
                  (texts.BTN_REJECT, f"adm:no:{rows[0]['id']}")]])
    await message.answer("\n".join(lines), reply_markup=markup)
    if len(rows) > 1:
        await message.answer(f"Кнопки относятся к первой заявке; всего ждут {len(rows)}.")


async def send_invite(message: Message, role: str = "troupe") -> None:
    """Код приглашения кнопкой: печатать /invite больше не нужно."""
    res = await core.api("/admin/invite", {"role": role, "ttl_days": 7}, message.chat.id)
    if not res.get("ok"):
        await message.answer("Не удалось выпустить код: "
                             + errors.error_text(res))
        return
    code = str(res.get("code") or "")
    await message.answer(texts.invite_text(code, res.get("expires_at"), role), parse_mode="HTML",
                         reply_markup=keyboards.copy_code_kb(
                             code, [[("Ещё код", "panel:invite")],
                                    [(texts.TITLE_CODES, "panel:codes")]]))


async def panel_root(message: Message, edit: bool = True) -> None:
    """Админ-панель в чате: кнопки по возможностям, ничего печатать не нужно."""
    caps = await core.caps_of(message.chat.id)
    if not core.has(caps, *keyboards.PANEL_ENTRY_CAPS):
        await message.answer("Панель доступна организатору и администратору.")
        return
    text = "Админ-панель. Что открыть?"
    markup = keyboards.kb(keyboards.panel_rows(caps))
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "panel:root")
async def panel_back(cb: CallbackQuery) -> None:
    await panel_root(cb.message)
    await cb.answer()


@router.callback_query(F.data == "panel:people")
async def panel_people(cb: CallbackQuery) -> None:
    res = await core.api("/tools/participants", None, cb.message.chat.id, method="GET")
    if not res.get("ok"):
        await cb.message.edit_text(
            "Не удалось прочитать список: " + errors.error_text(res),
            parse_mode=None, reply_markup=keyboards.kb([[(texts.BTN_BACK, "panel:root")]]))
        await cb.answer()
        return
    rows = res.get("data", [])
    active = [p for p in rows if p["status"] == "active"]
    pending = [p for p in rows if p["status"] == "pending"]
    blocked = [p for p in rows if p["status"] == "blocked"]
    ordered = active + pending + blocked
    head = (f"Участники: активных {len(active)}, заявок {len(pending)}"
            + (f", заблокированных {len(blocked)}" if blocked else ""))
    text = head + "\n\n" + (texts.people_table(ordered) if ordered else "Список пуст.")
    rows = [[(texts._fit(p["display_name"], 24), f"panel:person:{p['id']}")]
            for p in ordered]
    buttons = [rows[i] + (rows[i + 1] if i + 1 < len(rows) else [])
               for i in range(0, len(rows), 2)]
    buttons.append([(texts.BTN_BACK, "panel:root")])
    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=keyboards.kb(buttons))
    await cb.answer()


async def _denied(cb: CallbackQuery, *codes: str) -> bool:
    """Отказ оболочки: кнопка могла остаться от старого меню (ADR-13, правило 5).

    Второй рубеж — сервер: каждый обработчик ниже зовёт путь записи, у которого
    своя проверка права. Здесь проверка только чтобы не вести человека в 403.
    """
    caps = await core.caps_of(cb.message.chat.id)
    if core.has(caps, *codes):
        return False
    await cb.answer("Нет права", show_alert=True)
    return True


async def _send_archive(cb: CallbackQuery) -> None:
    """Нарисовать список архива в текущем сообщении, не отвечая на callback.

    Отдельно от обработчика, потому что после возврата человека список нужно
    перерисовать, а `cb.answer()` на один запрос Telegram принимает один раз.
    """
    res = await core.api("/tools/participants?include_archived=true", None,
                         cb.message.chat.id, method="GET")
    if not res.get("ok"):
        await cb.message.edit_text(
            "Не удалось прочитать архив: " + errors.error_text(res),
            parse_mode=None, reply_markup=keyboards.kb([[(texts.BTN_BACK, "panel:root")]]))
        return
    archived = [p for p in (res.get("data") or []) if p.get("status") == "archived"]
    if not archived:
        await cb.message.edit_text(
            "В архиве никого нет.",
            reply_markup=keyboards.kb([[(texts.BTN_BACK, "panel:root")]]))
        return
    lines = [f"В архиве: {len(archived)}. Нажмите «Вернуть» — человек вернётся "
             "в прежний статус (заявка останется заявкой).", ""]
    for person in archived[:12]:
        roles = ", ".join(str(texts.ROLE_TITLES.get(r, r)) for r in (person.get("roles") or []))
        lines.append(f"{person['display_name']} — {roles}" if roles else person["display_name"])
    buttons = [[(f"{texts.BTN_RESTORE}: {texts._fit(p['display_name'], 22)}",
                 f"panel:unarchive:{p['id']}")] for p in archived[:12]]
    buttons.append([(texts.TITLE_PEOPLE, "panel:people"), (texts.BTN_BACK, "panel:root")])
    await cb.message.edit_text("\n".join(lines), parse_mode=None,
                               reply_markup=keyboards.kb(buttons))


@router.callback_query(F.data == "panel:archive")
async def panel_archive(cb: CallbackQuery) -> None:
    """Архив людей в чате (AUD-3, Л6, FR-A7): список и возврат кнопками."""
    if await _denied(cb, "participants.manage"):
        return
    await _send_archive(cb)
    await cb.answer()


@router.callback_query(F.data.startswith("panel:archive:"))
async def panel_person_archive(cb: CallbackQuery) -> None:
    """Отправить человека в архив: кнопка в карточке, путь записи — `/admin/archive`."""
    if await _denied(cb, "participants.manage"):
        return
    participant_id = _pid_of(cb.data)
    if participant_id is None:
        await cb.answer("Непонятный человек", show_alert=True)
        return
    res = await core.api("/admin/archive",
                         {"participant_id": participant_id, "reason": "chat"}, cb.message.chat.id)
    if not res.get("ok"):
        await cb.answer("Не удалось: " + errors.error_text(res), show_alert=True)
        return
    name = res.get("name") or "Человек"
    await cb.message.edit_text(
        f"«{name}» в архиве. Данные и история сохранены; вернуть можно из «Архив».",
        parse_mode=None,
        reply_markup=keyboards.kb([[(texts.TITLE_ARCHIVE, "panel:archive")],
                                   [(texts.TITLE_PEOPLE, "panel:people"),
                                    (texts.BTN_BACK, "panel:root")]]))
    await cb.answer("В архиве")


@router.callback_query(F.data.startswith("panel:unarchive:"))
async def panel_person_unarchive(cb: CallbackQuery) -> None:
    """Вернуть человека из архива: путь записи — `/admin/unarchive`."""
    if await _denied(cb, "participants.manage"):
        return
    participant_id = _pid_of(cb.data)
    if participant_id is None:
        await cb.answer("Непонятный человек", show_alert=True)
        return
    res = await core.api("/admin/unarchive",
                         {"participant_id": participant_id, "reason": "chat"}, cb.message.chat.id)
    if not res.get("ok"):
        await cb.answer("Не удалось: " + errors.error_text(res), show_alert=True)
        return
    await cb.answer("Возвращён")
    await _send_archive(cb)


async def _block_person(cb: CallbackQuery, blocked: bool) -> None:
    """Заблокировать или вернуть доступ: один путь записи — `/admin/block` (Л7, FR-A9).

    Блокировка обратима и закрывает доступ сразу, поэтому отдельного подтверждения
    нет: результат показан словами и с кнопкой обратного действия.
    """
    if await _denied(cb, "participants.manage"):
        return
    participant_id = _pid_of(cb.data)
    if participant_id is None:
        await cb.answer("Непонятный человек", show_alert=True)
        return
    res = await core.api("/admin/block",
                         {"participant_id": participant_id, "blocked": blocked,
                          "reason": "chat"}, cb.message.chat.id)
    if not res.get("ok"):
        await cb.answer("Не удалось: " + errors.error_text(res), show_alert=True)
        return
    name = res.get("name") or "Человек"
    if blocked:
        text = (f"«{name}» заблокирован: доступ закрыт сразу, из списков и истории "
                "не исчез. Вернуть доступ можно кнопкой ниже.")
        back = (texts.BTN_UNBLOCK, f"panel:unblock:{participant_id}")
    else:
        text = f"Доступ вернулся: «{name}», статус — {res.get('status') or 'active'}."
        back = (texts.BTN_BLOCK, f"panel:block:{participant_id}")
    await cb.message.edit_text(
        text, parse_mode=None,
        reply_markup=keyboards.kb([[back],
                                   [("К карточке", f"panel:person:{participant_id}")],
                                   [(texts.TITLE_PEOPLE, "panel:people"),
                                    (texts.BTN_BACK, "panel:root")]]))
    await cb.answer("Заблокирован" if blocked else "Доступ вернулся")


@router.callback_query(F.data.startswith("panel:block:"))
async def panel_person_block(cb: CallbackQuery) -> None:
    """Кнопка «Заблокировать» в карточке человека (Ш-07, Л7, FR-A9)."""
    await _block_person(cb, True)


@router.callback_query(F.data.startswith("panel:unblock:"))
async def panel_person_unblock(cb: CallbackQuery) -> None:
    """Кнопка «Разблокировать»: тот же путь записи с `blocked=false`."""
    await _block_person(cb, False)


@router.callback_query(F.data.startswith("panel:person:"))
async def panel_person(cb: CallbackQuery) -> None:
    """Карточка человека в чате админ-панели (Ш-50): чтение и правка кнопками."""
    parts = (cb.data or "").split(":")
    try:
        participant_id = int(parts[2])
    except (IndexError, ValueError):
        await cb.answer("Непонятный человек", show_alert=True)
        return
    res = await core.api(f"/tools/participants/{participant_id}/card", None,
                    cb.message.chat.id, method="GET")
    if not res.get("ok"):
        await cb.answer("Карточка недоступна: "
                        + errors.error_text(res), show_alert=True)
        return
    caps = await core.caps_of(cb.message.chat.id)
    buttons = []
    if core.has(caps, "participants.manage"):
        blocked = res["data"].get("status") == "blocked"
        buttons = [[("Роли", f"panel:proles:{participant_id}"),
                    ("Возможности", f"panel:caps:{participant_id}")],
                   [("Тариф взноса", f"panel:tariff:{participant_id}"),
                    (texts.BTN_ARCHIVE, f"panel:archive:{participant_id}")],
                   [(texts.BTN_UNBLOCK if blocked else texts.BTN_BLOCK,
                     f"panel:{'unblock' if blocked else 'block'}:{participant_id}")]]
    elif core.has(caps, "budget.write"):
        # организатор: тариф меняется здесь, остальное — в профиле окна (Д8, FR-B7)
        buttons = [[("Тариф взноса", f"panel:tariff:{participant_id}")]]
    tail = [[("К списку", "panel:people")], [(texts.BTN_BACK, "panel:root")]] if core.has(caps, "admin.panel") else []
    markup = keyboards.kb(buttons + tail) if (buttons or tail) else None
    await cb.message.edit_text(texts.person_card_text(res["data"]), parse_mode=None,
                               reply_markup=markup)
    if not core.has(caps, "admin.panel"):
        # профиль целиком — в окне; в чате остаётся сумма взноса
        profile_kb = webapp_keyboard("Открыть профиль в окне",
                                     f"/webapp/person?id={participant_id}", WEBAPP_BASE_URL)
        if profile_kb is not None:
            await cb.message.answer("Профиль участника: контакты, роли, взносы.",
                                    reply_markup=profile_kb)
    await cb.answer()


@router.callback_query(F.data.startswith("panel:proles:"))
async def panel_person_roles(cb: CallbackQuery) -> None:
    """Роли человека кнопками: нажатие переключает, печатать /role не нужно."""
    participant_id = _pid_of(cb.data)
    if participant_id is None:
        await cb.answer("Непонятный человек", show_alert=True)
        return
    res = await core.api(f"/tools/participants/{participant_id}/card", None,
                    cb.message.chat.id, method="GET")
    if not res.get("ok"):
        await cb.answer("Карточка недоступна", show_alert=True)
        return
    card = res["data"]
    current = set(card.get("roles") or [])
    rows = [[(f"{'✓ ' if role in current else '· '}{texts.ROLE_TITLES.get(role, role)}",
              f"panel:role:{participant_id}:{role}:{0 if role in current else 1}")]
            for role in ("admin", "org", "troupe")]
    # Педагог ведёт не прямое переключение, а экран дисциплин: там выбирают
    # предметы. Без них роль тоже выдаётся — с предупреждением (V-037).
    rows.append([(f"{'✓ ' if 'master' in current else '· '}{texts.ROLE_TITLES['master']}",
                  f"panel:mroles:{participant_id}")])
    await cb.message.edit_text(
        f"{card.get('display_name')}: роли. Нажатие выдаёт или снимает роль.",
        parse_mode=None,
        reply_markup=keyboards.kb(rows + [[("К карточке", f"panel:person:{participant_id}")],
                                [(texts.BTN_BACK, "panel:root")]]))
    await cb.answer()


@router.callback_query(F.data.startswith("panel:role:"))
async def panel_person_role_toggle(cb: CallbackQuery) -> None:
    parts = (cb.data or "").split(":")
    if len(parts) != 5:
        await cb.answer("Непонятное действие", show_alert=True)
        return
    _, _, participant_id, role, flag = parts
    res = await core.api("/admin/grant", {"participant_id": int(participant_id), "role": role,
                                     "enabled": flag == "1"}, cb.message.chat.id)
    if not res.get("ok"):
        await cb.answer("Не удалось: " + errors.error_text(res),
                        show_alert=True)
        return
    await cb.answer("Роль обновлена")
    await panel_person_roles(cb)


def _pid_of(data: str | None) -> int | None:
    try:
        return int((data or "").split(":")[2])
    except (IndexError, ValueError):
        return None


async def _card_of(cb: CallbackQuery, participant_id: int) -> dict | None:
    """Карточка человека для экранов дисциплин: одно чтение, один набор прав."""
    res = await core.api(f"/tools/participants/{participant_id}/card", None,
                         cb.message.chat.id, method="GET")
    return res["data"] if res.get("ok") else None


@router.callback_query(F.data.startswith("panel:mroles:"))
async def panel_person_master(cb: CallbackQuery) -> None:
    """Дисциплины педагога: отметить предмет, выдать или снять роль (140 §4.1–4.3).

    Кнопка «Педагог» ведёт сюда: дисциплину можно отметить до выдачи роли, но роль
    выдаётся и без неё — сервер отвечает предупреждением `master_without_discipline`
    (V-037, 08.11.2026). Здесь же снимается предметная связь — роль при этом не
    отзывается (140 §1).
    """
    participant_id = _pid_of(cb.data)
    if participant_id is None:
        await cb.answer("Непонятный человек", show_alert=True)
        return
    card = await _card_of(cb, participant_id)
    catalog = await core.api("/tools/disciplines", None, cb.message.chat.id, method="GET")
    if card is None or not catalog.get("ok"):
        await cb.answer("Нет доступа", show_alert=True)
        return
    have = {item["code"]: item["name"] for item in (card.get("disciplines") or [])}
    is_master = "master" in (card.get("roles") or [])
    rows = [[(f"{'✓ ' if item['code'] in have else '· '}{texts._fit(item['name'], 28)}",
              f"panel:mrel:{participant_id}:{item['code']}:{0 if item['code'] in have else 1}")]
            for item in catalog.get("disciplines", [])]
    if not is_master:
        rows.append([("Выдать роль педагога", f"panel:mgrant:{participant_id}")])
    if is_master:
        rows.append([("Снять роль педагога",
                      f"panel:role:{participant_id}:master:0")])
    lines = [f"{card.get('display_name')}: дисциплины педагога. "
             "Нажатие отмечает или снимает предмет.", ""]
    lines.append("Отмечено: " + ", ".join(have.values()) if have
                 else "Дисциплин нет: тренинг назначить не сможет.")
    await cb.message.edit_text(
        "\n".join(lines), parse_mode=None,
        reply_markup=keyboards.kb(rows + [[("К ролям", f"panel:proles:{participant_id}")],
                                [(texts.BTN_BACK, "panel:root")]]))
    await cb.answer()


@router.callback_query(F.data.startswith("panel:mrel:"))
async def panel_person_master_link(cb: CallbackQuery) -> None:
    """Отметить или снять дисциплину: путь записи — `/admin/participant/discipline`."""
    parts = (cb.data or "").split(":")
    if len(parts) != 5:
        await cb.answer("Непонятное действие", show_alert=True)
        return
    _, _, participant_id, code, flag = parts
    res = await core.api("/admin/participant/discipline",
                         {"participant_id": int(participant_id), "discipline": code,
                          "enabled": flag == "1"}, cb.message.chat.id)
    if not res.get("ok"):
        await cb.answer("Не удалось: " + errors.error_text(res), show_alert=True)
        return
    # После снятия последней дисциплины сервер отвечает предупреждением, а не
    # отказом: человек видит последствие словами (V-037, D2).
    warnings = res.get("warnings") or []
    if warnings:
        await cb.answer(warnings[0].get("text") or "Дисциплина снята", show_alert=True)
    else:
        await cb.answer("Дисциплина отмечена" if flag == "1" else "Дисциплина снята")
    await panel_person_master(cb)


@router.callback_query(F.data.startswith("panel:mgrant:"))
async def panel_person_master_grant(cb: CallbackQuery) -> None:
    """Роль педагога выдаётся с уже отмеченными дисциплинами, а не пустой."""
    participant_id = _pid_of(cb.data)
    if participant_id is None:
        await cb.answer("Непонятный человек", show_alert=True)
        return
    card = await _card_of(cb, participant_id)
    if card is None:
        await cb.answer("Карточка недоступна", show_alert=True)
        return
    codes = [item["code"] for item in (card.get("disciplines") or [])]
    payload = {"participant_id": participant_id, "role": "master", "enabled": True}
    # Дисциплины необязательны: роль выдаётся и без них, ответ несёт предупреждение
    if codes:
        payload["disciplines"] = codes
    res = await core.api("/admin/grant", payload, cb.message.chat.id)
    if not res.get("ok"):
        await cb.answer("Не удалось: " + errors.error_text(res), show_alert=True)
        return
    warnings = res.get("warnings") or []
    if warnings:
        await cb.answer("Роль педагога выдана. "
                        + (warnings[0].get("text") or ""), show_alert=True)
    else:
        await cb.answer("Роль педагога выдана")
    await panel_person_roles(cb)


@router.callback_query(F.data.startswith("panel:caps:"))
async def panel_person_caps(cb: CallbackQuery) -> None:
    """Возможности человека кнопками: у каждой — вкл/выкл, без /grant."""
    participant_id = _pid_of(cb.data)
    if participant_id is None:
        await cb.answer("Непонятный человек", show_alert=True)
        return
    card = await core.api(f"/tools/participants/{participant_id}/card", None,
                     cb.message.chat.id, method="GET")
    catalog = await core.api("/admin/capabilities", None, cb.message.chat.id, method="GET")
    if not card.get("ok") or not catalog.get("ok"):
        await cb.answer("Нет доступа", show_alert=True)
        return
    have = set(card["data"].get("capabilities") or [])
    rows = []
    for item in catalog.get("capabilities", []):
        code = item["code"]
        mark = "✓ " if code in have else "· "
        rows.append([(f"{mark}{item['title']}",
                      f"panel:cap:{participant_id}:{code}:{0 if code in have else 1}")])
    rows = rows[:60]
    await cb.message.edit_text(
        f"{card['data'].get('display_name')}: возможности. Нажатие выдаёт или отзывает.",
        parse_mode=None,
        reply_markup=keyboards.kb(rows + [[("К карточке", f"panel:person:{participant_id}")],
                                [(texts.BTN_BACK, "panel:root")]]))
    await cb.answer()


@router.callback_query(F.data.startswith("panel:cap:"))
async def panel_person_cap_toggle(cb: CallbackQuery) -> None:
    parts = (cb.data or "").split(":")
    if len(parts) != 5:
        await cb.answer("Непонятное действие", show_alert=True)
        return
    _, _, participant_id, code, flag = parts
    res = await core.api("/admin/grant", {"participant_id": int(participant_id),
                                     "capability": code, "allowed": flag == "1"},
                    cb.message.chat.id)
    if not res.get("ok"):
        await cb.answer("Не удалось: " + errors.error_text(res),
                        show_alert=True)
        return
    await cb.answer("Сохранено")
    await panel_person_caps(cb)


@router.callback_query(F.data.startswith("panel:tariff:"))
async def panel_person_tariff(cb: CallbackQuery, state: FSMContext) -> None:
    """Тариф взноса: бот спрашивает сумму, команду печатать не нужно.

    Право то же, что у пути записи (`budget.write`, Д8, FR-B7): без него не
    спрашиваем сумму, иначе человек введёт её и получит отказ на сохранении.
    """
    if await _denied(cb, "budget.write"):
        return
    participant_id = _pid_of(cb.data)
    if participant_id is None:
        await cb.answer("Непонятный человек", show_alert=True)
        return
    card = await core.api(f"/tools/participants/{participant_id}/card", None,
                     cb.message.chat.id, method="GET")
    if not card.get("ok"):
        await cb.answer("Карточка недоступна", show_alert=True)
        return
    tariff = (card["data"].get("tariff") or {}).get("tariff")
    await state.set_state(PanelEdit.tariff)
    await state.update_data(participant_id=participant_id)
    await cb.message.answer(
        f"Тариф взноса для «{card['data'].get('display_name')}»: "
        f"сейчас {('не назначен' if tariff is None else f'{int(tariff)} ₽{texts.PER_MONTH}')}.\n"
        "Напишите сумму в рублях; 0 — снять тариф. Новый тариф действует со следующего месяца.")
    await cb.answer()


@router.message(PanelEdit.tariff, F.text)
async def panel_tariff_save(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    participant_id = data.get("participant_id")
    raw = (message.text or "").strip().replace(" ", "").replace(",", ".")
    try:
        amount = float(raw)
    except ValueError:
        await message.answer("Нужно число в рублях, например 3000. Отмена — /menu.")
        return
    if amount < 0:
        await message.answer("Сумма не может быть отрицательной. Напишите число или 0.")
        return
    res = await core.api(f"/tools/participants/{participant_id}/dues-tariff",
                    {"tariff": None if amount == 0 else amount}, message.chat.id)
    await state.clear()
    if not res.get("ok"):
        await message.answer("Не удалось сохранить тариф: "
                             + errors.error_text(res))
        return
    await message.answer("Тариф снят." if amount == 0 else f"{texts.PREFIX_TARIFF}{int(amount)} ₽{texts.PER_MONTH}, "
                         "со следующего месяца.")


@router.callback_query(F.data == "panel:pending")
async def panel_pending(cb: CallbackQuery) -> None:
    res = await core.api("/admin/pending", None, cb.message.chat.id, method="GET")
    if not res.get("ok"):
        await cb.message.edit_text(
            "Не удалось прочитать заявки: " + errors.error_text(res),
            parse_mode=None, reply_markup=keyboards.kb([[(texts.BTN_BACK, "panel:root")]]))
        await cb.answer()
        return
    rows = res.get("data", [])
    if not rows:
        await cb.message.edit_text("Заявок нет.",
                                   reply_markup=keyboards.kb([[(texts.BTN_BACK, "panel:root")]]))
        await cb.answer()
        return
    p = rows[0]
    await cb.message.edit_text(
        f"{texts.PREFIX_REQUEST}{p['display_name']} — {texts.ROLE_TITLES.get(p['role'], p['role'])}",
        reply_markup=keyboards.kb([[(texts.BTN_CONFIRM, f"adm:ok:{p['id']}"),
                          (texts.BTN_REJECT, f"adm:no:{p['id']}")],
                         [(texts.BTN_BACK, "panel:root")]]))
    await cb.answer()


@router.callback_query(F.data == "panel:rights")
async def panel_rights(cb: CallbackQuery) -> None:
    """Роли и число возможностей (Л4, FR-A6).

    Вход — по `participants.manage`, как у выдачи прав (`/admin/grant`): экран
    Mini App закрыт этим же правом, и чат не должен показывать вход тому, кто
    получит отказ. Кнопка из старого меню тоже отказывает здесь, а не в API.
    """
    if await _denied(cb, "participants.manage"):
        return
    res = await core.api("/admin/capabilities", None, cb.message.chat.id, method="GET")
    if not res.get("ok"):
        await cb.answer("Нет доступа", show_alert=True)
        return
    lines = ["Роли и количество возможностей:"]
    for m in res.get("matrix", []):
        lines.append(f"{texts.ROLE_TITLES.get(m['role'], m['role'])} — {len(m['caps'])}")
    lines += ["", "Точечная выдача — в карточке человека: «Возможности» и «Роли»."]
    await cb.message.edit_text("\n".join(lines),
                               reply_markup=keyboards.kb([[(texts.BTN_BACK, "panel:root")]]))
    await cb.answer()


@router.callback_query(F.data == "panel:invite")
async def panel_invite(cb: CallbackQuery) -> None:
    """Шаг 1: выбираем роль, для которой выпускаем код."""
    cap = await core.caps_of(cb.message.chat.id)
    if "registration.code.issue" not in cap and "*" not in cap and "participants.manage" not in cap:
        await cb.answer("Нет права выпускать коды", show_alert=True)
        return
    await cb.message.edit_text(
        "Кому выпустить код?\n\n"
        "Код одноразовый, действует 7 дней. Человек вводит его боту командой /start "
        "или в ответ на запрос кода и попадает в систему с выбранной ролью.",
        parse_mode=None,
        reply_markup=keyboards.kb([[("Организатор", "inv:role:org"), ("Актёр труппы", "inv:role:troupe")],
                         [(texts.BTN_BACK, "panel:root")]]))
    await cb.answer()


@router.callback_query(F.data.startswith("inv:role:"))
async def invite_issue(cb: CallbackQuery) -> None:
    """Шаг 2: выпускаем код и отдаём его человеку текстом."""
    parts = (cb.data or "").split(":")
    role = parts[2] if len(parts) > 2 else ""
    if role not in ("org", "troupe"):
        await cb.answer("Неизвестная роль", show_alert=True)
        return
    res = await core.api("/admin/invite", {"role": role, "ttl_days": 7}, cb.message.chat.id)
    if not res.get("ok"):
        await cb.message.edit_text(
            "Не удалось выпустить код: " + errors.error_text(res),
            parse_mode=None,
            reply_markup=keyboards.kb([[(texts.BTN_BACK, "panel:root")]]))
        await cb.answer()
        return
    code = str(res.get("code") or "")
    await cb.message.edit_text(
        texts.invite_text(code, res.get("expires_at"), role),
        parse_mode="HTML",
        reply_markup=keyboards.copy_code_kb(code, [[("Ещё код", "panel:invite")],
                                         [(texts.TITLE_CODES, "panel:codes")],
                                         [(texts.BTN_BACK, "panel:root")]]))
    log.info("выпущен инвайт-код role=%s by=%s", role, cb.message.chat.id)
    await cb.answer("Код выпущен")


async def _send_codes(cb: CallbackQuery) -> None:
    """Нарисовать открытые коды, не отвечая на callback (ответ — у обработчика)."""
    res = await core.api("/admin/invites", None, cb.message.chat.id, method="GET")
    if not res.get("ok"):
        await cb.message.edit_text(
            "Не удалось прочитать коды: " + errors.error_text(res),
            parse_mode=None, reply_markup=keyboards.kb([[(texts.BTN_BACK, "panel:root")]]))
        return
    codes = res.get("data") or []
    back = [[(texts.BTN_BACK, "panel:root")]]
    if not codes:
        await cb.message.edit_text(
            "Открытых кодов нет. Выпустить новый — «Выпустить инвайт-код».",
            parse_mode=None,
            reply_markup=keyboards.kb([[(texts.TITLE_INVITES, "panel:invite")]] + back))
        return
    lines = [f"Открытые коды: {len(codes)}. Код действует до срока и до первого "
             "использования; «Отозвать» закрывает его сразу.", ""]
    for item in codes[:10]:
        role = texts.ROLE_TITLES.get(item.get("role"), item.get("role"))
        until = str(item.get("expires_at") or "")[:10]
        lines.append(f"{item['code']} — {role}, до {until}, "
                     f"использован {item.get('uses', 0)} из {item.get('max_uses', 1)}")
    buttons = [[(f"{texts.BTN_REVOKE} {item['code']}", f"inv:revoke:{item['code']}")]
               for item in codes[:10]]
    buttons.append([(texts.TITLE_INVITES, "panel:invite")])
    buttons += back
    await cb.message.edit_text("\n".join(lines), parse_mode=None,
                               reply_markup=keyboards.kb(buttons))


@router.callback_query(F.data == "panel:codes")
async def panel_codes(cb: CallbackQuery) -> None:
    """Открытые инвайт-коды и отзыв кнопкой (AUD-3, Л8, FR-A2).

    Раньше отзыв существовал только на экране Mini App: `POST /admin/invite/revoke`
    никто не звал из чата. Право то же, что у пути записи, —
    `registration.code.issue`.
    """
    if await _denied(cb, "registration.code.issue"):
        return
    await _send_codes(cb)
    await cb.answer()


@router.callback_query(F.data.startswith("inv:revoke:"))
async def invite_revoke(cb: CallbackQuery) -> None:
    """Отзыв кода: путь записи `POST /admin/invite/revoke` (Л8)."""
    if await _denied(cb, "registration.code.issue"):
        return
    parts = (cb.data or "").split(":", 2)
    code = parts[2] if len(parts) == 3 else ""
    if not code:
        await cb.answer("Непонятный код", show_alert=True)
        return
    res = await core.api("/admin/invite/revoke", {"code": code}, cb.message.chat.id)
    if not res.get("ok"):
        await cb.answer("Не удалось отозвать: " + errors.error_text(res), show_alert=True)
        return
    log.info("отозван инвайт-код %s by=%s", code, cb.message.chat.id)
    await cb.answer("Код отозван")
    await _send_codes(cb)


@router.callback_query(F.data.startswith("panel:money"))
async def panel_money(cb: CallbackQuery) -> None:
    """Операции бюджета за месяц (Ш-32).

    Без периода — текущий месяц (как раньше). Кнопки «Предыдущий/Следующий
    месяц» передают период в callback ``panel:money:YYYY-MM``, поэтому
    прошлые месяцы теперь доступны без отдельного экрана.
    """
    parts = (cb.data or "").split(":")
    asked = parts[2] if len(parts) > 2 else ""
    period = asked if money_periods.is_period(asked) else None
    path = "/tools/budget/recent?limit=5" + (f"&period={period}" if period else "")
    res = await core.api(path, None, cb.message.chat.id, method="GET")
    if not res.get("ok"):
        await cb.message.edit_text(
            "Не удалось прочитать операции: " + errors.error_text(res),
            parse_mode=None, reply_markup=keyboards.kb([[(texts.BTN_BACK, "panel:root")]]))
        await cb.answer()
        return
    shown = res.get("period") or ""
    period = res.get("period_title") or shown or "текущий месяц"
    rows = res.get("data", [])
    caps = await core.caps_of(cb.message.chat.id)
    nav_rows = list(money_periods.money_nav_rows(shown, res.get("first_period"), res.get("current_period")))
    if "budget.write" in caps or "*" in caps:
        # Ш-37: строки, до листа не дошедшие, дописываются прямо с этого экрана.
        nav_rows.insert(-1, [(texts.BTN_SHEET_RETRY, "panel:sheet_retry")])
    markup = keyboards.kb(nav_rows)
    if not rows:
        await cb.message.edit_text(f"За {period} операций нет.",
                                   parse_mode=None, reply_markup=markup)
        await cb.answer()
        return
    lines = [f"Последние операции за {period}:"]
    for r in rows:
        sign = "+" if r["tx_type"] == "income" else "−"
        lines.append(f"{r['tx_date']} {sign}{texts.money(float(r['amount']))} — {r['category']}")
    await cb.message.edit_text("\n".join(lines), parse_mode=None, reply_markup=markup)
    await cb.answer()


@router.callback_query(F.data == "panel:reconcile_apply")
async def reconcile_apply(cb: CallbackQuery) -> None:
    """Применить сверку: строки из листа добавляются, изменённые обновляются."""
    await cb.answer("Применяю…")
    res = await core.api("/admin/reconcile", {"apply": True, "scope": "all"}, cb.message.chat.id)
    text = str(res.get("text") or ("Сверка применена." if res.get("ok") else "Не удалось."))
    await cb.message.edit_text(text[:3800], parse_mode=None)


@router.callback_query(F.data == "panel:sheet_retry")
async def panel_sheet_retry(cb: CallbackQuery) -> None:
    """Дописать в лист строки, у которых запись упала (Ш-37, C-3)."""
    await cb.answer("Дописываю…")
    res = await core.api("/tools/budget/sheet-retry", {"limit": 50}, cb.message.chat.id)
    await cb.message.edit_text(
        texts.sheet_retry_text(res), parse_mode=None,
        reply_markup=keyboards.kb([[("К операциям", "panel:money")], [(texts.BTN_BACK, "panel:root")]]))
    await cb.answer(texts.BTN_DONE if res.get("ok") else "Не вышло")


@router.callback_query(F.data == "panel:feedback")
async def panel_feedback(cb: CallbackQuery) -> None:
    """
    Обращения участников: кто, что и когда написал.

    Читаем из базы через API и печатаем абзацами — так удобнее читать в чате,
    чем таблицей. Показываем только открытые: новые, в работе и в плане. Закрытые
    не показываем — они остаются в счётчиках под списком.
    """
    res = await core.api("/tools/feedback?open_only=true&limit=10", None, cb.message.chat.id,
                    method="GET")
    if not res.get("ok"):
        await cb.answer("Нужны права на разбор обращений", show_alert=True)
        return
    items = res.get("data") or []
    if not items:
        await cb.message.edit_text("Открытых обращений нет.",
                                   reply_markup=keyboards.kb([[(texts.BTN_BACK, "panel:root")]]))
        await cb.answer()
        return

    statuses = {"new": "новое", "seen": texts.STATUS_IN_PROGRESS, "planned": "в плане",
                "done": texts.STATUS_DONE, "rejected": texts.STATUS_REJECTED}
    kinds = {"bug": "проблема", "wish": "пожелание", "wish-fl": "просьба о доработке",
             "question": "вопрос", "praise": "хорошо работает"}
    blocks = []
    for item in items:
        when = str(item["created_at"])[:16].replace("T", " ")
        head = (f"№{item['id']} · {kinds.get(item['kind'], item['kind'])} · "
                f"{statuses.get(item['status'], item['status'])}")
        blocks.append(head + "\n"
                      + f"{item.get('author') or texts.ROLE_PARTICIPANT}, {when}\n"
                      + item["text"][:700])
    counts = res.get("counts") or {}
    summary = ", ".join(f"{statuses.get(k, k)}: {v}" for k, v in counts.items())
    text = "\n\n".join(blocks)[:3800]
    await cb.message.edit_text(
        text + ("\n\nПо всем статусам: " + summary if summary else ""),
        parse_mode=None,
        reply_markup=keyboards.kb([[(texts.BTN_BACK, "panel:root")]]))
    await cb.answer()


@router.callback_query(F.data == "panel:audit")
async def panel_audit(cb: CallbackQuery) -> None:
    res = await core.api("/tools/audit/recent?limit=12", None, cb.message.chat.id, method="GET")
    if not res.get("ok"):
        await cb.message.edit_text(
            "Не удалось прочитать аудит: " + errors.error_text(res),
            parse_mode=None, reply_markup=keyboards.kb([[(texts.BTN_BACK, "panel:root")]]))
        await cb.answer()
        return
    rows = res.get("data", [])
    if not rows:
        await cb.message.edit_text(
            "Событий пока нет.", parse_mode=None,
            reply_markup=keyboards.kb([[(texts.BTN_BACK, "panel:root")]]))
        await cb.answer()
        return
    await cb.message.edit_text(
        "Аудит дня:\n" + texts.audit_table(rows), parse_mode="HTML",
        reply_markup=keyboards.kb([[(texts.BTN_BACK, "panel:root")]]))
    await cb.answer()


@router.callback_query(F.data == "panel:health")
async def panel_health(cb: CallbackQuery) -> None:
    res = await core.api("/health", None, cb.message.chat.id, method="GET")
    await cb.message.edit_text(
        f"tool-api: {'ok' if res.get('ok') else 'недоступен'}\n"
        f"БД: {res.get('db')}\nУчастников: {res.get('participants')}\n"
        f"Время: {res.get('time')}",
        reply_markup=keyboards.kb([[(texts.BTN_BACK, "panel:root")]]))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:"))
async def admin_decision(cb: CallbackQuery) -> None:
    parts = (cb.data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit() or parts[1] not in ("ok", "no"):
        await cb.answer("Кнопка устарела, откройте заявки заново", show_alert=True)
        return
    _, action, pid = parts
    res = await core.api("/admin/approve", {"participant_id": int(pid), "approve": action == "ok"},
                    cb.message.chat.id)
    if res.get("ok"):
        await cb.message.edit_text("Заявка обработана.",
                                   reply_markup=keyboards.kb([[(texts.BTN_BACK, "panel:root")]]))
        await cb.answer(texts.BTN_DONE)
    else:
        await cb.answer("Не удалось", show_alert=True)


@router.callback_query(F.data == "panel:reconcile")
async def panel_reconcile(cb: CallbackQuery) -> None:
    await cb.answer("Читаю файл и базу…")
    res = await core.api("/admin/reconcile", {"apply": False, "scope": "all"}, cb.message.chat.id)
    if not res.get("ok"):
        await cb.message.edit_text("Сверка не прошла.\n\n" + str(res.get("text"))[:3000],
                                   reply_markup=keyboards.kb([[(texts.BTN_BACK, "panel:root")]]))
        return
    await cb.message.edit_text(
        "Сверка (только просмотр):\n\n" + str(res.get("text"))[:3000],
        reply_markup=keyboards.kb([[("Применить к базе", "rec:apply")], [(texts.BTN_BACK, "panel:root")]]))


@router.callback_query(F.data == "rec:apply")
async def reconcile_apply_panel(cb: CallbackQuery) -> None:
    await cb.answer("Применяю…")
    res = await core.api("/admin/reconcile", {"apply": True, "scope": "all"}, cb.message.chat.id)
    await cb.message.edit_text(
        ("Сверка применена:\n\n" if res.get("ok") else "Ошибка сверки:\n\n")
        + str(res.get("text"))[:3000],
        reply_markup=keyboards.kb([[(texts.BTN_BACK, "panel:root")]]))
