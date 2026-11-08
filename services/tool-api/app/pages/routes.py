"""Маршруты экранов Mini App: только HTML и редиректы (docs/development/86).

Зачем отдельный модуль: экраны и данные — разные оси. Пока маршруты страниц лежали
в `main.py` вместе с API, любая правка навигации (переименовать вкладку, перенести
раздел, поменять адрес) задевала файл на пять тысяч строк — и мёржилась с любой
параллельной работой по API. Здесь меняется только то, что относится к экранам.

Данные (`/webapp/home/data`, `/webapp/availability/*`, `/webapp/tx/save`, …) остались
в `main.py`: у них своя жизнь и свои тесты.

Подключение — одна строка в `main.py`:

    app.include_router(build_router(actor=webapp_actor, caps_for=_who_caps))

Зависимости передаются аргументами, а не импортом: `main.py` импортирует этот модуль,
и обратный импорт дал бы цикл. Заодно это позволяет тестировать маршруты с подменённым
`webapp_actor`.

Правила, которые здесь закреплены:

* адреса не удаляются: старый адрес отвечает `301` на новый, а не `404` — ссылки в чате
  и в закладках живут дальше;
* подпись четвёртой вкладки доезжает через `/webapp/nav` после обмена `initData`
  на токен: страницы — публичные константы без `Authorization` (05b7d2b);
* права на данные проверяются в API, страница лишь не рисует лишнего.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, RedirectResponse

from app.pages import admin, files, home, kit, layout, misc, money, notes, person, services, week
from app.webapp import page_sections

# Разделы «Управления» описаны в реестре `app/webapp.py:SECTIONS`, а не здесь:
# запись с полем `page` — это вложенный экран с шапкой и «‹», без ленты вкладок
# (docs/development/86 §5). Маршрут строится из адреса записи, страница — из имени
# её константы в `pages/admin.py`, поэтому новый раздел — одна запись в реестре.
# Префикс /t/ остаётся: /webapp/admin/invites — ещё и JSON-эндпоинт, и страница
# с тем же путём перехватывала его ответ (дефект 30.10.2026).

# Разделы, которые переехали: старый адрес ведёт на новый, а не отдаёт 404.
ADMIN_TAB_REDIRECTS: dict[str, str] = {"budget": "/webapp/money"}


def build_router(*, actor: Callable[..., dict], caps_for: Callable[[dict], list[str]]) -> APIRouter:
    """Собрать маршруты экранов.

    actor   — зависимость авторизации Mini App (`webapp_actor` из main.py);
    caps_for — функция «человек → его возможности» для подписи вкладок.
    """
    router = APIRouter()

    @router.get("/webapp/nav")
    def webapp_nav(who: dict = Depends(actor)) -> dict:
        """Оболочка по правам: подпись вкладки и флаг сводки (FR-H9, t_70a6d8e5)."""
        return layout.nav_payload(caps_for(who))

    # --- вкладки ------------------------------------------------------------

    @router.get("/webapp/home", response_class=HTMLResponse)
    def webapp_home_page() -> str:
        """«Главная»: что требует решения и что сегодня."""
        return home.HOME_PAGE

    @router.get("/webapp/more", response_class=HTMLResponse)
    def webapp_more_page() -> str:
        """«Ещё» у актёра, «Управление» у организатора и админа."""
        return home.MORE_PAGE

    @router.get("/webapp/week", response_class=HTMLResponse)
    def webapp_week_page() -> str:
        """Вкладка «Время»: неделя карточками, сохранение сразу."""
        return week.WEEK_PAGE

    @router.get("/webapp/week/summary", response_class=HTMLResponse)
    def webapp_week_summary_page() -> str:
        """Сводка занятости: дни и числа вместо матрицы «часы × дни»."""
        return week.SUMMARY_PAGE

    @router.get("/webapp/week/month", response_class=HTMLResponse)
    def webapp_month_page() -> str:
        """Сетка месяца «люди × дни»: только чтение, источник — Google-таблица (З9)."""
        return week.MONTH_PAGE

    @router.get("/webapp/money", response_class=HTMLResponse)
    def webapp_money_page() -> str:
        """«Деньги»: операции, бюджет, свои взносы — сегменты по правам."""
        return money.MONEY_PAGE

    # --- вложенные экраны ---------------------------------------------------

    @router.get("/webapp", response_class=HTMLResponse)
    def webapp_page() -> str:
        """Анкета участника: открывается кнопкой web_app из бота."""
        return misc.PROFILE_PAGE

    @router.get("/webapp/tx", response_class=HTMLResponse)
    def webapp_tx_page() -> str:
        """Форма операции: сумма, категория, дата, статья."""
        return money.TX_PAGE

    @router.get("/webapp/dues", response_class=HTMLResponse)
    def webapp_dues_page() -> str:
        """Мои взносы: тариф, внесено, остаток, история месяцами."""
        return misc.DUES_PAGE

    @router.get("/webapp/notifications", response_class=HTMLResponse)
    def webapp_notifications_page() -> str:
        """Уведомления: три типа событий и тихие часы."""
        return misc.NOTIFICATIONS_PAGE

    @router.get("/webapp/feedback", response_class=HTMLResponse)
    def webapp_feedback_page() -> str:
        """Обратная связь: проблема, пожелание, вопрос."""
        return misc.FEEDBACK_PAGE

    @router.get("/webapp/slots", response_class=HTMLResponse)
    def webapp_slots_page() -> str:
        """Поиск окон: даты, длительность, участники, результат карточками."""
        return misc.SLOTS_PAGE

    @router.get("/webapp/reconcile", response_class=HTMLResponse)
    def webapp_reconcile_page() -> str:
        """Сверка листов: сначала расхождения, потом применение."""
        return misc.RECONCILE_PAGE

    @router.get("/webapp/broadcast", response_class=HTMLResponse)
    def webapp_broadcast_page() -> str:
        """Рассылка: шаблон, текст, число адресатов, подтверждение."""
        return misc.BROADCAST_PAGE

    @router.get("/webapp/ui", response_class=HTMLResponse)
    def webapp_ui_kit() -> str:
        """Справочник компонентов: по нему сверяются люди и тесты."""
        return kit.UI_KIT_PAGE

    @router.get("/webapp/services", response_class=HTMLResponse)
    def webapp_services_page() -> str:
        """Сервисы: живо ли всё, что делает приложение (только admin.panel)."""
        return services.SERVICES_PAGE

    @router.get("/webapp/files", response_class=HTMLResponse)
    def webapp_files_page() -> str:
        """Файлы: загрузка мимо Telegram и список своих файлов с местом."""
        return files.FILES_PAGE

    @router.get("/webapp/notes", response_class=HTMLResponse)
    def webapp_notes_page() -> str:
        """Заметки: личные и общие, список и лист заметки (FR-F7)."""
        return notes.NOTES_PAGE

    @router.get("/webapp/folder", response_class=HTMLResponse)
    def webapp_folder_page() -> str:
        """Папка: треки по порядку, проигрывание подряд, архив (?id=<номер>)."""
        return files.FOLDER_PAGE

    @router.get("/webapp/person", response_class=HTMLResponse)
    def webapp_person_page() -> str:
        """Карточка участника: старый экран, ждёт переезда в лист человека."""
        return person.PERSON_PAGE

    # --- разделы «Управления» -----------------------------------------------

    @router.get("/webapp/admin")
    def webapp_admin_page() -> RedirectResponse:
        """Старый адрес панели: разделы теперь на своих адресах."""
        return RedirectResponse("/webapp/admin/t/people", status_code=301)

    def _admin_tab_endpoint(html: str) -> Callable[[dict], Any]:
        """Экран раздела: страница отдаётся как есть, права — на данных."""
        def endpoint() -> str:
            return html
        return endpoint

    def _admin_tab_redirect(target: str) -> Callable[[], RedirectResponse]:
        def endpoint() -> RedirectResponse:
            return RedirectResponse(target, status_code=301)
        return endpoint

    for _section in page_sections():
        router.add_api_route(_section.url, _admin_tab_endpoint(getattr(admin, _section.page)),
                             response_class=HTMLResponse, methods=["GET"],
                             name=f"webapp_admin_{_section.key}")

    for _tab, _target in ADMIN_TAB_REDIRECTS.items():
        router.add_api_route(f"/webapp/admin/t/{_tab}", _admin_tab_redirect(_target),
                             methods=["GET"], include_in_schema=False,
                             name=f"webapp_admin_{_tab}_moved")

    # --- старые адреса: редиректы, а не 404 ---------------------------------

    @router.get("/webapp/search")
    def webapp_search_page() -> RedirectResponse:
        """Бывший отдельный поиск людей: теперь поле поиска в «Управление → Люди»."""
        return RedirectResponse("/webapp/admin/t/people", status_code=301)

    @router.get("/webapp/days")
    def webapp_days() -> RedirectResponse:
        """Старый адрес недели: экран переехал на вкладку «Время»."""
        return RedirectResponse("/webapp/week", status_code=301)

    @router.get("/webapp/my/sheet")
    def webapp_my_page() -> RedirectResponse:
        """Сетка «16 часов × 30 дней» ушла в лист одного дня (86 §4)."""
        return RedirectResponse("/webapp/week", status_code=301)

    @router.get("/webapp/people")
    def webapp_people_page() -> RedirectResponse:
        """Люди живут в разделе «Управление»: вход агента не должен вести в 404."""
        return RedirectResponse("/webapp/admin/t/people", status_code=301)

    @router.get("/webapp/assign")
    def webapp_assign_page() -> RedirectResponse:
        """`/webapp/assign` — POST-эндпоинт назначения; как экран открываем окна."""
        return RedirectResponse("/webapp/slots", status_code=301)

    @router.get("/webapp/schedule")
    def webapp_schedule() -> RedirectResponse:
        """Месячная сетка заменена сводкой занятости по дням."""
        return RedirectResponse("/webapp/week/summary", status_code=301)

    return router
