"""Экраны «остального» Mini App: взносы, уведомления, обратная связь и админские.

Проверяем то, что нельзя увидеть глазами в диффе: каркас и путь назад, отсутствие
эмодзи и модальных окон, наличие компонентов системы вместо самодельных таблиц.
Браузерные замеры (шрифт, цели, переполнение) — в `tests/webapp`.

Правила и требования раздела: docs/development/91-design-system.md.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app.pages import misc  # noqa: E402

EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")
MODAL = re.compile(r"(?<![A-Za-z_.])(alert|confirm|prompt)\s*\(")
LEGACY = ("<table", "ui-list__row", "ui-tile", "ui-tabs", "ui-top", "overflow-x: auto")

PAGES = {
    "dues": (misc.DUES_PAGE, "/webapp/money"),
    "notifications": (misc.NOTIFICATIONS_PAGE, "/webapp/more"),
    "feedback": (misc.FEEDBACK_PAGE, "/webapp/more"),
    "broadcast": (misc.BROADCAST_PAGE, "/webapp/more"),
    "reconcile": (misc.RECONCILE_PAGE, "/webapp/more"),
    "slots": (misc.SLOTS_PAGE, "/webapp/week"),
    "profile": (misc.PROFILE_PAGE, "/webapp/more"),
}


def test_экраны_на_каркасе_и_с_правильным_возвратом():
    for name, (html, back) in PAGES.items():
        assert html.startswith("<!doctype html>"), name
        assert html.rstrip().endswith("</html>"), name
        assert 'href="/webapp/ui.css" data-svoi-ui="1"' in html, name
        assert 'class="ui-appbar"' in html and 'aria-label="Назад"' in html, name
        assert f'href="{back}"' in html, f"{name}: шапка ведёт не в свой раздел"
        assert f'window.SVOI_BACK = "{back}"' in html, f"{name}: BackButton ведёт не туда"
        # Вложенный экран не носит нижние вкладки: выход — «‹».
        assert "ui-tabbar" not in html, name


def test_нет_эмодзи_таблиц_и_модальных_окон():
    for name, (html, _back) in PAGES.items():
        assert not EMOJI.search(html), f"{name}: эмодзи"
        assert not MODAL.search(html), f"{name}: alert/confirm/prompt"
        for legacy in LEGACY:
            assert legacy not in html, f"{name}: вернулся {legacy}"


def test_одна_главная_кнопка_на_экране():
    for name, (html, _back) in PAGES.items():
        body = html.split('<main class="ui-screen">', 1)[1].split("</main>", 1)[0]
        # У «Моих взносов» главного действия нет: платит организатор.
        expected = 0 if name == "dues" else 1
        assert body.count("ui-btn--primary") == expected, f"{name}: главных кнопок не {expected}"


def test_компоненты_вместо_самодельных_полей():
    assert 'class="ui-switch-row' in misc.NOTIFICATIONS_PAGE
    assert misc.NOTIFICATIONS_PAGE.count('<button class="ui-switch-row') == 3
    assert "ui-range" in misc.NOTIFICATIONS_PAGE and "20:00" in misc.NOTIFICATIONS_PAGE
    assert "08:00" in misc.NOTIFICATIONS_PAGE
    assert 'class="ui-radio-row' in misc.FEEDBACK_PAGE
    # Пять тем обращения: к четырём прежним добавилась «Просьба о доработке»
    # (`wish-fl`, Ш-12, отчёт 124 §4.2 — вид обращения, а не отдельное действие).
    assert misc.FEEDBACK_PAGE.count('<button class="ui-radio-row') == 5
    for kind in ("bug", "wish", "wish-fl", "question", "praise"):
        assert f'data-kind="{kind}"' in misc.FEEDBACK_PAGE, f"нет темы {kind}"
    assert "ui-chip" in misc.BROADCAST_PAGE
    assert "ui-datefield" in misc.SLOTS_PAGE
    assert misc.SLOTS_PAGE.count('class="ui-datefield"') == 2
    assert "ui-chips" in misc.SLOTS_PAGE
    assert "ui-card" in misc.RECONCILE_PAGE


def test_тексты_требований_на_месте():
    # Д12: текст и месяц приходят с сервера (`summary.lines`), своей копии на экране нет.
    assert "ui-amount" in misc.DUES_PAGE and "summary.lines" in misc.DUES_PAGE
    assert "Взносы вносит организатор" in misc.DUES_PAGE
    assert "Применить" in misc.RECONCILE_PAGE and "Проверить" in misc.RECONCILE_PAGE
    assert "Назначить" in misc.SLOTS_PAGE
    assert "свободны " in misc.SLOTS_PAGE and "не отметились: " in misc.SLOTS_PAGE
    # Подтверждение рассылки — лист с текстом, а не переименование кнопки.
    assert "SVOI.sheet(" in misc.BROADCAST_PAGE and "SVOI.toast(" in misc.BROADCAST_PAGE
    assert "SVOI.sheet(" in misc.RECONCILE_PAGE and "SVOI.sheet(" in misc.SLOTS_PAGE
    assert "Спасибо, отправлено" in misc.FEEDBACK_PAGE
    # Анкета: пояснение одной строкой и ошибка словами под полем.
    assert "ui-field__error" in misc.PROFILE_PAGE
    assert "ui-field__hint" in misc.PROFILE_PAGE


def test_поиск_окон_знает_обе_области():
    """С6 — одно действие с двумя областями: события студии и субаренда.

    Экран выбирает тип (репетиция, тренинг, аренда зала), тип уходит на сервер,
    а у аренды список людей прячется: там решает зал (docs/architecture/40 §4).
    """
    html = misc.SLOTS_PAGE
    for kind in ("rehearsal", "training", "rental"):
        assert f'data-kind="{kind}"' in html, f"нет типа {kind}"
    assert "Аренда зала" in html and "Тренинг" in html
    assert "policy: kind" in html, "тип не уходит на сервер — строгость окна не применится"
    assert "isRental()" in html and '$("people-section").hidden' in html
    # Вход: сводка недели отдаёт даты, карточка события — тип и людей.
    assert 'params.get("kind")' in html and 'params.get("participants")' in html
    assert 'params.get("from")' in html
    # Нет данных о зале — экран говорит об этом прямо, а не рисует окно.
    assert "Занятость зала неизвестна" in html


def test_поиск_окон_отличает_право_от_сети():
    """403 на список людей — «раздел не для вашей роли», а не «люди не загрузились».

    Карта t_f802d135 и принципы 91: 8 (ошибка говорит, что делать), 9 (пусто — не
    тупик), Б3 (роль видна словами). Право `schedule.slots` есть у организатора и
    мастера, у труппы его нет (docs/architecture/25 §9.2, С6 — поиск окон ведёт
    организатор; db/init/061 — мастер). Поэтому 403 не должен попадать в ветку
    «Потяните экран вниз», которая означает сбой сети.
    """
    html = misc.SLOTS_PAGE
    assert "result.status === 403" in html, "403 не отличается от сетевой ошибки"
    assert "Поиск окон не для вашей роли" in html
    assert "организатор" in html and "администратор" in html, "роль не названа словами"
    assert 'id="search-form"' in html and '$("search-form").hidden = true' in html, \
        "форма поиска остаётся без права — тупик с кнопкой"
    # Сетевая ошибка сохраняет прежнее объяснение — там оно правдиво.
    assert "Люди не загрузились" in html and "Потяните экран вниз" in html
