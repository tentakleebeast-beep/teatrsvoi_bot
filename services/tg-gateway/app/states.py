"""Состояния FSM gateway (шаги форм и диалогов)."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class Reg(StatesGroup):
    await_code = State()
    await_name = State()
    choose = State()        # выбор себя из списка: ввод букв сужает кнопки (Ш-46)


class BudgetForm(StatesGroup):
    amount = State()
    category = State()
    confirm = State()


class AvailForm(StatesGroup):
    day = State()
    status = State()        # занят / свободен / не уверен — состояния карточки дня (FR-D6)
    time = State()
    note = State()          # комментарий к дню: необязательный, едет в лист (FR-C9)
    confirm = State()


class AssignDraft(StatesGroup):
    """Назначение события: помним, что уже понято, и спрашиваем только недостающее.

    Живой диалог 05.11.2026 показал, что без состояния каждая реплика разбиралась
    заново: «завтра» уходило в расписание, а «с 15-00 до 18-00» — в черновик расхода
    на 15 ₽. Состояние хранит фразу человека (`ad.text`), следующая реплика
    дописывается к ней; какого поля не хватает — говорит код ошибки tool-api.
    """

    await_kind = State()
    await_date = State()
    await_time = State()


class Feedback(StatesGroup):
    await_text = State()


class TaskFrame(StatesGroup):
    """Одноразовая рамка «Озадачить»: следующий текст уходит агенту с `task`."""
    await_text = State()


class EventMove(StatesGroup):
    """Перенос события: новое время спрашиваем текстом, запись — после подтверждения."""
    await_time = State()


class PanelEdit(StatesGroup):
    """Правка из админ-панели: значение спрашиваем текстом, но команду не печатают."""
    tariff = State()


class Intro(StatesGroup):
    name = State()
    alias = State()
    phone = State()
    birthday = State()
    travel = State()
    plays = State()
    roles = State()
    trainings = State()
    manifest = State()
    confirm = State()
