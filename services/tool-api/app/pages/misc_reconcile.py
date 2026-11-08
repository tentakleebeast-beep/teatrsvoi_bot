"""Экран «Сверка листов». «Сверка листов»: сначала расхождения, потом применение.

Вложенный экран с шапкой и «‹»; возврат ведёт в `/webapp/more` (`window.SVOI_BACK`),
а не всегда на «Главную» (docs/86 §6). Общий JS — `misc_common.py`.

Право `budget.import` есть у админа и организатора; у `troupe` и `master` его нет,
и раньше кнопка «Проверить» оставалась на экране — нажатие упиралось в 403 без
объяснения (карта t_01784cc4, отчёт 142 §3 строка 10). Страница — публичная
константа, поэтому «есть ли право» приезжает запросом `/webapp/nav` уже после
входа (docs/architecture/25 §9.2): без права вместо кнопки стоит фраза, кто ведёт
сверку. Отказ 403 на самом вызове ведёт к тому же объяснению.
"""

from __future__ import annotations

from app.pages import layout
from app.pages.misc_common import JS_COMMON as _JS_COMMON

_BODY_RECONCILE = """
<p class="ui-small ui-muted" id="intro">Сверяет базу с таблицей: чего нет в листе, что изменили
вручную и что удалили.</p>

<div class="ui-card" id="denied" hidden>
  <span class="ui-card__label">Сверку ведёт организатор</span>
  <span class="ui-small ui-muted">Расхождения базы с листом закрывает он: у вашей роли сверки нет.</span>
</div>

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

  /* Нет права `budget.import`: кнопки нет, вместо неё — кто ведёт сверку.
     Счётчики и «Применить» прячем тоже: без сверки их нечем наполнить. */
  function deny() {
    $("intro").hidden = true;
    $("check").hidden = true;
    $("apply").hidden = true;
    $("summary").hidden = true;
    $("denied").hidden = false;
    showError("");
    showDone("");
  }

  /* Права спрашивает сервер (`/webapp/nav`): страница — публичная константа.
     Убираем кнопку только по явному ответу; сломанный nav не должен отнимать
     сверку у организатора (так же решена строка сводки, карта t_70a6d8e5). */
  function applyCaps(body) {
    if (!body || body.ok !== true || typeof body.reconcile !== "boolean") { return; }
    if (!body.reconcile) { deny(); }
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
    if (result.status === 403) {
      // Право сняли после открытия или nav не ответил: говорим то же, что и на входе.
      deny();
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

  /* Права — до первого нажатия: участник не должен увидеть кнопку, которая
     упрётся в 403 (карта t_01784cc4). Ответа нет — кнопка остаётся. */
  api("/webapp/nav").then(function (answer) { applyCaps(answer.body); },
                          function () { /* nav не ответил: решает сервер на вызове */ });

})();
</script>"""

RECONCILE_PAGE = layout.page("Сверка листов", _BODY_RECONCILE,
                             back="/webapp/more", scripts=_SCRIPT_RECONCILE)
