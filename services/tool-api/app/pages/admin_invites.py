"""Админский экран «Приглашения»: выбор человека, «Новый участник», выпуск персонального кода.

Код выписывается на конкретного человека (docs/development/182 §4.3): форма
«роль → код» больше не существует. На экране — поиск людей без Telegram,
кнопка «Новый участник» (шторка: фамилия и имя + роль) и список активных кодов.

Модуль — только своя разметка и скрипт: общее ядро (токен, запросы, строки,
листы) и каркас лежат в `admin_core.py`. Границы и права — как в `docs/development/73`.

Форма выпуска рисуется только по праву `registration.code.issue`: оно есть у
администратора и нет у труппы, организатора и мастера (`db/init/008`, `db/init/061`,
`db/init/068`). Право приходит данными — список `allowed` и `actions.issue_codes`
ответа `/webapp/admin/overview`; форма отдаётся скрытой и показывается только по
явному «да». Без права (org, master — панель есть, `invites` в `allowed` нет)
пустое состояние говорит «Коды выпускает администратор», а не зовёт к
несуществующей кнопке; у труппы панель закрыта вовсе (403).
"""

from __future__ import annotations

from app.pages.admin_core import screen

_SCRIPT_INVITES = r"""
  var invites = [];
  var people = [];
  var chosen = null;
  var canIssue = false;
  var searchTimer = null;

  function selectPerson(person) {
    chosen = person;
    renderPeople();
    $("invite-issue").disabled = !chosen;
  }

  function personRow(person) {
    var row = el("button", "ui-row admin-row--stack");
    row.type = "button";
    row.setAttribute("data-person", String(person.id));
    if (chosen && chosen.id === person.id) row.classList.add("ui-row--attention");
    row.setAttribute("aria-pressed", chosen && chosen.id === person.id ? "true" : "false");
    row.appendChild(el("span", "ui-row__title", person.display_name));
    row.appendChild(el("span", "ui-row__meta", ROLE[person.role] || person.role || ""));
    row.onclick = function () { selectPerson(person); };
    return row;
  }

  function renderPeople() {
    var list = $("invite-people");
    clear(list);
    if (!people.length) {
      list.appendChild(el("p", "ui-small ui-muted",
        "Людей без Telegram нет — заведите кнопкой «Новый участник»."));
      return;
    }
    people.forEach(function (person) { list.appendChild(personRow(person)); });
  }

  function loadPeople() {
    var query = $("invite-search").value || "";
    return api("/webapp/admin/invite-candidates?q=" + encodeURIComponent(query))
      .then(function (res) {
        if (res.status >= 200 && res.status < 300 && res.body) {
          people = res.body.data || [];
          if (chosen && !people.some(function (p) { return p.id === chosen.id; })) {
            chosen = null;
            $("invite-issue").disabled = true;
          }
          renderPeople();
        }
      });
  }

  function issue() {
    if (!chosen) return;
    api("/webapp/admin/invite", { participant_id: chosen.id, ttl_days: 7 })
      .then(function (res) {
        if (res.status >= 200 && res.status < 300 && res.body && res.body.code) {
          showCode(res.body);
          refresh();
        } else {
          toast(problem(res));
        }
      });
  }

  function showCode(data) {
    var body = el("div", "admin-sheet");
    body.appendChild(el("span", "ui-amount ui-num admin-code", data.code));
    var who = [data.display_name, ROLE[data.role] || data.role].filter(Boolean).join(" · ");
    if (who) body.appendChild(el("p", "ui-text", who));
    body.appendChild(el("p", "ui-text", "Действует 7 дней, вход один."));
    body.appendChild(el("p", "ui-small ui-muted",
      "Передайте код человеку — он введёт его в боте."));
    sheet("Код приглашения", body, [
      { label: "Закрыть" },
      { label: "Копировать", primary: true, onTap: function () { copyText(data.code); } }
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
    var meta = [];
    if (invite.display_name) meta.push(invite.display_name);
    meta.push(ROLE[invite.role] || invite.role);
    if (invite.expires_at) meta.push("до " + when(invite.expires_at));
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

  /* Шторка «Новый участник»: запись заводится здесь же, где выпускается код
     (V-117, docs/development/182 §4.3.2). Роль перенесена из формы выпуска. */
  function createPerson(name, role) {
    api("/webapp/admin/person-new", { display_name: name, role: role })
      .then(function (res) {
        if (!(res.status >= 200 && res.status < 300 && res.body && res.body.participant)) {
          toast(problem(res));
          return null;
        }
        var person = res.body.participant;
        return api("/webapp/admin/invite", { participant_id: person.id, ttl_days: 7 })
          .then(function (issued) {
            closeSheet();
            if (issued.status >= 200 && issued.status < 300 && issued.body && issued.body.code) {
              showCode(issued.body);
            } else {
              toast("Человек создан, код не выпущен — выпустите ещё раз");
            }
            refresh();
          });
      });
  }

  function openNewPerson() {
    var body = el("div", "admin-sheet");
    var field = el("div", "ui-field");
    var label = el("label", "ui-field__label", "Фамилия Имя");
    label.setAttribute("for", "new-person-name");
    var input = el("input", "ui-input");
    input.id = "new-person-name";
    input.type = "text";
    input.setAttribute("autocomplete", "off");
    input.setAttribute("placeholder", "Например: Иванов Виктор");
    field.appendChild(label);
    field.appendChild(input);
    body.appendChild(field);

    var role = "troupe";
    var segments = el("div", "ui-segmented");
    segments.id = "new-person-role";
    segments.setAttribute("role", "group");
    segments.setAttribute("aria-label", "Роль");
    [["troupe", "Актёр"], ["org", "Организатор"], ["master", "Педагог"]].forEach(function (item) {
      var button = el("button", "ui-segmented__item" + (item[0] === role ? " ui-segmented__item--on" : ""),
        item[1]);
      button.type = "button";
      button.setAttribute("data-role", item[0]);
      button.setAttribute("aria-pressed", item[0] === role ? "true" : "false");
      button.onclick = function () {
        role = item[0];
        segments.querySelectorAll(".ui-segmented__item").forEach(function (other) {
          var on = other === button;
          other.classList.toggle("ui-segmented__item--on", on);
          other.setAttribute("aria-pressed", on ? "true" : "false");
        });
        hint.hidden = role !== "master";
      };
      segments.appendChild(button);
    });
    body.appendChild(segments);
    var hint = el("span", "ui-small ui-muted", "Дисциплину педагог выберет при регистрации.");
    hint.id = "new-person-hint";
    hint.hidden = true;
    body.appendChild(hint);

    sheet("Новый участник", body, [
      { label: "Отмена" },
      { label: "Создать и выпустить код", primary: true, keepOpen: true,
        onTap: function () { createPerson(input.value, role); } }
    ]);
    input.focus();
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
        return canIssue ? loadPeople() : null;
      });
    });
  }

  $("invite-search").addEventListener("input", function () {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(loadPeople, 250);
  });
  $("invite-new-person").addEventListener("click", openNewPerson);
  $("invite-issue").addEventListener("click", issue);
  refresh();
"""

_BODY_INVITES = """
<div class="ui-card" id="invite-new" hidden>
  <h2 class="ui-h2">Новый код</h2>
  <div class="ui-field">
    <label class="ui-field__label" for="invite-search">Фамилия или имя</label>
    <input class="ui-input" id="invite-search" type="search" inputmode="search"
           autocomplete="off" placeholder="Имя или фамилия">
  </div>
  <div class="ui-list" id="invite-people"></div>
  <button type="button" class="ui-btn ui-btn--block" id="invite-new-person">
    Новый участник</button>
  <button type="button" class="ui-btn ui-btn--primary ui-btn--block" id="invite-issue"
          disabled>Выпустить код</button>
</div>

<section class="ui-section">
  <div class="ui-section__head"><h2 class="ui-section__title">Активные коды</h2></div>
  <div class="ui-list" id="invite-list"></div>
</section>
<div class="ui-small ui-muted" id="status" role="status"></div>
"""

INVITES_PAGE = screen("Приглашения", _BODY_INVITES, _SCRIPT_INVITES)
