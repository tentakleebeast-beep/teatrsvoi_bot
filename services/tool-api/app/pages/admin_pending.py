"""Админский экран «Заявки». «Заявки»: строка — человек, под ней «Принять»/«Отклонить» (44 px).

Модуль — только своя разметка и скрипт: общее ядро (токен, запросы, строки,
листы) и каркас лежат в `admin_core.py`. Границы и права — как в `docs/development/73`.
"""

from __future__ import annotations

from app.pages.admin_core import screen

_SCRIPT_PENDING = r"""
  function decide(person, approve) {
    api("/webapp/admin/approve", { participant_id: person.id, approve: approve })
      .then(function (res) {
        if (res.status >= 200 && res.status < 300) {
          closeSheet();
          toast(approve ? "Заявка принята: " + person.display_name
                        : "Заявка отклонена: " + person.display_name);
          refresh();
        } else {
          toast(problem(res));
        }
      });
  }

  function confirmReject(person) {
    var body = el("div", "admin-sheet");
    body.appendChild(el("p", "ui-text",
      person.display_name + " не попадёт в труппу. Заявка останется в архиве."));
    sheet("Отклонить заявку: " + person.display_name + "?", body, [
      { label: "Отмена" },
      { label: "Отклонить", danger: true, onTap: function () { decide(person, false); } }
    ]);
  }

  function pendingRow(person) {
    var row = el("div", "ui-row admin-row--stack");
    var main = el("span", "ui-row__main");
    main.appendChild(el("span", "ui-row__title", person.display_name));
    var meta = [ROLE[person.role] || person.role || "без роли"];
    if (person.invite_code) meta.push("код " + person.invite_code);
    if (person.created_at) meta.push(when(person.created_at));
    main.appendChild(el("span", "ui-row__meta", meta.join(" · ")));
    row.appendChild(main);
    if (!person.invite_code && person.phone) {
      row.appendChild(el("span", "ui-row__meta", "Телефон: " + person.phone));
    }
    var actions = el("div", "ui-actions admin-actions--split");
    var accept = el("button", "ui-btn ui-btn--sm ui-btn--primary", "Принять");
    accept.type = "button";
    accept.onclick = function () { decide(person, true); };
    var reject = el("button", "ui-btn ui-btn--sm", "Отклонить");
    reject.type = "button";
    reject.onclick = function () { confirmReject(person); };
    actions.appendChild(accept);
    actions.appendChild(reject);
    row.appendChild(actions);
    return row;
  }

  function renderPending(rows) {
    var list = $("pending-list");
    clear(list);
    if (!rows.length) {
      list.appendChild(empty("Заявок нет",
        "Новая заявка появится здесь, когда человек зарегистрируется по коду."));
      return;
    }
    rows.forEach(function (person) { list.appendChild(pendingRow(person)); });
  }

  function refresh() {
    return auth().then(function (denied) {
      if (denied) { status(denied, true); return null; }
      return loadOverview().then(function (data) {
        if (!data) return null;
        var rows = BLOCKS.pending || [];
        renderPending(rows);
        status(rows.length
          ? rows.length + " " + plural(rows.length, "заявка ждёт", "заявки ждут", "заявок ждут")
            + " решения"
          : "");
        return data;
      });
    });
  }

  refresh();
"""

_BODY_PENDING = """
<div class="ui-list" id="pending-list"></div>
<div class="ui-small ui-muted" id="status" role="status"></div>
"""

PENDING_PAGE = screen("Заявки", _BODY_PENDING, _SCRIPT_PENDING)
