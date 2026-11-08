"""Блок «Дисциплины педагога» админской карточки человека (Э1, docs/architecture/140 §4.2-4.3).

Вынесен отдельным модулем, а не дописан в `admin_people.py`: там уже 333 строки, а
порог «модуль страницы — не больше 400 строк» (`tests/unit/test_webapp_pages_split.py`)
иначе нарушится. Скрипт подклеивается к `_SCRIPT_PEOPLE` и живёт в общем замыкании
ядра (`admin_core.py`): помощники `$`, `el`, `api`, `pair`, `empty`, `sheet`, `toast`,
`problem`, `ROLE`, `ROLE_ACC` приходят оттуда, поэтому отдельного каркаса здесь нет.

Два действия, которые различаются намеренно:

* **Отметить дисциплину** (`POST /webapp/admin/discipline`) — предметная связь
  `participant_discipline.relation='teacher'`. Её можно ставить и снимать сколько угодно;
  последнюю у педагога снять нельзя (`409 master_without_discipline`) — отказ
  показывается словами, а не молчанием.
* **Выдать роль педагога** (`POST /webapp/admin/grant` с `disciplines`) — роль без
  дисциплины не выдаётся (140 §2.2, V-037 вариант а): пока ни одна дисциплина не
  отмечена, запрос не уходит, а экран говорит, чего не хватает.
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

  function setDiscipline(person, code, enabled) {
    api("/webapp/admin/discipline", {
      participant_id: person.id, discipline: code, enabled: enabled
    }).then(function (res) {
      if (res.status >= 200 && res.status < 300) {
        closeSheet();
        toast((enabled ? "Дисциплина отмечена: " : "Дисциплина снята: ") + code);
        refresh();
        return;
      }
      // 409 master_without_discipline приходит кодом: проблема() скажет словами,
      // что последнюю дисциплину у педагога снять нельзя.
      toast(problem(res));
    });
  }

  // Отметки дисциплин у человека: чтение карточки + переключение связи.
  function disciplineSheet(person, card) {
    var body = el("div", "admin-sheet");
    body.appendChild(el("p", "ui-small ui-muted",
      "Что человек ведёт. Роль педагога выдаётся отдельной кнопкой в «Ролях»."));
    var list = el("div", "ui-list");
    body.appendChild(list);
    loadCatalogue().then(function (items) {
      var have = (card && card.disciplines) || [];
      if (!items.length) {
        list.appendChild(empty("Дисциплин нет", "Справочник дисциплин пуст."));
        return;
      }
      items.forEach(function (item) {
        var marked = have.some(function (own) { return own.code === item.code; });
        var row = el("div", "ui-row");
        var main = el("span", "ui-row__main");
        main.appendChild(el("span", "ui-row__title", item.name));
        main.appendChild(el("span", "ui-row__meta", marked ? "ведёт" : "не отмечено"));
        row.appendChild(main);
        var button = el("button", "ui-btn ui-btn--sm", marked ? "Снять" : "Отметить");
        button.type = "button";
        button.onclick = function () { setDiscipline(person, item.code, !marked); };
        row.appendChild(button);
        list.appendChild(row);
      });
    });
    return body;
  }

  // Выдача роли педагога: дисциплина выбирается до запроса, а не после отказа.
  function confirmMaster(person, card) {
    var body = el("div", "admin-sheet");
    body.appendChild(el("p", "ui-text",
      "Отметьте дисциплины, которые ведёт " + person.display_name +
      ". Педагогу нужна хотя бы одна: без неё он не сможет назначить тренинг."));
    var list = el("div", "ui-list");
    body.appendChild(list);
    var chosen = [];
    var issue = el("button", "ui-btn ui-btn--primary ui-btn--block", "Выдать роль педагога");
    issue.type = "button";
    issue.disabled = true;
    issue.onclick = function () {
      if (!chosen.length) {
        toast("Сначала отметьте дисциплину — без неё роль не выдаётся.");
        return;
      }
      changeRole(person, "master", true, chosen);
    };
    body.appendChild(issue);
    loadCatalogue().then(function (items) {
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
          issue.disabled = chosen.length === 0;
        };
        list.appendChild(row);
      });
    });
    sheet("Педагог: " + person.display_name, body, [{ label: "Закрыть" }]);
  }
"""
