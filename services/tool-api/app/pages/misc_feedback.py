"""Экран «Обратная связь». «Обратная связь»: тема, комментарий, мои сообщения.

Вложенный экран с шапкой и «‹»; возврат ведёт в `/webapp/more` (`window.SVOI_BACK`),
а не всегда на «Главную» (docs/86 §6). Общий JS — `misc_common.py`.

Форма отправки закрыта правом `feedback.send`: у мастера его нет
(`db/init/031`, `docs/development/141` §3). Право приходит полем `can_send`
в ответе `/webapp/feedback/mine` и берётся оттуда — страница не выводит его
из роли сама (принципы 91: 9, Б3; карта t_4f0c64d6). Без права формы и поля
комментария нет, состояние сказано словами: «Обращения принимает организатор».
"""

from __future__ import annotations

from app.pages import layout
from app.pages.misc_common import JS_COMMON as _JS_COMMON

_BODY_FEEDBACK = """
<p class="ui-small ui-muted">Что не работает, чего не хватает или что наоборот удобно.
Ответ придёт в чат.</p>

<section class="ui-section" id="feedback-form" hidden>
  <div class="ui-section__head"><h2 class="ui-section__title">Тема</h2></div>
  <div class="ui-section" id="kinds" role="radiogroup" aria-label="Тема обращения">
    <button class="ui-radio-row ui-radio-row--on" type="button" role="radio"
            aria-checked="true" data-kind="bug">
      <span>Не работает</span><span class="ui-radio-row__mark">выбрано</span>
    </button>
    <button class="ui-radio-row" type="button" role="radio"
            aria-checked="false" data-kind="wish">
      <span>Хочу улучшить</span><span class="ui-radio-row__mark" hidden></span>
    </button>
    <button class="ui-radio-row" type="button" role="radio"
            aria-checked="false" data-kind="wish-fl">
      <span>Просьба о доработке</span><span class="ui-radio-row__mark" hidden></span>
    </button>
    <button class="ui-radio-row" type="button" role="radio"
            aria-checked="false" data-kind="question">
      <span>Вопрос</span><span class="ui-radio-row__mark" hidden></span>
    </button>
    <button class="ui-radio-row" type="button" role="radio"
            aria-checked="false" data-kind="praise">
      <span>Хорошо работает</span><span class="ui-radio-row__mark" hidden></span>
    </button>
  </div>

  <div class="ui-field">
    <label class="ui-field__label" for="text">Комментарий</label>
    <textarea class="ui-textarea" id="text" maxlength="4000"
              placeholder="Что делали и что пошло не так"></textarea>
    <span class="ui-field__error" id="text-error" hidden></span>
  </div>

  <button class="ui-btn ui-btn--primary ui-btn--block" id="send" type="button">Отправить</button>
  <div class="ui-alert" id="error" hidden></div>
</section>

<section class="ui-section" id="feedback-denied" hidden>
  <div class="ui-empty">
    <div class="ui-empty__title">Обращения принимает организатор</div>
    <div class="ui-empty__text">Сообщить о проблеме или попросить доработку может
      организатор студии — здесь их не отправить.</div>
  </div>
</section>

<p class="ui-small ui-muted" id="feedback-status" hidden></p>

<section class="ui-section">
  <div class="ui-section__head"><h2 class="ui-section__title">Мои сообщения</h2></div>
  <div class="ui-section" id="list"></div>
</section>
"""

_SCRIPT_FEEDBACK = "<script>\n(function () {\n  \"use strict\";\n  window.SVOI_BACK = \"/webapp/more\";\n" + _JS_COMMON + r"""
  var $ = function (id) { return document.getElementById(id); };
  var KIND = { bug: "Проблема", wish: "Пожелание", "wish-fl": "Просьба о доработке",
               question: "Вопрос", praise: "Хорошо" };
  var STATUS = {
    new: "у организаторов", seen: "посмотрели", planned: "в плане",
    done: "сделано", rejected: "отклонено",
  };
  var kind = "bug";
  var canSend = false;
  tgBoot();

  function setKind(next) {
    kind = next;
    Array.prototype.forEach.call(document.querySelectorAll("#kinds .ui-radio-row"),
      function (button) {
        var on = button.getAttribute("data-kind") === next;
        button.classList.toggle("ui-radio-row--on", on);
        button.setAttribute("aria-checked", on ? "true" : "false");
        var mark = button.querySelector(".ui-radio-row__mark");
        mark.textContent = on ? "выбрано" : "";
        mark.hidden = !on;
      });
  }

  document.getElementById("kinds").addEventListener("click", function (event) {
    var button = event.target.closest(".ui-radio-row");
    if (button) { setKind(button.getAttribute("data-kind")); }
  });

  /* Право `feedback.send` приходит полем `can_send` в ответе данных: у мастера
     его нет (db/init/031, docs/141 §3). Форма отдаётся скрытой и показывается
     только по явному «да» сервера — человек без права не заполняет обращение,
     которое сервер отклонит (принципы 91: 9, Б3; карта t_4f0c64d6). */
  function applyRights(allowed) {
    canSend = allowed === true;
    $("feedback-form").hidden = !canSend;
    $("feedback-denied").hidden = canSend;
    $("feedback-status").hidden = true;
  }

  function showStatus(text) {
    $("feedback-status").textContent = text;
    $("feedback-status").hidden = false;
  }

  function itemCard(item) {
    var card = make("div", "ui-card");
    var when = humanDate(String(item.created_at || "").slice(0, 10));
    var meta = [KIND[item.kind] || "Сообщение", STATUS[item.status] || "принято", when]
      .filter(Boolean).join(" · ");
    card.appendChild(make("span", "ui-card__label", meta));
    card.appendChild(make("p", "ui-text", item.text || ""));
    if (item.resolution) {
      card.appendChild(make("hr", "ui-divider"));
      card.appendChild(make("p", "ui-small ui-muted", "Ответ: " + item.resolution));
    }
    return card;
  }

  function renderList(items) {
    var host = $("list");
    host.textContent = "";
    if (!items.length) {
      var empty = make("div", "ui-card ui-card--flat");
      empty.appendChild(make("p", "ui-small ui-muted",
        "Пока пусто. Первое сообщение появится здесь после отправки."));
      host.appendChild(empty);
      return;
    }
    items.forEach(function (item) { host.appendChild(itemCard(item)); });
  }

  async function load() {
    var result = await api("/webapp/feedback/mine");
    var body = result.body || {};
    if (result.status === 200 && body.ok) {
      renderList(body.data || []);
      if (typeof body.can_send === "boolean") { applyRights(body.can_send); }
      else { showStatus("Не удалось проверить, можно ли отправить обращение. Потяните экран вниз, чтобы повторить."); }
      return;
    }
    if (result.status === 401) {
      showStatus("Откройте приложение из бота — сессия не подтверждена.");
      return;
    }
    showStatus("Не удалось проверить доступ. Потяните экран вниз, чтобы повторить.");
  }

  $("send").addEventListener("click", async function () {
    var field = $("text");
    var error = $("text-error");
    if (!canSend) { return; }
    var text = field.value.trim();
    error.hidden = true;
    $("error").hidden = true;
    if (text.length < 3) {
      error.textContent = "Напишите пару слов — без текста не разобраться.";
      error.hidden = false;
      field.focus();
      return;
    }

    var button = $("send");
    button.disabled = true;
    var result = await api("/webapp/feedback", {
      kind: kind,
      text: text,
      context: { screen: "webapp/feedback" },
    });
    button.disabled = false;
    if (result.status === 200) {
      field.value = "";
      SVOI.toast("Спасибо, отправлено.");
      load();
      return;
    }
    /* 403 — это право, а не сеть: права могли снять после открытия экрана. */
    if (result.status === 403) { applyRights(false); return; }
    var box = $("error");
    box.textContent = "Не отправилось. Проверьте сеть и повторите.";
    box.hidden = false;
  });

  load();
})();
</script>"""

FEEDBACK_PAGE = layout.page("Обратная связь", _BODY_FEEDBACK,
                            back="/webapp/more", scripts=_SCRIPT_FEEDBACK)
