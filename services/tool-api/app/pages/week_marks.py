"""JS недели, часть вторая: лист дня, автосохранение и «Вернуть».

Шапка скрипта (вход, подписи, чтение недели) и разметка листа — в
`pages/week_sheet.py`; шаблон, очистка и запуск — в `pages/week_template.py`.
Части склеиваются в один `<script>` в `pages/week.py` (порядок: шапка, этот
модуль, шаблон).

Главное правило: отметки сохраняются сами. Лист дня закрывается — карточка
дня уже записана (`onClose` → `flush`), «Вернуть» живёт 10 секунд и остаётся
единственным откатом (П-4, З1).
"""

from __future__ import annotations

_SCRIPT_WEEK_MARKS = r"""
  function openDay(index) {
    var day = week.days[index];
    var type = "";
    if (day.marked) {
      type = (day.from || day.to) ? "hours" : (day.status === "free" ? "free" : "busy");
    }
    var overlay = SVOI.sheet({
      title: "Занятость: " + humanDate(day.day),
      body: sheetBody(),
      /* Отметки сохраняются сами: лист закрывается — запись уже ушла (П-4).
         Кнопка одна и называется «Готово»: второй «Сохранить» нет, иначе человек
         думает, что без нажатия данные пропадут. */
      actions: [
        { label: "Готово", primary: true },
      ],
      onClose: function () { return flush(day); },
    });

    var state = {
      type: type,
      /* «свободен с 18:00» и «занят до 18:00» — одно и то же с двух сторон,
         поэтому состояние часов спрашиваем отдельно. */
      status: day.marked && day.status === "free" ? "free" : "busy",
    };
    var modes = overlay.querySelector("#day-modes");
    var block = overlay.querySelector("#hours-block");
    var hoursState = overlay.querySelector("#hours-state");
    var from = overlay.querySelector("#day-from");
    var to = overlay.querySelector("#day-to");
    var note = overlay.querySelector("#day-note");
    var error = overlay.querySelector("#day-error");

    from.value = day.from || "";
    to.value = day.to || "";
    note.value = day.note || "";

    /* Действия листа держим на виду: на 320 px «Сохранить» уезжает (85 §4.9). */
    var actions = overlay.querySelector(".ui-sheet__actions");
    if (actions) {
      actions.style.position = "sticky";
      actions.style.bottom = "0";
      actions.style.background = "var(--ui-bg)";
      actions.style.paddingTop = "8px";
    }

    function sync() {
      Array.prototype.forEach.call(modes.querySelectorAll(".ui-radio-row"), function (row) {
        var on = row.getAttribute("data-mode") === state.type;
        row.classList.toggle("ui-radio-row--on", on);
        row.querySelector(".ui-radio-row__mark").textContent = on ? "выбрано" : "";
      });
      Array.prototype.forEach.call(hoursState.querySelectorAll(".ui-segmented__item"), function (item) {
        item.classList.toggle("ui-segmented__item--on", item.getAttribute("data-state") === state.status);
      });
      block.style.display = state.type === "hours" ? "flex" : "none";
    }

    Array.prototype.forEach.call(hoursState.querySelectorAll(".ui-segmented__item"), function (item) {
      item.onclick = function () {
        state.status = item.getAttribute("data-state");
        state.type = "hours";
        error.hidden = true;
        touched();
        sync();
      };
    });

    Array.prototype.forEach.call(modes.querySelectorAll(".ui-radio-row"), function (row) {
      row.onclick = function () {
        state.type = row.getAttribute("data-mode");
        if (state.type === "free" || state.type === "busy") { state.status = state.type; }
        error.hidden = true;
        touched();
        sync();
      };
    });

    Array.prototype.forEach.call(overlay.querySelectorAll(".ui-chip"), function (chip) {
      chip.onclick = function () {
        state.type = "hours";
        from.value = chip.getAttribute("data-from");
        to.value = chip.getAttribute("data-to");
        error.hidden = true;
        touched();
        sync();
      };
    });

    [from, to].forEach(function (input) {
      input.onchange = function () { state.type = "hours"; touched(); sync(); };
      /* Округление до получаса показываем сразу, а не молча при сохранении. */
      input.onblur = function () {
        var value = clock(input.value);
        if (value !== null) { input.value = value; }
      };
    });

    /* Комментарий тоже отметка: без «input» закрытие листа не заметило бы правку. */
    note.addEventListener("input", touched);

    sync();

    function showError(text) {
      error.textContent = text;
      error.hidden = false;
    }

    var before = week.days.filter(function (item) { return item.marked; }).map(toPayload);
    /* «Есть что сохранять»: лист, открытый и закрытый без правок, ничего не пишет. */
    var dirty = false;
    function touched() { dirty = true; }

    /* Что записать по текущему состоянию листа. Возвращает либо карточку дня,
       либо текст ошибки — проверки те же, что были у кнопки «Сохранить». */
    function collect(current) {
      if (!state.type) {
        return { error: "Выберите вариант: свободен, занят или часы." };
      }
      var item = { day: current.day };
      if (state.type === "hours") {
        var low = clock(from.value);
        var high = clock(to.value);
        if (low === null || high === null) {
          return { error: "Время пишется так: 18:00 или 18:30." };
        }
        from.value = low;
        to.value = high;
        if (low && high && low >= high) {
          return { error: "Конец раньше начала. Поменяйте время местами." };
        }
        item.status = state.status;
        item.from_time = low || null;
        item.to_time = high || null;
      } else {
        item.status = state.type;
        item.from_time = null;
        item.to_time = null;
      }
      item.note = (note.value || "").trim() || null;
      return { item: item };
    }

    /* Лист закрывается — запись уходит сама. Возврат false оставляет лист
       открытым: кривое время не должно теряться молча. */
    function flush(current) {
      if (!dirty) { return true; }
      var result = collect(current);
      if (result.error) { showError(result.error); return false; }
      persist(current, result.item);
      return true;
    }

    async function persist(current, item) {
      var days = week.days.filter(function (other) {
        return other.marked && other.day !== current.day;
      }).map(toPayload);
      days.push(item);

      pendingSave = api("/webapp/availability/week", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start: week.start, days: days }),
      });
      var answer = await pendingSave;
      pendingSave = null;
      if (!answer.body.ok) {
        var why = answer.body.detail || answer.body.error || answer.status;
        SVOI.toast("Не удалось сохранить: "
          + (typeof why === "string" ? why : "проверьте время"), { duration: 10000 });
        return;
      }
      dirty = false;
      await load(week.start);
      var fresh = week.days.filter(function (other) { return other.day === current.day; })[0];
      var text = "Сохранено: " + describe(fresh || current);
      /* «Вернуть» живёт 10 секунд — единственный откат автосохранения. */
      SVOI.toast(text, { action: "Вернуть", duration: 10000,
                         onAction: function () { restore(before); } });
    }
  }

  /* «Вернуть»: снять один день отдельным запросом нельзя, поэтому при
     отсутствии дня в снимке неделя очищается и пишется заново. */
  async function restore(days) {
    var keep = {};
    days.forEach(function (item) { keep[item.day] = true; });
    var drop = week.days.some(function (day) { return day.marked && !keep[day.day]; });
    var options = {
      method: "POST", headers: { "Content-Type": "application/json" },
    };
    if (drop) {
      var cleared = await api("/webapp/availability/clear",
        Object.assign({ body: JSON.stringify({ week: week.start }) }, options));
      if (!cleared.body.ok) {
        SVOI.toast("Не удалось вернуть прежние отметки. Проверьте сеть и повторите.");
        return;
      }
    }
    if (days.length) {
      var answer = await api("/webapp/availability/week",
        Object.assign({ body: JSON.stringify({ start: week.start, days: days }) }, options));
      if (!answer.body.ok) {
        SVOI.toast("Не удалось вернуть прежние отметки. Проверьте сеть и повторите.");
        return;
      }
    }
    await load(week.start);
    SVOI.toast("Вернул как было.");
  }
"""
