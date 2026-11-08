"""Рассылка в Mini App: право `notify.send` — только у администратора (FR-G2, Б3).

Обоснование: FR-G2 (рассылка участникам — право `notify.send`, только админ),
docs/development/91-design-system.md (принцип 8 — ошибка говорит, что делать;
Б3 — роль видна словами), docs/reports/142 §3 строка 11, docs/architecture/02 §54.

Дефект, который сторожат тесты: экран читал только `body.ok`, поэтому 403 на
`/webapp/admin/broadcast/preview` попадал в ветку сетевой ошибки — у org, master и
troupe вместо «нет права» было «Не удалось посчитать адресатов. Повторите позже»,
и человек ждал, что «позже» пройдёт. Право `notify.send` не выдаётся никому, кроме
администратора (db/init/057), поэтому «повторите позже» здесь — ложь.

Стенд: `tests/webapp/server.py` отвечает 403 по заголовку `X-SVOI-Role` с любой
ролью, кроме администратора; без заголовка роль — администратор.

Кнопку «Отправить» тесты не нажимают (141 §5.3): проверяется только счётчик
адресатов и то, что форма без права спрятана.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium
"""

from __future__ import annotations

import pytest

# Роли без `notify.send`: организатор, мастер и труппа (docs/architecture/140 §112,
# docs/architecture/02 §86). Стенд знает их по заголовку `X-SVOI-Role`.
NO_RIGHT = [
    pytest.param({"X-SVOI-Role": "org"}, id="org"),
    pytest.param({"X-SVOI-Role": "master"}, id="master"),
    pytest.param({"X-SVOI-Role": "troupe"}, id="troupe"),
]


@pytest.mark.parametrize("headers", NO_RIGHT)
def test_без_права_экран_говорит_о_праве_а_не_о_сети(app_page, headers) -> None:
    """403 на счётчик адресатов — «доступно администратору», а не «повторите позже».

    Карта t_41a4ef6d: у org/master/troupe экран писал «Не удалось посчитать
    адресатов. Повторите позже» в ответ на 403, и человек ждал несуществующего
    «позже». Принципы 91: 8 (ошибка говорит, что делать), Б3 (роль видна словами);
    право `notify.send` есть только у администратора (FR-G2, db/init/057).
    """
    page = app_page("/webapp/broadcast", headers=headers)
    page.wait_for_selector("#denied:not([hidden])", timeout=10_000)

    text = page.locator("#denied").inner_text()
    assert "администратор" in text.lower(), f"не сказано, у кого рассылка есть: {text}"
    assert "Рассылка доступна администратору" in text, text
    assert "Не удалось посчитать адресатов" not in page.inner_text("body"), \
        "403 подан как сбой сети: человек будет ждать «позже» бесконечно"
    assert page.locator("#form").is_hidden(), "форма рассылки без права — тупик с кнопкой"
    assert page.locator("#send").is_hidden(), "кнопка «Отправить» без права осталась видна"
    assert not page.svoi_errors, page.svoi_errors


def test_админ_видит_охват_и_не_видит_отказа(app_page) -> None:
    """Обратный путь того же стенда: с правом экран считает адресатов как раньше.

    Без этой проверки «починить» отказ можно было бы прятаньем формы у всех
    (FR-G2: администратору рассылка доступна).
    """
    page = app_page("/webapp/broadcast")
    page.wait_for_function(
        "() => document.getElementById('counts').textContent.indexOf('Уйдёт') !== -1",
        timeout=10_000)

    counts = page.inner_text("#counts").replace("\u00a0", " ")
    assert "14" in counts and "17" in counts, f"нет честного охвата: {counts}"
    assert page.locator("#denied").is_hidden(), "администратору показан отказ по праву"
    assert page.locator("#form").is_visible(), "форма спрятана у администратора"
    assert page.locator("#send").is_enabled(), "кнопка отправки недоступна администратору"
    assert not page.svoi_errors, page.svoi_errors
