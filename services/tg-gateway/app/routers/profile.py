"""Роутер профиля: своя анкета, роли, взносы."""

from __future__ import annotations

import core
import keyboards
import texts
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from webapp_links import keyboard as webapp_keyboard

from config import WEBAPP_BASE_URL

router = Router()


async def answer_profile(message: Message) -> None:
    """Мой профиль: что бот знает о человеке и что ему доступно (обращение №19)."""
    res = await core.api("/tools/participants/me", None, message.chat.id, method="GET")
    if not res.get("ok"):
        await message.answer("Профиль не найден. Напишите /start — начнём с регистрации.")
        return
    p = res["data"]
    roles = ", ".join(texts.ROLE_TITLES.get(r, r) for r in (p.get("roles") or [p.get("role")]))
    caps = p.get("capabilities") or []
    lines = [p["display_name"], f"{texts.PREFIX_ROLES}{roles}", ""]
    if caps:
        lines.append("Могу: " + ", ".join(texts.CAP_TITLES.get(c, c) for c in caps))
    else:
        lines.append("Возможностей пока нет — их выдаёт организатор.")
    dues = await core.api("/tools/dues/me", None, message.chat.id, method="GET")
    if dues.get("ok"):
        lines += ["", texts.dues_text(dues)]
    await message.answer("\n".join(lines)[:3800])


async def show_dues(message: Message) -> None:
    """Взносы человека: тариф, начислено и оплачено по периодам."""
    res = await core.api("/tools/dues/me", None, message.chat.id, method="GET")
    if not res.get("ok"):
        await message.answer(texts.DUES_DENIED, reply_markup=keyboards.MAIN_MENU)
        return
    await message.answer(texts.dues_text(res), reply_markup=keyboards.MAIN_MENU)


@router.message(Command("dues"))
async def cmd_dues(message: Message) -> None:
    res = await core.api("/tools/dues/me", None, message.chat.id, method="GET")
    if not res.get("ok"):
        await message.answer(texts.DUES_DENIED)
        return
    await message.answer(texts.dues_text(res, limit=8))


@router.message(Command("profile"))
async def profile_cmd(message: Message) -> None:
    """
    Своя анкета: время в пути и роли. Правка времени — прямо в чате,
    роли — в Mini App (когда появится HTTPS) или через админа.
    """
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1 and parts[1].strip().isdigit():
        minutes = int(parts[1].strip())
        res = await core.api("/tools/profile/me", {"travel_minutes": minutes}, message.chat.id)
        if res.get("ok"):
            await message.answer(f"{texts.PREFIX_WRITTEN}дорога до театра {minutes} мин. "
                                 f"Окна теперь считаются с этим временем.")
        else:
            await message.answer("Не получилось сохранить. Дорога должна быть от 0 до 240 минут.")
        return

    res = await core.api("/tools/profile/me", None, message.chat.id, method="GET")
    if not res.get("ok"):
        await message.answer("Не получилось прочитать анкету. Попробуйте позже.")
        return

    lines = [f"Анкета: {res['name']}", "",
             f"Дорога до театра: {res['travel_minutes']} мин"]
    if res.get("dues_tariff"):
        since = res.get("dues_tariff_from")
        lines.append(f"Взнос: {texts.money(float(res['dues_tariff']))}{texts.PER_MONTH}"
                     + (f" (с {since})" if since else ""))
    else:
        lines.append("Взнос: тариф не назначен")
    roles = res.get("roles") or []
    if roles:
        lines.append("Роли:")
        for role in roles:
            project = role.get("project_name") or "без спектакля"
            lines.append(f"  {role['alias']} — {project}")
    else:
        lines.append(texts.PREFIX_ROLES + "не заполнены")

    lines += ["", "Изменить дорогу: /profile 45"]
    app_kb = webapp_keyboard(texts.BTN_OPEN_PROFILE, "/webapp", WEBAPP_BASE_URL)
    if app_kb is None:
        lines.append("Роли заполняются через администратора: анкета Mini App "
                     "откроется, когда бот переедет на сервер.")
    else:
        lines.append("Роли, телефон и спектакли — в анкете.")
    await message.answer("\n".join(lines)[:3800], reply_markup=app_kb)


# ------------------------------------------------------------------ прочее
@router.callback_query(F.data == "sar:dues")
async def my_dues(cb: CallbackQuery) -> None:
    res = await core.api("/tools/dues/me", None, cb.message.chat.id, method="GET")
    if not res.get("ok"):
        await cb.answer("Взносы доступны участнику студии", show_alert=True)
        return
    await cb.message.edit_text(texts.dues_text(res))
    await cb.answer()
