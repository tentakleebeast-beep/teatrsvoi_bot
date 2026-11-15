"""Мокапы E8 (опросы в боте) и замер: сколько места занимает каждый экран.

Что делает:

1. собирает четыре мокапа настоящим каркасом Mini App (`app.pages.layout.page`) —
   значит меряется живой CSS проекта, а не нарисованная картинка;
2. пишет их файлами в `prototypes/e8-polls/` — их открывают глазами и сверяют с
   дизайн-системой (`docs/development/91`);
3. меряет Chromium 390 × 844: высоту документа, где оказывается главная кнопка,
   переполнение по горизонтали, минимальную цель нажатия;
4. печатает таблицу для `docs/development/162`.

Запуск:

    env -u PYTHONPATH .venv/bin/python scripts/probes/e8_polls_probe.py
    env -u PYTHONPATH .venv/bin/python scripts/probes/e8_polls_probe.py --json

Адресов экранов в пробе нет (docs/development/26): маршруты берутся из реестра
`app.webapp.SECTIONS`, а мокапы грузятся с диска через `file://`.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app.pages.layout import page  # noqa: E402
from app.webapp import SECTIONS  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

OUT = ROOT / "docs" / "development" / "162-mockups"
CSS = ROOT / "services" / "tool-api" / "app" / "static" / "webapp-ui.css"

#: Куда ведёт «‹» на всех четырёх экранах: раздел «Управление».
BACK = next(section.url for section in SECTIONS if section.group == "management")

VIEWPORT = {"width": 390, "height": 844}


# --- 1. Конструктор ---------------------------------------------------------

QUESTION_ROWS = """
<div class="ui-list">
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">1. Какие файлы вы открываете утром?</span>
    <span class="ui-row__meta">текст · обязательный</span></span>
    <span class="ui-row__chevron">›</span></button>
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">2. Как вы выбираете окно для репетиции?</span>
    <span class="ui-row__meta">один из списка · 4 варианта · свободный ответ</span></span>
    <span class="ui-row__chevron">›</span></button>
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">3. Что ломается чаще всего?</span>
    <span class="ui-row__meta">несколько из списка · 4 варианта</span></span>
    <span class="ui-row__chevron">›</span></button>
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">4. Можно ли перенести репетицию 12 декабря?</span>
    <span class="ui-row__meta">да/нет · обязательный</span></span>
    <span class="ui-row__chevron">›</span></button>
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">5. Сколько часов в неделю уходит на согласование?</span>
    <span class="ui-row__meta">число · можно пропустить</span></span>
    <span class="ui-row__chevron">›</span></button>
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">6. Как вы ведёте аренду: календарь, договор, оплата?</span>
    <span class="ui-row__meta">текст · можно пропустить</span></span>
    <span class="ui-row__chevron">›</span></button>
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">7. Какие события вы заводите в календарь?</span>
    <span class="ui-row__meta">текст · можно пропустить</span></span>
    <span class="ui-row__chevron">›</span></button>
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">8. Что вы делаете руками перед показом?</span>
    <span class="ui-row__meta">текст · можно пропустить</span></span>
    <span class="ui-row__chevron">›</span></button>
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">9. Какие заявки приходят из ВК и ТГ?</span>
    <span class="ui-row__meta">текст · можно пропустить</span></span>
    <span class="ui-row__chevron">›</span></button>
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">10. Кого считать «своим» при правке события?</span>
    <span class="ui-row__meta">один из списка · 3 варианта</span></span>
    <span class="ui-row__chevron">›</span></button>
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">11. Какой цвет поставить каждому типу?</span>
    <span class="ui-row__meta">текст · можно пропустить</span></span>
    <span class="ui-row__chevron">›</span></button>
</div>
"""

BUILDER_BODY = f"""
<section class="ui-section">
  <div class="ui-field">
    <label class="ui-field__label" for="title">Тема опроса</label>
    <input class="ui-input" id="title" type="text" value="Анкета организаторам">
    <span class="ui-field__hint">Черновик сохраняется сам: закрыли экран — вернулись к тому же.</span>
  </div>
</section>

<section class="ui-section">
  <div class="ui-section__head">
    <h2 class="ui-section__title">Вопросы</h2>
    <span class="ui-section__meta">11 · около 9 минут</span>
  </div>
  {QUESTION_ROWS}
</section>

<section class="ui-section">
  <h2 class="ui-section__title">Добавить вопрос</h2>
  <div class="ui-chips">
    <button class="ui-chip" type="button">Один из списка</button>
    <button class="ui-chip" type="button">Несколько</button>
    <button class="ui-chip" type="button">Да/нет</button>
    <button class="ui-chip" type="button">Число</button>
    <button class="ui-chip" type="button">Текст</button>
  </div>
</section>

<div class="ui-actions ui-actions--bottom">
  <button class="ui-btn ui-btn--primary ui-btn--block" id="main-action" type="button">
    Дальше: кому отправить
  </button>
</div>
"""

# --- 2. Адресаты ------------------------------------------------------------

AUDIENCE_BODY = """
<section class="ui-section">
  <div class="ui-section__head">
    <h2 class="ui-section__title">Кому отправить</h2>
    <span class="ui-section__meta">наборы складываются</span>
  </div>
  <div class="ui-chips">
    <button class="ui-chip ui-chip--on" type="button">Организаторы</button>
    <button class="ui-chip" type="button">Педагоги</button>
    <button class="ui-chip" type="button">Актёры</button>
    <button class="ui-chip" type="button">Спектакль</button>
    <button class="ui-chip" type="button">Все активные</button>
  </div>
  <div class="ui-alert ui-alert--warn">Уйдёт 2 из 3: у Марии Загаринской нет Telegram —
    приглашение ей не доставить. Результаты она увидит в mini app.</div>
</section>

<section class="ui-section">
  <h2 class="ui-section__title">Люди</h2>
  <div class="ui-list">
    <button class="ui-switch-row ui-switch-row--on" type="button">
      <span class="ui-row__main"><span class="ui-row__title">Виктор Мамлютов</span>
        <span class="ui-row__meta">организатор · Telegram есть</span></span>
      <span class="ui-switch-row__state">в списке</span><span class="ui-switch"></span></button>
    <button class="ui-switch-row ui-switch-row--on" type="button">
      <span class="ui-row__main"><span class="ui-row__title">Даина Тишина</span>
        <span class="ui-row__meta">организатор · Telegram есть</span></span>
      <span class="ui-switch-row__state">в списке</span><span class="ui-switch"></span></button>
    <button class="ui-switch-row" type="button">
      <span class="ui-row__main"><span class="ui-row__title">Мария Загаринская</span>
        <span class="ui-row__meta">организатор · без Telegram</span></span>
      <span class="ui-switch-row__state">не в списке</span><span class="ui-switch"></span></button>
  </div>
</section>

<section class="ui-section">
  <h2 class="ui-section__title">Кому показать результаты</h2>
  <div class="ui-list">
    <button class="ui-row" type="button"><span class="ui-row__main">
      <span class="ui-row__title">Организаторы</span>
      <span class="ui-row__meta">роль целиком</span></span>
      <span class="ui-row__chevron">›</span></button>
    <button class="ui-row" type="button"><span class="ui-row__main">
      <span class="ui-row__title">Виктор Мамлютов</span>
      <span class="ui-row__meta">один человек</span></span>
      <span class="ui-row__chevron">›</span></button>
  </div>
</section>

<div class="ui-actions ui-actions--bottom">
  <button class="ui-btn ui-btn--primary ui-btn--block" id="main-action" type="button">
    Отправить 2 людям
  </button>
</div>
"""

# --- 3. Результаты ----------------------------------------------------------

RESULTS_BODY = """
<section class="ui-section">
  <div class="ui-section__head">
    <h2 class="ui-section__title">Анкета организаторам</h2>
    <span class="ui-section__meta">11 вопросов</span>
  </div>
  <div class="ui-card">
    <div class="ui-dl">
      <div class="ui-dl__row"><span class="ui-dl__term">Ответили</span>
        <span class="ui-dl__value">2 из 3</span></div>
      <div class="ui-dl__row"><span class="ui-dl__term">Черновик</span>
        <span class="ui-dl__value">Мария · вопрос 7</span></div>
    </div>
  </div>
</section>

<section class="ui-section">
  <h2 class="ui-section__title">4. Можно ли перенести репетицию 12 декабря?</h2>
  <div class="ui-card">
    <div class="ui-row"><span class="ui-row__main">
      <span class="ui-row__title">Да</span>
      <div class="ui-meter"><span class="ui-meter__fill" style="width:100%"></span></div>
      </span><span class="ui-row__value">2 · 100 %</span></div>
    <div class="ui-row"><span class="ui-row__main">
      <span class="ui-row__title">Нет</span>
      <div class="ui-meter"><span class="ui-meter__fill" style="width:0%"></span></div>
      </span><span class="ui-row__value">0 · 0 %</span></div>
  </div>
</section>

<section class="ui-section">
  <h2 class="ui-section__title">5. Сколько часов в неделю уходит на согласование?</h2>
  <div class="ui-card">
    <div class="ui-dl">
      <div class="ui-dl__row"><span class="ui-dl__term">Ответы</span>
        <span class="ui-dl__value">3 и 5 часов</span></div>
      <div class="ui-dl__row"><span class="ui-dl__term">Среднее</span>
        <span class="ui-dl__value">4 часа</span></div>
    </div>
  </div>
</section>

<section class="ui-section">
  <h2 class="ui-section__title">6. Как вы ведёте аренду?</h2>
  <div class="ui-card ui-card--flat">
    <span class="ui-card__label">Виктор</span>
    <p class="ui-text">Календарь, договор в папке, оплату отмечаю в листе в конце месяца.</p>
    <span class="ui-card__label">Даина</span>
    <p class="ui-text">Ключи и залог пишу в заметке, аренду — в календарь отдельным событием.</p>
  </div>
</section>

<div class="ui-actions ui-actions--bottom">
  <button class="ui-btn ui-btn--primary ui-btn--block" id="main-action" type="button">
    Экспорт результатов
  </button>
</div>
"""

# --- 3b. Результаты целиком: 11 вопросов анкеты организаторам -----------------

def full_results_body() -> str:
    """Те же блоки, что на экране результатов, но все 11 вопросов анкеты 161."""
    blocks = []
    for number, question, kind in (
        (1, "Какие файлы вы открываете утром? В каком порядке?", "text"),
        (2, "Как вы выбираете окно для репетиции?", "single"),
        (3, "Что ломается чаще всего?", "multi"),
        (4, "Кто и когда правит «Расписание студийцы»?", "text"),
        (5, "Как вы ведёте аренду: календарь, договор, оплата, ключи?", "text"),
        (6, "Какие события вы заводите в календарь, а какие нет?", "text"),
        (7, "Что вы делаете руками перед показом и после него?", "text"),
        (8, "Какие заявки и вопросы приходят из ВК и ТГ?", "text"),
        (9, "Кого считать «своим» при правке события?", "single"),
        (10, "Что бот может сделать за вас без риска?", "text"),
        (11, "Какой цвет поставить каждому типу события?", "text"),
    ):
        if kind == "text":
            body = """<div class="ui-card ui-card--flat">
        <span class="ui-card__label">Виктор</span>
        <p class="ui-text">Смотрю календарь, потом лист «Расписание студийцы».</p>
        <span class="ui-card__label">Даина</span>
        <p class="ui-text">Сначала почта, потом звонки, календарь уже как проверка.</p>
      </div>"""
        elif kind == "single":
            body = """<div class="ui-card">
        <div class="ui-row"><span class="ui-row__main"><span class="ui-row__title">Календарь</span>
          <div class="ui-meter"><span class="ui-meter__fill" style="width:100%"></span></div></span>
          <span class="ui-row__value">2 · 100 %</span></div>
        <div class="ui-row"><span class="ui-row__main"><span class="ui-row__title">Звонки</span>
          <div class="ui-meter"><span class="ui-meter__fill" style="width:0%"></span></div></span>
          <span class="ui-row__value">0 · 0 %</span></div>
      </div>"""
        else:
            body = """<div class="ui-card">
        <div class="ui-row"><span class="ui-row__main"><span class="ui-row__title">Занятость</span>
          <div class="ui-meter"><span class="ui-meter__fill" style="width:100%"></span></div></span>
          <span class="ui-row__value">2 из 2</span></div>
        <div class="ui-row"><span class="ui-row__main"><span class="ui-row__title">Аренда</span>
          <div class="ui-meter"><span class="ui-meter__fill" style="width:50%"></span></div></span>
          <span class="ui-row__value">1 из 2</span></div>
      </div>"""
        blocks.append(f'<section class="ui-section">\n  <h2 class="ui-section__title">'
                      f'{number}. {question}</h2>\n  {body}\n</section>')
    return "\n".join(blocks) + """
<div class="ui-actions ui-actions--bottom">
  <button class="ui-btn ui-btn--primary ui-btn--block" id="main-action" type="button">
    Экспорт результатов
  </button>
</div>
"""


#: Вариант «строка в одну линию»: тип уходит из подписи в счётчик справа.
COMPACT_ROWS = """
<div class="ui-list">
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">1. Какие файлы вы открываете утром?</span></span>
    <span class="ui-row__value">текст</span></button>
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">2. Как вы выбираете окно для репетиции?</span></span>
    <span class="ui-row__value">список</span></button>
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">3. Что ломается чаще всего?</span></span>
    <span class="ui-row__value">несколько</span></button>
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">4. Можно ли перенести репетицию 12 декабря?</span></span>
    <span class="ui-row__value">да/нет</span></button>
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">5. Сколько часов в неделю уходит на согласование?</span></span>
    <span class="ui-row__value">число</span></button>
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">6. Как вы ведёте аренду?</span></span>
    <span class="ui-row__value">текст</span></button>
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">7. Какие события вы заводите в календарь?</span></span>
    <span class="ui-row__value">текст</span></button>
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">8. Что вы делаете руками перед показом?</span></span>
    <span class="ui-row__value">текст</span></button>
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">9. Какие заявки приходят из ВК и ТГ?</span></span>
    <span class="ui-row__value">текст</span></button>
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">10. Кого считать «своим» при правке события?</span></span>
    <span class="ui-row__value">список</span></button>
  <button class="ui-row" type="button"><span class="ui-row__main">
    <span class="ui-row__title">11. Какой цвет поставить каждому типу?</span></span>
    <span class="ui-row__value">текст</span></button>
</div>
"""

COMPACT_BODY = BUILDER_BODY.replace(QUESTION_ROWS, COMPACT_ROWS)


# --- 4. Сообщение в чате ----------------------------------------------------

CHAT_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Мокап: вопрос в чате</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #7d9cb3; font: 16px/1.35 -apple-system, "SF Pro Text", Roboto, sans-serif; }
  .wrap { padding: 12px; display: flex; flex-direction: column; gap: 12px; }
  .bubble { background: #fff; border-radius: 12px; padding: 10px 12px; max-width: 330px;
            box-shadow: 0 1px 1px rgba(0,0,0,.12); }
  .bubble--in { align-self: flex-start; }
  .progress { font-size: 13px; color: #707579; margin-bottom: 6px; }
  .topic { font-weight: 600; margin-bottom: 8px; }
  .keys { display: flex; flex-direction: column; gap: 6px; margin-top: 10px; }
  .keys .row { display: flex; gap: 6px; }
  .key { flex: 1; min-height: 44px; display: flex; align-items: center; justify-content: center;
         background: #eef3f9; border: 1px solid rgba(0,0,0,.08); border-radius: 8px;
         font-size: 15px; color: #1b2733; padding: 6px 8px; text-align: center; }
  .key--quiet { background: #f4f4f5; color: #4d5257; flex: 0 1 auto;
                padding: 0 14px; }
  .key--hint { background: #fff; color: #1459e3; border: 1px dashed rgba(20,89,227,.5); }
  .caption { font-size: 13px; color: #20313f; padding: 0 2px; }
  .chatbtns { display: flex; gap: 8px; align-items: flex-end; margin-top: 4px; }
  .chatbtn { flex: 1; min-height: 44px; border-radius: 8px; background: #eef3f9;
             display: flex; align-items: center; justify-content: center; font-size: 15px; }
  .draft { background: rgba(255,255,255,.92); border-radius: 10px; padding: 8px 10px;
           font-size: 14px; color: #20313f; }
</style></head>
<body><div class="wrap">

  <div class="caption">Состояние 1: вопрос с вариантами (тип «один из списка»)</div>
  <div class="bubble bubble--in" id="q-single">
    <div class="progress">Анкета организаторам · вопрос 2 из 11</div>
    <div class="topic">Как вы выбираете окно для репетиции? Что из этого решает,
      а что подтверждает?</div>
    <div class="keys">
      <div class="row"><div class="key">Расписание студийцы</div></div>
      <div class="row"><div class="key">Календарь</div><div class="key">Звонки</div></div>
      <div class="row"><div class="key">Сообщения</div></div>
      <div class="row"><div class="key key--hint">Другое — напишу</div></div>
      <div class="row"><div class="key key--quiet">Назад</div>
        <div class="key key--quiet">Пропустить</div></div>
    </div>
  </div>

  <div class="caption">Состояние 2: свободный ввод (тип «текст»), после «Другое» или ответа числом</div>
  <div class="bubble bubble--in" id="q-text">
    <div class="progress">Анкета организаторам · вопрос 5 из 11</div>
    <div class="topic">Сколько часов в неделю уходит на согласование окон?</div>
    <div style="font-size:13px;color:#707579">Нажмите «Ответить сообщением» и пришлите число.</div>
    <div class="keys">
      <div class="row"><div class="key key--hint">Ответить сообщением</div></div>
      <div class="row"><div class="key key--quiet">Назад</div>
        <div class="key key--quiet">Пропустить</div></div>
    </div>
  </div>

  <div class="caption">Состояние 3: человек закрыл чат и вернулся — ответы уже сохранены</div>
  <div class="bubble bubble--in" id="q-resume">
    <div class="progress">Анкета организаторам · вопрос 7 из 11</div>
    <div class="topic">Какие события вы заводите в календарь?</div>
    <div class="draft">Черновик: отвечено 6 из 11 · вопрос 7</div>
    <div class="keys">
      <div class="row"><div class="key key--hint">Ответить сообщением</div></div>
      <div class="row"><div class="key key--quiet">Назад</div>
        <div class="key key--quiet">Пропустить</div></div>
    </div>
  </div>

</div></body></html>
"""


# --- сборка -----------------------------------------------------------------

def strip_for_file(html: str) -> str:
    """Файл мокапа: живой CSS вместо ссылки на маршрут, без скриптов оболочки."""
    css = CSS.read_text(encoding="utf-8")
    html = re.sub(r'<link rel="stylesheet"[^>]*>', f"<style>{css}</style>", html)
    html = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    return html


def build() -> dict[str, pathlib.Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    pages = {
        "01-builder.html": page("Опрос", BUILDER_BODY, back=BACK),
        "01b-builder-compact.html": page("Опрос", COMPACT_BODY, back=BACK),
        "02-audience.html": page("Адресаты", AUDIENCE_BODY, back=BACK),
        "04-results.html": page("Результаты", RESULTS_BODY, back=BACK),
        "05-results-full.html": page("Результаты", full_results_body(), back=BACK),
    }
    written = {}
    for name, html in pages.items():
        path = OUT / name
        path.write_text(strip_for_file(html), encoding="utf-8")
        written[name] = path
    chat = OUT / "03-chat-question.html"
    chat.write_text(CHAT_HTML, encoding="utf-8")
    written["03-chat-question.html"] = chat
    return written


MEASURE = """() => {
  const bar = document.querySelector('.ui-tabbar');
  const action = document.getElementById('main-action');
  const doc = document.documentElement;
  const taps = [...document.querySelectorAll('button, a, .key')]
    .map(el => el.getBoundingClientRect())
    .filter(r => r.width > 0 && r.height > 0);
  const small = taps.filter(r => Math.min(r.width, r.height) < 44).length;
  const bubbles = [...document.querySelectorAll('.bubble')].map(el => {
    const r = el.getBoundingClientRect();
    return { id: el.id, top: Math.round(r.top + window.scrollY),
             height: Math.round(r.height) };
  });
  /* Что видно в первом кадре: строка считается видимой, если целиком выше
     закреплённой кнопки (она стоит у нижней границы окна). */
  const limit = action ? action.getBoundingClientRect().top : window.innerHeight;
  const rows = [...document.querySelectorAll('.ui-row, .ui-switch-row, .ui-card')]
    .map(el => el.getBoundingClientRect())
    .filter(r => r.height > 0);
  const sections = [...document.querySelectorAll('.ui-section')].map(el => {
    const r = el.getBoundingClientRect();
    return { title: (el.querySelector('.ui-section__title') || {}).textContent || '',
             top: Math.round(r.top + window.scrollY), height: Math.round(r.height) };
  });
  const row_heights = rows.map(r => Math.round(r.height));
  return {
    height: Math.round(doc.scrollHeight),
    vh: window.innerHeight,
    action_top: action ? Math.round(action.getBoundingClientRect().top + window.scrollY) : null,
    action_h: action ? Math.round(action.getBoundingClientRect().height) : null,
    overflow_x: Math.round(doc.scrollWidth - window.innerWidth),
    taps: taps.length,
    taps_small: small,
    tabbar: bar ? Math.round(bar.getBoundingClientRect().height) : 0,
    bubbles: bubbles,
    blocks: rows.length,
    blocks_visible: rows.filter(r => r.bottom <= limit).length,
    row_min: row_heights.length ? Math.min(...row_heights) : 0,
    sections: sections,
  };
}"""


def measure(files: dict[str, pathlib.Path]) -> dict:
    result = {}
    with sync_playwright() as play:
        browser = play.chromium.launch()
        context = browser.new_context(viewport=VIEWPORT, device_scale_factor=2,
                                      is_mobile=True, has_touch=True)
        for name, path in files.items():
            tab = context.new_page()
            tab.goto(path.as_uri())
            tab.wait_for_timeout(120)
            result[name] = tab.evaluate(MEASURE)
            # Кадр, который видит человек: окно, а не весь документ. На полном
            # снимке закреплённая кнопка рисуется там, где её «приклеил» скролл,
            # и мокап выглядит сломанным.
            tab.screenshot(path=str(OUT / name.replace(".html", ".png")))
            tab.close()
        browser.close()
    return result


def main() -> int:
    as_json = "--json" in sys.argv
    files = build()
    numbers = measure(files)
    if as_json:
        print(json.dumps({"files": {k: str(v) for k, v in files.items()},
                          "measured": numbers}, ensure_ascii=False, indent=2))
        return 0
    print(f"Мокапы: {OUT}")
    print(f"{'экран':26} {'высота':>7} {'кнопка':>7} {'h кн.':>6} "
          f"{'вбок':>5} {'целей':>6} {'<44':>4} {'блоков':>7} {'видно':>6} {'строка':>6}")
    for name, data in numbers.items():
        print(f"{name:26} {data['height']:>7} {data['action_top'] or '-':>7} "
              f"{data['action_h'] or '-':>6} {data['overflow_x']:>5} "
              f"{data['taps']:>6} {data['taps_small']:>4} {data['blocks']:>7} "
              f"{data['blocks_visible']:>6} {data['row_min']:>6}")
    bubbles = numbers["03-chat-question.html"]["bubbles"]
    if bubbles:
        print("\nПузыри в чате (top, высота):")
        for item in bubbles:
            print(f"  {item['id']:12} top={item['top']:>5} h={item['height']:>4}")
    print("\nБлоки конструктора (top, высота):")
    for item in numbers["01-builder.html"]["sections"]:
        print(f"  {item['top']:>5} h={item['height']:>4}  {item['title'][:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
