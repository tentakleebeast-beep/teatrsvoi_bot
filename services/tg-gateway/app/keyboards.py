"""Клавиатуры и состав меню: какие кнопки видит человек с его правами."""

from __future__ import annotations

import core
import texts
from aiogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from webapp_links import keyboard as webapp_keyboard

from config import WEBAPP_BASE_URL

# Кнопки, которые человек видит в чате вне меню и подменю. Держим их подписи здесь,
# а не литералами по месту: реестр функций агента (config/agent-features.yaml,
# поверхность `chat`) сверяется с этим кортежем, поэтому новая кнопка чата без
# записи в реестре роняет тест, а не появляется молча.
BTN_OPEN_MENU = "Открыть меню"
BTN_COPY_CODE = "Скопировать код"
CHAT_BUTTONS: tuple[str, ...] = (
    BTN_OPEN_MENU,
    texts.BTN_OPEN_WEEK,
    BTN_COPY_CODE,
)


def copy_code_kb(code: str,
                 rows: list[list[tuple[str, str]]] | None = None) -> InlineKeyboardMarkup:
    """Клавиатура: кнопка «Скопировать код» + обычные строки кнопок."""
    keyboard = [[InlineKeyboardButton(text=BTN_COPY_CODE,
                                      copy_text=CopyTextButton(text=code))]]
    for row in rows or []:
        keyboard.append([InlineKeyboardButton(text=text, callback_data=data)
                         for text, data in row])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _buttons_with_week(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """Кнопки действий плюс «Открыть неделю», если Mini App доступен по HTTPS."""
    markup = kb(rows)
    week = webapp_keyboard(texts.BTN_OPEN_WEEK, "/webapp/days", WEBAPP_BASE_URL)
    if week is not None:
        markup.inline_keyboard += week.inline_keyboard
    return markup


def with_home(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """Кнопки действий плюс «Открыть меню» — вход в хаб Mini App.

    Правило 124 §2 п.2: нераспознанное намерение чат не догадывается — он честно
    говорит «не разобрал» и даёт вход в дом действий. Адрес не задан или не https —
    кнопки нет вовсе (webapp_links), и текст остаётся честным без обещания ссылки.
    """
    markup = kb(rows)
    home = webapp_keyboard(BTN_OPEN_MENU, "/webapp/home", WEBAPP_BASE_URL)
    if home is not None:
        markup.inline_keyboard += home.inline_keyboard
    return markup


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows
    ])


# --- паритет чата с экраном: настройки уведомлений, «обычная неделя», состояние дня
# (карта AUD-5, реестр `docs/architecture/25` §9.4 — строки О3, З5, З1). Клавиатуры
# собраны здесь, а не по месту: сторож реестра функций агента читает keyboards.py и
# требует записи на каждую подпись, поэтому новая кнопка чата не появляется молча.
def quiet_kb() -> InlineKeyboardMarkup:
    """«Тихие часы»: типы уведомлений, привычный интервал, своё время (FR-G3)."""
    return kb([
        [(texts.BTN_QUIET_REHEARSALS, "quiet:type:rehearsals"),
         (texts.BTN_QUIET_DUES, "quiet:type:dues")],
        [(texts.BTN_QUIET_SCHEDULE, "quiet:type:schedule_changes")],
        [(texts.BTN_QUIET_MORNING, "quiet:preset:20:00-08:00"),
         (texts.BTN_QUIET_OFF, "quiet:preset:00:00-00:00")],
        [(texts.BTN_QUIET_CUSTOM, "quiet:custom")],
    ])


def week_kb() -> InlineKeyboardMarkup:
    """«Обычная неделя»: запомнить текущую и заполнить по ней (FR-C10)."""
    return kb([[(texts.BTN_WEEK_REMEMBER, "week:remember"),
                (texts.BTN_WEEK_APPLY, "week:apply")]])


def status_kb() -> InlineKeyboardMarkup:
    """Состояние дня: занят / свободен / не уверен — значения `WeekDayIn` (FR-D6)."""
    return kb([
        [(texts.BTN_STATUS_BUSY, "av:s:busy"), (texts.BTN_STATUS_FREE, "av:s:free")],
        [(texts.BTN_STATUS_UNCERTAIN, "av:s:uncertain")],
    ])


def skip_note_kb() -> InlineKeyboardMarkup:
    """Комментарий к дню необязателен: «Пропустить» — тот же путь без потери."""
    return kb([[(texts.BTN_SKIP, "av:n:skip")]])


def _candidates_kb(cands: list[dict]) -> InlineKeyboardMarkup:
    """Кнопки «кто из них вы»: только имена, не больше восьми (Ш-46, V-006)."""
    rows = [[(c["display_name"], f"reg:pick:{c['id']}")] for c in cands[:8]]
    rows.append([("Меня нет в списке", "reg:none")])
    return kb(rows)


# Панель — только у администратора (решение 29.10.2026). Организатору она не нужна:
# заявки и приглашения у него кнопками в разделе «Админ». Подтверждения крупных сумм
# нет — расход пишется сразу, любой суммы (снято 30.10.2026, docs/architecture/25 §4).
PANEL_ENTRY_CAPS = ("admin.panel",)


def panel_rows(caps: list[str]) -> list[list[tuple[str, str]]]:
    """Пункты админ-панели по возможностям: недоступное не показываем.

    Право пункта — то же, что проверяет сервер у его действия (docs/architecture/25
    §9.2, правило 5): «Права» и «Архив» — `participants.manage`, как `/admin/grant`
    и `/admin/archive`, а не `participants.view`. Иначе организатор видит вход,
    нажимает его и получает отказ (Л4, Л6).
    """
    cells: list[tuple[str, str]] = []
    if core.has(caps, "participants.view", "participants.manage"):
        cells.append((texts.TITLE_PEOPLE, "panel:people"))
    if core.has(caps, "registration.approve", "participants.manage"):
        cells.append((texts.TITLE_REQUESTS, "panel:pending"))
    if core.has(caps, "participants.manage"):
        cells.append(("Права", "panel:rights"))
    if core.has(caps, "participants.manage"):
        cells.append((texts.TITLE_ARCHIVE, "panel:archive"))
    if core.has(caps, "budget.read"):
        cells.append((texts.TITLE_BUDGET, "panel:money"))
    if core.has(caps, "feedback.triage"):
        cells.append((texts.TITLE_FEEDBACK, "panel:feedback"))
    if core.has(caps, "audit.view"):
        cells.append(("Аудит", "panel:audit"))
    if core.has(caps, "system.health"):
        cells.append(("Сервисы", "panel:health"))
    if core.has(caps, "budget.import"):
        cells.append(("Сверка файл ↔ база", "panel:reconcile"))
    if core.has(caps, "budget.import"):
        cells.append((texts.BTN_SHEET_RETRY, "panel:sheet_retry"))
    if core.has(caps, "registration.code.issue"):
        cells.append(("Выпустить инвайт-код", "panel:invite"))
    if core.has(caps, "registration.code.issue"):
        cells.append((texts.TITLE_CODES, "panel:codes"))
    return [cells[i:i + 2] for i in range(0, len(cells), 2)]


# Главное меню по группам (Ш-47, V-008). Решение 28.10.2026: перестроить сразу,
# админ-панель до релиза остаётся и в чате, и в Mini App.
# Группа — кнопка клавиатуры, внутри — пункты по возможностям (пустой кортеж — виден всем).
# Обращение №13: недоступное не показываем, пустые группы не рисуем.
MENU_GROUPS: tuple[tuple[str, tuple[tuple[str, tuple[str, ...]], ...]], ...] = (
    # Деньги (решение 30.10.2026): расход и доход — одна кнопка, форма в Mini App;
    # «Бюджет» видит только администратор; сверка листов — работа организатора.
    ("Деньги", ((texts.TITLE_MONEY, ("budget.write",)),
                (texts.TITLE_BUDGET, ("admin.panel",)),
                (texts.TITLE_RECONCILE, ("budget.import",)),
                (texts.TITLE_DUES, ("budget.read.self",)))),
    (texts.TITLE_AVAILABILITY, ((texts.TITLE_AVAILABILITY, ("schedule.availability.self",)),
               (texts.TITLE_SCHEDULE, ("schedule.read.all",)),
               (texts.TITLE_SLOTS, ("schedule.slots",)))),
    ("Профиль", ((texts.TITLE_PROFILE, ()),)),
    # Админ: у организатора — участники, заявки и приглашения; панель только у админа.
    ("Админ", ((texts.TITLE_ADMIN, PANEL_ENTRY_CAPS),
               (texts.TITLE_PEOPLE, ("participants.view",)),
               (texts.TITLE_REQUESTS, ("registration.approve",)),
               (texts.TITLE_INVITES, ("registration.code.issue",)))),
    # Ш-49, V-010: «Справка» — отдельный раздел под правом howto.read, пока только у админа.
    ("Помощь", ((texts.TITLE_FEEDBACK, ()), (texts.TITLE_CAPABILITIES, ()), (texts.TITLE_HELP, ("howto.read",)))),
)


# Постоянная клавиатура управляет диалогом, а не доменами (решение владельца 05.11.2026):
# домены живут в Mini App и в инлайн-входах, а три кнопки ниже отвечают на вопросы
# «что бот делает сейчас», «что он понял» и «сколько осталось». Поэтому они видны всем
# независимо от прав — это не действие домена, и отказом на них ответить нельзя.
BTN_RESET = "Обнулить"
BTN_TASK = "Озадачить"
BTN_QUOTA = "Сколько мне осталось"
DIALOG_CONTROLS: tuple[str, ...] = (BTN_RESET, BTN_TASK, BTN_QUOTA)


# Пункты (их разбирает run_menu_action) и группы (навигация) — разные сущности:
# группа открывает подменю и никогда не ходит в обработчик пункта.
MENU_ACTIONS: tuple[str, ...] = tuple(
    label for _, items in MENU_GROUPS for label, _ in items)


MENU_GROUP_LABELS: tuple[str, ...] = tuple(label for label, _ in MENU_GROUPS)


MENU_SURFACE: tuple[str, ...] = MENU_GROUP_LABELS + MENU_ACTIONS


def menu_groups(caps: list[str]) -> list[tuple[str, list[str]]]:
    """Состав меню: группы с доступными пунктами; пустые группы не возвращаются."""
    out: list[tuple[str, list[str]]] = []
    for label, items in MENU_GROUPS:
        shown = [text for text, need in items if not need or core.has(caps, *need)]
        if shown:
            out.append((label, shown))
    return out


def group_kb(caps: list[str], group: str) -> InlineKeyboardMarkup:
    """Подменю группы: только её пункты, доступные этому набору возможностей."""
    for label, items in menu_groups(caps):
        if label == group:
            rows = [[(text, f"mi:{text}") for text in items[i:i + 2]]
                    for i in range(0, len(items), 2)]
            return kb(rows)
    return kb([])


def menu_kb(caps: list[str] | None = None) -> ReplyKeyboardMarkup:
    """Клавиатура чата: три контроля диалога, а не домены (решение 05.11.2026).

    «Обнулить», «Озадачить», «Сколько мне осталось» видны всем: это управление
    разговором, а не действие домена. Домены живут в Mini App (вход — кнопка меню
    чата, `setChatMenuButton` в `/start`) и в инлайн-кнопках сообщений.

    Параметр `caps` оставлен, чтобы вызовы `menu_kb(caps)` не расходились с формой
    функции: состав клавиатуры от прав больше не зависит.
    """
    del caps        # состав от прав не зависит — см. docstring
    rows = [[KeyboardButton(text=BTN_RESET), KeyboardButton(text=BTN_TASK)],
            [KeyboardButton(text=BTN_QUOTA)]]
    return ReplyKeyboardMarkup(
        keyboard=rows, resize_keyboard=True, is_persistent=True,
        input_field_placeholder="Написать или выбрать",
    )


MAIN_MENU = menu_kb(["*"])      # полный набор — справка, тесты, фолбэк


def help_text(caps: list[str], ui_texts: dict | None = None) -> str:
    """Что умеет бот — ровно то, что доступно этому человеку, пункт за пунктом.

    Подписи берутся из конфигурации (`config/ui-texts.yaml`), умолчания — из
    `MENU_HELP`: правка текста не требует релиза.
    """
    menu_texts = (ui_texts or {}).get("menu") or {}
    help_texts = (ui_texts or {}).get("help") or {}
    lines = ["Что умею:"]
    for group, items in menu_groups(caps):
        lines.append("")
        lines.append(group)
        for item in items:
            help_line = menu_texts.get(item) or texts.MENU_HELP.get(item)
            lines.append(f"· {item} — {help_line}" if help_line else f"· {item}")
    lines.append("")
    lines.append(help_texts.get("free_text") or
                 f"Свободным текстом тоже можно: {texts.FREE_TEXT_EXAMPLES}.")
    return "\n".join(lines)


# ------------------------------------------------------------------ знакомство (№17)
def intro_skip_kb() -> InlineKeyboardMarkup:
    return kb([[("Пропустить", "intro:skip")]])


def intro_confirm_kb() -> InlineKeyboardMarkup:
    return kb([[(texts.BTN_SAVE, "intro:save")], [("Отменить", "intro:cancel")]])
