"""Админский экран «Архив». «Архив»: «Вернуть в активные» через подтверждение с именем.

Модуль — только своя разметка и скрипт: общее ядро (токен, запросы, строки,
листы) и каркас лежат в `admin_core.py`. Границы и права — как в `docs/development/73`.

Право раздела — `participants.manage`: то же, что проверяет сервер у `/webapp/admin/archive`
и `/webapp/admin/unarchive` (`db/init/008`). Право приходит данными — действием
`manage_rights` ответа `/webapp/admin/overview`, которое считает сервер; а сам блок
`archive` сервер отдаёт только с этим правом.
Без права (org и master — панель у них есть, `participants.manage` нет) экран не
рисует ни списка, ни поиска, а печатает ту же плашку, что видит troupe при 403, —
один словарь состояний с экранами 21–23 (`ERRORS.admin_panel_forbidden`).
До правки блок архива приходил по `participants.view` и экран показывал ушедших
людей обеим ролям (отчёт 142 §3 строка 26, принципы 91: 8, 9, Б3; карта t_c6a13266).
"""

from __future__ import annotations

from app.pages.admin_core import screen

_SCRIPT_ARCHIVE = r"""
  var archived = [];
  var shown = 20;
  var query = "";
  var canRestore = false;

  function restore(person) {
    api("/webapp/admin/unarchive", { participant_id: person.id, reason: "webapp" })
      .then(function (res) {
        if (res.status >= 200 && res.status < 300) {
          closeSheet();
          toast(person.display_name + " снова в активных");
          refresh();
        } else {
          toast(problem(res));
        }
      });
  }

  function confirmRestore(person) {
    var body = el("div", "admin-sheet");
    body.appendChild(el("p", "ui-text",
      person.display_name + " вернётся в списки и снова сможет отмечать занятость."));
    sheet("Вернуть: " + person.display_name + "?", body, [
      { label: "Отмена" },
      { label: "Вернуть", primary: true, onTap: function () { restore(person); } }
    ]);
  }

  function archiveRow(person) {
    var row = el("div", "ui-row admin-row--stack");
    var main = el("span", "ui-row__main");
    main.appendChild(el("span", "ui-row__title", person.display_name));
    var meta = [rolesText(person.roles)];
    if (person.archived_at) meta.push("в архиве с " + when(person.archived_at));
    main.appendChild(el("span", "ui-row__meta", meta.join(" · ")));
    row.appendChild(main);
    if (ACTIONS.manage_rights) {
      var actions = el("div", "ui-actions admin-actions--split");
      var back = el("button", "ui-btn ui-btn--sm", "Вернуть в активные");
      back.type = "button";
      back.onclick = function () { confirmRestore(person); };
      actions.appendChild(back);
      row.appendChild(actions);
    }
    return row;
  }

  function renderArchive() {
    var list = $("archive-list");
    clear(list);
    /* Без права списка нет вовсе: причину уже сказал статус общим словарём
       ядра. Пустое «Архив пуст» здесь читалось бы как «возвращать нечего». */
    if (!canRestore) { toggle($("archive-more"), false); return; }
    var needle = query.trim().toLowerCase();
    var visible = archived.filter(function (person) {
      return !needle || String(person.display_name || "").toLowerCase().indexOf(needle) >= 0;
    });
    $("archive-count").textContent = visible.length === archived.length
      ? archived.length + " " + plural(archived.length, "человек", "человека", "человек")
        + " в архиве"
      : "найдено " + visible.length;
    var slice = visible.slice(0, shown);
    if (!slice.length) {
      list.appendChild(empty("Архив пуст",
        "Здесь появятся те, кого отправили в архив или чью заявку отклонили."));
    }
    slice.forEach(function (person) { list.appendChild(archiveRow(person)); });
    var more = $("archive-more");
    var rest = visible.length - slice.length;
    toggle(more, rest > 0);
    more.textContent = "Показать ещё " + rest;
  }

  /* Право `participants.manage` приходит данными: действие `manage_rights` ответа
     панели — та же возможность, что проверяет сервер у возврата из архива. Роль
     на экране не решает: у troupe панель закрыта вовсе (403), у org и master
     панель есть, а права на людей нет. Поиск и счётчик — часть списка, поэтому
     гаснут вместе с ним: поле ввода над пустотой зовёт фильтровать то, чего нет
     (принцип 8, docs/development/91). */
  function applyRights(can) {
    canRestore = can === true;
    toggle($("archive-search-field"), canRestore);
    toggle($("archive-count"), canRestore);
    archived = canRestore ? ((BLOCKS.archive && BLOCKS.archive.data) || []) : [];
    renderArchive();
    status(canRestore ? "" : ERRORS.admin_panel_forbidden, !canRestore);
  }

  function refresh() {
    return auth().then(function (denied) {
      if (denied) { applyRights(false); status(denied, true); return null; }
      return loadOverview().then(function (data) {
        /* 403 панели (troupe) и сетевая ошибка: права никто не подтвердил, списка
           быть не должно. Причину `loadOverview` уже напечатал в статусе. */
        if (!data) {
          toggle($("archive-search-field"), false);
          toggle($("archive-count"), false);
          clear($("archive-list"));
          return null;
        }
        applyRights(Boolean(ACTIONS.manage_rights));
        return data;
      });
    });
  }

  $("archive-search").addEventListener("input", function (event) {
    query = event.target.value || "";
    shown = 20;
    renderArchive();
  });
  $("archive-more").addEventListener("click", function () { shown += 20; renderArchive(); });
  refresh();
"""

_BODY_ARCHIVE = """
<div class="ui-field" id="archive-search-field" hidden>
  <label class="ui-field__label" for="archive-search">Кого найти</label>
  <input class="ui-input" id="archive-search" type="search" inputmode="search"
         autocomplete="off" placeholder="Имя или фамилия">
</div>
<div class="ui-small ui-muted" id="archive-count" hidden></div>
<div class="ui-list" id="archive-list"></div>
<button class="ui-btn ui-btn--block" id="archive-more" type="button" hidden>Показать ещё</button>
<div class="ui-small ui-muted" id="status" role="status"></div>
"""

ARCHIVE_PAGE = screen("Архив", _BODY_ARCHIVE, _SCRIPT_ARCHIVE)
