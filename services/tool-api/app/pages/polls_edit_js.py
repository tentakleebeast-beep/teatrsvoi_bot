"""Скрипт конструктора опроса (E8). Вынесен из `polls_edit.py`: модуль страницы ≤ 400 строк.

Правила, которые держит скрипт:
* черновик сохраняется целиком (`POST /webapp/polls/save`), сервер проверяет типы;
* шаг адресатов считает «уйдёт N» до отправки — как у рассылки, а не после;
* после отправки всё заморожено: кнопки правки исчезают, а не отказывают позже (Б3).
"""

from __future__ import annotations

from app.pages.misc_common import JS_COMMON as _JS_COMMON
from app.pages.polls_audience_js import AUDIENCE_JS as _AUDIENCE_JS

SCRIPT = "<script>\n(function () {\n  \"use strict\";\n" + _JS_COMMON + r"""
  var $ = function (id) { return document.getElementById(id); };
  var KIND_TITLE = { single: "один из списка", multi: "несколько", yesno: "да/нет",
                     number: "число", text: "текст" };
  var OPTION_KINDS = { single: 1, multi: 1 };
  var state = { poll_id: null, title: "", intro: null, anonymous: false, closes_at: null,
                questions: [], rules: [], include: [], exclude: [],
                viewers: { people: [], roles: [] },
                people: [], projects: [], frozen: false, editing: -1, shown: 8,
                dirty: false };
  var EXIT_TEXT = "Выйти без сохранения? Черновик не сохранён.";
  var DELETE_TEXT = "Удалить черновик? Вернуть его будет нельзя.";
  var COPY_NOTE = "Копия сохранена как черновик. Адресаты не перенесены: " +
                  "выберите их заново.";
  tgBoot();

  function showError(text) {
    var box = $("error");
    box.textContent = text || "";
    box.hidden = !text;
  }

  function showNote(text) {
    var box = $("note");
    box.textContent = text || "";
    box.hidden = !text;
  }

  function showDenied() {
    $("denied").hidden = false;
    $("form").hidden = true;
  }

  function showForm() {
    $("form").hidden = false;
  }

  function detailOf(result) {
    var body = result.body || {};
    var text = body.detail || body.message || "";
    return text || "Не получилось. Повторите позже.";
  }

  /* «Есть несохранённые правки»: ставится там же, где меняется state, и снимается
     после успешного сохранения. Стрелка «‹» по флагу спрашивает, а не теряет ввод
     молча (166 §1 D1). Автосохранения нет намеренно: в WebView нет надёжного
     события «ухожу», а неудавшийся запрос при уходе — та же тихая потеря. */
  function markDirty() { state.dirty = true; }

  function pollIdFromUrl() {
    var match = /[?&]poll_id=(\d+)/.exec(window.location.search || "");
    return match ? Number(match[1]) : null;
  }

  /* ------------------------------------------------------------ вопросы */

  function renderQuestions() {
    var host = $("questions");
    host.textContent = "";
    state.questions.forEach(function (item, index) {
      var row = make("button", "ui-row ui-row--compact");
      row.type = "button";
      var main = make("span", "ui-row__main");
      main.appendChild(make("span", "ui-row__title", item.text || "Без текста"));
      var meta = (item.required ? "обязательный" : "можно пропустить");
      if (item.options && item.options.length) {
        meta += " · вариантов " + item.options.length;
      }
      main.appendChild(make("span", "ui-row__meta", meta));
      row.appendChild(main);
      row.appendChild(make("span", "ui-row__value", KIND_TITLE[item.kind] || item.kind));
      row.addEventListener("click", function () { openEditor(index); });
      host.appendChild(row);
    });
  }

  function openEditor(index) {
    state.editing = index;
    var item = state.questions[index];
    $("q-text").value = item.text || "";
    $("q-kind").value = item.kind || "single";
    $("q-required").checked = item.required !== false;
    $("q-free").checked = !!item.free_text;
    $("q-options").value = (item.options || []).map(function (option) {
      return option.label;
    }).join("\n");
    syncKindFields();
    $("editor").hidden = false;
    $("q-text").focus();
  }

  function syncKindFields() {
    var list = OPTION_KINDS[$("q-kind").value] === 1;
    $("options-field").hidden = !list;
    $("free-field").hidden = !list;
    if (!list) { $("q-free").checked = false; }
  }

  function readEditor() {
    var kind = $("q-kind").value;
    var list = OPTION_KINDS[kind] === 1;
    var labels = list ? $("q-options").value.split("\n").map(function (line) {
      return line.trim();
    }).filter(function (line) { return line.length > 0; }) : [];
    return { position: state.editing + 1, text: $("q-text").value.trim(), kind: kind,
             required: $("q-required").checked, free_text: list && $("q-free").checked,
             max_choices: null,
             options: labels.map(function (label) { return { label: label }; }) };
  }

  function editorDone() {
    var item = readEditor();
    if (!item.text) { showError("У вопроса нет текста."); return; }
    if (OPTION_KINDS[item.kind] === 1 && !item.options.length) {
      showError("У вопроса со списком нужен хотя бы один вариант."); return;
    }
    showError("");
    state.questions[state.editing] = item;
    markDirty();
    renumber();
    renderQuestions();
    $("editor").hidden = true;
  }

  function renumber() {
    state.questions.forEach(function (item, index) { item.position = index + 1; });
  }

  function addQuestion() {
    if ($("editor").hidden === false) { editorDone(); }
    state.questions.push({ position: state.questions.length + 1, text: "", kind: "single",
                           required: true, free_text: false, max_choices: null, options: [] });
    markDirty();
    renderQuestions();
    openEditor(state.questions.length - 1);
  }

  function deleteQuestion() {
    if (state.editing < 0) { return; }
    state.questions.splice(state.editing, 1);
    state.editing = -1;
    markDirty();
    renumber();
    renderQuestions();
    $("editor").hidden = true;
  }

  /* ------------------------------------------------------------ сохранение */

  function payload() {
    return { poll_id: state.poll_id, title: $("title").value.trim(),
             intro: state.intro, anonymous: state.anonymous, closes_at: state.closes_at,
             questions: state.questions.map(function (item) {
               return { position: item.position, text: item.text, kind: item.kind,
                        required: item.required, free_text: item.free_text,
                        max_choices: item.max_choices,
                        options: (item.options || []).map(function (option) {
                          return { label: option.label };
                        }) };
             }) };
  }

  async function saveDraft() {
    if (!$("editor").hidden) { editorDone(); }
    if (!$("title").value.trim()) { showError("У опроса нет темы."); return false; }
    if (!state.questions.length) { showError("В опросе нет ни одного вопроса."); return false; }
    var result = await api("/webapp/polls/save", payload());
    if (result.status === 403) { showDenied(); return false; }
    if (result.status !== 200) {
      var body = result.body || {};
      showError(body.code === "poll_running"
        ? "Опрос уже отправлен: правка вопросов запрещена."
        : detailOf(result));
      return false;
    }
    state.poll_id = result.body.poll_id;
    state.dirty = false;
    syncDelete();
    showError("");
    return true;
  }

  /* «Сохранить черновик»: сохраняет и остаётся на месте (166 §1 D1). Повторное
     нажатие правит тот же опрос — `state.poll_id` уже заполнен ответом. */
  async function saveDraftOnly() {
    var button = $("save-draft");
    button.disabled = true;
    var ok = await saveDraft();
    button.disabled = false;
    if (ok) { showNote("Черновик сохранён"); }
  }

  /* Кнопка «Удалить черновик» появляется только у сохранённого черновика: у нового
     удалять нечего, у отправленного — нельзя (есть ответы), поэтому у замороженного
     её нет вовсе, а не «нажми и получи отказ». */
  function syncDelete() {
    $("delete-draft").hidden = !state.poll_id || state.frozen;
  }

  function tgConfirm(text) {
    return new Promise(function (resolve) {
      var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
      if (tg && tg.showConfirm) {
        tg.showConfirm(text, function (ok) { resolve(!!ok); });
        return;
      }
      resolve(window.confirm(text));
    });
  }

  async function deleteDraft() {
    if (!state.poll_id) { showError("Черновик ещё не сохранён: удалять нечего."); return; }
    if (!(await tgConfirm(DELETE_TEXT))) { return; }
    var button = $("delete-draft");
    button.disabled = true;
    var result = await api("/webapp/polls/delete", { poll_id: state.poll_id });
    button.disabled = false;
    if (result.status === 403) { showDenied(); return; }
    if (result.status !== 200) {
      var body = result.body || {};
      showError(body.code === "poll_not_draft"
        ? "Отправленный опрос не удаляют: в нём есть ответы."
        : detailOf(result));
      return;
    }
    state.dirty = false;
    window.location.href = "/webapp/polls";
  }""" + _AUDIENCE_JS + r"""
  /* ------------------------------------------------------------ шаги */

  function showStep(name) {
    $("step-questions").hidden = name !== "questions";
    $("step-audience").hidden = name !== "audience";
    window.SVOI_STEP = name;
  }

  async function toAudience() {
    if (state.frozen) { showError("Опрос отправлен: вопросы заморожены."); return; }
    if (!(await saveDraft())) { return; }
    await loadAudience();
    showStep("audience");
  }

  async function send() {
    var saved = await api("/webapp/polls/start", { poll_id: state.poll_id });
    if (saved.status === 403) { showDenied(); return; }
    if (saved.status !== 200) { showError(detailOf(saved)); return; }
    window.location.href = "/webapp/polls/results?poll_id=" + state.poll_id;
  }

  /* ------------------------------------------------------------ старт */

  function bind() {
    $("add-question").addEventListener("click", addQuestion);
    $("q-kind").addEventListener("change", syncKindFields);
    $("q-done").addEventListener("click", editorDone);
    $("q-delete").addEventListener("click", deleteQuestion);
    $("save-draft").addEventListener("click", saveDraftOnly);
    $("delete-draft").addEventListener("click", deleteDraft);
    $("title").addEventListener("input", markDirty);
    bindExit();
    $("to-audience").addEventListener("click", toAudience);
    $("back-to-questions").addEventListener("click", async function () {
      showStep("questions");
      if (state.poll_id) {
        var result = await api("/webapp/polls/edit/data?poll_id=" + state.poll_id);
        if (result.status === 200) { state.frozen = !result.body.can_edit; }
      }
    });
    $("send").addEventListener("click", send);
    $("people-all").addEventListener("click", function () {
      state.shown = state.people.length;
      drawPeople();
    });
    $("exclude").addEventListener("change", countAudience);
    $("viewers").addEventListener("change", function () { readSelections(); });
    Array.prototype.forEach.call($("rule-chips").children, function (chip) {
      chip.addEventListener("click", function () {
        toggleRule(chip.getAttribute("data-rule"));
      });
    });
    $("rule-project").addEventListener("change", function () {
      var value = $("rule-project").value;
      state.rules = state.rules.filter(function (rule) { return rule.kind !== "project"; });
      if (value) { toggleRule("project:" + value); } else { drawRules(); countAudience(); }
    });
  }

  /* Стрелка «‹» значит «выйти»: она не сохраняет и не блокирует, но и не теряет
     ввод молча — при несохранённых правках спрашивает нативным диалогом Telegram.
     В браузере стенда хоста нет: без `Telegram.WebApp` уход без вопроса (166 §1). */
  function bindExit() {
    var back = document.querySelector("a.ui-appbar__back");
    if (!back) { return; }
    back.addEventListener("click", function (event) {
      if (!state.dirty) { return; }
      var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
      if (!tg || !tg.showConfirm) { return; }
      event.preventDefault();
      tg.showConfirm(EXIT_TEXT, function (ok) {
        if (ok) { window.location.href = back.getAttribute("href"); }
      });
    });
  }

  /* Право `poll.manage` до отрисовки конструктора: у нового опроса нет своего
     запроса данных, поэтому спрашиваем тот же список, что и экран «Опросы».
     Без этого экран открывался любой роли, а отказ приходил только при сохранении. */
  async function canManage() {
    var result = await api("/webapp/polls/list");
    if (result.status === 403) { showDenied(); return false; }
    if (result.status !== 200) { showForm(); showError(detailOf(result)); return false; }
    return true;
  }

  async function init() {
    bind();
    var pollId = pollIdFromUrl();
    if (!pollId) {
      if (!(await canManage())) { return; }
      showForm();
      addQuestion();
      state.dirty = false;
      return;
    }
    var result = await api("/webapp/polls/edit/data?poll_id=" + pollId);
    if (result.status === 403) { showDenied(); return; }
    if (result.status !== 200) { showForm(); showError(detailOf(result)); return; }
    showForm();
    var poll = result.body.poll;
    state.poll_id = poll.id;
    state.title = poll.title;
    state.intro = poll.intro;
    state.anonymous = !!poll.anonymous;
    state.closes_at = poll.closes_at;
    state.questions = result.body.questions || [];
    state.frozen = !result.body.can_edit;
    $("title").value = state.title || "";
    renderQuestions();
    syncDelete();
    if (state.frozen) {
      showNote("Опрос уже отправлен: вопросы и адресаты заморожены.");
      $("add-question").hidden = true;
    }
    /* Метка `note=copy` — только показ полосы: правил она не меняет, и по прямой
       ссылке без метки полосы нет (166 §1 D5, §5.6). */
    if (/[?&]note=copy/.test(window.location.search || "")) { showNote(COPY_NOTE); }
    if (/[#&]audience/.test(window.location.href)) {
      await loadAudience();
      showStep("audience");
    }
  }

  init();
})();
</script>
"""
