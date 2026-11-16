"""Экспорт по короткой ссылке: право на выдаче, файл без заголовка, отказ словами.

Обращение №44: «CSV» и «Markdown» были голыми `href` на `GET /webapp/polls/export`.
Переход верхнего уровня в WKWebView не несёт Authorization, поэтому человек видел
JSON 401 `no_webapp_token` вместо файла. Теперь страница берёт ссылку в
`GET /webapp/polls/export/link` (права проверяются там), а браузер телефона
скачивает файл по подписи.

Что проверяется:

* ссылка выдаётся автору опроса, открывается без заголовка и отдаёт настоящий файл;
* человек без `_require_results` получает на выдаче 403 `no_results`, как у `export`;
* истёкшая или подписанная чужим секретом ссылка отвечает 401 страницей со словами,
  а не сырым JSON — её показывает браузер, а не Mini App.

Обоснование: FR-G18, AC-11, AC-12, `docs/development/162-bot-polls.md` §14.2, §14.7.
"""

from __future__ import annotations

import psycopg
from app import core
from app.webapp import issue_export_link

from tests.conftest import _test_dsn
from tests.integration.polls_helpers import create_poll, person, token


def _admin(chat_id: int, name: str = "Экспорт Админ") -> None:
    with psycopg.connect(_test_dsn()) as conn:
        person(conn, chat_id, name, "admin")


def test_ссылка_выдаётся_автору_и_файл_скачивается_без_заголовка(client):
    _admin(784060)
    author = token(client, 784060)
    poll_id = create_poll(client, author)

    link = client.get(f"/webapp/polls/export/link?poll_id={poll_id}&format=csv",
                      headers=author)
    assert link.status_code == 200, link.text
    assert link.json()["expires_in"] <= 900
    url = link.json()["url"]
    assert "/webapp/polls/export" in url and "token=" in url, url

    # Браузер телефона заголовков не носит: только подпись в адресе.
    file = client.get(url)
    assert file.status_code == 200, file.text
    assert "text/csv" in file.headers["content-type"]
    assert file.content.startswith(b"\xef\xbb\xbf"), "CSV потерял BOM"

    markdown = client.get(f"/webapp/polls/export/link?poll_id={poll_id}&format=md",
                          headers=author)
    assert markdown.status_code == 200, markdown.text
    md = client.get(markdown.json()["url"])
    assert md.status_code == 200, md.text
    assert "text/markdown" in md.headers["content-type"]


def test_без_права_ссылка_не_выдаётся(client):
    """Право проверяется на выдаче: посторонний получает 403 `no_results`."""
    _admin(784061)
    author = token(client, 784061)
    poll_id = create_poll(client, author)
    with psycopg.connect(_test_dsn()) as conn:
        person(conn, 784062, "Экспорт Посторонний", "troupe")
    stranger = token(client, 784062)

    denied = client.get(f"/webapp/polls/export/link?poll_id={poll_id}&format=csv",
                        headers=stranger)
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == "no_results"


def test_просроченная_ссылка_отвечает_401_страницей_со_словами(client):
    _admin(784063)
    author = token(client, 784063)
    poll_id = create_poll(client, author)
    stale = issue_export_link(poll_id, "csv", core.WEBAPP_SECRET, ttl=1, now=1_000)

    response = client.get(f"/webapp/polls/export?poll_id={poll_id}&format=csv&token={stale}")
    assert response.status_code == 401, response.text
    assert response.headers["content-type"].startswith("text/html")
    assert "Ссылка устарела" in response.text
    assert "no_webapp_token" not in response.text


def test_чужая_подпись_и_чужой_опрос_не_пускают(client):
    _admin(784064)
    author = token(client, 784064)
    poll_id = create_poll(client, author)
    foreign = issue_export_link(poll_id, "csv", "not-our-secret")

    broken = client.get(f"/webapp/polls/export?poll_id={poll_id}&format=csv&token={foreign}")
    assert broken.status_code == 401, broken.text
    assert "Ссылка устарела" in broken.text

    # Ссылка на один опрос не открывает другой: предмет внутри подписи.
    other_id = create_poll(client, author, title="Другой опрос")
    moved = client.get(f"/webapp/polls/export?poll_id={other_id}&format=csv&token={foreign}")
    assert moved.status_code == 401, moved.text

    # И формат тоже: csv-ссылка не отдаёт Markdown.
    good = client.get(f"/webapp/polls/export/link?poll_id={poll_id}&format=csv",
                      headers=author).json()["url"]
    csv_token = good.split("token=", 1)[1]
    swapped = client.get(f"/webapp/polls/export?poll_id={poll_id}&format=md&token={csv_token}")
    assert swapped.status_code == 401, swapped.text
