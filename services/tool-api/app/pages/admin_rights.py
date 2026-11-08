"""Админский экран «Права». «Права»: матрица «роль → возможности» и точечная выдача через поиск.

Модуль — только своя разметка и скрипт: общее ядро (токен, запросы, строки,
листы) и каркас лежат в `admin_core.py`. Границы и права — как в `docs/development/73`.
"""

from __future__ import annotations

from app.pages.admin_core import screen

_SCRIPT_RIGHTS = r"""
  var people = [];
  var matrix = [];
  var query = "";

  function capabilityCodes() {
    var seen = {};
    matrix.forEach(function (row) {
      (row.caps || []).forEach(function (code) { seen[code] = true; });
    });
    return Object.keys(seen).sort();
  }

  function renderMatrix() {
    var box = $("matrix");
    clear(box);
    matrix.forEach(function (row) {
      var card = el("div", "ui-card");
      card.appendChild(el("h2", "ui-h2", ROLE[row.role] || row.role));
      var dl = el("dl", "ui-dl");
      var caps = (row.caps || []).slice().sort();
      pair(dl, "Возможностей", caps.length + "");
      card.appendChild(dl);
      card.appendChild(el("span", "ui-card__label", "Что может"));
      var codes = el("div", "ui-small ui-muted admin-caps");
      codes.style.display = "flex";
      codes.style.flexDirection = "column";
      codes.style.gap = "2px";
      if (caps.length) {
        caps.forEach(function (code) { codes.appendChild(el("span", null, code)); });
      } else {
        codes.appendChild(el("span", null, "ничего"));
      }
      card.appendChild(codes);
      box.appendChild(card);
    });
    if (!matrix.length) box.appendChild(empty("Матрица пуста", "Роли не настроены."));
  }

  function resultRow(person) {
    var row = el("button", "ui-row");
    row.type = "button";
    var main = el("span", "ui-row__main");
    main.appendChild(el("span", "ui-row__title", person.display_name));
    main.appendChild(el("span", "ui-row__meta", personMeta(person)));
    row.appendChild(main);
    row.appendChild(el("span", "ui-row__chevron", "›"));
    row.onclick = function () { openRights(person); };
    return row;
  }

  function renderResults() {
    var box = $("rights-results");
    clear(box);
    var needle = query.trim().toLowerCase();
    var hint = $("rights-hint");
    if (!needle) {
      hint.textContent = "Начните вводить имя — покажем совпадения.";
      return;
    }
    var found = people.filter(function (person) {
      return String(person.display_name || "").toLowerCase().indexOf(needle) >= 0;
    }).slice(0, 10);
    hint.textContent = found.length ? "Совпадений: " + found.length : "Никого не нашли.";
    found.forEach(function (person) { box.appendChild(resultRow(person)); });
  }

  function setCapability(person, code, allowed) {
    api("/webapp/admin/grant", {
      participant_id: person.id, capability: code, allowed: allowed
    }).then(function (res) {
      if (res.status >= 200 && res.status < 300) {
        closeSheet();
        toast((allowed ? "Право выдано: " : "Право отозвано: ") + code);
        refresh();
      } else {
        toast(problem(res));
      }
    });
  }

  function rightsSheet(person, card) {
    var body = el("div", "admin-sheet");
    body.appendChild(el("p", "ui-small ui-muted",
      rolesText(person.roles) + " · " + statusWord(person.status)));

    var search = el("input", "ui-input");
    search.type = "search";
    search.inputMode = "search";
    search.placeholder = "Найти право";
    search.setAttribute("aria-label", "Найти право");
    body.appendChild(search);

    var list = el("div", "ui-list");
    body.appendChild(list);
    var current = card && card.capabilities ? card.capabilities : (person.capabilities || []);
    var codes = capabilityCodes();

    function draw() {
      var needle = String(search.value || "").trim().toLowerCase();
      clear(list);
      var shown = codes.filter(function (code) {
        return !needle || code.toLowerCase().indexOf(needle) >= 0;
      });
      if (!shown.length) {
        list.appendChild(empty("Ничего не нашли", "Проверьте написание кода права."));
        return;
      }
      shown.forEach(function (code) {
        var has = current.indexOf(code) >= 0;
        var row = el("div", "ui-row");
        var main = el("span", "ui-row__main");
        main.appendChild(el("span", "ui-row__title admin-caps", code));
        row.appendChild(main);
        var button = el("button", "ui-btn ui-btn--sm", has ? "Отозвать" : "Выдать");
        button.type = "button";
        button.onclick = function () { setCapability(person, code, !has); };
        row.appendChild(button);
        list.appendChild(row);
      });
    }

    search.addEventListener("input", draw);
    draw();
    return body;
  }

  function openRights(person) {
    sheet(person.display_name, el("div", "ui-skeleton"), [{ label: "Закрыть" }]);
    loadCard(person).then(function (card) {
      if (!window.SVOI || !window.SVOI.sheet) return;
      sheet(person.display_name, rightsSheet(person, card), [{ label: "Закрыть" }]);
    });
  }

  function refresh() {
    return auth().then(function (denied) {
      if (denied) { status(denied, true); return null; }
      return loadOverview().then(function (data) {
        if (!data) return null;
        people = (BLOCKS.people && BLOCKS.people.data) || [];
        matrix = (BLOCKS.rights && BLOCKS.rights.matrix) || [];
        renderMatrix();
        renderResults();
        return data;
      });
    });
  }

  function loadCard(person) {
    return api("/webapp/admin/person", { participant_id: person.id }).then(function (res) {
      return res.status === 200 && res.body ? res.body.data : null;
    }, function () { return null; });
  }

  $("rights-search").addEventListener("input", function (event) {
    query = event.target.value || "";
    renderResults();
  });
  refresh();
"""

_BODY_RIGHTS = """
<section class="ui-section">
  <div class="ui-section__head"><h2 class="ui-section__title">Роли и возможности</h2></div>
  <div id="matrix" style="display:flex;flex-direction:column;gap:12px"></div>
</section>

<section class="ui-section">
  <div class="ui-section__head"><h2 class="ui-section__title">Выдать право человеку</h2></div>
  <div class="ui-field">
    <label class="ui-field__label" for="rights-search">Имя</label>
    <input class="ui-input" id="rights-search" type="search" inputmode="search"
           autocomplete="off" placeholder="Имя или фамилия">
  </div>
  <div class="ui-small ui-muted" id="rights-hint">Начните вводить имя — покажем совпадения.</div>
  <div class="ui-list" id="rights-results"></div>
</section>

<div class="ui-small ui-muted" id="status" role="status"></div>
"""

RIGHTS_PAGE = screen("Права", _BODY_RIGHTS, _SCRIPT_RIGHTS)
