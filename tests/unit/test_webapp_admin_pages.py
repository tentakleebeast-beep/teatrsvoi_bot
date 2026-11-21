"""Админские экраны Mini App: без таблиц, ленты вкладок и горизонтального скролла.

Проверяем то, из-за чего панель переделывалась (docs/development/92 §5): каждый
раздел — вложенный экран с возвратом в «Управление», строки вместо таблиц,
действия в листе человека, пагинация по 20 и никаких `alert`/`confirm`.
"""

from __future__ import annotations

import pathlib
import re
import sys
from typing import get_args

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app.api.admin import WebAppNewPersonIn  # noqa: E402
from app.pages import admin  # noqa: E402
from app.pages.admin_disciplines import DISCIPLINES_SCRIPT  # noqa: E402

EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")

PAGES = {
    "Люди": admin.PEOPLE_PAGE,
    "Взносы": admin.DUES_ADMIN_PAGE,
    "Права": admin.RIGHTS_PAGE,
    "Архив": admin.ARCHIVE_PAGE,
    "Аудит": admin.AUDIT_PAGE,
    "Приглашения": admin.INVITES_PAGE,
    "Уведомления": admin.NOTIFICATION_DEFAULTS_PAGE,
}


def test_разделы_отдельными_экранами():
    assert set(PAGES) == {"Люди", "Взносы", "Права", "Архив", "Аудит",
                          "Приглашения", "Уведомления"}
    for title, html in PAGES.items():
        assert f"<title>{title}</title>" in html


def test_каждый_экран_вложенный_и_возвращается_в_управление():
    for html in PAGES.values():
        assert 'href="/webapp/more"' in html          # «‹» в шапке
        assert 'class="ui-screen"' in html            # без нижних вкладок
        assert "ui-tabbar" not in html
        assert 'window.SVOI_BACK = "/webapp/more"' in html


def test_взносы_начисляются_кнопкой_с_подтверждением():
    """Д6: путь начисления вызывается из UI, а не только по curl."""
    html = admin.DUES_ADMIN_PAGE
    assert "Начислить за месяц" in html
    assert "/webapp/admin/dues/accrue" in html      # тот же обработчик, что у пути-канона
    assert "/webapp/admin/dues" in html             # данные месяца
    assert "SVOI.sheet(" in html, "начисление без подтверждения"
    assert "/webapp/admin/tariff" in html           # Д8: тариф тем же правом


def test_нет_таблиц_и_горизонтального_скролла():
    for title, html in PAGES.items():
        assert "<table" not in html, f"{title}: таблица на телефоне"
        assert "overflow-x: auto" not in html, f"{title}: лента вкладок"
        assert "overflow-x: auto" not in html
        assert "@media" not in html or "max-width" not in html


def test_нет_эмодзи_и_модалов():
    for title, html in PAGES.items():
        match = EMOJI.search(html)
        assert not match, f"{title}: эмодзи {match.group(0) if match else ''}"
        assert "alert(" not in html and "confirm(" not in html and "prompt(" not in html


def test_нет_служебных_фраз():
    for title, html in PAGES.items():
        assert "проверяются на сервере" not in html, title
        assert "теми же правами" not in html, title


def test_люди_ищутся_и_тариф_справа_из_карточки():
    html = admin.PEOPLE_PAGE
    assert 'class="ui-input" id="people-search"' in html
    assert "inputmode=\"search\"" in html
    assert "/webapp/admin/overview" in html
    assert "/webapp/admin/person" in html          # тарифа в списке нет — берём карточкой
    assert "function enrich" in html and "data-tariff" in html
    assert "Показать ещё" in html                  # страницы по 20


def test_лист_человека_вместо_полей_в_строке():
    html = admin.PEOPLE_PAGE
    assert "SVOI.sheet" in html
    assert "ui-sheet" in html or "window.SVOI.sheet" in html
    assert "/webapp/admin/grant" in html          # роли и права
    assert "/webapp/admin/tariff" in html         # тариф
    assert "/webapp/admin/archive" in html        # архив
    assert "select" not in html.split("<script>")[0].lower()  # в разметке нет select


def test_права_это_матрица_и_точечная_выдача_а_не_второй_список():
    html = admin.RIGHTS_PAGE
    assert '"ui-dl"' in html
    assert "BLOCKS.rights" in html and "matrix" in html
    assert "rights-search" in html
    assert "capability" in html and "/webapp/admin/grant" in html
    # полного списка людей на экране нет: строки появляются только после ввода
    assert "rights-results" in html
    assert "function renderResults" in html


def test_права_без_права_говорят_о_праве_а_не_о_пустой_матрице():
    """Отчёт 142 §3 строка 25, принципы 91: 8, Б3; карта t_eb9e0661.

    У org, master и troupe нет `participants.manage` (`db/init/008`, `db/init/061`),
    поэтому раздела `rights` в ответе панели нет вовсе. Прежний экран получал
    пустой список и печатал «Матрица пуста. Роли не настроены» — ложное утверждение
    о данных (у администратора матрица полная) — рядом с формой «Выдать право
    человеку», которой человек без права всё равно не мог воспользоваться.
    """
    html = admin.RIGHTS_PAGE
    assert 'id="rights-matrix" hidden' in html, "матрица отдаётся видимой — до ответа панели"
    assert 'id="rights-grant" hidden' in html, "форма выдачи отдаётся видимой — до ответа панели"
    assert "Права выдаёт администратор" in html, "нет объяснения, у кого права есть"
    assert 'indexOf("rights")' in html, "экран не сверяет право раздела с ответом панели"
    assert "ACTIONS.manage_rights" in html, "право выдачи берётся не из данных панели"
    body = html.split("function applyRights", 1)[1]
    assert body.index("if (can !== true)") < body.index("matrix = (BLOCKS.rights"), \
        "матрица запрашивается и рисуется до проверки права"
    # «Матрица пуста» осталась только честным пустым состоянием «право есть, данных нет»
    assert 'if (!matrix.length) box.appendChild(empty("Матрица пуста"' in html
    denied = html.split('id="rights-denied"', 1)[1].split("</section>", 1)[0]
    assert "Матрица пуста" not in denied


def test_аудит_по_двадцать_и_время_кто_что():
    html = admin.AUDIT_PAGE
    assert "shown += 20" in html
    assert "actionWord" in html and "actor_name" in html
    assert "when(event.ts, true)" in html


def test_приглашение_выпускается_в_лист_с_копированием():
    html = admin.INVITES_PAGE
    assert "/webapp/admin/invite\"" in html
    assert "Копировать" in html and "function copyText" in html
    assert "invite-issue" in html
    assert "/webapp/admin/invite-revoke" in html
    assert "/webapp/admin/invite-candidates" in html
    assert "/webapp/admin/person-new" in html
    assert "Новый участник" in html
    assert 'disabled>Выпустить код' in html, "выпуск доступен без выбранного человека"


def test_приглашения_без_права_не_рисуют_форму_и_не_зовут_к_кнопке():
    """Отчёт 142 §3 строка 24, принципы 91: 8, 9, Б3; карта t_343d42fe.

    У troupe нет `registration.code.issue`, и форма «Выпустить код» стояла на
    экране одновременно с отказом «Раздел доступен организатору и администратору».
    У org и master формы не было, но пустое состояние звало к кнопке сверху,
    которой у них нет. Право `registration.code.issue` приходит данными (`allowed`
    и `actions.issue_codes` ответа панели), роль на странице его не выводит.
    """
    html = admin.INVITES_PAGE
    assert 'id="invite-new" hidden' in html, "форма отдаётся видимой — до ответа данных"
    assert 'indexOf("invites")' in html, "экран не сверяет право раздела с ответом панели"
    assert "ERRORS.admin_panel_forbidden" in html, "причина не из общего словаря ядра"
    assert "Коды выпускает администратор" in html
    assert "Выпустите код кнопкой сверху" not in html, "экран зовёт к несуществующей кнопке"
    script = html.split("function renderInvites", 1)[1]
    assert script.index("if (!canIssue)") < script.index("Выпустите код кнопкой выше"), \
        "пустое состояние без права нарисовано после пустого состояния с правом"


def test_действия_подписаны_именем_объекта():
    """Опасное подтверждение называет человека или код, а не «вы уверены?»."""
    assert "В архив: " in admin.PEOPLE_PAGE
    assert "Отозвать код: " in admin.INVITES_PAGE


def test_общие_настройки_уведомлений_это_те_же_три_типа_и_тихие_часы():
    """FR-G17 (О5): общий экран повторяет форму частного, но задаёт значения студии."""
    html = admin.NOTIFICATION_DEFAULTS_PAGE
    assert 'class="ui-switch-row' in html
    assert html.count('<button class="ui-switch-row') == 3
    assert "quiet_from" in html and "quiet_to" in html
    assert "20:00" in html and "08:00" in html
    assert "/webapp/admin/notifications/settings" in html
    assert "Частные" in html and "важнее общих" in html      # правило П-8 на экране
    assert html.count("ui-btn--primary") == 1                # одно главное действие


def test_общие_настройки_уведомлений_без_права_не_рисуют_форму():
    """Роль без `admin.panel` не получает ни переключателей, ни «Сохранить».

    До 08.11.2026 форма стояла на экране всегда, а над ней висело «Не хватает прав
    на это действие»: экран предлагал сохранить то, что сохранить нельзя
    (принципы 91: 9, 12, Б3; дизайн-ревью 142 §3 строка 30, карта t_5b401a86).
    Форма приходит скрытой и показывается только после 200 от настроек; на 403 —
    сообщение об администраторе и ссылка на свои настройки.
    """
    html = admin.NOTIFICATION_DEFAULTS_PAGE
    assert 'id="defaults-form" hidden' in html, "форма общих значений отдаётся видимой"
    assert "#defaults-form[hidden]" in html, "`.ui-section` — флекс: атрибута `hidden` мало"
    assert 'id="denied" hidden' in html, "нет блока «нет права»"
    assert 'href="/webapp/notifications"' in html, "нет ссылки на свои настройки"
    assert "if (res.status === 403) { showDenied(); return null; }" in html, \
        "403 не отличается от сетевого сбоя"
    assert "admin.panel" in html


def test_обращения_без_права_говорят_кто_их_разбирает():
    """Отчёт 142 §3 строка 27, принципы 91: 9, Б3; docs/reports/142 §5.1.

    У org, master и troupe нет `feedback.triage` (V-005: обращения идут админу
    дайджестом), а экран писал «Открытых обращений нет» — читалось как «разбирать
    нечего». Право приходит данными: раздел `feedback` в `allowed` ответа панели;
    у troupe панель закрыта вовсе (403), и это тоже «нет права».
    """
    html = admin.FEEDBACK_QUEUE_PAGE
    assert "Обращения разбирает администратор" in html
    assert 'indexOf("feedback")' in html, "экран не сверяет раздел с правом"
    # Очередь не запрашивается до подтверждённого права: в `refresh()` только
    # проверка панели и вызов `loadQueue()`, а сам запрос живёт в отдельной функции.
    body = html.split("function refresh()", 1)[1]
    assert "return loadQueue()" in body, "очередь не грузится после проверки права"
    assert 'api("/webapp/admin/feedback")' not in body, \
        "очередь запрашивается раньше проверки права"
    assert "res.status === 403" in body, "403 панели не признан отсутствием права"
    # Пустое состояние осталось для того, у кого право есть.
    assert "Открытых обращений нет" in html


def test_аудит_без_права_говорит_нет_доступа_а_не_пустой_журнал():
    """Отчёт 142 §3 строка 28, принципы 91: 9, Б3; карта t_8212983f.

    `audit.view` есть только у администратора: блок `audit` в ответе панели
    org и master не приходит, и экран рисовал пустой журнал «Событий нет» —
    как будто записей нет, а не как будто журнал закрыт. У troupe то же место
    отвечает 403 и «Раздел доступен организатору и администратору». Право
    проверяется до чтения блока, слова — из общего словаря ядра.
    """
    html = admin.AUDIT_PAGE
    assert 'indexOf("audit")' in html, "экран не сверяет блок с правом"
    assert "ERRORS.admin_panel_forbidden" in html, "слова не из общего словаря ядра"
    assert html.index('indexOf("audit")') < html.index("var audit = BLOCKS.audit"), \
        "проверка права стоит после чтения журнала — пустое состояние успеет отрисоваться"


# ------------------------------------------- V-037: дисциплины педагога (карта t_6e49e144)
def test_лист_дисциплин_это_список_а_не_тумблеры():
    """D2-D3 дизайна V-037: отмеченное строками, остальное — выпадающим списком.

    Четыре строки-тумблера заменены: каждое действие пишет сразу и не закрывает
    лист (`closeSheet` в `setDiscipline` нет), поле выбора исчезает, когда
    добавлять нечего.
    """
    assert "ui-select" in DISCIPLINES_SCRIPT
    assert "discipline-add" in admin.PEOPLE_PAGE, "кнопки добавления нет на экране"
    assert "discipline-add" in DISCIPLINES_SCRIPT
    assert "Добавить ещё" in DISCIPLINES_SCRIPT
    assert "Все дисциплины отмечены" in DISCIPLINES_SCRIPT
    assert "issue.disabled = true" not in DISCIPLINES_SCRIPT, "кнопка выдачи блокируется"
    assert "Педагогу нужна хотя бы одна" not in DISCIPLINES_SCRIPT
    # Лист не закрывается после отметки: закрытие — только кнопкой внизу
    body = DISCIPLINES_SCRIPT.split("function setDiscipline", 1)[1].split("loadCatalogue", 1)[0]
    assert "closeSheet" not in body, "действие закрывает лист"


def test_отказ_по_правам_людей_сказан_словами():
    """Б3 (docs/development/141 §4.2): отказ без права — словами, а не «не хватает прав».

    `master_discipline_required` больше не возвращается ни одним путём (D1),
    поэтому его текста в словаре экрана тоже нет. Словарь `ERRORS` живёт в JS ядра,
    поэтому проверяется отрисованная страница (`admin_core.ERRORS` — не переменная
    Python).
    """
    html = admin.PEOPLE_PAGE
    assert '"participants_manage_forbidden": "Роли и дисциплины педагога меняет ' \
           'администратор."' in html
    assert '"master_discipline_required":' not in html
    assert '"master_without_discipline":' not in html, \
        "предупреждение — не ошибка: текст приходит с сервера"


def test_карточка_рисует_предупреждение_из_ответа():
    """D3: текст предупреждения приходит с сервера, своих слов в JS нет."""
    html = admin.PEOPLE_PAGE
    assert "card.warnings" in html
    assert "ui-alert--warn" in html
    assert "тренинг назначить не сможет" not in html, "текст предупреждения вписан в JS"
    assert "нет ни одной" in html, "поле «Дисциплины» в карточке пропало"


# --------------------- E13: персональный код и «Новый участник» (docs/development/182 §4.3.2)
# Обоснование: FR-A2 (код приглашения), FR-A6 (права решает сервер), docs/182 §4.3.2.
# Форма «роль → код» больше не существует: роль переехала в шторку «Новый участник»,
# а выпуск требует выбранного человека (`participant_id`). Набор ролей шторки повторяет
# контракт `WebAppNewPersonIn.role` без `admin`.
HINT = "Дисциплину педагог выберет при регистрации."
TERM = "Действует 7 дней, вход один."
ROLE_CHOICES = '[["troupe", "Актёр"], ["org", "Организатор"], ["master", "Педагог"]]'


def test_набор_ролей_шторки_равен_WebAppNewPersonIn_минус_admin():
    """AC-1: источник правды — тип, а не список в разметке.

    Тест падает и когда роль появилась в API без кнопки (её не завести из
    приложения), и когда кнопка есть вне контракта (`admin`: сервер ответит 422).
    """
    api_roles = set(get_args(WebAppNewPersonIn.model_fields["role"].annotation))
    html = admin.INVITES_PAGE

    assert api_roles == {"org", "troupe", "master"}
    assert ROLE_CHOICES in html, "подписи ролей шторки сдвинулись или разошлись с API"


def test_шторка_нового_участника_подписи_и_умолчание():
    """AC-2: заголовок «Новый участник», кнопка «Создать и выпустить код», умолчание — труппа."""
    html = admin.INVITES_PAGE

    assert '"Новый участник"' in html
    assert '"Создать и выпустить код"' in html
    assert '"Например: Иванов Виктор"' in html
    assert 'var role = "troupe";' in html, "умолчание роли в шторке сдвинулось"


def test_подсказка_о_дисциплине_скрыта_и_одна():
    """AC-3: подсказка одна, отдаётся скрытой и показывается только у «Педагога».

    Строку «Действует 7 дней, вход один.» подсказка не дублирует: она остаётся
    ровно один раз — в листе после выпуска, рядом с кодом и именем человека.
    """
    html = admin.INVITES_PAGE

    assert html.count('"new-person-hint"') == 1, "подсказка не одна"
    assert 'el("span", "ui-small ui-muted", ' + f'"{HINT}")' in html
    assert "hint.hidden = role !== \"master\"" in html
    assert html.count(TERM) == 1, "у строки срока появилась вторая копия"
    assert len(HINT) == 43


def test_лист_выпуска_называет_человека_и_роль():
    """AC-4: лист печатает имя человека и роль из `ROLE`, а не «Роль: …» из формы.

    Второго источника подписи нет: `ROLE` живёт в ядре (`admin_core.py`), а роль
    приходит в ответе `POST /webapp/admin/invite`.
    """
    html = admin.INVITES_PAGE

    assert 'data.display_name' in html, "лист не печатает, на кого выписан код"
    assert 'ROLE[data.role] || data.role' in html, "слово роли вписано литералом"
    assert '"Роль: "' not in html, "остался текст формы «роль → код»"
    assert 'master: "педагог"' in admin.INVITES_PAGE or 'master: "педагог"' in html


def test_разделитель_сегментов_в_системе():
    """AC-12 (V-105 а): линия между невыбранными сегментами — правило системы.

    Рамка сдвинула бы раскладку и съела ширину на 320 px, поэтому разделитель
    нарисован тенью `inset`. Правило системное (`.ui-segmented` живёт и на
    «Деньгах»), поэтому стоит после `.ui-segmented__item--on`, а не внутри экрана.
    """
    css = (ROOT / "services" / "tool-api" / "app" / "static" / "webapp-ui.css").read_text(
        encoding="utf-8")
    selector = ".ui-segmented__item + .ui-segmented__item:not(.ui-segmented__item--on)"

    assert css.count(selector) == 1, "правило разделителя задано не один раз"
    rule = css.split(selector, 1)[1].split("}", 1)[0]
    assert "box-shadow: inset 1px 0 0 var(--ui-line);" in rule
    assert "border" not in rule, "рамка сдвинет раскладку сегментов"
    assert css.index(".ui-segmented__item--on") < css.index(selector), \
        "правило разделителя стоит до правила выбранного сегмента"

    design = (ROOT / "docs" / "development" / "91-design-system.md").read_text(encoding="utf-8")
    section = design.split("### 4.7.", 1)[1].split("### 4.8.", 1)[0]
    assert "разделител" in section.lower(), "в §4.7 нет слова о разделителе"
    assert "320 px" in section, "в §4.7 нет правила про узкую ширину"

def test_отказ_админ_раздела_не_перечисляет_роли():
    """068 снял `registration.approve` у организатора — отказ не должен обещать ему доступ.

    Один и тот же `ERRORS.admin_panel_forbidden` печатают «Приглашения»,
    «Аудит» и «Архив». Перечисление ролей в отказе устаревает при каждой правке
    матрицы прав (так и вышло с 068), поэтому в тексте остаётся один админ.
    """
    html = admin.INVITES_PAGE
    assert '"admin_panel_forbidden": "Раздел доступен администратору."' in html
    assert "организатору и администратору" not in html
