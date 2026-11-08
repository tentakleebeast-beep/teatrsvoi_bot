"""Общие куски экранов «Занятости»: строка дня и переключатель недель.

Их используют и неделя (`pages/week.py`), и сводка (`pages/week_summary.py`),
поэтому они лежат здесь, а не в одном из экранов: иначе модули импортировали
бы друг друга. Строка дня рисует состояние и «сегодня», строка навигации может
нести счётчик отмеченных дней под названием недели.
"""

from __future__ import annotations


# Строка дня: текст подставляет страница, когда придёт неделя.
def _day_row(index: int) -> str:
    """Строка дня в списке: заголовок, состояние и «сегодня» заполняет JS.

    Состояние дня — отдельный элемент справа (`.ui-day__state`) и атрибут
    `data-state` на самой строке: цвет полосы и фона задаёт CSS, поэтому
    «занят / свободен / не отмечено» видно до чтения подписи. Значок
    «сегодня» появляется только у сегодняшнего дня (`todayIso()` в скрипте).

    `layout.row` здесь не подходит: он не умеет ни второго элемента справа,
    ни стрелки у кнопки, а состояние дня — часть строки, а не подписи.
    """
    return (
        f'<button class="ui-row ui-day" type="button" data-day-slot="{index}" '
        'data-state="empty">'
        '<span class="ui-row__main">'
        '<span class="ui-row__title">…</span>'
        '<span class="ui-row__meta"></span></span>'
        '<span class="ui-day__today" hidden>сегодня</span>'
        '<span class="ui-day__state">…</span>'
        '<span class="ui-row__chevron">&#8250;</span>'
        "</button>"
    )


_DAYS = "".join(_day_row(index) for index in range(7))


def week_nav(note: str = "") -> str:
    """Переключатель недель: `‹`, название, `›`.

    `note` — необязательная строка под названием (счётчик отмеченных дней).
    Держим её в той же строке, а не отдельным блоком: на 390×844 экран
    недели должен помещаться целиком (V-028, docs/reports/74).
    """
    return (
        '<div class="ui-actions" style="align-items:center;flex-wrap:nowrap">'
        '<button class="ui-btn ui-btn--sm ui-btn--icon" type="button" id="prev-week" '
        'aria-label="Предыдущая неделя">&#8249;</button>'
        '<span class="ui-nav__title">'
        '<span class="ui-text ui-num" id="week-title">…</span>'
        f"{note}"
        "</span>"
        '<button class="ui-btn ui-btn--sm ui-btn--icon" type="button" id="next-week" '
        'aria-label="Следующая неделя">&#8250;</button>'
        "</div>"
    )


_WEEK_NAV = week_nav()
_WEEK_NAV_WITH_PROGRESS = week_nav(
    '<span class="ui-small ui-muted" id="progress">Отмечено 0 из 7 дней</span>'
)
