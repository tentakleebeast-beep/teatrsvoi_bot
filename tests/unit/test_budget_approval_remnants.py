"""Остатки второго подтверждения крупных сумм убраны (решение 30.10.2026).

Механизма нет: расходы вносят только организаторы, второй подтверждающий никого не
защищает (`services/tool-api/app/api/budget.py:261`). В проде остались исторические
события `budget.approval.*` (проверено read-only 04.11.2026: requested — 1,
rejected — 1, approved — 0), поэтому старые строки аудита показываются одной
пометкой «Крупная сумма (архив)», а не сырым кодом и не двумя отдельными метками.

Обоснование: FR-B5 (docs/development/19-functional-requirements.md, статус «убрано
30.10.2026», решение — docs/architecture/25 §4).
"""

from __future__ import annotations

import pathlib
import re

from app.pages.admin_audit import _SCRIPT_AUDIT

# Активные документы: человек читает их как требования, поэтому любое упоминание
# порога, флага или очереди обязано стоять рядом с пометкой «убрано 30.10.2026».
_ACTIVE_FILES = (
    "ARCHITECTURE.md",
    "docs/architecture/01-data-model.md",
    "docs/architecture/03-budget.md",
    "docs/architecture/05-notifications-and-audit.md",
    "docs/architecture/11-business-rules.md",
    "docs/architecture/17-business-algorithms.md",
    "docs/infrastructure/44-google-calendar-capabilities.md",
    "docs/development/12-bot-behavior.md",
    "docs/development/16-test-strategy.md",
    "docs/development/20-scenarios.md",
    "docs/development/23-ux-scenarios.md",
    "docs/development/31-user-guide.md",
    "docs/development/57-channels-and-routing.md",
    "docs/development/58-regression-test-plan.md",
    "docs/development/92-navigation-model.md",
    "docs/development/93-cjm.md",
    "bot/manifests/participants/_example.md",
    ".env.example",
    "services/tg-gateway/app/keyboards.py",
)

# Слова снятого механизма. «крупн» сюда не входит: слово живёт в обычной речи.
# «отправлено на подтверждение» и «итог согласования» — живые фразы старых документов
# (docs/57, 58): бот такого ответа не даёт (`services/tg-gateway/app/routers/budget.py:67`).
_RUINS = ("100 000", "second_approval", "pending_approval", "confirm_above",
          "CONFIRM_ABOVE", "budget_approval", "Подтвердить/Отклонить", "выше порога",
          "очередь подтверждения", "отправлено на подтверждение", "итог согласования",
          "approval:<id>")

# Решение и его запись: дата либо ссылка на разбор (`docs/architecture/25` §4).
_MARKERS = ("30.10.2026", "docs/architecture/25")

# Журналы решений и планы: текст не переписываем, но помечаем, что механизма нет.
_HISTORY_FILES = (
    "docs/development/70-obstacles.md",
    "docs/plans/07-roadmap-metrics.md",
    "docs/plans/37-mini-app-plan.md",
    "docs/development/55-requirements-review.md",
)


def _blocks(text: str) -> list[str]:
    """Абзацы без блоков кода: схема БД держит `pending_approval` как значение CHECK."""
    return [b.strip() for b in re.split(r"\n\s*\n", re.sub(r"```.*?```", "", text, flags=re.S))
            if b.strip()]


def test_метки_подтверждения_сведены_к_одной_архивной():
    """Двух меток нет; всё семейство budget.approval.* печатается одной пометкой."""
    assert '"budget.approval.approved"' not in _SCRIPT_AUDIT, "вернулась метка подтверждения"
    assert '"budget.approval.rejected"' not in _SCRIPT_AUDIT, "вернулась метка отклонения"
    assert 'LEGACY_APPROVAL = "budget.approval."' in _SCRIPT_AUDIT, "нет ветки для старых событий"
    assert "Крупная сумма (архив)" in _SCRIPT_AUDIT, "старые события не отображаются"


def test_справка_не_обещает_подтверждения_крупных_сумм():
    """В /help не должно быть механизма, которого нет: иначе человек ищет кнопки."""
    root = pathlib.Path(__file__).resolve().parents[2]
    for role in ("admin", "org"):
        text = (root / "bot" / "manifests" / "help" / f"{role}.md").read_text(encoding="utf-8")
        assert "100 000" not in text, f"справка {role} называет порог снятого механизма"
        assert "идёт на подтверждение" not in text, f"справка {role} обещает второе подтверждение"
        assert "Подтвердить/Отклонить" not in text, f"справка {role} обещает кнопки подтверждения"


def test_документация_не_описывает_снятую_очередь_как_живую():
    """Маршрута /webapp/admin/approval в коде нет — документация не должна его обещать."""
    root = pathlib.Path(__file__).resolve().parents[2]
    page = (root / "docs" / "development" / "73-admin-panel-webapp.md").read_text(encoding="utf-8")
    assert "/webapp/admin/approval" not in page, "в docs/73 вернулся снятый маршрут"
    assert "убрано 30.10.2026" in page, "нет пометки, что механизм убран"
    menu = (root / "docs" / "architecture" / "44-menu-map.html").read_text(encoding="utf-8")
    assert "/webapp/admin/approval" not in menu, "на карте меню вернулся снятый маршрут"
    assert "Крупные суммы ·" not in menu, "в панели снова обещана очередь крупных сумм"


def test_env_example_не_предлагает_порог_подтверждения():
    """Шаблон .env читают при выкладке (`deploy/server-deploy.sh` собирает из него прод-.env).

    Строка `CONFIRM_ABOVE_AMOUNT` держала настройку снятого механизма: человек заполнял
    порог, которого никто не читает (`services/tool-api/app/api/budget.py:261`).
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    text = (root / ".env.example").read_text(encoding="utf-8")
    assert "CONFIRM_ABOVE" not in text, "в шаблоне .env снова настройка снятого порога"
    assert "второе подтверждение" not in text, "шаблон .env обещает второе подтверждение"


def _unmarked(rel: str, text: str) -> list[str]:
    """Где снятый механизм упомянут без пометки «убрано 30.10.2026»/ссылки на решение."""
    out: list[str] = []
    for block in _blocks(text):
        lines = [line for line in block.splitlines() if line.strip()]
        # В таблице проверяем построчно: иначе пометка в одной строке прикрыла бы другую.
        units = lines if all(line.lstrip().startswith("|") for line in lines) else [block]
        for unit in units:
            hit = [token for token in _RUINS if token in unit]
            if hit and not any(marker in unit for marker in _MARKERS):
                out.append(f"{rel}: «{hit[0]}» без пометки — {unit.strip()[:100]}")
    return out


def test_активные_документы_помечают_снятый_механизм():
    """Активный документ вправе вспоминать порог только как убранный, а не как живой.

    Человек читает ARCHITECTURE, docs/03, 05, 11, 17, 20, 23, 31, 57, 58, 92, 93, 44
    (инфраструктура) и манифесты участников как требования: упоминание без пометки
    возвращает ожидание кнопок, которых нет (`services/tool-api/app/api/budget.py:261`).
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    problems: list[str] = []
    for rel in _ACTIVE_FILES:
        problems.extend(_unmarked(rel, (root / rel).read_text(encoding="utf-8")))
    assert not problems, "порог/подтверждение описан как действующий:\n" + "\n".join(problems)


def test_исторические_документы_помечены_как_история():
    """Журналы решений и планы не переписаны, но помечены: механизма больше нет."""
    root = pathlib.Path(__file__).resolve().parents[2]
    for rel in _HISTORY_FILES:
        text = (root / rel).read_text(encoding="utf-8")
        assert "30.10.2026" in text, f"{rel}: нет даты снятия механизма"
        assert "docs/architecture/25" in text, f"{rel}: нет ссылки на разбор решения"
    checkpoint = root / "docs" / "archive" / "15-checkpoint.md"
    assert checkpoint.read_text(encoding="utf-8").startswith("> **Архив.**"), \
        "срез 26.10.2026 перестал читаться как история"
