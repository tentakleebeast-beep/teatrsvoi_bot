"""Разбор свободного текста: суммы, даты, черновик операции, имена файлов."""

from __future__ import annotations

import re
from datetime import date as _date
from datetime import datetime as _dt

import texts

from config import LOCAL_TZ


def command_name(text: str | None) -> str:
    """Первое слово сообщения как команда: `/quiet@bot`, `/тихие` → `/quiet`, `/тихие`.

    Зачем не только `aiogram.filters.Command`. Telegram разбирает командами лишь
    латиницу и цифры, поэтому русская команда (`/тихие`, `/неделя`) приходит обычным
    текстом, и `Command(...)` её не видит. Адрес `@имя_бота` из группы отрезаем:
    иначе в групповом чате команда не сработает вовсе.
    """
    first = (text or "").strip().split(maxsplit=1)[0].lower() if (text or "").strip() else ""
    return first.split("@", 1)[0]


def detect_event_kind(text: str = "") -> str | None:
    """Тип события из фразы: от него зависит строгость поиска окон (docs/architecture/42)."""
    low = (text or "").lower()
    for kind, pattern in texts.KIND_WORDS:
        if re.search(pattern, low):
            return kind
    return None


# --- свободный текст → карточка операции (docs/development/82 §4) ----------------------
# Текст первичен: если в нём есть сумма, бот сразу собирает операцию и показывает её
# на подтверждение. Результат разбора не «выплёвывается» в чат, а превращается в
# карточку с деталями: категория, сумма, дата, статья.

INCOME_WORDS = ("получил", "получила", "пришло", "приход", texts.WORD_INCOME, "взнос", "взносы",
                "внесл", "внесли",
                "собрали", "оплатили взнос", "поступило")


EXPENSE_WORDS = ("потратил", "потратила", "оплатил", "оплатила", "заплатил", "заплатила",
                 "купил", "купила", texts.WORD_EXPENSE, "потрачено")


# Категория → направление. Доход нельзя записать в расходную статью: сервер
# этого не проверял, поэтому «аренда» из фразы «получила за аренду» уходила
# в «Аренда помещения» при tx_type=income (нашёл UX-прогон 30.10.2026).
CATEGORY_HINTS = (
    (("аренда", "аренду", "помещение"), "Аренда помещения", "expense"),
    (("гонорар", "зарплат", "доля", "выплат"), "Гонорары и доли", "expense"),
    (("свет", "жкх", "коммунал", "вода", "электрич"), "Коммунальные платежи", "expense"),
    (("реклам", "афиш", "полиграф", "печат"), "Реклама и полиграфия", "expense"),
    (("костюм", "реквизит"), "Реквизит и костюмы", "expense"),
    (("декорац", "ремонт"), "Ремонт и содержание", "expense"),
    (("расходник", "хозтов", "бумага"), "Расходники и хозтовары", "expense"),
    (("фото", "видео", "съёмк", "съемк"), "Фото/видео", "expense"),
    (("тренинг", "мастер-класс"), "Тренинги и занятия", "expense"),
    (("спектакл", "закупк"), "Закупки к спектаклям", "expense"),
    (("взнос", "взносы"), "Взносы студийцев", "income"),
    (("донат", "пожертв"), "Донат", "income"),
    (("билет", "показ"), "Продажа билетов", "income"),
    # Доход за помещение — это субаренда (Валерий, Добряки, Форма-Т), а расход —
    # «Аренда помещения» (Прачка). Решение заказчика 30.10.2026.
    (("субаренд", "сдал", "сдали", "аренда", "аренду", "снимает"), "Субаренда", "income"),
    (("курс",), "Курсы", "income"),
    (("подарок",), "Подарок", "income"),
    (("возврат",), "Возвраты", "income"),
)


DEFAULT_CATEGORY = {"expense": texts.CATEGORY_OTHER, "income": "Прочее (доход)"}


MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
          "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}


# --- защита бюджета от интервала времени (карта t_ebce081e) ---------------------
# «с 15-00 до 18-00» — это часы работы, а не сумма 15 ₽. Раньше такая реплика
# рождала черновик расхода «Прочее, 15 ₽» и человек подтверждал не то, что просил.
# Правило узкое: блокируем разбор только когда в строке НЕТ денежного признака
# и есть интервал часов. «3000 на реквизит» остаётся операцией (docs/82 §4.1).
MONEY_MARKS = ("₽", "руб", "р.", "тыс", "потрат", "заплат", "оплат", "купил", "купила",
               "получил", "получила", "приход", "взнос", "расход", "доход", "перевел",
               "перевела", "собрали", "поступило", "донат", "выручк")

#: Интервал часов: «с 15 до 18», «с 15-00 до 18-00», «15-18», «19:00».
TIME_SPAN = re.compile(
    r"(?:\bс\s*)?\b\d{1,2}(?:[:.]\d{2})?\s*(?:до|-)\s*\d{1,2}\b"
    r"|\b\d{1,2}(?:[:.]\d{2})?\s*-\s*\d{1,2}\b"
    r"|\b\d{1,2}[:.]\d{2}\b")


def has_money_mark(text: str) -> bool:
    """Есть ли в фразе признак денег: знак валюты или слово операции."""
    low = (text or "").lower()
    return any(mark in low for mark in MONEY_MARKS)


def looks_like_time(text: str) -> bool:
    """Фраза про часы, а не про деньги: «с 15-00 до 18-00», «15-18», «в 19:00»."""
    low = (text or "").lower().replace("–", "-").replace("—", "-")
    return bool(TIME_SPAN.search(low))


def parse_amount(text: str) -> float | None:
    """Сумма из свободной фразы: «15 000», «15к», «2,5 тыс», «3000 ₽»."""
    lowered = text.lower().replace("\u00a0", " ")
    match = re.search(r"(\d[\d\s.,]*)\s*(к|тыс[.]?|руб|₽)?", lowered)
    if not match:
        return None
    raw = match.group(1).replace(" ", "").replace(",", ".")
    raw = raw.rstrip(".")
    try:
        value = float(raw)
    except ValueError:
        return None
    unit = (match.group(2) or "").lower()
    if unit in ("к", "тыс", "тыс."):
        value *= 1000
    return value if value > 0 else None


def parse_date(text: str) -> str:
    """Дата из фразы: «дд.мм», «5 сентября», иначе — сегодня."""
    today = _dt.now(LOCAL_TZ).date()
    match = re.search(r"\b(\d{1,2})[.](\d{1,2})(?:[.](\d{2,4}))?\b", text)
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        year = int(match.group(3)) if match.group(3) else today.year
        if year < 100:
            year += 2000
        try:
            return _date(year, month, day).isoformat()
        except ValueError:
            pass
    lowered = text.lower()
    day_match = re.search(r"\b(\d{1,2})\b", lowered)
    for stem, month in MONTHS.items():
        if stem in lowered and day_match:
            day = int(day_match.group(1))
            try:
                return _date(today.year, month, day).isoformat()
            except ValueError:
                pass
    match = re.search(r"за\s+([а-яё]+)", lowered)
    if match:
        for stem, month in MONTHS.items():
            if match.group(1).startswith(stem):
                return _date(today.year, month, 1).isoformat()
    return today.isoformat()


def text_to_draft(text: str) -> dict | None:
    """Собирает черновик операции из фразы. None — если это не про деньги."""
    if looks_like_time(text) and not has_money_mark(text):
        return None
    amount = parse_amount(text)
    if amount is None:
        return None
    lowered = text.lower()
    tx_type = "income" if any(word in lowered for word in INCOME_WORDS) else "expense"
    if any(word in lowered for word in EXPENSE_WORDS):
        tx_type = "expense"
    category = DEFAULT_CATEGORY[tx_type]
    for stems, name, direction in CATEGORY_HINTS:
        if direction == tx_type and any(stem in lowered for stem in stems):
            category = name
            break
    counterparty = ""
    match = re.search(r"\bот\s+([А-ЯЁ][а-яё-]+)", text) or re.search(r"\bу\s+([А-ЯЁ][а-яё-]+)", text)
    if match:
        counterparty = match.group(1)
    return {"amount": amount, "tx_type": tx_type, "category": category,
            "tx_date": parse_date(text), "counterparty": counterparty or None,
            "purpose": counterparty or ""}


# --- файлы (Ш-3, docs/development/86) ---------------------------------------

# 20 МБ — предел Bot API на скачивание, а не наше решение (docs/development/86 §2)
FILE_MAX_BYTES = 20 * 1024 * 1024


def human_size(size: int) -> str:
    """Размер по-человечески: «12 КБ», «4,5 МБ» — без «12345678 байт»."""
    if size >= 1024 ** 2:
        return f"{size / 1024 ** 2:.1f} МБ".replace(".", ",")
    if size >= 1024:
        return f"{size / 1024:.0f} КБ"
    return f"{size} Б"


def human_duration(seconds: int) -> str:
    """Длительность для человека: «0:42», «3:07», «1:02:15» — как в плеере."""
    total = max(int(seconds or 0), 0)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


# --- выдача файлов (Ш-4) -----------------------------------------------------

# 50 МБ — предел Bot API на отправку ботом. Всё, что больше, уходит ссылкой (FR-K7).
FILES_IN_CHAT_LIMIT = 50 * 1024 * 1024


# --- папки (Ш-6) -------------------------------------------------------------

def split_folder_path(text: str) -> list[str]:
    """«Спектакли/Пурга/шумы» → ['Спектакли', 'Пурга', 'шумы'].

    Пустые куски выбрасываются: «А//Б» — это опечатка, а не папка с пустым именем.
    """
    return [part.strip() for part in (text or "").split("/") if part.strip()]
