"""Экраны файлов на дизайн-системе v2 и право `files.access` (92 §9, V-039 (в)).

Проверяем то, ради чего экраны переписаны: они собраны тем же каркасом, что и
остальные, раздел закрыт правом `files.access` на сервере (решение владельца V-039 (в):
файлы видят admin, org и master), а загрузка по-прежнему идёт мимо Telegram
(`presign → commit`).

Обоснование: V-039 (в) `docs/reports/74-questions.md`, `docs/development/86-files-and-storage.md`.
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app.pages import files, routes  # noqa: E402

EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]")
# маршруты файлов живут в своём домене (этап B, карта t_135f65cd)
MAIN = (ROOT / "services" / "tool-api" / "app" / "api" / "files.py").read_text(encoding="utf-8")
ROUTES = (ROOT / "services" / "tool-api" / "app" / "pages" / "routes.py").read_text(encoding="utf-8")


def test_экраны_собраны_каркасом():
    for page in (files.FILES_PAGE, files.FOLDER_PAGE):
        assert page.startswith("<!doctype html>")
        assert 'class="ui-page"' in page, "экран не на каркасе дизайн-системы"
        assert "ui-appbar__back" in page, "нет шапки с возвратом"
        assert page.count("<link rel=\"stylesheet\"") == 1, "ровно один файл стилей — общий"


def test_нет_эмодзи_и_таблиц():
    for page in (files.FILES_PAGE, files.FOLDER_PAGE):
        assert not EMOJI.search(page)
        assert "<table" not in page, "таблицы на телефоне не используем"


def test_своих_стилей_минимум():
    """Дизайн-система разрешает одну-две собственные строки (§7), не свой файл стилей."""
    assert files.FILES_PAGE.count("<style>") == 1
    assert files.FILES_PAGE.count(".files-hint {") == 1
    assert files.FILES_PAGE.count(".files-actions {") == 1


def test_экран_файлов_загружает_мимо_телеграма():
    page = files.FILES_PAGE
    assert "/webapp/files/presign" in page and "/webapp/files/commit" in page
    assert "/webapp/files/list" in page
    assert "PUT" in page, "файл кладётся в хранилище напрямую"
    assert 'accept="' not in page, "тип проверяет сервер, а не input"


def test_страницы_публичны_а_данные_закрыты():
    """Страница — публичный HTML-каркас: Telegram не шлёт Authorization при переходе.

    Закрывать надо данные (/webapp/files/*, /webapp/folder/*), а не разметку —
    иначе человек открывает кнопку и получает 401 вместо экрана.
    """
    assert "def webapp_files_page() -> str:" in ROUTES
    assert "def webapp_folder_page() -> str:" in ROUTES
    assert "def webapp_files_page(who: dict" not in ROUTES


def test_данные_файлов_закрыты_правом_files_access():
    """Кнопку спрятать мало: прямой запрос к API должен получать отказ.

    Право одно на весь дом — `files.access` (V-039 (в)). `admin.panel` здесь больше
    не проверяется: иначе org и master, которым решение владельца раздел открыло,
    получали бы 403 на собственном экране.
    """
    assert MAIN.count('_webapp_require(who, "files.access")') >= 6
    assert '"admin.panel"' not in MAIN, "файлы снова закрыты админ-панелью"
    for endpoint in ("webapp_files_list", "webapp_files_presign", "webapp_files_commit",
                     "webapp_file_delete",
                     "webapp_folder_files", "webapp_folder_order", "webapp_folder_zip"):
        start = MAIN.index(f"def {endpoint}(")
        block = MAIN[start:start + 1200]
        assert 'files.access' in block, f"{endpoint} без проверки прав"


def test_у_админа_одна_главная_кнопка_на_экране():
    """А9 (docs/development/141 §4): `.ui-btn--primary` на экране ровно одна.

    До правки главных было три — «Открыть доступ», «Загрузить», «Выбрать файлы»;
    осталась кнопка самой задачи экрана — «Загрузить».
    """
    page = files.FILES_PAGE
    # Считаем разметку, а не строки скрипта: `el("button", "ui-btn ui-btn--sm ui-btn--primary")`
    # рисует кнопку строки (сохранить/утвердить), и это не главная кнопка экрана.
    assert page.count('class="ui-btn ui-btn--primary') == 1, "на экране больше одной главной кнопки"
    assert 'id="upload"' in page and 'class="ui-btn ui-btn--primary ui-btn--block"' in page


def test_удаление_файла_кнопкой_с_подтверждением():
    """FR-K5, FR-L8 (V-045): удаление — кнопка в строке файла и вопрос в листе.

    Кнопка рисуется вместе со списком «Мои файлы», а список приходит только с правом
    `files.access`: без права экран отвечает отказом и удалять нечего. Тот же маршрут
    зовёт чат — второй копии удаления нет.
    """
    page = files.FILES_PAGE
    assert "deleteFileButton(file" in page, "в строке файла нет кнопки удаления"
    assert 'api("/webapp/files/" + file.id, { method: "DELETE" })' in page
    assert 'sheet("Удалить файл?"' in page and '"Отмена"' in page
    assert "Файл удалён" in page
    assert 'if (res.status === 403)' in page, "без права экран не рисует список"


def test_папка_играет_подряд_и_качает_архив():
    page = files.FOLDER_PAGE
    assert "/webapp/folder/" in page and "/zip" in page
    assert "addEventListener(\"ended\"" in page, "треки должны идти подряд"
    assert "<audio" in page and "hidden" in page, "плеер не выставляем на показ"


def test_папка_без_номера_не_тупик():
    """Пусто — не тупик (docs/development/91 §1 п.9, отчёт 142 §3 строка 17).

    Без `?id=` экран говорил только «Папка не выбрана»: как в папку попасть,
    не сказано, хотя вход в неё один — из «Файлов». Теперь пустое состояние
    называет источник и даёт ссылку. Редирект не выбран намеренно: адрес
    `/webapp/folder` остаётся открывающимся (141 §2.4 — старые адреса не удаляются).
    """
    page = files.FOLDER_PAGE
    # Подсказка лежит в разметке и ждёт решения скрипта: первый кадр не пустой.
    assert '<div id="no-folder" hidden>' in page
    start = page.index('<div id="no-folder"')
    block = page[start:start + 700]
    assert "Папки открываются из «Файлов»" in block
    assert 'href="/webapp/files"' in block, "в пустом состоянии нет пути в «Файлы»"
    # Показывается именно при отсутствии номера папки, а не всегда.
    assert '$("no-folder").hidden = false' in page
    assert 'if (!folderId)' in page


def test_маршруты_файлов_на_месте():
    router = routes.build_router(actor=lambda: {}, caps_for=lambda who: [])
    paths = {route.path for route in router.routes}
    assert {"/webapp/files", "/webapp/folder"} <= paths
