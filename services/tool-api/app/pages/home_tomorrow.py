"""Главная: блок «Завтра» и ссылка «Весь календарь» (V-131, docs/reports/74-questions.md).

Вынесено из `home_loading.py`: модуль страницы не должен превышать 400 строк
(`tests/unit/test_webapp_pages_split.py`). Сборка Главной подставляет этот
фрагмент внутрь своего замыкания (`{tomorrow}`), поэтому помощники `el` и `$`
берутся из общего скрипта страницы, а своих копий здесь нет
(`tests/unit/test_webapp_pages_split.py`: одна реализация на проект).

Разметка — из дизайн-системы (docs/development/91): `ui-section` с заголовком
«Завтра» и датой словами в `ui-section__meta`, `ui-card` с событиями, ссылка —
строка `ui-row` с шевроном. Без эмодзи: статус и переход передаются словами.

Данные приходят тем же ответом `/webapp/home/data`, что и «Сегодня»
(`app/api/webapp_api.py:_calendar_blocks`):

    "tomorrow": {"label": "завтра, 24 ноября, воскресенье",
                 "events": [{"time": "19:00", "title": "Репетиция", "place": "Зал"}]},
    "calendar_url": "/webapp/week/summary"   # без schedule.read.all — "/webapp/week"

Пустой день — пустой `events`: блок остаётся и говорит «завтра событий нет».
`tomorrow = null` — календарь не прочитан: блок тоже остаётся и говорит об этом
словами, а не исчезает и не показывает пустое место.
"""

from __future__ import annotations

#: Фрагмент скрипта Главной: две функции и ничего своего. Строки-подписи живут
#: здесь, чтобы текст пустого дня читался рядом с разметкой блока.
JS = """
  function eventRow(event) {
    var row = el("div", "ui-row");
    var body = el("span", "ui-row__main");
    body.appendChild(el("span", "ui-row__title",
      (event.time ? event.time + " \\u2014 " : "") + (event.title || "Событие")));
    if (event.place) { body.appendChild(el("span", "ui-row__meta", event.place)); }
    row.appendChild(body);
    return row;
  }

  function tomorrowSection(tomorrow, url) {
    var section = el("section", "ui-section");
    section.id = "tomorrow";
    var head = el("div", "ui-section__head");
    head.appendChild(el("h2", "ui-section__title", "Завтра"));
    if (tomorrow && tomorrow.label) {
      head.appendChild(el("span", "ui-section__meta", tomorrow.label));
    }
    section.appendChild(head);

    var card = el("div", "ui-card");
    card.id = "tomorrow-card";
    var events = (tomorrow && tomorrow.events) || [];
    if (!tomorrow) {
      card.appendChild(el("span", "ui-small ui-muted",
        "Расписание на завтра не получено."));
    } else if (!events.length) {
      card.appendChild(el("span", "ui-small ui-muted", "Завтра событий нет."));
    } else {
      var list = el("div", "ui-list");
      list.id = "tomorrow-list";
      events.forEach(function (event) { list.appendChild(eventRow(event)); });
      card.appendChild(list);
    }
    section.appendChild(card);

    var row = document.createElement("a");
    row.className = "ui-row";
    row.id = "calendar-link";
    row.href = url || "/webapp/week";
    var body = el("span", "ui-row__main");
    body.appendChild(el("span", "ui-row__title", "Весь календарь"));
    body.appendChild(el("span", "ui-row__meta", "расписание студии"));
    row.appendChild(body);
    row.appendChild(el("span", "ui-row__chevron", "\\u203A"));
    var links = el("div", "ui-list");
    links.appendChild(row);
    section.appendChild(links);
    return section;
  }
"""
