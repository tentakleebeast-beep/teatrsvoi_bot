"""Форма операции «Записать» — второй экран раздела «Деньги» (docs/87 §4).

Одна форма: тип, сумма, категория, дата, статья, план/факт и одна кнопка
«Записать». Разметка здесь, скрипт — в `pages/money_tx_script.py`.

Права на запись (`budget.write`) на момент отдачи HTML неизвестны — страница
публичная константа. Поэтому форма отдаётся скрытой (`#tx-form[hidden]`), а
скрипт показывает её только после ответа `/webapp/home/data` с `can_record`.
Человек без права видит состояние словами («Операции записывает организатор»)
и не заполняет форму, которую сервер всё равно отклонит (docs/91 принципы 8, 9,
docs/92 §2 таблица экранов: «Деньги: операции» — по `budget.write`).
"""

from __future__ import annotations

from app.pages import layout
from app.pages.money_tx_script import _SCRIPT_TX

# Обёртка формы — свой флекс-столбец: внутри общего `.ui-screen` она должна
# держать тот же ритм, а `[hidden]` обязан перебивать `display: flex`
# (иначе скрытая форма остаётся видимой — так уже было в `money.py`).
_HEAD_TX = (
    "<style>\n"
    "  #tx-form { display: flex; flex-direction: column; gap: var(--ui-s4); }\n"
    "  #tx-form[hidden], #tx-denied[hidden] { display: none; }\n"
    "</style>"
)

_BODY_TX = (
    '<div class="ui-small ui-muted" id="tx-status">Проверяю доступ…</div>'
    '<div id="tx-form" hidden>'
    '<div class="ui-segmented" id="tx-type" role="group" aria-label="Тип операции">'
    '<button type="button" class="ui-segmented__item ui-segmented__item--on"'
    ' data-type="expense" aria-pressed="true">Расход</button>'
    '<button type="button" class="ui-segmented__item"'
    ' data-type="income" aria-pressed="false">Доход</button>'
    "</div>"
    + layout.field(
        "Сумма, ₽",
        '<input class="ui-input ui-input--amount" id="tx-amount" type="text"'
        ' inputmode="decimal" enterkeyhint="next" autocomplete="off"'
        ' placeholder="3 000" aria-describedby="err-amount">'
        '<span class="ui-field__error" id="err-amount" hidden></span>',
        field_id="tx-amount",
    )
    + layout.field(
        "Категория",
        '<select class="ui-select" id="tx-category" aria-describedby="err-category">'
        '<option value="">Загружаю категории…</option></select>'
        '<span class="ui-field__error" id="err-category" hidden></span>',
        field_id="tx-category",
    )
    + layout.field(
        "Дата",
        '<div class="ui-datefield" id="tx-datefield">'
        '<span class="ui-datefield__value" id="tx-date-human">сегодня</span>'
        '<span class="ui-datefield__hint" aria-hidden="true">изменить</span>'
        '<input type="date" id="tx-date" aria-label="Дата учёта">'
        "</div>"
        '<span class="ui-field__error" id="err-tx_date" hidden></span>',
        field_id="tx-date",
    )
    + '<div class="ui-chips" id="tx-chips" role="group" aria-label="Быстрая дата">'
    '<button type="button" class="ui-chip" data-offset="0">Сегодня</button>'
    '<button type="button" class="ui-chip" data-offset="-1">Вчера</button>'
    '<button type="button" class="ui-chip" data-offset="1">Завтра</button>'
    "</div>"
    + layout.field(
        "Статья",
        '<input class="ui-input" id="tx-purpose" type="text" enterkeyhint="done"'
        ' autocomplete="off" placeholder="например, «Бунин»"'
        ' aria-describedby="err-purpose">'
        '<span class="ui-field__error" id="err-purpose" hidden></span>',
        field_id="tx-purpose",
    )
    + '<div class="ui-segmented" id="tx-plan" role="group" aria-label="План или факт">'
    '<button type="button" class="ui-segmented__item ui-segmented__item--on"'
    ' data-plan="fact" aria-pressed="true">Факт</button>'
    '<button type="button" class="ui-segmented__item"'
    ' data-plan="plan" aria-pressed="false">План</button>'
    "</div>"
    '<div class="ui-alert" id="tx-alert" role="alert" hidden></div>'
    '<div class="ui-actions ui-actions--bottom">'
    '<button type="button" class="ui-btn ui-btn--primary ui-btn--block" id="tx-save">'
    "Записать</button></div>"
    "</div>"
    # Состояние без права: словом, что происходит и где смотреть записанное.
    '<div class="ui-empty" id="tx-denied" hidden>'
    '<div class="ui-empty__title">Операции записывает организатор</div>'
    '<div class="ui-empty__text">Расход и доход вносит организатор студии — '
    "здесь их не добавить. Записанное за месяц видно в разделе «Деньги».</div>"
    "</div>"
)


TX_PAGE = layout.page("Операция", _BODY_TX, back="/webapp/money", head=_HEAD_TX,
                      scripts=_SCRIPT_TX)

__all__ = ["TX_PAGE"]
