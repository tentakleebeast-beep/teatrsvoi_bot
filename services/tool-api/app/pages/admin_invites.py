"""Админский экран «Приглашения». «Приглашения»: выпуск кода в листе с «Копировать» и список активных.

Модуль — только своя разметка и скрипт: общее ядро (токен, запросы, строки,
листы) и каркас лежат в `admin_core.py`. Границы и права — как в `docs/development/73`.
"""

from __future__ import annotations

from app.pages.admin_core import screen

_SCRIPT_INVITES = r"""
  var invites = [];
  var role = "troupe";

  function issue() {
    api("/webapp/admin/invite", { role: role, ttl_days: 7, max_uses: 1 })
      .then(function (res) {
        if (res.status >= 200 && res.status < 300 && res.body && res.body.code) {
          showCode(res.body.code);
          refresh();
        } else {
          toast(problem(res));
        }
      });
  }

  function showCode(code) {
    var body = el("div", "admin-sheet");
    body.appendChild(el("span", "ui-amount ui-num admin-code", code));
    body.appendChild(el("p", "ui-text",
      "Роль: " + (ROLE[role] || role) + ". Действует 7 дней, вход один."));
    body.appendChild(el("p", "ui-small ui-muted",
      "Передайте код человеку — он введёт его в боте."));
    sheet("Код приглашения", body, [
      { label: "Закрыть" },
      { label: "Копировать", primary: true, onTap: function () { copyText(code); } }
    ]);
  }

  function revoke(invite) {
    api("/webapp/admin/invite-revoke", { code: invite.code }).then(function (res) {
      if (res.status >= 200 && res.status < 300) {
        closeSheet();
        toast("Код отозван: " + invite.code);
        refresh();
      } else {
        toast(problem(res));
      }
    });
  }

  function confirmRevoke(invite) {
    var body = el("div", "admin-sheet");
    body.appendChild(el("p", "ui-text",
      "Код " + invite.code + " перестанет действовать. Ввести его будет нельзя."));
    sheet("Отозвать код: " + invite.code + "?", body, [
      { label: "Отмена" },
      { label: "Отозвать", danger: true, onTap: function () { revoke(invite); } }
    ]);
  }

  function inviteRow(invite) {
    var row = el("div", "ui-row admin-row--stack");
    var main = el("span", "ui-row__main");
    main.appendChild(el("span", "ui-row__title admin-code", invite.code));
    var meta = [ROLE[invite.role] || invite.role];
    if (invite.expires_at) meta.push("до " + when(invite.expires_at));
    if (invite.max_uses) meta.push("использован " + (invite.uses || 0) + " из " + invite.max_uses);
    main.appendChild(el("span", "ui-row__meta", meta.join(" · ")));
    row.appendChild(main);
    if (ACTIONS.issue_codes) {
      var actions = el("div", "ui-actions admin-actions--split");
      var copy = el("button", "ui-btn ui-btn--sm", "Копировать");
      copy.type = "button";
      copy.onclick = function () { copyText(invite.code); };
      var kill = el("button", "ui-btn ui-btn--sm", "Отозвать");
      kill.type = "button";
      kill.onclick = function () { confirmRevoke(invite); };
      actions.appendChild(copy);
      actions.appendChild(kill);
      row.appendChild(actions);
    }
    return row;
  }

  function renderInvites() {
    var list = $("invite-list");
    clear(list);
    if (!invites.length) {
      list.appendChild(empty("Активных кодов нет",
        "Выпустите код кнопкой сверху — он появится здесь."));
      return;
    }
    invites.forEach(function (invite) { list.appendChild(inviteRow(invite)); });
  }

  function refresh() {
    return auth().then(function (denied) {
      if (denied) { status(denied, true); return null; }
      return loadOverview().then(function (data) {
        if (!data) return null;
        invites = BLOCKS.invites || [];
        renderInvites();
        toggle($("invite-new"), Boolean(ACTIONS.issue_codes));
        return data;
      });
    });
  }

  document.querySelectorAll("#invite-role .ui-segmented__item").forEach(function (button) {
    button.onclick = function () {
      role = button.getAttribute("data-role");
      document.querySelectorAll("#invite-role .ui-segmented__item").forEach(function (item) {
        item.classList.toggle("ui-segmented__item--on", item === button);
      });
    };
  });
  $("invite-issue").addEventListener("click", issue);
  refresh();
"""

_BODY_INVITES = """
<div class="ui-card" id="invite-new">
  <h2 class="ui-h2">Новый код</h2>
  <div class="ui-segmented" id="invite-role" role="group" aria-label="Роль">
    <button type="button" class="ui-segmented__item ui-segmented__item--on"
            data-role="troupe">Труппа</button>
    <button type="button" class="ui-segmented__item" data-role="org">Организатор</button>
  </div>
  <span class="ui-small ui-muted">Действует 7 дней, вход один.</span>
  <button type="button" class="ui-btn ui-btn--primary ui-btn--block" id="invite-issue">
    Выпустить код</button>
</div>

<section class="ui-section">
  <div class="ui-section__head"><h2 class="ui-section__title">Активные коды</h2></div>
  <div class="ui-list" id="invite-list"></div>
</section>
<div class="ui-small ui-muted" id="status" role="status"></div>
"""

INVITES_PAGE = screen("Приглашения", _BODY_INVITES, _SCRIPT_INVITES)
