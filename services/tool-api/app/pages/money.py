"""«Деньги» — вкладка раздела денег (docs/86 §2.3, docs/87 §2).

Первый экран отвечает на вопрос «сколько за месяц и что было», а не показывает
реестр: итог крупно, операции строками, взносы строками. Второй экран — форма
операции — в `pages/money_tx.py`, его скрипт в `pages/money_tx_script.py`,
скрипт этой вкладки — в `pages/money_script.py`. Имена `MONEY_PAGE` и `TX_PAGE`
остаются доступны отсюда: это точка импорта для `webapp_page.py`, стенда и тестов.

Контракты, на которые опирается страница (менять их здесь нельзя):

    GET  /webapp/home/data        — что человеку можно: tile /webapp/tx = budget.write
    GET  /webapp/admin/overview   — блок budget: period_title + последние операции месяца
    GET  /webapp/my/dues          — свои начисления: current / debt / tariff / data
    GET  /webapp/categories       — категории по направлению (expense / income)
    POST /webapp/tx/save          — запись; ключ идемпотентности от клиента
    POST /webapp/tx/save          — обратная операция для «Вернуть» в сообщении

Правила дизайн-системы, которые держит этот модуль: без эмодзи, без таблиц, без
горизонтального скролла, текст не мельче 13 px, цели не меньше 44 px, одна главная
кнопка на экран, вместо alert/confirm — SVOI.sheet и SVOI.toast.
"""

from __future__ import annotations

from app.pages import layout
from app.pages.money_script import _SCRIPT_MONEY
from app.pages.money_tx import TX_PAGE

__all__ = ["MONEY_PAGE", "TX_PAGE"]

# Цвет знака суммы: расход — смысловой danger, доход — ok. Классы локальные,
# потому что общий CSS проекта не трогаем (он общий для всех экранов).
_HEAD = """<style>
  #view-ops, #view-budget, #view-dues {
    display: flex; flex-direction: column; gap: var(--ui-s4);
  }
  #view-ops[hidden], #view-budget[hidden], #view-dues[hidden] { display: none; }
  .money-out { color: var(--ui-danger); }
  .money-in { color: var(--ui-ok); }
  .money-value { white-space: nowrap; font-variant-numeric: tabular-nums; }
  .money-note { margin: 0; }
  /* Главное действие вкладки не должно уезжать под нижние вкладки. */
  .money-actions { bottom: calc(var(--ui-tabbar-h) + env(safe-area-inset-bottom)); }
  /* Три подписи в сегменте должны помещаться одной строкой и на 320 px. */
  #money-seg .ui-segmented__item { font-size: 14px; padding: 0 var(--ui-s2); white-space: nowrap; }
  .money-sheet-line { font-size: var(--ui-text); }
  .ui-row__title, .ui-row__meta { overflow-wrap: anywhere; }
</style>"""

_BODY_MONEY = """
<div class="ui-segmented" id="money-seg" role="group" aria-label="Раздел денег" hidden>
  <button type="button" class="ui-segmented__item ui-segmented__item--on"
          data-view="ops" aria-pressed="true" hidden>Операции</button>
  <button type="button" class="ui-segmented__item"
          data-view="budget" aria-pressed="false" hidden>Бюджет</button>
  <button type="button" class="ui-segmented__item"
          data-view="dues" aria-pressed="false">Мои взносы</button>
</div>

<section id="view-ops" hidden aria-label="Операции месяца">
  <div class="ui-card">
    <span class="ui-card__label" id="ops-period"></span>
    <span class="ui-amount ui-num" id="ops-total">—</span>
    <span class="ui-small ui-muted" id="ops-meta"></span>
  </div>
  <div class="ui-list" id="ops-list"></div>
  <p class="ui-small ui-muted money-note" id="ops-note" hidden>
    Показаны последние операции месяца.
  </p>
</section>

<section id="view-budget" hidden aria-label="Бюджет месяца">
  <div class="ui-card">
    <span class="ui-card__label" id="budget-period"></span>
    <span class="ui-amount ui-num" id="budget-total">—</span>
    <span class="ui-small ui-muted" id="budget-meta"></span>
  </div>
  <div class="ui-list" id="budget-list"></div>
  <p class="ui-small ui-muted money-note" id="budget-note" hidden>
    План и весь месяц — в разделе «Управление», пункт «Бюджет».
  </p>
</section>

<section id="view-dues" aria-label="Мои взносы">
  <div class="ui-card">
    <span class="ui-card__label" id="dues-period"></span>
    <span class="ui-amount ui-num" id="dues-total">—</span>
    <span class="ui-small ui-muted" id="dues-meta">Считаю…</span>
  </div>
  <p class="ui-small ui-muted money-note" id="dues-status"></p>
  <div class="ui-list" id="dues-history"></div>
</section>

<div class="ui-actions ui-actions--bottom money-actions" id="money-actions" hidden>
  <a class="ui-btn ui-btn--primary ui-btn--block" href="/webapp/tx"
     data-svoi-cap="budget.write">Записать операцию</a>
</div>
"""

MONEY_PAGE = layout.page("Деньги", _BODY_MONEY, tab="money", head=_HEAD,
                         scripts=_SCRIPT_MONEY)
