"""Админский экран «Разбор агента» (A-5, docs/development/108 §6).

Зачем: по ответу агента в чате видно только текст. Здесь лежат последние сессии —
вопрос, шаги с именами инструментов, ответ, время и цена (шаги, токены, мс).
Без этого разбор «почему бот сказал чепуху» превращается в гадание.

Право — `admin.panel`: журнал хранит тексты вопросов людей, поэтому раздел не открыт
ни организатору, ни роли с `audit.view`. Проверка на сервере (`GET /webapp/admin/agent`),
страница лишь не рисует чужого.

Модуль — только своя разметка и скрипт: общее ядро (вход, запросы, строки) в
`admin_core.py`. Аргументы инструментов приходят уже обезличенными (`app/masking.py`).
"""

from __future__ import annotations

from app.pages.admin_core import screen

_SCRIPT_AGENT = r"""
  var sessions = [];

  var STATUS_RU = { answer: "ответ", proposal: "предложение", refusal: "отказ" };

  function agentStatusWord(code) { return STATUS_RU[code] || code || "неизвестно"; }

  function stepText(step) {
    var parts = [String(step.step || "") + ". " + (step.tool || "инструмент")];
    if (step.latency_ms !== undefined && step.latency_ms !== null) {
      parts.push(step.latency_ms + " мс");
    }
    if (step.ok === false) parts.push("ошибка" + (step.error ? ": " + step.error : ""));
    return parts.join(" · ");
  }

  function metaText(session) {
    var parts = [when(session.ts, true)];
    parts.push(agentStatusWord(session.status));
    parts.push(session.step_count + " " + plural(session.step_count, "шаг", "шага", "шагов"));
    if (session.tokens) parts.push(session.tokens + " токенов");
    if (session.latency_ms) parts.push(session.latency_ms + " мс");
    return parts.filter(Boolean).join(" · ");
  }

  function openSession(session) {
    var body = el("div", "admin-sheet");
    var questions = el("div", "ui-dl");
    pair(questions, "Вопрос", session.question || "—");
    pair(questions, "Ответ", session.answer || "агент не ответил");
    pair(questions, "Итог", agentStatusWord(session.status));
    pair(questions, "Время", when(session.ts, true));
    body.appendChild(questions);
    var list = el("div", "ui-list");
    (session.steps || []).slice().forEach(function (step) {
      var row = el("div", "ui-row");
      var main = el("span", "ui-row__main");
      main.appendChild(el("span", "ui-row__title", step.tool || "инструмент"));
      main.appendChild(el("span", "ui-row__meta", stepText(step)));
      if (step.args && Object.keys(step.args).length) {
        main.appendChild(el("span", "ui-row__meta", JSON.stringify(step.args)));
      }
      row.appendChild(main);
      list.appendChild(row);
    });
    if (!list.firstChild) {
      list.appendChild(empty("Инструменты не вызывались",
        "Ответ дан без фактов студии."));
    }
    body.appendChild(list);
    sheet("Сессия " + when(session.ts, true), body);
  }

  function sessionRow(session) {
    var row = el("div", "ui-row admin-row--stack");
    var main = el("span", "ui-row__main");
    main.appendChild(el("span", "ui-row__title", session.question || "(пустой вопрос)"));
    main.appendChild(el("span", "ui-row__meta", metaText(session)));
    (session.steps || []).forEach(function (step) {
      main.appendChild(el("span", "ui-row__meta", stepText(step)));
    });
    row.appendChild(main);
    row.addEventListener("click", function () { openSession(session); });
    return row;
  }

  function render() {
    var list = $("agent-list");
    clear(list);
    if (!sessions.length) {
      list.appendChild(empty("Сессий пока нет",
        "Здесь появятся вопросы агента: шаги, инструменты и цена ответа."));
      return;
    }
    sessions.forEach(function (session) { list.appendChild(sessionRow(session)); });
  }

  function refresh() {
    return auth().then(function (denied) {
      if (denied) { status(denied, true); return null; }
      return api("/webapp/admin/agent?limit=50").then(function (res) {
        if (res.status !== 200) { status(problem(res), true); return null; }
        sessions = res.body.sessions || [];
        render();
        status(sessions.length
          ? "Последние " + sessions.length + " " + plural(sessions.length, "сессия", "сессии", "сессий")
          : "");
        return res.body;
      });
    });
  }

  refresh();
"""

_BODY_AGENT = """
<div class="ui-small ui-muted" id="status" role="status"></div>
<div class="ui-list" id="agent-list"></div>
"""

AGENT_PAGE = screen("Разбор агента", _BODY_AGENT, _SCRIPT_AGENT)
