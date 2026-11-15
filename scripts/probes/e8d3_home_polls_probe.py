"""Замер блока «Открытые опросы» на Главной Mini App (E8-D3).

Что мерит: сколько пикселей добавляет блок адресата и что при этом уезжает за
нижнюю ленту вкладок. Сравниваются три кандидата разметки, собранные классами
проекта (`ui-section`, `ui-row`, `ui-card`, `ui-btn`):

* `внимание` — строки дописываются в блок «Требует внимания»; если блока нет
  (у студийца его не бывает), блок создаётся — иначе строку некуда положить;
* `строки`   — отдельная секция «Открытые опросы» со строками `ui-row`,
  счётчик в шапке; цель нажатия — вся строка;
* `карточка` — отдельная секция, на опрос карточка с главной кнопкой «Ответить».

Каждый кандидат мерится на двух составах Главной: организатор (три строки
«Требует внимания», событие сегодня, долг) и адресат (ни «Требует внимания»,
ни долга — как у студийца, который и есть основной получатель опроса). Состав
адресата собирается удалением узлов из живой страницы: стенд отдаёт фикстуру
организатора, правды о правах адресата в ней нет.

Опросов два: один с коротким названием, второй — с длинным, чтобы строка
переносилась. На одном опросе считается отдельно (§«один»).

Запуск (стенд поднимается отдельно, см. `tests/webapp/server.py`):

    env -u PYTHONPATH ../../.venv/bin/python \
        scripts/probes/e8d3_home_polls_probe.py http://127.0.0.1:8831
    ... --json /tmp/e8d3.json --shots /tmp/e8d3

Адресов экранов в пробе нет: Главная берётся из реестра `TABS`. Строка опроса
ведёт на пустой якорь — мерится место блока, а маршрут — дело спецификации.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))
sys.path.insert(0, str(ROOT))

from app.pages.layout import TABS  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from tests.webapp.conftest import TELEGRAM_HASH  # noqa: E402

HOME_URL = next(url for key, _label, url in TABS if key == "home")

VIEWPORTS = {"390x844": {"width": 390, "height": 844},
             "320x568": {"width": 320, "height": 568}}

ENGINES = ("chromium", "webkit")

#: Состав Главной: какие условные блоки человек видит.
PROFILES = ("организатор", "адресат")

#: Кандидаты разметки. `нет` — сегодняшняя Главная без блока.
VARIANTS = ("нет", "внимание", "строки", "строки-сверху", "карточка")

#: Сколько опросов в наборе; `один` — короткий, `два` — короткий и длинный.
COUNTS = {"один": 1, "два": 2}

#: Латинские имена для снимков: кириллица в именах файлов едет в git плохо.
PROFILE_SLUG = {"организатор": "staff", "адресат": "addressee"}
VARIANT_SLUG = {"нет": "none", "внимание": "attention", "строки": "rows",
                "строки-сверху": "rows-top", "карточка": "card"}
COUNT_SLUG = {"один": "one", "два": "two"}

POLLS = [
    ("Тест опросов E8 (стенд)", "12 вопросов · не начат"),
    ("Анкета организаторам: подготовка к полугодию", "11 вопросов · продолжить, вопрос 3 из 11"),
]

INJECT = """
([variant, polls]) => {
  function row(poll, attention) {
    var node = document.createElement("a");
    node.className = attention ? "ui-row ui-row--attention" : "ui-row";
    node.href = "#";
    var main = document.createElement("span");
    main.className = "ui-row__main";
    var title = document.createElement("span");
    title.className = "ui-row__title";
    title.textContent = poll[0];
    main.appendChild(title);
    var meta = document.createElement("span");
    meta.className = "ui-row__meta";
    meta.textContent = poll[1];
    main.appendChild(meta);
    node.appendChild(main);
    var chevron = document.createElement("span");
    chevron.className = "ui-row__chevron";
    chevron.textContent = "\\u203A";
    node.appendChild(chevron);
    return node;
  }

  function section(title, count, id) {
    var node = document.createElement("section");
    node.className = "ui-section";
    if (id) { node.id = id; }
    var head = document.createElement("div");
    head.className = "ui-section__head";
    var h2 = document.createElement("h2");
    h2.className = "ui-section__title";
    h2.textContent = title;
    head.appendChild(h2);
    if (count !== null) {
      var meta = document.createElement("span");
      meta.className = "ui-section__meta";
      meta.textContent = String(count);
      head.appendChild(meta);
    }
    node.appendChild(head);
    var list = document.createElement("div");
    list.className = "ui-list";
    node.appendChild(list);
    node.__list = list;
    return node;
  }

  function card(poll) {
    var node = document.createElement("div");
    node.className = "ui-card";
    var title = document.createElement("span");
    title.className = "ui-row__title";
    title.textContent = poll[0];
    node.appendChild(title);
    var meta = document.createElement("span");
    meta.className = "ui-small ui-muted";
    meta.textContent = poll[1];
    node.appendChild(meta);
    var btn = document.createElement("a");
    btn.className = "ui-btn ui-btn--primary ui-btn--block";
    btn.href = "#";
    btn.textContent = "Ответить";
    node.appendChild(btn);
    return node;
  }

  var home = document.querySelector(".ui-home");
  if (!home || variant === "нет") { return; }
  var week = document.getElementById("week");

  if (variant === "внимание") {
    var attention = document.getElementById("attention");
    var list;
    if (attention) {
      list = document.getElementById("attention-list");
      polls.forEach(function (poll) { list.appendChild(row(poll, true)); });
      return;
    }
    // У адресата блока нет вовсе: строка опроса означает новый блок.
    attention = section("Требует внимания", null, "attention");
    attention.__list.id = "attention-list";
    polls.forEach(function (poll) { attention.__list.appendChild(row(poll, true)); });
    home.insertBefore(attention, week || null);
    return;
  }

  var block = section("Открытые опросы", polls.length, "polls-open");
  polls.forEach(function (poll) {
    block.__list.appendChild(variant === "карточка" ? card(poll) : row(poll, false));
  });
  if (variant === "строки-сверху") {
    // Первым блоком после строк имени и роли: опрос адресован лично, а не
    // «требует внимания» организатора.
    var role = document.getElementById("role");
    home.insertBefore(block, role ? role.nextSibling : home.firstChild);
  } else {
    home.insertBefore(block, week || null);
  }
}
"""

#: Состав «адресата»: у студийца нет ни «Требует внимания», ни долга —
#: данные Главной таких ключей не отдают вовсе.
STRIP = """
(function () {
  ["attention", "debt"].forEach(function (id) {
    var node = document.getElementById(id);
    if (node) { node.remove(); }
  });
})();
"""

MEASURE = """
(function () {
  var vh = window.innerHeight;
  var tabbar = document.querySelector(".ui-tabbar");
  var tab = tabbar ? tabbar.getBoundingClientRect() : null;
  // Нижняя лента вкладок закреплена: первый экран — это всё выше её верха.
  var limit = tab ? Math.min(vh, tab.top) : vh;
  function box(selector) {
    var node = typeof selector === "string" ? document.querySelector(selector) : selector;
    if (!node) { return null; }
    var r = node.getBoundingClientRect();
    return {top: Math.round(r.top), bottom: Math.round(r.bottom),
            height: Math.round(r.height), width: Math.round(r.width),
            inFirst: r.top < limit, fits: r.bottom <= limit};
  }
  var blocks = {};
  [["внимание", "#attention"], ["опросы", "#polls-open"], ["сегодня", "#today"],
   ["неделя", "#week"], ["долг", "#debt"]].forEach(function (pair) {
    var value = box(pair[1]);
    if (value) { blocks[pair[0]] = value; }
  });
  var targets = [];
  document.querySelectorAll("#polls-open a, #attention-list a").forEach(function (node) {
    var r = node.getBoundingClientRect();
    targets.push([Math.round(r.width), Math.round(r.height)]);
  });
  return {
    vh: vh, limit: Math.round(limit),
    doc: Math.round(document.documentElement.scrollHeight),
    overflowX: Math.round(document.documentElement.scrollWidth - document.documentElement.clientWidth),
    blocks: blocks,
    weekBtn: box(document.querySelector("#week a.ui-btn")),
    targets: targets,
  };
})();
"""


def measure_engine(engine: str, base_url: str, shots: pathlib.Path | None) -> dict:
    out: dict = {}
    with sync_playwright() as play:
        browser = getattr(play, engine).launch()
        for size, viewport in VIEWPORTS.items():
            for profile in PROFILES:
                for variant in VARIANTS:
                    # `нет` мерится один раз: сколько опросов ни возьми, блока нет.
                    counts = ("один",) if variant == "нет" else tuple(COUNTS)
                    for count in counts:
                        polls = POLLS[:COUNTS[count]]
                        context = browser.new_context(viewport=viewport)
                        page = context.new_page()
                        errors: list[str] = []
                        page.on("pageerror",
                                lambda exc, sink=errors: sink.append(str(exc)))
                        page.goto(f"{base_url}{HOME_URL}{TELEGRAM_HASH}", wait_until="load")
                        page.wait_for_selector(".ui-home #week", timeout=10_000)
                        page.wait_for_timeout(150)   # анимация появления
                        if profile == "адресат":
                            page.evaluate(STRIP)
                        page.evaluate(INJECT, [variant, polls])
                        page.wait_for_timeout(50)
                        data = page.evaluate(MEASURE)
                        data["jsErrors"] = errors
                        out[f"{size}|{profile}|{variant}|{count}"] = data
                        if shots is not None:
                            shots.mkdir(parents=True, exist_ok=True)
                            name = (f"{engine}-{size}-{PROFILE_SLUG[profile]}-"
                                    f"{VARIANT_SLUG[variant]}-{COUNT_SLUG[count]}.png")
                            page.screenshot(path=str(shots / name))
                        context.close()
        browser.close()
    return out


def report(result: dict) -> None:
    for engine, rows in result.items():
        print(f"\n=== {engine} ===")
        print(f"{'экран':8} {'состав':11} {'вариант':10} {'опросов':7} {'стр':>5} "
              f"{'вбок':>4} {'низ':>4} {'опросы':>14} {'неделя':>7} {'кнопка':>7} "
              f"{'minцель':>7} js")
        for key, data in rows.items():
            size, profile, variant, count = key.split("|")
            week = data["blocks"].get("неделя")
            btn = data["weekBtn"]
            polls = data["blocks"].get("опросы")
            smallest = min([min(w, h) for w, h in data["targets"]], default=0)
            span = (f"{polls['top']}–{polls['bottom']} ({polls['height']})"
                    if polls else "—")
            print(f"{size:8} {profile:11} {variant:10} {count:7} {data['doc']:5} "
                  f"{data['overflowX']:4} {data['limit']:4} {span:>14} "
                  f"{(week['bottom'] if week else 0):7} {(btn['bottom'] if btn else 0):7} "
                  f"{smallest:7} {len(data['jsErrors'])}")


def main() -> None:
    argv = sys.argv[1:]
    base_url = argv[0] if argv and argv[0].startswith("http") else "http://127.0.0.1:8831"
    wanted = argv[1] if len(argv) > 1 and not argv[1].startswith("--") else None
    shots = None
    if "--shots" in argv:
        shots = pathlib.Path(argv[argv.index("--shots") + 1])

    result = {}
    for engine in ENGINES if not wanted else (wanted,):
        result[engine] = measure_engine(engine, base_url, shots)

    report(result)

    if "--json" in argv:
        path = pathlib.Path(argv[argv.index("--json") + 1])
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\njson: {path}")


if __name__ == "__main__":
    main()
