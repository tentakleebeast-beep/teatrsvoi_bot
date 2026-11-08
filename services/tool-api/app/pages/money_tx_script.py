"""JS формы операции: дата, тип, категория, план/факт, сохранение и «Вернуть».

Живёт отдельно от разметки (`pages/money_tx.py`) по той же причине: скрипт
большой, а модуль экрана должен оставаться читаемым (~400 строк). Текст
скрипта не менялся.
"""

from __future__ import annotations

_SCRIPT_TX = r"""
<script>
window.SVOI_BACK = "/webapp/money";
(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) { tg.ready(); tg.expand(); if (tg.BackButton) tg.BackButton.show(); }

  var $ = function (id) { return document.getElementById(id); };
  var NBSP = "\u00A0";
  var MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
                "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  var WEEKDAYS = ["понедельник", "вторник", "среда", "четверг",
                  "пятница", "суббота", "воскресенье"];

  var txType = "expense";
  var isPlan = false;
  var byDirection = { expense: [], income: [] };
  var pendingKey = "";

  function money(value) {
    var number = Math.round(Number(value) || 0);
    var digits = String(Math.abs(number)).replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
    return (number < 0 ? "\u2212" : "") + digits + NBSP + "\u20BD";
  }

  function isoLocal(day) {
    var month = String(day.getMonth() + 1);
    var date = String(day.getDate());
    return day.getFullYear() + "-" + (month.length < 2 ? "0" + month : month) +
      "-" + (date.length < 2 ? "0" + date : date);
  }

  function humanDate(value) {
    if (!value) { return ""; }
    var parts = String(value).slice(0, 10).split("-");
    if (parts.length !== 3) { return String(value); }
    var day = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
    if (isNaN(day.getTime())) { return String(value); }
    return day.getDate() + " " + MONTHS[day.getMonth()] + ", " +
      WEEKDAYS[(day.getDay() + 6) % 7];
  }

  function shifted(offset) {
    var day = new Date();
    day.setHours(12, 0, 0, 0);
    day.setDate(day.getDate() + Number(offset));
    return isoLocal(day);
  }

  // --- дата -----------------------------------------------------------------

  function refreshDate() {
    var value = $("tx-date").value || shifted(0);
    $("tx-date-human").textContent = humanDate(value) || "выберите дату";
    document.querySelectorAll("#tx-chips .ui-chip").forEach(function (chip) {
      var on = shifted(chip.getAttribute("data-offset")) === value;
      chip.classList.toggle("ui-chip--on", on);
      chip.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  $("tx-date").addEventListener("change", function () {
    clearError("tx_date");
    refreshDate();
  });
  document.querySelectorAll("#tx-chips .ui-chip").forEach(function (chip) {
    chip.addEventListener("click", function () {
      $("tx-date").value = shifted(chip.getAttribute("data-offset"));
      clearError("tx_date");
      refreshDate();
    });
  });
  $("tx-date").value = shifted(0);
  refreshDate();

  // --- тип и план/факт ------------------------------------------------------

  function pickType(type) {
    txType = type === "income" ? "income" : "expense";
    document.querySelectorAll("#tx-type [data-type]").forEach(function (button) {
      var on = button.getAttribute("data-type") === txType;
      button.classList.toggle("ui-segmented__item--on", on);
      button.setAttribute("aria-pressed", on ? "true" : "false");
    });
    clearError("category");
    fillCategories();
  }

  function pickPlan(plan) {
    isPlan = plan === "plan";
    document.querySelectorAll("#tx-plan [data-plan]").forEach(function (button) {
      var on = (button.getAttribute("data-plan") === "plan") === isPlan;
      button.classList.toggle("ui-segmented__item--on", on);
      button.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  document.querySelectorAll("#tx-type [data-type]").forEach(function (button) {
    button.addEventListener("click", function () { pickType(button.getAttribute("data-type")); });
  });
  document.querySelectorAll("#tx-plan [data-plan]").forEach(function (button) {
    button.addEventListener("click", function () { pickPlan(button.getAttribute("data-plan")); });
  });

  // --- поля и ошибки --------------------------------------------------------

  var LABELS = { amount: "Сумма", category: "Категория", tx_date: "Дата",
                 purpose: "Статья", tx_type: "Тип", is_plan: "План или факт" };

  function setError(field, text) {
    var box = $("err-" + field);
    if (box) { box.textContent = text; box.hidden = !text; }
    var control = $("tx-" + (field === "tx_date" ? "date" : field));
    if (control) { control.setAttribute("aria-invalid", text ? "true" : "false"); }
  }

  function clearError(field) {
    setError(field, "");
  }

  function clearErrors() {
    ["amount", "category", "tx_date", "purpose", "tx_type"].forEach(clearError);
    $("tx-alert").hidden = true;
    $("tx-alert").textContent = "";
  }

  function alertMessage(text) {
    $("tx-alert").textContent = text;
    $("tx-alert").hidden = !text;
  }

  // Ошибку FastAPI показываем словами и под тем полем, к которому она относится:
  // общий текст «[object Object]» ничего не говорил человеку.
  function errorText(field, item) {
    var kind = item && item.type;
    if (field === "amount") {
      if (kind === "missing") { return "Укажите сумму — например, 3 000."; }
      return "Сумма должна быть числом: 3 000 или 500,50.";
    }
    if (field === "category") { return "Выберите категорию — без неё не ясно, куда отнести деньги."; }
    if (field === "tx_date") { return "Укажите дату: сегодня или другую."; }
    if (field === "purpose") {
      if (kind === "string_too_long") { return "Статья слишком длинная — сократите её."; }
      return "Статья должна быть текстом.";
    }
    if (field === "tx_type") { return "Выберите направление: расход или доход."; }
    return "Проверьте значение поля «" + (LABELS[field] || field) + "».";
  }

  function showValidation(detail) {
    clearErrors();
    var first = null;
    var unknown = [];
    detail.forEach(function (item) {
      var loc = Array.isArray(item.loc) ? item.loc : [];
      var field = String(loc[loc.length - 1] || "");
      if (LABELS[field]) {
        setError(field, errorText(field, item));
        if (!first) { first = field; }
      } else {
        unknown.push(errorText(field, item));
      }
    });
    if (!first && unknown.length) { alertMessage("Не записано: " + unknown.join(" ")); }
    if (first) {
      var control = $("tx-" + (first === "tx_date" ? "date" : first));
      if (control && control.focus) { control.focus(); }
    }
  }

  // --- категории ------------------------------------------------------------

  function fillCategories() {
    var select = $("tx-category");
    var names = byDirection[txType] || [];
    var current = select.value;
    select.textContent = "";
    if (!names.length) {
      var none = document.createElement("option");
      none.value = "";
      none.textContent = "Категорий нет — обратитесь к организатору";
      select.appendChild(none);
      return;
    }
    var placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Выберите категорию";
    select.appendChild(placeholder);
    names.forEach(function (name) {
      var option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      select.appendChild(option);
    });
    if (names.indexOf(current) >= 0) { select.value = current; }
  }

  async function loadCategories() {
    try {
      var response = await fetch("/webapp/categories");
      var body = await response.json().catch(function () { return {}; });
      if (!response.ok || !body.ok) {
        alertMessage("Не удалось загрузить категории. Проверьте связь и откройте форму заново.");
        return;
      }
      if (body.by_direction) {
        byDirection = { expense: body.by_direction.expense || [],
                        income: body.by_direction.income || [] };
      } else {
        byDirection = { expense: body.categories || [], income: body.categories || [] };
      }
      fillCategories();
    } catch (error) {
      alertMessage("Сайт недоступен, попробуйте ещё раз.");
    }
  }

  // --- сохранение -----------------------------------------------------------

  function parseAmount(raw) {
    var value = String(raw == null ? "" : raw)
      .split(String.fromCharCode(160)).join("")
      .split(String.fromCharCode(8239)).join("")
      .split(" ").join("").split("\t").join("")
      .replace(",", ".");
    var number = Number(value);
    return Number.isFinite(number) ? number : NaN;
  }

  async function save(payload) {
    var response = await fetch("/webapp/tx/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    return { status: response.status, body: await response.json().catch(function () { return {}; }) };
  }

  function newKey() {
    return "webapp:" + Date.now() + ":" + Math.random().toString(36).slice(2, 8);
  }

  function undo(record) {
    var opposite = record.tx_type === "income" ? "expense" : "income";
    var body = document.createElement("div");
    var line = document.createElement("p");
    line.className = "money-sheet-line";
    line.textContent = "В журнале появится обратная операция: «" + record.category +
      "», " + money(record.amount) + ". Исходная запись останется — история не стирается.";
    body.appendChild(line);

    if (!window.SVOI || !window.SVOI.sheet) { return; }
    window.SVOI.sheet({
      title: "Вернуть запись?",
      body: body,
      actions: [
        { label: "Отмена" },
        {
          label: "Записать обратную",
          primary: true,
          onTap: async function () {
            var result = await save({
              tx_type: opposite,
              amount: record.amount,
              category: record.category,
              purpose: "Возврат: " + record.category,
              tx_date: record.tx_date,
              is_plan: record.is_plan,
              idempotency_key: newKey(),
            });
            if (result.body && result.body.ok) {
              if (window.SVOI.toast) {
                window.SVOI.toast("Записано обратное: " + money(record.amount) + ", " + record.category);
              }
            } else if (window.SVOI.toast) {
              window.SVOI.toast("Не удалось вернуть. Проверьте связь и повторите.");
            }
          },
        },
      ],
    });
  }

  var saveButton = $("tx-save");

  saveButton.addEventListener("click", async function () {
    if (saveButton.disabled) { return; }
    clearErrors();

    var amount = parseAmount($("tx-amount").value);
    if (!(amount > 0)) {
      setError("amount", "Укажите сумму больше нуля — например, 3 000.");
      $("tx-amount").focus();
      return;
    }
    var category = $("tx-category").value;
    if (!category) {
      setError("category", "Выберите категорию — без неё не ясно, куда отнести деньги.");
      $("tx-category").focus();
      return;
    }
    var txDate = $("tx-date").value;
    if (!txDate) {
      setError("tx_date", "Укажите дату: сегодня или другую.");
      return;
    }

    if (!pendingKey) { pendingKey = newKey(); }
    saveButton.disabled = true;
    saveButton.textContent = "Записываю…";

    var record = {
      tx_type: txType,
      amount: amount,
      category: category,
      purpose: $("tx-purpose").value || "",
      tx_date: txDate,
      is_plan: isPlan,
      idempotency_key: pendingKey,
    };

    var result;
    try {
      result = await save(record);
    } catch (error) {
      saveButton.disabled = false;
      saveButton.textContent = "Записать";
      alertMessage("Сайт недоступен, попробуйте ещё раз — запись не ушла.");
      return;
    }

    saveButton.disabled = false;
    saveButton.textContent = "Записать";

    if (result.status === 422 && result.body && Array.isArray(result.body.detail)) {
      showValidation(result.body.detail);
      return;                       // ключ сохраняем: повтор после правки — та же операция
    }
    if (!result.body || !result.body.ok) {
      var detail = result.body && result.body.detail;
      alertMessage("Не записано: " + (typeof detail === "string"
        ? detail
        : "сервер не принял операцию. Проверьте поля и повторите."));
      return;
    }

    pendingKey = "";
    $("tx-amount").value = "";
    $("tx-purpose").value = "";
    clearErrors();

    var text = "Записано: " + money(amount) + ", " + category;
    if (window.SVOI && window.SVOI.toast) {
      window.SVOI.toast(text, {
        action: "Вернуть",
        onAction: function () { undo(record); },
      });
    }
  });

  // Сумма: «1 000», «500,50», «500 ₽» — всё это число, запятая допустима.
  $("tx-amount").addEventListener("input", function () { clearError("amount"); });
  $("tx-category").addEventListener("change", function () { clearError("category"); });
  $("tx-purpose").addEventListener("input", function () { clearError("purpose"); });

  loadCategories();
})();
</script>
"""
