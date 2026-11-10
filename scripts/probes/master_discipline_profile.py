"""Замер блока «Дисциплины» в «Анкете участника» (V-037, дизайн).

Что мерится: насколько ниже уезжает кнопка «Сохранить», если между полем «Сколько
минут ехать» и «Ролями по спектаклям» поставить блок дисциплин — чтение для мастера
и предупреждение, когда дисциплины нет.

Запуск (стенд уже поднят на 8799):

    env -u PYTHONPATH .venv/bin/python scripts/probes/master_discipline_profile.py
"""

from __future__ import annotations

import json
import pathlib

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8799/webapp"

JS = r"""
(discs) => {
  document.querySelectorAll(".probe-block").forEach((n) => n.remove());
  const make = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const save = document.getElementById("save");
  const before = {
    save_bottom: Math.round(save.getBoundingClientRect().bottom),
    save_top: Math.round(save.getBoundingClientRect().top),
    doc_h: document.documentElement.scrollHeight,
    travel_bottom: Math.round(document.getElementById("travel").getBoundingClientRect().bottom),
  };

  const section = make("section", "ui-section probe-block");
  const head = make("div", "ui-section__head");
  head.appendChild(make("h2", "ui-section__title", "Дисциплины"));
  section.appendChild(head);
  section.appendChild(make("div", "ui-alert ui-alert--warn probe-none",
    "Дисциплина не выбрана: назначить тренинг не получится. Выберите дисциплину или попросите администратора."));
  const field = make("div", "ui-field");
  field.appendChild(make("label", "ui-field__label", "Дисциплина"));
  const sel = make("select", "ui-select");
  const blank = make("option", "", "— выберите —");
  blank.value = "";
  sel.appendChild(blank);
  discs.forEach((d) => { const o = make("option", "", d); o.value = d; sel.appendChild(o); });
  field.appendChild(sel);
  section.appendChild(field);
  const travel = document.getElementById("travel").closest(".ui-field");
  travel.parentNode.insertBefore(section, travel.nextSibling);

  const after_none = {
    save_bottom: Math.round(save.getBoundingClientRect().bottom),
    save_top: Math.round(save.getBoundingClientRect().top),
    doc_h: document.documentElement.scrollHeight,
    section_h: Math.round(section.getBoundingClientRect().height),
  };

  // Одна дисциплина: предупреждения нет, строка «вы ведёте Музыку».
  section.querySelector(".probe-none").remove();
  const rowHost = make("div", "ui-list");
  const row = make("div", "ui-row");
  const main = make("span", "ui-row__main");
  main.appendChild(make("span", "ui-row__title", "Музыка"));
  main.appendChild(make("span", "ui-row__meta", "вы ведёте"));
  row.appendChild(main);
  rowHost.appendChild(row);
  section.insertBefore(rowHost, field);
  sel.remove();
  const tagline = make("div", "ui-small ui-muted", "Меняет администратор.");
  section.appendChild(tagline);
  const after_one = {
    save_bottom: Math.round(save.getBoundingClientRect().bottom),
    doc_h: document.documentElement.scrollHeight,
    section_h: Math.round(section.getBoundingClientRect().height),
  };

  // Вариант «ниже кнопки Сохранить»: блок не правится в этой форме, поэтому он
  // не должен отодвигать сохранение.
  section.remove();
  const tail = make("section", "ui-section probe-block");
  const tailHead = make("div", "ui-section__head");
  tailHead.appendChild(make("h2", "ui-section__title", "Дисциплины"));
  tail.appendChild(tailHead);
  tail.appendChild(make("div", "ui-alert ui-alert--warn",
    "Дисциплина не выбрана: назначить тренинг не получится. Выберите дисциплину или попросите администратора."));
  const holder = document.getElementById("error") || save.parentNode;
  holder.parentNode.insertBefore(tail, holder);
  const after_tail = {
    save_bottom: Math.round(save.getBoundingClientRect().bottom),
    doc_h: document.documentElement.scrollHeight,
    section_top: Math.round(tail.getBoundingClientRect().top),
    section_h: Math.round(tail.getBoundingClientRect().height),
  };

  // Только предупреждение сверху, без списка: сколько стоит сама строка.
  tail.remove();
  const banner = make("div", "ui-alert ui-alert--warn probe-block",
    "Дисциплина не выбрана: назначить тренинг не получится.");
  const hint = document.querySelector("p.ui-small.ui-muted");
  hint.parentNode.insertBefore(banner, hint.nextSibling);
  const after_banner = {
    banner_h: Math.round(banner.getBoundingClientRect().height),
    save_bottom: Math.round(save.getBoundingClientRect().bottom),
  };
  return {before: before, after_none: after_none, after_one: after_one,
          after_tail: after_tail, after_banner: after_banner};
}
"""


def main() -> int:
    report: dict[str, object] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, w, h in (("390x844", 390, 844), ("320x658", 320, 658)):
            page = browser.new_page(viewport={"width": w, "height": h})
            page.goto(URL, wait_until="networkidle")
            page.wait_for_timeout(400)
            report[name] = page.evaluate(
                JS, ["Актёрское мастерство", "Музыка", "Танец", "Сценическая речь"])
            page.close()
        browser.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    pathlib.Path(__file__).with_name("master_discipline_profile.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
