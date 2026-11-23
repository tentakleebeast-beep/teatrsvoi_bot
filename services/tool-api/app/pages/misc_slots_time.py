"""Личное время участия в форме назначения события (V-134).

Отдельный модуль, а не ещё тридцать строк в `misc_slots.py`: у экрана потолок
400 строк (`tests/unit/test_webapp_pages_split.py`), а скрипт секции живёт одним
куском и подклеивается к странице, как `misc_slots_discipline.py`.

Зачем. Актёры в анкете 23.11.2026 просят «со скольки я буду задействован, во
сколько освобожусь». Поле необязательное: пусто значит весь интервал события,
поэтому форма отправляется и без него. Время считается в часы события — других
суток у репетиции нет.

Помощники `make`, `people`, `chosen` живут в общем замыкании CORE, поэтому
скрипт просто конкатенируется к странице.
"""

from __future__ import annotations

TIME_FIELDS_SCRIPT = r"""
  // Личное время участия (V-134): «с / до» на каждого выбранного, по желанию.
  function windowFields(picked) {
    var host = document.createElement("div");
    host.className = "ui-form";
    if (!picked.length) { return host; }
    host.appendChild(make("span", "ui-field__label",
      "Время участия — по желанию; пусто значит всё событие"));
    picked.forEach(function (person) {
      var row = make("div", "ui-range");
      ["from", "to"].forEach(function (edge) {
        if (edge === "to") { row.appendChild(make("span", "ui-range__sep", "—")); }
        var field = make("div", "ui-field");
        field.appendChild(make("span", "ui-field__label",
          edge === "from" ? "с" : "до"));
        var input = document.createElement("input");
        input.className = "ui-input";
        input.setAttribute("inputmode", "numeric");
        input.setAttribute("maxlength", "5");
        input.setAttribute("placeholder", "ЧЧ:ММ");
        input.setAttribute("aria-label", person.display_name + ": "
          + (edge === "from" ? "с" : "до"));
        input.setAttribute("data-win-" + edge, String(person.id));
        field.appendChild(input);
        row.appendChild(field);
      });
      host.appendChild(row);
    });
    return host;
  }

  // Что уйдёт на сервер: только заполненные поля. Ошибка возвращается словами,
  // а не молчаливой потерей ввода.
  function collectWindows(host, picked) {
    var windows = [];
    for (var i = 0; i < picked.length; i += 1) {
      var id = String(picked[i].id);
      var fromInput = host.querySelector('[data-win-from="' + id + '"]');
      var toInput = host.querySelector('[data-win-to="' + id + '"]');
      var from = fromInput ? fromInput.value.trim() : "";
      var to = toInput ? toInput.value.trim() : "";
      if (!from && !to) { continue; }
      if (!/^\d{1,2}:\d{2}$/.test(from || "") || (to && !/^\d{1,2}:\d{2}$/.test(to))) {
        return { error: "Время участия — ЧЧ:ММ, например 19:30.", windows: [] };
      }
      windows.push({ participant_id: picked[i].id,
                     from_hour: from ? Number(from.split(":")[0]) : null,
                     to_hour: to ? Number(to.split(":")[0]) : null });
    }
    return { error: null, windows: windows };
  }
"""
