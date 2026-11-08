"""`GET /tools/quota/me` — сколько лимита AI осталось у человека (чтение).

Кнопка «Сколько мне осталось» (решение владельца 05.11.2026) отвечает цифрами, но
не тратит то, о чём спрашивают: маршрут только читает `v_llm_balance` и **не**
вызывает модель. Строку в `llm_usage` создаёт разговор, а не вопрос о лимите —
поэтому здесь нет ни `llm`, ни `agently`.

Ответ:

    {"ok": true, "tokens_left": 1234567, "tokens_limit": 2000000,
     "month_tokens": 765433, "requests_left": 75, "daily_limit": 80,
     "cost_limit": 400.0, "month_cost_rub": 12.34, "text": "..."}

`text` — те же числа человеческими словами: кнопка показывает текст, а не JSON.
Политика задаётся на трёх уровнях (`participant` → `role` → `global`), поэтому пустое
значение — это «лимит не задан», а не «ноль»: view отдаёт `null` в `tokens_limit` и
`daily_limit`, и текст обязан сказать это словами.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import core

router = APIRouter()

#: Поля, которые видит человек и клиент. Порядок — от «сколько осталось» к «что задано».
QUOTA_FIELDS = ("tokens_left", "tokens_limit", "month_tokens", "requests_left",
                "daily_limit", "cost_limit", "month_cost_rub")


def _number(value: int) -> str:
    """Число с разрядами: 1 234 567 читается, 1234567 — нет."""
    return f"{int(value):,}".replace(",", " ")


def _rub(value: float) -> str:
    """Рубли с копейками: 12,34 ₽."""
    return f"{float(value):.2f}".replace(".", ",") + " ₽"


def quota_text(row: dict) -> str:
    """Ответ словами. Пустой лимит называется пустым, а не нулём.

    `v_llm_balance` считает остаток как `coalesce(limit, 0) - расход`, поэтому при
    незаданном лимите `tokens_left` отрицателен. Показывать это как «минус 765 000
    токенов осталось» — ложь: лимита нет, а расход есть.
    """
    spent = int(row.get("month_tokens") or 0)
    tokens_limit = row.get("tokens_limit")
    if tokens_limit is None:
        lines = ["Токенов на месяц: лимит не задан. "
                 f"Израсходовано за месяц: {_number(spent)}."]
    else:
        lines = [f"Токенов на месяц осталось: {_number(row.get('tokens_left') or 0)} "
                 f"из {_number(tokens_limit)}."]

    cost_limit = row.get("cost_limit")
    spent_rub = float(row.get("month_cost_rub") or 0)
    if cost_limit is None:
        lines.append(f"Стоимость за месяц: лимит не задан. Израсходовано: {_rub(spent_rub)}.")
    else:
        lines.append(f"Стоимость за месяц: {_rub(spent_rub)} из {_rub(cost_limit)}.")

    daily_limit = row.get("daily_limit")
    if daily_limit is None:
        lines.append("Запросов на сегодня: лимит не задан.")
    else:
        left = int(row.get("requests_left") or 0)
        lines.append(f"Запросов сегодня осталось: {left} из {int(daily_limit)}.")
    return "\n".join(lines)


def read_quota(participant_id: int) -> dict | None:
    """Строка баланса человека: `None` — человека нет во view (не зарегистрирован)."""
    with core.pool.connection() as conn:
        row = conn.execute(
            "select tokens_left, tokens_limit, month_tokens, requests_left,"
            "       daily_limit, cost_limit, month_cost_rub"
            "  from v_llm_balance where participant_id = %s",
            (participant_id,)).fetchone()
    return dict(row) if row else None


@router.get("/tools/quota/me")
def quota_me(who: dict = Depends(core.current)) -> dict:
    """Лимит AI этого человека: только чтение, без модели и без записи расхода."""
    pid = int(who.get("id") or 0)
    if not pid:
        return {"ok": False, "error": "unknown_participant",
                "detail": "лимиты считаются по участнику; chat_id не привязан"}
    row = read_quota(pid)
    if row is None:
        return {"ok": False, "error": "no_quota_row",
                "detail": "участник не найден в v_llm_balance"}
    numbers = {field: row.get(field) for field in QUOTA_FIELDS}
    for key in ("tokens_left", "tokens_limit", "month_tokens", "requests_left", "daily_limit"):
        value = numbers[key]
        numbers[key] = None if value is None else int(value)
    numbers["cost_limit"] = None if row.get("cost_limit") is None else float(row["cost_limit"])
    numbers["month_cost_rub"] = float(row.get("month_cost_rub") or 0)
    return {"ok": True, **numbers, "text": quota_text(row)}
