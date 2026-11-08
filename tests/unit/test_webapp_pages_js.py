"""Страницы Mini App: JS должен парситься (дефект docs/development/53-disciplines-and-search-form.md §6).

Обоснование (Ш-51): файл создан в dd4065e feat: дисциплины педагогов, режиссёр «по возможности», форма поиска окон (docs/53)."""

import pathlib
import shutil
import subprocess

import pytest
from app.webapp_page import (
    ADMIN_PAGE,
    BROADCAST_PAGE,
    DAYS_PAGE,
    DUES_PAGE,
    FEEDBACK_PAGE,
    GRID_PAGE,
    MY_PAGE,
    PAGE,
    PERSON_PAGE,
    RECONCILE_PAGE,
    SEARCH_PAGE,
    SLOTS_PAGE,
    TX_PAGE,
    UI_KIT_PAGE,
)

# Все шесть страниц, а не четыре: админ-панель и форма обращений ломаются так же,
# но до переезда на HTTPS они не проверялись (docs/infrastructure/75 §5.3).
PAGES = {"profile": PAGE, "grid": GRID_PAGE, "my": MY_PAGE, "search": SEARCH_PAGE,
         "admin": ADMIN_PAGE, "feedback": FEEDBACK_PAGE, "days": DAYS_PAGE,
         # экраны организатора (решение 30.10.2026)
         "tx": TX_PAGE, "person": PERSON_PAGE, "slots": SLOTS_PAGE,
         "dues": DUES_PAGE, "reconcile": RECONCILE_PAGE}
NODE = shutil.which("node") or next(
    (candidate for candidate in ("/opt/homebrew/bin/node", "/usr/local/bin/node")
     if pathlib.Path(candidate).exists()), None)


@pytest.mark.skipif(NODE is None, reason="нет node для проверки синтаксиса")
@pytest.mark.parametrize("name,html", PAGES.items())
def test_js_парсится(name, html, tmp_path):
    start = html.index("<script>") + len("<script>")
    end = html.rindex("</script>")
    script = tmp_path / f"{name}.js"
    script.write_text(html[start:end], encoding="utf-8")
    result = subprocess.run([NODE, "--check", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, f"{name}: {result.stderr[:400]}"


@pytest.mark.parametrize("name,html", PAGES.items())
def test_нет_переводов_строк_внутри_строк(name, html):
    """Реальный перевод строки внутри кавычек ломает скрипт целиком."""
    start = html.index("<script>") + len("<script>")
    script = html[start:html.rindex("</script>")]
    for number, line in enumerate(script.splitlines(), 1):
        stripped = line.rstrip()
        quotes = stripped.count('"') % 2
        if quotes and stripped.endswith(("(", "+", ",")):
            raise AssertionError(f"{name}: строка {number} обрывается: {stripped[:60]}")


@pytest.mark.parametrize("name,html", PAGES.items())
def test_нет_абсолютных_http_ссылок(name, html):
    """Mini App на проде живёт по HTTPS (docs/infrastructure/75 §4).

    Абсолютный `http://` в странице означает либо смешанный контент, либо
    запрос на другой адрес — оба варианта ломаются ровно в проде, не локально.
    """
    assert "http://" not in html, f"{name}: в странице есть абсолютная http-ссылка"


@pytest.mark.parametrize("name,html", PAGES.items())
def test_нет_модалов_prompt_и_confirm(name, html):
    """Ш-41: нативный модал Telegram WebView не показывает — действие молча не идёт.

    Роль, тариф, ночные часы и подтверждение назначения вводятся элементами
    страницы, а не `window.prompt`/`window.confirm`.
    """
    assert "prompt(" not in html, f"{name}: остался prompt()"
    assert "confirm(" not in html, f"{name}: остался confirm()"


def test_админ_меняет_роль_и_тариф_в_листе_человека():
    """Строка таблицы больше не содержит select, input и две кнопки (docs/92 §5).

    Действия переехали в лист человека: там же роли, тариф, права и архив.
    """
    from app.pages.admin import PEOPLE_PAGE

    assert "/webapp/admin/grant" in PEOPLE_PAGE
    assert "/webapp/admin/tariff" in PEOPLE_PAGE
    assert "/webapp/admin/block" in PEOPLE_PAGE, "блокировка — тем же листом (FR-A9)"
    assert "sheet(" in PEOPLE_PAGE, "действия должны открываться листом"
    assert "ui-row__chevron" in PEOPLE_PAGE, "список — строки, а не таблица"


# Страницы, которые ходят в защищённый API (Depends(webapp_actor)). Без обмена
# initData они уходили с пустым токеном, и человек видел сырое «no_webapp_token»
# вместо понятного текста — так было на проде 30.10.2026 у формы операции и карточки.
API_PAGES = {"tx": TX_PAGE, "person": PERSON_PAGE}


@pytest.mark.parametrize("name,html", API_PAGES.items())
def test_страница_получает_токен_через_оболочку(name, html):
    """Токен выпускает и обновляет `webapp-shell.js`, страница не держит свою копию.

    Раньше каждая страница сама обменивала initData и сама писала токен в
    localStorage — из-за этого оболочка не видела токен и не перевыпускала его
    (docs/development/81 §4). Теперь это одно место: копий быть не должно ни на
    одной странице, карточка участника переехала на оболочку 01.11.2026.
    """
    assert '<script src="/webapp/shell.js"></script>' in html, f"{name}: нет оболочки"
    assert "localStorage.setItem(\"svoi_token\"" not in html, \
        f"{name}: страница снова хранит токен сама"
    assert '"svoi_token"' not in html, f"{name}: страница знает имя ключа токена"
    assert "localStorage.getItem(\"svoi_token\"" not in html, \
        f"{name}: страница читает токен мимо оболочки"


@pytest.mark.parametrize("name,html", API_PAGES.items())
def test_страница_не_показывает_сырые_коды(name, html):
    """Пользователю — русский текст, а не detail из ответа API."""
    assert "no_webapp_token" not in html, f"{name}: в странице остался код ошибки"
    assert ("Нет связи" in html or "Нет доступа" in html or "Профиль недоступен" in html
            or "Сайт недоступен" in html), \
        f"{name}: нет человеческого текста ошибки"


def test_один_текст_об_обрыве_сети():
    """На весь Mini App — одна формулировка об обрыве (обращение №40, docs/83 §4).

    Баннер оболочки говорит «Сайт недоступен, попробуйте ещё раз.»; экраны держали
    свою копию старой формулировки про связь с сервером — для человека это были две
    разные новости. Тест держит формулировку в одном виде.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    pages = root / "services/tool-api/app/pages"
    stale_text = "Нет связи" + " с сервером"
    stale = sorted(path.name for path in pages.glob("*.py")
                   if stale_text in path.read_text(encoding="utf-8"))
    assert not stale, f"старая формулировка осталась в экранах: {stale}"
    one_text = "Сайт недоступен, попробуйте ещё раз."
    for name in ("home.py", "money_script.py", "money_tx_script.py", "kit.py"):
        text = (pages / name).read_text(encoding="utf-8")
        assert one_text in text, f"{name}: нет общей формулировки"
    services = (pages / "services.py").read_text(encoding="utf-8")
    assert "Сайт недоступен" in services and "Попробуйте ещё раз" in services
    shell = (root / "services/tool-api/app/static/webapp-shell.js").read_text(encoding="utf-8")
    assert f'var MESSAGE = "{one_text}";' in shell, "баннер и экраны разошлись словами"


def test_форма_операции_пишет_статус_в_элемент():
    """Причина отказа обязана быть видна: встроенное сообщение, а не пустая строка.

    Проверено в браузере: вызов несуществующей функции оставлял статус пустым,
    и человек не видел, почему запись не ушла.
    """
    assert 'id="tx-alert"' in TX_PAGE
    assert "function alertMessage" in TX_PAGE
    assert "alertMessage(\"Сайт недоступен, попробуйте ещё раз." in TX_PAGE


def test_категории_фильтруются_по_типу_операции():
    """Доход нельзя записать в расходную статью: список пересобирается при смене типа."""
    assert "function fillCategories" in TX_PAGE
    assert "byDirection[txType]" in TX_PAGE
    assert "fillCategories();" in TX_PAGE            # и в pick(), и в boot()
    assert "by_direction" in TX_PAGE


def test_повторное_нажатие_не_создаёт_вторую_операцию():
    """Ключ идемпотентности на одно нажатие давал дубли: 4 дохода подряд на проде.

    Ключ должен жить на операцию, а кнопка — блокироваться до ответа сервера.
    """
    assert 'var pendingKey = "";' in TX_PAGE
    assert "if (saveButton.disabled) { return; }" in TX_PAGE
    assert "saveButton.disabled = true;" in TX_PAGE
    assert "saveButton.disabled = false;" in TX_PAGE
    assert 'pendingKey = "";' in TX_PAGE          # сброс только после успеха
    assert "idempotency_key: pendingKey" in TX_PAGE


def test_форма_операции_разбирает_сумму_и_ошибки_валидации():
    """«не записано [object Object]» — это список ошибок FastAPI в строке.

    Плюс сумма с запятой или пробелом между разрядами должна приниматься:
    браузер на iOS отдаёт запятую, люди пишут «1 000».
    """
    assert "function parseAmount" in TX_PAGE
    assert "Array.isArray(result.body.detail)" in TX_PAGE   # список ошибок по полям
    assert 'inputmode="decimal"' in TX_PAGE                 # type=number режет запятую
    assert 'parseAmount($("tx-amount").value)' in TX_PAGE


def test_каждая_страница_грузит_sdk_telegram():
    """Без SDK `window.Telegram` не существует, initData пуст и вход невозможен.

    Так было на проде 30.10.2026: страницы отдавались, а Mini App писал
    «откройте форму из бота» на любом устройстве.
    """
    for name, html in PAGES.items():
        assert '<script src="/webapp/tg.js"></script>' in html, f"{name}: нет SDK Telegram"
        # порядок важен: SDK → общий слой (тема, токен) → код страницы.
        # Код страницы живёт в конце <body>, поэтому сравниваем позиции, а не
        # соседние строки: у старой системы он был инлайном в <head>.
        sdk = html.index('<script src="/webapp/tg.js"></script>')
        shell = html.index('<script src="/webapp/shell.js"></script>')
        page_code = html.rindex("<script>")
        assert sdk < shell < page_code, \
            f"{name}: SDK и shell подключены не до кода страницы"


def test_страница_объявляет_UI_если_использует():
    """Анкета обращалась к UI.hint без объявления — скрипт падал, страница пустая.

    Проверено браузерным тестом: pageerror «UI is not defined» на /webapp.
    """
    import re as _re

    for name, html in PAGES.items():
        uses = len(_re.findall(r"[^a-zA-Z]UI\.", html))
        if uses:
            assert "const UI =" in html, f"{name}: использует UI.{uses} раз, но не объявляет"


def test_общий_слой_лечит_тему_и_токен():
    """iOS в тёмной теме давал чёрный фон с тёмным текстом, а токен жил 30 минут."""
    shell = (pathlib.Path(__file__).resolve().parents[2]
             / "services/tool-api/app/static/webapp-shell.js")
    assert shell.exists(), "нет webapp-shell.js"
    text = shell.read_text(encoding="utf-8")

    assert "themeParams" in text and "--fg" in text      # тема с контрастным текстом
    assert "isDark" in text                              # иначе светлый текст на светлом
    assert "relogin" in text and "/webapp/auth" in text   # тихое перевыпуск токена
    assert "status !== 401" in text                       # повтор запроса после перевыпуска

    # файл должен быть валидным JS
    assert NODE, "node не найден"
    result = subprocess.run([NODE, "--check", str(shell)], capture_output=True, text=True)
    assert result.returncode == 0, f"shell.js не парсится: {result.stderr[:200]}"


def test_sdk_отдаётся_с_своего_домена():
    """telegram.org в РФ недоступен — SDK лежит в репозитории и отдаётся локально."""
    sdk = pathlib.Path(__file__).resolve().parents[2] / "services/tool-api/app/static/telegram-web-app.js"
    assert sdk.exists(), "нет файла SDK в services/tool-api/app/static"
    assert sdk.stat().st_size > 50_000, "файл SDK подозрительно маленький"

    api = (pathlib.Path(__file__).resolve().parents[2]
           / "services/tool-api/app/api/webapp_api.py").read_text(encoding="utf-8")
    assert '@router.get("/webapp/tg.js")' in api
