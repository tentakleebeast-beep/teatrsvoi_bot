"""Секция «Дисциплина» экрана «Поиск окон» (`/webapp/slots`).

Отдельный модуль, а не ещё двадцать строк в `misc_slots.py`: у экрана потолок
400 строк (`tests/unit/test_webapp_pages_split.py`), а разметка и скрипт секции
живут одним куском и подклеиваются к странице.

Зачем блок менялся (V-037, решение владельца 08.11.2026). До правки секция молча
скрывалась, когда у мастера не было ни одной дисциплины (`myDisciplines` пуст):
мастер видел форму без выбора и не понимал, почему выбрать нечего. Теперь сервер
присылает `warnings` (`GET /webapp/slots/people`, `audience="self"`), и экран
показывает предупреждение его словами — состояние из роли экран не выводит (D3).

Помощники `$`, `make` живут в общем замыкании CORE, поэтому скрипт просто
конкатенируется к странице; `myDisciplines`, `warnings`, `kind`, `discipline`
объявлены на странице.
"""

from __future__ import annotations

# Разметка секции: предупреждение — вместо пустой строки чипов, а не рядом с ней.
DISCIPLINE_BODY = """
<section class="ui-section" id="discipline-section" hidden>
  <div class="ui-section__head"><h2 class="ui-section__title">Дисциплина</h2></div>
  <div class="ui-chips" id="disciplines"></div>
  <div class="ui-alert ui-alert--warn" id="discipline-warning" hidden></div>
</section>
"""

# Скрипт секции: чипы — только когда дисциплины есть; иначе текст предупреждения.
DISCIPLINE_SCRIPT = r"""
  /* Дисциплина тренинга: без неё состав подтягивал бы всех педагогов и мастер
     назначал бы чужой предмет. Одна дисциплина — выбрана заранее. Пустой список
     у мастера — предупреждение с сервера, а не молча скрытая секция (V-037). */
  function drawDiscipline() {
    var section = $("discipline-section");
    var warnBox = $("discipline-warning");
    var text = (warnings.length && warnings[0].text) || "";
    section.hidden = !(kind === "training" && (myDisciplines.length || text));
    if (section.hidden) { return; }
    warnBox.textContent = text;
    warnBox.hidden = !text;
    var host = $("disciplines");
    host.textContent = "";
    if (text) { return; }
    myDisciplines.forEach(function (item) {
      var chip = make("button", "ui-chip" + (item.code === discipline ? " ui-chip--on" : ""),
                      item.name);
      chip.type = "button";
      chip.addEventListener("click", function () { discipline = item.code; drawDiscipline(); });
      host.appendChild(chip);
    });
  }
"""
