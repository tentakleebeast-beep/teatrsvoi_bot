"""Права на экран Mini App: словарь экранов, разметка и меню чата.

Правило владельца 08.11.2026: если действие человеку недоступно, вход в него не
показываем — ни плитку в хабе, ни строку, ни кнопку, ни прямой адрес. До этой
карты правило держалось только на хабе (`webapp.sections_for()`), а внутри 30
экранов кнопки рисовались всегда, и право всплывало отказом 403 после нажатия.

Здесь проверяется то, что можно проверить без базы и браузера:

* `SCREEN_CAPS` покрывает ровно те экраны, которые отдают маршруты, — новый экран
  не может появиться без решения «кому он положен»;
* записи разделов хаба в нём совпадают с реестром `SECTIONS` — два списка не
  разъедутся;
* каждая ссылка на экран, закрытый не всем ролям, помечена `data-svoi-cap`;
  оболочка прячет такие элементы по ответу `/webapp/nav`;
* в меню чата нет доменных действий — только управление разговором.

Обоснование: FR-H9 (права решает сервер, оболочка только не показывает),
docs/development/92 §9–10 (модель навигации), карта t_8873ed85,
отчёт `docs/reports/146-screen-rights-matrix.md`.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from app import webapp  # noqa: E402
from app.pages import home, layout, routes  # noqa: E402
from app.webapp import SCREEN_CAPS, screen_flags, screen_visible  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402

#: Возможности, которые есть у всех четырёх ролей (`db/init/008` + 031–061):
#: пересечение наборов admin, org, troupe и master. Экран, которому достаточно
#: любой из них, видят все — такой вход помечать не надо. Права сверяются с живой
#: базой в `tests/integration/test_webapp_screen_rights.py`.
UNIVERSAL_CAPS = frozenset({
    "schedule.availability.self",
    "notes.write",
    "files.access",
})

#: Четыре роли реестра (docs/development/92 §1, 061): у каждой свой набор возможностей.
#: `student` убран миграцией 017, `master` добавлен 061 — четвёртая роль сегодня педагог.
ROLE_CAPS = {
    "admin": set(UNIVERSAL_CAPS) | {
        "budget.read.self", "registration.code.issue", "registration.approve",
        "participants.view", "participants.manage", "budget.read", "budget.write",
        "budget.import", "schedule.read.all", "schedule.slots", "calendar.write",
        "notify.send", "audit.view", "quota.manage", "system.health", "admin.panel",
        "feedback.triage",
    },
    "org": set(UNIVERSAL_CAPS) | {
        "budget.read.self", "registration.approve", "participants.view", "budget.read",
        "budget.write", "budget.import", "schedule.read.all", "schedule.slots",
        "calendar.write", "system.health",
    },
    "troupe": set(UNIVERSAL_CAPS) | {"budget.read.self", "system.health"},
    "master": set(UNIVERSAL_CAPS) | {
        "schedule.read.all", "schedule.slots", "schedule.write", "participants.view",
    },
}

ANCHOR = re.compile(r'<a\b[^>]*href="(/webapp[^"#?]*)"[^>]*>', re.S)
CAP_ATTR = re.compile(r'data-svoi-cap="([^"]+)"')


def _screen_routes() -> set[str]:
    """Адреса экранов: маршруты, которые отдают HTML, а не JSON и не редирект."""
    router = routes.build_router(actor=lambda *a, **k: {}, caps_for=lambda who: [])
    found = set()
    for route in router.routes:
        if "GET" not in (getattr(route, "methods", None) or set()):
            continue
        if getattr(route, "response_class", None) is HTMLResponse:
            found.add(route.path)
    return found


def _page_sources() -> list[tuple[str, str]]:
    """Собранная разметка всех страниц: пары (имя модуля, готовый HTML).

    Берём константы модулей (`HOME_PAGE`, `WEEK_PAGE`, …), а не исходный текст:
    половина разметки собирается конкатенацией и строковыми кусками, и поиск по
    файлу пропустил бы ссылку, собранную из двух строк.
    """
    import importlib
    import pkgutil

    pages = []
    package = importlib.import_module("app.pages")
    for info in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(f"app.pages.{info.name}")
        for name, value in vars(module).items():
            if isinstance(value, str) and value.startswith("<!doctype"):
                pages.append((f"{info.name}.{name}", value))
    return pages


def _restricted(url: str) -> bool:
    """Закрыт ли экран хотя бы одной роли — тогда вход помечается правом.

    Достаточно любой из возможностей экрана (`screen_visible`), поэтому экран
    универсален, если хотя бы одна его возможность есть у всех ролей.
    """
    need = SCREEN_CAPS.get(url) or ()
    if not need:
        return False
    return not (set(need) & UNIVERSAL_CAPS)


def test_словарь_покрывает_ровно_экраны_маршрутов():
    """Новый экран без записи в `SCREEN_CAPS` — дырка в правиле, а не мелочь."""
    routes_found = _screen_routes()
    assert len(routes_found) == 31, f"экранов {len(routes_found)}, ожидалось 31"
    missing = sorted(routes_found - set(SCREEN_CAPS))
    assert not missing, "экраны без решения о правах: " + ", ".join(missing)
    extra = sorted(set(SCREEN_CAPS) - routes_found)
    assert not extra, "в словаре адреса, которых нет среди экранов: " + ", ".join(extra)


def test_разделы_хаба_и_экраны_совпадают_по_правам():
    """Плитка хаба и сам экран не могут закрываться разными правами."""
    for section in webapp.SECTIONS:
        assert SCREEN_CAPS[section.url] == section.caps, section.url


def test_плитки_хаба_не_шире_прав_роли():
    """(а) в хабе нет пунктов сверх прав: разделы отбирает один реестр."""
    for role, caps in ROLE_CAPS.items():
        for section in webapp.sections_for(caps):
            assert screen_visible(section.url, caps), (role, section.url)


def test_признак_экранов_в_nav_отвечает_по_правам():
    """Оболочка не решает сама: `screens` в `/webapp/nav` — приговор сервера."""
    flags = screen_flags(ROLE_CAPS["master"])
    assert set(flags) == set(SCREEN_CAPS)
    assert flags["/webapp/reconcile"] is False
    assert flags["/webapp/services"] is False
    assert flags["/webapp/home"] is True
    # Педагогу деньги не положены (140 §3): и экран, и вкладка закрыты.
    assert flags["/webapp/money"] is False
    assert layout.nav_payload(ROLE_CAPS["master"])["screens"] == flags
    assert layout.nav_payload(ROLE_CAPS["admin"])["screens"]["/webapp/services"] is True
    # Список возможностей для `data-svoi-cap`: тот же серверный ответ.
    payload = layout.nav_payload(ROLE_CAPS["troupe"])
    assert "budget.read.self" in payload["caps"]
    assert "budget.write" not in payload["caps"]


@pytest.mark.parametrize("role", sorted(ROLE_CAPS))
def test_кнопки_закрытых_экранов_помечены_правом(role: str):
    """(в) на экранах роли нет входов, которых у неё нет.

    Правило структурное: каждая ссылка на экран, закрытый не всем, обязана нести
    `data-svoi-cap`, и признак этой пометки обязан совпадать с признаком самого
    экрана. Тогда оболочка прячет ровно то, что роли недоступно: ссылка видна
    тогда и только тогда, когда экран открывается.
    """
    caps = ROLE_CAPS[role]
    problems = []
    for module, html in _page_sources():
        if module.startswith("kit."):
            continue            # справочник компонентов: ссылки в нём — образцы, не входы
        for tag in ANCHOR.finditer(html):
            url = tag.group(1)
            if not _restricted(url):
                continue
            # «‹» в шапке — возврат туда, откуда человек пришёл, а не вход:
            # на закрытый экран он иначе не попал бы.
            if "ui-appbar__back" in tag.group(0):
                continue
            # Вкладки прячет оболочка по `screens` из `/webapp/nav`: разметка
            # страниц — публичная константа, права туда не подставляются.
            if "ui-tabbar" in html[max(0, tag.start() - 400):tag.start()]:
                continue
            marked = CAP_ATTR.findall(tag.group(0))
            if not marked:
                problems.append(f"{module}: ссылка на {url} без data-svoi-cap")
                continue
            visible_by_mark = any(set(need.split()) <= caps for need in marked)
            if visible_by_mark != screen_visible(url, caps):
                problems.append(
                    f"{module}: {url} помечен {marked}, а экран "
                    f"{'открыт' if screen_visible(url, caps) else 'закрыт'} роли {role}")
    assert not problems, "\n".join(sorted(set(problems)))


def test_меню_чата_без_доменных_действий():
    """(а) в меню чата нет пунктов сверх прав: там только управление разговором.

    Модуль гейтвея тоже называется `app`, поэтому грузим его по пути: обычный
    импорт взял бы одноимённый пакет tool-api.
    """
    import importlib.util

    gateway_app = str(ROOT / "services" / "tg-gateway" / "app")
    sys.path.insert(0, gateway_app)
    try:
        spec = importlib.util.spec_from_file_location(
            "gateway_keyboards", ROOT / "services" / "tg-gateway" / "app" / "keyboards.py")
        keyboards = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(keyboards)
    finally:
        sys.path.remove(gateway_app)

    for role, caps in ROLE_CAPS.items():
        buttons = [button.text for row in keyboards.menu_kb(sorted(caps)).keyboard
                   for button in row]
        assert buttons == ["Обнулить", "Озадачить", "Сколько мне осталось"], role


def test_оболочка_прячет_по_праву_и_уводит_с_чужого_экрана():
    """Исполнение правила живёт в одном месте — shell.js, а не в 30 страницах."""
    shell = (ROOT / "services" / "tool-api" / "app" / "static" / "webapp-shell.js").read_text(
        encoding="utf-8")
    assert "data-svoi-cap" in shell, "оболочка не прячет элементы по праву"
    assert "body.screens[here] === false" in shell, "оболочка не уводит с чужого экрана"
    assert 'location.replace("/webapp/home")' in shell
