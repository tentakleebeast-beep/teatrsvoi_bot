"""Роутер знакомства (№17): вопросы профиля и запись одним запросом."""

from __future__ import annotations

import core
import intro
import keyboards
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from states import Intro

import routers.menu as menu_router

router = Router()


async def intro_ask(message: Message, state: FSMContext, step: str,
                    skippable: bool = True) -> None:
    await state.set_state(getattr(Intro, step).state)
    await message.answer(intro.question(step),
                         reply_markup=keyboards.intro_skip_kb() if skippable else None)


async def intro_start(message: Message, chat_id: int, state: FSMContext) -> None:
    """Начать знакомство при входе: план — из трёх обязательных полей.

    Известное не переспрашиваем: имя — только если в записи одно слово, телефон —
    если он пуст. Короткое имя нужно всегда (docs/development/182 §2.2).
    """
    res = await core.api("/tools/profile/me", None, chat_id, method="GET")
    profile = res if res.get("ok") else {}
    plan = intro.build_entry_plan(profile)
    await state.update_data(plan=plan, answers={}, mode="entry")
    await state.set_state(getattr(Intro, plan[0]).state)
    await message.answer(intro.INTRO_HEAD_ENTRY + "\n\n" + intro.question(plan[0]),
                         reply_markup=keyboards.intro_skip_kb())


async def intro_fill(message: Message, state: FSMContext) -> None:
    """Дозаполнение по кнопке «Знакомство»: только пустые необязательные поля."""
    res = await core.api("/tools/profile/me", None, message.chat.id, method="GET")
    if not res.get("ok"):
        await message.answer("Сначала регистрация: /start")
        return
    plan = intro.build_fill_plan(res)
    if not plan:
        await message.answer(intro.FILL_NOTHING)
        return
    await state.update_data(plan=plan, answers={}, mode="fill")
    await state.set_state(getattr(Intro, plan[0]).state)
    await message.answer(intro.INTRO_HEAD_FILL + "\n\n" + intro.question(plan[0]),
                         reply_markup=keyboards.intro_skip_kb())


async def intro_finish(message: Message, state: FSMContext) -> None:
    """Ответы собраны — показываем сводку и ждём подтверждения.

    В профиль до «Записать» не уходит ничего: решение V-009.
    """
    data = await state.get_data()
    answers = data.get("answers") or {}
    await state.set_state(Intro.confirm.state)
    await message.answer(intro.summary(answers), reply_markup=keyboards.intro_confirm_kb())


async def intro_answer(message: Message, state: FSMContext, step: str) -> None:
    """Ответ на текущий вопрос: копим в состоянии и идём дальше.

    Профиль пишется одним запросом после сводки, поэтому человек может прервать
    знакомство на середине — тогда не запишется ничего недосмотренного.
    """
    text = message.text or ""
    chat_id = message.chat.id
    if text.strip() in keyboards.MENU_SURFACE:
        # Ш-52: любое состояние имеет выход — кнопки меню не становятся ответом
        await state.clear()
        await menu_router.menu_action(message, state)
        return
    data = await state.get_data()
    plan = data.get("plan") or []
    if not plan:
        await state.clear()
        await message.answer("Начните заново: /intro")
        return
    answers = dict(data.get("answers") or {})
    if step == "alias" and intro.is_skip(text):
        # Короткое имя обязательно при входе: «Пропустить» повторяет вопрос.
        await message.answer(intro.RETRY["alias"])
        return
    if not intro.is_skip(text):
        patch = intro.parse_step(step, text)
        if not patch and step in intro.LLM_STEPS:
            # Правила промолчали — спрашиваем модель, но только про одно поле шага.
            # Нет ключа, квота или сбой: ответ тот же, что и без LLM, — переспрос.
            llm = await core.api("/tools/profile/intro-parse",
                            {"step": step, "text": text}, chat_id)
            if llm.get("ok"):
                patch = intro.sanitize_patch(llm.get("patch"), step)
        if not patch:
            await message.answer(intro.RETRY[step])
            return
        answers[step] = next(iter(patch.values()))
        await state.update_data(answers=answers)
    follow = intro.next_step(plan, step)
    if follow:
        await intro_ask(message, state, follow, skippable=follow != "alias")
        return
    await intro_finish(message, state)


async def intro_skip_current(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    data = await state.get_data()
    plan = data.get("plan") or []
    if current == Intro.confirm.state:
        await message.answer("Сводка уже показана: «Записать» или «Отменить».",
                             reply_markup=keyboards.intro_confirm_kb())
        return
    step = next((s for s in plan if getattr(Intro, s).state == current), None)
    if step is None:
        await message.answer("Знакомство уже закончено. Начать заново — /intro.")
        return
    follow = intro.next_step(plan, step)
    if follow:
        await intro_ask(message, state, follow)
        return
    await intro_finish(message, state)


@router.message(Command("intro"))
async def cmd_intro(message: Message, state: FSMContext) -> None:
    await intro_start(message, message.chat.id, state)


@router.callback_query(F.data == "intro:save")
async def intro_save(cb: CallbackQuery, state: FSMContext) -> None:
    """Подтверждение сводки: единственная точка записи профиля в знакомстве."""
    await cb.answer("Записываю…")
    data = await state.get_data()
    mode = data.get("mode") or "entry"
    patch = intro.build_patch(data.get("answers") or {})
    if not patch:
        await state.clear()
        await cb.message.edit_text(intro.CANCELLED)
        return
    res = await core.api("/tools/profile/me", patch, cb.message.chat.id)
    if not res.get("ok"):
        await cb.message.edit_text(
            "Не получилось записать. Попробуйте ещё раз или отмените — /intro.",
            reply_markup=keyboards.intro_confirm_kb())
        return
    await state.clear()
    if mode == "fill":
        await cb.message.edit_text(intro.DONE_FILL)
        return
    await cb.message.edit_text(intro.DONE_ENTRY)
    me = await core.api("/tools/participants/me", None, cb.message.chat.id, method="GET")
    if me.get("ok"):
        p = me["data"]
        await menu_router.send_menu(cb.message, cb.message.chat.id, p["display_name"],
                                    p.get("roles") or [p["role"]],
                                    p.get("capabilities") or [], p.get("disciplines"))


@router.callback_query(F.data == "intro:cancel")
async def intro_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    data = await state.get_data()
    mode = data.get("mode") or "entry"
    await state.clear()
    await cb.message.edit_text(intro.CANCELLED)
    if mode == "fill":
        return
    # Человек уже зарегистрирован — оставлять его без меню нельзя.
    me = await core.api("/tools/participants/me", None, cb.message.chat.id, method="GET")
    if me.get("ok"):
        p = me["data"]
        await menu_router.send_menu(cb.message, cb.message.chat.id, p["display_name"],
                                    p.get("roles") or [p["role"]],
                                    p.get("capabilities") or [], p.get("disciplines"))


@router.message(Intro.name, F.text)
async def intro_name(message: Message, state: FSMContext) -> None:
    await intro_answer(message, state, "name")


@router.message(Intro.alias, F.text)
async def intro_alias(message: Message, state: FSMContext) -> None:
    await intro_answer(message, state, "alias")


@router.message(Intro.phone, F.text)
async def intro_phone(message: Message, state: FSMContext) -> None:
    await intro_answer(message, state, "phone")


@router.message(Intro.birthday, F.text)
async def intro_birthday(message: Message, state: FSMContext) -> None:
    await intro_answer(message, state, "birthday")


@router.message(Intro.travel, F.text)
async def intro_travel(message: Message, state: FSMContext) -> None:
    await intro_answer(message, state, "travel")


@router.message(Intro.plays, F.text)
async def intro_plays(message: Message, state: FSMContext) -> None:
    await intro_answer(message, state, "plays")


@router.message(Intro.roles, F.text)
async def intro_roles(message: Message, state: FSMContext) -> None:
    await intro_answer(message, state, "roles")


@router.message(Intro.trainings, F.text)
async def intro_trainings(message: Message, state: FSMContext) -> None:
    await intro_answer(message, state, "trainings")


@router.message(Intro.manifest, F.text)
async def intro_manifest(message: Message, state: FSMContext) -> None:
    await intro_answer(message, state, "manifest")


@router.callback_query(F.data == "intro:skip")
async def intro_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await intro_skip_current(cb.message, state)
