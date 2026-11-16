"""Экран вопроса: прохождение опроса в Mini App (E8-D4, docs/development/164 §12.5).

Один шаг — один экран. Адрес при переходе между вопросами не меняется: `position`
живёт в `poll_run.position`, поэтому перезапуск приложения возвращает человека на
тот же вопрос с прежними ответами (AC-17).

Логика шага здесь не повторяется: экран только рисует форму шага и отправляет
действие (`o`/`n`/`y`/`x`/`text`/`b`/`s`) на `/webapp/polls/answer`, где работает
тот же `_apply_answer`, что и у чата. Второй обработчик ответа означал бы два
поведения на один опрос.

Что важно в разметке:

* загрузка — скелет строки вопроса, а не пустой экран;
* `single`/`multi` — крупные кнопки (`ui-btn--block`, ≥ 44 px), выбор помечен
  `ui-btn--primary`; у `multi` ниже «Дальше»;
* `number` при неверном вводе печатает «Не понял число…» под полем и остаётся на
  том же вопросе;
* 5xx и обрыв — баннер оболочки, экран своё сообщение не дублирует.
"""

from __future__ import annotations

from app.pages import layout
from app.pages.misc_common import JS_COMMON as _JS_COMMON

_BODY = """
<div id="loading">
  <div class="ui-small ui-muted"><span class="ui-skeleton ui-skeleton--line-text"
    style="width:45%" aria-hidden="true"></span></div>
  <h2 class="ui-h2"><span class="ui-skeleton ui-skeleton--line-title"
    style="width:80%" aria-hidden="true"></span></h2>
  <div class="ui-list">
    <div class="ui-row"><span class="ui-row__main"><span
      class="ui-skeleton ui-skeleton--line-text" style="width:60%"
      aria-hidden="true"></span></span></div>
    <div class="ui-row"><span class="ui-row__main"><span
      class="ui-skeleton ui-skeleton--line-text" style="width:50%"
      aria-hidden="true"></span></span></div>
  </div>
</div>

<div id="step" hidden></div>

<div id="notfound" hidden>
  <div class="ui-empty">
    <div class="ui-empty__title">Прохождение не найдено.</div>
    <div class="ui-empty__text">Опрос мог быть закрыт или назначен другому человеку.</div>
  </div>
  <a class="ui-btn ui-btn--block" href="/webapp/polls/mine">К списку</a>
</div>

<div class="ui-alert" id="error" hidden></div>
"""

_SCRIPT = "<script>\n(function () {\n  \"use strict\";\n" + _JS_COMMON + r"""
  var $ = function (id) { return document.getElementById(id); };
  tgBoot();

  var RUN_ID = Number(new URLSearchParams(location.search).get("run_id") || 0);
  var step = null;
  var sending = false;

  function lock(value) {
    // Двойного «Дальше» быть не должно: пока ответ не пришёл, кнопки выключены.
    var buttons = $("step").querySelectorAll("button");
    for (var i = 0; i < buttons.length; i += 1) { buttons[i].disabled = value; }
  }

  function showError(text) {
    var box = $("error");
    box.textContent = text || "";
    box.hidden = !text;
  }

  function showNotFound() {
    $("loading").hidden = true;
    $("step").hidden = true;
    $("notfound").hidden = false;
  }

  function toList() {
    var link = make("a", "ui-btn ui-btn--block", "К списку");
    link.href = "/webapp/polls/mine";
    return link;
  }

  function button(text, extra, action) {
    var node = make("button", "ui-btn ui-btn--block" + (extra ? " " + extra : ""), text);
    node.type = "button";
    node.onclick = function () { answer(action); };
    return node;
  }

  function choiceBlock() {
    var host = make("div");
    var list = make("div", "ui-actions");
    (step.options || []).forEach(function (option) {
      var on = (step.chosen || []).indexOf(option.code) >= 0;
      list.appendChild(button(option.label, on ? "ui-btn--primary" : "",
        { action: "o", code: option.code }));
    });
    if (step.kind === "multi") {
      list.appendChild(button("Дальше", "ui-btn--primary", { action: "n" }));
    }
    host.appendChild(list);
    if (step.free_text) {
      // Свободный ответ у вопроса со списком: поле открывается по просьбе, а не
      // висит под вариантами всегда (мокап 03-chat-question).
      var other = make("button", "ui-btn ui-btn--ghost ui-btn--block", "Другое — напишу");
      other.type = "button";
      other.onclick = function () { host.replaceChild(textField(step.chosen_text), list); };
      host.appendChild(other);
    }
    return host;
  }

  function yesnoBlock() {
    var list = make("div", "ui-actions");
    [["y", "Да"], ["x", "Нет"]].forEach(function (pair) {
      var on = (step.chosen || []).indexOf(pair[0]) >= 0;
      list.appendChild(button(pair[1], on ? "ui-btn--primary" : "",
        { action: pair[0] }));
    });
    return list;
  }

  function textField(initial) {
    var host = make("div");
    var field = make("div", "ui-field");
    var area = make("textarea", "ui-textarea");
    area.id = "answer-text";
    area.maxLength = 2000;
    area.value = initial || "";
    field.appendChild(area);
    host.appendChild(field);
    var send = make("button", "ui-btn ui-btn--primary ui-btn--block", "Ответить");
    send.type = "button";
    send.onclick = function () { answer({ action: "text", text: area.value }); };
    host.appendChild(send);
    return host;
  }

  function numberField() {
    var host = make("div");
    var field = make("div", "ui-field");
    var input = make("input", "ui-input ui-input--amount");
    input.type = "text";
    input.inputMode = "decimal";
    input.id = "answer-number";
    if (step.number_value !== null && step.number_value !== undefined) {
      input.value = String(step.number_value);
    }
    field.appendChild(input);
    var retry = make("span", "ui-field__error");
    retry.id = "answer-retry";
    retry.hidden = true;
    field.appendChild(retry);
    host.appendChild(field);
    var send = make("button", "ui-btn ui-btn--primary ui-btn--block", "Ответить");
    send.type = "button";
    send.onclick = function () { answer({ action: "text", text: input.value }); };
    host.appendChild(send);
    return host;
  }

  function navBlock() {
    var row = make("div", "ui-actions ui-actions--bottom");
    if (step.position > 1) { row.appendChild(button("Назад", "", { action: "b" })); }
    // «Пропустить» есть только у необязательного вопроса: обязательный сервер
    // всё равно отклонит, и кнопка вела бы в отказ (AC-16).
    if (!step.required) { row.appendChild(button("Пропустить", "", { action: "s" })); }
    return row;
  }

  function controls() {
    var host = make("div");
    if (step.kind === "single" || step.kind === "multi") { host.appendChild(choiceBlock()); }
    else if (step.kind === "yesno") { host.appendChild(yesnoBlock()); }
    else if (step.kind === "number") { host.appendChild(numberField()); }
    else { host.appendChild(textField(step.chosen_text)); }
    var nav = navBlock();
    if (nav.childNodes.length) { host.appendChild(nav); }
    return host;
  }

  function prepare(host) {
    host.textContent = "";
    host.hidden = false;
    $("loading").hidden = true;
    $("notfound").hidden = true;
    showError("");
  }

  function showStep(next) {
    step = next;
    var host = $("step");
    prepare(host);
    var title = (next.title ? next.title + " · " : "") +
      "вопрос " + next.position + " из " + next.total;
    host.appendChild(make("div", "ui-small ui-muted", title));
    host.appendChild(make("h2", "ui-h2", next.text || ""));
    host.appendChild(controls());
  }

  function showDone(next) {
    step = next;
    var host = $("step");
    prepare(host);
    host.appendChild(make("div", "ui-small ui-muted", next.title || ""));
    host.appendChild(make("h2", "ui-h2",
      "Ответы сохранены — " + next.position + " из " + next.total));
    host.appendChild(toList());
  }

  function showClosed(next) {
    step = next;
    var host = $("step");
    prepare(host);
    // Дата — когда опрос закрыли; у закрытого по сроку её место занимает срок.
    var when = humanDate(next.closed_at || next.closes_at);
    host.appendChild(make("h2", "ui-h2", when
      ? "Опрос закрыт " + when + ", ответы больше не принимаются"
      : "Опрос закрыт, ответы больше не принимаются"));
    host.appendChild(toList());
  }

  function showRetry(message) {
    // Вопрос не меняется: человек исправляет ввод в том же поле.
    var box = $("answer-retry");
    if (box) {
      box.textContent = message || "Не понял число. Например: 4";
      box.hidden = false;
    } else {
      showError(message || "");
    }
  }

  async function answer(payload) {
    if (sending || !step) { return; }
    sending = true;
    lock(true);
    payload.run_id = RUN_ID;
    payload.position = step.position;
    var result = await api("/webapp/polls/answer", payload);
    sending = false;
    if (!result) { return; }
    lock(false);
    // 5xx и обрыв: сообщение даёт баннер оболочки, экран своё не дублирует.
    if (result.status === 0 || result.status >= 500) { return; }
    if (result.status === 404) { showNotFound(); return; }
    var body = result.body || {};
    if (result.status !== 200 || body.ok !== true) {
      showError(body.detail || body.message || "Не удалось сохранить ответ.");
      return;
    }
    if (body.retry) { showRetry(body.message); return; }
    if (body.closed) { showClosed(body.step); return; }
    if (body.done) { showDone(body.step); return; }
    showStep(body.step);
  }

  async function load() {
    if (!RUN_ID) { showNotFound(); return; }
    var result = await api("/webapp/polls/step?run_id=" + RUN_ID);
    if (!result) { return; }
    if (result.status === 0 || result.status >= 500) { return; }
    if (result.status === 404) { showNotFound(); return; }
    var body = result.body || {};
    if (result.status !== 200 || body.ok !== true) {
      showError("Не удалось загрузить вопрос. Потяните экран вниз, чтобы повторить.");
      return;
    }
    var next = body.step;
    if (next.done) { showDone(next); return; }
    if (next.closed) { showClosed(next); return; }
    showStep(next);
  }

  load();
})();
</script>
"""

POLLS_ANSWER_PAGE = layout.page("Опрос", _BODY, back="/webapp/polls/mine", caps=(),
                                scripts=_SCRIPT)
