"""«Главная», личные экраны и деньги: навигация по правам и сохранение настроек.

После переработки (docs/development/92) хаба с плитками нет: «Главная» отвечает на
вопрос «что требует решения и что сегодня», четвёртая вкладка у организатора и админа
называется «Управление», а разделы открываются своим адресом с шапкой и «‹».
"""

from __future__ import annotations

import re

import pytest

EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]")


@pytest.mark.parametrize("device", ("iPhone 14", "Galaxy S9+"))
def test_главная_отвечает_что_требует_решения(device_page, device):
    page = device_page("/webapp/home", device)
    page.wait_for_timeout(600)
    text = page.inner_text("body")
    assert "Требует внимания" in text, "нет блока с задачами"
    assert "Заявки" in text, "нет строки заявок"
    assert "Занятость на неделе" in text
    # Вкладки: четыре слова, четвёртая — «Управление» (стенд изображает админа).
    tabs = [tab.inner_text().strip() for tab in page.locator(".ui-tabbar__item").all()]
    assert tabs == ["Главная", "Время", "Деньги", "Управление"], tabs
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 1, f"{device}: Главная шире экрана"
    assert not EMOJI.search(page.inner_text("body")), "эмодзи на Главной"


def test_главная_ведёт_в_занятость(device_page):
    page = device_page("/webapp/home", "iPhone 14")
    page.wait_for_timeout(600)
    href = page.locator('a.ui-btn[href="/webapp/week"]').first.get_attribute("href")
    assert href == "/webapp/week"


def test_уведомления_переключаются_и_сохраняются(device_page):
    page = device_page("/webapp/notifications", "iPhone 14")
    page.wait_for_timeout(600)
    assert page.locator(".ui-switch-row").count() == 3

    page.locator(".ui-switch-row").first.click()            # выключить репетиции
    page.fill("#quiet_from", "22:00")
    page.click("#save")
    page.wait_for_timeout(600)
    # Результат показывается сообщением внизу (SVOI.toast), а не строкой «status»:
    # у экрана нет отдельного поля статуса, ошибки — в #error.
    toast = page.locator(".ui-toast")
    assert toast.count() == 1, "нет подтверждения сохранения"
    assert "Сохранено" in toast.inner_text(), toast.inner_text()


def test_очистка_недели_есть_на_экране(device_page):
    page = device_page("/webapp/week", "iPhone 14")
    page.wait_for_timeout(600)
    text = page.inner_text("body")
    assert "Очистить неделю" in text, "нет действия «Очистить неделю»"
    assert "Сохранить неделю" not in text, "кнопка «Сохранить неделю» противоречит автосохранению"


@pytest.mark.parametrize("device", ("iPhone 14", "Galaxy S9+"))
def test_взносы_показывают_сумму_и_остаток(device_page, device):
    page = device_page("/webapp/dues", device)
    page.wait_for_timeout(700)
    # Intl.NumberFormat ставит неразрывный пробел между разрядами — сравниваем без него
    rest = page.inner_text("#rest").replace("\u00a0", " ")
    assert "3 000" in rest, f"{device}: нет остатка: {rest}"
    assert page.locator("#history .ui-row").count() >= 2, "нет истории месяцами"
    assert page.locator("table").count() == 0, "таблица вернулась на мобильный экран"
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 1, f"{device}: экран взносов шире экрана"


@pytest.mark.parametrize("tab", ("people", "pending", "audit", "feedback", "notifications"))
def test_раздел_управления_открывается_своим_адресом(device_page, tab):
    page = device_page("/webapp/admin/t/" + tab, "iPhone 14")
    page.wait_for_timeout(800)
    # Ленты вкладок больше нет: экран вложенный, возврат — в «Управление».
    assert page.locator("nav.ui-tabs, #nav").count() == 0, "вернулась лента вкладок"
    back = page.locator(".ui-appbar__back")
    assert back.count() == 1 and back.get_attribute("href") == "/webapp/more"
    # Стрелка остаётся стрелкой: оболочка подписывала «Управление» четвёртую вкладку,
    # но селектор без области цеплял и эту ссылку — в шапке появлялось «авлениеАудит»
    # (08.11.2026). Проверяем текст, а не только наличие.
    assert back.inner_text().strip() == "‹", f"шапка раздела «{tab}»: «{back.inner_text()}»"
    assert page.locator("table").count() == 0, "таблица в админском разделе"
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 1, f"админка ({tab}) шире экрана"


def test_общие_настройки_уведомлений_переключаются_и_сохраняются(device_page):
    """FR-G17 (О5): админский экран задаёт общие значения студии и подтверждает сохранение."""
    page = device_page("/webapp/admin/t/notifications", "iPhone 14")
    page.wait_for_timeout(800)
    assert page.locator(".ui-switch-row").count() == 3
    assert page.input_value("#quiet_from") == "20:00"
    assert page.input_value("#quiet_to") == "08:00"
    assert "важнее общих" in page.inner_text("body")

    page.locator(".ui-switch-row").first.click()            # выключить репетиции
    page.fill("#quiet_to", "07:30")
    page.click("#save")
    page.wait_for_timeout(600)
    toast = page.locator(".ui-toast")
    assert toast.count() == 1, "нет подтверждения сохранения"
    assert "Сохранено" in toast.inner_text(), toast.inner_text()


def test_рассылка_показывает_охват_и_требует_подтверждения(device_page):
    page = device_page("/webapp/broadcast", "iPhone 14")
    page.wait_for_timeout(800)
    counts = page.inner_text("#counts").replace("\u00a0", " ")
    assert "14" in counts and "17" in counts, f"нет честного охвата: {counts}"
    page.fill("#text", "Напоминаю про взнос")
    page.click("#send")
    page.wait_for_timeout(300)
    assert page.locator("text=Отправить 14 людям").count() >= 1, "нет подтверждения с числом"


def test_шаблоны_подставляют_текст(device_page):
    page = device_page("/webapp/broadcast", "iPhone 14")
    page.wait_for_timeout(800)
    chips = page.locator("#templates button")
    assert chips.count() >= 3, "нет шаблонов"
    chips.first.click()
    page.wait_for_timeout(200)
    assert page.input_value("#text"), "шаблон не подставил текст"


def test_личные_разделы_на_ещё_приходят_данными(device_page):
    """«Файлы студии» — строка «Личного» на «Ещё», между «Профилем» и «Уведомлениями».

    До этого строка не появлялась вовсе: `/webapp/more` отдавал константу с HTML-слотом,
    и в `/webapp/files` из интерфейса было не попасть никому, включая администратора.
    Теперь строки «Личного» приходят в `more.personal` и рисуются скриптом страницы.
    """
    page = device_page("/webapp/more", "iPhone 14")
    page.wait_for_timeout(800)

    rows = page.locator("#personal-list .ui-row__title")
    titles = [title.inner_text().strip() for title in rows.all()]
    assert "Файлы студии" in titles, titles
    assert titles.index("Профиль") < titles.index("Файлы студии") < titles.index("Уведомления"), titles

    # Это ссылка, а не текст: строка — одно действие, площадь нажатия вся.
    link = page.locator('#personal-list a.ui-row[href="/webapp/files"]')
    assert link.count() == 1, "строка файлов не ссылка"
    assert "Файлы студии" in link.inner_text()

    # Переход по ней отдаёт экран файлов.
    link.click()
    page.wait_for_timeout(600)
    assert page.url.endswith("/webapp/files"), page.url


def test_обрыв_сети_даёт_понятное_сообщение(device_page):
    """Экран не должен молча висеть: при обрыве — сообщение и «Повторить».

    Слова — из обращения №40 (порог и тексты разбирает test_miniapp_net_guard.py).
    """
    page = device_page("/webapp/home", "iPhone 14")
    page.route("**/webapp/home/data", lambda route: route.abort())
    page.reload(wait_until="load")
    # Ждём событие, а не секунды: сообщение появилось.
    page.wait_for_selector("#net-error", state="visible", timeout=10_000)
    # Маяк загрузки и тихий вход отвечают уже после данных. Успех соседнего
    # запроса не должен снимать аварию — иначе сообщение мелькало и человек
    # снова видел пустой экран (гонка, красные webapp/webapp-webkit).
    status = page.evaluate("() => fetch('/webapp/profile').then(r => r.status)")
    assert status == 200, status
    assert page.locator("#net-error").count() == 1, "нет сообщения об обрыве"
    assert page.locator("#net-error").is_visible(), "чужой успешный ответ снял баннер"
    message = page.inner_text("#net-error span").lower()
    assert "сайт недоступен, попробуйте ещё раз" in message, message
    assert page.locator("#net-error button").inner_text() == "Повторить"


def test_справочник_элементов_на_тап_удобен(device_page):
    """Кнопки и строки — не меньше 44 px: иначе в дороге промахиваются."""
    page = device_page("/webapp/ui", "iPhone 14")
    page.wait_for_timeout(600)
    heights = page.evaluate(
        "() => Array.from(document.querySelectorAll('.ui-btn, .ui-row'))"
        ".map(el => el.getBoundingClientRect().height)")
    assert heights, "нет кнопок в справочнике"
    assert min(heights) >= 43, f"есть цели ниже 44 px: {min(heights)}"
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 1, f"справочник шире экрана: {overflow}px"
    assert page.locator("h1.ui-title").count() == 1


def test_масштаб_жестами_отключён(device_page):
    """Двойной тап и «щипок» ломали вёрстку: масштаб должен быть закреплён."""
    page = device_page("/webapp/home", "iPhone 14")
    page.wait_for_timeout(400)
    content = page.evaluate(
        "() => document.querySelector('meta[name=viewport]').getAttribute('content')")
    assert "maximum-scale=1" in content, content
    assert "user-scalable=no" in content, content
    touch = page.evaluate("() => getComputedStyle(document.body).touchAction")
    assert touch == "manipulation", touch
