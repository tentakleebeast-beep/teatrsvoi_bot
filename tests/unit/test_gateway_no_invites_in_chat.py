"""Кодов приглашения в чате больше нет: исходники бота их не содержат (V-118).

Обоснование: docs/development/182 §4.3.3 и §6.2 п. 5. Решение владельца 21.11.2026:
код выпускается, показывается и отзывается только в Mini App, поэтому чатовый
путь удалён целиком — не спрятан, а удалён.

Тест читает исходники, а не списки меню: подпись, собранная мимо `MENU_GROUPS`,
тоже находится. Обратная сторона: `tests/unit/test_menu_invites.py` проверяет,
что у каждой подписи меню есть ветка обработчика.
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY = ROOT / "services" / "tg-gateway" / "app"

#: Имена и подписи, которых после V-118 быть не должно нигде в гейтвее.
GONE = (
    "panel:invite",
    "panel:codes",
    "inv:role:",
    "inv:revoke:",
    "send_invite",
    "invite_text",
    "copy_code_kb",
    "TITLE_INVITES",
    "TITLE_CODES",
    "copy_code",
)


def _sources() -> list[pathlib.Path]:
    return sorted(GATEWAY.rglob("*.py"))


def test_имён_чатового_пути_нет() -> None:
    """Ни одного имени из удалённого пути: выпуск, показ, отзыв, копирование."""
    found: list[str] = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for name in GONE:
            if name in text:
                found.append(f"{path.relative_to(ROOT)}: {name}")
    assert not found, "чатовый путь работы с кодами вернулся: " + ", ".join(found)


def test_в_меню_нет_пунктов_кодов() -> None:
    """`MENU_GROUPS` и `panel_rows` не рисуют «Приглашения», «Коды», «Выпустить инвайт-код»."""
    import sys

    sys.path.insert(0, str(ROOT / "services" / "tg-gateway" / "app"))
    import keyboards  # noqa: PLC0415 — модуль бота без побочных эффектов

    labels = {text for _, items in keyboards.MENU_GROUPS for text, _ in items}
    assert labels.isdisjoint({"Приглашения", "Коды приглашений", "Выпустить инвайт-код"})

    panel = {text for row in keyboards.panel_rows(["*"]) for text, _ in row}
    assert panel.isdisjoint({"Приглашения", "Коды приглашений", "Выпустить инвайт-код"})


def test_кнопка_копирования_удалена_вместе_с_строкой_реестра() -> None:
    """`BTN_COPY_CODE` и его запись в реестре не висят в воздухе (docs/182 §4.3.3)."""
    keyboards = (GATEWAY / "keyboards.py").read_text(encoding="utf-8")
    assert "BTN_COPY_CODE" not in keyboards
    assert "Скопировать код" not in keyboards

    registry = (ROOT / "config" / "agent-features.yaml").read_text(encoding="utf-8")
    assert "chat.copy_code" not in registry, "запись реестра осиротела"
