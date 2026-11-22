"""`/help` отдаёт памятку первого входа целиком (E13, docs/development/182 §3.2).

Обоснование: FR-A2 (код приглашения), FR-G16 (первый вход), docs/development/182
§3.2 и §6.2 п. 3. Памятка — константа `services/shared/onboarding.HELP_MEMO`;
`/help` не собирает её из кусков и не добавляет своих строк.

Отдельно проверяются слова, которых в памятке быть не должно: заявок нет (§4.1),
кодов в чате нет (§4.3.3), старая подпись «Дозаполнить профиль» заменена на
«Знакомство» (V-108, решение 22.11.2026).
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.shared.onboarding import HELP_MEMO  # noqa: E402

MENU_PY = ROOT / "services" / "tg-gateway" / "app" / "routers" / "menu.py"


def test_help_отдаёт_памятку_целиком() -> None:
    """Обработчик `/help` шлёт `HELP_MEMO` одной строкой, без склейки и правок."""
    source = MENU_PY.read_text(encoding="utf-8")
    start = source.index('@router.message(Command("help"))')
    body = source[start:start + 700]

    assert "message.answer(texts.HELP_MEMO)" in body, "памятка ушла не целиком"
    assert "HELP_MEMO +" not in body and "HELP_MEMO," not in body, \
        "к памятке приклеивают ещё текст"

    # Текст объявлен один раз — в services/shared/onboarding.py; `texts` только
    # реэкспортирует его (§3.3, §6.2 п. 4).
    texts_py = ROOT / "services" / "tg-gateway" / "app" / "texts.py"
    assert "from services.shared.onboarding import HELP_MEMO" in texts_py.read_text(
        encoding="utf-8")


def test_шаги_памятки_на_месте() -> None:
    """Шесть шагов §1.2 — по проверяемой подстроке на шаг."""
    for step in ("1. Код приглашения", "2. Повторный вход", "3. Если вы педагог",
                 "4. Знакомство", "5. Приложение", "6. Если что-то не работает"):
        assert step in HELP_MEMO, f"шага нет в памятке: {step}"


def test_в_памятке_нет_старых_обещаний() -> None:
    """Заявки, выбор себя, коды в чате и старая подпись кнопки — удалены (§6.2 п. 3)."""
    lowered = HELP_MEMO.lower()
    for gone in ("заявк", "выберите себя", "дозаполнить профиль", "подтвердит"):
        assert gone not in lowered, f"в памятке снова «{gone}»"
    assert "который дал организатор" not in HELP_MEMO


def test_организатор_упомянут_только_в_перечислениях() -> None:
    """Слово «организатор» — там, где перечислены роли, а не в шаге про код."""
    paragraphs = HELP_MEMO.split("\n\n")
    code_step = next(item for item in paragraphs if item.startswith("1. Код приглашения"))
    assert "организатор" not in code_step.lower(), "код даёт администратор, не организатор"
    allowed = ("доступом к составу", "она называется «Управление»")
    for number, item in enumerate(paragraphs):
        if "организатор" in item.lower():
            assert any(marker in item for marker in allowed), \
                f"«организатор» в неожиданном месте ({number}): {item[:80]}"
