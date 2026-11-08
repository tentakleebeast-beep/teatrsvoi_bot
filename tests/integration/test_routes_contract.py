"""Инвариант маршрутов Mini App: ни один экран не отвечает 500.

Повод: 01.11.2026 после разреза tool-api на роутеры `/webapp/ui.css` и
`/webapp/shell.js` отдавали 500, и это заметили только на телефоне.

Тест требует «не 500» для всех GET-маршрутов семейства `/webapp` и `/health`:
200, 301, 401 или 403 — норма (права и редиректы), 500 — наша ошибка, которую
нельзя выкладывать.

Список берём из схемы OpenAPI, а не из `app.routes`: включённые роутеры FastAPI
держит ленивыми обёртками, и обход внутренностей ломается от версии к версии.
Маршруты `/tools/*` сюда не входят — им нужны внешние сервисы (Google, модель),
в тестах они намеренно не поднимаются.

Правила и требования раздела: docs/reports/97-tool-api-routers.md.
"""

from __future__ import annotations

PREFIXES = ("/webapp", "/health")


def _paths(app) -> list[str]:
    schema = app.openapi()
    found = set()
    for path, operations in (schema.get("paths") or {}).items():
        if "get" not in operations:
            continue
        if "{" in path:                      # маршруты с параметрами проверяют свои тесты
            continue
        if path.startswith(PREFIXES):
            found.add(path)
    return sorted(found)


def test_маршруты_mini_app_не_отдают_500(api):
    from app.main import app  # noqa: PLC0415 — приложение собирается в фикстуре

    paths = _paths(app)
    assert len(paths) >= 30, f"маршрутов нашлось подозрительно мало: {len(paths)}"

    broken = []
    for path in paths:
        response = api.get(path)
        if response.status_code >= 500:
            broken.append(f"{path} -> {response.status_code}")

    assert not broken, "маршруты отвечают 500:\n  " + "\n  ".join(broken)


def test_вебхука_роутера_в_сервисе_нет(api):
    """V-026: старый адрес роутера закрыт — ни `/webhook/*`, ни n8n-путей в tool-api.

    Проверяем по схеме OpenAPI (ленивые роутеры FastAPI не дают читать `app.routes`),
    а не по факту 404: так тест ловит и случайно добавленный маршрут с тем же префиксом.
    """
    from app.main import app  # noqa: PLC0415 — приложение собирается в фикстуре

    paths = list(app.openapi().get("paths") or {})
    leftover = [path for path in paths if "webhook" in path.lower() or "n8n" in path.lower()]
    assert not leftover, f"в сервисе остались маршруты n8n-контура: {leftover}"
    assert "/tools/route" in paths, "нет маршрута роутера POST /tools/route"
