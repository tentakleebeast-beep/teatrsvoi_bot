"""Админский экран «Приглашения». «Приглашения»: выпуск кода в листе с «Копировать» и список активных.

Модуль — только своя разметка и скрипт: общее ядро (токен, запросы, строки,
листы) и каркас лежат в `admin_core.py`. Границы и права — как в `docs/development/73`.

Форма выпуска рисуется только по праву `registration.code.issue`: оно есть у
администратора и нет у труппы, организатора и мастера (`db/init/008`, `db/init/061`).
Право приходит данными — список `allowed` и `actions.issue_codes` ответа
`/webapp/admin/overview`; форма отдаётся скрытой и показывается только по явному
«да». Без права (org, master — панель есть, `invites` в `allowed` нет) пустое
состояние говорит «Коды выпускает администратор», а не зовёт к несуществующей
кнопке; у труппы панель закрыта вовсе (403) и причину печатает отказ, но формы
на экране тоже нет (отчёт 142 §3 строка 24, принципы 91: 8, 9, Б3; карта t_343d42fe).
"""

from __future__ import annotations

from app.pages.admin_core import screen

_SCRIPT_INVITES = r"""
  var invites = [];
  var role = "troupe";
  var canIssue = false;

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
    if (!canIssue) {
      list.appendChild(empty("Коды выпускает администратор",
        "У вашей роли нет права выпускать приглашения."));
      return;
    }
    if (!invites.length) {
      list.appendChild(empty("Активных кодов нет",
        "Выпустите код кнопкой выше — он появится здесь."));
      return;
    }
    invites.forEach(function (invite) { list.appendChild(inviteRow(invite)); });
  }

  /* Право `registration.code.issue` приходит данными: список `allowed` и
     `actions.issue_codes` ответа `/webapp/admin/overview`. Роль на экране не
     решает: у troupe панель закрыта вовсе (403), у org и master панель есть, а
     формы выпуска нет. До правки форма стояла в разметке и убиралась уже после
     ответа, поэтому у troupe «Выпустить код» и «Раздел доступен организатору и
     администратору» были на экране одновременно (отчёт 142 §3 строка 24,
     принципы 91: 8, 9, Б3; карта t_343d42fe). */
  function applyRights(can) {
    canIssue = can === true;
    toggle($("invite-new"), canIssue);
    invites = canIssue ? (BLOCKS.invites || []) : [];
    renderInvites();
    status(canIssue ? "" : ERRORS.admin_panel_forbidden, !canIssue);
  }

  function refresh() {
    return auth().then(function (denied) {
      /* Вход не состоялся — формы на экране быть не должно; причину печатают
         слова сервера. */
      if (denied) { toggle($("invite-new"), false); status(denied, true); return null; }
      return loadOverview().then(function (data) {
        /* 403 панели (troupe) и сетевая ошибка: форму не показываем — права
           никто не подтвердил. Причину `loadOverview` уже сказал в статусе. */
        if (!data) { toggle($("invite-new"), false); return null; }
        applyRights((data.allowed || []).indexOf("invites") !== -1
          && Boolean(ACTIONS.issue_codes));
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
<div class="ui-card" id="invite-new" hidden>
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
