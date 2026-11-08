"""Экран «Уведомления». «Уведомления»: три типа событий и тихие часы.

Вложенный экран с шапкой и «‹»; возврат ведёт в `/webapp/more` (`window.SVOI_BACK`),
а не всегда на «Главную» (docs/86 §6). Общий JS — `misc_common.py`.
"""

from __future__ import annotations

from app.pages import layout
from app.pages.misc_common import JS_COMMON as _JS_COMMON

_BODY_NOTIFICATIONS = """
<section class="ui-section">
  <div class="ui-section__head"><h2 class="ui-section__title">Что присылать</h2></div>
  <div class="ui-list" id="switches">
    <button class="ui-switch-row" type="button" data-key="rehearsals" aria-pressed="false">
      <span class="ui-row__main">
        <span class="ui-row__title">Репетиции</span>
        <span class="ui-row__meta">напоминание за 2 часа</span>
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
  <div class="ui-section__head"><h2 class="ui-section__title">Тихие часы</h2></div>
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
    <span class="ui-field__hint">Обычные уведомления в это время придут утром.</span>
    <span class="ui-field__hint" id="source_hint" hidden>Сейчас действуют общие настройки
      студии. Сохраните свои, чтобы их изменить.</span>
  </div>
</section>

<button class="ui-btn ui-btn--primary ui-btn--block" id="save" type="button">Сохранить</button>
<div class="ui-alert" id="error" hidden></div>
"""

_SCRIPT_NOTIFICATIONS = "<script>\n(function () {\n  \"use strict\";\n  window.SVOI_BACK = \"/webapp/more\";\n" + _JS_COMMON + r"""
  var $ = function (id) { return document.getElementById(id); };
  var KEYS = ["rehearsals", "dues", "schedule_changes"];
  var CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;
  tgBoot();

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

  function showError(text) {
    var box = $("error");
    box.textContent = text || "";
    box.hidden = !text;
  }

  KEYS.forEach(function (key) {
    row(key).addEventListener("click", function () { setOn(key, !isOn(key)); });
  });

  function checkClock(field) {
    var value = field.value.trim();
    if (CLOCK.test(value)) { return true; }
    var error = $("quiet_error");
    error.textContent = "Время пишется как 20:00.";
    error.hidden = false;
    field.focus();
    return false;
  }

  async function load() {
    var result = await api("/webapp/notifications/settings");
    if (result.status === 401) {
      showError("Откройте приложение из бота — сессия не подтверждена.");
      return;
    }
    if (!result.body.ok) {
      showError("Настройки недоступны. Попробуйте открыть экран ещё раз.");
      return;
    }
    var settings = result.body.settings || {};
    KEYS.forEach(function (key) { setOn(key, settings[key]); });
    $("quiet_from").value = String(settings.quiet_from || "20:00").slice(0, 5);
    $("quiet_to").value = String(settings.quiet_to || "08:00").slice(0, 5);
    // Пока частной настройки нет, экран показывает общие значения студии (FR-G17, О3).
    $("source_hint").hidden = settings.source !== "defaults";
  }

  $("save").addEventListener("click", async function () {
    showError("");
    $("quiet_error").hidden = true;
    var from = $("quiet_from");
    var to = $("quiet_to");
    if (!checkClock(from) || !checkClock(to)) { return; }

    var button = $("save");
    button.disabled = true;
    var payload = {
      rehearsals: isOn("rehearsals"),
      dues: isOn("dues"),
      schedule_changes: isOn("schedule_changes"),
      quiet_from: from.value.trim(),
      quiet_to: to.value.trim(),
    };
    var result = await api("/webapp/notifications/settings", payload);
    button.disabled = false;
    if (result.status === 200 && result.body.ok) {
      $("source_hint").hidden = true;      // частные значения сохранены — общие больше не действуют
      SVOI.toast("Сохранено: тихие часы " + payload.quiet_from + "–" + payload.quiet_to);
      return;
    }
    showError("Не сохранилось. Проверьте время и повторите.");
  });

  load();
})();
</script>"""

NOTIFICATIONS_PAGE = layout.page("Уведомления", _BODY_NOTIFICATIONS,
                                  back="/webapp/more", scripts=_SCRIPT_NOTIFICATIONS)
