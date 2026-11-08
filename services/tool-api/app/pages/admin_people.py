"""Админский экран «Люди». «Люди»: поиск, строки «имя / роли и статус», тариф справа из карточки.

Модуль — только своя разметка и скрипт: общее ядро (токен, запросы, строки,
листы) и каркас лежат в `admin_core.py`. Границы и права — как в `docs/development/73`.
"""

from __future__ import annotations

from app.pages.admin_core import screen
from app.pages.admin_disciplines import DISCIPLINES_SCRIPT

_SCRIPT_PEOPLE = r"""
  var people = [];
  var shown = 20;
  var query = "";
  var tariff = {};     // id → сумма или null; в списке участников тарифа нет
  var cards = {};      // id → карточка /webapp/admin/person
  var fetching = {};

  function tariffText(id) {
    if (!(id in tariff)) return "—";
    return tariff[id] === null ? "—" : money(tariff[id]);
  }

  function tariffOfCard(card) {
    if (card && card.tariff && card.tariff.tariff !== null
        && card.tariff.tariff !== undefined) return Number(card.tariff.tariff);
    return null;
  }

  function fillTariff(id, text) {
    var node = document.querySelector('[data-tariff="' + id + '"]');
    if (node) node.textContent = text;
  }

  function rememberCard(id, card) {
    cards[id] = card;
    tariff[id] = tariffOfCard(card);
    fillTariff(id, tariffText(id));
  }

  function loadCard(person) {
    if (cards[person.id]) return Promise.resolve(cards[person.id]);
    return api("/webapp/admin/person", { participant_id: person.id }).then(function (res) {
      if (res.status === 200 && res.body && res.body.data) {
        rememberCard(person.id, res.body.data);
        return res.body.data;
      }
      return null;
    }, function () { return null; });
  }

  function enrich(rows) {
    var queue = rows.filter(function (person) {
      return !(person.id in tariff) && !fetching[person.id];
    });
    function pump() {
      var person = queue.shift();
      if (!person) return;
      fetching[person.id] = true;
      loadCard(person).then(function () {
        delete fetching[person.id];
        pump();
      });
    }
    for (var i = 0; i < 4; i += 1) pump();
  }

  function matches(person) {
    var needle = query.trim().toLowerCase();
    if (!needle) return true;
    return String(person.display_name || "").toLowerCase().indexOf(needle) >= 0;
  }

  function personRow(person) {
    var row = el("button", "ui-row");
    row.type = "button";
    var main = el("span", "ui-row__main");
    main.appendChild(el("span", "ui-row__title", person.display_name));
    main.appendChild(el("span", "ui-row__meta", personMeta(person)));
    row.appendChild(main);
    var value = el("span", "ui-row__value", tariffText(person.id));
    value.setAttribute("data-tariff", person.id);
    row.appendChild(value);
    row.appendChild(el("span", "ui-row__chevron", "›"));
    row.onclick = function () { openPerson(person); };
    return row;
  }

  function renderPeople() {
    var list = $("people-list");
    clear(list);
    var visible = people.filter(matches);
    var slice = visible.slice(0, shown);
    var counts = (BLOCKS.people && BLOCKS.people.counts) || {};
    $("people-count").textContent = visible.length === people.length
      ? countLine(counts.active || 0, counts.pending || 0)
      : "найдено " + visible.length;
    if (!slice.length) {
      list.appendChild(empty("Никого не нашли", "Проверьте написание или очистите поиск."));
    }
    slice.forEach(function (person) { list.appendChild(personRow(person)); });
    var more = $("people-more");
    var rest = visible.length - slice.length;
    toggle(more, rest > 0);
    more.textContent = "Показать ещё " + rest;
    enrich(slice);
  }

  function changeRole(person, role, enabled, disciplines) {
    var payload = { participant_id: person.id, role: role, enabled: enabled };
    // Дисциплины передаются только при выдаче педагога: без них сервер отвечает
    // `master_discipline_required` — отказ придёт словами, но лучше не отправлять
    if (disciplines && disciplines.length) { payload.disciplines = disciplines; }
    api("/webapp/admin/grant", payload).then(function (res) {
      if (res.status >= 200 && res.status < 300) {
        closeSheet();
        toast((enabled ? "Роль выдана: " : "Роль отозвана: ")
              + (ROLE[role] || role) + " — " + person.display_name);
        refresh();
      } else {
        toast(problem(res));
      }
    });
  }

  function confirmAdmin(person) {
    var body = el("div", "admin-sheet");
    body.appendChild(el("p", "ui-text",
      "Полный доступ ко всем разделам, людям и деньгам. Роль можно отозвать позже."));
    sheet("Сделать администратором: " + person.display_name + "?", body, [
      { label: "Отмена" },
      { label: "Выдать роль", primary: true, onTap: function () { changeRole(person, "admin", true); } }
    ]);
  }

  function saveTariff(person, input) {
    var raw = String(input.value || "").replace(/\u00a0/g, "").replace(/\s/g, "").replace(",", ".");
    var value = raw === "" ? null : Number(raw);
    if (value !== null && (!isFinite(value) || value < 0)) {
      toast("Тариф — число не меньше нуля.");
      return;
    }
    api("/webapp/admin/tariff", { participant_id: person.id, tariff: value }).then(function (res) {
      if (res.status >= 200 && res.status < 300) {
        tariff[person.id] = value;
        closeSheet();
        toast(value === null ? "Тариф снят: " + person.display_name
                             : "Тариф обновлён: " + money(value) + " в месяц");
        refresh();
      } else {
        toast(problem(res));
      }
    });
  }

  function setBlocked(person, blocked) {
    api("/webapp/admin/block", {
      participant_id: person.id, blocked: blocked, reason: "webapp"
    }).then(function (res) {
      if (res.status >= 200 && res.status < 300) {
        closeSheet();
        toast((blocked ? "Доступ закрыт: " : "Доступ вернулся: ") + person.display_name);
        refresh();
      } else {
        toast(problem(res));
      }
    });
  }

  function confirmBlock(person) {
    var body = el("div", "admin-sheet");
    body.appendChild(el("p", "ui-text",
      "Доступ закроется сразу. Карточка и история останутся, вернуть можно здесь же — "
      + "кнопкой «Разблокировать»."));
    sheet("Заблокировать: " + person.display_name + "?", body, [
      { label: "Отмена" },
      { label: "Заблокировать", danger: true, onTap: function () { setBlocked(person, true); } }
    ]);
  }

  function confirmArchive(person) {
    var body = el("div", "admin-sheet");
    body.appendChild(el("p", "ui-text",
      person.display_name + " исчезнет из активных списков. Данные и история сохранятся — "
      + "вернуть можно в разделе «Архив»."));
    sheet("В архив: " + person.display_name + "?", body, [
      { label: "Отмена" },
      { label: "В архив", danger: true, onTap: function () {
        api("/webapp/admin/archive", { participant_id: person.id, reason: "webapp" })
          .then(function (res) {
            if (res.status >= 200 && res.status < 300) {
              closeSheet();
              toast(person.display_name + " в архиве");
              refresh();
            } else {
              toast(problem(res));
            }
          });
      } }
    ]);
  }

  function personSheet(person, card) {
    var body = el("div", "admin-sheet");

    if (!card) {
      body.appendChild(el("div", "ui-skeleton"));
      body.appendChild(el("div", "ui-skeleton"));
    } else {
      var dl = el("dl", "ui-dl");
      pair(dl, "Роли", rolesText(person.roles));
      pair(dl, "Статус", statusWord(person.status));
      // Дисциплины видны всем, кто открыл карточку: это чтение, а не управление
      pair(dl, "Дисциплины",
           disciplineNames(card.disciplines) || "нет ни одной");
      pair(dl, "Тариф", tariff[person.id] === null || tariff[person.id] === undefined
        ? "не назначен" : money(tariff[person.id]) + " в месяц");
      if (card.phone) pair(dl, "Телефон", card.phone);
      body.appendChild(dl);
    }

    if (ACTIONS.manage_rights) {
      var rolesBox = el("div", "ui-section");
      rolesBox.appendChild(el("h2", "ui-section__title", "Роли"));
      var actions = el("div", "ui-actions");
      ROLE_ORDER.forEach(function (role) {
        var has = (person.roles || []).indexOf(role) >= 0;
        var button = el("button", "ui-btn ui-btn--sm",
                        (has ? "Отозвать " : "Выдать ") + (ROLE_ACC[role] || role));
        button.type = "button";
        button.onclick = function () {
          if (!has && role === "admin") { confirmAdmin(person); return; }
          // Педагог выдаётся только с дисциплиной: сначала выбор, потом запрос (Г16)
          if (!has && role === "master") { confirmMaster(person, card); return; }
          changeRole(person, role, !has);
        };
        actions.appendChild(button);
      });
      rolesBox.appendChild(actions);
      if (card) {
        var subjects = el("button", "ui-btn ui-btn--sm ui-btn--block",
                          "Дисциплины педагога");
        subjects.type = "button";
        subjects.onclick = function () {
          sheet(person.display_name + ": дисциплины",
                disciplineSheet(person, card), [{ label: "Закрыть" }]);
        };
        rolesBox.appendChild(subjects);
      }
      body.appendChild(rolesBox);
    }

    if (ACTIONS.set_tariff) {
      var field = el("div", "ui-field");
      var label = el("label", "ui-field__label", "Тариф взноса, ₽ в месяц");
      var input = el("input", "ui-input ui-input--amount");
      input.type = "text";
      input.inputMode = "decimal";
      input.autocomplete = "off";
      input.id = "tariff-" + person.id;
      label.setAttribute("for", input.id);
      var current = tariff[person.id];
      input.value = current === null || current === undefined ? "" : String(current);
      field.appendChild(label);
      field.appendChild(input);
      field.appendChild(el("span", "ui-field__hint", "Пусто — снять тариф."));
      body.appendChild(field);
      var save = el("button", "ui-btn ui-btn--block", "Сохранить тариф");
      save.type = "button";
      save.onclick = function () { saveTariff(person, input); };
      body.appendChild(save);
    }

    if (card) {
      var caps = (card.capabilities || person.capabilities || []).slice().sort();
      var rights = el("div", "ui-section");
      rights.appendChild(el("h2", "ui-section__title", "Права"));
      rights.appendChild(el("p", "ui-small ui-muted",
        caps.length ? caps.length + " " + plural(caps.length, "возможность", "возможности", "возможностей")
                    : "Только то, что дают роли."));
      if (caps.length) rights.appendChild(el("p", "ui-small ui-muted admin-caps", caps.join(", ")));
      body.appendChild(rights);
    }

    if (ACTIONS.manage_rights && person.status !== "archived") {
      var isBlocked = person.status === "blocked";
      var block = el("button", "ui-btn ui-btn--block",
                     isBlocked ? "Разблокировать" : "Заблокировать");
      block.type = "button";
      block.onclick = function () {
        if (isBlocked) { setBlocked(person, false); } else { confirmBlock(person); }
      };
      body.appendChild(block);
      var archive = el("button", "ui-btn ui-btn--danger ui-btn--block", "В архив");
      archive.type = "button";
      archive.onclick = function () { confirmArchive(person); };
      body.appendChild(archive);
    }
    return body;
  }

  function openPerson(person) {
    var holder = el("div", "admin-sheet");
    holder.appendChild(el("div", "ui-skeleton"));
    sheet(person.display_name, holder, [{ label: "Закрыть" }]);
    loadCard(person).then(function (card) {
      holder.replaceChildren(personSheet(person, card));
      if (!card) toast("Карточка не открылась. Повторите.");
    });
  }

  function refresh() {
    return auth().then(function (denied) {
      if (denied) { status(denied, true); return null; }
      return loadOverview().then(function (data) {
        if (!data) return null;
        people = (BLOCKS.people && BLOCKS.people.data) || [];
        // Д6: вход в «Взносы» — по тому же праву, что у начисления и тарифа.
        toggle($("people-dues"), !!(ACTIONS.accrue_dues || ACTIONS.set_tariff));
        renderPeople();
        return data;
      });
    });
  }

  $("people-dues").onclick = function () { location.href = "/webapp/admin/t/dues"; };

  $("people-search").addEventListener("input", function (event) {
    query = event.target.value || "";
    shown = 20;
    renderPeople();
  });
  $("people-more").addEventListener("click", function () { shown += 20; renderPeople(); });
  refresh();
"""

_BODY_PEOPLE = """
<div class="ui-field">
  <label class="ui-field__label" for="people-search">Кого найти</label>
  <input class="ui-input" id="people-search" type="search" inputmode="search"
         autocomplete="off" placeholder="Имя или фамилия">
</div>
<button class="ui-btn ui-btn--block" id="people-dues" type="button" hidden
        data-svoi-cap="budget.write">Взносы: тариф и начисление</button>
<div class="ui-small ui-muted" id="people-count"></div>
<div class="ui-list" id="people-list"></div>
<button class="ui-btn ui-btn--block" id="people-more" type="button" hidden>Показать ещё</button>
<div class="ui-small ui-muted" id="status" role="status"></div>
"""

PEOPLE_PAGE = screen("Люди", _BODY_PEOPLE, _SCRIPT_PEOPLE + DISCIPLINES_SCRIPT)
