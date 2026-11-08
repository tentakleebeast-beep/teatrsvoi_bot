"""
Детерминированные правила разбора — чистые функции без БД и сети.

Вынесены из main.py, чтобы их можно было покрыть unit-тестами
(docs/development/16 §3.1: unit-тесты не ходят в базу и не зависят от FastAPI).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any

# ---------------------------------------------------------------- бюджет

BUDGET_CATS: list[tuple[str, list[str]]] = [
    ("Тренинги и занятия", ["тренинг"]),
    # фамилий получателей здесь нет: они приходят из данных (extra_hints) — см.
    # docs/plans/129 §Ш-03a. Имя человека в коде после обезличивания указывало в пустоту.
    ("Гонорары и доли", ["гонорар", "доля", "%"]),
    ("Аренда помещения", ["аренд"]),
    ("Коммунальные платежи", ["комуналк", "коммуналк", "электрик", "вода", "жкх"]),
    ("Закупки к спектаклям", ["закупк"]),
    ("Реклама и полиграфия", ["программк", "афиш", "макет", "репертуарк", "реклам", "печат"]),
    ("Ремонт и содержание", ["ремонт", "кран", "раковин", "замок", "линолеум", "краск",
                             "прожектор", "переходник"]),
    ("Фото/видео", ["съемк", "видео", "фото", "монтаж", "оператор"]),
    ("Реквизит и костюмы", ["реквизит", "костюм", "ткан", "декорац"]),
    ("Расходники и хозтовары", ["кофе", "чай", "бумаг", "картридж", "цвет", "бутыл"]),
]

EXP_VERBS = ["расход", "потрат", "оплат", "заплат", "купил", "отдал", "перевел", "снял", "закуп"]
INC_VERBS = ["доход", "приход", "пришл", "поступ", "сдал", "выручк", "пожертвован"]

PROJECT_WORDS = {"пурга": "Пурга", "бунин": "Бунин", "литрейв": "ЛитРейв",
                 "насеком": "Жизнь насекомых"}

CATEGORY_FALLBACK = "Прочее"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е")).strip()


def detect_amount(text: str) -> float | None:
    """12000 / 12к / 12 тыс / «45 000» / «1,5к»."""
    t = _norm(text)
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:к|тыс)(?![а-яa-z0-9])", t)
    if m:
        return round(float(m.group(1).replace(",", ".")) * 1000)
    m = re.search(r"(\d{1,3}(?:[  ]\d{3})+)", t)
    if m:
        return round(float(re.sub(r"\s", "", m.group(1))))
    m = re.search(r"(?<![\d.,])(\d+(?:[.,]\d{1,2})?)(?![\d.,])", t)
    if m:
        return round(float(m.group(1).replace(",", ".")))
    return None


def detect_type(text: str) -> str | None:
    t = _norm(text)
    result = None
    if any(w in t for w in INC_VERBS):
        result = "income"
    if any(w in t for w in EXP_VERBS):
        result = "expense"
    return result


def detect_category(text: str, extra_hints: Iterable[tuple[str, str]] = ()) -> str | None:
    """Категория по тексту. `extra_hints` — (имя человека, категория) из данных.

    Подсказки проверяются последними: «аренда» и «коммуналка» важнее фамилии, случайно
    попавшей в строку. Пустой набор — обычное дело: конфига может не быть.
    """
    t = _norm(text)
    for name, keys in BUDGET_CATS:
        if any(k in t for k in keys):
            return name
    for hint, category in extra_hints:
        if hint and hint in t:
            return category
    return None


def detect_project(text: str) -> str | None:
    t = _norm(text)
    for word, name in PROJECT_WORDS.items():
        if word in t:
            return name
    return None


def detect_counterparty(text: str) -> str | None:
    m = re.search(r"(?:^|\s)([а-яе]+(?:ов|ев|ин|ский|ская|ова|ева|ина))\s*$", _norm(text))
    return m.group(1).capitalize() if m else None


def parse_budget_text(text: str, today: date | None = None,
                      extra_hints: Iterable[tuple[str, str]] = ()) -> dict[str, Any]:
    """
    Разбор траты. Возвращает контракт /tools/budget/parse:
    {ok, draft{...}, flags[]}.
    ok=True только если понятны сумма, тип и категория.
    """
    today = today or date.today()
    amount = detect_amount(text)
    tx_type = detect_type(text)
    category = detect_category(text, extra_hints=extra_hints)

    flags: list[str] = []
    if amount is None:
        flags.append("amount_missing")
    if tx_type is None:
        flags.append("type_missing")
    if category is None:
        flags.append("category_missing")

    return {
        "ok": bool(amount and tx_type and category),
        "draft": {
            "amount": amount,
            "tx_type": tx_type,
            "category": category or CATEGORY_FALLBACK,
            "tx_date": today.isoformat(),
            "purpose": (text or "").strip(),
            "project": detect_project(text),
            "counterparty": detect_counterparty(text),
        },
        "flags": flags,
        "engine": "rules-py",
    }


# ---------------------------------------------------------------- занятость

# Единственный справочник дней недели: все падежи и сокращения.
# Им пользуются разбор занятости (rules), разбор назначения (assign) и API.
WEEKDAYS = {
    "понедельник": 0, "пн": 0,
    "вторник": 1, "вт": 1,
    "среда": 2, "среду": 2, "среды": 2, "ср": 2,
    "четверг": 3, "четверга": 3, "чт": 3,
    "пятница": 4, "пятницу": 4, "пятницы": 4, "пт": 4,
    "суббота": 5, "субботу": 5, "субботы": 5, "сб": 5,
    "воскресенье": 6, "воскресенья": 6, "вс": 6,
}


WORK_DAY_START = 8          # рабочий день студии: 08:00–23:00 (решение заказчика 28.10.2026)
WORK_DAY_END = 24           # граница «до», не включается: 23:00 — последний рабочий час


def parse_availability(text: str, today: date | None = None) -> dict[str, Any]:
    """
    Разбор «занят в среду после 18», «свободен 28.10 с 10 до 14».
    Возвращает {ok, day, start, end, kind, label} или {ok: False, error}.
    Часы вне рабочего дня 08:00–23:00 обрезаются: их нельзя ни отметить, ни показать.
    """
    today = today or date.today()
    t = _norm(text)

    day: date | None = None
    if "послезавтра" in t:
        day = today + timedelta(days=2)
    elif "завтра" in t:
        day = today + timedelta(days=1)
    elif "сегодня" in t:
        day = today
    else:
        for word, wd in WEEKDAYS.items():
            if re.search(rf"\b{word}\b", t):
                delta = (wd - today.weekday()) % 7
                day = today + timedelta(days=delta or 7)
                break
    if day is None:
        # dd.mm[.yyyy] — год в тексте обязателен к сохранению: «07.01.2027» не должен
        # превращаться в текущий год (дефект Д-01 смоука Э3, FR-C2).
        m = re.search(r"(?<!\d)(\d{1,2})[.\s/](\d{1,2})(?:[./](\d{2,4}))?(?!\d)", t)
        if m:
            raw_year = m.group(3)
            if raw_year:
                year = int(raw_year) if len(raw_year) == 4 else 2000 + int(raw_year)
            else:
                year = today.year
            try:
                day = date(year, int(m.group(2)), int(m.group(1)))
            except ValueError:
                day = None
    if day is None:
        return {"ok": False, "error": "need_date"}

    kind = "busy"
    if re.search(r"свобод|могу\b|удобн", t):
        kind = "free"
    if "удобн" in t or "лучше" in t:
        kind = "preferred"
    if re.search(r"не могу|занят|не смогу|работаю", t):
        kind = "busy"

    start, end = WORK_DAY_START, WORK_DAY_END
    m = re.search(r"с\s*(\d{1,2})(?::(\d{2}))?\s*(?:до|по)\s*(\d{1,2})(?::(\d{2}))?", t)
    if m:
        start, end = int(m.group(1)), int(m.group(3))
    else:
        explicit_start = False
        m = re.search(r"после\s*(\d{1,2})", t)
        if m:
            start = int(m.group(1))
            explicit_start = True
        m = re.search(r"\bдо\s*(\d{1,2})", t)
        if m and not explicit_start:
            end = int(m.group(1))
        if "весь день" in t:
            start, end = WORK_DAY_START, WORK_DAY_END

    # рабочий день 08–23 — норма; 00–07 и «до 24» — исключение, которое человек
    # подтверждает отдельно (решение заказчика 28.10.2026, docs/development/22 §7)
    start = max(0, min(23, start))
    end = max(start + 1, min(24, end))
    outside = start < WORK_DAY_START or end > WORK_DAY_END

    names = {"busy": "занят", "free": "свободен", "preferred": "удобно"}
    # конец интервала не включается: 24 — это «до конца рабочего дня», в тексте 23:00
    shown_end = end - 1 if end >= 24 else end
    label = f"{day.isoformat()} {start:02d}:00–{shown_end:02d}:00, {names.get(kind, kind)}"
    if outside:
        label += " (вне рабочего дня 08–23)"
    return {"ok": True, "day": day.isoformat(), "start": start, "end": end, "kind": kind,
            "outside_work_day": outside, "label": label}


# ---------------------------------------------------------------- интервал без даты

# «с 10 до 14», «10-14», «с 10:00 до 14:00», «10 — 14»
_INTERVAL_RE = re.compile(
    r"(?:^|\s)(?:с\s*)?(\d{1,2})(?::(\d{2}))?\s*(?:до|по|—|–|-)\s*(\d{1,2})(?::(\d{2}))?")


def parse_interval(text: str) -> dict[str, Any]:
    """Интервал часов без даты — для формы занятости, где день уже известен.

    Критическая причина: разбор «занят в среду с 10 до 14» требует даты, и в форме,
    где дата уже выбрана, фраза «с 10 до 14» не разбиралась вовсе — бот уходил в цикл
    «не разобрал время» (инцидент 28.10.2026, обращение Виктора).
    """
    t = _norm(text)
    match = _INTERVAL_RE.search(t)
    if not match:
        return {"ok": False, "error": "need_time",
                "hint": "Напишите интервал: «с 10 до 14»"}
    start, end = int(match.group(1)), int(match.group(3))
    if not 0 <= start < end <= 24:
        return {"ok": False, "error": "bad_interval",
                "hint": "Начало должно быть раньше конца, часы от 0 до 24"}
    outside = start < WORK_DAY_START or end > WORK_DAY_END
    shown_end = end - 1 if end >= 24 else end
    label = f"{start:02d}:00–{shown_end:02d}:00"
    if outside:
        label += " (вне рабочего дня 08–23)"
    return {"ok": True, "start": start, "end": end,
            "outside_work_day": outside, "label": label}


# ---------------------------------------------------------------- каскад разбора

CONFIDENCE_THRESHOLD = 0.7
FALLBACK_CATEGORY = "Прочее"


def cascade_decide(text: str, allowed_categories: list[str],
                   llm_category: str | None = None,
                   llm_confidence: float | None = None,
                   today: date | None = None) -> dict[str, Any]:
    """
    Решение каскада docs/development/21: правила → модель (только из закрытого списка) → кнопки.

    Правила:
      * категория из правил всегда побеждает: они детерминированы и покрыты тестами;
      * модель может выбрать только то, что есть в allowed_categories;
      * уверенность ниже порога → решение не принимается, идём к человеку;
      * «Прочее» от модели не считается уверенным ответом: это признак «не знаю».
    """
    from datetime import date as _date

    parsed = parse_budget_text(text, today=today or _date.today())
    draft = parsed["draft"]
    rules_category = draft["category"] if parsed["ok"] else None

    decision = {"ok": parsed["ok"], "draft": draft, "flags": parsed["flags"],
                "category_rules": rules_category, "category_llm": None,
                "category_final": None, "confidence": None, "source": None,
                "need": None, "candidates": []}

    if rules_category and rules_category != FALLBACK_CATEGORY:
        decision.update(category_final=rules_category, source="rules", need=None)
        return decision

    allowed = [c for c in allowed_categories if c != FALLBACK_CATEGORY]
    decision["candidates"] = allowed[:5]

    llm_ok = (llm_category in allowed and
              llm_confidence is not None and llm_confidence >= CONFIDENCE_THRESHOLD)
    if llm_ok:
        decision.update(category_llm=llm_category, category_final=llm_category,
                        confidence=llm_confidence, source="llm", need=None)
        return decision

    decision.update(category_llm=llm_category, confidence=llm_confidence,
                    need="llm" if llm_category is None else "buttons",
                    source=None)
    return decision


# ---------------------------------------------------------------- идемпотентность


def idempotency_key(chat_id: int, message_id: int | None, kind: str = "budget") -> str:
    """Ключ операции: одно и то же сообщение нельзя записать дважды."""
    if message_id is None:
        return ""
    return f"tg:{kind}:{chat_id}:{message_id}"
