"""Организатор без кодов и заявок: сторож состояния после миграции 068.

Решение владельца 19.11.2026 (вариант 2): организатор ведёт бюджет, расписание,
занятость и файлы, а коды приглашений и обработка заявок остаются администратору.
Миграция `068_org_without_approve` снимает у роли `org` право `registration.approve`;
`registration.code.issue` у неё не было никогда (`db/init/008`).

Проверяются три поверхности, которыми человек видит права, и одна граница:

1. меню чата (`keyboards.menu_groups`) — «Заявки» и «Приглашения» не появляются
   ни в одной группе, рабочие пункты организатора на месте;
2. разделы Mini App (`webapp.sections_for`) — нет `pending` и `invites`, а `people`,
   `dues`, `files`, `polls` остались;
3. справка `/help` (`bot/manifests/help/org.md`) — не обещает ни заявок, ни кодов;
4. применённый `db/init/008` не переписан: снятие живёт только в `068`, иначе
   «починка» правкой истории разошлась бы с ревизией и хэшем (`test_migrations_registry`).

Набор прав организатора — замер живой схемы после `068` и `071` (15 возможностей),
а не список из памяти: `budget.import, budget.read, budget.read.self, budget.write,
calendar.write, feedback.send, files.access, notes.write, participants.contact,
participants.view, poll.manage, schedule.availability.self, schedule.read.all,
schedule.slots, schedule.write`. `system.health` снят с `org` миграцией `071`
(V-121): входов у права не было, матрица не должна обещать недоступное.

Обоснование: FR-A6 (права решает сервер, оболочка только не показывает),
FR-A2 (код приглашения), карта t_848c60ed, docs/architecture/02-access-control.md §1.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

# `app` из tool-api — до путей гейтвея: у гейтвея тоже есть пакет `app`, и поздний
# импорт подхватил бы не тот.
sys.path.insert(0, str(ROOT / "services" / "tool-api"))
sys.path.insert(0, str(ROOT))

from app import webapp  # noqa: E402

#: Замер живой схемы после `068` и `071`: 15 возможностей роли `org`.
ORG_CAPS = [
    "budget.import", "budget.read", "budget.read.self", "budget.write", "calendar.write",
    "feedback.send", "files.access", "notes.write", "participants.contact",
    "participants.view", "poll.manage", "schedule.availability.self",
    "schedule.read.all", "schedule.slots", "schedule.write",
]
ADMIN_CAPS = ["*"]

#: Пункты, которые организатор теряет по решению 19.11.2026.
GONE = {"Заявки", "Приглашения"}


def _keyboards():
    """Модуль клавиатур гейтвея: грузим по файлу, чтобы не спорить с пакетом `app`."""
    gateway_app = str(ROOT / "services" / "tg-gateway" / "app")
    sys.path.insert(0, gateway_app)
    try:
        spec = importlib.util.spec_from_file_location(
            "gateway_keyboards_org", pathlib.Path(gateway_app) / "keyboards.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(gateway_app)
    return module


def test_в_меню_чата_у_организатора_нет_заявок_и_приглашений() -> None:
    """Пункт меню — по праву: у `org` нет ни `registration.approve`, ни кодов."""
    keyboards = _keyboards()
    groups = dict(keyboards.menu_groups(ORG_CAPS))
    items = {item for values in groups.values() for item in values}

    assert not (GONE & items), f"у организатора снова {sorted(GONE & items)}"
    assert groups["Люди"] == ["Участники"], "«Участники» организатор не теряет"
    assert {"Расход и доход", "Сверка листов", "Опросы"} <= items, \
        "рабочие пункты организатора сузились"

    admin_items = {item for _, values in keyboards.menu_groups(ADMIN_CAPS) for item in values}
    assert not (GONE & admin_items), "заявки и приглашения убраны из чата у всех (V-118)"


def test_в_mini_app_у_организатора_нет_разделов_заявок_и_приглашений() -> None:
    """Раздел хаба — тоже по праву: `pending` и `invites` организатору не рисуются."""
    keys = {section.key for section in webapp.sections_for(ORG_CAPS)}

    assert not ({"pending", "invites"} & keys), f"у организатора остались {sorted(keys)}"
    assert {"people", "dues", "files", "polls"} <= keys, \
        "рабочие разделы организатора сузились"

    admin_keys = {section.key for section in webapp.sections_for(ADMIN_CAPS)}
    assert "invites" in admin_keys, "коды остались у администратора — только в Mini App"
    assert "pending" not in admin_keys, "раздела «Заявки» нет ни у кого (docs/182 §4.1)"


def test_справка_организатора_не_обещает_заявок_и_кодов() -> None:
    """`/help` обещает ровно то, что человек видит в меню, — иначе он ищет кнопки."""
    text = (ROOT / "bot" / "manifests" / "help" / "org.md").read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines()]

    assert not [line for line in lines if line.startswith("- **Заявки**")], \
        "справка снова обещает организатору заявки"
    assert not [line for line in lines if line.startswith("- **Пригласить**")], \
        "справка снова обещает организатору коды приглашений"
    assert [line for line in lines if line.startswith("- **Расход и доход**")], \
        "справка потеряла рабочий пункт организатора"


def test_право_снято_миграцией_а_не_правкой_применённого_файла() -> None:
    """`008` остаётся как применён: снятие — только в `068`, иначе разойдётся хэш."""
    db = ROOT / "db" / "init"
    applied = (db / "008_rbac_capabilities.sql").read_text(encoding="utf-8")
    assert re.search(r"\(\s*'org'\s*,\s*'registration\.approve'\s*\)", applied), \
        "применённый 008 переписан: снятие права должно жить в новой миграции"

    migration = (db / "068_org_without_approve.sql").read_text(encoding="utf-8")
    assert "delete from rbac_role_capability" in migration
    assert "role = 'org' and capability = 'registration.approve'" in migration, \
        "068 не снимает право у роли org"
    assert "19.11.2026" in migration, "в миграции нет даты решения владельца"
