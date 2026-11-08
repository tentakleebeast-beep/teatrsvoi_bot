"""Экран «Сверка листов». «Сверка листов»: сначала расхождения, потом применение.

Вложенный экран с шапкой и «‹»; возврат ведёт в `/webapp/more` (`window.SVOI_BACK`),
а не всегда на «Главную» (docs/86 §6). Общий JS — `misc_common.py`.
"""

from __future__ import annotations

from app.pages import layout
from app.pages.misc_common import JS_COMMON as _JS_COMMON

_BODY_RECONCILE = """
<p class="ui-small ui-muted">Сверяет базу с таблицей: чего нет в листе, что изменили
вручную и что удалили.</p>

<button class="ui-btn ui-btn--primary ui-btn--block" id="check" type="button">Проверить</button>

<section class="ui-section" id="summary" hidden>
  <div class="ui-card">
    <span class="ui-card__label">Нет в листе</span>
    <span class="ui-amount" id="c-missing">0</span>
    <span class="ui-small ui-muted">строки базы, которых нет в таблице</span>
  </div>
  <div class="ui-card">
    <span class="ui-card__label">Разошлось</span>
    <span class="ui-amount" id="c-changed">0</span>
    <span class="ui-small ui-muted">изменили в таблице вручную</span>
  </div>
  <div class="ui-card">
    <span class="ui-card__label">Удалено из листа</span>
    <span class="ui-amount" id="c-deleted">0</span>
    <span class="ui-small ui-muted">строки, которые пропали из таблицы</span>
  </div>
</section>

<button class="ui-btn ui-btn--block" id="apply" type="button" hidden>Применить</button>

<div class="ui-alert" id="error" hidden></div>
<div class="ui-alert ui-alert--ok" id="done" hidden></div>
"""

_SCRIPT_RECONCILE = "<script>\n(function () {\n  \"use strict\";\n  window.SVOI_BACK = \"/webapp/more\";\n" + _JS_COMMON + r"""
  var $ = function (id) { return document.getElementById(id); };
  var counts = { missing: 0, changed: 0, deleted: 0 };
  tgBoot();

  function total() { return counts.missing + counts.changed + counts.deleted; }

  function rowsWord(count) {
    return count + " " + plural(count, "строку", "строки", "строк");
  }

  function showError(text) {
    var box = $("error");
    box.textContent = text || "";
    box.hidden = !text;
  }

  function showDone(text) {
    var box = $("done");
    box.textContent = text || "";
    box.hidden = !text;
  }

  function draw(report) {
    var budget = (report && report.budget) || {};
    counts.missing = Number(budget["нет_в_бд"] || 0);
    counts.changed = Number(budget["расхождения"] || 0);
    counts.deleted = Number(budget["нет_в_файле"] || 0);
    $("c-missing").textContent = String(counts.missing);
    $("c-changed").textContent = String(counts.changed);
    $("c-deleted").textContent = String(counts.deleted);
    $("summary").hidden = false;

    var apply = $("apply");
    if (total() > 0) {
      apply.hidden = false;
      apply.classList.add("ui-btn--primary");
      $("check").classList.remove("ui-btn--primary");
      apply.textContent = "Применить " + rowsWord(total());
    } else {
      apply.hidden = true;
      $("check").classList.add("ui-btn--primary");
      showDone("Расхождений нет: база и лист совпадают.");
    }
  }

  async function run(apply) {
    var button = apply ? $("apply") : $("check");
    button.disabled = true;
    button.textContent = apply ? "Записываю…" : "Сверяю…";
    showError("");
    showDone("");

    var result = await api("/webapp/admin/reconcile", { apply: apply, scope: "all" });
    button.disabled = false;

    if (result.status === 401) {
      button.textContent = apply ? "Применить" : "Проверить";
      showError("Откройте приложение из бота — сессия не подтверждена.");
      return;
    }
    if (!result.body.ok) {
      button.textContent = apply ? "Применить" : "Проверить";
      showError("Сверка не прошла. Попробуйте ещё раз через минуту.");
      return;
    }
    if (apply) {
      $("apply").hidden = true;
      $("check").classList.add("ui-btn--primary");
      $("check").textContent = "Проверить";
      showDone("Записано " + rowsWord(total()) + ". Расхождений больше нет.");
      SVOI.toast("Записано " + rowsWord(total()) + ".");
      return;
    }
    $("check").textContent = "Проверить";
    draw(result.body.report || {});
  }

  $("check").addEventListener("click", function () { run(false); });

  $("apply").addEventListener("click", function () {
    var count = total();
    SVOI.sheet({
      title: "Записать " + rowsWord(count) + " в лист?",
      body: '<p class="ui-text">Изменения уйдут в таблицу: ' + rowsWord(count)
        + '. Отменить запись после этого нельзя.</p>',
      actions: [
        { label: "Отмена" },
        { label: "Применить", primary: true, onTap: function () { run(true); } },
      ],
    });
  });

})();
</script>"""

RECONCILE_PAGE = layout.page("Сверка листов", _BODY_RECONCILE,
                             back="/webapp/more", scripts=_SCRIPT_RECONCILE)
