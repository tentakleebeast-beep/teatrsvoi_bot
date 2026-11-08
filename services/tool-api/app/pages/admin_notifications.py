"""Админский экран «Уведомления». Общие значения: типы событий и тихие часы (FR-G17, О5).

Модуль — только своя разметка и скрипт: общее ядро (токен, запросы, строки, листы)
и каркас лежат в `admin_core.py`. Границы и права — как в `docs/development/73`.

Экран задаёт значения по умолчанию для всей студии. Частные настройки человека
остаются в профиле (`/webapp/notifications`) и важнее общих: смена общего значения
не переписывает то, что человек настроил себе сам (П-8, FR-G17).
"""

from __future__ import annotations

from app.pages.admin_core import screen

_SCRIPT_NOTIFICATION_DEFAULTS = r"""
  var KEYS = ["rehearsals", "dues", "schedule_changes"];
  var CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;

  function row(key) {
    return document.querySelector('.ui-switch-row[data-key="' + key + '"]');
  }

  function setOn(key, value) {
    var button = row(key);
    button.classList.toggle("ui-switch-row--on", Boolean(value));
    button.setAttribute("aria-pressed", Boolean(value) ? "true" : "false");
    button.querySelector(".ui-switch-row__state").textContent =
      value ? "включено" : "выключено";
  }

  function isOn(key) {
    return row(key).classList.contains("ui-switch-row--on");
  }

  function checkClock(field) {
    var value = field.value.trim();
    if (CLOCK.test(value)) { return true; }
    var error = $("quiet_error");
    error.textContent = "Время пишется как 20:00.";
    error.hidden = false;
    field.focus();
    return false;
  }

  function render(settings) {
    KEYS.forEach(function (key) { setOn(key, settings[key]); });
    $("quiet_from").value = String(settings.quiet_from || "20:00").slice(0, 5);
    $("quiet_to").value = String(settings.quiet_to || "08:00").slice(0, 5);
  }

  function refresh() {
    return auth().then(function (denied) {
      if (denied) { status(denied, true); return null; }
      return api("/webapp/admin/notifications/settings").then(function (res) {
        if (res.status !== 200 || !res.body || !res.body.ok) {
          status(problem(res), true);
          return null;
        }
        render(res.body.settings || {});
        status("");
        return res.body.settings;
      });
    });
  }

  KEYS.forEach(function (key) {
    row(key).addEventListener("click", function () { setOn(key, !isOn(key)); });
  });

  $("save").addEventListener("click", function () {
    status("");
    $("quiet_error").hidden = true;
    var from = $("quiet_from");
    var to = $("quiet_to");
    if (!checkClock(from) || !checkClock(to)) { return; }

    var button = $("save");
    button.disabled = true;
    api("/webapp/admin/notifications/settings", {
      rehearsals: isOn("rehearsals"),
      dues: isOn("dues"),
      schedule_changes: isOn("schedule_changes"),
      quiet_from: from.value.trim(),
      quiet_to: to.value.trim()
    }).then(function (res) {
      button.disabled = false;
      if (res.status === 200 && res.body && res.body.ok) {
        render(res.body.settings || {});
        toast("Сохранено: тихие часы " + from.value.trim() + "–" + to.value.trim());
        return;
      }
      status(problem(res), true);
    });
  });

  refresh();
"""

_BODY_NOTIFICATION_DEFAULTS = """
<div class="ui-small ui-muted" id="status" role="status"></div>

<section class="ui-section">
  <div class="ui-section__head"><h2 class="ui-section__title">Что присылать по умолчанию</h2></div>
  <div class="ui-list" id="switches">
    <button class="ui-switch-row" type="button" data-key="rehearsals" aria-pressed="false">
      <span class="ui-row__main">
        <span class="ui-row__title">Репетиции</span>
        <span class="ui-row__meta">напоминание заполнить занятость</span>
      </span>
      <span class="ui-switch-row__state">выключено</span><span class="ui-switch"></span>
    </button>
    <button class="ui-switch-row" type="button" data-key="dues" aria-pressed="false">
      <span class="ui-row__main">
        <span class="ui-row__title">Взносы</span>
        <span class="ui-row__meta">начисление и срок оплаты</span>
      </span>
      <span class="ui-switch-row__state">выключено</span><span class="ui-switch"></span>
    </button>
    <button class="ui-switch-row" type="button" data-key="schedule_changes" aria-pressed="false">
      <span class="ui-row__main">
        <span class="ui-row__title">Изменения расписания</span>
        <span class="ui-row__meta">срочные: приходят и в тихие часы</span>
      </span>
      <span class="ui-switch-row__state">выключено</span><span class="ui-switch"></span>
    </button>
  </div>
</section>

<section class="ui-section">
  <div class="ui-section__head"><h2 class="ui-section__title">Тихие часы по умолчанию</h2></div>
  <div class="ui-card">
    <div class="ui-range">
      <div class="ui-field">
        <span class="ui-field__label">с</span>
        <input class="ui-input" id="quiet_from" value="20:00" inputmode="numeric"
               maxlength="5" autocomplete="off" aria-label="Тихие часы с">
      </div>
      <span class="ui-range__sep">—</span>
      <div class="ui-field">
        <span class="ui-field__label">до</span>
        <input class="ui-input" id="quiet_to" value="08:00" inputmode="numeric"
               maxlength="5" autocomplete="off" aria-label="Тихие часы до">
      </div>
    </div>
    <span class="ui-field__error" id="quiet_error" hidden></span>
    <span class="ui-field__hint">Действуют для тех, кто не настроил свои. Частные
      настройки важнее общих.</span>
  </div>
</section>

<button class="ui-btn ui-btn--primary ui-btn--block" id="save" type="button">Сохранить</button>
"""

NOTIFICATION_DEFAULTS_PAGE = screen("Уведомления", _BODY_NOTIFICATION_DEFAULTS,
                                    _SCRIPT_NOTIFICATION_DEFAULTS)
