"""Каркас страниц Mini App: вкладки, шапка, форматирование (docs/development/92 §2, §7).

Проверяем то, из-за чего переделывалась навигация: четыре вкладки, подпись четвёртой
по правам, отсутствие эмодзи, экранирование и человеческие даты вместо ISO.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app.pages import home, kit, layout, routes  # noqa: E402

EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")


def test_каркас_закрыт_и_подключает_систему():
    html = layout.page("Занятость", "<p>текст</p>", tab="week", caps=[])
    assert html.startswith("<!doctype html>")
    assert html.rstrip().endswith("</html>")
    assert 'href="/webapp/ui.css" data-svoi-ui="1"' in html
    assert 'class="ui-screen ui-screen--tabbed"' in html
    assert "maximum-scale=1" in html


def test_щипок_включён_только_у_экрана_с_флагом_zoom():
    """V-051: флаг `zoom` печатает одну мету «можно увеличивать» и помечает <html>.

    Мету исторически писали два места (`layout.page` и `viewportGuard` в оболочке),
    и оболочка перетирала страницу при загрузке. Поэтому у экрана по умолчанию
    проверяется и мета, и отсутствие флага: разъехавшаяся пара вернула бы дефект
    молча (docs/development/150 §2.8, §3 D3.2).
    """
    zoomed = layout.page("Файл", "", back="/webapp/files", zoom=True)
    assert zoomed.count('name="viewport"') == 1
    assert "maximum-scale=5" in zoomed and "user-scalable=yes" in zoomed
    assert "maximum-scale=1" not in zoomed and "user-scalable=no" not in zoomed
    assert "viewport-fit=cover" in zoomed
    assert zoomed.split("\n", 1)[1].startswith(
        '<html lang="ru" data-svoi-zoom="1" data-svoi-rights="pending">')

    plain = layout.page("Деньги", "", tab="money", caps=[])
    assert plain.count('name="viewport"') == 1
    assert "maximum-scale=1, user-scalable=no, viewport-fit=cover" in plain
    assert '<html lang="ru" data-svoi-rights="pending">' in plain
    assert "data-svoi-zoom" not in plain

    # Экран файла в продукте получает флаг: иначе щипок не включится нигде.
    from app.pages import files_file

    assert 'data-svoi-zoom="1"' in files_file.FILE_PAGE
    assert "maximum-scale=5, user-scalable=yes" in files_file.FILE_PAGE


def test_вкладок_ровно_четыре():
    html = layout.tabbar("week", [])
    assert html.count('class="ui-tabbar__item') == 4
    assert "Главная" in html and "Время" in html and "Деньги" in html and "Ещё" in html


def test_четвёртая_вкладка_по_правам():
    # Студиец: личная вкладка. Право «свои взносы» — ещё не управление.
    assert layout.tab_label("more", ["budget.read.self"]) == "Ещё"
    # Организатор: одного управленческого права достаточно.
    assert layout.tab_label("more", ["participants.view"]) == "Управление"
    assert layout.tab_label("more", ["registration.approve"]) == "Управление"
    # Админ: «*» покрывает всё.
    assert layout.tab_label("more", ["*"]) == "Управление"
    # Бюджетные права в управление не входят — они на вкладке «Деньги».
    assert layout.tab_label("more", ["budget.write", "budget.read"]) == "Ещё"


def test_активная_вкладка_помечена():
    html = layout.tabbar("money", [])
    assert html.count("ui-tabbar__item--active") == 1
    assert 'aria-current="page"' in html


def test_экран_вкладки_без_шапки_вложенный_с_шапкой():
    tabbed = layout.page("Деньги", "", tab="money", caps=[])
    assert "ui-appbar" not in tabbed
    nested = layout.page("Мои взносы", "", back="/webapp/money")
    assert "ui-appbar" in nested and "ui-appbar__back" in nested
    assert "ui-tabbar" not in nested


def test_текст_экранируется():
    html = layout.page("Имя", '<span class="ui-text">' + layout.esc("<script>x</script>") + "</span>")
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html
    assert layout.esc(None) == ""


def test_суммы_и_даты_по_русски():
    assert layout.money(15000) == "15\u00a0000\u00a0₽"
    assert layout.money(-173077) == "−173\u00a0077\u00a0₽"
    assert layout.money(None) == "—"
    assert layout.human_date("2026-10-31") == "31 октября, суббота"
    assert layout.human_month("2026-10-01") == "октябрь 2026"
    # Неизвестный формат возвращается как есть, а не падает.
    assert layout.human_date("когда-нибудь") == "когда-нибудь"


def test_справочник_без_эмодзи_и_на_новой_системе():
    html = kit.UI_KIT_PAGE
    assert not EMOJI.search(html), "в справочнике эмодзи"
    for component in ("ui-appbar", "ui-card", "ui-row", "ui-btn", "ui-input",
                      "ui-datefield", "ui-range", "ui-segmented", "ui-switch",
                      "ui-badge", "ui-status", "ui-empty"):
        assert component in html, f"в справочнике нет {component}"
    # Лист и сообщение — через оболочку: в разметке их нет, их создаёт SVOI.sheet/toast.
    assert "SVOI.sheet(" in html and "SVOI.toast(" in html
    # Старые селекторы версии 1 не должны вернуться.
    for legacy in ("ui-list__row", "ui-tile", "ui-tabs", "ui-top"):
        assert legacy not in html, f"старый селектор {legacy}"


def test_страница_одна_для_любых_прав():
    """Страница — публичная константа: права не меняют ни байта разметки.

    Подпись четвёртой вкладки доезжает отдельным запросом `/webapp/nav` уже после
    обмена `initData` на токен (05b7d2b: HTML отдаётся без `Authorization`), поэтому
    серверной подстановки по правам нет: одна и та же сборка на любые возможности.
    """
    html = layout.page("Главная", "", tab="home")
    assert ">Ещё<" in html
    # Права в сборку страницы не передаются: константы собираются без знания о человеке.
    assert layout.page("Главная", "", tab="home", caps=[]) == html
    assert ">Ещё<" in home.HOME_PAGE and ">Ещё<" in home.MORE_PAGE

    router = routes.build_router(actor=lambda: {}, caps_for=lambda who: ["*"])
    more = next(route.endpoint() for route in router.routes if route.path == "/webapp/more")
    assert more == home.MORE_PAGE


def test_nav_знает_о_праве_на_сверку_листов():
    """`/webapp/nav` отдаёт признак `reconcile` — право `budget.import` (отчёт 142 §3 строка 10).

    Сверка пишет в лист, поэтому кнопка «Проверить» на `/webapp/reconcile` без
    права упрётся в 403. Право есть у админа и организатора, у `troupe` и `master`
    его нет: страница узнаёт это признаком с сервера (карта t_01784cc4,
    docs/architecture/25 §9.2).
    """
    assert layout.nav_payload(["budget.import"])["reconcile"] is True
    assert layout.nav_payload(["*"])["reconcile"] is True
    assert layout.nav_payload(["budget.write", "budget.read.self"])["reconcile"] is False
    assert layout.nav_payload([])["reconcile"] is False


def test_страницы_не_живут_в_маршрутах():
    """Маршруты отдают экраны из `pages/*`, а не держат свои HTML-константы.

    Иначе появляется страница, которую не отдаёт ни один маршрут: ровно так отказ
    «Раздел для администратора» пролежал мёртвым, пока его не удалили (92 §9.1).
    """
    own_pages = [name for name, value in vars(routes).items()
                 if isinstance(value, str) and value.startswith("<!doctype")]
    assert not own_pages, "в маршрутах своя страница: " + ", ".join(own_pages)


def test_строка_списка_одно_действие():
    link = layout.row("Заявки", "ждут решения", href="/webapp/more", badge=2, attention=True)
    assert link.startswith("<a ") and 'href="/webapp/more"' in link
    assert "ui-badge" in link and "ui-row__chevron" in link
    button = layout.row("Принять", "", attrs=' data-act="approve"')
    assert button.startswith("<button ") and 'type="button"' in button
    # Пустой счётчик не рисуется.
    assert "ui-badge" not in layout.row("Люди", "17 активных", href="/webapp/more")
