"""Экран «Мои взносы». «Мои взносы»: тариф, внесено, остаток, история месяцами.

Вложенный экран с шапкой и «‹»; возврат ведёт в `/webapp/money` (`window.SVOI_BACK`),
а не всегда на «Главную» (docs/86 §6). Общий JS — `misc_common.py`.
"""

from __future__ import annotations

from app.pages import layout
from app.pages.misc_common import JS_COMMON as _JS_COMMON

_BODY_DUES = """
<div class="ui-card">
  <span class="ui-card__label" id="period">Считаю…</span>
  <span class="ui-amount" id="rest">—</span>
  <div class="ui-text" id="summary"></div>
</div>

<section class="ui-section">
  <div class="ui-section__head"><h2 class="ui-section__title">Последние месяцы</h2></div>
  <div class="ui-list" id="history"></div>
</section>

<div class="ui-alert" id="error" hidden></div>
<div class="ui-small ui-muted">Взносы вносит организатор — отдельно сообщать не нужно.</div>
"""

_SCRIPT_DUES = "<script>\n(function () {\n  \"use strict\";\n  window.SVOI_BACK = \"/webapp/money\";\n" + _JS_COMMON + r"""
  var $ = function (id) { return document.getElementById(id); };
  tgBoot();

  function showError(text) {
    var box = $("error");
    box.textContent = text || "";
    box.hidden = !text;
  }

  function historyRow(item) {
    var rest = Number(item.amount_due || 0) - Number(item.amount_paid || 0);
    var row = make("div", "ui-row");
    var main = make("span", "ui-row__main");
    main.appendChild(make("span", "ui-row__title", monthName(item.period)));
    var meta = "внесено " + money(item.amount_paid) + " из " + money(item.amount_due);
    if (rest > 0) {
      meta += " · осталось " + money(rest);
    }
    main.appendChild(make("span", "ui-row__meta", meta));
    row.appendChild(main);
    if (rest <= 0) {
      row.appendChild(make("span", "ui-row__value", "закрыт"));
    }
    return row;
  }

  function render(state) {
    var current = state.current || {};
    var summary = state.summary || {};
    // Месяц и текст — из ответа сервера: тот же месяц и те же слова человек видит
    // в чате (`/dues`), второй копии текста на экране нет (FR-B7, Д12).
    $("period").textContent = summary.title || monthName(current.period) || "Мои взносы";

    var due = Number(current.due || 0);
    var paid = Number(current.paid || 0);
    var rest = Math.max(0, due - paid);
    $("rest").textContent = current.charged ? money(rest) : "—";
    $("rest").className = "ui-amount"
      + (current.charged ? (rest <= 0 ? " ui-amount--ok" : " ui-amount--due") : "");

    var box = $("summary");
    box.textContent = "";
    (summary.lines || []).forEach(function (line) {
      box.appendChild(make("div", null, line));
    });

    var host = $("history");
    host.textContent = "";
    var rows = (state.data || []).slice(0, 6);
    if (!rows.length) {
      var empty = make("div", "ui-empty");
      empty.appendChild(make("div", "ui-empty__title", "Начислений пока не было"));
      empty.appendChild(make("div", "ui-empty__text",
        "Когда организатор начислит взнос, месяц появится здесь."));
      host.appendChild(empty);
      return;
    }
    rows.forEach(function (item) { host.appendChild(historyRow(item)); });
  }

  async function load() {
    var result = await api("/webapp/my/dues");
    if (result.status === 401) {
      $("period").textContent = "Мои взносы";
      showError("Откройте приложение из бота — сессия не подтверждена.");
      return;
    }
    if (!result.body.ok) {
      $("period").textContent = "Мои взносы";
      showError("Не удалось загрузить взносы. Потяните экран вниз, чтобы повторить.");
      return;
    }
    showError("");
    render(result.body);
  }

  load();
})();
</script>"""

DUES_PAGE = layout.page("Мои взносы", _BODY_DUES, back="/webapp/money", scripts=_SCRIPT_DUES)
