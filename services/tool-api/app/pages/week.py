"""«Занятость»: неделя, лист дня и сводка (docs/development/86 §2.2, §4).

Один экран вместо трёх (`/days`, `/my/sheet`, `/schedule`): семь строк-дней,
переключение недель `‹`/`›` вместо горизонтальной ленты, лист дня вместо сетки
часов × дней. Правила, из-за которых экран собран именно так:

* строка дня — карточка (`.ui-row.ui-day`): состояние («занят / свободен / не
  отмечено») видно полосой, фоном и словом справа, сегодняшний день в рамке;
  нажатие открывает `.ui-sheet` с тремя вариантами и интервалом `.ui-range`;
  шаг интервала — 30 минут;
* сохранение идёт сразу при закрытии листа, результат — `.ui-toast` с «Вернуть»;
* «Заполнить по шаблону» — компактная кнопка под неделей; «Загрузить из
  общего листа» открывает предпросмотр своей колонки из «Расписания
  студийцы»; сводка, «Сохранить шаблон недели» и «Очистить неделю» — строки
  нижнего списка. Такой порядок — ответ владельца на V-048 (09.11.2026):
  строка загрузки встала на место шаблона, шаблон уехал в блок со сводкой;
* отдельной кнопки «Отметить сегодня» нет (V-048, пересмотр 09.11.2026): её
  работу делает нажатие на строку сегодняшнего дня, а вторая строка этой
  строки говорит «нажмите». Экран стал короче на 64 px —
  замер 890 px при экране 844, под нижней панелью остаётся только «Очистить
  неделю» и всего на 7 px;
* сводка — те же семь строк с числами «свободны 7 из 9 · не отметились: …»,
  нажатие раскрывает людей в листе. Ни одной таблицы и ни одной сетки 30 × 16.

Модуль держит сборку недели: разметку и склейку скрипта из двух смысловых
частей (`pages/week_marks.py`, `pages/week_template.py`). Сводка —
`pages/week_summary.py`, общие строка дня и переключатель недель —
`pages/week_shared.py`. Имя `SUMMARY_PAGE` по-прежнему доступно отсюда.

Две кнопки под неделей делают ровно две разные вещи (П-4): «Заполнить по
шаблону» открывает лист предпросмотра (З5), «Сохранить шаблон недели»
запоминает неделю как обычную (З8). Сами отметки сохраняются без кнопки —
при закрытии листа дня.

Контракты API (менять их этот модуль не может):

    GET  /webapp/availability/week?start=YYYY-MM-DD
         → {ok, start, title, female, days: [{day, marked, status, from, to,
            note, text, weekday, short}]}
    POST /webapp/availability/week  {start, days: [{day, status, from_time,
         to_time, note}]}          → {ok, start, days, hours}
    POST /webapp/availability/clear {week}  → {ok, cleared (дни), hours, start}
    GET  /webapp/availability/template                          → {ok, days}
    POST /webapp/availability/template/from-week {start}        → {ok, filled}
    POST /webapp/availability/template/apply {start, apply}     → {ok, days, skipped}
    GET  /webapp/grid?month=YYYY-MM  (сводка: люди, дни, marks) → {ok, month,
         days, weekdays, people: [{id, name}], marks: {pid: {day: {state}}}}

Интервал в карточке — всегда интервал занятости: «занят с 18:00 до 23:00»
и «свободен с 08:00 до 18:00» описывают одно и то же с двух сторон, поэтому
лист спрашивает одно — когда человек занят.
"""

from __future__ import annotations

from app.pages import layout
from app.pages.month import MONTH_PAGE
from app.pages.week_marks import _SCRIPT_WEEK_MARKS
from app.pages.week_shared import _DAYS, _WEEK_NAV_WITH_PROGRESS
from app.pages.week_sheet import _SCRIPT_WEEK_SHEET
from app.pages.week_sheet_load import _SCRIPT_WEEK_SHEET_LOAD, ROW_LOAD_FROM_SHEET
from app.pages.week_summary import SUMMARY_PAGE
from app.pages.week_template import _SCRIPT_WEEK_TEMPLATE

#: Порядок нижнего блока — решение владельца по V-048 (09.11.2026): «Загрузить из
#: общего листа» встаёт на место «Сохранить шаблон недели», сам шаблон переезжает
#: ниже, в блок со сводкой и очисткой. Очистка — разрушающее действие — последняя.
_BODY_WEEK = (
    _WEEK_NAV_WITH_PROGRESS
    + '<div class="ui-list" id="day-list">'
    + _DAYS
    + "</div>"
    + '<div class="ui-actions">'
    '<button class="ui-btn ui-btn--sm" type="button" id="fill-template" '
    'style="flex:1">Заполнить по шаблону</button>'
    "</div>"
    + '<div class="ui-list">'
    + ROW_LOAD_FROM_SHEET
    + '<a class="ui-row" id="to-summary" href="/webapp/week/summary" '
    'data-svoi-cap="schedule.read.all">'
    '<span class="ui-row__main">'
    '<span class="ui-row__title">Сводка занятости</span>'
    '<span class="ui-row__meta">кто свободен на этой неделе</span></span>'
    '<span class="ui-row__chevron">&#8250;</span></a>'
    + '<button class="ui-row" type="button" id="save-template">'
    '<span class="ui-row__main">'
    '<span class="ui-row__title">Сохранить шаблон недели</span>'
    '<span class="ui-row__meta">запомнить эту неделю как обычную</span></span>'
    '<span class="ui-row__chevron">&#8250;</span></button>'
    + '<button class="ui-row" type="button" id="clear-week">'
    '<span class="ui-row__main">'
    '<span class="ui-row__title">Очистить неделю</span>'
    '<span class="ui-row__meta">снять все отметки этой недели</span></span>'
    '<span class="ui-row__chevron">&#8250;</span></button>'
    + "</div>"
    + '<div class="ui-small ui-muted" id="status"></div>'
)

_SCRIPT_WEEK = (
    _SCRIPT_WEEK_SHEET + _SCRIPT_WEEK_MARKS + _SCRIPT_WEEK_SHEET_LOAD
    + _SCRIPT_WEEK_TEMPLATE
)

WEEK_PAGE = layout.page("Занятость", _BODY_WEEK, tab="week", scripts=_SCRIPT_WEEK)

__all__ = ["MONTH_PAGE", "SUMMARY_PAGE", "WEEK_PAGE"]
