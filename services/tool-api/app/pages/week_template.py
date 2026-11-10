"""JS недели, часть вторая: шаблон недели, очистка, навигация и запуск.

Продолжение скрипта из `pages/week_marks.py` (см. пояснение там): очистка
недели с двойным подтверждением, «Запомнить эту неделю», «Заполнить по
шаблону», стрелки недель, вход по правам и стартовая загрузка.
"""

from __future__ import annotations

_SCRIPT_WEEK_TEMPLATE = r"""
  function openClear(snapshot) {
    var count = snapshot.length;
    SVOI.sheet({
      title: "Очистить неделю?",
      body: '<p class="ui-text">' + week.title + ": снимутся все отметки, "
        + count + " " + plural(count, "день", "дня", "дней") + ".</p>",
      actions: [
        { label: "Отмена" },
        { label: "Очистить", danger: true, keepOpen: true,
          onTap: function () { askAgain(snapshot); } },
      ],
    });
  }

  function askAgain(snapshot) {
    SVOI.sheet({
      title: "Точно очистить?",
      body: '<p class="ui-text">Отметки этой недели исчезнут. Вернуть их можно '
        + "кнопкой в сообщении внизу экрана — пока оно на экране.</p>",
      actions: [
        { label: "Отмена" },
        { label: "Очистить неделю", danger: true, keepOpen: true,
          onTap: function () { clearWeek(snapshot); } },
      ],
    });
  }

  async function clearWeek(snapshot) {
    var answer = await api("/webapp/availability/clear", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ week: week.start }),
    });
    if (!answer.body.ok) {
      SVOI.closeSheet();
      SVOI.toast("Не удалось очистить неделю. Проверьте сеть и повторите.");
      return;
    }
    SVOI.closeSheet();
    await load(week.start);
    // cleared — это дни-отметки (Ш-53): столько отметок человек видел на экране,
    // а не число часов в базе. Ноль тоже называем числом, а не «пусто».
    var count = answer.body.cleared || 0;
    var text = "Неделя очищена: снято " + count + " "
      + plural(count, "отметка", "отметки", "отметок");
    if (!count) {
      text = "Неделя уже была пустой: снято 0 отметок.";
    }
    if (snapshot.length) {
      SVOI.toast(text, { action: "Вернуть", onAction: function () { restore(snapshot); } });
    } else {
      SVOI.toast(text);
    }
  }

  async function rememberWeek() {
    if (!week) { return; }
    if (!week.days.some(function (day) { return day.marked; })) {
      SVOI.toast("Отметьте хотя бы один день — из пустой недели шаблон не собрать.");
      return;
    }
    var answer = await api("/webapp/availability/template/from-week", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start: week.start }),
    });
    SVOI.toast(answer.body.ok
      ? "Обычная неделя сохранена: " + answer.body.filled + " " + plural(answer.body.filled, "день", "дня", "дней")
      : "Не удалось запомнить неделю. Проверьте сеть и повторите.");
  }

  async function fillTemplate() {
    var preview = await api("/webapp/availability/template/apply", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start: week.start, apply: false }),
    });
    if (!preview.body.ok) {
      if (preview.body.error === "no_template") {
        SVOI.sheet({
          title: "Обычной недели ещё нет",
          body: '<p class="ui-text">Заполните неделю руками и нажмите «Запомнить эту неделю '
            + "как обычную» — в следующий раз её можно будет подставить одним нажатием.</p>",
          actions: [
            { label: "Отмена" },
            { label: "Запомнить как обычную", primary: true, onTap: rememberWeek },
          ],
        });
        return;
      }
      SVOI.toast("Не удалось прочитать обычную неделю. Проверьте сеть и повторите.");
      return;
    }

    var days = preview.body.days || [];
    var skipped = (preview.body.skipped || []).length;
    var overlay = SVOI.sheet({
      title: "Заполнить по обычной неделе",
      body: '<div class="ui-list" id="tpl-list"></div>'
        + (skipped
          ? '<p class="ui-small ui-muted">' + skipped + " "
            + plural(skipped, "день", "дня", "дней") + " уже отмечено — их не трогаем.</p>"
          : ""),
      actions: [
        { label: "Отмена" },
        { label: "Заполнить", primary: true, keepOpen: true,
          onTap: function () { applyTemplate(); } },
      ],
    });

    var list = overlay.querySelector("#tpl-list");
    days.forEach(function (item) {
      var row = document.createElement("div");
      row.className = "ui-row";
      var main = document.createElement("span");
      main.className = "ui-row__main";
      var title = document.createElement("span");
      title.className = "ui-row__title";
      title.textContent = dayTitle(item);
      main.appendChild(title);
      row.appendChild(main);
      var value = document.createElement("span");
      value.className = "ui-row__value";
      value.textContent = item.text || "";
      row.appendChild(value);
      list.appendChild(row);
    });

    async function applyTemplate() {
      var answer = await api("/webapp/availability/template/apply", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start: week.start, apply: true }),
      });
      if (!answer.body.ok) {
        SVOI.toast("Не удалось заполнить неделю. Проверьте сеть и повторите.");
        return;
      }
      SVOI.closeSheet();
      await load(week.start);
      var filled = (answer.body.days || []).length;
      SVOI.toast(filled === 1
        ? "Заполнен 1 день."
        : "Заполнено " + filled + " " + plural(filled, "день", "дня", "дней") + ".");
    }
  }

  $("prev-week").onclick = function () { load(shiftWeek(start || todayIso(), -1)); };
  $("next-week").onclick = function () { load(shiftWeek(start || todayIso(), 1)); };
  $("save-template").onclick = rememberWeek;
  $("fill-template").onclick = fillTemplate;
  $("clear-week").onclick = function () {
    if (!week) { return; }
    var snapshot = week.days.filter(function (day) { return day.marked; }).map(toPayload);
    if (!snapshot.length) {
      SVOI.toast("На этой неделе нет отметок — очищать нечего.");
      return;
    }
    openClear(snapshot);
  };

  Array.prototype.forEach.call(document.querySelectorAll("[data-day-slot]"), function (row) {
    row.onclick = function () {
      if (!week) { return; }
      openDay(Number(row.getAttribute("data-day-slot")));
    };
  });

  /* «Сводка занятости» закрыта правом `schedule.read.all` (FR-H9): экран недели —
     публичная константа, поэтому «есть ли право» приезжает отдельным запросом
     `/webapp/nav` уже после входа (docs/architecture/25 §9.2: оболочка может
     только не показывать кнопку). Убираем строку лишь по явному отказу сервера:
     без ответа или при сбое она остаётся, иначе сломанный nav отнимал бы сводку
     у организатора. Карта t_70a6d8e5. */
  function applyNav(body) {
    if (!body || body.ok !== true || typeof body.summary !== "boolean") { return; }
    var row = $("to-summary");
    if (row) { row.hidden = !body.summary; }
  }

  async function caps() {
    try {
      var answer = await api("/webapp/nav");
      applyNav(answer.body);
    } catch (e) { /* строка остаётся: отказ должен быть явным */ }
  }

  (async function boot() {
    var denied = await auth();
    if (denied) {
      $("status").textContent = denied;
      return;
    }
    /* Сначала права оболочки: строку сводки убираем до загрузки недели,
       чтобы участник не успел увидеть и нажать её. */
    await caps();
    if (!(await load(start))) {
      $("progress").textContent = "Не удалось загрузить неделю.";
    }
  })();
})();
</script>
"""
