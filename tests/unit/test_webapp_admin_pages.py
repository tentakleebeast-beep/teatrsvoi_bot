"""Админские экраны Mini App: без таблиц, ленты вкладок и горизонтального скролла.

Проверяем то, из-за чего панель переделывалась (docs/development/92 §5): каждый
раздел — вложенный экран с возвратом в «Управление», строки вместо таблиц,
действия в листе человека, пагинация по 20 и никаких `alert`/`confirm`.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app.pages import admin  # noqa: E402

EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")

PAGES = {
    "Люди": admin.PEOPLE_PAGE,
    "Взносы": admin.DUES_ADMIN_PAGE,
    "Заявки": admin.PENDING_PAGE,
    "Права": admin.RIGHTS_PAGE,
    "Архив": admin.ARCHIVE_PAGE,
    "Аудит": admin.AUDIT_PAGE,
    "Приглашения": admin.INVITES_PAGE,
    "Уведомления": admin.NOTIFICATION_DEFAULTS_PAGE,
}


def test_разделы_отдельными_экранами():
    assert set(PAGES) == {"Люди", "Взносы", "Заявки", "Права", "Архив", "Аудит",
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


def test_заявки_принимаются_и_отклоняются_кнопками():
    html = admin.PENDING_PAGE
    assert "Принять" in html and "Отклонить" in html
    assert "ui-btn--sm" in html                   # 44 px внутри строки
    assert "/webapp/admin/approve" in html
    assert "SVOI.sheet" in html                   # отклонение — через лист


def test_права_это_матрица_и_точечная_выдача_а_не_второй_список():
    html = admin.RIGHTS_PAGE
    assert '"ui-dl"' in html
    assert "BLOCKS.rights" in html and "matrix" in html
    assert "rights-search" in html
    assert "capability" in html and "/webapp/admin/grant" in html
    # полного списка людей на экране нет: строки появляются только после ввода
    assert "rights-results" in html
    assert "function renderResults" in html


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
    assert "Отклонить заявку: " in admin.PENDING_PAGE
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


def test_заявки_без_права_говорят_о_праве_а_не_пустотой():
    """docs/development/91 принцип 9, Б3: право проверяется до пустого состояния.

    У master есть только `participants.view`: блок `pending` в ответе панели не
    приходит, и экран рисовал «Заявок нет» — как будто решать нечего. У troupe
    в том же месте честное «Раздел доступен организатору и администратору» (403),
    то есть экран вёл себя по-разному у двух ролей без права.
    """
    html = admin.PENDING_PAGE
    assert 'indexOf("pending")' in html, "экран не сверяет блок с правом"
    assert "ERRORS.admin_panel_forbidden" in html, "слова не из общего словаря ядра"
    assert html.index('indexOf("pending")') < html.index("var rows = BLOCKS.pending"), \
        "проверка права стоит после чтения строк — пустое состояние успеет отрисоваться"


def test_у_заявки_одна_главная_кнопка_на_экран_а_не_на_строку():
    """А9 отчёта 142, принцип 1 docs/development/91: главная кнопка одна на экран.

    «Принять» стояло главным в каждой строке: четыре заявки — четыре главных.
    Действие строки остаётся обычной кнопкой и рисуется только по праву.
    """
    html = admin.PENDING_PAGE
    assert "ui-btn--primary" not in html
    assert "if (ACTIONS.approve_pending)" in html, "кнопки строки без проверки права"
    assert "Принять" in html and "Отклонить" in html
