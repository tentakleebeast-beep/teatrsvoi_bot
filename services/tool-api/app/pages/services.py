"""Экран «Сервисы»: живо ли всё, что делает приложение (docs/development/86 §2.5).

Зачем человеку страница: админ получает «бот молчит» и раньше шёл на сервер смотреть
`systemctl`. Здесь те же сведения, но словами и без терминала: что проверено, когда
и на чём основан вывод.

Принципы, которые держит экран:

* состояние — **словом** («работает», «внимание», «недоступен», «нет данных»), цвет
  только помогает;
* у каждой строки видно, **когда** проверяли и **на чём** основан вывод;
* никаких графиков, процентов и «uptime 99.98 %» — только то, что можно проверить;
* страница видна только с правом `admin.panel`, данные отдаёт сервер.

Данные: `GET /webapp/admin/services` — один ответ, потому что проверка живёт рядом
с базой и календарём, а не в браузере.
"""

from __future__ import annotations

from app.pages import layout

_BODY = """
<div class="ui-card">
  <div style="display:flex;align-items:center;gap:8px">
    <h2 class="ui-h2" id="overall" style="flex:1">Проверяю…</h2>
    <span class="ui-small ui-muted" id="overall-count"></span>
  </div>
  <span class="ui-small ui-muted" id="checked">Это займёт пару секунд</span>
</div>

<div class="ui-list" id="rows"></div>

<button class="ui-btn ui-btn--primary ui-btn--block" type="button" id="recheck">
  Проверить снова</button>
<div class="ui-small ui-muted" id="status" role="status"></div>
"""

_SCRIPT = """
<script>
(function () {
  "use strict";
  window.SVOI_BACK = "/webapp/more";
  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) { tg.ready(); tg.expand(); }
  var $ = function (id) { return document.getElementById(id); };
  var WORDS = { ok: "работает", warn: "внимание", down: "недоступен", unknown: "нет данных" };

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) cls.split(" ").forEach(function (name) { name && node.classList.add(name); });
    if (text) node.textContent = text;
    return node;
  }

  function row(item) {
    var line = document.createElement("div");
    line.className = "ui-row";
    var main = el("span", "ui-row__main");
    main.appendChild(el("span", "ui-row__title", item.title || ""));
    if (item.meta) main.appendChild(el("span", "ui-row__meta", item.meta));
    line.appendChild(main);
    var status = el("span", "ui-status ui-status--" + (
      item.status === "ok" ? "ok" : item.status === "warn" ? "warn"
        : item.status === "down" ? "danger" : "muted"), WORDS[item.status] || WORDS.unknown);
    line.appendChild(status);
    return line;
  }

  function render(data) {
    var rows = data.rows || [];
    var list = $("rows");
    list.textContent = "";
    rows.forEach(function (item) { list.appendChild(row(item)); });
    var bad = rows.filter(function (item) {
      return item.status === "warn" || item.status === "down";
    }).length;
    $("overall").textContent = bad
      ? "Требует внимания: " + bad
      : (data.overall === "ok" ? "Всё работает" : "Проверка не удалась");
    $("overall-count").textContent = "из " + rows.length;
    $("checked").textContent = data.checked_label || "";
    if (data.note) { $("status").textContent = data.note; }
  }

  async function check() {
    var button = $("recheck");
    button.disabled = true;
    button.textContent = "Проверяю…";
    try {
      var response = await fetch("/webapp/admin/services");
      var data = await response.json().catch(function () { return {}; });
      if (!response.ok || !data.ok) {
        $("overall").textContent = response.status === 403
          ? "Раздел доступен администратору"
          : "Проверка не удалась";
        $("checked").textContent = response.status === 403
          ? "" : "Проверьте связь и повторите";
        $("status").textContent = "";
        return;
      }
      render(data);
    } catch (error) {
      if (window.console) { console.error("services check failed:", error); }
      $("overall").textContent = "Сайт недоступен";
      $("checked").textContent = "Попробуйте ещё раз";
    } finally {
      button.disabled = false;
      button.textContent = "Проверить снова";
    }
  }

  $("recheck").onclick = check;
  check();
})();
</script>
"""

SERVICES_PAGE = layout.page("Сервисы", _BODY, back="/webapp/more", scripts=_SCRIPT)
