"""Экран «Мои опросы»: три группы своих прохождений (E8-D4, docs/development/164 §12.4).

Зачем свой экран: адресат получал приглашение в чат и, как только сообщение уехало
вверх, опрос было не найти (V-094, вариант «а»). Здесь те же прохождения лежат
списком: ждут ответа, пройденные, закрытые.

Права `poll.manage` на экране нет намеренно (`SCREEN_CAPS` — пустой кортеж): экран
показывает только свои прохождения, и отбирает их `participant_id` в
`/webapp/polls/mine/data`, а не возможность. Записи в `SECTIONS` тоже нет — с пустым
`caps` строка раздела не показалась бы никому (`section_visible` на пустом кортеже
отвечает «нет»), а с чужим правом дала бы второй вход.

Строки собирает сервер: подписи («3 вопроса · не начат», «пройден 14 ноября») и
склонения — его работа, своя копия склонений в JS разъехалась бы с чатом.
"""

from __future__ import annotations

from app.pages import layout
from app.pages.misc_common import JS_COMMON as _JS_COMMON

_BODY = """
<div id="loading" class="ui-list" aria-hidden="true">
  <div class="ui-row"><span class="ui-row__main">
    <span class="ui-skeleton ui-skeleton--line-title" style="width:60%"></span>
    <span class="ui-skeleton ui-skeleton--line-text" style="width:40%"></span>
  </span></div>
  <div class="ui-row"><span class="ui-row__main">
    <span class="ui-skeleton ui-skeleton--line-title" style="width:50%"></span>
    <span class="ui-skeleton ui-skeleton--line-text" style="width:35%"></span>
  </span></div>
</div>

<div id="groups"></div>

<div class="ui-empty" id="empty" hidden>
  <div class="ui-empty__title">Опросов пока нет</div>
  <div class="ui-empty__text">Когда вам назначат опрос, он появится здесь.</div>
</div>

<div class="ui-alert" id="error" hidden></div>
"""

_SCRIPT = "<script>\n(function () {\n  \"use strict\";\n" + _JS_COMMON + r"""
  var $ = function (id) { return document.getElementById(id); };
  tgBoot();

  // Порядок групп фиксирован: «Ждут ответа» сверху — это то, ради чего экран открыт.
  var GROUPS = [
    { key: "open", title: "Ждут ответа" },
    { key: "done", title: "Пройденные" },
    { key: "closed", title: "Закрытые" }
  ];

  function row(item, clickable) {
    // Открытое прохождение — ссылка (шеврон есть), пройденное и закрытое — просто
    // строка: смотреть свои прошлые ответы в E8 некуда (границы карты).
    var node = make(clickable ? "a" : "div", "ui-row");
    if (clickable) { node.href = item.url; }
    var main = make("span", "ui-row__main");
    main.appendChild(make("span", "ui-row__title", item.title || ""));
    if (item.meta) { main.appendChild(make("span", "ui-row__meta", item.meta)); }
    node.appendChild(main);
    if (clickable) { node.appendChild(make("span", "ui-row__chevron", "\u203a")); }
    return node;
  }

  function section(group, items) {
    var node = make("section", "ui-section");
    var head = make("div", "ui-section__head");
    head.appendChild(make("h2", "ui-section__title", group.title));
    // Счётчик — число строк в группе, а не что-то ещё: человек сверяет его глазами.
    head.appendChild(make("span", "ui-section__meta", String(items.length)));
    node.appendChild(head);
    var list = make("div", "ui-list");
    items.forEach(function (item) { list.appendChild(row(item, group.key === "open")); });
    node.appendChild(list);
    return node;
  }

  function render(body) {
    var host = $("groups");
    host.textContent = "";
    var shown = 0;
    GROUPS.forEach(function (group) {
      var items = body[group.key] || [];
      if (!items.length) { return; }        // пустая группа не рисуется
      shown += items.length;
      host.appendChild(section(group, items));
    });
    $("empty").hidden = shown > 0;
  }

  async function load() {
    var result = await api("/webapp/polls/mine/data");
    $("loading").hidden = true;
    // 5xx и обрыв: сообщение даёт баннер оболочки, экран своё не дублирует.
    if (result.status === 0 || result.status >= 500) { return; }
    if (result.status !== 200 || !result.body || result.body.ok !== true) {
      var box = $("error");
      box.textContent = "Не удалось загрузить опросы. Потяните экран вниз, чтобы повторить.";
      box.hidden = false;
      return;
    }
    render(result.body);
  }

  load();
})();
</script>
"""

POLLS_MINE_PAGE = layout.page("Мои опросы", _BODY, back="/webapp/home", caps=(),
                              scripts=_SCRIPT)
