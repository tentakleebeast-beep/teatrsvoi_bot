"""Скрипт шага адресатов: наборы, галочки, исключения, счётчик до отправки (E8).

Вынесен из `polls_edit_js.py`: модуль страницы не больше 400 строк
(`tests/unit/test_webapp_pages_split.py`). Скрипт конкатенируется в одно замыкание
с вопросами — помощники `$`/`make`/`api` общие, поэтому здесь только функции шага.
"""

from __future__ import annotations

#: Кусок `<script>` конструктора: функции адресатов. Склеивается в `polls_edit_js`.
AUDIENCE_JS = r"""
  /* ------------------------------------------------------------ адресаты */

  function ruleKey(rule) {
    if (rule.kind === "project") { return "project:" + rule.project_id; }
    if (rule.kind === "role") { return "role:" + rule.role; }
    return "all_active";
  }

  function hasRule(key) {
    return state.rules.some(function (rule) { return ruleKey(rule) === key; });
  }

  function toggleRule(key) {
    if (hasRule(key)) {
      state.rules = state.rules.filter(function (rule) { return ruleKey(rule) !== key; });
    } else if (key === "all_active") {
      state.rules.push({ kind: "all_active", role: null, project_id: null });
    } else if (key.indexOf("role:") === 0) {
      state.rules.push({ kind: "role", role: key.slice(5), project_id: null });
    } else if (key.indexOf("project:") === 0) {
      state.rules.push({ kind: "project", role: null,
                         project_id: Number(key.slice(8)) });
    }
    drawRules();
    countAudience();
  }

  function drawRules() {
    var chips = $("rule-chips").children;
    for (var i = 0; i < chips.length; i += 1) {
      chips[i].className = hasRule(chips[i].getAttribute("data-rule"))
        ? "ui-chip ui-chip--on" : "ui-chip";
    }
    $("rule-project").value = "";
    state.projects.forEach(function (project) {
      if (hasRule("project:" + project.id)) { $("rule-project").value = String(project.id); }
    });
  }

  function drawPeople() {
    var host = $("people");
    host.textContent = "";
    var limit = state.shown;
    var shown = state.people.slice(0, limit);
    shown.forEach(function (person) {
      var row = make("label", "ui-row ui-row--dense");
      var main = make("span", "ui-row__main");
      main.appendChild(make("span", "ui-row__title", person.display_name));
      var meta = (person.roles || []).join(", ");
      if (!person.has_chat) { meta += " · без Telegram"; }
      main.appendChild(make("span", "ui-row__meta", meta));
      row.appendChild(main);
      var box = document.createElement("input");
      box.type = "checkbox";
      box.checked = state.include.indexOf(person.id) >= 0;
      box.addEventListener("change", function () {
        togglePerson(person.id, box.checked);
      });
      row.appendChild(box);
      host.appendChild(row);
    });
    $("people-all").hidden = state.people.length <= limit;
    $("people-all").textContent = "в списке " + Math.min(limit, state.people.length) +
      " из " + state.people.length + ", показать всех";
  }

  function togglePerson(id, on) {
    state.include = state.include.filter(function (value) { return value !== id; });
    if (on) { state.include.push(id); }
    countAudience();
  }

  function drawSelects() {
    var exclude = $("exclude");
    var viewers = $("viewers");
    exclude.textContent = "";
    viewers.textContent = "";
    state.people.forEach(function (person) {
      var option = document.createElement("option");
      option.value = String(person.id);
      option.textContent = person.display_name;
      exclude.appendChild(option);
      var viewer = option.cloneNode(true);
      viewer.selected = state.viewers.people.indexOf(person.id) >= 0;
      viewers.appendChild(viewer);
    });
    ["org", "master", "troupe"].forEach(function (role) {
      var option = document.createElement("option");
      option.value = "r:" + role;
      option.textContent = "роль: " + role;
      option.selected = state.viewers.roles.indexOf(role) >= 0;
      viewers.appendChild(option);
      var out = option.cloneNode(true);
      out.textContent = "роль: " + role;
      exclude.appendChild(out);
    });
    state.exclude.forEach(function (id) {
      Array.prototype.forEach.call(exclude.options, function (option) {
        if (option.value === String(id)) { option.selected = true; }
      });
    });
  }

  function readSelections() {
    state.exclude = [];
    Array.prototype.forEach.call($("exclude").options, function (option) {
      if (option.selected && option.value.indexOf("r:") !== 0) {
        state.exclude.push(Number(option.value));
      }
    });
    state.viewers = { people: [], roles: [] };
    Array.prototype.forEach.call($("viewers").options, function (option) {
      if (!option.selected) { return; }
      if (option.value.indexOf("r:") === 0) { state.viewers.roles.push(option.value.slice(2)); }
      else { state.viewers.people.push(Number(option.value)); }
    });
  }

  function audiencePayload() {
    readSelections();
    return { poll_id: state.poll_id, rules: state.rules, include: state.include,
             exclude: state.exclude, viewers: state.viewers };
  }

  async function countAudience() {
    if (!state.poll_id) { return; }
    var result = await api("/webapp/polls/audience", audiencePayload());
    if (result.status === 409) {
      state.frozen = true;
      $("audience-count").textContent = "Опрос отправлен: состав адресатов заморожен.";
      $("send").disabled = true;
      return;
    }
    if (result.status === 403) { showDenied(); return; }
    if (result.status !== 200) {
      $("audience-count").textContent = detailOf(result);
      return;
    }
    var body = result.body;
    var text = "Уйдёт " + body.will_send + " из " + body.total + " в наборе.";
    if (body.without_telegram) {
      text += " Не получат: " + body.without_telegram + " без Telegram.";
    }
    $("audience-count").textContent = text;
    var button = $("send");
    button.disabled = body.will_send === 0;
    button.textContent = "Отправить " + body.will_send + " " +
      plural(body.will_send, "человеку", "людям", "людям");
  }

  async function loadAudience() {
    var result = await api("/webapp/polls/audience/data?poll_id=" + state.poll_id);
    if (result.status === 403) { showDenied(); return; }
    if (result.status !== 200) { showError(detailOf(result)); return; }
    var body = result.body;
    state.rules = body.rules || [];
    state.include = body.include || [];
    state.exclude = body.exclude || [];
    state.viewers = body.viewers || { people: [], roles: [] };
    state.people = body.people || [];
    state.projects = body.projects || [];
    state.frozen = !!body.frozen;
    var project = $("rule-project");
    project.textContent = "";
    var none = document.createElement("option");
    none.value = "";
    none.textContent = "не выбран";
    project.appendChild(none);
    state.projects.forEach(function (item) {
      var option = document.createElement("option");
      option.value = String(item.id);
      option.textContent = item.name;
      project.appendChild(option);
    });
    drawRules();
    drawPeople();
    drawSelects();
    $("send").disabled = state.frozen;
    if (state.frozen) {
      $("audience-count").textContent = "Опрос отправлен: состав адресатов заморожен.";
    } else {
      countAudience();
    }
  }
"""
