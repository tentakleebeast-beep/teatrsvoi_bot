"""Админский экран «Обращения». «Обращения»: открытые обращения, решение принимается на месте.

Модуль — только своя разметка и скрипт: общее ядро (токен, запросы, строки,
листы) и каркас лежат в `admin_core.py`. Границы и права — как в `docs/development/73`.
"""

from __future__ import annotations

from app.pages.admin_core import screen

_SCRIPT_FEEDBACK_QUEUE = r"""
  var FB_STATUS = { new: "новое", seen: "в работе", planned: "запланировано",
                 done: "сделано", rejected: "отклонено" };
  var KIND = { broken: "не работает", idea: "улучшение", "wish-fl": "просьба о доработке",
               question: "вопрос", praise: "хорошо работает" };

  function decide(item, next, resolution) {
    api("/webapp/admin/feedback/decide", { feedback_id: item.id, status: next,
                                           resolution: resolution || null,
                                           source: "human" })
      .then(function (res) {
        if (res.status >= 200 && res.status < 300) {
          closeSheet();
          toast("Обращение №" + item.id + ": " + (FB_STATUS[next] || next));
          refresh();
        } else {
          toast(problem(res));
        }
      });
  }

  function askResolution(item, next) {
    var box = el("div", "admin-sheet");
    box.appendChild(el("p", "ui-text", item.text));
    var field = el("div", "ui-field");
    field.appendChild(el("label", "ui-field__label", "Ответ автору"));
    var area = document.createElement("textarea");
    area.className = "ui-textarea";
    area.placeholder = "Что сделано или почему так";
    field.appendChild(area);
    box.appendChild(field);
    sheet("Отметить: " + (FB_STATUS[next] || next), box, [
      { label: "Отмена" },
      { label: "Сохранить", primary: true, onTap: function () { decide(item, next, area.value.trim()); } }
    ]);
  }

  function itemRow(item) {
    var row = el("div", "ui-row admin-row--stack");
    var main = el("span", "ui-row__main");
    main.appendChild(el("span", "ui-row__title", KIND[item.kind] || item.kind || "обращение"));
    var meta = ["№" + item.id];
    if (item.author) meta.push(item.author);
    if (item.created_at) meta.push(when(item.created_at));
    meta.push(FB_STATUS[item.status] || item.status || "");
    main.appendChild(el("span", "ui-row__meta", meta.filter(Boolean).join(" · ")));
    main.appendChild(el("span", "ui-row__meta", item.text || ""));
    row.appendChild(main);
    var actions = el("div", "ui-actions admin-actions--split");
    var more = el("button", "ui-btn ui-btn--sm", "Решить");
    more.type = "button";
    more.onclick = function () { askResolution(item, "done"); };
    var planned = el("button", "ui-btn ui-btn--sm", "В план");
    planned.type = "button";
    planned.onclick = function () { askResolution(item, "planned"); };
    actions.appendChild(more);
    actions.appendChild(planned);
    row.appendChild(actions);
    return row;
  }

  function refresh() {
    return auth().then(function (denied) {
      if (denied) { status(denied, true); return null; }
      return api("/webapp/admin/feedback").then(function (res) {
        var data = (res.body && res.body.ok) ? res.body : { data: [] };
        var rows = data.data || [];
        var list = $("feedback-list");
        clear(list);
        if (!rows.length) {
          list.appendChild(empty("Открытых обращений нет",
            "Новое обращение появится здесь, когда кто-то напишет из бота."));
        } else {
          rows.forEach(function (item) { list.appendChild(itemRow(item)); });
        }
        $("feedback-count").textContent = rows.length
          ? rows.length + " " + plural(rows.length, "обращение", "обращения", "обращений")
          : "";
        return data;
      });
    });
  }

  refresh();
"""

_BODY_FEEDBACK_QUEUE = """
<div class="ui-small ui-muted" id="feedback-count"></div>
<div class="ui-list" id="feedback-list"></div>
<div class="ui-small ui-muted" id="status" role="status"></div>
"""

FEEDBACK_QUEUE_PAGE = screen("Обращения", _BODY_FEEDBACK_QUEUE, _SCRIPT_FEEDBACK_QUEUE)
