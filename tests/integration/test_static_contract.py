"""Контракт статики: всё, что подключено на страницах, должно отдаваться.

Повод — дефект 01.11.2026: `/webapp/ui.css` и `/webapp/shell.js` отдавали 500 после
разреза tool-api на роутеры. Mini App приезжал без стилей и без оболочки: на iPhone
текст наложением, в Telegram Desktop вечная загрузка. Тесты этого не видели, потому что
проверяли только ссылку в разметке, а не факт отдачи файла.

Правило, которое держит этот файл: страница, которая на что-то ссылается, обязана
получать это «что-то» живым. Инвентарь страниц не пишется руками, а берётся из
openapi приложения: экран — это маршрут `GET /webapp*`, объявленный как `text/html`
(`response_class=HTMLResponse`). Поэтому новая страница попадает под проверку сама,
без правки списка, — и «забыть добавить экран в тест» больше нельзя.

Обоснование: docs/reports/104-test-audit.md §5.2, §5.3 (P1 «страница → каждый
подключённый ассет отдаётся»), docs/reports/97-tool-api-routers.md.
"""

from __future__ import annotations

import re

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

ASSET_RE = re.compile(r'(?:href|src)="(/(?:[^"]+)\.(?:css|js))"')
MIN_PAGES = 20        # защита от «инвентарь опустел»: сейчас экранов больше двадцати


def html_pages(app: FastAPI) -> list[str]:
    """Экраны Mini App по инвентарю маршрутов приложения.

    Признак экрана — маршрут `GET /webapp…` без параметра пути, у которого ответ
    объявлен как `text/html`. Экраны-редиректы (`/webapp/admin`, `/webapp/days`) в
    схему не попадают: они отдают 301 и своего HTML не имеют.
    """
    pages = []
    for path, methods in app.openapi()["paths"].items():
        if not path.startswith("/webapp") or "get" not in methods:
            continue
        if "{" in path:                    # параметр пути — это данные, а не экран
            continue
        content = methods["get"].get("responses", {}).get("200", {}).get("content", {})
        if any(kind.startswith("text/html") for kind in content):
            pages.append(path)
    return sorted(pages)


def assets_of(html: str) -> set[str]:
    """Локальные css/js, на которые ссылается разметка страницы."""
    return set(ASSET_RE.findall(html))


def broken_assets(client: TestClient, pages: dict[str, str]) -> list[str]:
    """Подключённые файлы, которые не отдались: адрес → код и размер тела."""
    broken = []
    for path in sorted(pages):
        for url in sorted(assets_of(pages[path])):
            response = client.get(url)
            if response.status_code != 200 or not response.content:
                broken.append(f"{url} (со страницы {path}) -> "
                              f"{response.status_code}, {len(response.content)} байт")
    return broken


def test_инвентарь_экранов_берётся_из_маршрутов(client):
    """Список экранов — не константа в тесте: он собирается из маршрутов приложения."""
    pages = html_pages(client.app)
    assert len(pages) >= MIN_PAGES, f"экранов нашлось всего {len(pages)}: {pages}"
    assert pages == sorted(set(pages)), "инвентарь продублирован"
    assert "/webapp/home" in pages, "якорный экран не попал в инвентарь"


def test_экраны_отдают_200(client):
    """Ни один экран из инвентаря не отвечает 500 — это ловит поломки путей после рефакторинга."""
    broken = []
    for path in html_pages(client.app):
        response = client.get(path)
        if response.status_code != 200:
            broken.append(f"{path} -> {response.status_code}")
    assert not broken, "экраны не отдаются: " + ", ".join(broken)


def test_подключённые_файлы_отдаются(client):
    """Каждый css/js, на который ссылается любой экран, обязан вернуть 200 и непустое тело."""
    pages = {path: client.get(path).text for path in html_pages(client.app)}
    assert any(assets_of(html) for html in pages.values()), \
        "на страницах не нашлось ни одного подключённого файла"

    broken = broken_assets(client, pages)
    assert not broken, "static не отдаётся: " + "; ".join(broken)


def test_стили_и_оболочка_непустые(client):
    """Отдельно: пустой файл с кодом 200 — тоже поломка, страница поедет без стилей."""
    for url, minimum in (("/webapp/ui.css", 5000), ("/webapp/shell.js", 5000)):
        response = client.get(url)
        assert response.status_code == 200, f"{url} -> {response.status_code}"
        assert len(response.text) >= minimum, \
            f"{url} подозрительно мал: {len(response.text)} байт"


def test_новая_страница_попадает_в_проверку_без_правки_списка():
    """Новый экран подхватывается сам, а его битая ссылка — валит контракт.

    Проверка на синтетическом приложении: здесь видно и обнаружение экрана по
    объявленному `text/html`, и то, что отсутствующий файл действительно ловится.
    """
    stand = FastAPI()

    @stand.get("/webapp/новая-страница", response_class=HTMLResponse)
    def new_page() -> str:
        return ('<link rel="stylesheet" href="/webapp/ui.css">'
                '<script src="/webapp/нет-такого.js"></script>')

    @stand.get("/webapp/данные")
    def data() -> dict:
        return {"ok": True}

    @stand.get("/webapp/ui.css")
    def css() -> str:
        return "body { color: black; }"

    pages = html_pages(stand)
    assert pages == ["/webapp/новая-страница"], pages

    with TestClient(stand) as client:
        broken = broken_assets(client, {path: client.get(path).text for path in pages})
    assert len(broken) == 1, broken
    assert "нет-такого.js" in broken[0], broken
