"""Замер E8-D5: кнопка «Сохранить черновик» и действие «Копировать» (docs/development/166).

Что меряется живым CSS проекта (`app/pages/layout.py` + `static/webapp-ui.css`), а не на глаз:

1. низ конструктора — три варианта: одна кнопка «Дальше», две в строку, две друг под другом:
   высота закреплённой панели, ширина каждой кнопки, перенос текста, сколько строк вопросов
   видно в первом кадре 390×844 (порог docs/development/162 §14.5 п.1 — не меньше 8 из 11);
2. список опросов — строка с действием «Копировать»: ширина колонки заголовка, размер цели
   нажатия, переполнение вбок на 390 и 320 px;
3. результаты — «Копировать» четвёртой кнопкой в нижней панели против отдельной строки.

Запуск:

    env -u PYTHONPATH .venv/bin/python scripts/probes/e8d5_draft_copy_probe.py
    env -u PYTHONPATH .venv/bin/python scripts/probes/e8d5_draft_copy_probe.py --json

Адресов экранов в пробе нет (docs/development/26): «назад» берётся из реестра
`app.webapp.SECTIONS`, страницы собираются тем же `layout.page`, что и в сервисе.
Мокапы пишутся в scratch, в репозиторий не попадают: это замер, а не мокап.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app.pages.layout import page  # noqa: E402
from app.webapp import SECTIONS  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

CSS = ROOT / "services" / "tool-api" / "app" / "static" / "webapp-ui.css"
BACK = next(section.url for section in SECTIONS if section.group == "management")

#: Вопросы анкеты 161 — тот же набор, что в `e8_polls_probe.py` (11 штук).
QUESTIONS = (
    "Какие файлы вы открываете утром? В каком порядке?",
    "Как вы выбираете окно для репетиции?",
    "Что ломается чаще всего?",
    "Кто и когда правит «Расписание студийцы»?",
    "Как вы ведёте аренду: календарь, договор, оплата, ключи?",
    "Какие события вы заводите в календарь, а какие нет?",
    "Что вы делаете руками перед показом и после него?",
    "Какие заявки и вопросы приходят из ВК и ТГ?",
    "Кого считать «своим» при правке события?",
    "Что бот может сделать за вас без риска?",
    "Какой цвет поставить каждому типу события?",
)

ROWS = "\n".join(
    f"""  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">{number}. {text}</span></span>
    <span class="ui-row__value">текст</span></button>"""
    for number, text in enumerate(QUESTIONS, start=1))

HINT = ('<span class="ui-field__hint">Черновик сохранится, когда нажмёте '
        '«Сохранить черновик».</span>')


def builder(bottom: str, column: bool = False) -> str:
    style = ' style="flex-direction:column"' if column else ''
    return f"""
<section class="ui-section">
  <div class="ui-field">
    <label class="ui-field__label" for="title">Тема опроса</label>
    <input class="ui-input" id="title" type="text" value="Анкета организаторам">
    {HINT}
  </div>
</section>
<section class="ui-section">
  <div class="ui-list">
{ROWS}
  </div>
</section>
<div class="ui-actions ui-actions--bottom"{style}>
{bottom}
</div>
"""


ONE_BUTTON = """  <button class="ui-btn ui-btn--primary ui-btn--block" id="main-action" type="button">
    Дальше: кому отправить
  </button>"""

TWO_IN_ROW = """  <button class="ui-btn ui-btn--ghost" id="save" type="button">Сохранить черновик</button>
  <button class="ui-btn ui-btn--primary" id="main-action" type="button">Дальше: кому отправить</button>"""

STACKED = """  <button class="ui-btn ui-btn--ghost ui-btn--block" id="save" type="button">Сохранить черновик</button>
  <button class="ui-btn ui-btn--primary ui-btn--block" id="main-action" type="button">Дальше: кому отправить</button>"""

STACKED_COLUMN = """  <button class="ui-btn ui-btn--ghost ui-btn--block" id="save" type="button">Сохранить черновик</button>
  <button class="ui-btn ui-btn--primary ui-btn--block" id="main-action" type="button">Дальше: кому отправить</button>"""


def list_inline() -> str:
    """Строка списка: статус уходит в подпись, справа — только «Копировать».

    Действие внутри строки-ссылки невозможно: `button` внутри `a` не нажимается
    отдельно. Поэтому строка — `div.ui-row`, а переход — `a.ui-row__main`.
    """
    rows = []
    for status, title, meta in (
        ("черновик", "Анкета организаторам", "11 вопросов · адресаты не выбраны"),
        ("идёт", "Что по опросам?", "4 вопроса · ответили 2 из 3"),
        ("закрыт", "Анкета перед показом", "6 вопросов · ответили 6 из 6"),
    ):
        action = ("<span class=\"ui-row__value\">черновик</span>"
                  if status == "черновик" else
                  '<button class="ui-btn ui-btn--ghost ui-btn--sm" type="button">Копировать</button>')
        rows.append(f"""  <div class="ui-row">
    <a class="ui-row__main" href="#">
      <span class="ui-row__title">{title}</span>
      <span class="ui-row__meta">{status} · {meta}</span>
    </a>{action}
  </div>""")
    return f"""
<section class="ui-section">
  <p class="ui-small ui-muted">Опрос — это вопросы в чате: человек отвечает кнопками,
    прогресс сохраняется. После отправки вопросы и адресаты замораживаются.</p>
  <div class="ui-list">
{chr(10).join(rows)}
  </div>
  <div class="ui-actions">
    <a class="ui-btn ui-btn--primary ui-btn--block" href="#">Новый опрос</a>
  </div>
</section>
"""


def list_body(with_copy: bool) -> str:
    """Список: черновик и два готовых опроса. `with_copy` добавляет действие в строку."""
    rows = []
    for status, title, meta in (
        ("черновик", "Анкета организаторам", "11 вопросов · адресаты не выбраны"),
        ("идёт", "Что по опросам?", "4 вопроса · ответили 2 из 3"),
        ("закрыт", "Анкета перед показом", "6 вопросов · ответили 6 из 6"),
    ):
        action = ""
        if with_copy and status != "черновик":
            action = ('<button class="ui-btn ui-btn--ghost ui-btn--sm" type="button">'
                      'Копировать</button>')
        rows.append(f"""  <div class="ui-row">
    <a class="ui-row__main" href="#">
      <span class="ui-row__title">{title}</span>
      <span class="ui-row__meta">{meta}</span>
    </a>
    <span class="ui-row__value">{status}</span>{action}
  </div>""")
    return f"""
<section class="ui-section">
  <p class="ui-small ui-muted">Опрос — это вопросы в чате: человек отвечает кнопками,
    прогресс сохраняется. После отправки вопросы и адресаты замораживаются.</p>
  <div class="ui-list">
{chr(10).join(rows)}
  </div>
  <div class="ui-actions">
    <a class="ui-btn ui-btn--primary ui-btn--block" href="#">Новый опрос</a>
  </div>
</section>
"""


def results_body(bottom: str) -> str:
    return f"""
<section class="ui-section">
  <h2 class="ui-section__title">Анкета организаторам</h2>
  <div class="ui-card"><div class="ui-dl">
    <div class="ui-dl__row"><span class="ui-dl__term">Ответили</span>
      <span class="ui-dl__value">2 из 3</span></div>
  </div></div>
</section>
<div class="ui-actions ui-actions--bottom">
{bottom}
</div>
"""


FOUR_IN_ROW = """  <button class="ui-btn ui-btn--ghost" id="close" type="button">Закрыть опрос</button>
  <button class="ui-btn ui-btn--ghost" id="copy" type="button">Копировать</button>
  <a class="ui-btn" href="#">Экспорт CSV</a>
  <a class="ui-btn ui-btn--primary" id="main-action" href="#">Экспорт Markdown</a>"""

COPY_ABOVE = """  <button class="ui-btn ui-btn--ghost" id="close" type="button">Закрыть опрос</button>
  <a class="ui-btn" href="#">Экспорт CSV</a>
  <a class="ui-btn ui-btn--primary" id="main-action" href="#">Экспорт Markdown</a>"""

COPY_ROW = """
<div class="ui-actions ui-actions--row">
  <button class="ui-btn ui-btn--ghost ui-btn--block" id="copy" type="button">Копировать опрос</button>
</div>
"""


def strip_for_file(html: str) -> str:
    css = CSS.read_text(encoding="utf-8")
    html = re.sub(r'<link rel="stylesheet"[^>]*>', f"<style>{css}</style>", html)
    return re.sub(r"<script.*?</script>", "", html, flags=re.S)


MEASURE = """() => {
  const doc = document.documentElement;
  const bar = document.querySelector('.ui-actions--bottom');
  const buttons = [...document.querySelectorAll('.ui-actions--bottom .ui-btn')].map(el => {
    const r = el.getBoundingClientRect();
    const range = document.createRange();
    range.selectNodeContents(el);
    const lines = [...range.getClientRects()].filter(box => box.height > 1).length;
    return { text: el.textContent.trim(), top: Math.round(r.top), width: Math.round(r.width),
             height: Math.round(r.height), lines,
             clipped: el.scrollWidth > el.clientWidth + 1,
             wrapped: el.scrollHeight > el.clientHeight + 1 };
  });
  const taps = [...document.querySelectorAll('button, a')]
    .map(el => el.getBoundingClientRect()).filter(r => r.width > 0 && r.height > 0);
  const small = taps.filter(r => Math.min(r.width, r.height) < 44).length;
  const action = document.getElementById('main-action');
  const limit = action ? action.getBoundingClientRect().top : window.innerHeight;
  const rows = [...document.querySelectorAll('.ui-row')]
    .map(el => el.getBoundingClientRect()).filter(r => r.height > 0);
  const copy = document.getElementById('copy');
  const copyBox = copy ? copy.getBoundingClientRect() : null;
  const firstTitle = document.querySelector('.ui-row__title');
  const rowDetail = [...document.querySelectorAll('.ui-row')].map(row => {
    const r = row.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height),
             kids: [...row.children].map(k => {
               const b = k.getBoundingClientRect();
               return { cls: k.className, w: Math.round(b.width),
                        text: k.textContent.trim().slice(0, 14) };
             }) };
  });
  return {
    height: Math.round(doc.scrollHeight),
    vh: window.innerHeight,
    bar_h: bar ? Math.round(bar.getBoundingClientRect().height) : 0,
    bar_rows: bar ? new Set(buttons.map(b => b.top)).size : 0,
    buttons,
    overflow_x: Math.round(doc.scrollWidth - window.innerWidth),
    taps: taps.length,
    taps_small: small,
    rows: rows.length,
    rows_visible: rows.filter(r => r.bottom <= limit).length,
    title_w: firstTitle ? Math.round(firstTitle.getBoundingClientRect().width) : null,
    copy: copyBox ? {w: Math.round(copyBox.width), h: Math.round(copyBox.height)} : null,
    row_detail: rowDetail,
  };
}"""


def build(workdir: pathlib.Path) -> dict[str, pathlib.Path]:
    pages = {
        "builder-one.html": page("Опрос", builder(ONE_BUTTON), back=BACK),
        "builder-two-row.html": page("Опрос", builder(TWO_IN_ROW), back=BACK),
        "builder-stacked.html": page("Опрос", builder(STACKED), back=BACK),
        "builder-column.html": page("Опрос", builder(STACKED_COLUMN, column=True), back=BACK),
        "list-inline.html": page("Опросы", list_inline(), back=BACK),
        "list-without-copy.html": page("Опросы", list_body(False), back=BACK),
        "list-with-copy.html": page("Опросы", list_body(True), back=BACK),
        "results-four.html": page("Результаты", results_body(FOUR_IN_ROW), back=BACK),
        "results-copy-row.html": page("Результаты",
                                      results_body(COPY_ABOVE) + COPY_ROW, back=BACK),
    }
    written = {}
    for name, html in pages.items():
        path = workdir / name
        path.write_text(strip_for_file(html), encoding="utf-8")
        written[name] = path
    return written


def measure(files: dict[str, pathlib.Path], shots: pathlib.Path | None = None) -> dict:
    result: dict[str, dict] = {}
    with sync_playwright() as play:
        browser = play.chromium.launch()
        for width, height in ((390, 844), (320, 658)):
            context = browser.new_context(viewport={"width": width, "height": height},
                                          device_scale_factor=2, is_mobile=True,
                                          has_touch=True)
            for name, path in files.items():
                tab = context.new_page()
                tab.goto(path.as_uri())
                tab.wait_for_timeout(80)
                result[f"{width}:{name}"] = tab.evaluate(MEASURE)
                if shots is not None:
                    tab.screenshot(path=str(shots / f"{width}-{name.replace('.html', '.png')}"))
                tab.close()
            context.close()
        browser.close()
    return result


def main() -> int:
    shots = None
    if "--shots" in sys.argv:
        shots = pathlib.Path(sys.argv[sys.argv.index("--shots") + 1])
        shots.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="e8d5-probe-") as tmp:
        numbers = measure(build(pathlib.Path(tmp)), shots)
    if "--json" in sys.argv:
        print(json.dumps(numbers, ensure_ascii=False, indent=2))
        return 0
    print(f"{'экран':34} {'панель':>7} {'рядов':>6} {'вбок':>5} {'<44':>4} "
          f"{'строк':>6} {'видно':>6} {'заголовок':>10}")
    for name, data in numbers.items():
        print(f"{name:34} {data['bar_h']:>7} {data['bar_rows']:>6} "
              f"{data['overflow_x']:>5} {data['taps_small']:>4} "
              f"{data['rows']:>6} {data['rows_visible']:>6} "
              f"{str(data['title_w']):>10}")
    print("\nКнопки нижней панели (top, ширина × высота, перенос текста):")
    for name, data in numbers.items():
        if not data["buttons"]:
            continue
        print(f"  {name}")
        for button in data["buttons"]:
            print(f"    top={button['top']:>4} {button['width']:>4}×{button['height']:<4} "
                  f"строк текста {button['lines']}  "
                  f"{'перенос' if button['wrapped'] else ''}"
                  f"{'обрезка' if button['clipped'] else '':8} {button['text']}")
    print("\nСтроки списка (ширина детей):")
    for name, data in numbers.items():
        if not data["row_detail"]:
            continue
        print(f"  {name}")
        for row in data["row_detail"]:
            kids = " | ".join(f"{k['cls'].split()[-1]}:{k['w']}" for k in row["kids"])
            print(f"    строка {row['w']}×{row['h']}: {kids}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
