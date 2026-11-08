# 100. Гейтвей: зелёный гейт после выноса роутеров (C-1)

Карта `t_6bbd4f24`, ветка `wt/t_f4b2ed7b`, база — `e5d93b5` (карта C `t_f4b2ed7b`,
`docs/reports/98`). Область: `tests/unit`, поведение бота и тексты не менялись.

## Что было

Карта C закрылась как done, но гейт её ветки был красный: 4 failed, 19 errors,
1382 passed. Падал `tests/unit/test_intro.py::test_регистрация_из_списка_начинает_знакомство`
и ещё три файла с той же картиной:

```
E   AttributeError: 'Namespace' object has no attribute 'intro_start'
E   AttributeError: 'Namespace' object has no attribute 'caps_of'
E   AttributeError: 'Namespace' object has no attribute 'api'
```

## Уточнение диагноза

Постановка карты говорила: «тесты патчат `app.main`, где обработчиков больше нет».
На ветке это уже было не так: после разреза C тесты брали имена через
`tests/unit/gateway_modules.py` (`namespace()` / `source()`), и `app.main` в них
не упоминается вовсе — проверено поиском по `tests/unit/test_intro.py` и
`tests/unit/test_gateway_*.py`.

Настоящая причина — в прокси `Namespace`. `monkeypatch.setattr` запоминает старое
значение через `target.__dict__.get(name)`, а у прокси собственного `__dict__`
с именами модулей нет: `oldval` получался «значения не было». Откат в этом случае
делает `delattr(obj, name)`; для `Namespace` это `AttributeError` на teardown —
отсюда 19 errors. Хуже: откат не срабатывал, и подмена оставалась в модуле
навсегда. Затекшие заглушки `api`/`caps_of` и давали 4 «настоящих» падения в
`test_gateway_menu_rights.py` (`IndexError: list index out of range`, пустые
ответы бота).

Факт про сам вызов: `test_регистрация_из_списка_начинает_знакомство` проходил
(1 passed), ошибка возникала только на teardown. То есть подмена в модуль
попадала, ломала тесты не она, а её отмена.

## Что сделано

Точки подмены переведены на модуль, который объявляет имя, — туда, откуда
обработчики его читают. Всего 20 подмен в четырёх файлах:

| файл | было | стало |
|---|---|---|
| `test_intro.py` | `gw.api = fake_api` (5), `monkeypatch.setattr(gw, "intro_start", ...)` | `monkeypatch.setattr(gateway_modules.load("core"), "api", ...)`, `...load("routers.intro"), "intro_start"`, `...load("routers.menu"), "send_menu"` |
| `test_gateway_menu_rights.py` | `monkeypatch.setattr(gw, "caps_of"/"api"/"run_menu_action", ...)` | `load("core")` / `load("routers.menu")` |
| `test_gateway_assign_guard.py` | `gw.api`, `gw.router_ask`, `gw.user_ctx`, `gw.caps_of` | `load("core")` |
| `test_gateway_registration.py` | `gw.api`, `gw.send_menu`, `gw.router_ask`, `gw.caps_of` | `load("core")` / `load("routers.menu")` |

Хелпер `answer()` в `test_intro.py` получил параметр `monkeypatch`: заглушка
`api` ставится через `monkeypatch` и теперь возвращается обратно, а не остаётся
в `core` до конца сессии.

Утверждения не ослаблены: ни один тест и ни один `assert` не удалён и не
переписан; `raising=False` у `send_menu` убран — имя существует, ошибка должна
быть громкой.

## Проверки (ветка `wt/t_f4b2ed7b`)

- `pytest -p no:warnings -o addopts="" -q` — 1386 passed, 282 skipped, 0 failed, 0 errors;
- `ruff check .` — All checks passed;
- `scripts/check_invariants.py` — все проверки пройдены;
- `scripts/check_registers.py` — все проверки пройдены, предупреждений 0.

## Шов с main

Номер 99 в main занят отчётом карты D (`99-shared-helpers.md`, `b5ac42c`), 100 и
101 — отчётами карт E и R-9 (`100-narrow-except-and-types.md`,
`101-webapp-week-money-split.md`), 102 — отчётом соседней карты W-1 (`t_769f4756`).
Отчёт этой карты получил следующий свободный номер — `103-gateway-green-gate.md`:
две ветки не должны приносить один номер,
`tests/unit/test_docs_links.py::test_номера_уникальны` проверяет это после слияния.
Номер свободен на момент проверки; если main уйдёт вперёд и займёт 103, отчёт нужно
перенумеровать тем же способом (файл + строка индекса).

Трёхзначный номер потребовал правки самой проверки: в ветке `test_номера_уникальны`
брала `name[:2]`, и `103-…` сталкивался с `10-data-model-v2.md`. В main этот разбор
уже исправлен (`name.split("-", 1)[0]`), поэтому сюда перенесён тот же вид — файл
после слияния совпадает с main построчно.

Тот же дефект на `main` уже закрыт иначе: в `b5ac42c` (карта D) `Namespace`
научился отдавать `__dict__`-вид и молча переживать `delattr`, гейт `main`
зелёный. Файл `tests/unit/gateway_modules.py` в этой ветке не менялся: тесты
патчат настоящие модули и без этого вида, поэтому слияние бесконфликтно, а оба
решения совместимы.

## Проверка слиянием

Пробное слияние в `main` (`e9a0f4c`) во временном worktree: единственный конфликт —
таблица индекса `docs/README.md` (обе стороны добавили строку отчёта и обновили
счётчик); разрешён объединением строк и счётчиком 30. Номеров-дублей нет.
На слитом дереве: pytest `1403 passed, 284 skipped`, `ruff check .` чист,
`check_invariants` и `check_registers` зелёные.
