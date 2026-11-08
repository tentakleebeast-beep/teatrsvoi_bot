"""Форма операции «Записать» — второй экран раздела «Деньги» (docs/87 §4).

Одна форма: тип, сумма, категория, дата, статья, план/факт и одна кнопка
«Записать». Разметка здесь, скрипт — в `pages/money_tx_script.py`.
"""

from __future__ import annotations

from app.pages import layout
from app.pages.money_tx_script import _SCRIPT_TX

_BODY_TX = (
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
)


TX_PAGE = layout.page("Операция", _BODY_TX, back="/webapp/money", scripts=_SCRIPT_TX)

__all__ = ["TX_PAGE"]
