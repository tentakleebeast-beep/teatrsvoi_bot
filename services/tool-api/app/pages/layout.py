"""Каркас страниц Mini App: шапка, нижние вкладки, форматирование (docs/development/86).

Зачем отдельный модуль: до переработки каждый из 16 экранов собирал свою шапку,
свои стили и свой путь назад. Из-за этого одни и те же вещи выглядели по-разному,
а «назад» вёл не туда. Теперь каркас один:

    from app.pages.layout import page

    return page("Занятость", body, tab="week", caps=caps)

Правила, которые держит каркас:
* не больше четырёх вкладок, четвёртая называется «Управление», если у человека есть
  хоть одно право из управленческих, иначе «Ещё»;
* экран-вкладка без шапки, вложенный экран — с шапкой и «‹»;
* ссылка на CSS стоит в разметке (а не появляется из JS), иначе первый кадр пустой;
* весь текст экранируется: HTML собирается на сервере из данных базы.
"""

from __future__ import annotations

from datetime import date
from html import escape

from app.webapp import screen_flags, sections_for
from services.shared import format as shared_format

# Форматы сумм и дат — одни на оба сервиса (services/shared/format.py, R-6).
# Имена оставлены прежними: каркас страниц и main.py импортируют их отсюда.
MONTHS = shared_format.MONTHS
MONTHS_NOM = shared_format.MONTHS_NOM
WEEKDAYS_SHORT = shared_format.WEEKDAYS_SHORT
WEEKDAYS_FULL = shared_format.WEEKDAYS_FULL

# Вкладки: (ключ, подпись, адрес). Порядок фиксирован — положение помнят мышцы.
TABS: tuple[tuple[str, str, str], ...] = (
    ("home", "Главная", "/webapp/home"),
    # Подпись вкладки — «Время» (решение заказчика 31.10.2026). Экран внутри
    # по-прежнему называется «Занятость»: вкладка — раздел, экран — задача.
    ("week", "Время", "/webapp/week"),
    ("money", "Деньги", "/webapp/money"),
    ("more", "Ещё", "/webapp/more"),
)

STAFF_LABEL = "Управление"

# Права, при которых четвёртая вкладка — «Управление», а не «Ещё», отдельным
# списком больше не ведутся: это ровно те возможности, что открывают хотя бы один
# управленческий раздел в реестре (`app/webapp.py:SECTIONS`). Бюджетные и личные
# разделы туда не входят — «Деньги» отдельная вкладка.


def esc(value: object) -> str:
    """Экранирование текста для HTML. Пустое значение — пустая строка."""
    if value is None:
        return ""
    return escape(str(value), quote=True)


def has(caps: list[str] | tuple[str, ...] | set[str] | None, *need: str) -> bool:
    """Есть ли возможности. `*` покрывает всё, пустой `need` — доступно всем."""
    if not need:
        return True
    current = set(caps or ())
    if "*" in current:
        return True
    return set(need) <= current


def is_staff(caps: list[str] | tuple[str, ...] | set[str] | None) -> bool:
    """Четвёртая вкладка — рабочая («Управление») или личная («Ещё»).

    Достаточно **любого** управленческого раздела: у организатора это «Люди»
    и «Заявки», у админа — весь набор. Список разделов — в реестре, здесь только
    вопрос «есть ли хоть один».
    """
    return bool(sections_for(caps, group="management"))


def nav_payload(caps: list[str] | tuple[str, ...] | set[str] | None) -> dict:
    """Что оболочка показывает по правам: `/webapp/nav` отдаёт это как есть.

    `staff`     — подпись четвёртой вкладки;
    `summary`   — строка «Сводка занятости» на экране недели: сводка читает
                  `GET /webapp/grid`, а он закрыт правом `schedule.read.all`
                  (FR-H9, docs/development/92 §7). Без права ссылка вела бы на 403
                  (карта t_70a6d8e5, docs/architecture/25 §9.2).
    `reconcile` — кнопка «Проверить» на `/webapp/reconcile`: сверка пишет в лист
                  и закрыта правом `budget.import`, которого нет у `troupe` и
                  `master`. Без права экран показывает словами, что сверку ведёт
                  организатор, вместо кнопки, упирающейся в 403
                  (карта t_01784cc4, отчёт 142 §3 строка 10).

    `caps`      — возможности человека списком: по нему оболочка прячет элементы,
                  помеченные `data-svoi-cap` (кнопка, строка, ссылка). Решение
                  снова серверное: страница — публичная константа и прав не знает.
    `screens`   — `{адрес: можно}` для всех экранов (`app/webapp.py:SCREEN_CAPS`):
                  оболочка уводит с чужого экрана на «Главную», если человек открыл
                  его прямым адресом. Так правило «не показываем вход туда, куда
                  действие недоступно» держится для всех 30 экранов, а не только
                  для плиток хаба (правило владельца 08.11.2026).

    Решение принимает сервер: страницы — публичные константы, поэтому страница
    лишь прячет строку по этому признаку и ничего не решает сама.
    """
    return {"ok": True,
            "staff": is_staff(caps),
            "summary": has(caps, "schedule.read.all"),
            "reconcile": has(caps, "budget.import"),
            "caps": sorted(set(caps or ())),
            "screens": screen_flags(caps)}


def tab_label(key: str, caps: list[str] | tuple[str, ...] | set[str] | None) -> str:
    for tab_key, label, _ in TABS:
        if tab_key == key:
            return STAFF_LABEL if key == "more" and is_staff(caps) else label
    return ""


def tabbar(active: str, caps: list[str] | tuple[str, ...] | set[str] | None = None) -> str:
    """Четыре вкладки. Активная помечена словом и цветом, не иконкой."""
    items = []
    for key, _label, url in TABS:
        on = key == active
        cls = "ui-tabbar__item ui-tabbar__item--active" if on else "ui-tabbar__item"
        mark = ' aria-current="page"' if on else ""
        items.append(f'<a class="{cls}" href="{esc(url)}"{mark}>{esc(tab_label(key, caps))}</a>')
    return '<nav class="ui-tabbar" aria-label="Разделы">' + "".join(items) + "</nav>"


def appbar(title: str, *, back: str = "/webapp/home", action: str = "") -> str:
    """Шапка вложенного экрана: назад, название, необязательное действие (HTML)."""
    return (
        '<header class="ui-appbar">'
        f'<a class="ui-appbar__back" href="{esc(back)}" aria-label="Назад">&#8249;</a>'
        f'<span class="ui-appbar__title">{esc(title)}</span>'
        f"{action}"
        "</header>"
    )


def page(title: str, body: str, *, tab: str = "", back: str = "",
         action: str = "", caps: list[str] | tuple[str, ...] | set[str] | None = None,
         head: str = "", scripts: str = "") -> str:
    """Собрать экран целиком.

    tab   — ключ вкладки: экран получает нижние вкладки и не получает шапку;
    back  — адрес возврата: экран получает шапку с «‹» (по умолчанию на «Главную»);
    head  — дополнительная разметка в <head>; scripts — код страницы перед </body>.
    """
    screen_class = "ui-screen ui-screen--tabbed" if tab else "ui-screen"
    header = appbar(title, back=back, action=action) if back else ""
    footer = tabbar(tab, caps) if tab else ""
    return (
        "<!doctype html>\n"
        '<html lang="ru">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, '
        'maximum-scale=1, user-scalable=no, viewport-fit=cover">\n'
        f"<title>{esc(title)}</title>\n"
        '<link rel="stylesheet" href="/webapp/ui.css" data-svoi-ui="1">\n'
        '<script src="/webapp/tg.js"></script>\n'
        # Маяк загрузки: Mini App не открывался на телефоне, а данных с устройства не было —
        # гадали по логам прокси. Теперь сама страница сообщает, что видит на старте:
        # платформа, длина initData, адрес, текст JS-ошибки. Сам initData не отправляется.
        "<script data-svoi-beacon=\"1\">(function(){function send(why){try{var tg=window.Telegram&&window.Telegram.WebApp;"
        "fetch('/webapp/boot',{method:'POST',headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({platform:(tg&&tg.platform)||'none',"
        "init_len:(tg&&tg.initData?tg.initData.length:0),"
        "sdk:(tg&&tg.version)||'',url:location.href.slice(0,200),"
        "ready:document.readyState,error:why||''})}).catch(function(){});}catch(e){}}"
        "window.addEventListener('error',function(e){send('js: '+(e.message||'').slice(0,150));});"
        "if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',function(){send('');},{once:true});}"
        "else{send('');}})();</script>\n"
        '<script src="/webapp/shell.js"></script>\n'
        f"{head}"
        "</head>\n"
        '<body class="ui-page">\n'
        f"{header}"
        f'<main class="{screen_class}">{body}</main>\n'
        f"{footer}"
        f"{scripts}"
        "</body>\n</html>"
    )


def money(value: float | int | None) -> str:
    """Сумма по-русски: неразрывный пробел между разрядами, знак рубля."""
    return shared_format.money(value, design=True)


def human_date(value: date | str | None, *, with_weekday: bool = True) -> str:
    """Дата словами: «31 октября, суббота». Не `2026-10-31`."""
    return shared_format.human_date(value, with_weekday=with_weekday)


def human_month(value: date | str | None) -> str:
    """Месяц словами: «октябрь 2026»."""
    return shared_format.human_month(value)


def row(title: str, meta: str = "", *, href: str = "", value: str = "",
        badge: int | str = "", attention: bool = False, attrs: str = "") -> str:
    """Строка списка: название, пояснение, значение/счётчик и «›» при переходе.

    Одна строка — одно действие. Вся площадь нажатия — строка, не текст в ней.
    """
    classes = "ui-row ui-row--attention" if attention else "ui-row"
    tag = "a" if href else "button"
    href_attr = f' href="{esc(href)}"' if href else ' type="button"'
    parts = ['<span class="ui-row__main">',
             f'<span class="ui-row__title">{esc(title)}</span>']
    if meta:
        parts.append(f'<span class="ui-row__meta">{esc(meta)}</span>')
    parts.append("</span>")
    if value:
        parts.append(f'<span class="ui-row__value">{esc(value)}</span>')
    if badge not in ("", None, 0):
        parts.append(f'<span class="ui-badge">{esc(badge)}</span>')
    if href:
        parts.append('<span class="ui-row__chevron">&#8250;</span>')
    return (f'<{tag} class="{classes}"{href_attr}{attrs}>'
            + "".join(parts) + f"</{tag}>")


def field(label: str, control: str, *, hint: str = "", error: str = "",
          field_id: str = "") -> str:
    """Поле: подпись сверху, контрол, при необходимости подсказка или ошибка."""
    label_html = (f'<label class="ui-field__label" for="{esc(field_id)}">{esc(label)}</label>'
                  if field_id else f'<span class="ui-field__label">{esc(label)}</span>')
    extra = ""
    if error:
        extra = f'<span class="ui-field__error">{esc(error)}</span>'
    elif hint:
        extra = f'<span class="ui-field__hint">{esc(hint)}</span>'
    return f'<div class="ui-field">{label_html}{control}{extra}</div>'
