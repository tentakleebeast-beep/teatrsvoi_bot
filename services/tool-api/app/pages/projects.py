"""Проекты: список и карточка (E6-2, docs/development/196 §6).

Два экрана одного раздела. Список отвечает на «какие проекты есть и что
ближайшее», карточка — на «сколько план и факт, что за события, кто занят».
Числа не хранятся: их считает `/webapp/projects/data` и `/webapp/project/data`.

Права на данные — в эндпоинтах (`budget.read`, `budget.write`); страница лишь
не рисует кнопок, которых человек всё равно не получит. Формы проекта и плана
живут на своих экранах отдельными секциями: третьего маршрута не заводим.
"""

from __future__ import annotations

from app.pages import layout
from app.pages.misc_common import JS_COMMON as _JS_COMMON

__all__ = ["PROJECTS_PAGE", "PROJECT_CARD_PAGE"]

_SHARED_JS = r"""
  var $ = function (id) { return document.getElementById(id); };
  tgBoot();

  function fillOption(select, value, text) {
    var option = make("option", "", text);
    option.value = value;
    select.appendChild(option);
  }

  function showError(node, result) {
    var message = (result.body && (result.body.detail || result.body.code)) || "Не удалось";
    node.textContent = String(message);
    node.hidden = false;
  }
"""

# --- список -----------------------------------------------------------------

_BODY_LIST = """
<div class="ui-field">
  <input class="ui-input" id="q" type="search" placeholder="Поиск по названию"
         autocomplete="off" maxlength="100">
</div>

<div class="ui-segmented" id="kind-seg" role="group" aria-label="Тип проекта">
  <button type="button" class="ui-segmented__item ui-segmented__item--on"
          data-kind="all" aria-pressed="true">Все</button>
  <button type="button" class="ui-segmented__item" data-kind="show">Спектакли</button>
  <button type="button" class="ui-segmented__item" data-kind="event">Мероприятия</button>
  <button type="button" class="ui-segmented__item" data-kind="rental">Арендаторы</button>
</div>

<label class="ui-row ui-row--switch" id="archive-row">
  <span class="ui-row__main"><span class="ui-row__title">Показать архив</span></span>
  <input type="checkbox" id="archive-toggle" class="ui-switch">
</label>

<div class="ui-list" id="list"></div>
<div class="ui-empty" id="empty" hidden>
  <div class="ui-empty__title">Проектов пока нет</div>
</div>
<div class="ui-alert" id="error" hidden></div>

<div class="ui-actions ui-actions--bottom">
  <button type="button" class="ui-btn ui-btn--primary ui-btn--block" id="add-open"
          data-svoi-cap="budget.write">+ Проект</button>
</div>

<div class="ui-sheet" id="form-sheet" hidden>
  <div class="ui-sheet__backdrop" data-close="1"></div>
  <div class="ui-sheet__panel">
    <h2 class="ui-sheet__title">Новый проект</h2>
    <label class="ui-field"><span class="ui-field__label">Название</span>
      <input class="ui-input" id="f-name" maxlength="120"></label>
    <label class="ui-field"><span class="ui-field__label">Тип</span>
      <select class="ui-input" id="f-kind"></select></label>
    <label class="ui-field"><span class="ui-field__label">Описание</span>
      <input class="ui-input" id="f-description" maxlength="500"></label>
    <label class="ui-field"><span class="ui-field__label">Контрагент</span>
      <input class="ui-input" id="f-counterparty" maxlength="200"></label>
    <div class="ui-actions">
      <button type="button" class="ui-btn ui-btn--primary ui-btn--block"
              id="f-save">Сохранить</button>
    </div>
    <div class="ui-alert" id="f-error" hidden></div>
  </div>
</div>
"""

_SCRIPT_LIST = "<script>\n(function () {\n  \"use strict\";\n" + _JS_COMMON + _SHARED_JS + r"""
  var kind = "all";
  var archived = false;

  var KINDS = [["show", "Спектакль"], ["event", "Мероприятие"], ["rental", "Арендатор"]];
  KINDS.forEach(function (pair) { fillOption($("f-kind"), pair[0], pair[1]); });

  function nearestText(item) {
    if (!item.nearest) { return ""; }
    return "ближайшее: " + item.nearest.weekday + " " + shortDate(item.nearest.date)
      + ", " + item.nearest.time_from + " · " + item.nearest.kind_title.toLowerCase();
  }

  function moneyText(value) { return value === null ? "—" : money(value); }

  function row(item) {
    var node = make("a", "ui-row");
    node.href = "/webapp/project?id=" + item.id;
    var main = make("span", "ui-row__main");
    main.appendChild(make("span", "ui-row__title", item.name));
    main.appendChild(make("span", "ui-row__meta",
      item.kind_title.toLowerCase() + " · " + item.status_title));
    if (item.nearest) { main.appendChild(make("span", "ui-row__meta", nearestText(item))); }
    main.appendChild(make("span", "ui-row__meta",
      "план " + moneyText(item.plan) + " · факт " + moneyText(item.fact)));
    node.appendChild(main);
    return node;
  }

  function load() {
    var query = "?kind=" + encodeURIComponent(kind)
      + "&status=" + (archived ? "archive" : "active")
      + "&q=" + encodeURIComponent($("q").value || "");
    api("/webapp/projects/data" + query).then(function (result) {
      var list = $("list");
      list.textContent = "";
      if (result.status !== 200 || !result.body.ok) { showError($("error"), result); return; }
      $("error").hidden = true;
      result.body.items.forEach(function (item) { list.appendChild(row(item)); });
      $("empty").hidden = result.body.count !== 0;
    });
  }

  $("kind-seg").addEventListener("click", function (event) {
    var button = event.target.closest("[data-kind]");
    if (!button) { return; }
    kind = button.getAttribute("data-kind");
    Array.prototype.forEach.call($("kind-seg").children, function (node) {
      var on = node === button;
      node.classList.toggle("ui-segmented__item--on", on);
      node.setAttribute("aria-pressed", on ? "true" : "false");
    });
    load();
  });

  $("archive-toggle").addEventListener("change", function () {
    archived = $("archive-toggle").checked;
    load();
  });

  var timer = null;
  $("q").addEventListener("input", function () {
    if (timer) { clearTimeout(timer); }
    timer = setTimeout(load, 250);
  });

  $("add-open").addEventListener("click", function () { $("form-sheet").hidden = false; });
  $("form-sheet").addEventListener("click", function (event) {
    if (event.target.getAttribute("data-close")) { $("form-sheet").hidden = true; }
  });
  $("f-save").addEventListener("click", function () {
    api("/webapp/project/save", {
      id: null, name: $("f-name").value, kind: $("f-kind").value,
      status: "active", description: $("f-description").value || null,
      counterparty: $("f-counterparty").value || null
    }).then(function (result) {
      if (result.status !== 200 || !result.body.ok) { showError($("f-error"), result); return; }
      $("form-sheet").hidden = true;
      load();
    });
  });

  load();
})();
</script>"""

PROJECTS_PAGE = layout.page("Проекты", _BODY_LIST, back="/webapp/more",
                            scripts=_SCRIPT_LIST)

# --- карточка ---------------------------------------------------------------

_BODY_CARD = """
<div id="card" hidden>
  <div class="ui-card">
    <span class="ui-card__label" id="c-title">Проект</span>
    <span class="ui-small ui-muted" id="c-meta"></span>
    <span class="ui-small ui-muted" id="c-lead" hidden></span>
  </div>

  <h2 class="ui-section__title">Время</h2>
  <div class="ui-list" id="c-events"></div>
  <p class="ui-small ui-muted" id="c-events-empty" hidden>Событий пока нет</p>
  <a class="ui-btn ui-btn--ghost ui-btn--block" id="c-week" href="/webapp/week">Все события</a>

  <h2 class="ui-section__title">Деньги</h2>
  <div class="ui-card">
    <span class="ui-small" id="c-money"></span>
    <span class="ui-small ui-muted" id="c-more-money"></span>
    <span class="ui-small ui-muted" id="c-unlinked" hidden></span>
  </div>

  <h2 class="ui-section__title">Люди</h2>
  <div class="ui-list" id="c-people"></div>
  <p class="ui-small ui-muted" id="c-people-empty" hidden>Состав не заполнен</p>

  <div class="ui-actions">
    <button type="button" class="ui-btn ui-btn--ghost" id="c-edit"
            data-svoi-cap="budget.write">Изменить</button>
    <button type="button" class="ui-btn ui-btn--ghost" id="c-plan"
            data-svoi-cap="budget.write">Добавить план</button>
  </div>
</div>

<div class="ui-alert" id="error" hidden></div>

<div class="ui-sheet" id="form-sheet" hidden>
  <div class="ui-sheet__backdrop" data-close="1"></div>
  <div class="ui-sheet__panel">
    <h2 class="ui-sheet__title">Проект</h2>
    <label class="ui-field"><span class="ui-field__label">Название</span>
      <input class="ui-input" id="f-name" maxlength="120"></label>
    <label class="ui-field"><span class="ui-field__label">Тип</span>
      <select class="ui-input" id="f-kind"></select></label>
    <label class="ui-field"><span class="ui-field__label">Статус</span>
      <select class="ui-input" id="f-status">
        <option value="active">активен</option>
        <option value="planned">запланирован</option>
        <option value="archive">в архиве</option>
      </select></label>
    <label class="ui-field"><span class="ui-field__label">Описание</span>
      <input class="ui-input" id="f-description" maxlength="500"></label>
    <label class="ui-field"><span class="ui-field__label">Контрагент</span>
      <input class="ui-input" id="f-counterparty" maxlength="200"></label>
    <div class="ui-actions">
      <button type="button" class="ui-btn ui-btn--primary ui-btn--block" id="f-save">
        Сохранить</button>
    </div>
    <div class="ui-alert" id="f-error" hidden></div>
  </div>
</div>

<div class="ui-sheet" id="plan-sheet" hidden>
  <div class="ui-sheet__backdrop" data-close="1"></div>
  <div class="ui-sheet__panel">
    <h2 class="ui-sheet__title">План проекта</h2>
    <label class="ui-field"><span class="ui-field__label">Месяц</span>
      <input class="ui-input" id="p-period" placeholder="2026-11" maxlength="7"></label>
    <label class="ui-field"><span class="ui-field__label">Статья</span>
      <input class="ui-input" id="p-category" value="Гонорары и доли" maxlength="120"></label>
    <label class="ui-field"><span class="ui-field__label">Сумма плана</span>
      <input class="ui-input" id="p-amount" inputmode="numeric"></label>
    <div class="ui-actions">
      <button type="button" class="ui-btn ui-btn--primary ui-btn--block" id="p-save">
        Сохранить план</button>
    </div>
    <div class="ui-alert" id="p-error" hidden></div>
  </div>
</div>
"""

_SCRIPT_CARD = "<script>\n(function () {\n  \"use strict\";\n" + _JS_COMMON + _SHARED_JS + r"""
  var projectId = Number(new URLSearchParams(window.location.search).get("id")) || null;
  var current = null;

  var KINDS = [["show", "Спектакль"], ["event", "Мероприятие"], ["rental", "Арендатор"]];
  KINDS.forEach(function (pair) { fillOption($("f-kind"), pair[0], pair[1]); });

  function renderMoney(moneyBlock) {
    // Пустое — фразой, а не пустой подписью: «не задан» и «ноль» разные ответы,
    // поэтому строки не исчезают (196 §6.2: «План не задан» / «Операций нет»).
    var parts = [moneyBlock.plan === null ? "План не задан" : "план " + money(moneyBlock.plan),
                 moneyBlock.fact === null ? "Операций нет" : "факт " + money(moneyBlock.fact)];
    if (moneyBlock.plan !== null && moneyBlock.rest !== null) {
      parts.push("остаток " + money(moneyBlock.rest));
    }
    $("c-money").textContent = parts.join(" · ");
    $("c-more-money").textContent = "расходы " + money(moneyBlock.expenses)
      + " · доходы " + money(moneyBlock.income);
    var unlinked = moneyBlock.unlinked_income;
    $("c-unlinked").hidden = !(unlinked && unlinked > 0);
    $("c-unlinked").textContent = "доход вне событий " + money(unlinked || 0);
  }

  function render(data) {
    current = data.project;
    $("card").hidden = false;
    $("c-title").textContent = current.name;
    var meta = [current.kind_title.toLowerCase(), current.status_title];
    if (current.starts_on || current.ends_on) {
      meta.push((current.starts_on || "…") + " — " + (current.ends_on || "…"));
    }
    $("c-meta").textContent = meta.join(" · ");
    $("c-lead").hidden = !current.lead;
    $("c-lead").textContent = current.lead ? "Ведущий: " + current.lead.name : "";

    renderMoney(data.money);

    var events = $("c-events");
    events.textContent = "";
    data.events.items.forEach(function (item) {
      var row = make("div", "ui-row");
      var main = make("span", "ui-row__main");
      main.appendChild(make("span", "ui-row__title",
        item.weekday + " " + shortDate(item.date) + ", " + item.time_from
        + " · " + item.kind_title));
      if (item.location) { main.appendChild(make("span", "ui-row__meta", item.location)); }
      row.appendChild(main);
      events.appendChild(row);
    });
    $("c-events-empty").hidden = data.events.items.length !== 0;

    var people = $("c-people");
    people.textContent = "";
    data.people.roles.forEach(function (role) {
      var row = make("div", "ui-row");
      var main = make("span", "ui-row__main");
      main.appendChild(make("span", "ui-row__title", role.alias + " — " + role.name));
      main.appendChild(make("span", "ui-row__meta", "роль"));
      row.appendChild(main);
      people.appendChild(row);
    });
    if (data.people.participants_count > 0) {
      people.appendChild(make("p", "ui-small ui-muted",
        "ещё " + data.people.participants_count + " участников событий"));
    }
    $("c-people-empty").hidden = data.people.roles.length !== 0
      || data.people.participants_count !== 0;

    $("c-edit").hidden = !data.rights.can_edit;
    $("c-plan").hidden = !data.rights.can_plan;
  }

  function load() {
    if (!projectId) { showError($("error"), { body: { detail: "Проект не выбран" } }); return; }
    api("/webapp/project/data?id=" + projectId).then(function (result) {
      if (result.status !== 200 || !result.body.ok) { showError($("error"), result); return; }
      render(result.body);
    });
  }

  $("c-edit").addEventListener("click", function () {
    $("f-name").value = current.name;
    $("f-kind").value = current.kind;
    $("f-status").value = current.status;
    $("f-description").value = current.description || "";
    $("f-counterparty").value = current.counterparty || "";
    $("form-sheet").hidden = false;
  });
  $("c-plan").addEventListener("click", function () { $("plan-sheet").hidden = false; });

  Array.prototype.forEach.call(document.querySelectorAll("[data-close]"), function (node) {
    node.addEventListener("click", function () {
      node.parentElement.hidden = true;
    });
  });

  $("f-save").addEventListener("click", function () {
    api("/webapp/project/save", {
      id: projectId, name: $("f-name").value, kind: $("f-kind").value,
      status: $("f-status").value, description: $("f-description").value || null,
      counterparty: $("f-counterparty").value || null
    }).then(function (result) {
      if (result.status !== 200 || !result.body.ok) { showError($("f-error"), result); return; }
      $("form-sheet").hidden = true;
      load();
    });
  });

  $("p-save").addEventListener("click", function () {
    var amount = Number(String($("p-amount").value).replace(/\s/g, "").replace(",", "."));
    api("/webapp/project/plan", {
      project_id: projectId, period: $("p-period").value,
      block: "expense", category: $("p-category").value, amount: amount
    }).then(function (result) {
      if (result.status !== 200 || !result.body.ok) { showError($("p-error"), result); return; }
      $("plan-sheet").hidden = true;
      load();
    });
  });

  load();
})();
</script>"""

PROJECT_CARD_PAGE = layout.page("Проект", _BODY_CARD, back="/webapp/projects",
                                scripts=_SCRIPT_CARD)
