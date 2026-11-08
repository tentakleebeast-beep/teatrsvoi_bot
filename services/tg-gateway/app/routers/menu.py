"""Роутер меню: старт, главное меню, справка, регистрация по коду."""

from __future__ import annotations

import re
from urllib.parse import quote

import core
import errors
import keyboards
import parsers
import runtime
import texts
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, MenuButtonWebApp, Message, WebAppInfo
from routing import (
    ABOUT_ME,
    ASSIGN_Q,
    HOWTO_Q,
    LIST_PEOPLE,
    MONEY_Q,
    SCHEDULE_Q,
    SLOTS_Q,
    data_input_kind,
    is_task,
)
from states import AssignDraft, AvailForm, BudgetForm, Reg, TaskFrame
from webapp_links import keyboard as webapp_keyboard
from webapp_links import page_url

import routers.admin as admin_router
import routers.assign as assign_router
import routers.availability as availability_router
import routers.budget as budget_router
import routers.intro as intro_router
import routers.notes as notes_router
import routers.profile as profile_router
from config import ADMIN_CHAT_ID, WEBAPP_BASE_URL, log

router = Router()


# Кнопки-действия, которые человек видит в обычных сообщениях чата (/start, подсказка
# о вводе, фолбэк когда агент не ответил). Один список на все три места: подпись
# и колбэк не расходятся, а реестр функций агента (config/agent-features.yaml,
# поверхность `chat`) сверяется именно с ним — новая кнопка без записи роняет тест.
CHAT_ACTION_BUTTONS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("Записать расход", ("budget.write",), "form:expense"),
    ("Записать доход", ("budget.write",), "form:income"),
    ("Сообщить занятость", ("schedule.availability.self",), "form:busy"),
)


def _chat_action_buttons(*labels: str) -> list[list[tuple[str, str]]]:
    """Кнопки-действия по подписям — для сообщений без проверки прав."""
    wanted = set(labels)
    return [[(text, data)] for text, _, data in CHAT_ACTION_BUTTONS if text in wanted]


def disciplines_line(disciplines: list[dict] | None) -> str:
    """Строка «Дисциплины: Музыка» — только если человек ведёт хоть что-то (140 §4.1).

    Дисциплины приходят из ответа `/tools/participants/me` (`disciplines: [{code,name}]`),
    а не из локального списка: у роли нет собственной копии справочника.
    """
    names = [str(item.get("name") or item.get("code") or "") for item in (disciplines or [])]
    names = [name for name in names if name]
    return f"{texts.PREFIX_DISCIPLINES}{', '.join(names)}" if names else ""


# ------------------------------------------------------------------ /start
async def send_menu(message: Message, chat_id: int, name: str, roles: list[str],
                    caps: list[str], disciplines: list[dict] | None = None) -> None:
    # Кнопок здесь нет: состав меню — группами `keyboards.menu_groups` по правам, и
    # «Админ-панель» появляется по `admin.panel`, а не по `participants.view`. Педагог
    # `participants.view` имеет, поэтому прежнее условие вело его в отказ (140 §4.1).
    titles = ", ".join(texts.ROLE_TITLES.get(r, r) for r in roles)
    lines = [name, f"{texts.PREFIX_ROLES}{titles}"]
    subjects = disciplines_line(disciplines)
    if subjects:
        lines.append(subjects)
    lines.append(f"Возможностей: {len(caps)}")
    await message.answer(
        "\n".join(lines) + "\n\n"
        "Выберите раздел кнопкой (внутри — пункты). Данные — командой "
        "(/expense 3000 реквизит, /busy занят 28.10 с 10 до 14) или формой; "
        "спросить можно текстом: «что у меня завтра», «что ты умеешь?».",
        reply_markup=keyboards.menu_kb(caps),
    )
    # отдельным сообщением: в одном сообщении клавиатура и inline-кнопка несовместимы
    week = webapp_keyboard(texts.BTN_OPEN_WEEK, "/webapp/days", WEBAPP_BASE_URL)
    if week is not None and "schedule.availability.self" in caps:
        await message.answer("Занятость на неделю удобнее отмечать в приложении:",
                             reply_markup=week)


async def ensure_menu_button(bot: Bot, chat_id: int) -> None:
    """Кнопка меню в этом чате ведёт в хаб.

    Дефолтная кнопка бота перекрывается Main Mini App, настроенным в BotFather:
    `setChatMenuButton` без chat_id отвечает ok, но человек продолжает видеть
    старый адрес. Пер-чатовая настройка это перекрытие снимает (проверено 30.10.2026),
    поэтому ставим её каждому, кто открывает бота.
    """
    menu_url = page_url("/webapp/home", WEBAPP_BASE_URL)
    if not menu_url:
        return
    try:
        await bot.set_chat_menu_button(
            chat_id=chat_id,
            menu_button=MenuButtonWebApp(text="Меню", web_app=WebAppInfo(url=menu_url)))
    except errors.TELEGRAM_ERRORS as exc:                        # кнопка меню — не критично
        log.warning("не удалось поставить кнопку меню в чате %s: %s", chat_id, exc)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    chat_id = message.chat.id
    await state.clear()
    await ensure_menu_button(message.bot, chat_id)
    res = await core.api("/tools/participants/me", None, chat_id, method="GET")

    if res.get("ok"):
        p = res["data"]
        await send_menu(message, chat_id, p["display_name"],
                        p.get("roles") or [p["role"]], p.get("capabilities") or [],
                        p.get("disciplines"))
        return

    boot = await core.api("/admin/bootstrap",
                     {"chat_id": chat_id, "display_name": message.from_user.full_name}, chat_id)
    if boot.get("ok"):
        p = boot["participant"]
        await send_menu(message, chat_id, p["display_name"], ["admin"], ["*"])
        return

    await state.set_state(Reg.await_code)
    await message.answer(
        "Это бот театра-студии СВОИ.\nВы пока не зарегистрированы.\n\n"
        "Введите код приглашения (8 символов), который дал организатор."
    )


async def about_me(message: Message) -> bool:
    """«Кто я» — отвечаем данными из БД, а не моделью."""
    me = await core.api("/tools/participants/me", None, message.chat.id, method="GET")
    if not me.get("ok"):
        return False
    p = me["data"]
    roles = ", ".join(texts.ROLE_TITLES.get(r, r) for r in (p.get("roles") or [p["role"]]))
    caps = len(p.get("capabilities") or [])
    lines = [p["display_name"], f"{texts.PREFIX_ROLES}{roles}"]
    subjects = disciplines_line(p.get("disciplines"))
    if subjects:
        lines.append(subjects)
    lines += [f"Возможностей: {caps}", f"Ключ: {p.get('person_key') or '—'}"]
    await message.answer("\n".join(lines))
    return True


async def list_people(message: Message) -> bool:
    """Список участников — из БД, с проверкой права participants.view."""
    caps = await core.caps_of(message.chat.id)
    if "participants.view" not in caps and "*" not in caps:
        await message.answer(
            "Список участников доступен организатору и администратору. "
            "Ваши роли этого не позволяют.")
        return True
    res = await core.api("/tools/participants", None, message.chat.id, method="GET")
    if not res.get("ok"):
        await message.answer("Не удалось получить список: " + errors.error_text(res)[:120])
        return True
    rows = res.get("data", [])
    active = [p for p in rows if p["status"] == "active"]
    pending = [p for p in rows if p["status"] == "pending"]
    lines = [f"Зарегистрировано: {len(active)}, ожидают подтверждения: {len(pending)}", ""]
    for p in active:
        roles = ", ".join(texts.ROLE_TITLES.get(r, r) for r in (p.get("roles") or [p["role"]]))
        tg = "telegram привязан" if p.get("chat_id") else "без telegram"
        lines.append(f"{p['display_name']} — {roles} ({tg})")
    if pending:
        lines += ["", "Ожидают: " + ", ".join(p["display_name"] for p in pending)]
    await message.answer("\n".join(lines)[:3800])
    return True


async def answer_howto(message: Message) -> bool:
    """
    «Как/где/почему» — это инструкция, а не операция. Отвечаем той же справкой,
    что и «Что умею»: список строится из меню, поэтому команды там не всплывают.
    """
    await message.answer(keyboards.help_text(await core.caps_of(message.chat.id),
                                  await core.ui_texts_cached(message.chat.id)))
    return True


async def group_menu(message: Message, group: str, caps: list[str]) -> None:
    """Раздел главного меню: доступные пункты группы кнопками (Ш-47)."""
    for label, _ in keyboards.menu_groups(caps):
        if label == group:
            await message.answer(f"{label}: выберите пункт.",
                                 reply_markup=keyboards.group_kb(caps, group))
            return
    await message.answer("Этот раздел вам недоступен.", reply_markup=keyboards.menu_kb(caps))


async def menu_action(message: Message, state: FSMContext) -> None:
    """
    Кнопки постоянного меню. Отдельный обработчик ДО свободного текста:
    иначе «Админ-панель» и «Бюджет» уходили в модель как обычная фраза.
    Ш-47: кнопка-группа открывает подменю, пункт сразу выполняет действие.
    """
    label = message.text.strip()
    if label in keyboards.MENU_GROUP_LABELS:
        await state.clear()
        await group_menu(message, label, await core.caps_of(message.chat.id))
        return
    await run_menu_action(message, state, label)


async def run_menu_action(message: Message, state: FSMContext, label: str) -> None:
    """Действие пункта меню: одна ветка и для кнопки клавиатуры, и для подменю."""
    chat_id = message.chat.id

    if label == texts.TITLE_MONEY:
        await state.clear()
        # Форма живёт в Mini App: в чате это пять полей и три кнопки, в окне — один экран.
        app_kb = webapp_keyboard("Внести операцию", "/webapp/tx", WEBAPP_BASE_URL)
        if app_kb is None:
            await state.update_data(tx_type="expense")
            await state.set_state(BudgetForm.amount)
            await message.answer("Окно недоступно — внесём в чате. Сумма цифрами, например: 3000")
        else:
            await message.answer("Категория, сумма, дата учёта, статья, план или факт.",
                                 reply_markup=app_kb)
        return

    if label == texts.TITLE_AVAILABILITY:
        await state.clear()
        await state.set_state(AvailForm.day)
        await message.answer(
            texts.QUESTION_WHEN, reply_markup=keyboards.kb([[(texts.BTN_TODAY, "av:day:0"), (texts.BTN_TOMORROW, "av:day:1")],
                                       [(texts.BTN_OTHER_DATE, "av:day:other")]]))
        return

    if label == texts.TITLE_FEEDBACK:
        await state.clear()
        await message.answer(
            "Что рассказать?",
            reply_markup=keyboards.kb([[(texts.FEEDBACK_KIND_BROKEN, "fb:bug"), (texts.FEEDBACK_KIND_IMPROVE, "fb:wish")],
                             [(texts.FEEDBACK_KIND_WISH_FL, "fb:wish-fl")],
                             [(texts.FEEDBACK_KIND_QUESTION, "fb:question"), (texts.FEEDBACK_KIND_GOOD, "fb:praise")]]))
        return

    if label == texts.TITLE_DUES:
        res = await core.api("/tools/dues/me", None, chat_id, method="GET")
        if not res.get("ok"):
            await message.answer(texts.DUES_DENIED)
            return
        await message.answer(texts.dues_text(res))
        return

    if label == texts.TITLE_BUDGET:
        await budget_router.answer_money(message, "")
        return

    if label == texts.TITLE_SCHEDULE:
        await availability_router.answer_schedule(message)
        return

    if label == texts.TITLE_RECONCILE:
        await state.clear()
        await admin_router.start_reconcile(message)
        return

    if label == texts.TITLE_PEOPLE:
        await state.clear()
        await admin_router.send_people(message)
        return

    if label == texts.TITLE_SLOTS:
        await state.clear()
        app_kb = webapp_keyboard("Открыть поиск окон", "/webapp/slots", WEBAPP_BASE_URL)
        if app_kb is None:
            await availability_router.answer_slots(message)
        else:
            await message.answer("Даты, кто нужен, длительность — и покажу окна с пояснениями.",
                                 reply_markup=app_kb)
        return

    if label == texts.TITLE_PROFILE:
        # Профиль живёт в Mini App (решение 30.10.2026): в чате — только кнопка входа,
        # а текстовый ответ остаётся резервом, когда адрес Mini App не настроен.
        app_kb = webapp_keyboard(texts.BTN_OPEN_PROFILE, "/webapp", WEBAPP_BASE_URL)
        if app_kb is None:
            await profile_router.answer_profile(message)
        else:
            await message.answer("Анкета открывается в Mini App: имя, контакты, "
                                 "короткие имена, род.", reply_markup=app_kb)
        return

    if label == texts.TITLE_CAPABILITIES:
        await state.clear()
        await message.answer(keyboards.help_text(await core.caps_of(chat_id), await core.ui_texts_cached(chat_id)))
        return

    if label == texts.TITLE_HELP:
        await state.clear()
        res = await core.api("/tools/howto", None, chat_id, method="GET")
        if not res.get("ok") or not res.get("sections"):
            await message.answer("Справка пока недоступна.")
            return
        for section in res["sections"]:
            await message.answer((f"{section['title']}\n\n{section['text']}")[:3800])
        return

    if label == texts.TITLE_ADMIN:
        await admin_router.panel_root(message, edit=False)
        return

    if label == texts.TITLE_REQUESTS:
        await state.clear()
        await admin_router.send_pending(message)
        return

    if label == "Пригласить":
        await state.clear()
        await admin_router.send_invite(message)
        return

    caps = await core.caps_of(chat_id)
    await message.answer(keyboards.help_text(caps))


@router.message(StateFilter(None, AssignDraft.await_kind, AssignDraft.await_date,
                            AssignDraft.await_time), F.text.in_(keyboards.MENU_SURFACE))
async def on_menu_button(message: Message, state: FSMContext) -> None:
    # Кнопка меню снимает незавершённый диалог назначения: человек ушёл в другой дом.
    await state.clear()
    await menu_action(message, state)


@router.callback_query(F.data.startswith("mi:"))
async def menu_item_cb(cb: CallbackQuery, state: FSMContext) -> None:
    """Пункт подменю: право проверяем заново — кнопка могла остаться от старого меню."""
    label = (cb.data or "")[3:]
    if label not in keyboards.MENU_ACTIONS:
        await cb.answer("Кнопка устарела, откройте меню заново", show_alert=True)
        return
    caps = await core.caps_of(cb.message.chat.id)
    if label not in {text for _, items in keyboards.menu_groups(caps) for text in items}:
        await cb.answer()
        await cb.message.answer("Этот пункт вам недоступен.")
        return
    await cb.answer()
    await state.clear()
    await run_menu_action(cb.message, state, label)


async def answer_question(message: Message, text: str) -> bool:
    """Вопрос о существующем — диалог: отвечаем данными, без состояний (Ш-52)."""

    low = text.lower()
    if re.search(ABOUT_ME, low):
        if await about_me(message):
            return True
    if re.search(LIST_PEOPLE, low):
        if await list_people(message):
            return True
    if re.search(HOWTO_Q, low) and not re.search(r"\d", low):
        if await answer_howto(message):
            return True
    if re.search(SLOTS_Q, low):
        if await availability_router.answer_slots(message, text=text):
            return True
    if re.search(MONEY_Q, low):
        if await budget_router.answer_money(message, text):
            return True
    if re.search(SCHEDULE_Q, low):
        if await availability_router.answer_schedule(message):
            return True

    return False


async def data_input_hint(message: Message, kind: str = "budget") -> None:
    """Данные вносятся командой или формой, а не свободным текстом (Ш-52)."""
    lines = [
        texts.DATA_INPUT_HINTS.get(kind, ""),
        "Данные бот принимает командой или формой — так ничего не теряется:",
        "· расход: /expense 3000 реквизит",
        "· доход: /income 5000 взнос",
        "· занятость: /busy занят 28.10 с 10 до 14",
        "Или кнопками ниже.",
        "",
        "А спросить можно обычным текстом: «что у меня завтра», «когда все свободны»,",
        "«сколько потратили в октябре».",
    ]
    await message.answer("\n".join(lines), reply_markup=keyboards._buttons_with_week(
        _chat_action_buttons("Записать расход", "Сообщить занятость")))


_TODAY_Q = re.compile(
    r"^(?:а\s+)?(?:какой|какое|какая|какие|что)\s+(?:сейчас|сегодня)\s+"
    r"(?:день|число|дата)(?:\s+недели)?$",
    re.IGNORECASE)
_TODAY_Q_BARE = re.compile(
    r"^(?:а\s+)?(?:какой|какое|какая)\s+(?:день|число|дата)(?:\s+недели)?$",
    re.IGNORECASE)
_TODAY_Q_ALT = re.compile(r"^сегодня\s+(?:какое|какой|что)\b", re.IGNORECASE)


def asks_today(text: str) -> bool:
    """Спрашивают ли про сегодняшнюю дату.

    Узко и намеренно: только цельные вопросы вида «какой сегодня день» или
    «какое число». Фраза «какой день лучше для репетиции» не перехватывается —
    она уходит к модели, как и раньше.
    """
    t = " ".join((text or "").strip().split()).rstrip("?.!").lower()
    if not t or len(t) > 40:
        return False
    return bool(_TODAY_Q.match(t) or _TODAY_Q_BARE.match(t) or _TODAY_Q_ALT.match(t))


#: Причины деградации, где модель доступна, а исчерпан дневной лимит чата.
#: Для них tool-api уже добавил одну строку «Дальше — по кнопкам.», и gateway
#: не должен повторять её и тем более писать «модель недоступна» — это ложь
#: (возврат ревью A-4: человек видел внутренний код chat_tokens).
BUDGET_REASONS = frozenset({"chat_tokens", "chat_steps"})
#: Пояснение для остальных причин: модель правда не ответила.
NO_MODEL_NOTE = "Модель сейчас недоступна — ответил по правилам ({reason})."

#: Слова-отмены задачи. «Обнулить» — из карты «Озадачить»; остальные нужны, потому
#: что человек в чате нажмёт то, что помнит, а не то, что написано в документе.
CANCEL_TASK_WORDS = frozenset({"обнулить", "стоп", "остановись", "отмени задачу"})


def visible_answer(agent: dict) -> str:
    """Текст ответа агента ровно в том виде, в каком его увидит человек.

    Единственное место, где к ответу добавляются пояснение о деградации и строка
    шагов: бюджетные причины (`chat_tokens`, `chat_steps`) пояснения не получают —
    в ответе уже есть строка «Дальше — по кнопкам.», а модель при этом доступна.
    Строка шагов («календарь → занятость → окна») показывает, что агент делал, а
    не только что сказал (решение 05.11.2026).
    """
    answer = core.clean_answer(str(agent.get("answer") or ""))[:3800]
    answer = texts.with_steps(answer, str(agent.get("step_line") or ""))
    reason = (agent.get("degraded") or {}).get("reason")
    if reason and reason not in BUDGET_REASONS:
        answer = answer + "\n\n" + NO_MODEL_NOTE.format(reason=reason)
    return answer


#: Право, без которого карточка не рисуется: ровно то же, что у формы действия.
PROPOSAL_CAPS = {"budget.tx": "budget.write", "event.assign": "schedule.write",
                 "note.create": "notes.write"}


async def _show_plan_card(message: Message, chat_id: int, agent: dict, plan: dict) -> bool:
    """План одним токеном: карточка со списком шагов и кнопкой «Выполнить план»."""
    card = await core.agent_plan(chat_id, plan)
    if not (card.get("ok") and card.get("token")):
        log.warning("план без токена: %s", card.get("error"))
        answer = core.clean_answer(str((agent or {}).get("answer") or ""))[:3800]
        if answer:
            await message.answer(answer)
            return True
        return False
    await message.answer(
        texts.agent_plan_card(card.get("plan") or plan),
        reply_markup=keyboards.kb([
            [(texts.BTN_RUN_PLAN, f"agp:ok:{card['token']}"),
             (texts.BTN_CANCEL, f"agp:no:{card['token']}")],
        ]),
    )
    return True


async def _show_open_card(message: Message, proposal: dict) -> bool:
    """Не хватает данных: кнопка в Mini App вместо придуманной даты или суммы."""
    screen = str(proposal.get("screen") or "/webapp/home")
    text = str(proposal.get("text") or texts.AGENT_OPEN_NOTE)
    markup = webapp_keyboard(texts.AGENT_OPEN_BUTTON, screen, WEBAPP_BASE_URL)
    if markup is None:
        # Mini App не настроен: адрес не выдумываем, говорим что делать словами
        await message.answer(text)
        return True
    await message.answer(text + "\n" + texts.AGENT_OPEN_NOTE, reply_markup=markup)
    return True


async def show_agent_card(message: Message, chat_id: int, agent: dict) -> bool:
    """Показать предложение записи, план или вход в приложение.

    Возвращает True, если предложение обработано (карточка или объяснение отказа):
    тогда обычный ответ агента уже не нужен. False — предложения нет или tool-api
    не выдал токен, и вызывающий показывает ответ как обычно.

    Право проверяется до запроса токена: без права записи карточки нет вовсе,
    иначе кнопка вела бы в 403 после нажатия (та же причина, что у формы).
    """
    plan = (agent or {}).get("plan") or {}
    if plan.get("steps"):
        return await _show_plan_card(message, chat_id, agent, plan)

    proposal = (agent or {}).get("propose") or {}
    if not proposal:
        return False
    if proposal.get("kind") == "open":
        # Нехватка данных — всегда кнопка, даже без права записи: экран покажет
        # человеку то, что ему доступно.
        return await _show_open_card(message, proposal)

    tool = proposal.get("tool")
    if tool not in PROPOSAL_CAPS:
        return False
    if not core.has(await core.caps_of(chat_id), PROPOSAL_CAPS[tool]):
        answer = core.clean_answer(str((agent or {}).get("answer") or ""))[:3800]
        await message.answer(answer or texts.AGENT_NO_WRITE)
        return True
    card = await core.agent_propose(chat_id, proposal)
    if not (card.get("ok") and card.get("token")):
        log.warning("предложение без токена: %s", card.get("error"))
        return False
    await message.answer(
        texts.agent_proposal_card(card.get("proposal") or proposal),
        reply_markup=keyboards.kb([
            [(texts.BTN_CONFIRM, f"agc:ok:{card['token']}"),
             (texts.BTN_CANCEL, f"agc:no:{card['token']}")],
        ]),
    )
    return True


# ------------------------------------------------- три кнопки диалога (05.11.2026)
# Постоянная клавиатура управляет разговором, а не доменами (docs/development/82 §3.3).
# Обработчик стоит до `on_text` и без фильтра состояния: «Обнулить» обязан выпускать
# и из формы занятости, и из назначения события, и из рамки задачи. Роутер меню
# подключён первым, поэтому его обработчик побеждает обработчики состояний других
# роутеров — так выход из формы не зависит от того, где человек застрял.
async def answer_quota(message: Message) -> None:
    """«Сколько мне осталось»: цифры из `v_llm_balance`, без вызова модели.

    Маршрут `GET /tools/quota/me` только читает (`llm_usage` не пополняется):
    вопрос о лимитах не должен тратить тот лимит, о котором спрашивают.
    """
    res = await core.api("/tools/quota/me", None, message.chat.id, method="GET")
    if not res.get("ok"):
        await message.answer(texts.QUOTA_UNAVAILABLE)
        return
    await message.answer(str(res.get("text") or texts.QUOTA_FALLBACK.format(text="—"))[:3900])


async def dialog_control(message: Message, state: FSMContext) -> None:
    """Три контроля диалога: «Обнулить», «Озадачить», «Сколько мне осталось»."""
    label = (message.text or "").strip()
    if label == keyboards.BTN_RESET:
        await state.clear()
        await message.answer(texts.DIALOG_RESET_DONE)
        return
    if label == keyboards.BTN_TASK:
        # Запоминаем незавершённое назначение: если следующая фраза окажется
        # ответом диалогу («занят завтра с 10 до 14», «заметка: …»), она продолжится
        # в нём, а не потеряется (карта t_ebce081e).
        prev = await state.get_state()
        draft = (await state.get_data()).get(assign_router.DRAFT_KEY)
        await state.clear()
        await state.set_state(TaskFrame.await_text)
        if prev in assign_router.DRAFT_STATES:
            await state.update_data(tf_prev=str(prev), **{assign_router.DRAFT_KEY: draft})
        await message.answer(texts.TASK_FRAME_ASK)
        return
    await state.clear()             # вопрос о лимитах — другое намерение, рамку снимает
    await answer_quota(message)


@router.message(F.text.in_(keyboards.DIALOG_CONTROLS))
async def on_dialog_control(message: Message, state: FSMContext) -> None:
    await dialog_control(message, state)


@router.message(TaskFrame.await_text, F.text, ~F.text.startswith("/"))
async def task_frame_text(message: Message, state: FSMContext) -> None:
    """Текст внутри рамки «Озадачить»: один запрос к агенту с признаком `task`.

    Рамка одноразовая: состояние снимается до запроса, поэтому после ответа
    следующий текст — снова обычный (Ш-12 не меняется). Явно другое намерение
    (данные, заметка) рамку тоже снимает и идёт обычным путём: «занят завтра с 10
    до 14» не должно превращаться в задачу. Если перед рамкой был открыт диалог
    назначения, его состояние возвращается до вызова `on_text`: фраза проходит
    через обработку назначения, а не теряется (карта t_ebce081e).
    """
    text = (message.text or "").strip()
    data = await state.get_data()
    prev = data.get("tf_prev")
    await state.clear()
    if data_input_kind(text) or notes_router.NOTE_PREFIX.match(text):
        if prev in assign_router.DRAFT_STATES:
            await state.set_state(prev)
            await state.update_data(**{assign_router.DRAFT_KEY: data.get(assign_router.DRAFT_KEY)})
        await on_text(message, state)
        return
    chat_id = message.chat.id
    placeholder = await message.answer("Разбираю задачу…")
    agent = await core.agent_ask(chat_id, text, task=True)
    if await show_agent_card(message, chat_id, agent):
        await placeholder.delete()
        return
    if agent.get("answer"):
        await placeholder.edit_text(visible_answer(agent))
        return
    await placeholder.edit_text(texts.TASK_FRAME_FAILED)


@router.message(StateFilter(None, Reg.await_code, AssignDraft.await_kind,
                            AssignDraft.await_date, AssignDraft.await_time),
                F.text & ~F.text.startswith("/"))
async def on_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    chat_id = message.chat.id

    # Уточнение внутри назначения — это ответ боту, а не новая фраза (карта t_ebce081e).
    # Стоит первым: пока диалог назначения открыт, «завтра» означает день события,
    # а не расписание, и «с 15-00 до 18-00» — время, а не трата 15 ₽.
    if await state.get_state() in assign_router.DRAFT_STATES:
        if await assign_router.assign_continue(message, state, text):
            return
        # намерение оказалось другим: состояние уже снято, идём обычным путём

    # «Обнулить» посреди задачи: флаг читает петля агента между шагами
    # (agently/tasks.py). Новый вопрос снимет флаг сам.
    if text.lower().strip(" .!") in CANCEL_TASK_WORDS:
        await core.agent_cancel_task(chat_id)
        await message.answer(texts.AGENT_STOPPED)
        return

    if await state.get_state() == Reg.await_code.state:
        res = await core.api("/tools/participants/check_code",
                        {"chat_id": chat_id, "code": text}, chat_id)
        if errors.error_code(res) == "already_registered":
            await state.clear()
            p = res["participant"]
            await send_menu(message, chat_id, p["display_name"],
                            p.get("roles") or [p["role"]], p.get("capabilities") or [])
            return
        if not res.get("ok"):
            code_texts = {"code_not_found": "Код не найден. Проверьте написание.",
                     "code_expired": "Код устарел — попросите новый.",
                     "code_exhausted": "Этот код уже использован."}
            await message.answer(code_texts.get(errors.error_code(res), "Код не подошёл."))
            return

        await state.update_data(code=text, role=res.get("role"))
        cands = res.get("candidates", [])
        if cands:
            await state.set_state(Reg.choose)
            await message.answer(
                "Код принят. Выберите себя из списка — так ваши взносы и занятость "
                "останутся на одной записи. Начните вводить фамилию или имя: список "
                "сузится, а кнопки обновятся.", reply_markup=keyboards._candidates_kb(cands))
        else:
            await state.set_state(Reg.await_name)
            await message.answer("Код принят. Напишите фамилию и имя — «Фамилия Имя».")
        return

    # «заметка: …» — задача, а не вопрос: текст → карточка → подтверждение (FR-F7).
    # Стоит ДО ответов на вопросы и ДО черновика бюджета: в заметке бывают и цифры,
    # и слова вроде «сколько», но это запись человека, а не трата и не вопрос.
    if notes_router.NOTE_PREFIX.match(text):
        await notes_router.note_start(message, state)
        return

    if await answer_question(message, text):
        return

    # «назначь репетицию с Витей в четверг в 20» — событие, а не трата 20 ₽.
    # Свой обработчик assign_start зарегистрирован после catch-all, поэтому
    # aiogram до него не доходит: передаём фразу напрямую, до бюджета и роутера.
    if re.search(ASSIGN_Q, text.lower()):
        await assign_router.assign_start(message, state)
        return

    # «занят завтра с 10 до 14» разбирается детерминированно и показывается карточкой:
    # тот же путь, что у /busy (AUD-4, docs/architecture/25 §9.5). Стоит ДО черновика
    # бюджета: в фразе есть цифры, и раньше она превращалась в трату на 10 ₽ или в
    # подсказку «вводите командой» — хотя ошибка видна до записи.
    input_kind = data_input_kind(text)
    if input_kind == "availability" and await availability_router.busy_card(message, text, state):
        return

    # Свободный текст первичен: фраза с суммой сразу превращается в карточку.
    # Стоит ДО подсказки «данные вводят командой»: раньше подсказка перехватывала
    # любую фразу с цифрой, и сценарий §4.1 не срабатывал вовсе.
    draft = parsers.text_to_draft(text)
    if draft:
        caps_now = await core.caps_of(chat_id)
        if not core.has(caps_now, "budget.write"):
            # Без права «Записать» кнопка вела бы в 403 после подтверждения.
            await data_input_hint(message, "budget")
            return
        await state.update_data(bd=draft, raw=text)
        await message.answer(
            texts.draft_card(draft),
            reply_markup=keyboards.kb([[(texts.BTN_SAVE, "bud:ok")],
                             [("Изменить", "form:" + draft["tx_type"]), (texts.BTN_CANCEL, "bud:no")]]),
        )
        return

    # Ш-52: для остального свободного текста данные вводятся командой или формой.
    if input_kind in ("availability", "budget"):
        await data_input_hint(message, input_kind)
        return

    # Дата: отвечает календарь проекта, а не модель. Дефект 01.11.2026 — на вопрос
    # «какой сегодня день» бот отвечал «27 октября 2023 года»: у LLM не было
    # текущей даты в промпте, и она придумывала.
    if asks_today(text):
        await message.answer(texts.today_answer(await core.today_local(chat_id)))
        return

    # всё остальное — решает агент tool-api (/tools/agent/ask): инструменты, шаги,
    # предложение записи. Переключателя режима нет (Ш-12, П-1): свободный текст
    # всегда идёт этим путём, а честный отказ агента ведёт в каскад интентов
    # (/tools/route) — второй разбор, не второй режим (docs/development/108).
    placeholder = await message.answer(texts.AGENT_WORKING)
    bot = getattr(message, "bot", None)
    typing = getattr(bot, "send_chat_action", None)
    if typing is not None:
        try:
            await typing(chat_id, "typing")
        except (TelegramAPIError, RuntimeError) as exc:   # индикатор — не повод терять ответ
            log.debug("typing не отправился: %s", exc)
    agent = await core.agent_ask(chat_id, text, task=is_task(text))
    if agent.get("error") == "cancelled":
        # Задачу отменили между шагами: человеку уже ответили на «Обнулить».
        await placeholder.delete()
        return
    if await show_agent_card(message, chat_id, agent):
        await placeholder.delete()
        return
    if agent.get("answer"):
        answer = visible_answer(agent)
        await placeholder.edit_text(answer)
        return
    if agent.get("error"):
        log.warning("агент -> %s", agent.get("error"))

    routed = await core.router_ask(chat_id, text, await core.user_ctx(chat_id))

    if routed.get("intent") == "dues":
        await placeholder.delete()
        await profile_router.show_dues(message)
        return

    if routed.get("intent") in ("budget", "schedule"):
        if routed.get("answer"):
            await placeholder.edit_text(core.clean_answer(str(routed["answer"]))[:3800])
            return
        await placeholder.delete()
        await data_input_hint(message, data_input_kind(text) or "budget")
        return

    if routed.get("answer"):
        await placeholder.edit_text(core.clean_answer(str(routed["answer"]))[:3800])
        return

    res = await core.api("/tools/assistant/ask", {"chat_id": chat_id, "text": text}, chat_id)
    if res.get("ok"):
        await placeholder.edit_text(res["answer"][:3800])
        return

    reason = errors.error_text(res) if errors.error_code(res) in {
        "llm_unavailable", "quota_exceeded", "llm_error"} else "Не получилось ответить."
    await placeholder.edit_text(
        f"{reason}\n\nНе разобрал просьбу: дома действий — в приложении.\n"
        "Записать операцию можно кнопкой:",
        reply_markup=keyboards.with_home(
            _chat_action_buttons("Записать расход", "Сообщить занятость")),
    )


@router.message(Reg.choose, F.text, ~F.text.startswith("/"))
async def reg_filter(message: Message, state: FSMContext) -> None:
    """Ввод букв сужает список людей (Ш-46, V-006): «Смирнова» находит «Дарья Смирнова».

    Автосопоставления нет: бот только показывает записи, привязку делает человек
    нажатием кнопки. Ничего не нашлось — человек идёт в заявку, а не в чужой профиль.
    """
    data = await state.get_data()
    if not data.get("code"):
        await message.answer("Начните заново: /start")
        return
    query = (message.text or "").strip()
    res = await core.api(f"/tools/participants/search?q={quote(query)}", None,
                    message.chat.id, method="GET")
    if not res.get("ok"):
        await message.answer("Не получилось получить список. Попробуйте ещё раз "
                             "или нажмите «Меня нет в списке».")
        return
    cands = res.get("candidates") or []
    if not cands:
        await state.set_state(Reg.await_name)
        await message.answer(
            f"{texts.PREFIX_QUERY}{query}» никого не нашёл. Напишите фамилию и имя — "
            "заявку подтвердит администратор.")
        return
    await message.answer(f"Кто из них вы? Найдено: {len(cands)}.",
                         reply_markup=keyboards._candidates_kb(cands))


@router.callback_query(F.data.startswith("reg:pick:"))
async def reg_pick(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    code = data.get("code")
    if not code:
        await cb.answer("Начните заново: /start", show_alert=True)
        return
    pid = int(cb.data.split(":")[2])
    res = await core.api("/tools/participants/attach",
                    {"chat_id": cb.message.chat.id, "participant_id": pid, "code": code},
                    cb.message.chat.id)
    if res.get("ok"):
        await state.clear()
        p = res["participant"]
        await send_menu(cb.message, cb.message.chat.id, p["display_name"],
                        p.get("roles") or [p["role"]], p.get("capabilities") or [])
        await cb.message.delete()
        await cb.answer("Зарегистрировано")
        await intro_router.intro_start(cb.message, cb.message.chat.id, state)
    else:
        await cb.answer(f"Ошибка: {res.get('error')}", show_alert=True)


@router.callback_query(F.data == "reg:none")
async def reg_none(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Reg.await_name)
    await cb.message.edit_text("Напишите фамилию и имя — заявку подтвердит администратор.")
    await cb.answer()


@router.message(Reg.await_name, F.text)
async def reg_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    code = data.get("code")
    if not code:
        await message.answer("Начните заново: /start")
        return
    res = await core.api("/tools/participants/register",
                    {"chat_id": message.chat.id, "code": code,
                     "display_name": message.text.strip(), "require_approval": True},
                    message.chat.id)
    if errors.error_code(res) == "person_key_taken":
        await message.answer("Такой человек уже есть. Отправьте /start и выберите себя из списка.")
        return
    if not res.get("ok"):
        await message.answer(errors.error_text(res))
        return
    p = res["participant"]
    await state.clear()
    if p["status"] == "pending":
        await message.answer(f"Заявка создана: {p['display_name']}. "
                             "Доступ откроется после подтверждения.")
        if ADMIN_CHAT_ID and runtime._bot:
            await runtime._bot.send_message(
                ADMIN_CHAT_ID,
                f"{texts.PREFIX_REQUEST}{p['display_name']} — {texts.ROLE_TITLES.get(p['role'], p['role'])}",
                reply_markup=keyboards.kb([[(texts.BTN_CONFIRM, f"adm:ok:{p['id']}"),
                                  (texts.BTN_REJECT, f"adm:no:{p['id']}")]]))
    else:
        await send_menu(message, message.chat.id, p["display_name"],
                        p.get("roles") or [p["role"]], p.get("capabilities") or [])
        await intro_router.intro_start(message, message.chat.id, state)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(keyboards.help_text(await core.caps_of(message.chat.id),
                                  await core.ui_texts_cached(message.chat.id)))


@router.message(Command("menu"))
async def menu_cmd(message: Message, state: FSMContext) -> None:
    # /menu — явный выход из любого диалога: форма, рамка «Озадачить» и незавершённое
    # назначение снимаются, следующая фраза снова обычная (05.11.2026, t_ebce081e).
    await state.clear()
    caps = await core.caps_of(message.chat.id)
    await message.answer("Главное меню:", reply_markup=keyboards.menu_kb(caps))


# --------------------------------------------------------- тихие часы (О3, FR-G3)
# `docs/development/12` §5 обещает настройку «из Telegram». Чат — вторая оболочка
# над тем же путём, что экран `/webapp/notifications`: оба зовут обработчик
# tool-api, который пишет `notification_pref` (`docs/architecture/25` §9.2 п.2).
QUIET_COMMANDS = ("/quiet", "/тихие")


def parse_quiet_hours(arg: str) -> tuple[str, str] | None:
    """`21:00-09:00` / `20-8` / `выкл` → тихие часы парой ЧЧ:ММ. None — не разобрали.

    `выкл` — это 00:00–00:00: пустой интервал, в который не попадает ни один час,
    поэтому отдельного поля «включено» не нужно.
    """
    text = (arg or "").strip().lower().replace("–", "-").replace("—", "-")
    if not text:
        return None
    if text in (texts.QUIET_OFF_WORD, "выключить", "нет"):
        return "00:00", "00:00"
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*-\s*(\d{1,2})(?::(\d{2}))?", text)
    if not match:
        return None
    hour_from, minute_from, hour_to, minute_to = match.groups()
    if int(hour_from) > 23 or int(hour_to) > 23:
        return None
    start = f"{int(hour_from):02d}:{minute_from or '00'}"
    end = f"{int(hour_to):02d}:{minute_to or '00'}"
    return start, end


async def _quiet_settings(chat_id: int) -> dict | None:
    """Настройки с сервера: None — недоступны, а не «по умолчанию»."""
    res = await core.api("/tools/notifications/settings", None, chat_id, method="GET")
    return res.get("settings") if res.get("ok") else None


async def _save_quiet(chat_id: int, settings: dict) -> dict | None:
    """Отправить все пять полей: экран присылает тот же набор, частичной записи нет."""
    body = {
        "rehearsals": bool(settings.get("rehearsals", True)),
        "dues": bool(settings.get("dues", True)),
        "schedule_changes": bool(settings.get("schedule_changes", True)),
        "quiet_from": str(settings.get("quiet_from") or "20:00"),
        "quiet_to": str(settings.get("quiet_to") or "08:00"),
    }
    res = await core.api("/tools/notifications/settings", body, chat_id)
    return res.get("settings") if res.get("ok") else None


async def _show_quiet(message: Message, chat_id: int, *, notice: str = "") -> None:
    settings = await _quiet_settings(chat_id)
    if settings is None:
        await message.answer(texts.QUIET_UNAVAILABLE)
        return
    text = texts.quiet_text(settings)
    if notice:
        text = f"{notice}\n\n{text}"
    await message.answer(text[:3900], reply_markup=keyboards.quiet_kb())


@router.message(lambda message: parsers.command_name(message.text) in QUIET_COMMANDS)
async def cmd_quiet(message: Message, state: FSMContext) -> None:
    """`/quiet` — тихие часы и типы уведомлений из чата (FR-G3, карта AUD-5)."""
    chat_id = message.chat.id
    if not await core.caps_of(chat_id):
        await message.answer(texts.QUIET_DENIED)
        return
    await state.clear()
    parts = (message.text or "").split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg:
        await _show_quiet(message, chat_id)
        return
    hours = parse_quiet_hours(arg)
    if hours is None:
        await message.answer(texts.QUIET_BAD_TIME)
        return
    settings = await _quiet_settings(chat_id)
    if settings is None:
        await message.answer(texts.QUIET_UNAVAILABLE)
        return
    saved = await _save_quiet(chat_id, {**settings, "quiet_from": hours[0],
                                        "quiet_to": hours[1]})
    if saved is None:
        await message.answer(texts.SAVE_FAILED)
        return
    await _show_quiet(message, chat_id, notice=texts.QUIET_SAVED)


@router.callback_query(F.data.startswith("quiet:"))
async def quiet_action(cb: CallbackQuery, state: FSMContext) -> None:
    """Кнопки настройки: тип уведомления включает/выключается, интервал меняется."""
    chat_id = cb.message.chat.id
    parts = (cb.data or "").split(":", 2)
    kind = parts[1] if len(parts) > 1 else ""
    value = parts[2] if len(parts) > 2 else ""
    if kind == "custom":
        await cb.answer()
        await cb.message.edit_text(texts.QUIET_HINT)
        return
    if not await core.caps_of(chat_id):
        await cb.answer(texts.QUIET_DENIED, show_alert=True)
        return
    settings = await _quiet_settings(chat_id)
    if settings is None:
        await cb.answer(texts.QUIET_UNAVAILABLE, show_alert=True)
        return
    if kind == "type":
        if value not in {key for key, _ in texts.QUIET_KINDS}:
            await cb.answer("Кнопка устарела, откройте /quiet заново", show_alert=True)
            return
        settings[value] = not bool(settings.get(value))
    elif kind == "preset":
        hours = parse_quiet_hours(value)
        if hours is None:
            await cb.answer("Кнопка устарела, откройте /quiet заново", show_alert=True)
            return
        settings["quiet_from"], settings["quiet_to"] = hours
    else:
        await cb.answer("Кнопка устарела, откройте /quiet заново", show_alert=True)
        return
    saved = await _save_quiet(chat_id, settings)
    if saved is None:
        await cb.answer(texts.SAVE_FAILED, show_alert=True)
        return
    await cb.message.edit_text((texts.QUIET_SAVED + "\n\n" + texts.quiet_text(saved))[:3900],
                               reply_markup=keyboards.quiet_kb())
    await cb.answer()
