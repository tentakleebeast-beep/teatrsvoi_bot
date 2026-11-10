"""«Занятость»: неделя, лист дня и сводка (docs/development/92 §2.2, §4).

Тесты держат то, из-за чего экран переделывался: семь строк вместо сетки часов,
переключение недель вместо горизонтальной ленты, нижний лист вместо модалок,
одно главное действие на экран и отсутствие эмодзи.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app.pages import week  # noqa: E402

EMOJI = re.compile("[\U0001f300-\U0001faff\u2600-\u27bf\u2b00-\u2bff\ufe0f]")
MODALS = re.compile(r"\b(alert|confirm|prompt)\s*\(")


def test_экраны_собраны_каркасом() -> None:
    for html in (week.WEEK_PAGE, week.SUMMARY_PAGE):
        assert html.startswith("<!doctype html>")
        assert html.rstrip().endswith("</html>")
        # Экран вкладки: вкладки есть, шапки с «‹» нет.
        assert html.count('class="ui-tabbar__item') == 4
        assert "ui-appbar" not in html
        assert 'href="/webapp/ui.css" data-svoi-ui="1"' in html


def test_семь_строк_дней_и_ничего_вбок() -> None:
    for html in (week.WEEK_PAGE, week.SUMMARY_PAGE):
        assert len(re.findall(r'data-day-slot="\d+"', html)) == 7
        # Строка дня — компонент системы, а не самодельный div.
        assert html.count('class="ui-row ui-day"') == 7
        assert "ui-table" not in html
        assert "overflow-x" not in html
        # Горизонтальной ленты чипов-недель нет: недели листаются кнопками.
        assert 'id="prev-week"' in html and 'id="next-week"' in html
        assert "неделя" in html


def test_состояние_дня_видно_без_мелкой_подписи() -> None:
    """V-028(в)/V-029(а): у строки есть состояние, слово справа и признак «сегодня»."""
    for html in (week.WEEK_PAGE, week.SUMMARY_PAGE):
        assert html.count('data-state="empty"') == 7
        assert html.count('class="ui-day__state"') == 7
        assert html.count('class="ui-day__today" hidden') == 7
        # Класс сегодняшнего дня ставит скрипт, дату берёт из todayIso().
        assert "ui-row--today" in html
        assert "todayIso()" in html


def test_сводка_открывается_строкой_а_не_сегментом() -> None:
    """Дублирующий сегмент «Моя неделя | Сводка» убран: переключение — вкладкой «Время»."""
    for html in (week.WEEK_PAGE, week.SUMMARY_PAGE):
        assert 'role="tablist"' not in html
        assert "Моя неделя" not in html
    assert 'href="/webapp/week/summary"' in week.WEEK_PAGE
    assert "Сводка занятости" in week.WEEK_PAGE
    assert 'href="/webapp/slots"' in week.SUMMARY_PAGE
    assert "Найти окно для события" in week.SUMMARY_PAGE


def test_одна_главная_кнопка_на_экран() -> None:
    """V-048 (пересмотр 09.11.2026): на неделе главной кнопки нет.

    Носитель главного действия — строка сегодняшнего дня: её нажатие открывает
    тот же лист дня, что открывала кнопка «Отметить сегодня». Правило «одна
    главная кнопка» не отменено, а уточнено: на сводке она осталась.
    """
    assert week.WEEK_PAGE.count("ui-btn--primary") == 0
    assert "Отметить сегодня" not in week.WEEK_PAGE
    assert "mark-today" not in week.WEEK_PAGE
    assert "markToday" not in week.WEEK_PAGE
    assert week.SUMMARY_PAGE.count("ui-btn--primary") == 1
    # Кнопки «Сохранить неделю» больше нет: сохраняется сразу после листа.
    assert "Сохранить неделю" not in week.WEEK_PAGE


def test_скрипт_не_держит_удалённый_обработчик() -> None:
    """К22: разметку убрали — привязку и функцию тоже, иначе экран умирает.

    `$("mark-today")` на отсутствующем элементе вернул бы `null`, и
    `null.onclick` под `"use strict"` оборвал бы IIFE: ниже не выполнились бы
    `‹`/`›`, шаблон, очистка, привязка строк дней и `boot()`.
    """
    html = week.WEEK_PAGE
    assert '$("mark-today")' not in html
    assert "markToday" not in html
    # Скрипт не обрезан: ключевые функции недели на месте.
    for name in ("openDay", "flush", "fillTemplate", "rememberWeek", "clearWeek"):
        assert name in html, f"в скрипте недели нет {name}"


def test_загрузка_из_листа_на_месте_шаблона() -> None:
    """Ответ V-048 (09.11.2026): строка загрузки вместо кнопки шаблона.

    Порядок нижнего блока — тоже решение владельца: загрузка, сводка, шаблон,
    очистка. Проверяем позиции, а не просто наличие.
    """
    html = week.WEEK_PAGE
    assert 'id="load-from-sheet"' in html
    assert "Загрузить из общего листа" in html
    assert "своя колонка из «Расписание студийцы»" in html
    load = html.index('id="load-from-sheet"')
    summary = html.index('id="to-summary"')
    template = html.index('id="save-template"')
    clear = html.index('id="clear-week"')
    assert load < summary < template < clear
    # Шаблон — строка нижнего блока, а не кнопка рядом с «Заполнить по шаблону».
    assert 'class="ui-btn ui-btn--sm" type="button" id="save-template"' not in html
    assert 'class="ui-row" type="button" id="save-template"' in html


def test_лист_дня_вместо_сетки() -> None:
    html = week.WEEK_PAGE
    assert "SVOI.sheet(" in html and "SVOI.toast(" in html
    for label in (
        "Свободен весь день",
        "Занят весь день",
        "Указать часы",
        "После 18:00",
        "До 15:00",
        "Весь день",
        "Комментарий",
    ):
        assert label in html, f"в листе дня нет варианта «{label}»"
    assert "ui-range" in html and "ui-chip" in html
    # Шаблон и очистка — строки-действия, а не четыре кнопки на экране.
    assert 'id="fill-template"' in html and 'id="clear-week"' in html


def test_контракты_api_на_месте() -> None:
    html = week.WEEK_PAGE
    for path in (
        "/webapp/availability/week",
        "/webapp/availability/clear",
        "/webapp/availability/template/apply",
        "/webapp/availability/template/from-week",
    ):
        assert path in html, f"страница не зовёт {path}"
    # Сводка читает те же данные, что старый экран расписания.
    assert "/webapp/grid?month=" in week.SUMMARY_PAGE
    # Вложенный экран возвращается в свой раздел, а не на «Главную».
    assert 'window.SVOI_BACK = "/webapp/week"' in html


def test_без_эмодзи_и_модалок() -> None:
    for html in (week.WEEK_PAGE, week.SUMMARY_PAGE):
        assert not EMOJI.search(html), "в разметке эмодзи"
        assert not MODALS.search(html), "модалка alert/confirm/prompt"
        # Подтверждение очистки — два листа, а не confirm.
        assert "Точно очистить?" in html or "Сводка" in html


def test_счётчик_очистки_берёт_дни_из_ответа() -> None:
    """Ш-53: «снято N отметок» — N из ответа сервера (дни), а не счёт часов на экране."""
    html = week.WEEK_PAGE
    assert "answer.body.cleared" in html, "экран печатает число из ответа очистки"
    assert "rowcount" not in html
    # Ноль тоже называется числом, а не прячется за «пусто» без счётчика.
    assert "снято 0 отметок" in html


def test_fr_h9_отказ_на_сводке_назван_а_не_списан_на_сеть() -> None:
    """FR-H9, t_70a6d8e5: 403 на `/webapp/grid` — «у организатора», а не «проверьте сеть».

    Сводка закрыта правом `schedule.read.all` на сервере; экран обязан назвать
    причину отказа, а совет про сеть оставить только настоящим сбоям. Строку
    «Сводка занятости» на неделе прячет оболочка по признаку `summary` из
    `/webapp/nav` — решение принимает сервер (docs/architecture/25 §9.2).
    """
    html = week.SUMMARY_PAGE
    assert "answer.status === 403" in html, "отказ по праву не отделён от сбоя"
    assert "Сводка занятости — у организатора" in html
    # «Проверьте сеть» осталось ровно для прочих сбоев — и не для 403.
    assert html.count("Проверьте сеть и повторите") == 1

    week_html = week.WEEK_PAGE
    # Строку «Сводка занятости» прячет оболочка по `data-svoi-cap` (карта
    # t_f279a0eb): своего запроса прав на неделе больше нет — иначе признак прав
    # приходил двумя ответами, и строка исчезала уже после снятия скелета.
    assert 'id="to-summary"' in week_html, "строки сводки нет в разметке"
    assert 'data-svoi-cap="schedule.read.all"' in week_html, "строка не помечена правом"
    assert 'api("/webapp/nav")' not in week_html and 'fetch("/webapp/nav")' not in week_html, \
        "неделя снова спрашивает права сама"


def test_сетка_месяца_только_читается() -> None:
    """FR-H9, П-3, Ш-04: на экране месяца нет записи, только чтение сетки.

    Критерий карты «вызовов из UI нет» проверяется по разметке: страница зовёт
    `GET /webapp/month-grid`, а `POST /webapp/grid` не зовёт ни один экран.
    """
    html = week.MONTH_PAGE
    assert "/webapp/month-grid" in html and "?month=" in html
    assert 'method: "POST"' not in html, "экран месяца что-то пишет"
    assert "/webapp/grid" not in html.replace("/webapp/month-grid", ""), \
        "экран месяца ходит в удалённую сетку"

    # Вход в месяц — строка на сводке, и она есть в разметке публичной константы.
    assert 'href="/webapp/week/month"' in week.SUMMARY_PAGE

    # Ни один экран не пишет в сетку месяца: запись у неё удалена (Ш-04).
    for page in (week.WEEK_PAGE, week.SUMMARY_PAGE, week.MONTH_PAGE):
        assert 'method: "POST"' not in page or "/webapp/grid" not in page


def test_сводка_ведёт_в_поиск_окон_с_датами_недели() -> None:
    """Вход в дом поиска окон не заставляет заново выбирать период (Ш-05).

    Экран `Поиск окон` принимает `from`/`to` из адреса, поэтому ссылка со сводки
    несёт даты той недели, которую человек смотрит (docs/architecture/40 §5).
    """
    html = week.SUMMARY_PAGE
    assert 'id="to-slots"' in html
    assert 'href="/webapp/slots"' in html, "статический адрес — резерв без JS"
    assert '"/webapp/slots?from="' in html and 'view.days[6]' in html
