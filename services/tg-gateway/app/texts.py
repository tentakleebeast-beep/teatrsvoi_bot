"""Тексты gateway: всё, что читает человек, — в одном месте."""

from __future__ import annotations

import datetime
from datetime import datetime as _dt
from html import escape

import _bootstrap  # noqa: F401  # корень репозитория в sys.path: services.shared
import errors

from services.shared.dues import dues_lines  # noqa: E402
from services.shared.format import MONTHS, WEEKDAYS_FULL, money  # noqa: E402

# --- общие слова и подписи -------------------------------------------
# Одна формулировка — одно место: её берут меню, панель, формы и ответы.
TITLE_AVAILABILITY = "Занятость"
TITLE_SCHEDULE = "Расписание"
TITLE_ADMIN = "Админ-панель"
TITLE_BUDGET = "Бюджет"
TITLE_REQUESTS = "Заявки"
TITLE_DUES = "Мои взносы"
TITLE_PROFILE = "Мой профиль"
TITLE_FEEDBACK = "Обратная связь"
TITLE_SLOTS = "Поиск окон"
TITLE_MONEY = "Расход и доход"
TITLE_RECONCILE = "Сверка листов"
TITLE_HELP = "Справка"
MENU_BUTTON = "Меню"          # кнопка Mini App в чате (routers/menu.py и main.py)
TITLE_PEOPLE = "Участники"
TITLE_CAPABILITIES = "Что умею"
TITLE_INVITES = "Приглашения"
TITLE_ARCHIVE = "Архив"
TITLE_CODES = "Коды приглашений"
TITLE_FILES = "файлы"
FEEDBACK_KIND_QUESTION = "Вопрос"
FEEDBACK_KIND_BROKEN = "Не работает"
FEEDBACK_KIND_GOOD = "Хорошо работает"
FEEDBACK_KIND_IMPROVE = "Хочу улучшить"
FEEDBACK_KIND_WISH_FL = "Просьба о доработке"
BTN_CANCEL = "Отмена"
BTN_SAVE = "Записать"
BTN_DONE = "Готово"
BTN_BACK = "Назад"
BTN_CONFIRM = "Подтвердить"
BTN_REJECT = "Отклонить"
BTN_TODAY = "Сегодня"
BTN_TOMORROW = "Завтра"
BTN_OTHER_DATE = "Другая дата"
BTN_OPEN_PROFILE = "Открыть анкету"
BTN_OPEN_WEEK = "Открыть неделю"
BTN_SHEET_RETRY = "Дописать в лист"
BTN_ARCHIVE = "В архив"
BTN_RESTORE = "Вернуть"
BTN_BLOCK = "Заблокировать"
BTN_UNBLOCK = "Разблокировать"
BTN_REVOKE = "Отозвать"
QUESTION_WHEN = "Когда?"
WORD_INCOME = "доход"
WORD_EXPENSE = "расход"
CATEGORY_OTHER = "Прочее"
ROLE_PARTICIPANT = "участник"
STATUS_IN_PROGRESS = "в работе"
STATUS_REJECTED = "отклонено"
STATUS_DONE = "сделано"
NO_ANSWER = "нет ответа"
DUES_DENIED = "Взносы доступны участнику студии."
PREFIX_WRITTEN = "Записал: "
PREFIX_REQUEST = "Заявка: "
STALE_BUTTON = "Кнопка устарела"
SAVE_FAILED = "Не удалось записать"
CANCELLED = "Отменено."
# --- подтверждение предложения агента (A-3, docs/development/108 §8) --------
AGENT_CARD_TITLE = "Агент предлагает записать операцию."
AGENT_CARD_FOOTER = ("Проверьте и подтвердите: до нажатия кнопки ничего не записано. "
                     "Кнопка действует 5 минут.")
AGENT_NO_WRITE = "Записывать расходы и доходы может участник с правом записи."
AGENT_ALREADY = "Эта операция уже записана — второй раз не создаю."
AGENT_NOTHING_WRITTEN = "Ничего не записал."
AGENT_EXPIRED = "Предложение устарело — попросите записать заново."
AGENT_WRITE_FAILED = "Не удалось записать. Попробуйте формой: «Расход» или «Доход»."
AGENT_CONFIRM_FAILED = "Подтвердить не получилось. Попробуйте ещё раз или запишите формой."
# --- задача: план, вход в приложение, работа по шагам (05.11.2026) -------------
BTN_RUN_PLAN = "Выполнить план"
AGENT_PLAN_TITLE = "Агент собрал план."
AGENT_PLAN_FOOTER = ("Проверьте шаги и подтвердите: до нажатия ничего не записано. "
                     "Если шаг не пройдёт, исполнение остановится на нём.")
AGENT_OPEN_BUTTON = "Открыть приложение"
AGENT_OPEN_NOTE = "Так я не догадываюсь, а открываю нужный экран."
AGENT_WORKING = "Работаю: смотрю данные…"
AGENT_STOPPED = "Остановил задачу. Ничего дальше не делаю."
AGENT_STEPS = "Шаги: "
AGENT_PLAN_DONE = "План выполнен."
AGENT_PLAN_PARTIAL = "План выполнен не полностью."
AGENT_EVENT_CARD_TITLE = "Агент предлагает назначить событие."
# --- три кнопки диалога (решение 05.11.2026, docs/development/82 §3.3) --------
# «Обнулить» снимает то, что бот не дособрал. Записанное не отменяется: для отката
# остаются «Вернуть» после записи и «Отмена» у черновика.
DIALOG_RESET_DONE = ("Сбросил. Могу ответить на вопрос, разобрать намерение или "
                     "записать что-то по вашей команде.")
TASK_FRAME_ASK = "Что за задача? Следующее сообщение разберу как задачу."
TASK_FRAME_FAILED = ("Не разобрал задачу. Сформулируйте иначе — или «Обнулить», "
                     "чтобы вернуться к обычному разговору.")
QUOTA_UNAVAILABLE = ("Счёт по лимитам сейчас недоступен — не отвечаю цифрами наугад. "
                     "Попробуйте позже.")
QUOTA_FALLBACK = "Лимит AI: {text}"
# Итог подтверждения записи: одна строка факта про заметку.
AGENT_NOTE_WRITTEN = "Заметка создана."
PREFIX_QUERY = "По запросу «"
PER_MONTH = "в месяц"
PREFIX_ROLES = "Роли: "
PREFIX_TARIFF = "Тариф: "
SCHEDULE_HEADER = "Расписание на 7 дней:"
SERVICE_UNAVAILABLE = "Сервис данных временно недоступен, попробуйте через минуту."
MESSAGE_UNAVAILABLE = "Сообщение недоступно"

# --- заметки (FR-F7) ---------------------------------------------------------
# Подсказки называют только канонические латинские команды (`commands.py`):
# кириллическую форму клиент Telegram не подсвечивает и не дополняет, и совет
# выглядит командой, которой в меню нет. Русская форма остаётся словами — её
# ловит `NOTE_PREFIX` (`routers/notes.py`).
NOTE_HINT = ("Напишите заметку после команды, например:\n"
             "/note взять реквизит из склада #реквизит\n"
             "Или фразой: „заметка: позвонить в зал“.")
#: Ответ на `/notes`: где живёт дом заметок и как записать одну здесь.
NOTES_HINT = ("Заметки открываются в приложении: «Ещё → Заметки».\n"
              "Записать здесь: /note текст заметки, например "
              "/note взять реквизит из склада #реквизит")
NOTE_CARD_TITLE = "Записать заметку?"
NOTE_CARD_FOOTER = "Проверьте: до нажатия кнопки ничего не записано."
NOTE_BTN_PRIVATE = "Сохранить личную"
NOTE_BTN_SHARED = "В общую"
NOTE_SCOPE_PRIVATE = "личная — видите только вы"
NOTE_SCOPE_SHARED = "общая — видят все участники"
NOTE_SAVED = "Заметка сохранена: "
NOTE_SAVE_FAILED = "Не удалось сохранить заметку. Попробуйте ещё раз."
NOTE_NO_WRITE = "Заметки доступны участнику студии с правом notes.write."
NOTE_STALE = "Черновик заметки устарел — начните заново."

ROLE_TITLES = {"admin": "администратор", "org": "организатор", "troupe": "актёр труппы"}


CAP_TITLES = {
    "registration.code.issue": "выпуск кодов", "registration.approve": "подтверждение заявок",
    "participants.view": "список участников", "participants.manage": "управление людьми",
    "budget.read": "бюджет", "budget.read.self": "свои взносы", "budget.write": "запись трат",
    "budget.import": "импорт бюджета", "schedule.read.all": "сводное расписание",
    "schedule.availability.self": "своя занятость", "schedule.slots": "подбор окон",
    "calendar.write": "события в календаре", "notes.write": "заметки",
    "files.access": "файлы", "notify.send": "рассылки", "audit.view": "аудит",
    "quota.manage": "лимиты AI", "system.health": "состояние сервисов",
    "howto.read": "справка",
}


def sheet_retry_text(res: dict) -> str:
    """Ответ человека после повтора записи в лист (Ш-37).

    Эндпоинт возвращает списки ID: дописанные, уже стоявшие в листе (append прошёл,
    ответ потерялся) и не удавшиеся. Человеку нужны три числа, а не JSON, поэтому
    «дописано N, не удалось M, осталось M» и до пяти текстов ошибок.
    """
    if not res.get("ok"):
        error = errors.error_code(res) or "нет ответа"
        if error == "no_spreadsheet":
            return "Лист бюджета не настроен: дописывать некуда."
        return "Повтор записи в лист не прошёл: " + str(error)
    written = res.get("written") or []
    failed = res.get("failed") or []
    already = res.get("already") or []
    remaining = int(res.get("remaining") or 0)
    if not res.get("attempted"):
        return "Незаписанных строк нет: в листе всё, что есть в базе."
    lines = [f"Дописано: {len(written)}, не удалось: {len(failed)}, "
             f"осталось: {remaining}."]
    if already:
        lines.append(f"Уже были в листе, подтверждены без повтора: {len(already)}.")
    for item in failed[:5]:
        lines.append(f"№{item.get('id')}: {str(item.get('error'))[:200]}")
    if len(failed) > 5:
        lines.append(f"… ещё {len(failed) - 5}, полный список — в журнале аудита.")
    if remaining:
        lines.append("Строки остались в очереди: повторите, когда лист ответит.")
    return "\n".join(lines)


def invite_text(code: str, expires_at: str | None, role: str) -> str:
    """Текст инвайт-кода (HTML): код в ``<code>`` — по нему тапают и копируют.

    Обращение №9: код раньше шёл голым текстом, ``parse_mode=None``, и его
    приходилось переписывать вручную. Теперь он копируется тапом, плюс есть
    кнопка с ``copy_text``.
    """
    until = str(expires_at or "")[:10]
    title = escape(ROLE_TITLES.get(role, role))
    tail = f"Действует до {escape(until)}, использовать можно один раз.\n" if until else \
           "Использовать можно один раз.\n"
    return (f"Код приглашения для роли «{title}»:\n\n"
            f"<code>{escape(code)}</code>\n\n"
            + tail +
            "Скопируйте код кнопкой ниже или нажмите на него в тексте.\n"
            "Передайте код человеку: он отправляет боту этот код и регистрируется.")


def person_card_text(card: dict) -> str:
    """Карточка человека для админ-панели в чате (Ш-50, обращение №25).

    Только чтение. Без HTML: имена и тексты обращений приходят от людей.
    """
    lines = [f"Карточка: {card.get('display_name') or '—'}"]
    aliases = [a["alias"] for a in (card.get("aliases") or [])]
    if aliases:
        lines.append("Короткие имена: " + ", ".join(aliases))
    lines.append("Роли: " + ", ".join(ROLE_TITLES.get(r, r)
                                        for r in (card.get("roles") or [])))
    if card.get("status"):
        lines.append("Статус: " + STATUS_TITLES.get(str(card["status"]), str(card["status"])))
    caps = card.get("capabilities") or []
    lines.append(f"Возможностей: {len(caps)}")
    if caps:
        lines.append(_fit(", ".join(caps), 700))
    lines.append(f"Время в пути: {card.get('travel_minutes')} мин")
    lines.append("Телефон: " + (card.get("phone") or "не указан"))
    lines.append("День рождения: " + (card.get("birthday") or "не указан"))
    tariff = (card.get("tariff") or {}).get("tariff")
    if tariff is None:
        lines.append("Тариф: не назначен")
    else:
        since = (card.get("tariff") or {}).get("tariff_from")
        lines.append(f"Тариф: {money(tariff)} в месяц" + (f" с {since}" if since else ""))
    if card.get("plays"):
        lines.append("Спектакли: " + str(card["plays"]))
    if card.get("roles_note"):
        lines.append("Роли по опроснику: " + str(card["roles_note"]))
    shows = card.get("shows") or []
    lines.append("Показы: " + ("; ".join(
        f"{s['title']} ({str(s['starts_at'])[:10]})" for s in shows) if shows else "участия нет"))
    feedback = card.get("feedback") or []
    lines.append("Последние обращения: " + ("; ".join(
        f"№{f['id']} {f['text']}" for f in feedback) if feedback else "нет"))
    return "\n".join(lines)[:3900]


STATUS_TITLES = {"active": "активен", "pending": "заявка",
                 "blocked": "заблокирован", "left": "вышел"}


def _fit(text: str, width: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[:width - 1] + "…"


def people_table(people: list[dict], limit: int = 3500) -> str:
    """Список участников моноширинной таблицей в ``<pre>`` (HTML).

    Обращение №14: строки «Имя — роли» читались как каша; теперь колонки
    имя, роли и статус. Длинные значения подрезаются, лишние строки — «… ещё N»,
    чтобы готовый HTML не резался посередине тега.
    """
    header = f"{'ИМЯ':<18} {'РОЛИ':<16} СТАТУС"
    lines = [header, "-" * len(header)]
    size = sum(len(x) + 1 for x in lines)
    shown = 0
    for p in people:
        roles = ", ".join(p.get("roles") or [p.get("role") or ""])
        status = STATUS_TITLES.get(str(p.get("status")), str(p.get("status") or ""))
        row = (f"{_fit(p.get('display_name') or '—', 18):<18} "
               f"{_fit(roles, 16):<16} {status}").rstrip()
        if size + len(row) + 1 > limit:
            break
        lines.append(row)
        size += len(row) + 1
        shown += 1
    if shown < len(people):
        lines.append(f"… ещё {len(people) - shown}")
    body = escape("\n".join(lines))
    return f"<pre>{body}</pre>"


def audit_result(row: dict, width: int = 40) -> str:
    """Короткий итог события для колонки «результат»."""
    action = str(row.get("action") or "")
    if action.endswith(".denied"):
        return "отказ"
    after = row.get("after")
    if isinstance(after, dict) and after:
        parts = [f"{k}={v}" for k, v in list(after.items())[:3]]
        return _fit(", ".join(parts), width)
    if isinstance(after, str) and after:
        return _fit(after, width)
    return "выполнено"


def audit_table(rows: list[dict], limit: int = 3500) -> str:
    """Аудит дня моноширинной таблицей в ``<pre>`` (HTML).

    Обращение №16: событие шло одной строкой «время действие» — не видно,
    кто и что сделал. Колонки: время, инициатор, действие, объект, результат.
    """
    header = (f"{'ВРЕМЯ':<12} {'КТО':<14} {'ДЕЙСТВИЕ':<24} "
              f"{'ОБЪЕКТ':<16} РЕЗУЛЬТАТ")
    lines = [header, "-" * len(header)]
    size = sum(len(x) + 1 for x in lines)
    shown = 0
    for r in rows:
        raw_ts = str(r.get("ts") or "")
        # «28.10 14:32» — день и время без года, в колонку 12 знаков
        ts = (f"{raw_ts[8:10]}.{raw_ts[5:7]} {raw_ts[11:16]}"
              if len(raw_ts) >= 16 else raw_ts)
        actor = r.get("actor_name") or {"bot": "бот", "system": "система"}.get(
            str(r.get("actor_type")), "человек")
        action = str(r.get("action") or "")
        entity = str(r.get("entity_type") or "")
        entity_id = r.get("entity_id")
        if entity_id:
            entity = f"{entity}#{entity_id}"
        line = (f"{_fit(ts, 12):<12} {_fit(actor, 14):<14} "
                f"{_fit(action, 24):<24} {_fit(entity, 16):<16} "
                f"{audit_result(r)}").rstrip()
        if size + len(line) + 1 > limit:
            break
        lines.append(line)
        size += len(line) + 1
        shown += 1
    if shown < len(rows):
        lines.append(f"… ещё {len(rows) - shown}")
    return f"<pre>{escape(chr(10).join(lines))}</pre>"


KIND_WORDS = (
    ("rental", r"(аренд|сда[тё]м\s+зал|сдать\s+зал)"),
    ("show", r"(показ|спектакл|премьер|выступлен)"),
    ("training", r"(тренинг|тренировк|мастер-класс|занятие\s+для)"),
    ("rehearsal", r"(репетиц|прогон|сбор\s+трупп)"),
)


def schedule_lines(events: list[dict]) -> list[str]:
    """Строки расписания: название мероприятия — ссылка на календарь (HTML).

    Обращение №10: раньше ссылка шла отдельной строкой, и в чате её никто не
    нажимал. Теперь название само ведёт в событие календаря.
    """
    out = ["Расписание на 7 дней:"]
    by_day: dict[str, list[dict]] = {}
    for event in events:
        day = (event.get("start") or "")[:10]
        if len(day) != 10:
            continue
        by_day.setdefault(day, []).append(event)
    for day in sorted(by_day):
        weekday = WEEKDAY_TITLES.get(datetime.date.fromisoformat(day).weekday(), "")
        out.append("")
        out.append(f"{weekday}, {day[8:10]}.{day[5:7]}")
        for event in sorted(by_day[day], key=lambda item: item.get("start") or ""):
            start = (event.get("start") or "")[11:16]
            end = (event.get("end") or "")[11:16]
            kind = KIND_TITLES.get(event.get("kind") or "", "")
            prefix = f"{escape(kind)}: " if kind else ""
            title = escape(event.get("title") or "без названия")
            link = event.get("link")
            if link:
                title = f'<a href="{escape(str(link), quote=True)}">{title}</a>'
            out.append(f"• {start}–{end} {prefix}{title}")
    return out


def html_join(lines: list[str], limit: int = 3800) -> str:
    """Склеить строки, не разрывая HTML-тег жёстким обрезанием."""
    out: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > limit:
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


WEEKDAY_TITLES = {0: "Понедельник", 1: "Вторник", 2: "Среда", 3: "Четверг",
                  4: "Пятница", 5: "Суббота", 6: "Воскресенье"}


WEEKDAY_SHORT = {0: "пн", 1: "вт", 2: "ср", 3: "чт", 4: "пт", 5: "сб", 6: "вс"}


def availability_lines(rows: list[dict]) -> list[str]:
    """Строки «свободно по дням»: рабочий день, пустые дни и часы вне 08–23."""
    out = ["Свободно по дням (рабочий день 08–23):"]
    for r in rows:
        day = datetime.date.fromisoformat(r["day"])
        title = WEEKDAY_SHORT[day.weekday()]
        label = f"{title}, {day:%d.%m}"
        if not r.get("known_cnt"):
            out.append(f"  {label}: отметок нет")
            continue
        line = (f"  {label}: свободно {r['free_cnt']} из {r['known_cnt']} ч, "
                f"людей {r.get('people_cnt', 0)}")
        if r.get("outside_hours"):
            line += f"; вне 08–23 ещё {r['outside_hours']} ч"
        out.append(line)
    return out


KIND_TITLES = {"rental": "Аренда", "rehearsal": "Репетиция", "show": "Показ",
               "training": "Тренинг", "meeting": "Встреча"}


PROJECT_WORDS = {"бунин": "Бунин", "пург": "Пурга", "литрейв": "ЛитРейв",
                 "насеком": "Жизнь насекомых"}


FEEDBACK_KINDS = {"bug": FEEDBACK_KIND_BROKEN, "wish": FEEDBACK_KIND_IMPROVE,
                  "wish-fl": FEEDBACK_KIND_WISH_FL,
                  "question": FEEDBACK_KIND_QUESTION, "praise": FEEDBACK_KIND_GOOD}


# Один пункт меню — одна строка справки. Список строится из самого меню, поэтому
# новый пункт без описания роняет тест, а справка не отстаёт от кнопок.
MENU_HELP: dict[str, str] = {
    TITLE_MONEY: "внести операцию: категория, сумма, дата учёта, статья, план или факт",
    TITLE_BUDGET: "итоги по месяцам и статьи — только администратор",
    TITLE_RECONCILE: "сверить базу с листами бюджета на Google Drive",
    TITLE_DUES: "тариф и учтено ли за текущий месяц",
    TITLE_AVAILABILITY: "отметить дни: свободен, занят, до какого часа; обычная неделя и запись в лист",
    TITLE_SCHEDULE: "что идёт на неделе: показы, репетиции, тренинги",
    TITLE_SLOTS: "найти время, когда нужные люди свободны: даты, кто нужен, длительность",
    TITLE_PROFILE: "анкета в окне: имя, контакты, короткие имена, род",
    TITLE_PEOPLE: "список людей; нажатие открывает профиль и тариф",
    TITLE_REQUESTS: "кто ждёт подтверждения — принять или отклонить",
    TITLE_INVITES: "выпустить код для нового человека",
    TITLE_ADMIN: "участники, заявки, права, приглашения, бюджет, аудит, лист бюджета",
    TITLE_FEEDBACK: "сообщить о проблеме или пожелании",
    TITLE_CAPABILITIES: "этот список",
    TITLE_HELP: "подробная инструкция по вашей роли",
}


FREE_TEXT_EXAMPLES = "«расход 3000 на реквизит», «занят в среду после 18», «что у меня завтра»"


DATA_INPUT_HINTS = {
    "budget": "Похоже на операцию с деньгами.",
    "availability": "Похоже на занятость.",
}


# --- назначение события: уточнения диалога (карта t_ebce081e) ----------------
# Вопрос задаётся только про то, чего не хватает; остальное уже понято и лежит
# в состоянии (`ad.text`), поэтому «завтра» не отменяет названных людей.
ASSIGN_ASK = {
    "no_kind": "Тип события не назван: репетиция, показ, тренинг или аренда?",
    "bad_date": "Дата не разобралась. Назовите её так: «завтра», «в пятницу» или «5.11».",
    "no_date": "День не назван. Скажите «завтра», «в пятницу» или «5.11».",
    "no_time": "Время не названо. Например: «с 15:00 до 18:00» или «в 19:00».",
}
ASSIGN_CANCEL_DONE = "Отменил назначение, ничего не записал."


def draft_card(draft: dict) -> str:
    """Карточка подтверждения: детали обязательны, иначе подтверждать нечего (NN/g)."""
    kind = "доход" if draft["tx_type"] == "income" else "расход"
    lines = [f"Записать {kind}?", "",
             f"• Категория: {draft['category']}",
             f"• Сумма: {money(float(draft['amount']))}",
             f"• Дата: {_dt.fromisoformat(draft['tx_date']).strftime('%d.%m.%Y')}"]
    if draft.get("purpose"):
        lines.append(f"• Статья: {draft['purpose']}")
    return "\n".join(lines)


def agent_card(proposal: dict) -> str:
    """Карточка подтверждения предложения агента: что будет сделано — до нажатия.

    Формулировки карточки живут здесь, а не в tool-api: агент присылает намерение,
    текст для человека собирает бот (граница карты A-3). Сумма, категория и дата —
    из уже проверенных аргументов предложения, а не из ответа модели.
    """
    args = proposal.get("args") or {}
    kind = WORD_INCOME if args.get("tx_type") == "income" else WORD_EXPENSE
    lines = [AGENT_CARD_TITLE, "",
             f"• Сумма: {money(float(args.get('amount') or 0))}",
             f"• Категория: {args.get('category') or CATEGORY_OTHER}",
             f"• Дата: {_human_date(args.get('tx_date'))}"]
    if args.get("purpose"):
        lines.append(f"• Статья: {args['purpose']}")
    if args.get("counterparty"):
        lines.append(f"• Контрагент: {args['counterparty']}")
    lines += ["", f"Тип: {kind}.", AGENT_CARD_FOOTER]
    return "\n".join(lines)


def note_card(draft: dict, shared: bool = False) -> str:
    """Карточка заметки: заголовок, текст и кому она будет видна — до записи.

    Формулировки живут здесь, а не в обработчике: тот же текст уходит и в ответ
    после сохранения, и в тест, который сверяет слова, а не код.
    """
    lines = [NOTE_CARD_TITLE, "",
             f"• Заголовок: {draft.get('title') or '—'}",
             f"• Текст: {draft.get('body') or '—'}"]
    tags = draft.get("tags") or []
    if tags:
        lines.append("• Теги: " + ", ".join(tags))
    lines.append(f"• Кому: {NOTE_SCOPE_SHARED if shared else NOTE_SCOPE_PRIVATE}")
    lines += ["", NOTE_CARD_FOOTER]
    return "\n".join(lines)


def _human_date(raw: object) -> str:
    """`2026-11-01` → `01.11.2026`; неразобранное отдаём как есть, а не падаем."""
    try:
        return _dt.fromisoformat(str(raw)).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return str(raw or "")


def agent_written(proposal: dict, deduped: bool = False, result: dict | None = None) -> str:
    """Итог подтверждения по виду действия: деньги, событие или заметка.

    Бюджетный шаблон «Записано: 0 ₽ · Прочее · .» на событии и заметке был ложью
    (ревью 05.11.2026): текст обязан называть то, что человек подтвердил.
    """
    if deduped:
        return AGENT_ALREADY
    tool = str(proposal.get("tool") or "")
    args = proposal.get("args") or {}
    if tool == "event.assign":
        card = (result or {}).get("card")
        if not isinstance(card, dict):
            args_card = args.get("card")
            card = args_card if isinstance(args_card, dict) else {}
        return agent_event_written(card, result or {})
    if tool == "note.create":
        return AGENT_NOTE_WRITTEN
    return (f"Записано: {money(float(args.get('amount') or 0))} · "
            f"{args.get('category') or CATEGORY_OTHER} · {_human_date(args.get('tx_date'))}.")


def agent_event_written(card: dict, result: dict) -> str:
    """Итог назначения события: что, когда, где — и честно про календарь."""
    title = card.get("title") or card.get("kind_title") or "событие"
    when = (f"{_human_date(card.get('date'))}, "
            f"{int(card.get('from_hour') or 0):02d}:00–"
            f"{int(card.get('to_hour') or 0):02d}:00")
    line = f"Назначено: {title} · {when} · {card.get('venue_name') or 'зал не указан'}."
    if result.get("in_calendar") is False:
        line += " В календарь не записалось — событие сохранено в базе."
    return line


def agent_event_text(card: dict) -> str:
    """Карточка события до записи: тип, когда, где, кто."""
    people = ", ".join(str(person.get("name")) for person in card.get("participants") or [])
    day = _human_date(card.get("date"))
    lines = [AGENT_EVENT_CARD_TITLE, "",
             f"• {card.get('kind_title') or 'Событие'}: {card.get('title') or '—'}",
             f"• Когда: {day}, {int(card.get('from_hour') or 0):02d}:00–"
             f"{int(card.get('to_hour') or 0):02d}:00",
             f"• Зал: {card.get('venue_name') or '—'}"]
    if people:
        lines.append(f"• Участники: {people}")
    lines += ["", AGENT_CARD_FOOTER]
    return "\n".join(lines)


def agent_note_text(args: dict) -> str:
    """Карточка заметки: заголовок, текст и кому она будет видна."""
    return note_card({"title": args.get("title"), "body": args.get("body"),
                      "tags": args.get("tags") or []},
                     shared=args.get("scope") == "shared")


def agent_proposal_card(proposal: dict) -> str:
    """Одна карточка на любой вид предложения: что именно будет записано."""
    tool = proposal.get("tool")
    args = proposal.get("args") or {}
    if tool == "event.assign" and isinstance(args.get("card"), dict):
        return agent_event_text(args["card"])
    if tool == "note.create":
        return agent_note_text(args)
    return agent_card(proposal)


def agent_plan_card(plan: dict) -> str:
    """Карточка плана: пронумерованные шаги и одно подтверждение на весь план."""
    steps = plan.get("steps") or []
    lines = [AGENT_PLAN_TITLE, ""]
    for index, step in enumerate(steps, 1):
        text = str(step.get("human_text") or "").strip() or str(step.get("tool") or "")
        lines.append(f"{index}. {text}")
    lines += ["", AGENT_PLAN_FOOTER]
    return "\n".join(lines)


def agent_plan_result(result: dict) -> str:
    """Итог исполнения плана: что прошло, на чём остановились. Частичный успех виден."""
    total = int(result.get("total") or 0)
    done = int(result.get("done") or 0)
    head = AGENT_PLAN_DONE if result.get("ok") else AGENT_PLAN_PARTIAL
    lines = [head, f"Выполнено шагов: {done} из {total}."]
    for item in result.get("results") or []:
        mark = "готово" if item.get("ok") else "не вышло"
        lines.append(f"{item.get('step')}. {item.get('title') or item.get('tool')}: {mark}")
    failed = result.get("failed_step")
    if failed:
        lines += ["", f"Остановился на шаге {failed.get('step')}: "
                      f"{failed.get('message') or failed.get('error')}"]
    return "\n".join(lines)


def with_steps(answer: str, step_line: str) -> str:
    """Добавить к ответу строку шагов, если она есть и ещё не названа."""
    line = (step_line or "").strip()
    if not line or line in (answer or ""):
        return answer
    return (answer or "").rstrip() + "\n\n" + AGENT_STEPS + line + "."


def dues_text(res: dict, limit: int = 6) -> str:
    """«Мои взносы» коротко: тариф, учтено ли за месяц, долг за прошлые.

    Реестр по месяцам человеку в чате не нужен — он в учёте у организатора
    (решение 29.10.2026). Строки строит общая функция `services/shared/dues.py`:
    тот же текст печатает экран Mini App, поэтому чат и экран не могут разойтись
    ни в месяце, ни в числах (FR-B7, Д12). `limit` оставлен ради вызовов, которые
    передают его явно; текст один и тот же.
    """
    return "\n".join(dues_lines(res))


FILE_HINT = ("Файл больше 20 МБ — таким его бот не скачает. "
             "Загрузите через Mini App: там загрузка идёт мимо Telegram.")

# Файлы пока только у админа (П-7, docs/development/92 §9.1): чат проверяет то же
# право, что и экран, поэтому отказ нужен и здесь, а не только в API.
FILES_ADMIN_ONLY = "Файлы пока доступны только администратору."


def today_answer(day) -> str:
    """Ответ на «какой сегодня день» — из календаря проекта, а не от модели.

    Дефект 01.11.2026: вопрос про дату уходил в LLM без текущей даты в промпте,
    и бот уверенно отвечал «27 октября 2023 года». Дату знает сервер, спрашивать
    её у модели незачем.
    """
    weekday = WEEKDAYS_FULL[day.weekday()]
    return f"Сегодня {weekday}, {day.day} {MONTHS[day.month - 1]} {day.year} года."


# ------------------------------------------------- тихие часы и типы уведомлений
# Строка О3 реестра (`docs/architecture/25` §9.4) и FR-G3: настройка доступна и с
# экрана, и из чата, а значения читаются из одного места — `notification_pref`.
TITLE_QUIET = "Тихие часы"
BTN_QUIET_REHEARSALS = "Репетиции"
BTN_QUIET_DUES = "Взносы"
BTN_QUIET_SCHEDULE = "Изменения расписания"
BTN_QUIET_MORNING = "20:00–08:00"
BTN_QUIET_OFF = "Выключить тихие часы"
BTN_QUIET_CUSTOM = "Своё время"
QUIET_HINT = "Напишите /quiet 20:00-08:00 — или нажмите кнопку."
QUIET_DENIED = "Тихие часы настраивает участник студии."
QUIET_UNAVAILABLE = "Настройки уведомлений сейчас недоступны. Попробуйте через минуту."
QUIET_BAD_TIME = ("Время пишется как 20:00-08:00. Например: /quiet 22:00-07:00, "
                  "/quiet выкл, /quiet 20-8.")
QUIET_SAVED = "Сохранено."
QUIET_OFF_WORD = "выкл"

#: Типы уведомлений: ключ в настройках — как их называть человеку.
QUIET_KINDS = (
    ("rehearsals", "репетиции"),
    ("dues", "взносы"),
    ("schedule_changes", "изменения расписания"),
)


def quiet_text(settings: dict) -> str:
    """«Тихие часы»: что приходит, что выключено и какой интервал (FR-G3).

    Значения приходят из `GET /tools/notifications/settings`, то есть из того же
    места, откуда их читает экран `/webapp/notifications`: чат ничего не считает
    и не хранит своего.
    """
    on = [title for key, title in QUIET_KINDS if settings.get(key)]
    off = [title for key, title in QUIET_KINDS if not settings.get(key)]
    quiet_from = str(settings.get("quiet_from") or "20:00")
    quiet_to = str(settings.get("quiet_to") or "08:00")
    lines = [TITLE_QUIET, ""]
    lines.append("Приходят: " + (", ".join(on) if on else "ничего"))
    if off:
        lines.append("Выключено: " + ", ".join(off))
    if quiet_from == quiet_to:
        lines.append("Тихие часы выключены: обычные уведомления приходят в любое время.")
    else:
        lines.append(f"Тихие часы: {quiet_from}–{quiet_to}")
        lines.append("Обычные уведомления в это время придут утром.")
        lines.append("Срочные изменения расписания приходят и в тихие часы.")
    lines += ["", QUIET_HINT]
    return "\n".join(lines)


# ------------------------------------------------------ «обычная неделя» (FR-C10)
TITLE_WEEK = "Обычная неделя"
BTN_WEEK_REMEMBER = "Запомнить неделю"
BTN_WEEK_APPLY = "Заполнить по обычной неделе"
WEEK_DENIED = "«Обычную неделю» настраивает участник студии."
WEEK_UNAVAILABLE = "«Обычная неделя» сейчас недоступна. Попробуйте через минуту."
WEEK_NOTHING_TO_SAVE = ("На этой неделе нет отмеченных дней — запоминать нечего. "
                        "Сначала отметьте занятость.")
WEEK_NO_TEMPLATE = "Обычная неделя ещё не сохранена: сначала нажмите «Запомнить неделю»."
WEEK_SAVED = "Запомнил обычную неделю: {filled} дн."
WEEK_APPLIED = ("Заполнил по обычной неделе: {applied} дн., "
                "пропущено отмеченных: {skipped}.")


def week_text(template: dict) -> str:
    """«Обычная неделя»: состояние каждого дня недели (FR-C10).

    Текст `item['text']` приходит с сервера (`/tools/availability/template`) —
    той же канонической формулировкой, что уходит в лист и читается экраном.
    """
    days = template.get("days") or []
    lines = [TITLE_WEEK]
    if not days:
        lines.append("Пока не сохранена: нажмите «Запомнить неделю» — возьму текущую.")
    else:
        lines.append("Как обычно:")
        for item in days:
            weekday = WEEKDAY_TITLES.get(item.get("weekday"), "").lower()
            lines.append(f"· {weekday}: {item.get('text') or ''}")
    lines += ["",
              "«Запомнить неделю» — взять текущую неделю за образец.",
              "«Заполнить по обычной неделе» — расставить её на эту неделю, "
              "не трогая уже отмеченные дни."]
    return "\n".join(lines)


# --------------------------------------------- состояния дня и комментарий (FR-C9)
BTN_STATUS_BUSY = "Занят"
BTN_STATUS_FREE = "Свободен"
BTN_STATUS_UNCERTAIN = "Не уверен"
BTN_SKIP = "Пропустить"
QUESTION_STATUS = "Что в этот день?"
QUESTION_NOTE = "Комментарий к дню? Напишите текстом или нажмите «Пропустить»."
STATUS_WORDS = {"busy": "занят", "free": "свободен", "uncertain": "не уверен"}
NOTE_LIMIT = 120


def day_label(day: str) -> str:
    """`2026-11-04` → `04.11.2026, среда`. Неразобранное отдаём как есть, не падая."""
    try:
        moment = datetime.date.fromisoformat(str(day))
    except ValueError:
        return str(day or "")
    return f"{moment:%d.%m.%Y}, {WEEKDAY_TITLES.get(moment.weekday(), '').lower()}".strip(" ,")


def day_card_text(day: str, status: str, start: int | None = None,
                  end: int | None = None, note: str = "",
                  *, question: bool = True) -> str:
    """Карточка дня: состояние, границы, комментарий (FR-C9, FR-D6).

    Формулировки те же, что у канона `availability_card.to_text`: «занят с 10:00
    до 14:00», «свободен до 15:00», «не уверен». Чат не придумывает свой язык —
    иначе человек читает одно, а в листе лежит другое.
    """
    label = STATUS_WORDS.get(status, status)
    if status != "uncertain" and (start is not None or end is not None):
        low = f"{int(start):02d}:00" if start is not None else None
        high = f"{int(end):02d}:00" if end is not None else None
        if low and high:
            label += f" с {low} до {high}"
        elif low:
            label += f" с {low}"
        else:
            label += f" до {high}"
    lines = [f"{day_label(day)} — {label}."]
    if note:
        lines.append(f"Комментарий: {note}.")
    if question:
        lines += ["", "Записать?"]
    return "\n".join(lines)
