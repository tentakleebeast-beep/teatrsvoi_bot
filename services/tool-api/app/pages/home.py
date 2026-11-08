"""«Главная» и «Ещё/Управление» — вход в Mini App (docs/development/86 §2.1, §2.4).

Главная перестала быть каталогом из тринадцати плиток: человек открывает приложение,
чтобы узнать «что у меня сейчас» и «что требует решения». Поэтому сверху — только
действия со счётчиками, затем сегодняшний день, неделя занятости и долг.

Данные приходят из `/webapp/home/data` одним ответом:

    {
      "ok": true, "name": "…", "role": "admin", "role_label": "администратор",
      "staff": true,
      "attention": [{"title": "Заявки", "meta": "ждут решения",
                     "url": "/webapp/admin/t/pending", "count": 2}],
      "today": {"label": "31 октября, суббота",
                "event": {"time": "19:00", "title": "Репетиция", "place": "Зал"}},
      "week": {"title": "26 октября — 1 ноября", "marked": 4, "days": 7},
      "debt": {"period": "октябрь 2026", "rest": 3000, "due": 5000},
      "can_record": true,
      "more": {"management": [{"title": "Заявки", "meta": "ждут решения",
                               "url": "/webapp/admin/t/pending", "count": 2}],
               "personal": [{"title": "Файлы студии", "meta": "записи, фонограммы, тексты",
                             "url": "/webapp/files"}]}
    }

Названия, адреса и подписи строк — из реестра разделов (`app/webapp.py:SECTIONS`);
здесь только разметка и отрисовка. «Управление» и «Личное» рисует скрипт страницы по
данным ответа: страница — публичная константа, права на момент отдачи HTML неизвестны.
Любой блок может отсутствовать — тогда он
не рисуется. Это нормальное состояние, а не ошибка: у студийца нет ни заявок, ни долга.
"""

from __future__ import annotations

from app.pages import layout

_BODY_HOME = """
<div>
  <h1 class="ui-title" id="name">СВОИ</h1>
  <div class="ui-small ui-muted" id="role"></div>
</div>

<section class="ui-section" id="attention" hidden>
  <div class="ui-section__head"><h2 class="ui-section__title">Требует внимания</h2></div>
  <div class="ui-list" id="attention-list"></div>
</section>

<div class="ui-card" id="today" hidden>
  <span class="ui-card__label" id="today-label"></span>
  <h2 class="ui-h2" id="today-title"></h2>
  <span class="ui-small ui-muted" id="today-place"></span>
</div>

<div class="ui-card" id="week">
  <div class="ui-section__head" style="padding:0">
    <h2 class="ui-section__title">Занятость на неделе</h2>
    <span class="ui-section__meta" id="week-count"></span>
  </div>
  <span class="ui-small ui-muted" id="week-meta">Открывается в разделе «Занятость».</span>
  <a class="ui-btn ui-btn--primary ui-btn--block" href="/webapp/week">Отметить занятость</a>
</div>

<div class="ui-card" id="debt" hidden>
  <span class="ui-card__label" id="debt-label"></span>
  <span class="ui-amount ui-amount--due" id="debt-rest"></span>
  <span class="ui-small ui-muted" id="debt-meta"></span>
</div>

<a class="ui-btn ui-btn--block" id="quick-tx" href="/webapp/tx" hidden
   data-svoi-cap="budget.write">Записать операцию</a>

<div class="ui-small ui-muted" id="status"></div>
"""

_SCRIPT_HOME = """
<script>
(function () {
  "use strict";
  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) { tg.ready(); tg.expand(); if (tg.BackButton) tg.BackButton.hide(); }
  var $ = function (id) { return document.getElementById(id); };

  function attentionRow(item) {
    var row = document.createElement("a");
    row.className = "ui-row ui-row--attention";
    row.href = item.url || "/webapp/more";
    var main = document.createElement("span");
    main.className = "ui-row__main";
    var title = document.createElement("span");
    title.className = "ui-row__title";
    title.textContent = item.title || "";
    main.appendChild(title);
    if (item.meta) {
      var meta = document.createElement("span");
      meta.className = "ui-row__meta";
      meta.textContent = item.meta;
      main.appendChild(meta);
    }
    row.appendChild(main);
    if (item.count) {
      var badge = document.createElement("span");
      badge.className = "ui-badge";
      badge.textContent = String(item.count);
      row.appendChild(badge);
    }
    var chevron = document.createElement("span");
    chevron.className = "ui-row__chevron";
    chevron.textContent = "\\u203A";
    row.appendChild(chevron);
    return row;
  }

  function render(data) {
    $("name").textContent = data.name || "СВОИ";
    // Подпись роли и дисциплины одной строкой: педагог видит, что он ведёт
    // (140 §4.2). Данные приходят в ответе, страница их не вычисляет.
    var role = data.role_label || "";
    var subjects = (data.disciplines || []).map(function (item) { return item.name; });
    if (subjects.length) { role += " · " + subjects.join(", "); }
    $("role").textContent = role;

    var attention = data.attention || [];
    if (attention.length) {
      var list = $("attention-list");
      attention.forEach(function (item) { list.appendChild(attentionRow(item)); });
      $("attention").hidden = false;
    }

    var today = data.today || {};
    if (today.event) {
      $("today-label").textContent = today.label || "сегодня";
      $("today-title").textContent = (today.event.time ? today.event.time + " — " : "")
        + (today.event.title || "Событие");
      $("today-place").textContent = today.event.place || "";
      $("today").hidden = false;
    }

    var week = data.week || {};
    if (week.days) {
      $("week-count").textContent = "отмечено " + (week.marked || 0) + " из " + week.days;
      if (week.marked >= week.days) {
        $("week-meta").textContent = "Неделя заполнена целиком. Можно поправить любую отметку.";
      } else if (week.marked) {
        $("week-meta").textContent = "Не отмечены " + (week.days - week.marked)
          + " дня. Пустая клетка — это «не знаю», а не «свободен».";
      }
    }

    var debt = data.debt || null;
    if (debt && debt.rest > 0) {
      $("debt-label").textContent = "взносы за " + (debt.period || "месяц");
      $("debt-rest").textContent = formatMoney(debt.rest);
      $("debt-meta").textContent = "осталось внести из " + formatMoney(debt.due)
        + " · вносит организатор";
      $("debt").hidden = false;
    }

    if (data.can_record) { $("quick-tx").hidden = false; }

    if (data.more && data.more.management) { renderMore(data.more.management); }
  }

  function formatMoney(value) {
    var digits = String(Math.round(Number(value) || 0)).replace(/\\B(?=(\\d{3})+(?!\\d))/g, "\\u00A0");
    return digits + "\\u00A0\\u20BD";
  }

  function renderMore(items) {
    var host = $("management-list");
    if (!host) { return; }
    items.forEach(function (item) { host.appendChild(attentionRow(item)); });
    $("management").hidden = items.length === 0;
  }

  async function boot() {
    try {
      var res = await fetch("/webapp/home/data");
      var data = await res.json().catch(function () { return {}; });
      if (!res.ok || !data.ok) {
        $("status").textContent = res.status === 401
          ? "Откройте приложение из бота — сессия не подтверждена."
          : "Не удалось загрузить. Потяните экран вниз, чтобы повторить.";
        return;
      }
      $("status").textContent = "";
      render(data);
    } catch (error) {
      $("status").textContent = "Сайт недоступен, попробуйте ещё раз.";
    }
  }

  boot();
})();
</script>
"""

_BODY_MORE = """
<section class="ui-section" id="management" hidden>
  <div class="ui-section__head"><h2 class="ui-section__title">Управление</h2></div>
  <div class="ui-list" id="management-list"></div>
</section>

<section class="ui-section">
  <div class="ui-section__head"><h2 class="ui-section__title">Личное</h2></div>
  <div class="ui-list" id="personal-list">
    <a class="ui-row" href="/webapp">
      <span class="ui-row__main"><span class="ui-row__title">Профиль</span>
        <span class="ui-row__meta">имя, роли, время в пути</span></span>
      <span class="ui-row__chevron">&#8250;</span>
    </a>
    <a class="ui-row" href="/webapp/notifications">
      <span class="ui-row__main"><span class="ui-row__title">Уведомления</span>
        <span class="ui-row__meta">что и когда приходит</span></span>
      <span class="ui-row__chevron">&#8250;</span>
    </a>
    <a class="ui-row" href="/webapp/feedback">
      <span class="ui-row__main"><span class="ui-row__title">Обратная связь</span>
        <span class="ui-row__meta">вопрос или предложение</span></span>
      <span class="ui-row__chevron">&#8250;</span>
    </a>
    <button class="ui-row" type="button" id="help-open">
      <span class="ui-row__main"><span class="ui-row__title">Что умею</span>
        <span class="ui-row__meta">короткая справка</span></span>
      <span class="ui-row__chevron">&#8250;</span>
    </button>
  </div>
</section>

<div class="ui-small ui-muted" id="status"></div>
"""

_SCRIPT_MORE = """
<script>
(function () {
  "use strict";
  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) { tg.ready(); tg.expand(); if (tg.BackButton) tg.BackButton.hide(); }
  var $ = function (id) { return document.getElementById(id); };

  function row(item) {
    var link = document.createElement("a");
    link.className = "ui-row";
    link.href = item.url || "/webapp/more";
    var main = document.createElement("span");
    main.className = "ui-row__main";
    var title = document.createElement("span");
    title.className = "ui-row__title";
    title.textContent = item.title || "";
    main.appendChild(title);
    if (item.meta) {
      var meta = document.createElement("span");
      meta.className = "ui-row__meta";
      meta.textContent = item.meta;
      main.appendChild(meta);
    }
    link.appendChild(main);
    if (item.count) {
      var badge = document.createElement("span");
      badge.className = "ui-badge";
      badge.textContent = String(item.count);
      link.appendChild(badge);
    }
    var chevron = document.createElement("span");
    chevron.className = "ui-row__chevron";
    chevron.textContent = "\\u203A";
    link.appendChild(chevron);
    return link;
  }

  function renderManagement(items) {
    var host = $("management-list");
    if (!host) { return; }
    host.textContent = "";
    (items || []).forEach(function (item) { host.appendChild(row(item)); });
    $("management").hidden = !items || items.length === 0;
  }

  function renderPersonal(items) {
    var host = $("personal-list");
    if (!host || !items || !items.length) { return; }
    var at = host.children[1] || null;   // после «Профиля», до «Уведомлений»
    items.forEach(function (item) { host.insertBefore(row(item), at); });
  }

  $("help-open").onclick = function () {
    SVOI.sheet({
      title: "Что умею",
      body: '<p class="ui-text">Отмечать вашу занятость по дням и часам, показывать взносы ' +
            'и расписание, искать общие окна для репетиций, записывать расходы и доходы ' +
            'в общий бюджет.</p><p class="ui-text ui-muted" style="margin-top:8px">' +
            'Если что-то непонятно — кнопка «Обратная связь» в этом же списке.</p>',
      actions: [{ label: "Понятно", primary: true }],
    });
  };

  async function boot() {
    try {
      var res = await fetch("/webapp/home/data");
      var data = await res.json().catch(function () { return {}; });
      if (!res.ok || !data.ok) {
        $("status").textContent = res.status === 401
          ? "Откройте приложение из бота — сессия не подтверждена."
          : "Не удалось загрузить разделы.";
        return;
      }
      renderManagement(data.more ? data.more.management : []);
      renderPersonal(data.more ? data.more.personal : []);
    } catch (error) {
      $("status").textContent = "Сайт недоступен, попробуйте ещё раз.";
    }
  }

  boot();
})();
</script>
"""

HOME_PAGE = layout.page("СВОИ", _BODY_HOME, tab="home", scripts=_SCRIPT_HOME)
# Личные разделы на «Ещё» приходят данными: `/webapp/home/data` кладёт их в
# `more.personal` из реестра `app/webapp.py:SECTIONS` (права известны только там,
# где есть человек). Страница — публичная константа, строки рисует `renderPersonal`.
# Новый личный раздел добавляется записью в реестре, а не правкой разметки.

MORE_PAGE = layout.page("Ещё", _BODY_MORE, tab="more", scripts=_SCRIPT_MORE)
