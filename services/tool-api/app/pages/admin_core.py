"""Общее ядро админских экранов Mini App (docs/development/86 §5).

Один раздел — один модуль (`admin_people.py`, `admin_pending.py`, …), а здесь то,
что у них общее: собственные строки стилей дизайн-системы и ядро страницы —
токен, запросы, слова состояний, строки и листы. До 31.10.2026 всё это лежало
в одном `admin.py` на 1251 строку.

`api()` и `auth()` приходят из `static/webapp-shell.js`: своих копий на
страницах нет, иначе правка в одной не доезжает до остальных.
"""

from __future__ import annotations

from app.pages import layout

_STYLE = """
<style>
  .admin-row--stack { flex-direction: column; align-items: stretch; gap: var(--ui-s2); }
  .admin-row--stack .ui-row__main { width: 100%; }
  /* Обращение со скриншотом (Э6, D6): текст и миниатюра в одной строке, кнопки — ниже.
     Третьим ребёнком `.admin-row--stack` миниатюра уходила под текст и добавляла 64 px
     впустую (замер дизайна: 108 → 172 px строки). */
  .feedback-row { flex-direction: column; align-items: stretch; gap: var(--ui-s1);
                  padding-top: var(--ui-s1); padding-bottom: var(--ui-s1); }
  .feedback-row .ui-row__main { gap: 0; }
  .feedback-top { display: flex; align-items: center; gap: var(--ui-s2); }
  .feedback-top .ui-row__main { flex: 1 1 auto; min-width: 240px; }
  .feedback-shot { flex: none; width: 56px; height: 56px; border-radius: var(--ui-r-sm);
                   overflow: hidden; background: var(--ui-surface-2); }
  .feedback-shot img { display: block; width: 100%; height: 100%; object-fit: cover; }
  .admin-actions--split .ui-btn { flex: 1; }
  .admin-caps { margin: 0; overflow-wrap: anywhere; }
  .ui-dl__value { overflow-wrap: anywhere; }
  .admin-sheet { display: flex; flex-direction: column; gap: var(--ui-s3); }
  .admin-code { letter-spacing: .08em; }
</style>
"""

_CORE = r"""
(function () {
  "use strict";
  window.SVOI_BACK = "/webapp/more";
  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) { tg.ready(); tg.expand(); }
  var ACTIONS = {};
  var BLOCKS = {};

  var ROLE = { admin: "администратор", org: "организатор", troupe: "труппа",
               master: "педагог" };
  var ROLE_ACC = { admin: "администратора", org: "организатора", troupe: "труппу",
                   master: "педагога" };
  var ROLE_ORDER = ["admin", "org", "troupe", "master"];
  var STATUS = { active: "активен", pending: "ждёт решения", archived: "в архиве",
                 blocked: "заблокирован" };
  var MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
                "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  var ERRORS = {
    "no_webapp_token": "Нет доступа. Откройте раздел из бота.",
    "token_expired": "Сессия истекла. Откройте раздел заново.",
    "bad_token_signature": "Сессия истекла. Откройте раздел заново.",
    "not_registered": "Сначала зарегистрируйтесь в боте.",
    "admin_panel_forbidden": "Раздел доступен организатору и администратору.",
    "participant_archived": "Доступ закрыт.",
    "participant_not_found": "Человек не найден.",
    "cannot_revoke_last_role": "Нельзя отозвать последнюю роль: человек потеряет доступ.",
    "self_revoke_admin": "Нельзя отозвать роль администратора у себя.",
    "last_admin": "Это последний администратор. Сначала назначьте другого.",
    "unknown_discipline": "Такой дисциплины нет. Обновите экран и выберите из списка.",
    "participants_manage_forbidden": "Роли и дисциплины педагога меняет администратор.",
    "discipline_not_taught": "Вы не ведёте эту дисциплину.",
    "self_archive": "Себя в архив отправить нельзя.",
    "no_spreadsheet": "Лист бюджета не настроен."
  };

  function $(id) { return document.getElementById(id); }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clear(box) { while (box && box.firstChild) box.removeChild(box.firstChild); }

  function status(text, bad) {
    var box = $("status");
    if (!box) return;
    box.textContent = text || "";
    box.className = bad ? "ui-alert" : "ui-small ui-muted";
  }

  function plural(count, one, few, many) {
    if (count % 10 === 1 && count % 100 !== 11) return one;
    if (count % 10 >= 2 && count % 10 <= 4 && !(count % 100 >= 12 && count % 100 <= 14)) return few;
    return many;
  }

  function money(value) {
    if (value === null || value === undefined) return "";
    var digits = String(Math.round(Number(value))).replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0");
    return digits + "\u00a0₽";
  }

  function when(iso, withTime) {
    if (!iso) return "";
    var text = String(iso);
    var month = Number(text.slice(5, 7));
    var day = Number(text.slice(8, 10));
    if (!month) return text.slice(0, 10);
    var out = day + " " + MONTHS[month - 1];
    if (withTime && text.length >= 16) out += ", " + text.slice(11, 16);
    return out;
  }

  function rolesText(roles) {
    var list = (roles || []).filter(Boolean);
    if (!list.length) return "без роли";
    return list.map(function (role) { return ROLE[role] || role; }).join(", ");
  }

  function statusWord(code) { return STATUS[code] || code || "неизвестно"; }

  // Конверт ошибок tool-api (docs/architecture/85-error-catalog): {code, title, detail}.
  // Старые ключи ERRORS остаются первыми — в них слова проекта, а не заголовки статуса.
  function problem(res) {
    var body = (res && res.body) || {};
    var detail = body.detail;
    if (Array.isArray(detail)) return "Проверьте введённые данные.";
    var key = String(body.code || detail || "");
    if (ERRORS[key]) return ERRORS[key];
    if (key === "capability_required" || key.indexOf("capability required") === 0) {
      return "Не хватает прав на это действие.";
    }
    if (typeof body.title === "string" && body.title) return body.title;
    if (!res || !res.status) return "Нет связи. Проверьте сеть и повторите.";
    if (res.status === 403) return "Не хватает прав на это действие.";
    if (res.status === 404) return "Не нашли — возможно, запись уже удалена.";
    if (res.status === 409) return "Уже изменили в другом месте. Обновите экран.";
    if (res.status === 429) return "Слишком часто. Повторите через минуту.";
    if (res.status >= 500) return "Сервер не ответил. Повторите позже.";
    return "Не получилось. Повторите.";
  }

  // Отказ по «сырому» `fetch`, мимо `api()`: у потока нет разобранного тела.
  // Сначала `detail` конверта («Архив больше 100 МБ — скачивайте по файлам»), потому
  // что `title` — общий заголовок статуса («Слишком большой запрос») и ничего не
  // объясняет. Ответ не JSON (шлюз, прокси) — пустая строка: экран оставит свой
  // запасной текст с кодом.
  function refusalText(response) {
    var type = (response.headers && response.headers.get("content-type")) || "";
    if (type.indexOf("json") === -1) return Promise.resolve("");
    return response.json().then(function (body) {
      var detail = body && body.detail;
      return typeof detail === "string" && detail
        ? detail : problem({ status: response.status, body: body });
    }, function () { return ""; });
  }

  // Запрос и вход — общие, из оболочки (static/webapp-shell.js). Своей копии
  // `api()`/`auth()` на странице нет: до 31.10.2026 их было пять и три.
  // Ядром пользуются и файлы (`pages/files.py`), поэтому вход тоже здесь.
  // Берём `auth()`, а не `enter()`: она отдаёт причину отказа словами сервера,
  // и экран печатает именно их, а не общее «откройте раздел из бота».
  var api = window.SVOI.api;
  var auth = window.SVOI.auth;

  function loadOverview() {
    return api("/webapp/admin/overview").then(function (res) {
      if (res.status !== 200) {
        status(problem(res), true);
        return null;
      }
      ACTIONS = res.body.actions || {};
      BLOCKS = res.body.blocks || {};
      return res.body;
    });
  }

  function toast(text) {
    if (window.SVOI && window.SVOI.toast) window.SVOI.toast(text);
  }

  function closeSheet() {
    if (window.SVOI && window.SVOI.closeSheet) window.SVOI.closeSheet();
  }

  function sheet(title, body, actions) {
    if (!window.SVOI || !window.SVOI.sheet) return;
    window.SVOI.sheet({ title: title, body: body, actions: actions || [{ label: "Закрыть" }] });
  }

  function copyText(text) {
    function fallback() {
      var area = el("textarea", "ui-input");
      area.value = text;
      area.setAttribute("readonly", "readonly");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try { document.execCommand("copy"); } catch (error) { /* копирование не критично */ }
      document.body.removeChild(area);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).catch(fallback);
    } else {
      fallback();
    }
    toast("Код скопирован: " + text);
  }

  /* `hidden` не работает для `.ui-btn`: собственный display перекрывает правило
     браузера, и «Показать ещё 0» остаётся на экране. Гасим ещё и display. */
  function toggle(button, show) {
    if (!button) return;
    button.hidden = !show;
    button.style.display = show ? "" : "none";
  }

  function empty(title, text) {
    var box = el("div", "ui-empty");
    box.appendChild(el("div", "ui-empty__title", title));
    if (text) box.appendChild(el("div", "ui-empty__text", text));
    return box;
  }

  function pair(dl, term, value) {
    var row = el("div", "ui-dl__row");
    row.appendChild(el("dt", "ui-dl__term", term));
    row.appendChild(el("dd", "ui-dl__value", value));
    dl.appendChild(row);
  }

  function countLine(active, pending) {
    var text = active + " " + plural(active, "активный", "активных", "активных");
    if (pending) text += " · " + pending + " " + plural(pending, "заявка", "заявки", "заявок");
    return text;
  }

  function personMeta(person) {
    return rolesText(person.roles) + " · " + statusWord(person.status);
  }
"""

# Общие имена ядра: страницы вне «Управления» (файлы и папки) собирают свой экран
# тем же ядром, что и админские разделы.
STYLE = _STYLE
CORE = _CORE


def screen(title: str, body: str, script: str, head: str = "") -> str:
    """Экран раздела: вложенный (шапка и «‹»), возврат — в «Управление».

    `window.SVOI_BACK` ставится в ядре страницы, поэтому кнопка «назад» в Telegram
    ведёт в «/webapp/more», а не в «Главную» (docs/development/86 §6).

    `head` — дополнительная разметка для `<head>` (свои правила `[hidden]` у экрана,
    которому мало скрыть элемент: `.ui-section` — флекс, и атрибут `hidden` его не
    прячет, см. `pages/money_tx.py`).
    """
    return layout.page(title, body, back="/webapp/more", head=_STYLE + head,
                       scripts="<script>\n" + _CORE + script + "\n})();\n</script>")
