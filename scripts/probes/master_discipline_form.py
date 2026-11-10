"""Замер формы «Дисциплины педагога» в админской карточке человека (V-037, дизайн).

Стенд `tests.webapp.server` отдаёт настоящие HTML/CSS/JS Mini App, поэтому высоты
считаются по проектной разметке (`.ui-sheet`, `.ui-field`, `.ui-select`, `.ui-row`,
`.ui-alert`), а не по макету.

Что мерится:

* V0 — как сейчас (`pages/admin_disciplines.py:disciplineSheet`): список из четырёх
  строк «название + Отметить/Снять»;
* V1 — предложение «нет дисциплины»: предупреждение + выпадающий список + кнопка;
* V2 — «одна»: строка дисциплины + список добавления;
* V3 — «несколько»: три строки + список добавления.

Плюс проверка переноса/обрезки: ширина `select` и полная ширина текста опции
(`scrollWidth` против `clientWidth`) на 390 и 320 px.

Запуск (стенд уже поднят на 8799):

    env -u PYTHONPATH .venv/bin/python scripts/probes/master_discipline_form.py
"""

from __future__ import annotations

import json
import pathlib
import sys

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8799/webapp/admin/t/people"
DISCIPLINES = ["Актёрское мастерство", "Музыка", "Танец", "Сценическая речь"]

# Разметка вариантов повторяет классы проекта 1:1 (static/webapp-ui.css).
JS_BUILD = r"""
(discs) => {
  document.querySelectorAll(".probe-overlay").forEach((n) => n.remove());
  const make = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const row = (title, meta, label) => {
    const r = make("div", "ui-row");
    const main = make("span", "ui-row__main");
    main.appendChild(make("span", "ui-row__title", title));
    main.appendChild(make("span", "ui-row__meta", meta));
    r.appendChild(main);
    const b = make("button", "ui-btn ui-btn--sm", label);
    b.type = "button";
    r.appendChild(b);
    return r;
  };
  const list = (items) => {
    const l = make("div", "ui-list");
    items.forEach((n) => l.appendChild(n));
    return l;
  };
  const alert = (text) => make("div", "ui-alert ui-alert--warn", text);
  const picker = (chosen) => {
    const field = make("div", "ui-field");
    field.appendChild(make("label", "ui-field__label", "Дисциплина"));
    const sel = make("select", "ui-select");
    sel.id = "probe-select";
    const blank = make("option", "", "— выберите —");
    blank.value = "";
    sel.appendChild(blank);
    discs.forEach((d) => {
      if (chosen.indexOf(d) >= 0) return;
      const o = make("option", "", d);
      o.value = d;
      sel.appendChild(o);
    });
    field.appendChild(sel);
    return field;
  };
  const addBtn = (text) => make("button", "ui-btn ui-btn--block", text);

  const variants = {
    V0_current: () => [
      make("p", "ui-small ui-muted",
           "Что человек ведёт. Роль педагога выдаётся отдельной кнопкой в «Ролях»."),
      list(discs.map((d, i) => row(d, i === 1 ? "ведёт" : "не отмечено", i === 1 ? "Снять" : "Отметить"))),
    ],
    V1_none: () => [
      alert("У педагога нет дисциплины: назначить тренинг он не сможет, пока не выберете."),
      picker([]),
      addBtn("Добавить дисциплину"),
    ],
    V2_one: () => [
      list([row(discs[1], "ведёт", "Снять")]),
      picker([discs[1]]),
      addBtn("Добавить ещё"),
    ],
    V3_many: () => [
      list([row(discs[1], "ведёт", "Снять"), row(discs[2], "ведёт", "Снять"),
            row(discs[3], "ведёт", "Снять")]),
      picker([discs[1], discs[2], discs[3]]),
      addBtn("Добавить ещё"),
    ],
    V4_all: () => [
      list(discs.map((d) => row(d, "ведёт", "Снять"))),
      make("p", "ui-small ui-muted", "Все дисциплины отмечены."),
    ],
  };

  const out = {};
  Object.entries(variants).forEach(([name, build]) => {
    const overlay = make("div", "ui-overlay probe-overlay");
    const sheet = make("section", "ui-sheet");
    sheet.appendChild(make("div", "ui-sheet__handle"));
    sheet.appendChild(make("h2", "ui-sheet__title", "Педагог: Попова"));
    const body = make("div", "ui-sheet__body");
    build().forEach((n) => body.appendChild(n));
    sheet.appendChild(body);
    const actions = make("div", "ui-sheet__actions");
    const close = make("button", "ui-btn", "Закрыть");
    close.type = "button";
    actions.appendChild(close);
    sheet.appendChild(actions);
    overlay.appendChild(sheet);
    document.body.appendChild(overlay);

    const bodyRect = body.getBoundingClientRect();
    const addRect = (body.lastElementChild || body).getBoundingClientRect();
    const actRect = actions.getBoundingClientRect();
    const sel = sheet.querySelector("#probe-select");
    const selInfo = sel ? {
      width: Math.round(sel.getBoundingClientRect().width),
      scrollWidth: sel.scrollWidth,
      clientWidth: sel.clientWidth,
      text: sel.options[1] ? sel.options[1].text : null,
    } : null;
    // Сколько видно, а сколько уехало под скролл листа.
    out[name] = {
      sheet_h: Math.round(sheet.getBoundingClientRect().height),
      sheet_client: sheet.clientHeight,
      sheet_scroll: sheet.scrollHeight,
      overflow: Math.max(0, sheet.scrollHeight - sheet.clientHeight),
      body_h: Math.round(bodyRect.height),
      body_top: Math.round(bodyRect.top),
      body_bottom: Math.round(bodyRect.bottom),
      primary_bottom: Math.round(addRect.bottom),
      actions_bottom: Math.round(actRect.bottom),
      select: selInfo,
    };
    overlay.remove();
  });
    // Помещается ли самый длинный вариант в список: ширина текста против доступной
    // (ширина select минус padding left `--ui-s2` и padding-right `--ui-s6` = 12 + 24).
    const cv = document.createElement("canvas").getContext("2d");
    const cs = getComputedStyle(document.body);
    cv.font = cs.fontSize + " " + cs.fontFamily;
    const longest = discs.reduce((a, b) => (cv.measureText(a).width >= cv.measureText(b).width ? a : b));
    out._meta = {
      font: cv.font.slice(0, 60),
      longest: longest,
      longest_px: Math.round(cv.measureText(longest).width),
      available_390: 358 - 36,
      available_320: 288 - 36,
    };
  return out;
}
"""


def run() -> int:
    report: dict[str, object] = {"url": URL, "viewports": {}}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, width, height in (("390x844", 390, 844), ("320x658", 320, 658)):
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(URL, wait_until="networkidle")
            page.wait_for_timeout(400)
            report["viewports"][name] = page.evaluate(JS_BUILD, DISCIPLINES)
            page.close()
        browser.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    out = pathlib.Path(__file__).with_name("master_discipline_form.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nсохранено: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
