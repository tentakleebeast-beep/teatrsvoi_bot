"""Экран «Файл» (`/webapp/file?id=N`): просмотр вместо прослушивания (А3).

До этой карты открыть файл в Mini App было нельзя: картинка и pdf лежали в «Файлах»
мёртвым грузом, а pdf в папке уходил в `<audio src>` — человек «слушал» ноты
(`docs/development/150` §1). Здесь проверяется то, что видно без базы и браузера:

* экран собран тем же каркасом, что остальные, и не тащит свой файл стилей;
* адрес данных — `GET /webapp/files/{id}/view`; «Скачать» берёт отдельную ссылку
  `GET /webapp/files/{id}/link` (Б2, V-053 а) и открывает её браузером;
* отказы 403/404/410 написаны словами из спецификации, без сырых кодов;
* истёкшая ссылка повторяется ровно один раз, «Открыть в браузере» берёт свежую;
* 5xx и обрыв не заводят своей кнопки «Повторить» — это делает оболочка (V-047 а);
* текст внутри Mini App не читается: `.txt` и `.pdf` ведут себя одинаково.

Обоснование: docs/development/150-miniapp-file-view.md (D1, D2, D4–D8), FR-K8,
FR-K9, FR-K10, `docs/development/86-files-and-storage.md` §3.5, карта t_a97e9a0c.
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app.pages import files, routes  # noqa: E402
from app.webapp import SCREEN_CAPS  # noqa: E402

EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]")
API = (ROOT / "services" / "tool-api" / "app" / "api" / "files.py").read_text(encoding="utf-8")
PAGE = files.FILE_PAGE


def test_экран_на_каркасе_и_с_одним_блоком_стилей():
    """Свой стиль — сцена и картинка; общий `webapp-ui.css` не трогаем (7.2)."""
    assert PAGE.startswith("<!doctype html>")
    assert 'class="ui-page"' in PAGE
    assert "ui-appbar__back" in PAGE and 'href="/webapp/files"' in PAGE, "нет возврата в «Файлы»"
    assert PAGE.count('<link rel="stylesheet"') == 1
    assert PAGE.count("<style>") == 1, "своих блоков стилей больше одного"
    assert "object-fit: contain" in PAGE, "картинка не вписывается целиком"


def test_нет_эмодзи_и_таблиц():
    assert not EMOJI.search(PAGE)
    assert "<table" not in PAGE


def test_данные_из_view_а_скачивание_берёт_отдельную_ссылку():
    """`/view` даёт ссылку и вид; «Скачать» зовёт `/link` (Б2, §5, V-053 а)."""
    assert 'api("/webapp/files/" + fileId + "/view")' in PAGE
    assert 'api("/webapp/files/" + fileId + "/link")' in PAGE
    assert "tg.openLink(res.body.url)" in PAGE
    assert "object_key" not in PAGE, "ключ объекта наружу не отдаётся"


def test_отказы_написаны_словами_из_спецификации():
    """403/404/410 — слова человека, а не `detail` сервера (§7.4, §7.9)."""
    assert "Раздел доступен администратору, организатору и мастеру." in PAGE
    assert "Файл не найден. Возможно, его удалили." in PAGE
    assert "Файл не найден ни в хранилище, ни в архиве." in PAGE
    assert "К файлам" in PAGE and 'href="/webapp/files"' in PAGE
    # Сырой код живёт только в условии ветки: в текст человека он не попадает (Б2 §6).
    for raw in ("file_not_found", "file_cold_missing", "file_not_uploaded"):
        assert not re.search(rf'showNote\("[^"]*{raw}', PAGE), \
            f"на экран печатается сырой код {raw}"


def test_без_номера_файла_экран_не_тупик():
    """Без `?id=` — пустое состояние и путь в «Файлы», запроса нет (§7.3)."""
    assert "Файл не выбран" in PAGE
    assert "Открыть Файлы" in PAGE
    assert "if (!fileId) {" in PAGE


def test_виды_ведут_себя_по_таблице():
    """Аудио играет, картинка показывается, документ и текст открываются (4, 7.4)."""
    assert 'if (data.kind === "image") { showImage(data); return; }' in PAGE
    assert 'if (data.kind === "audio") { showAudio(data); return; }' in PAGE
    assert "Документ откроется в браузере телефона" in PAGE
    assert "Текст откроется в браузере телефона" in PAGE
    assert "Файл откроется в браузере телефона" in PAGE
    # У звука нет «Открыть в браузере»: плеер и «Скачать» (Б2 §5).
    audio = PAGE[PAGE.index("function showAudio("):PAGE.index("function showCard(")]
    assert "setActions([downloadButton()])" in audio and "Открыть в браузере" not in audio


def test_текст_внутри_mini_app_не_читается():
    """`.txt` и `.pdf` показывают одну карточку и «Открыть в браузере» (К16).

    Причина — кодировка: страница не угадает её у русских файлов, и вместо текста
    человек увидит «ÐŸÑ€Ð¸Ð²ÐµÑ». Чтение байтов — отдельная тема.
    """
    assert 'if (word === "документ") {' in PAGE and 'if (word === "текст") {' in PAGE
    assert "showCard(data, \"документ\"" in PAGE and "showCard(data, \"текст\"" in PAGE
    for marker in ("open_stream", "TextDecoder", "file_text", "readAsText"):
        assert marker not in PAGE, f"экран читает содержимое файла ({marker})"


def test_истёкшая_ссылка_повторяется_один_раз():
    """Повтор ровно один: флаг `retried` не сбрасывается (§7.6, §7.8)."""
    assert "var retried = false;" in PAGE
    assert "if (retried) { imageFailure(); return; }" in PAGE
    assert "if (retried) { showNote(\"Не удалось открыть запись.\"); return; }" in PAGE
    assert PAGE.count("retried = true;") == 2, "повтор заводится в двух местах — картинка и звук"
    assert "Не удалось показать картинку. Откройте в браузере." in PAGE


def test_открыть_в_браузере_берёт_свежую_ссылку():
    """Каждый клик — новый `/view`: подпись живёт 15 минут, старая уже не годится (7.7)."""
    opener = PAGE[PAGE.index("function openInBrowser("):PAGE.index("function refuse(")]
    assert 'api("/webapp/files/" + fileId + "/view")' in opener
    assert "tg.openLink(res.body.url)" in opener, "в Telegram ссылка открывается через SDK"
    assert 'window.open(res.body.url, "_blank")' in opener, "без SDK — обычная вкладка"


def test_пятьсот_не_заводит_своей_кнопки():
    """5xx и обрыв — одно сообщение оболочки, своих слов и своей кнопки нет (V-047 а)."""
    assert '"Повторить"' not in PAGE
    assert "Не удалось открыть файл" not in PAGE


def test_холодный_файл_объясняет_ожидание():
    """Холод возвращается до ответа; текст ожидания — по §7.4 и FR-K16."""
    assert "Файл лежал в архиве — возвращаю" in PAGE
    assert "Готовлю файл" in PAGE


def test_реестр_прав_знает_экран():
    """Новый экран без строки в `SCREEN_CAPS` — дырка в правиле (К13)."""
    assert SCREEN_CAPS["/webapp/file"] == ("files.access",)
    router = routes.build_router(actor=lambda *a, **k: {}, caps_for=lambda who: [])
    routes_found = {route.path for route in router.routes if "GET" in (route.methods or set())}
    assert "/webapp/file" in routes_found


def test_экран_страницы_отдаётся_html():
    """Страница публична, право проверяется на данных — как у всех экранов (§7.1)."""
    source = (ROOT / "services" / "tool-api" / "app" / "pages" / "routes.py").read_text(
        encoding="utf-8")
    assert 'def webapp_file_page() -> str:' in source
    assert "def webapp_file_page(who: dict" not in source
    assert '@router.get("/webapp/files/{file_id}/view")' in API
    assert 'core._webapp_require(who, "files.access")' in API
    assert "file_for_reader(conn, file_id, reader)" in API
