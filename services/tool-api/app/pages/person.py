"""Карточка участника (`/webapp/person`): лист человека на общей оболочке.

Экран открывается из старых ссылок бота (`?id=<номер>`) и показывает карточку
человека: имя, ключ, роли, телефон, короткие имена, время в пути и тариф взноса.
Тариф сохраняется через `POST /webapp/person/tariff`.

До 01.11.2026 экран жил на собственной вёрстке: свой каркас `<head>`, своя копия
`<style>` и свои стили полей. Из-за этого правки оболочки (тема, кнопка «назад»,
тихое обновление токена) в него не доезжали, а критерий 44 px пришлось закрывать
точным `min-height` внутри экрана (docs/reports/106-assets-and-js-behavior-tests.md §3).
Теперь каркас — `layout.page`, стили — классы `webapp-ui.css`, токен и запросы —
`static/webapp-shell.js`; своей копии `svoi_token` страница не пишет
(docs/development/81 §4).

Экран ждёт переезда в «Управление → Люди» (docs/plans/88), но ссылки бота живут:
ломать их нельзя.
"""

from __future__ import annotations

from app.pages import layout
from app.pages.misc_common import JS_COMMON as _JS_COMMON

# Подписи ошибок — по коду ответа, а не по коду ошибки из тела: сырое
# `no_webapp_token` человеку ничего не говорит, а статус сервера говорит.
_BODY_PERSON = """
<section class="ui-section">
  <h1 class="ui-title" id="name">…</h1>
  <div class="ui-card"><dl class="ui-dl" id="facts"></dl></div>
</section>

<section class="ui-section">
  <div class="ui-section__head"><h2 class="ui-section__title">Взнос</h2></div>
  <div class="ui-field">
    <label class="ui-field__label" for="tariff">Сумма в месяц, ₽</label>
    <input class="ui-input ui-input--amount" id="tariff" type="text" inputmode="decimal"
           autocomplete="off" placeholder="не назначен">
    <span class="ui-field__hint">Пусто — снять тариф. Новый тариф действует со следующего месяца.</span>
  </div>
  <p class="ui-small ui-muted" id="month">Текущий месяц: —</p>
</section>

<button class="ui-btn ui-btn--primary ui-btn--block" id="send" type="button">Сохранить тариф</button>
<div class="ui-alert" id="status" role="status" hidden></div>
"""

_SCRIPT_PERSON = ("<script>\n(function () {\n  \"use strict\";\n"
                  "  window.SVOI_BACK = \"/webapp/home\";\n" + _JS_COMMON + r"""
  var $ = function (id) { return document.getElementById(id); };
  var params = new URLSearchParams(location.search);
  var personId = Number(params.get("id") || 0);
  tgBoot();

  // Статус — один элемент на экран: и удача, и отказ видны словами (не пустой
  // строкой и не «undefined»), цвет берётся из дизайн-системы.
  function showStatus(text, ok) {
    var box = $("status");
    box.textContent = text || "";
    box.hidden = !text;
    box.className = ok ? "ui-alert ui-alert--ok" : "ui-alert";
  }

  // Ошибки полей FastAPI — русским текстом. Коды вида float_parsing человеку
  // не показываем: он не обязан знать, как называется проверка на сервере.
  function detailText(detail) {
    if (!Array.isArray(detail)) {
      return typeof detail === "string" ? detail : "";
    }
    return detail.map(function (item) {
      var field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : "";
      if (field === "tariff") {
        if (item.type === "float_parsing" || item.type === "int_parsing") {
          return "Тариф должен быть числом.";
        }
        return "Тариф не подошёл: проверьте значение.";
      }
      return "Значение не подошло: проверьте поле.";
    }).join(" ");
  }

  function problem(result) {
    if (result.status === 401) { return "Нет доступа: откройте карточку из бота."; }
    if (result.status === 403) { return "Нет прав: тариф меняет организатор."; }
    if (result.status === 404) { return "Человек не найден."; }
    if (result.status === 400 || result.status === 422) {
      var detail = detailText(result.body && result.body.detail);
      if (detail) { return detail; }
      return "Проверьте тариф: значение не подошло.";
    }
    return "Сайт недоступен, попробуйте ещё раз.";
  }

  function pair(term, value) {
    var row = make("div", "ui-dl__row");
    row.appendChild(make("dt", "ui-dl__term", term));
    row.appendChild(make("dd", "ui-dl__value", value === "" || value === null ||
                                              value === undefined ? "—" : String(value)));
    return row;
  }

  function monthText(month) {
    if (!month) { return "Текущий месяц: начислений нет."; }
    if (month.settled) { return "Текущий месяц: взнос учтён."; }
    if (month.charged) { return "Текущий месяц: взнос не учтён."; }
    return "Текущий месяц: начислений нет.";
  }

  function render(card) {
    var data = card.data || {};
    $("name").textContent = data.display_name || "Участник";
    var facts = $("facts");
    facts.textContent = "";
    facts.appendChild(pair("Ключ", data.person_key));
    facts.appendChild(pair("Роли", (data.roles || []).join(", ")));
    facts.appendChild(pair("Телефон", data.phone));
    facts.appendChild(pair("Короткие имена",
                           (data.aliases || []).map(function (item) { return item.alias; })
                             .join(", ")));
    facts.appendChild(pair("Время в пути",
                           data.travel_minutes === null || data.travel_minutes === undefined
                             ? "—" : data.travel_minutes + " мин"));
    var tariff = (data.tariff || {}).tariff;
    $("tariff").value = tariff === null || tariff === undefined ? "" : String(tariff);
    $("month").textContent = monthText(card.month);
  }

  async function boot() {
    var problemText = await SVOI.auth();
    if (problemText) { showStatus(problemText, false); return; }
    var result = await api("/webapp/person/info?id=" + personId);
    if (!result.body || !result.body.ok) {
      showStatus("Профиль недоступен: " + problem(result), false);
      return;
    }
    render(result.body);
  }

  $("send").addEventListener("click", async function () {
    var element = $("send");
    if (element.disabled) { return; }
    var raw = $("tariff").value.trim().replace(/\s/g, "").replace(",", ".");
    var value = raw === "" ? null : Number(raw);
    if (value !== null && (!isFinite(value) || value < 0)) {
      showStatus("Нужно число не меньше нуля.", false);
      return;
    }
    showStatus("Сохраняю…", true);
    element.disabled = true;
    var result = await api("/webapp/person/tariff",
                           { participant_id: personId, tariff: value });
    element.disabled = false;
    if (result.body && result.body.ok && result.status >= 200 && result.status < 300) {
      showStatus(value === null ? "Тариф снят."
                                : "Тариф сохранён, действует со следующего месяца.", true);
      return;
    }
    showStatus("Не удалось: " + problem(result), false);
  });

  boot();
})();
</script>""")

PERSON_PAGE = layout.page("Карточка участника", _BODY_PERSON, back="/webapp/home",
                          scripts=_SCRIPT_PERSON)
