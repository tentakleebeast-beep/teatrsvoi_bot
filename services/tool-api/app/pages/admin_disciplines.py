"""Блок «Дисциплины педагога» админской карточки человека (Э1, docs/architecture/140 §4.2-4.3).

Вынесен отдельным модулем, а не дописан в `admin_people.py`: там уже 333 строки, а
порог «модуль страницы — не больше 400 строк» (`tests/unit/test_webapp_pages_split.py`)
иначе нарушится. Скрипт подклеивается к `_SCRIPT_PEOPLE` и живёт в общем замыкании
ядра (`admin_core.py`): помощники `$`, `el`, `api`, `pair`, `empty`, `sheet`, `toast`,
`problem`, `ROLE`, `ROLE_ACC` приходят оттуда, поэтому отдельного каркаса здесь нет.

Два действия, которые различаются намеренно:

* **Дисциплины педагога** (`POST /webapp/admin/discipline`) — предметная связь
  `participant_discipline.relation='teacher'`. Отмеченное — строками, остальное —
  выпадающим списком; каждое действие пишет сразу и **не закрывает лист**, чтобы
  вторую дисциплину не ставили повторным открытием карточки (D2-D3 дизайна V-037).
  Снять последнюю можно: сначала подтверждение в этом же листе, после — состояние
  «нет» с предупреждением от сервера (`warnings`, D2).
* **Выдать роль педагога** (`POST /webapp/admin/grant`) — роль выдаётся и без
  дисциплин: мастер работает сразу, а сервер отвечает предупреждением, которое
  карточка показывает словами (D4). Кнопка поэтому активна всегда.
"""

from __future__ import annotations

DISCIPLINES_SCRIPT = r"""
  var catalogue = null;   // справочник дисциплин студии: приходит один раз на экран

  function disciplineNames(list) {
    return (list || []).map(function (item) { return item.name; }).join(", ");
  }

  function loadCatalogue() {
    if (catalogue) return Promise.resolve(catalogue);
    return api("/webapp/form-data").then(function (res) {
      catalogue = (res.body && res.body.disciplines) || [];
      return catalogue;
    }, function () { return []; });
  }

  /* Состояние листа: отмеченные дисциплины и предупреждение приходят с сервера
     (`disciplines`, `warnings`), второй GET карточки после действия не нужен. */
  function disciplineSheet(person, card) {
    var body = el("div", "admin-sheet");
    body.appendChild(el("p", "ui-small ui-muted",
      "Что человек ведёт. Роль педагога выдаётся отдельной кнопкой в «Ролях»."));
    var warnBox = el("div", "ui-alert ui-alert--warn");
    warnBox.hidden = true;
    body.appendChild(warnBox);
    var list = el("div", "ui-list");
    body.appendChild(list);
    var adder = el("div", "ui-field");
    body.appendChild(adder);
    var state = { disciplines: (card && card.disciplines) || [],
                  warnings: (card && card.warnings) || [] };
    var items = [];

    function nameOf(code) {
      for (var i = 0; i < items.length; i += 1) {
        if (items[i].code === code) { return items[i].name; }
      }
      return code;
    }

    function has(code) {
      return state.disciplines.some(function (own) { return own.code === code; });
    }

    function drawWarning() {
      var text = (state.warnings.length && state.warnings[0].text) || "";
      warnBox.textContent = text;
      warnBox.hidden = !text;
    }

    function drawRows() {
      list.textContent = "";
      if (!state.disciplines.length) { return; }
      state.disciplines.forEach(function (item) {
        var row = el("div", "ui-row");
        var main = el("span", "ui-row__main");
        main.appendChild(el("span", "ui-row__title", item.name));
        main.appendChild(el("span", "ui-row__meta", "ведёт"));
        row.appendChild(main);
        var button = el("button", "ui-btn ui-btn--sm", "Снять");
        button.type = "button";
        button.setAttribute("data-discipline", item.code);
        button.onclick = function () {
          if (state.disciplines.length === 1) { askLast(item); return; }
          setDiscipline(item.code, item.name, false);
        };
        row.appendChild(button);
        list.appendChild(row);
      });
    }

    function drawAdder() {
      adder.textContent = "";
      var rest = items.filter(function (item) { return !has(item.code); });
      if (!rest.length) {
        adder.appendChild(el("p", "ui-small ui-muted", "Все дисциплины отмечены"));
        return;
      }
      var select = el("select", "ui-select");
      select.id = "discipline-select";
      select.setAttribute("aria-label", "Дисциплина");
      var blank = el("option", "", "— выберите —");
      blank.value = "";
      select.appendChild(blank);
      rest.forEach(function (item) {
        var option = el("option", "", item.name);
        option.value = item.code;
        select.appendChild(option);
      });
      adder.appendChild(select);
      var add = el("button", "ui-btn ui-btn--primary ui-btn--block",
                   state.disciplines.length ? "Добавить ещё" : "Добавить дисциплину");
      add.type = "button";
      add.id = "discipline-add";
      add.onclick = function () {
        var code = select.value;
        if (!code) { toast("Выберите дисциплину из списка."); return; }
        setDiscipline(code, nameOf(code), true);
      };
      adder.appendChild(add);
    }

    function draw() {
      drawWarning();
      drawRows();
      drawAdder();
    }

    /* Снятие последней: подтверждение в том же листе — вернуть её мастер сам
       не сможет, дисциплину ставит администратор (D5). */
    function askLast(item) {
      var box = el("div", "ui-alert ui-alert--warn");
      box.id = "discipline-confirm";
      box.appendChild(el("p", "ui-text",
        "У педагога не останется ни одной дисциплины: тренинг он назначить не сможет."));
      var actions = el("div", "ui-actions");
      var cancel = el("button", "ui-btn ui-btn--sm", "Отмена");
      cancel.type = "button";
      cancel.onclick = function () { box.remove(); };
      var confirm = el("button", "ui-btn ui-btn--sm ui-btn--danger", "Снять дисциплину");
      confirm.type = "button";
      confirm.onclick = function () {
        box.remove();
        setDiscipline(item.code, item.name, false);
      };
      actions.appendChild(cancel);
      actions.appendChild(confirm);
      box.appendChild(actions);
      body.insertBefore(box, list);
      confirm.focus();
    }

    function setDiscipline(code, name, enabled) {
      api("/webapp/admin/discipline", {
        participant_id: person.id, discipline: code, enabled: enabled
      }).then(function (res) {
        if (res.status >= 200 && res.status < 300) {
          state.disciplines = (res.body && res.body.disciplines) || [];
          state.warnings = (res.body && res.body.warnings) || [];
          toast((enabled ? "Дисциплина отмечена: " : "Дисциплина снята: ") + name);
          draw();
          return;
        }
        toast(problem(res));
      });
    }

    loadCatalogue().then(function (loaded) {
      items = loaded || [];
      if (!items.length) {
        drawWarning();
        list.appendChild(empty("Дисциплин нет", "Справочник дисциплин пуст."));
        adder.hidden = true;
        return;
      }
      draw();
    });
    drawWarning();
    return body;
  }

  // Выдача роли педагога: выбор дисциплин необязателен — без них роль выдаётся,
  // а сервер вернёт предупреждение, которое покажет карточка (D4).
  function confirmMaster(person, card) {
    var body = el("div", "admin-sheet");
    body.appendChild(el("p", "ui-text",
      "Отметьте дисциплины, которые ведёт " + person.display_name +
      ". Без дисциплины роль выдаётся, но тренинг педагог назначить не сможет."));
    var list = el("div", "ui-list");
    body.appendChild(list);
    var chosen = [];
    var issue = el("button", "ui-btn ui-btn--primary ui-btn--block", "Выдать роль педагога");
    issue.type = "button";
    issue.onclick = function () { changeRole(person, "master", true, chosen); };
    body.appendChild(issue);
    loadCatalogue().then(function (loaded) {
      var items = loaded || [];
      if (!items.length) {
        list.appendChild(empty("Дисциплин нет", "Справочник дисциплин пуст."));
        return;
      }
      items.forEach(function (item) {
        var row = el("button", "ui-row");
        row.type = "button";
        row.setAttribute("aria-pressed", "false");
        var main = el("span", "ui-row__main");
        main.appendChild(el("span", "ui-row__title", item.name));
        main.appendChild(el("span", "ui-row__meta", "не выбрана"));
        row.appendChild(main);
        row.onclick = function () {
          var at = chosen.indexOf(item.code);
          if (at >= 0) { chosen.splice(at, 1); } else { chosen.push(item.code); }
          var on = chosen.indexOf(item.code) >= 0;
          row.setAttribute("aria-pressed", on ? "true" : "false");
          row.classList.toggle("ui-row--attention", on);
          main.lastChild.textContent = on ? "выбрана" : "не выбрана";
        };
        list.appendChild(row);
      });
    });
    sheet("Педагог: " + person.display_name, body, [{ label: "Закрыть" }]);
  }
"""
