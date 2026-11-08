"""Разделы Mini App описаны один раз: реестр вместо пяти списков (docs/development/92 §10).

Повод: «какой раздел видит человек с такой возможностью» было описано в навигации,
маршрутах экранов, разметке «Ещё» и списке «Управления». Новая вкладка требовала
четырёх правок, а забытая давала либо невидимый раздел, либо ссылку, которая всё
равно получит 403.

Ниже проверяется то, ради чего реестр и заведён: состав и порядок, скрытие по
возможности сразу во всех местах и то, что новый раздел — одна запись в `SECTIONS`.
Права на данные этими тестами не ослабляются: реестр отвечает только «что показывать».
"""

from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app import webapp  # noqa: E402
from app.api import webapp_api  # noqa: E402
from app.pages import admin, home, layout, routes  # noqa: E402

#: Порядок «Управления»: он же порядок записей реестра (сначала то, что ждёт решения).
MANAGEMENT_URLS = [
    "/webapp/admin/t/pending",
    "/webapp/admin/t/people",
    "/webapp/admin/t/invites",
    "/webapp/admin/t/rights",
    "/webapp/admin/t/archive",
    "/webapp/admin/t/feedback",
    "/webapp/admin/t/audit",
    "/webapp/broadcast",
    "/webapp/reconcile",
    "/webapp/services",
    "/webapp/admin/t/agent",
    "/webapp/admin/t/notifications",
]

#: Файлы, где раньше лежали копии списка разделов. Новых копий (словарь с готовыми
#: `title` и `url`) здесь быть не должно — только чтение реестра.
FORMER_COPY_SITES = (
    "services/tool-api/app/pages/layout.py",
    "services/tool-api/app/pages/routes.py",
    "services/tool-api/app/pages/home.py",
    "services/tool-api/app/api/webapp_api.py",
)


def _router_paths() -> set[str]:
    router = routes.build_router(actor=lambda: {}, caps_for=lambda who: [])
    return {route.path for route in router.routes}


def test_реестр_без_повторов_ключей_и_адресов():
    keys = [section.key for section in webapp.SECTIONS]
    urls = [section.url for section in webapp.SECTIONS]
    assert len(keys) == len(set(keys)), "ключ раздела повторяется"
    assert len(urls) == len(set(urls)), "два раздела на одном адресе — ссылка их не различит"
    assert all(section.url.startswith("/webapp/") for section in webapp.SECTIONS)
    assert all(section.caps for section in webapp.SECTIONS), "раздел без возможности виден всем"


def test_порядок_управления_один_и_тот_же():
    """Копий списка нет: порядок берётся из реестра, а не из четырёх файлов."""
    caps = ["*"]
    assert [row["url"] for row in webapp_api.management_rows(caps, {})] == MANAGEMENT_URLS
    assert [section.url for section in webapp.sections_for(caps, group="management")] == \
        MANAGEMENT_URLS


def test_навигация_и_управление_считаются_по_одному_реестру():
    """Подпись 4-й вкладки и непустое «Управление» — всегда одно и то же решение."""
    for caps in ([], ["schedule.availability.self"], ["budget.read", "budget.write"],
                 ["participants.view"], ["registration.approve"], ["notify.send"],
                 ["budget.approve"], ["audit.view"], ["*"]):
        has_management = bool(webapp_api.management_rows(caps, {}))
        assert layout.is_staff(caps) is has_management, caps
        assert (layout.tab_label("more", caps) == "Управление") is has_management, caps


def test_человек_без_возможности_не_видит_раздел_ни_в_навигации_ни_в_управлении():
    """Организатор с «Людьми», но без разбора обращений.

    Раздел «Обращения» не приходит ни строкой в «Управлении», ни подписью вкладки:
    скрывать его в одном месте и показывать в другом — та самая ошибка, из-за которой
    ссылка вела на экран, отдающий 403.
    """
    caps = ["participants.view"]
    rows = webapp_api.management_rows(caps, {"feedback": 3})
    assert [row["url"] for row in rows] == ["/webapp/admin/t/people"]
    assert "/webapp/admin/t/feedback" not in {row["url"] for row in rows}

    # Без управленческих разделов вкладка личная, и «Управление» пустое.
    actor = ["budget.read", "budget.approve", "schedule.availability.self"]
    assert layout.is_staff(actor) is False
    assert layout.tab_label("more", actor) == "Ещё"
    assert webapp_api.management_rows(actor, {}) == []


def test_сигнал_внимания_из_того_же_реестра():
    """«Требует внимания» не дублирует название и адрес: они в записи раздела."""
    assert webapp_api.attention_rows(["registration.approve"], {"pending": 2}) == [
        {"title": "Заявки", "meta": "ждут решения",
         "url": "/webapp/admin/t/pending", "count": 2}]
    assert webapp_api.attention_rows(["registration.approve"], {"pending": 0}) == []
    assert [row["title"] for row in webapp_api.attention_rows(["*"], {"feedback": 1})] \
        == ["Обращения"]


def test_у_каждого_экрана_реестра_есть_страница_и_маршрут():
    paths = _router_paths()
    for section in webapp.page_sections():
        assert isinstance(getattr(admin, section.page, None), str), section.key
        assert section.url in paths, f"нет маршрута для раздела {section.key}"


def test_новый_раздел_добавляется_одной_записью(monkeypatch):
    """Критерий «правка одного места»: дописали запись — появились все входы.

    Маршрут экрана, строка «Управления» и подпись вкладки строятся из `SECTIONS`,
    поэтому больше ни один файл трогать не нужно.
    """
    widgets = webapp.WebappSection(
        "widgets", ("widgets.view",), "Виджеты", "/webapp/admin/t/widgets",
        "проверка реестра", page="PEOPLE_PAGE")
    monkeypatch.setattr(webapp, "SECTIONS", webapp.SECTIONS + (widgets,))

    assert layout.is_staff(["widgets.view"]) is True
    assert webapp_api.management_rows(["widgets.view"], {}) == [
        {"title": "Виджеты", "meta": "проверка реестра", "url": "/webapp/admin/t/widgets"}]
    assert "/webapp/admin/t/widgets" in _router_paths()


def test_новый_личный_раздел_появляется_на_ещё(monkeypatch):
    """Критерий «правка одного места» для «Личного»: дописали запись — пришла данными.

    Строки «Личного» отдаёт `/webapp/home/data` (`more.personal`), а рисует скрипт
    страницы: страница — публичная константа, права на момент отдачи HTML неизвестны.
    """
    memos = webapp.WebappSection(
        "memos", ("notes.read",), "Заметки", "/webapp/memos", "мои записи",
        group=webapp.PERSONAL)
    monkeypatch.setattr(webapp, "SECTIONS", webapp.SECTIONS + (memos,))

    rows = webapp_api.personal_rows(["notes.read"])
    assert {"title": "Заметки", "meta": "мои записи", "url": "/webapp/memos"} in rows
    assert all(row["title"] != "Файлы студии" for row in rows)
    # Порядок — порядок реестра: «Файлы студии» раньше, новая запись после.
    assert [row["url"] for row in rows] == ["/webapp/memos"]


def test_вложенный_экран_не_становится_строкой_управления():
    """«Люди → Взносы» (Д6): свой адрес есть, строки в «Управлении» нет.

    Вход лежит на экране «Люди»; отдельная строка сделала бы бюджетное право
    «управленческим» и переключила четвёртую вкладку (docs/development/92 §10).
    """
    nested = [section for section in webapp.page_sections()
              if section.group == webapp.NESTED]
    assert [section.key for section in nested] == ["dues"]
    urls = {row["url"] for row in webapp_api.management_rows(["*"], {})}
    assert "/webapp/admin/t/dues" not in urls
    assert nested[0].url in _router_paths()
    assert layout.is_staff(["budget.write"]) is False


def test_копий_списка_в_пяти_файлах_не_осталось():
    """Словарь с готовыми `title` и `url` — это копия раздела, и её видно по AST."""
    copies: list[str] = []
    for rel in FORMER_COPY_SITES:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = {key.value for key in node.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)}
            values = {value.value for value in node.values
                      if isinstance(value, ast.Constant) and isinstance(value.value, str)}
            if {"title", "url"} <= keys and any(v.startswith("/webapp/") for v in values):
                copies.append(f"{rel}:{node.lineno}")
    assert not copies, "раздел описан в двух местах: " + ", ".join(copies)
