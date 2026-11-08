"""JS вкладки «Деньги»: операции месяца, бюджет месяца и свои взносы.

Скрипт вынесен из `pages/money.py` отдельным модулем, чтобы сам экран оставался
в пределах ~400 строк. Текст скрипта не менялся: разметка, стили и поведение
экрана — прежние, страница собирается в `pages/money.py`.
"""

from __future__ import annotations

_SCRIPT_MONEY = r"""
<script>
(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) { tg.ready(); tg.expand(); if (tg.BackButton) tg.BackButton.hide(); }
  if (window.SVOI_BACK === undefined) { window.SVOI_BACK = "/webapp/money"; }

  var $ = function (id) { return document.getElementById(id); };
  var NBSP = "\u00A0";
  var MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
                "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  var MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь",
                    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
  var WEEKDAYS = ["понедельник", "вторник", "среда", "четверг",
                  "пятница", "суббота", "воскресенье"];
  var BLOCKS = { dues: "взносы", expense: "расходы", show_income: "сборы",
                 show_invest: "вложения", sublease: "субаренда" };

  var view = "dues";
  var canRead = false;      // budget.read: операции и бюджет месяца
  var canWrite = false;     // budget.write: «Записать операцию»
  var opsRows = [];
  var periodTitle = "";

  function money(value) {
    var number = Math.round(Number(value) || 0);
    var sign = number < 0 ? "\u2212" : "";
    var digits = String(Math.abs(number)).replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
    return sign + digits + NBSP + "\u20BD";
  }

  function signedMoney(value, type) {
    var number = Math.abs(Number(value) || 0);
    return (type === "income" ? "+" : "\u2212") + money(number);
  }

  function humanDate(value) {
    if (!value) { return ""; }
    var parts = String(value).slice(0, 10).split("-");
    if (parts.length !== 3) { return String(value); }
    var day = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
    if (isNaN(day.getTime())) { return String(value); }
    var weekday = WEEKDAYS[(day.getDay() + 6) % 7];
    return day.getDate() + " " + MONTHS[day.getMonth()] + ", " + weekday;
  }

  function monthName(period) {
    var parts = String(period || "").split("-");
    if (parts.length < 2) { return String(period || ""); }
    var index = Number(parts[1]) - 1;
    return (MONTHS_NOM[index] || parts[1]) + " " + parts[0];
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }

  function sheet(rows) {
    var body = el("div", "ui-dl");
    rows.forEach(function (pair) {
      if (!pair[1]) { return; }
      var line = el("div", "ui-dl__row");
      line.appendChild(el("span", "ui-dl__term", pair[0]));
      line.appendChild(el("span", "ui-dl__value", pair[1]));
      body.appendChild(line);
    });
    var kit = window.SVOI || {};
    if (kit.sheet) {
      kit.sheet({ title: rows.length ? rows[0][1] : "Операция", body: body,
                  actions: [{ label: "Закрыть" }] });
    }
  }

  function empty(title, text) {
    var box = el("div", "ui-empty");
    box.appendChild(el("div", "ui-empty__title", title));
    box.appendChild(el("div", "ui-empty__text", text));
    return box;
  }

  function status(text) {
    $("dues-meta").textContent = text;
  }

  // --- операции месяца -------------------------------------------------------

  function operationRow(item) {
    var date = humanDate(item.tx_date);
    var author = item.author || item.entered_by_name || item.created_by_name || "";
    var purpose = item.purpose || "";
    var meta = [date, author, purpose].filter(Boolean).join(" \u00B7 ");

    var row = el("button");
    row.type = "button";
    row.className = "ui-row";
    var main = el("span", "ui-row__main");
    main.appendChild(el("span", "ui-row__title", item.category || "Прочее"));
    if (meta) { main.appendChild(el("span", "ui-row__meta", meta)); }
    row.appendChild(main);

    var type = item.tx_type === "income" ? "income" : "expense";
    var value = el("span", "ui-row__value money-value " + (type === "income" ? "money-in" : "money-out"),
                   signedMoney(item.amount, type));
    row.appendChild(value);

    row.onclick = function () {
      sheet([
        [item.category || "Прочее", item.category || "Прочее"],
        ["Тип", type === "income" ? "доход" : "расход"],
        ["Дата", date],
        ["Сумма", signedMoney(item.amount, type)],
        ["Статья", purpose],
        ["Кто записал", author || "не указан"],
        ["Лист", BLOCKS[item.block] || item.block || ""],
      ]);
    };
    return row;
  }

  function totals(rows) {
    var income = 0, expense = 0;
    rows.forEach(function (item) {
      var number = Number(item.amount) || 0;
      if (item.tx_type === "income") { income += number; } else { expense += number; }
    });
    return { income: income, expense: expense, balance: income - expense };
  }

  function renderOps(block) {
    opsRows = block.data || [];
    periodTitle = block.period_title || monthName(block.period);
    var sum = totals(opsRows);
    $("ops-period").textContent = periodTitle || "текущий месяц";
    var total = $("ops-total");
    total.textContent = money(sum.balance);
    total.className = "ui-amount ui-num " + (sum.balance < 0 ? "money-out" : "money-in");
    $("ops-meta").textContent = "доходы " + money(sum.income) + " \u00B7 расходы " + money(sum.expense);
    $("ops-note").hidden = opsRows.length < 2;

    var list = $("ops-list");
    list.textContent = "";
    if (!opsRows.length) {
      list.appendChild(empty("Операций нет", "Запишите первую — кнопка внизу экрана."));
      return;
    }
    opsRows.forEach(function (item) { list.appendChild(operationRow(item)); });
  }

  function renderBudget(block) {
    var rows = block.data || [];
    var sum = totals(rows);
    $("budget-period").textContent = (block.period_title || monthName(block.period)) + " \u00B7 факт";
    var total = $("budget-total");
    total.textContent = money(sum.balance);
    total.className = "ui-amount ui-num " + (sum.balance < 0 ? "money-out" : "money-in");
    $("budget-meta").textContent = "доходы " + money(sum.income) +
      " \u00B7 расходы " + money(sum.expense);
    $("budget-note").hidden = false;

    var byCategory = {};
    rows.forEach(function (item) {
      var name = item.category || "Прочее";
      var bucket = byCategory[name] || (byCategory[name] = { total: 0, count: 0, type: "expense" });
      var number = Number(item.amount) || 0;
      bucket.count += 1;
      bucket.type = item.tx_type === "income" ? "income" : "expense";
      bucket.total += bucket.type === "income" ? number : -number;
    });
    var items = Object.keys(byCategory).map(function (name) {
      return { name: name, total: byCategory[name].total,
               count: byCategory[name].count, type: byCategory[name].type };
    });
    items.sort(function (a, b) { return Math.abs(b.total) - Math.abs(a.total); });

    var list = $("budget-list");
    list.textContent = "";
    if (!items.length) {
      list.appendChild(empty("Статей нет", "В этом месяце операций ещё не было."));
      return;
    }
    items.forEach(function (item) {
      var row = el("div", "ui-row");
      var main = el("span", "ui-row__main");
      main.appendChild(el("span", "ui-row__title", item.name));
      main.appendChild(el("span", "ui-row__meta",
        item.count === 1 ? "1 операция" : item.count + " операций"));
      row.appendChild(main);
      var sign = item.total < 0 ? "expense" : "income";
      row.appendChild(el("span", "ui-row__value money-value " +
        (sign === "income" ? "money-in" : "money-out"), signedMoney(item.total, sign)));
      list.appendChild(row);
    });
  }

  // --- мои взносы ------------------------------------------------------------

  function duesRow(item) {
    var due = Number(item.amount_due) || 0;
    var paid = Number(item.amount_paid) || 0;
    var rest = due - paid;
    var row = el("div", "ui-row");
    var main = el("span", "ui-row__main");
    main.appendChild(el("span", "ui-row__title", monthName(item.period)));
    main.appendChild(el("span", "ui-row__meta", money(paid) + " из " + money(due)));
    row.appendChild(main);
    var state = rest > 0 ? (paid > 0 ? "частично" : "не оплачено") : "оплачено";
    var tone = rest > 0 ? (paid > 0 ? "ui-status--warn" : "ui-status--danger") : "ui-status--ok";
    row.appendChild(el("span", "ui-status " + tone, state));
    return row;
  }

  function renderDues(data) {
    var current = data.current || {};
    var due = Number(current.due) || 0;
    var paid = Number(current.paid) || 0;
    var rest = Math.max(0, due - paid);

    $("dues-period").textContent = monthName(current.period);
    var total = $("dues-total");
    if (!current.charged) {
      total.textContent = "—";
      total.className = "ui-amount ui-num ui-muted";
      $("dues-meta").textContent = "за этот месяц начислений нет";
    } else if (rest > 0) {
      total.textContent = money(rest);
      total.className = "ui-amount ui-num money-out";
      $("dues-meta").textContent = "осталось " + money(rest) + " из " + money(due);
    } else {
      total.textContent = money(due);
      total.className = "ui-amount ui-num money-in";
      $("dues-meta").textContent = "взнос закрыт: " + money(paid) + " из " + money(due);
    }

    var note = "Взносы вносит организатор.";
    if (current.charged) {
      note += " Оплачено " + money(paid) + " из " + money(due) + ".";
    }
    var debt = Number(data.debt) || 0;
    if (debt > 0) { note += " Долг за прошлые месяцы — " + money(debt) + "."; }
    $("dues-status").textContent = note;

    var list = $("dues-history");
    list.textContent = "";
    var history = data.data || [];
    if (!history.length) {
      list.appendChild(empty("Начислений пока нет",
        "Когда организатор начислит взнос, месяц появится здесь."));
      return;
    }
    history.slice(0, 6).forEach(function (item) { list.appendChild(duesRow(item)); });
  }

  // --- загрузка и права ------------------------------------------------------

  function tileUrls(payload) {
    var urls = [];
    (payload.sections || []).forEach(function (section) {
      (section.tiles || []).forEach(function (tile) {
        if (tile && tile.url) { urls.push(String(tile.url)); }
      });
    });
    return urls;
  }

  async function loadRights() {
    var urls = [];
    try {
      var home = await fetch("/webapp/home/data");
      var payload = await home.json().catch(function () { return {}; });
      if (home.ok && payload.ok) {
        urls = tileUrls(payload);
        canWrite = Boolean(payload.can_record) ||
          urls.indexOf("/webapp/tx") >= 0 ||
          urls.some(function (url) { return url.indexOf("/tx") >= 0; });
      }
    } catch (error) { /* права уточним по ответу бюджета */ }

    var overview = null;
    try {
      var response = await fetch("/webapp/admin/overview");
      var body = await response.json().catch(function () { return {}; });
      if (response.ok && body.ok) {
        overview = body;
        canRead = Boolean(body.blocks && body.blocks.budget);
        if (body.actions) {
          canWrite = canWrite || Boolean(body.actions.set_tariff || body.actions.retry_sheet);
        }
      }
    } catch (error) { canRead = false; }

    $("money-actions").hidden = !canWrite;
    if (!canRead) {
      $("money-seg").hidden = true;
      $("view-ops").hidden = true;
      $("view-budget").hidden = true;
      $("view-dues").hidden = false;
      return;
    }

    $("money-seg").hidden = false;
    document.querySelectorAll("#money-seg [data-view]").forEach(function (button) {
      button.hidden = false;
    });
    if (overview && overview.blocks && overview.blocks.budget) {
      renderOps(overview.blocks.budget);
      renderBudget(overview.blocks.budget);
    }
    switchView("ops");
  }

  async function loadDues() {
    try {
      var response = await fetch("/webapp/my/dues");
      var body = await response.json().catch(function () { return {}; });
      if (!response.ok || !body.ok) {
        status(response.status === 401
          ? "Откройте приложение из бота — сессия не подтверждена."
          : "Не удалось загрузить взносы. Потяните экран вниз, чтобы повторить.");
        return;
      }
      renderDues(body);
    } catch (error) {
      status("Сайт недоступен, попробуйте ещё раз.");
    }
  }

  function switchView(next) {
    view = next;
    $("view-ops").hidden = next !== "ops";
    $("view-budget").hidden = next !== "budget";
    $("view-dues").hidden = next !== "dues";
    document.querySelectorAll("#money-seg [data-view]").forEach(function (button) {
      var on = button.getAttribute("data-view") === next;
      button.classList.toggle("ui-segmented__item--on", on);
      button.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  document.querySelectorAll("#money-seg [data-view]").forEach(function (button) {
    button.addEventListener("click", function () {
      switchView(button.getAttribute("data-view"));
    });
  });

  // Взносы грузим первым: у студийца это единственное содержимое раздела.
  loadDues();
  loadRights();
})();
</script>
"""
