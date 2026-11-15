"""Экран «Опросы»: список опросов и вход в конструктор (E8, docs/development/162 §14.5).

Раздел Mini App под правом `poll.manage` (admin и org, V-088). Права на
данные проверяет API: страница — публичная константа, она лишь не рисует лишнего.
Отказ по праву — словами (как у «Рассылки»), а не 403 в пустоту: экран говорит,
у кого опросы есть.

Конструктор и сводка — соседние модули (`polls_edit.py`, `polls_results.py`):
модуль страницы не больше 400 строк (`tests/unit/test_webapp_pages_split.py`).
"""

from __future__ import annotations

from app.pages import layout
from app.pages.misc_common import JS_COMMON as _JS_COMMON

_BODY = """
<section class="ui-section" id="denied" hidden>
  <div class="ui-empty">
    <div class="ui-empty__title">Опросы ведут администратор и организатор</div>
    <div class="ui-empty__text">Создать опрос может человек с правом poll.manage —
      оно есть у администратора и организатора. Если анкета нужна вам, попросите
      их создать её и открыть вам результаты.</div>
  </div>
</section>

<div id="form">
  <p class="ui-small ui-muted">Опрос — это вопросы в чате: человек отвечает кнопками,
    прогресс сохраняется, ответы попадают в базу. После отправки вопросы и адресаты
    замораживаются.</p>

  <div class="ui-list" id="polls"></div>
  <div class="ui-alert" id="empty" hidden>Опросов пока нет. Заведите первый — из анкеты
    или с нуля.</div>

  <div class="ui-actions">
    <a class="ui-btn ui-btn--primary ui-btn--block" id="create"
       href="/webapp/polls/edit" data-svoi-cap="poll.manage">
      Новый опрос
    </a>
  </div>
  <div class="ui-alert" id="error" hidden></div>
</div>
"""

_SCRIPT = "<script>\n(function () {\n  \"use strict\";\n" + _JS_COMMON + r"""
  var $ = function (id) { return document.getElementById(id); };
  tgBoot();

  var STATUS = { draft: "черновик", running: "идёт", closed: "закрыт" };

  function showError(text) {
    var box = $("error");
    box.textContent = text || "";
    box.hidden = !text;
  }

  function showDenied() {
    $("denied").hidden = false;
    $("form").hidden = true;
  }

  function pollRow(item) {
    var target = item.status === "draft"
      ? "/webapp/polls/edit?poll_id=" + item.id
      : "/webapp/polls/results?poll_id=" + item.id;
    var row = make("a", "ui-row");
    row.href = target;
    var main = make("span", "ui-row__main");
    main.appendChild(make("span", "ui-row__title", item.title));
    var meta = plural(item.questions, "вопрос", "вопроса", "вопросов");
    if (item.status === "running" || item.status === "closed") {
      meta += " · ответили " + item.answered + " из " + item.invited;
    }
    main.appendChild(make("span", "ui-row__meta", meta));
    row.appendChild(main);
    row.appendChild(make("span", "ui-row__value", STATUS[item.status] || item.status));
    row.appendChild(make("span", "ui-row__chevron", "\u203a"));
    return row;
  }

  async function load() {
    var result = await api("/webapp/polls/list");
    if (result.status === 403) { showDenied(); return; }
    if (result.status !== 200) {
      showError("Не удалось загрузить опросы. Повторите позже.");
      return;
    }
    var host = $("polls");
    host.textContent = "";
    var items = result.body.polls || [];
    items.forEach(function (item) { host.appendChild(pollRow(item)); });
    $("empty").hidden = items.length > 0;
  }

  load();
})();
</script>
"""

POLLS_PAGE = layout.page("Опросы", _BODY, back="/webapp/more", caps=(), scripts=_SCRIPT)
