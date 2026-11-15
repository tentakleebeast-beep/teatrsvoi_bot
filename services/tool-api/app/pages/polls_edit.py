"""Конструктор опроса: вопросы и адресаты (E8, docs/development/162 §14.5).

Один экран — два шага: «вопросы» и «кому отправить». Так сделано намеренно:
адресат — не отдельный раздел, а следующий шаг той же задачи, и кнопка
«Дальше: кому отправить» закреплена у низа, как на мокапе `01b`.

Строки вопросов компактные (56 px, тип значением справа): конструктор открывают,
чтобы поправить один вопрос, а не листать два экрана (§6 п. 1, замер 1 255 px
против 1 446). Люди на шаге адресатов сворачиваются до «в списке N, показать
всех» — иначе экран растёт линейно по труппе (§6 п. 2).

Скрипт вынесен в `polls_edit_js.py`: модуль страницы не больше 400 строк
(`tests/unit/test_webapp_pages_split.py`).
"""

from __future__ import annotations

from app.pages import layout
from app.pages.polls_edit_js import SCRIPT as _SCRIPT

_BODY = """
<section class="ui-section" id="denied" hidden>
  <div class="ui-empty">
    <div class="ui-empty__title">Опросы ведут администратор и организатор</div>
    <div class="ui-empty__text">Право poll.manage есть у администратора и организатора —
      конструктор открыт им. Попросите администратора или организатора создать опрос.</div>
  </div>
</section>

<div id="form" hidden>
  <div class="ui-field">
    <label class="ui-field__label" for="title">Тема опроса</label>
    <input class="ui-input" id="title" type="text" maxlength="300"
           placeholder="Анкета организаторам">
    <span class="ui-field__error" id="title-error" hidden></span>
  </div>

  <section id="step-questions">
    <p class="ui-small ui-muted">Вопросы идут сверху вниз — так человек их увидит в чате.</p>
    <div class="ui-list" id="questions"></div>
    <div class="ui-actions">
      <button class="ui-btn" id="add-question" type="button">Добавить вопрос</button>
    </div>

    <div class="ui-card" id="editor" hidden>
      <div class="ui-field">
        <label class="ui-field__label" for="q-text">Вопрос</label>
        <textarea class="ui-textarea" id="q-text" maxlength="2000"></textarea>
      </div>
      <div class="ui-field">
        <label class="ui-field__label" for="q-kind">Тип ответа</label>
        <select class="ui-select" id="q-kind">
          <option value="single">один из списка</option>
          <option value="multi">несколько из списка</option>
          <option value="yesno">да или нет</option>
          <option value="number">число</option>
          <option value="text">свободный текст</option>
        </select>
      </div>
      <div class="ui-field" id="options-field">
        <label class="ui-field__label" for="q-options">Варианты — по одному в строке</label>
        <textarea class="ui-textarea" id="q-options"></textarea>
      </div>
      <label class="ui-row"><span class="ui-row__main">Обязательный вопрос</span>
        <input type="checkbox" id="q-required" checked></label>
      <label class="ui-row" id="free-field"><span class="ui-row__main">Можно свой вариант</span>
        <input type="checkbox" id="q-free"></label>
      <div class="ui-actions">
        <button class="ui-btn ui-btn--primary" id="q-done" type="button">Готово</button>
        <button class="ui-btn ui-btn--ghost" id="q-delete" type="button">Удалить</button>
      </div>
    </div>

    <div class="ui-actions ui-actions--bottom">
      <button class="ui-btn ui-btn--primary ui-btn--block" id="to-audience" type="button">
        Дальше: кому отправить
      </button>
    </div>
  </section>

  <section id="step-audience" hidden>
    <div class="ui-chips" id="rule-chips">
      <button class="ui-chip" type="button" data-rule="all_active">Все активные</button>
      <button class="ui-chip" type="button" data-rule="role:org">Организаторы</button>
      <button class="ui-chip" type="button" data-rule="role:troupe">Актёры</button>
      <button class="ui-chip" type="button" data-rule="role:master">Педагоги</button>
    </div>
    <div class="ui-field">
      <label class="ui-field__label" for="rule-project">По спектаклю</label>
      <select class="ui-select" id="rule-project"><option value="">не выбран</option></select>
    </div>

    <div class="ui-alert" id="audience-count">Считаю адресатов…</div>

    <div class="ui-list" id="people"></div>
    <button class="ui-btn ui-btn--ghost" id="people-all" type="button" hidden>
      показать всех</button>

    <details class="ui-acc" id="audience-extra">
      <summary class="ui-row ui-row--compact">
        <span class="ui-row__main">
          <span class="ui-row__title">Исключения и получатели результатов</span>
          <span class="ui-row__meta">необязательно</span>
        </span>
      </summary>
      <div class="ui-field">
        <label class="ui-field__label" for="exclude">Кроме — выберите людей</label>
        <select class="ui-select" id="exclude" multiple size="2"></select>
      </div>
      <div class="ui-field">
        <label class="ui-field__label" for="viewers">Кому показать результаты</label>
        <select class="ui-select" id="viewers" multiple size="2"></select>
      </div>
    </details>

    <div class="ui-actions ui-actions--bottom">
      <button class="ui-btn" id="back-to-questions" type="button">К вопросам</button>
      <button class="ui-btn ui-btn--primary" id="send" type="button" disabled>Отправить</button>
    </div>
  </section>

  <div class="ui-alert" id="error" hidden></div>
  <div class="ui-alert ui-alert--ok" id="note" hidden></div>
</div>
"""

POLL_EDIT_PAGE = layout.page("Конструктор опроса", _BODY, back="/webapp/polls",
                             caps=(), scripts=_SCRIPT)
