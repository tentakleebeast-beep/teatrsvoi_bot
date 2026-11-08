"""Экран «Поиск окон». «Поиск окон»: даты, длительность, участники, результат карточками.

Вложенный экран с шапкой и «‹»; возврат ведёт в `/webapp/week` (`window.SVOI_BACK`),
а не всегда на «Главную» (docs/86 §6). Общий JS — `misc_common.py`.
"""

from __future__ import annotations

from app.pages import layout
from app.pages.misc_common import JS_COMMON as _JS_COMMON
from app.pages.misc_slots_denied import DENIED_BODY as _DENIED_BODY
from app.pages.misc_slots_denied import DENIED_SCRIPT as _DENIED_SCRIPT

_BODY_SLOTS = _DENIED_BODY + """
<div id="search-form">
<section class="ui-section">
  <div class="ui-section__head"><h2 class="ui-section__title">Что ищем</h2></div>
  <div class="ui-chips" id="kinds">
    <button class="ui-chip ui-chip--on" type="button" data-kind="rehearsal">Репетиция</button>
    <button class="ui-chip" type="button" data-kind="training">Тренинг</button>
    <button class="ui-chip" type="button" data-kind="rental">Аренда зала</button>
  </div>
</section>

<section class="ui-section" id="discipline-section" hidden>
  <div class="ui-section__head"><h2 class="ui-section__title">Дисциплина</h2></div>
  <div class="ui-chips" id="disciplines"></div>
</section>

<section class="ui-section">
  <div class="ui-section__head"><h2 class="ui-section__title">Когда</h2></div>
  <div class="ui-field">
    <span class="ui-field__label">С</span>
    <label class="ui-datefield">
      <span class="ui-datefield__value" id="from-value">—</span>
      <span class="ui-datefield__hint">изменить</span>
      <input type="date" id="from" aria-label="С какого дня искать">
    </label>
  </div>
  <div class="ui-field">
    <span class="ui-field__label">По</span>
    <label class="ui-datefield">
      <span class="ui-datefield__value" id="to-value">—</span>
      <span class="ui-datefield__hint">изменить</span>
      <input type="date" id="to" aria-label="По какой день искать">
    </label>
  </div>
</section>

<section class="ui-section">
  <div class="ui-section__head"><h2 class="ui-section__title">Сколько длится</h2></div>
  <div class="ui-chips" id="hours">
    <button class="ui-chip" type="button" data-hours="1">1 час</button>
    <button class="ui-chip ui-chip--on" type="button" data-hours="2">2 часа</button>
    <button class="ui-chip" type="button" data-hours="3">3 часа</button>
  </div>
</section>

<section class="ui-section" id="people-section">
  <div class="ui-section__head">
    <h2 class="ui-section__title">Кто нужен</h2>
    <span class="ui-section__meta" id="picked"></span>
  </div>
  <div class="ui-list" id="people"></div>
  <p class="ui-small ui-muted" id="people-hint">Нажатие: обязателен → желателен → не нужен.</p>
</section>

<button class="ui-btn ui-btn--primary ui-btn--block" id="find" type="button">Найти окна</button>
<div class="ui-alert" id="error" hidden></div>

<section class="ui-section" id="result-section" hidden>
  <div class="ui-section__head"><h2 class="ui-section__title">Окна</h2></div>
  <div class="ui-section" id="windows"></div>
</section>
</div>
"""

_SCRIPT_SLOTS = "<script>\n(function () {\n  \"use strict\";\n  window.SVOI_BACK = \"/webapp/week\";\n" + _JS_COMMON + _DENIED_SCRIPT + r"""
  var $ = function (id) { return document.getElementById(id); };
  var state = {};
  var people = [];
  var hours = 2;
  var found = [];
  var kind = "rehearsal";
  // Дисциплины мастера из `/webapp/slots/people`: у администратора и организатора
  // список пуст, тогда выбор не рисуется (140 §4.2).
  var myDisciplines = [];
  var discipline = "";
  tgBoot();

  /* Две области одного действия (С6, docs/architecture/40 §4): у событий студии
     окно строится по людям, у аренды — по залу. Людей у арендатора нет, поэтому
     список «Кто нужен» прячется, а не пугает пустотой. */
  function isRental() { return kind === "rental"; }

  function drawKind() {
    Array.prototype.forEach.call(document.querySelectorAll("#kinds .ui-chip"),
      function (chip) {
        chip.classList.toggle("ui-chip--on", chip.getAttribute("data-kind") === kind);
      });
    $("people-section").hidden = isRental();
    $("people-hint").textContent = isRental()
      ? "Аренда считается по залу: занятость труппы не важна."
      : "Нажатие: обязателен → желателен → не нужен.";
    drawDiscipline();
  }

  /* Дисциплина тренинга: без неё состав подтягивал бы всех педагогов и мастер
     назначал бы чужой предмет. Одна дисциплина — выбрана заранее. */
  function drawDiscipline() {
    var section = $("discipline-section");
    section.hidden = !(kind === "training" && myDisciplines.length);
    if (section.hidden) { return; }
    var host = $("disciplines");
    host.textContent = "";
    myDisciplines.forEach(function (item) {
      var chip = make("button", "ui-chip" + (item.code === discipline ? " ui-chip--on" : ""),
                      item.name);
      chip.type = "button";
      chip.addEventListener("click", function () { discipline = item.code; drawDiscipline(); });
      host.appendChild(chip);
    });
  }

  function showError(text) {
    var box = $("error");
    box.textContent = text || "";
    box.hidden = !text;
  }

  function drawDates() {
    $("from-value").textContent = humanDate($("from").value) || "выберите день";
    $("to-value").textContent = humanDate($("to").value) || "выберите день";
  }

  function chosen(kind) {
    return Object.keys(state).filter(function (id) { return state[id] === kind; })
      .map(Number);
  }

  function drawPicked() {
    var must = chosen("must").length;
    var optional = chosen("optional").length;
    var parts = [];
    if (must) { parts.push("обязательных: " + must); }
    if (optional) { parts.push("желательных: " + optional); }
    $("picked").textContent = parts.join(" · ");
  }

  function cycle(id) {
    state[id] = state[id] === "must" ? "optional" : (state[id] === "optional" ? "" : "must");
    drawPeople();
  }

  function drawPeople() {
    var host = $("people");
    host.textContent = "";
    if (!people.length) {
      var empty = make("div", "ui-empty");
      empty.appendChild(make("div", "ui-empty__title", "Люди не загрузились"));
      empty.appendChild(make("div", "ui-empty__text",
        "Потяните экран вниз, чтобы попробовать снова."));
      host.appendChild(empty);
      return;
    }
    people.forEach(function (person) {
      var mode = state[person.id] || "";
      var row = make("button", "ui-row");
      row.type = "button";
      row.setAttribute("aria-pressed", mode ? "true" : "false");
      if (mode) { row.classList.add("ui-row--attention"); }
      var main = make("span", "ui-row__main");
      main.appendChild(make("span", "ui-row__title", person.display_name || "Без имени"));
      var note = mode === "must" ? "обязателен" : (mode === "optional" ? "желателен" : "не выбран");
      main.appendChild(make("span", "ui-row__meta", note));
      row.appendChild(main);
      row.addEventListener("click", function () { cycle(person.id); });
      host.appendChild(row);
    });
    drawPicked();
  }

  function windowCard(item, index) {
    var card = make("article", "ui-card");
    var day = item.day || item.date;
    var from = pad2(Number(item.from_hour != null ? item.from_hour : item.from || 0));
    var to = pad2(Number(item.to_hour != null ? item.to_hour : item.to || 0));
    card.appendChild(make("h3", "ui-h2",
      shortDate(day) + ", " + from + ":00–" + to + ":00"));

    if (isRental()) {
      /* Аренда: решает зал. Свои люди (администратор, сопровождение) — только
         если их отметили: тогда сервер считает аренду с персоналом (docs/architecture/42 §10.3). */
      var hall = "зал свободен";
      var present = item.present || [];
      var absent = item.absent || [];
      if (present.length) { hall += ", свои свободны: " + present.length; }
      if (absent.length) {
        hall += ", не смогут: " + absent.map(function (id) {
          var person = people.filter(function (item2) { return item2.id === Number(id); })[0];
          return person ? person.display_name : String(id);
        }).join(", ");
      }
      card.appendChild(make("span", "ui-text", hall));
    } else {
      var total = people.length;
      var unknown = item.unknown || [];
      var free = Math.max(0, total - unknown.length);
      card.appendChild(make("span", "ui-text", "свободны " + free + " из " + total));

      if (unknown.length) {
        var names = unknown.map(function (id) {
          var person = people.filter(function (item2) { return item2.id === Number(id); })[0];
          return person ? person.display_name : String(id);
        });
        card.appendChild(make("span", "ui-small ui-muted",
          "не отметились: " + names.join(", ")));
      }
    }

    /* Назначение — событие студии (С1). Для арендатора записи нет: зал сдаётся
       договором, а не репетицией в календаре. */
    if (!isRental()) {
      var assign = make("button", "ui-btn ui-btn--sm", "Назначить");
      assign.type = "button";
      assign.addEventListener("click", function () { confirmAssign(item, index); });
      card.appendChild(assign);
    }
    return card;
  }

  function confirmAssign(item, index) {
    var day = item.day || item.date;
    var from = Number(item.from_hour != null ? item.from_hour : item.from || 0);
    var to = Number(item.to_hour != null ? item.to_hour : item.to || 0);
    var label = shortDate(day) + ", " + pad2(from) + ":00–" + pad2(to) + ":00";
    SVOI.sheet({
      title: "Назначить на " + label + "?",
      body: '<p class="ui-text">Появится репетиция в календаре, участники получат '
        + 'уведомление. Занятость и зал проверятся ещё раз.</p>',
      actions: [
        { label: "Отмена" },
        { label: "Назначить", primary: true,
          onTap: function () { assign(item, index, label); } },
      ],
    });
  }

  async function assign(item, index, label) {
    var day = item.day || item.date;
    var participants = chosen("must").concat(chosen("optional"));
    var preview = await api("/webapp/preview", {
      kind: kind, date: day,
      from_hour: Number(item.from_hour != null ? item.from_hour : item.from || 0),
      to_hour: Number(item.to_hour != null ? item.to_hour : item.to || 0),
      participants: participants,
      // Тренинг несёт дисциплину мастера: без неё сервер добавлял бы всех педагогов
      // и не мог проверить «вы ведёте этот предмет» (140 §4.3).
      discipline: kind === "training" && discipline ? discipline : null,
      persist: true,
    });
    if (!preview.body.ok || !preview.body.proposal_id) {
      var reason = (preview.body.errors || [])[0];
      SVOI.toast(reason && reason.text ? reason.text : "Не назначилось. Попробуйте ещё раз.");
      return;
    }
    var result = await api("/webapp/assign", { proposal_id: preview.body.proposal_id });
    if (result.body.ok) {
      SVOI.toast("Назначено: " + label);
      found.splice(index, 1);
      drawWindows(found);
      return;
    }
    SVOI.toast("Не назначилось. Попробуйте ещё раз.");
  }

  function drawWindows(items, reason) {
    var host = $("windows");
    host.textContent = "";
    if (!items.length) {
      var empty = make("div", "ui-empty");
      if (reason === "venue_unknown") {
        empty.appendChild(make("div", "ui-empty__title", "Занятость зала неизвестна"));
        empty.appendChild(make("div", "ui-empty__text",
          "Календарь не отдал ни одного события за эти дни. Окна не предлагаю: "
          + "свободное время зала нужно сверить вручную."));
      } else {
        empty.appendChild(make("div", "ui-empty__title", "Окон не нашлось"));
        empty.appendChild(make("div", "ui-empty__text",
          "Попробуйте длительность меньше или даты шире."));
      }
      host.appendChild(empty);
      return;
    }
    items.forEach(function (item, index) { host.appendChild(windowCard(item, index)); });
  }

  document.getElementById("hours").addEventListener("click", function (event) {
    var chip = event.target.closest(".ui-chip");
    if (!chip) { return; }
    hours = Number(chip.getAttribute("data-hours"));
    Array.prototype.forEach.call(document.querySelectorAll("#hours .ui-chip"),
      function (item) { item.classList.toggle("ui-chip--on", item === chip); });
  });

  document.getElementById("kinds").addEventListener("click", function (event) {
    var chip = event.target.closest(".ui-chip");
    if (!chip) { return; }
    kind = chip.getAttribute("data-kind");
    drawKind();
  });

  $("from").addEventListener("change", drawDates);
  $("to").addEventListener("change", drawDates);

  $("find").addEventListener("click", async function () {
    showError("");
    var participants = chosen("must");
    var optional = chosen("optional");
    if (!isRental() && !participants.length && !optional.length) {
      showError("Отметьте, кто нужен: без людей окна искать нечего.");
      return;
    }
    var button = $("find");
    button.disabled = true;
    button.textContent = "Ищу…";
    var result = await api("/webapp/slots/find", {
      date_from: $("from").value || null,
      date_to: $("to").value || null,
      min_hours: hours,
      participants: participants,
      optional: optional,
      policy: kind,
    });
    button.disabled = false;
    button.textContent = "Найти окна";
    if (!result.body.ok) {
      /* 403 на сам поиск — тот же закрытый `schedule.slots`: говорим о праве,
         а не «проверьте даты» (карта t_f802d135). */
      showError(result.status === 403
        ? deniedText()
        : "Не удалось найти окна. Проверьте даты и повторите.");
      return;
    }
    found = result.body.windows || [];
    $("result-section").hidden = false;
    drawWindows(found, result.body.reason || (result.body.meta || {}).reason);
  });

  /* Вход в экран: из сводки недели приходят даты, из карточки события — тип
     и участники (docs/architecture/40 §5, правило «дом + вход»). */
  function prefill() {
    var params = new URLSearchParams(window.location.search || "");
    var dayFrom = params.get("from");
    var dayTo = params.get("to");
    if (dayFrom) { $("from").value = dayFrom; }
    if (dayTo) { $("to").value = dayTo; }
    var wanted = params.get("kind");
    if (wanted === "rehearsal" || wanted === "training" || wanted === "rental") {
      kind = wanted;
    }
    var ids = (params.get("participants") || "").split(",").filter(function (item) {
      return item.trim() !== "";
    });
    ids.forEach(function (id) { state[Number(id)] = "must"; });
  }

  async function boot() {
    var today = new Date();
    var later = new Date();
    later.setDate(later.getDate() + 6);
    $("from").value = isoOf(today);
    $("to").value = isoOf(later);
    prefill();
    drawDates();
    drawKind();

    var result = await api("/webapp/slots/people");
    /* 403 от списка людей — не сбой сети, а закрытое право `schedule.slots`
       (FR-D5, docs/architecture/25 §9.2 С6). Труппа попадает сюда по ссылке или
       закладке, поэтому экран говорит, у кого поиск есть (карта t_f802d135). */
    if (result.status === 403) {
      drawDenied();
      return;
    }
    people = (result.body.people || []).map(function (person) {
      return { id: Number(person.id), display_name: person.display_name };
    });
    myDisciplines = result.body.my_disciplines || [];
    if (myDisciplines.length === 1) { discipline = myDisciplines[0].code; }
    drawDiscipline();
    drawPeople();
  }

  boot();
})();
</script>"""

SLOTS_PAGE = layout.page("Поиск окон", _BODY_SLOTS, back="/webapp/week", scripts=_SCRIPT_SLOTS)
