"""Админский экран «Аудит». «Аудит»: строка «время · кто · что», страницы по 20.

Модуль — только своя разметка и скрипт: общее ядро (токен, запросы, строки,
листы) и каркас лежат в `admin_core.py`. Границы и права — как в `docs/development/73`.
"""

from __future__ import annotations

from app.pages.admin_core import screen

_SCRIPT_AUDIT = r"""
  var events = [];
  var shown = 20;

  var ACTIONS_RU = {
    "participant.approve": "Заявка принята",
    "participant.reject": "Заявка отклонена",
    "participant.archive": "Человек в архиве",
    "participant.unarchive": "Человек возвращён",
    "rbac.role.grant": "Роль выдана",
    "rbac.role.revoke": "Роль отозвана",
    "rbac.capability.grant": "Право выдано",
    "rbac.capability.revoke": "Право отозвано",
    "dues.tariff.set": "Тариф изменён",
    "budget.sheet.retry": "Повтор записи в лист",
    "invite.create": "Код выпущен",
    "invite.revoke": "Код отозван",
    "access.denied": "Отказано в доступе"
  };

  var ENTITY = { participant: "человек", invite_code: "код приглашения",
                 budget_tx: "операция", feedback: "обращение", event_plan: "событие",
                 capability: "право", role: "роль" };

  // Решение 30.10.2026: подтверждение крупных сумм убрано, новые события
  // budget.approval.* не пишутся. Но в журнале прода остались старые строки
  // (проверено 04.11.2026, read-only: requested — 1, rejected — 1, approved — 0),
  // поэтому все они показываются одной пометкой «в архиве», а не сырым кодом.
  var LEGACY_APPROVAL = "budget.approval.";

  function actionWord(code) {
    if (ACTIONS_RU[code]) return ACTIONS_RU[code];
    if (String(code || "").indexOf(LEGACY_APPROVAL) === 0) return "Крупная сумма (архив)";
    return String(code || "событие").replace(/[._]/g, " ");
  }

  function auditRow(event) {
    var row = el("div", "ui-row");
    var main = el("span", "ui-row__main");
    main.appendChild(el("span", "ui-row__title", actionWord(event.action)));
    var who = event.actor_name || (event.actor_type === "human" ? "человек" : event.actor_type);
    var meta = [when(event.ts, true), who];
    main.appendChild(el("span", "ui-row__meta", meta.filter(Boolean).join(" · ")));
    if (event.entity_type) {
      main.appendChild(el("span", "ui-row__meta",
        (ENTITY[event.entity_type] || event.entity_type)
        + (event.entity_id ? " №" + event.entity_id : "")));
    }
    row.appendChild(main);
    return row;
  }

  function renderAudit() {
    var list = $("audit-list");
    clear(list);
    var slice = events.slice(0, shown);
    if (!slice.length) {
      list.appendChild(empty("Событий нет",
        "Здесь появятся действия: заявки, роли, тарифы, суммы."));
    }
    slice.forEach(function (event) { list.appendChild(auditRow(event)); });
    var more = $("audit-more");
    var rest = events.length - slice.length;
    toggle(more, rest > 0);
    more.textContent = "Показать ещё " + rest;
  }

  // Отказ по праву: журнала нет вовсе — ни строк, ни подписи «без записей».
  function noAccess() {
    clear(document.getElementById("audit-list"));
    toggle(document.getElementById("audit-more"), false);
    status(ERRORS.admin_panel_forbidden, true);
  }

  function refresh() {
    return auth().then(function (denied) {
      if (denied) { status(denied, true); return null; }
      return loadOverview().then(function (data) {
        if (!data) return null;
        // Право проверяется до чтения журнала: `audit.view` есть только у
        // администратора, и у org/master блок `audit` в ответе панели не приходит.
        // Экран читал отсутствующий блок как пустой список и писал «Событий нет» —
        // как будто журнал пуст, а не закрыт (принцип 9, Б3 docs/development/91).
        // Слова — из общего словаря ядра: те же, что у troupe при 403.
        if ((data.allowed || []).indexOf("audit") === -1) {
          noAccess();
          return data;
        }
        // overview отдаёт раздел как { data: [...] } — раньше страница падала
        // с «events.slice is not a function», когда приходил объект.
        var audit = BLOCKS.audit;
        events = Array.isArray(audit) ? audit : (audit && audit.data) || [];
        renderAudit();
        status(events.length ? "Последние " + events.length + " "
          + plural(events.length, "запись", "записи", "записей") : "");
        return data;
      });
    });
  }

  $("audit-more").addEventListener("click", function () { shown += 20; renderAudit(); });
  refresh();
"""

_BODY_AUDIT = """
<div class="ui-small ui-muted" id="status" role="status"></div>
<div class="ui-list" id="audit-list"></div>
<button class="ui-btn ui-btn--block" id="audit-more" type="button" hidden>Показать ещё</button>
"""

AUDIT_PAGE = screen("Аудит", _BODY_AUDIT, _SCRIPT_AUDIT)
