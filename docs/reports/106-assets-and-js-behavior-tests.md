# 106. Контракт отдачи ассетов на всех экранах и поведение JS вместо `node --check`

Карта: `t_d647008f`. Класс: tests. Источник: `docs/reports/104-test-audit.md` §5.2, §5.3
(рекомендации P1) и §6 (строки «Поведение JS в браузере», «Дизайн-система на 15 экранах
`LEGACY`»).

## Что было

| Дыра | Как проявлялась |
|---|---|
| контракт статики проверял список из 12 адресов руками | `/webapp/ui.css` и `/webapp/shell.js` отдавали 500 при зелёном наборе (`3d9816a`) |
| JS проверялся через `node --check` | «форма пишет в несуществующую функцию» (`ef9e5cf`), «хаб падает с `KeyError: 0`» (`12e4517`) |
| `LEGACY` в `tests/webapp/test_miniapp_system.py` — 15 экранов | эмодзи, шрифт < 13 px, цели < 44 px и горизонтальный скролл на них не проверялись |

## Что стало

### 1. Инвентарь экранов берётся из маршрутов, а не из константы

`tests/integration/test_static_contract.py` больше не содержит списка экранов. Инвентарь
собирается из `app.openapi()["paths"]`: экран — это `GET /webapp…` без параметра пути,
у которого ответ объявлен как `text/html` (`response_class=HTMLResponse`). Сейчас так
находятся **25 экранов** (включая семь разделов `/webapp/admin/t/*`); редиректы
(`/webapp/admin`, `/webapp/days`, `/webapp/search`, `/webapp/my/sheet`, `/webapp/schedule`)
в схему не попадают — у них нет своего HTML.

Проверки файла:

* `test_инвентарь_экранов_берётся_из_маршрутов` — инвентарь не пуст (порог 20), якорный
  `/webapp/home` на месте;
* `test_экраны_отдают_200` — каждый экран из инвентаря отвечает 200;
* `test_подключённые_файлы_отдаются` — для каждой страницы каждый локальный `href`/`src`
  с `.css`/`.js` возвращает 200 и непустое тело;
* `test_стили_и_оболочка_непустые` — прежняя проверка размера `ui.css`/`shell.js`;
* `test_новая_страница_попадает_в_проверку_без_правки_списка` — проверка самой проверки:
  синтетическое приложение с новой страницей и битой ссылкой; экран находится сам,
  отсутствующий файл валит контракт.

Негативные прогоны (выполнены и откачены):

| Что сломали | Результат |
|---|---|
| `services/tool-api/app/static/webapp-ui.css` убран с диска | `test_подключённые_файлы_отдаются` и `test_стили_и_оболочка_непустые` — FAILED (500, `FileNotFoundError`) |
| в боевой `app/pages/routes.py` добавлен временный `GET /webapp/временная` со ссылкой на несуществующий `нет-такого.css` | экран сам попал в инвентарь, `test_подключённые_файлы_отдаются` — FAILED: «/webapp/нет-такого.css (со страницы /webapp/временная) -> 404» |

### 2. Поведение JS в браузере

Новый файл `tests/webapp/test_miniapp_js_behavior.py` — четыре проверки на двух
рискованных экранах, `node --check` в `tests/unit/test_webapp_pages_js.py` оставлен рядом:

* `/webapp/tx` — переключение типа операции пересобирает категории (`Аренда помещения`
  исчезает, `Взносы студийцев` появляется, `aria-pressed` переставлен);
* `/webapp/tx` — пустая сумма даёт текст отказа в `#err-amount`, а не пустую строку и не
  `undefined`;
* `/webapp/tx` — успешная запись показывает тост «Записано: 3 000 ₽, Аренда помещения» и
  очищает поле суммы;
* `/webapp/admin/t/people` — панель рисует обе строки из `overview`, поиск «Валерий»
  сужает список до одной, `#people-count` пишет «найдено 1», `#status` пуст (не `undefined`).

Негативный прогон (выполнен и откачен): переименование `fillCategories` в
`money_tx_script.py` и `renderPeople` в `admin_people.py` роняет все четыре проверки
(таймаут ожидания эффекта) — тест держит поведение, а не имя функции.

### 3. `LEGACY` снят целиком: 15 → 0

Точка отсчёта на `main` `bf080f2`: в стенде `tests/webapp/server.py` **25 экранов**,
`LEGACY` — 15 записей, из них 6 мёртвых, реальных исключений 9.

Проверки дизайн-системы проходили **16 экранов** (вне `LEGACY`): `/webapp/home`,
`/webapp/more`, `/webapp/week`, `/webapp/week/summary`, `/webapp/money`, `/webapp/files`,
`/webapp/folder`, `/webapp/services`, `/webapp/ui`, `/webapp/admin/t/people`,
`/webapp/admin/t/pending`, `/webapp/admin/t/rights`, `/webapp/admin/t/archive`,
`/webapp/admin/t/audit`, `/webapp/admin/t/invites`, `/webapp/admin/t/feedback`.

Замер на ветке карты (Chromium, `SVOI_WEBAPP_TESTS=1`, список исключений пуст) показал,
что все 9 реальных исключений делятся так:

* **6 записей были мёртвыми**: `/webapp/admin`, `/webapp/days`, `/webapp/my/sheet`,
  `/webapp/schedule`, `/webapp/search` — редиректы `301`; `/webapp/grid` — уже не экран,
  а JSON-API `api/schedule.py`. Адресов нет в `PAGE_ROUTES`, параметризация их никогда
  не запускала: исключение «на всякий случай» скрывало, что проверять нечего.
* **8 экранов** (`/webapp`, `/webapp/tx`, `/webapp/dues`, `/webapp/slots`,
  `/webapp/notifications`, `/webapp/feedback`, `/webapp/reconcile`, `/webapp/broadcast`)
  уже проходили проверки дизайн-системы — их держал в списке устаревший `xfail`.
* **1 экран требовал правки продукта**: `/webapp/person` — поле `#tariff` имело высоту
  43 px (цель меньше 44). В `services/tool-api/app/pages/person.py` добавлен
  `min-height:44px` для `input`; больше на этом экране ничего не менялось.

Итог: `LEGACY` удалён из `tests/webapp/test_miniapp_system.py`, ветки
`pytest.xfail(...)` убраны — в файле не осталось ни одного `skip`/`xfail`. Остаток
списка — **ноль**: критерий «пусто либо только адреса, которых нет в `PAGE_ROUTES`»
выполнен по строгому варианту. Прогон `test_miniapp_system.py` на Chromium:
**179 passed, 0 xfailed** (было 112 passed, 63 xfailed).

Отдельно: `/webapp/person` тогда остался единственным экраном вне общей оболочки — он не
использовал `layout.page` и держал свою копию токена. Правка 44 px закрыла проверку, но
не причину. Причина снята картой `t_e65eaaa7`: экран переведён на `layout.page`, стили —
только классы `webapp-ui.css`, токен и запросы — `static/webapp-shell.js` (docs/development/81
§4); цель 44 px теперь берётся из `ui-input`, своей таблицы стилей у экрана нет.

## Чем проверено

* `env -u PYTHONPATH .venv/bin/python -m pytest -p no:warnings -o addopts="" -q --ignore=tests/webapp`
* `.venv/bin/ruff check .`
* `env -u PYTHONPATH .venv/bin/python scripts/check_invariants.py` и `scripts/check_registers.py`
* `scripts/test_audit.py --strict` (новые тесты обоснованы ссылкой на этот отчёт и §5.2)
* `SVOI_WEBAPP_TESTS=1 … pytest tests/webapp -q --browser chromium` и `--browser webkit`

## Что осталось

Поведенческие проверки покрывают два экрана из двадцати пяти. Следующие кандидаты по
риску — `/webapp/files` (загрузка мимо Telegram) и экраны «Денег»; каждая проверка
требует своей фикстуры ответов стенда, иначе она будет изображать поведение, а не
держать его.

Перевод `/webapp/person` на `layout.page` выполнен картой `t_e65eaaa7` (родитель: эта
карта): поведенческие проверки карточки (сохранение тарифа и отказ словами) лежат в
`tests/webapp/test_miniapp_js_behavior.py`.
