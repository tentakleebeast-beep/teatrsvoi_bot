"""Экран «Рассылка». «Рассылка»: шаблон, текст, число адресатов, подтверждение листом.

Вложенный экран с шапкой и «‹»; возврат ведёт в `/webapp/more` (`window.SVOI_BACK`),
а не всегда на «Главную» (docs/86 §6). Общий JS — `misc_common.py`.

Право `notify.send` есть только у администратора (`db/init/057`, FR-G2), поэтому
ответ 403 на счётчик адресатов — не сбой сети: экран говорит, у кого рассылка
есть, и прячет форму (принципы 91: 8, Б3; карта t_41a4ef6d).
"""

from __future__ import annotations

from app.pages import layout
from app.pages.misc_common import JS_COMMON as _JS_COMMON

_BODY_BROADCAST = """
<section class="ui-section" id="denied" hidden>
  <div class="ui-empty">
    <div class="ui-empty__title">Рассылка доступна администратору</div>
    <div class="ui-empty__text" id="denied-text">Рассылка участникам — только у
      администратора: право notify.send есть у него одного. Если написать всем
      нужно вам — попросите администратора отправить сообщение.</div>
  </div>
</section>

<div id="form">
<p class="ui-small ui-muted">Шаблон подставит текст — его можно поправить до отправки.</p>

<div class="ui-chips" id="templates"></div>

<div class="ui-field">
  <label class="ui-field__label" for="text">Сообщение</label>
  <textarea class="ui-textarea" id="text" maxlength="3500"
            placeholder="Что написать людям"></textarea>
  <span class="ui-field__error" id="text-error" hidden></span>
</div>

<div class="ui-alert ui-alert--warn" id="counts">Считаю адресатов…</div>

<button class="ui-btn ui-btn--primary ui-btn--block" id="send" type="button" disabled>
  Отправить
</button>
<div class="ui-alert" id="error" hidden></div>
</div>
"""

_SCRIPT_BROADCAST = "<script>\n(function () {\n  \"use strict\";\n  window.SVOI_BACK = \"/webapp/more\";\n" + _JS_COMMON + r"""
  var $ = function (id) { return document.getElementById(id); };
  var TEMPLATES = [
    { title: "Взносы",
      text: "Напоминаю про взнос за этот месяц. Если оплатили — ответьте «оплатил», я отмечу." },
    { title: "Репетиция",
      text: "Собираемся на репетицию. Если не можете — напишите, поменяем время." },
    { title: "Показ",
      text: "Готовимся к показу. Подробности пришлю отдельно, время и место уточним." },
    { title: "Окно",
      text: "Освободилось окно для репетиции. Кому подходит — напишите." },
  ];
  var preview = null;
  var requestId = null;
  tgBoot();

  function reachable() {
    return preview ? Number(preview.reachable || 0) : 0;
  }

  function recipientsWord(count) {
    return count + " " + plural(count, "человеку", "людям", "людям");
  }

  function showError(text) {
    var box = $("error");
    box.textContent = text || "";
    box.hidden = !text;
  }

  /* 403 — не сбой сети, а закрытое право: рассылка есть только у администратора
     (право `notify.send`, db/init/057, FR-G2). Экран говорит, у кого действие
     есть, и прячет форму — иначе человек ждёт «повторите позже» вечно
     (принципы 91: 8, Б3; карта t_41a4ef6d). */
  function showDenied() {
    $("denied").hidden = false;
    $("form").hidden = true;
  }

  function deniedText() {
    return $("denied-text").textContent.replace(/\s+/g, " ").trim();
  }

  function showFieldError(text) {
    var box = $("text-error");
    box.textContent = text || "";
    box.hidden = !text;
  }

  function drawTemplates() {
    var host = $("templates");
    TEMPLATES.forEach(function (item) {
      var chip = make("button", "ui-chip", item.title);
      chip.type = "button";
      chip.addEventListener("click", function () {
        $("text").value = item.text;
        requestId = null;
        showFieldError("");
      });
      host.appendChild(chip);
    });
  }

  function drawCounts() {
    var box = $("counts");
    if (!preview) {
      box.textContent = "Не удалось посчитать адресатов. Повторите позже.";
      return;
    }
    var text = "Уйдёт " + reachable() + " из " + preview.total + " активных.";
    if (preview.no_telegram) {
      text += " Не получат: " + preview.no_telegram + " без Telegram.";
    }
    box.textContent = text;
    var button = $("send");
    button.disabled = false;
    button.textContent = "Отправить " + recipientsWord(reachable());
  }

  async function countRecipients() {
    var result = await api("/webapp/admin/broadcast/preview");
    /* 403 от счётчика — закрытое право `notify.send`, а не сеть (карта t_41a4ef6d). */
    if (result.status === 403) {
      showDenied();
      return;
    }
    if (!result.body.ok) {
      $("counts").textContent = "Не удалось посчитать адресатов. Повторите позже.";
      return;
    }
    preview = result.body;
    drawCounts();
  }

  function messageBody(text) {
    var wrapper = make("div", "ui-section");
    var card = make("div", "ui-card ui-card--flat");
    card.appendChild(make("span", "ui-card__label", "Текст сообщения"));
    var paragraph = make("p", "ui-text", text);
    paragraph.style.whiteSpace = "pre-wrap";
    card.appendChild(paragraph);
    wrapper.appendChild(card);
    wrapper.appendChild(make("p", "ui-small ui-muted",
      "Получат " + recipientsWord(reachable()) + " из " + (preview ? preview.total : 0)
      + " активных. Вернуть отправленное нельзя."));
    return wrapper;
  }

  async function send(text) {
    var button = $("send");
    button.disabled = true;
    if (!requestId) {
      requestId = String(Date.now()) + "-" + Math.random().toString(16).slice(2);
    }
    var result = await api("/webapp/admin/broadcast", {
      text: text, dry_run: false, request_id: requestId,
    });
    button.disabled = false;
    if (result.status === 403) {
      showError(deniedText());
      return;
    }
    if (result.body.ok) {
      SVOI.toast("Отправлено: " + (result.body.recipients || 0) + ".");
      button.textContent = "Отправить " + recipientsWord(reachable());
      return;
    }
    showError("Не отправилось. Проверьте сеть и повторите.");
  }

  $("text").addEventListener("input", function () {
    requestId = null;
    showFieldError("");
  });

  $("send").addEventListener("click", function () {
    showError("");
    showFieldError("");
    var text = $("text").value.trim();
    if (text.length < 3) {
      showFieldError("Напишите хотя бы пару слов.");
      $("text").focus();
      return;
    }
    if (text.length > 3500) {
      showFieldError("Длиннее 3500 символов Telegram не примет.");
      $("text").focus();
      return;
    }
    if (!preview || !reachable()) {
      showError("Адресаты ещё не посчитаны — иначе непонятно, кому уйдёт.");
      return;
    }
    SVOI.sheet({
      title: "Отправить " + recipientsWord(reachable()) + "?",
      body: messageBody(text),
      actions: [
        { label: "Отмена" },
        { label: "Отправить", primary: true, onTap: function () { send(text); } },
      ],
    });
  });

  drawTemplates();
  countRecipients();
})();
</script>"""

BROADCAST_PAGE = layout.page("Рассылка", _BODY_BROADCAST,
                             back="/webapp/more", scripts=_SCRIPT_BROADCAST)
