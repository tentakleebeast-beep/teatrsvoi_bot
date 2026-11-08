"""Один текст «Мои взносы» для чата и Mini App (FR-B7, Д12).

Повод: в чате человек читал «За текущий месяц учтено…», на экране — «Взнос закрыт:
внесено … из …», а месяц назывался по-разному. Один и тот же месяц выглядел как
два разных ответа (расхождение 4.7 отчёта 122). Здесь одна функция на оба сервиса:
tool-api кладёт её строки в ответ `GET /tools/dues/me` (`summary`), а tg-gateway
печатает те же строки в чате. Второй реализации текста быть не должно.

Функция ничего не читает из базы и не решает, что показывать: она переводит уже
посчитанный ответ (`tariff`, `current`, `debt`) в человеческие строки. Месяц берётся
из `current.period` — тот же, что считает сервер, поэтому чат и экран не могут
назвать разные месяцы.
"""

from __future__ import annotations

import re

from services.shared.format import MONTHS_NOM, money

_PERIOD = re.compile(r"^(\d{4})-(\d{2})")


def month_title(period: str | None) -> str:
    """«2026-11» → «ноябрь 2026»; кривой период — пустая строка, не исключение."""
    match = _PERIOD.match(str(period or ""))
    if not match:
        return ""
    year, number = int(match.group(1)), int(match.group(2))
    if not 1 <= number <= 12:
        return ""
    return f"{MONTHS_NOM[number - 1]} {year}"


def dues_lines(payload: dict) -> list[str]:
    """Строки «Мои взносы»: тариф, учтено ли за месяц, долг за прошлые. Пустые — нет."""
    lines: list[str] = []
    tariff = payload.get("tariff") or {}
    amount = tariff.get("amount")
    if amount:
        since = tariff.get("tariff_from")
        lines.append(f"Тариф: {money(float(amount))} в месяц"
                     + (f", с {since}" if since else ""))
    elif tariff.get("tariff") is not None:
        lines.append(f"Тариф: {money(float(tariff['tariff']))} в месяц, "
                     f"с {tariff.get('tariff_from')}")
    else:
        lines.append("Тариф не назначен: сумму определяет организатор.")

    current = payload.get("current") or {}
    month = month_title(current.get("period"))
    for_month = f"За {month}" if month else "За текущий месяц"
    if not current.get("charged"):
        lines.append(f"{for_month} начислений нет.")
    elif current.get("settled"):
        lines.append(f"{for_month} учтено: {money(current.get('paid', 0))}.")
    else:
        rest = float(current.get("due", 0)) - float(current.get("paid", 0))
        lines.append(f"{for_month} не учтено: внесено "
                     f"{money(current.get('paid', 0))} из {money(current.get('due', 0))}, "
                     f"осталось {money(rest)}.")

    debt = float(payload.get("debt") or 0)
    if debt > 0:
        lines.append(f"Долг за прошлые месяцы: {money(debt)}.")
    return lines
