"""Админский экран «Взносы»: тариф человека и начисление за месяц (Д6, Д8).

Зачем раздел: путь записи `POST /tools/dues/accrue` был API-сиротой — организатор
не мог начислить взносы без curl, хотя это его ежемесячная работа. Здесь кнопка
«Начислить за месяц» с подтверждением; путь тот же, обработчик тот же, второго
правила начисления нет (`docs/architecture/25` §9.4, FR-B7).

Право раздела — `budget.write`: то же, что проверяет сервер у начисления и у тарифа.
Поэтому организатор попадает сюда без `participants.manage` и без админ-панели
бота (расхождение 4.5 отчёта 122). Тариф правится в листе человека, тап по строке.

Модуль — только разметка и скрипт: общее ядро (токен, запросы, строки, листы)
лежит в `admin_core.py`.
"""

from __future__ import annotations

from app.pages.admin_core import screen

_SCRIPT_DUES = r"""
  var state = { period: "", title: "", totals: {}, data: [] };

  function shift(period, delta) {
    var parts = String(period || "").split("-");
    var year = Number(parts[0]), month = Number(parts[1]);
    if (!year || !month) return "";
    var index = year * 12 + (month - 1) + delta;
    return Math.floor(index / 12) + "-" + ("0" + (index % 12 + 1)).slice(-2);
  }

  function restOf(item) {
    return Math.max(0, Number(item.amount_due || 0) - Number(item.amount_paid || 0));
  }

  function tariffText(item) {
    return item.tariff === null || item.tariff === undefined
      ? "тариф не назначен" : money(item.tariff) + " в месяц";
  }

  function personRow(item) {
    var row = el("button", "ui-row");
    row.type = "button";
    var main = el("span", "ui-row__main");
    main.appendChild(el("span", "ui-row__title", item.display_name));
    var meta = tariffText(item);
    if (item.charged) {
      meta += " · начислено " + money(item.amount_due)
        + ", внесено " + money(item.amount_paid);
    } else if (item.tariff_applies) {
      meta += " · за месяц не начислено";
    }
    main.appendChild(el("span", "ui-row__meta", meta));
    row.appendChild(main);
    var rest = restOf(item);
    row.appendChild(el("span", "ui-row__value",
      !item.charged ? "—" : (rest <= 0 ? "закрыт" : money(rest))));
    row.appendChild(el("span", "ui-row__chevron", "›"));
    row.onclick = function () { tariffSheet(item); };
    return row;
  }

  function render() {
    var totals = state.totals || {};
    $("dues-title").textContent = "Взносы за " + (state.title || state.period);
    $("dues-due").textContent = money(totals.due || 0);
    $("dues-meta").textContent = "начислено " + (totals.charged || 0)
      + " из " + (totals.people || 0) + " человек · внесено " + money(totals.paid || 0);

    var list = $("dues-list");
    clear(list);
    var rows = state.data || [];
    if (!rows.length) {
      list.appendChild(empty("Пока никого", "Активных участников нет."));
      return;
    }
    rows.forEach(function (item) { list.appendChild(personRow(item)); });
  }

  function saveTariff(item, input) {
    var raw = String(input.value || "").replace(/\u00a0/g, "").replace(/\s/g, "").replace(",", ".");
    var value = raw === "" ? null : Number(raw);
    if (value !== null && (!isFinite(value) || value < 0)) {
      toast("Тариф — число не меньше нуля.");
      return;
    }
    api("/webapp/admin/tariff", { participant_id: item.id, tariff: value }).then(function (res) {
      if (res.status >= 200 && res.status < 300) {
        closeSheet();
        toast(value === null ? "Тариф снят: " + item.display_name
                             : "Тариф обновлён: " + money(value) + " в месяц");
        load(state.period);
      } else {
        toast(problem(res));
      }
    });
  }

  function tariffSheet(item) {
    var body = el("div", "admin-sheet");
    var dl = el("dl", "ui-dl");
    pair(dl, "Тариф", tariffText(item));
    if (item.charged) pair(dl, "За месяц", "начислено " + money(item.amount_due));
    body.appendChild(dl);

    var field = el("div", "ui-field");
    var label = el("label", "ui-field__label", "Тариф взноса, ₽ в месяц");
    var input = el("input", "ui-input ui-input--amount");
    input.type = "text";
    input.inputMode = "decimal";
    input.autocomplete = "off";
    input.id = "dues-tariff-" + item.id;
    label.setAttribute("for", input.id);
    input.value = item.tariff === null || item.tariff === undefined ? "" : String(item.tariff);
    field.appendChild(label);
    field.appendChild(input);
    field.appendChild(el("span", "ui-field__hint", "Пусто — снять тариф. Новый действует со следующего месяца."));
    body.appendChild(field);

    var save = el("button", "ui-btn ui-btn--block", "Сохранить тариф");
    save.type = "button";
    save.onclick = function () { saveTariff(item, input); };
    body.appendChild(save);
    sheet(item.display_name, body, [{ label: "Закрыть" }]);
  }

  function accrue() {
    var body = el("div", "admin-sheet");
    body.appendChild(el("p", "ui-text",
      "Взносы за " + state.title + " начислятся по тарифу, который действует с начала "
      + "месяца. Уже начисленный месяц не меняется — повтор ничего не перезапишет."));
    sheet("Начислить за " + state.title + "?", body, [
      { label: "Отмена" },
      { label: "Начислить", primary: true, onTap: function () {
        api("/webapp/admin/dues/accrue", { period: state.period }).then(function (res) {
          if (res.status >= 200 && res.status < 300 && res.body && res.body.ok) {
            closeSheet();
            toast("Начислено: " + res.body.created + " · пропущено: " + res.body.skipped);
            load(state.period);
          } else {
            toast(problem(res));
          }
        });
      } }
    ]);
  }

  function load(period) {
    var path = "/webapp/admin/dues" + (period ? "?period=" + encodeURIComponent(period) : "");
    return auth().then(function (denied) {
      if (denied) { status(denied, true); return; }
      return api(path).then(function (res) {
        if (res.status !== 200 || !res.body || !res.body.ok) {
          status(problem(res), true);
          return;
        }
        status("");
        state = res.body;
        render();
      });
    });
  }

  $("dues-prev").onclick = function () { load(shift(state.period, -1)); };
  $("dues-next").onclick = function () { load(shift(state.period, 1)); };
  $("dues-accrue").onclick = accrue;
  load("");
"""

_BODY_DUES = """
<div class="ui-card">
  <span class="ui-card__label" id="dues-title">Взносы</span>
  <span class="ui-amount" id="dues-due">—</span>
  <span class="ui-text" id="dues-meta"></span>
</div>

<div class="ui-actions">
  <button class="ui-btn ui-btn--sm" id="dues-prev" type="button">Прошлый месяц</button>
  <button class="ui-btn ui-btn--sm" id="dues-next" type="button">Следующий месяц</button>
</div>

<button class="ui-btn ui-btn--primary ui-btn--block" id="dues-accrue" type="button">Начислить за месяц</button>

<section class="ui-section">
  <div class="ui-section__head"><h2 class="ui-section__title">Тарифы и начисления</h2></div>
  <div class="ui-list" id="dues-list"></div>
</section>

<div class="ui-small ui-muted" id="status" role="status"></div>
"""

DUES_ADMIN_PAGE = screen("Взносы", _BODY_DUES, _SCRIPT_DUES)
