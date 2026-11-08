"""
Персональные данные в ответах: телефон показываем полностью только тем, кому он нужен.

Номера участников — не публичная информация: в списках и карточках чужой номер
маскируется («+7 917 ··· 24 18»), полный видят сам человек и те, кому выдали
возможность `participants.contact` (администратор и организатор по умолчанию).

Маска сохраняет то, что помогает узнать номер при сверке (код оператора и
последние две пары), и скрывает середину — по ней номер угадать нельзя.

Примеры в докстрингах и тестах вымышленные: живой номер в код и в тест не попадает.
"""

from __future__ import annotations

import re

MASK = "···"
CAPABILITY = "participants.contact"

NON_DIGITS = re.compile(r"\D")


def _digits(phone: str) -> str:
    return NON_DIGITS.sub("", phone)


def mask_phone(phone: str | None) -> str | None:
    """+79171232418 → +7 917 ··· 24 18; короткий или пустой номер не маскируем."""
    if not phone:
        return phone
    digits = _digits(phone)
    if len(digits) < 10:
        return phone
    tail = f"{digits[-4:-2]} {digits[-2:]}"
    return f"+{digits[0]} {digits[1:4]} {MASK} {tail}"


def phone_for(viewer_id: int | None, owner_id: int, phone: str | None,
              caps: list[str] | None = None) -> str | None:
    """Полный номер — свой или при праве `participants.contact`, иначе маска."""
    if not phone:
        return phone
    caps = caps or []
    if viewer_id is not None and viewer_id == owner_id:
        return phone
    if "*" in caps or CAPABILITY in caps:
        return phone
    return mask_phone(phone)
