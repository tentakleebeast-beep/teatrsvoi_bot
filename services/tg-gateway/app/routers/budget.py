"""Роутер бюджета: расход и доход, форма операции, категории."""

from __future__ import annotations

import re

import core
import keyboards
import texts
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from states import AvailForm, BudgetForm

import routers.menu as menu_router

router = Router()


async def answer_money(message: Message, text: str = "") -> bool:
    """«Сколько у нас денег» — итоги из БД; если назван проект, показываем по проекту."""
    project = next((name for key, name in texts.PROJECT_WORDS.items() if key in text.lower()), None)
    path = "/tools/budget/summary" + (f"?project={project}" if project else "")
    res = await core.api(path, None, message.chat.id, method="GET")
    if not res.get("ok"):
        await message.answer("Итоги по бюджету доступны организатору и администратору.")
        return True
    header = f"Проект «{project}»:" if project else "Баланс студии:"
    lines = [f"{header} {round(res['balance']):,} ₽".replace(",", " "),
             f"доходы {round(res['income_total']):,} ₽ · расходы {round(res['expense_total']):,} ₽"
             .replace(",", " "), ""]
    for p in res["periods"]:
        sign = "+" if p["result"] >= 0 else "−"
        lines.append(f"{p['period']}: {sign}{abs(round(p['result'])):,} ₽".replace(",", " "))
    await message.answer("\n".join(lines))
    return True


# ------------------------------------------------------------------ бюджет: черновик
@router.callback_query(F.data == "bud:ok")
async def budget_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    d = data.get("bd")
    if not d:
        await cb.answer("Черновик устарел", show_alert=True)
        return
    res = await core.api("/tools/budget/commit", {
        "chat_id": cb.message.chat.id,
        "idempotency_key": f"tg:budget:{cb.message.chat.id}:{cb.message.message_id}",
        "tx_type": d.get("tx_type") or "expense",
        "amount": float(d["amount"]),
        "category": d["category"] if isinstance(d["category"], str) else texts.CATEGORY_OTHER,
        "tx_date": d.get("tx_date"),
        "purpose": d.get("purpose") or data.get("raw"),
        "counterparty": d.get("counterparty"),
        "source_text": data.get("raw"),
    }, cb.message.chat.id)
    if res.get("ok"):
        await state.clear()
        if res.get("deduped"):
            await cb.message.edit_text("Эта операция уже записана — второй раз не создаю.")
            await cb.answer("Уже записано")
            return
        tail = "" if res.get("sheet") == "ok" else "\nВ таблицу не записал, повторит позже."
        await cb.message.edit_text(
            f"Записано: {texts.money(float(d['amount']))} · {d['category']} · {d['tx_date']}."
            f"{tail}"
        )
        await cb.answer(texts.BTN_DONE)
    else:
        await cb.answer(texts.SAVE_FAILED, show_alert=True)


@router.callback_query(F.data.startswith("icat:"))
async def intent_category(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    draft = data.get("bd") or {}
    if not draft.get("amount"):
        await cb.answer("Черновик устарел", show_alert=True)
        return
    val = cb.data.split(":", 1)[1]
    if val == "other":
        await state.set_state(BudgetForm.category)
        await cb.message.edit_text("Напишите название категории:")
        await cb.answer()
        return
    cats = data.get("cats") or [texts.CATEGORY_OTHER]
    if not val.isdigit() or int(val) >= len(cats):
        await cb.answer("Список категорий устарел, начните заново", show_alert=True)
        return
    category = cats[int(val)]
    draft["category"] = category
    await state.update_data(bd=draft)
    await cb.message.edit_text(
        f"Черновик операции:\n{texts.money(float(draft['amount']))} · {category} · "
        f"{draft.get('tx_date')}\n\nЗаписать?",
        reply_markup=keyboards.kb([[(texts.BTN_SAVE, "bud:ok"), (texts.BTN_CANCEL, "bud:no")]]))
    await cb.answer()


@router.callback_query(F.data.startswith("bud:type:"))
async def budget_type(cb: CallbackQuery, state: FSMContext) -> None:
    parts = (cb.data or "").split(":")
    kind = parts[2] if len(parts) > 2 else ""
    if kind not in ("income", "expense"):
        await cb.answer(texts.STALE_BUTTON, show_alert=True)
        return
    data = await state.get_data()
    d = data.get("bd") or {}
    if not d.get("amount"):
        await cb.answer("Черновик устарел", show_alert=True)
        return
    d["tx_type"] = kind
    if not d.get("category"):
        d["category"] = texts.CATEGORY_OTHER
    await state.update_data(bd=d)
    await cb.message.edit_text(
        f"{texts.money(float(d['amount']))} · {texts.WORD_INCOME if kind == 'income' else texts.WORD_EXPENSE} · "
        f"{d['category']}\n\nЗаписать?",
        reply_markup=keyboards.kb([[(texts.BTN_SAVE, "bud:ok"), (texts.BTN_CANCEL, "bud:no")]]))
    await cb.answer()


@router.callback_query(F.data == "bud:no")
async def budget_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text(texts.CANCELLED)
    await cb.answer()


@router.callback_query(F.data == "bud:fix")
async def budget_fix(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BudgetForm.amount)
    await cb.message.edit_text("Введите сумму цифрами:")
    await cb.answer()


# ------------------------------------------------------------------ форма бюджета
@router.callback_query(F.data.startswith("form:"))
async def form_start(cb: CallbackQuery, state: FSMContext) -> None:
    kind = cb.data.split(":")[1]
    if kind == "busy":
        await state.clear()
        await state.set_state(AvailForm.day)
        await cb.message.edit_text(
            texts.QUESTION_WHEN, reply_markup=keyboards.kb([[(texts.BTN_TODAY, "av:day:0"), (texts.BTN_TOMORROW, "av:day:1")],
                                       [(texts.BTN_OTHER_DATE, "av:day:other")]]))
        await cb.answer()
        return

    await state.clear()
    await state.update_data(tx_type="income" if kind == "income" else "expense")
    await state.set_state(BudgetForm.amount)
    await cb.message.edit_text("Сумма цифрами, например: 3000")
    await cb.answer()


@router.message(BudgetForm.amount, F.text)
async def form_amount(message: Message, state: FSMContext) -> None:
    # кнопка меню внутри формы — это команда, а не сумма (найдено smoke-тестом)
    if message.text.strip() in keyboards.MENU_SURFACE:
        await menu_router.menu_action(message, state)
        return
    digits = re.sub(r"[^\d.,]", "", message.text.replace(" ", ""))
    try:
        amount = float(digits.replace(",", "."))
    except ValueError:
        await state.clear()
        await message.answer(
            "Не понял сумму. Попробуйте ещё раз или обратитесь к разработчику — "
            "форма закрыта, начать заново можно кнопкой «Расход».")
        return
    await state.update_data(amount=amount)

    res = await core.api("/tools/categories", None, message.chat.id, method="GET")
    direction = (await state.get_data()).get("tx_type", "expense")
    names = [c["name"] for c in res.get("data", [])
             if c["direction"] in (direction, "both")][:9]
    await state.update_data(cats=names)
    await state.set_state(BudgetForm.category)
    rows = [[(n, f"cat:{i}")] for i, n in enumerate(names)]
    rows.append([("Другая категория", "cat:other")])
    await message.answer("Категория:", reply_markup=keyboards.kb(rows))


@router.callback_query(F.data.startswith("cat:"))
async def form_category(cb: CallbackQuery, state: FSMContext) -> None:
    val = cb.data.split(":", 1)[1]
    data = await state.get_data()
    if val == "other":
        await state.set_state(BudgetForm.category)
        await cb.message.edit_text("Напишите название категории:")
        await cb.answer()
        return
    cats = data.get("cats") or []
    if not data.get("amount") or not val.isdigit() or int(val) >= len(cats):
        await cb.answer("Форма устарела, начните заново", show_alert=True)
        return
    cat = cats[int(val)]
    await state.update_data(category=cat)
    await state.set_state(BudgetForm.confirm)
    tx_type = texts.WORD_INCOME if data.get("tx_type") == "income" else texts.WORD_EXPENSE
    await cb.message.edit_text(
        f"{tx_type.capitalize()}: {texts.money(float(data['amount']))} · {cat}\n\nЗаписать?",
        reply_markup=keyboards.kb([[(texts.BTN_SAVE, "bud:form_ok"), (texts.BTN_CANCEL, "bud:no")]]))
    await cb.answer()


@router.message(BudgetForm.category, F.text)
async def form_category_text(message: Message, state: FSMContext) -> None:
    if message.text.strip() in keyboards.MENU_SURFACE:
        await menu_router.menu_action(message, state)
        return
    category = " ".join(message.text.split())[:80]
    await state.update_data(category=category)
    data = await state.get_data()
    await state.set_state(BudgetForm.confirm)
    tx_type = texts.WORD_INCOME if data.get("tx_type") == "income" else texts.WORD_EXPENSE
    await message.answer(
        f"{tx_type.capitalize()}: {texts.money(float(data['amount']))} · {category}\n\nЗаписать?",
        reply_markup=keyboards.kb([[(texts.BTN_SAVE, "bud:form_ok"), (texts.BTN_CANCEL, "bud:no")]]))


@router.callback_query(F.data == "bud:form_ok")
async def form_save(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("amount") or not data.get("category"):
        await cb.answer("Форма устарела", show_alert=True)
        return
    res = await core.api("/tools/budget/commit", {
        "chat_id": cb.message.chat.id,
        "idempotency_key": f"tg:budget:{cb.message.chat.id}:{cb.message.message_id}",
        "tx_type": data.get("tx_type", "expense"),
        "amount": float(data["amount"]), "category": data["category"],
        "purpose": "внесено через форму", "source_text": "form",
    }, cb.message.chat.id)
    await state.clear()
    if res.get("ok"):
        await cb.message.edit_text(
            f"Записано: {texts.money(float(data['amount']))} · {data['category']}.")
        await cb.answer(texts.BTN_DONE)
    else:
        await cb.answer(texts.SAVE_FAILED, show_alert=True)


# --------------------------------------------------- команды-дубли меню
@router.message(Command("expense", "income"))
async def cmd_budget_input(message: Message, state: FSMContext) -> None:
    """/expense 3000 реквизит, /income 5000 взнос — ввод данных командой (Ш-52).

    Свободный текст черновик операции больше не создаёт: данные вносятся
    командой или формой, а текст остаётся для вопросов о существующем.
    """
    parts = (message.text or "").split(maxsplit=1)
    command = parts[0].lstrip("/").split("@")[0]
    tx_type = "income" if command == "income" else "expense"
    arg = parts[1].strip() if len(parts) > 1 else ""
    caps = await core.caps_of(message.chat.id)
    if not core.has(caps, "budget.write"):
        await message.answer("Записывать расходы и доходы может участник с правом budget.write.")
        return
    await state.clear()
    if not arg:
        await state.update_data(tx_type=tx_type)
        await state.set_state(BudgetForm.amount)
        await message.answer("Сумма цифрами, например: 3000")
        return
    draft = await core.parse_budget(arg, message.chat.id)
    d = dict(draft.get("draft") or {})
    if not d.get("amount"):
        await state.update_data(tx_type=tx_type)
        await state.set_state(BudgetForm.amount)
        await message.answer("Не увидел сумму. Напишите цифрами, например: 3000")
        return
    d["tx_type"] = d.get("tx_type") or tx_type
    category = d.get("category")
    if not isinstance(category, str) or not category:
        await message.answer("Уточните назначение: /expense 3000 реквизит")
        return
    amount_text = texts.money(float(d["amount"]))
    tx_date = d.get("tx_date")
    purpose_text = d.get("purpose") or arg
    await state.update_data(bd=d, raw=arg)
    await message.answer(
        f"Черновик операции:\n{amount_text} · {category} · {tx_date}"
        f"Назначение: {purpose_text}\n\nЗаписать?",
        reply_markup=keyboards.kb([[(texts.BTN_SAVE, "bud:ok"), (texts.BTN_CANCEL, "bud:no")]]))
