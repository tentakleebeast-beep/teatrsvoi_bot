# 102. Webapp: переделка страниц сведена с общей оболочкой D (W-1)

Дата: 01.11.2026. Карта: t_769f4756. Ветка: `wt/t_769f4756` (от main e9a0f4c).
Источник: аудит слияния 01.11.2026 (карты t_b1fcc902 и t_e19721d8).

## Что было

Переделка страниц Mini App делалась в ветках `wt/t_b1fcc902` (admin/misc по
модулям) и `wt/t_e19721d8` (week/money, порог 400 строк). Обе писались до карты D
(t_0489c36f), поэтому при слиянии с main конфликтовали пять файлов, а у потомка —
ещё семь. В main при этом оставались inline `function api(` в `pages/admin.py`,
`legacy.py`, `misc.py`, `week.py` (две копии) — цель D на этих файлах не была
достигнута.

## Как сведено

Правило разрешения конфликтов: разметка и поведение экранов — из ветки webapp;
`api()`/`auth()`/`money()` — одна реализация в общей оболочке, страницы только
вызывают.

1. `merge wt/t_b1fcc902` (`6caa8f9`): `admin.py`, `misc.py` взяты из ветки —
   это точки импорта разложенных разделов. `person.py` (бывший `legacy.py`) и
   `week.py` — из ветки: страницы зовут `window.SVOI.api`/`SVOI.auth`, своих
   обёрток нет. `webapp-shell.js` — версия ветки (dev_token, `enter()`,
   `auth()` со словами сервера) плюс `api()` из main: реализация D сохранена
   без изменений, второй аргумент — тело или набор опций fetch.
2. `merge wt/t_e19721d8` (`1fd319b`): week/money разложены на `week_*`/`money_*`;
   конфликты только в `docs/README.md` и `tests/unit/test_docs_links.py` —
   объединены записи 97–100. Отчёт ветки перенумерован в 101: номер 100 уже
   занят `100-narrow-except-and-types.md` из main.
3. Inline-копии `api()`/`auth()` в `pages/*.py` отсутствуют: `grep -r
   "function api\|function auth" services/tool-api/app/pages` — 0 совпадений.
   Имени `token` на страницах тоже нет: источник токена — `SVOI.token`.

## Проверки (на main, a999cb7)

| проверка | результат |
| --- | --- |
| `pytest -q` | 1403 passed, 284 skipped, 0 failed |
| `SVOI_WEBAPP_TESTS=1 pytest tests/webapp -q --browser chromium` | 147 passed, 23 skipped, 63 xfailed, 0 failed |
| `ruff check .` | All checks passed |
| `scripts/check_invariants.py` | все проверки пройдены |
| `scripts/check_registers.py` | все проверки пройдены, предупреждений 0 |

Порог строк: максимальный модуль `pages/week_marks.py` — 393 строки,
`pages/money_script.py` — 364, `pages/files.py` — 387. Ни один не больше 400.

Дизайн и тексты не менялись: экраны взяты из уже отревьюенных веток, браузерные
тесты `tests/webapp` (включая контракт оболочки) зелёные.
