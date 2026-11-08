# 105. Проверка обоснованности тестов: шаг в CI `lint` и в pre-commit

Карта: `t_bd23fee6`. Класс: process / tests. Источник: аудит тестов 104 §5.4,
правило — `docs/development/26-process-and-git.md` §7a п.3 (L-010, L-012).
Источник правила — `docs/reports/104-test-audit.md` §5.4: правило «тест без ID не
принимается» было объявлено, но не проверялось нигде.

## Что было

Правило «тест без ID требования или дефекта не принимается» было объявлено в `docs/development/26`
и `docs/reports/76`, но нигде не проверялось:

* `scripts/test_audit.py --strict` на базе карты (`origin/main` `2c8665e`) давал `exit 1`:
  1264 функции, **129 без обоснования** в 30 файлах (`tests/integration`, `tests/unit`,
  `tests/webapp`);
* в job `lint` (`.github/workflows/tests.yml`) были только `ruff` и `compileall`;
* `.githooks/pre-commit` звал `ruff` по staged-файлам и `scripts/check_invariants.py`,
  но не `test_audit.py`.

## Что стало

1. **129 тестов получили обоснование.** Правка только в обосновании — имя или docstring,
   логика тестов не менялась, продуктовый код не тронут. Честно про качество обоснования:
   своих ID у тестов как было **116**, так и осталось 116 — ни одного номера требования
   не придумано; все 129 закрыты **на уровне файла**: в docstring модуля добавлена ссылка
   на документ, который эти тесты описывают (`docs/architecture/…`, `docs/development/…`,
   `docs/infrastructure/…`, `docs/reports/…`). Скрипт такой вариант принимает по замыслу
   (колонка `файлом`), но это слабее исходного смысла правила: новый тест в таком файле
   получит обоснование от старой строки docstring. То есть после этой карты `docs/26 §7a`
   фактически означает «в каждом файле тестов есть ссылка на документ», а не «каждый тест
   говорит, что защищает». Гейт строго лучше, чем ничего (файл без единой ссылки не пройдёт),
   но 129 «осмысленных ID» — это не про нас: 116 своих ID были и до правки.
   Отдельно исправлена битая ссылка `doc/53` → `docs/development/53-disciplines-and-search-form.md`
   в `tests/unit/test_webapp_pages_js.py`.
2. **Шаг в CI.** В job `lint` рядом с `ruff` добавлен шаг
   `python scripts/test_audit.py --strict` — скрипт читает только исходники `tests/**`,
   база и зависимости сервисов не нужны.
3. **Шаг в pre-commit.** Хук запускает `scripts/test_audit.py --strict`, только когда в коммите
   есть staged `tests/**.py` (как `check_invariants.py` — по условию). Путь к скрипту
   переопределяется `SVOI_TEST_AUDIT_SCRIPT` для герметичного теста хука. На успехе строка
   вывода называет проверку: `тесты обоснованы`.
4. **Тесты самого механизма.** `tests/unit/test_precommit_hook.py`:
   * `test_clean_test_file_reports_audit` — тест с ID проходит, хук называет проверку;
   * `test_test_without_id_blocks_commit` — тест без ID останавливает коммит и печатает
     `тесты без ID (docs/development/26 §7a)`.
   `tests/unit/test_test_audit.py` не менялся: поведение скрипта не менялось.
5. **Доводка после ревью: два файла, приехавших в `main` позже базы карты.**
   На актуальном `main` (`71308d1`) гейт был красным не по вине правки карты: 24 теста
   в файлах, влитых другими картами после старта (`tests/unit/test_kanban_digest.py` — 17,
   карта `t_20f250c0`; `tests/unit/test_cli_dsn.py` — 7, карта `t_cfdc544f`). Обоим модулям
   добавлена та же строка-ссылка на существующий документ, логика не тронута:
   `docs/infrastructure/36-notifications.md §5b` (недельная сводка канбана) и
   `docs/reports/99-shared-helpers.md §2` (`scripts/_cli.py`, DSN).

## Было / стало

Три замера, все команда `scripts/test_audit.py --strict`:

| Показатель | База карты `2c8665e` | Актуальный `main` `71308d1` (до правки) | Ветка карты (стало) |
|---|---:|---:|---:|
| Всего тестовых функций | 1264 | 1307 | 1309 |
| С ID в самом тесте (`своих`) | 116 | 119 | 119 |
| Закрыто docstring файла (`файлом`) | 1019 | 1035 | 1190 |
| **Без обоснования** | **129** | **153** | **0** |
| `--strict` | `exit 1` | `exit 1` | `exit 0` |

Колонка «актуальный main» больше колонки базы на 43 теста: 41 тест приехал чужими картами
(`kanban_digest`, `cli_dsn`, сводка/метрики), 2 добавлены этой картой в `test_precommit_hook.py`.
Из 153 «без обоснования» на `main` эта карта закрыла все: 129 своих (на уровне файла)
и 24 в двух файлах, влитых уже после её старта.

## Вывод `--strict` (после правки)

```
$ env -u PYTHONPATH .venv/bin/python scripts/test_audit.py --strict
...                                                                        нет
unit/test_kanban_digest.py                                 17      0      17     0
unit/test_cli_dsn.py                                        7      0       7     0
...
----------------------------------------------------------------------------------
ИТОГО                                                    1309    119    1190     0
$ echo $?
0
```

## Чем проверено

Гейт гонялся на дереве после ребейза на `main` `71308d1`:

```
env -u PYTHONPATH .venv/bin/python -m pytest -p no:warnings -o addopts="" -q --ignore=tests/webapp
# 1478 passed, 51 skipped, exit 0
.venv/bin/ruff check .                                  # All checks passed!
env -u PYTHONPATH .venv/bin/python scripts/check_invariants.py   # Все проверки пройдены.
env -u PYTHONPATH .venv/bin/python scripts/check_registers.py    # Все проверки пройдены.
env -u PYTHONPATH .venv/bin/python scripts/test_audit.py --strict # exit 0, «нет 0»
env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_test_audit.py tests/unit/test_precommit_hook.py -q
```

Негативная проверка CI-шага: у `tests/unit/test_person_match.py` временно убрана строка
обоснования → `--strict` даёт `exit 1` и показывает `unit/test_person_match.py … 11`;
строка возвращена, снова `exit 0`. То же для хука закреплено тестом
`test_test_without_id_blocks_commit`.

## Где это ещё не проверено

`origin/main` в этой копии отстаёт от локального `main` на 13 коммитов, новых job'ов
(`webapp`, `webapp-webkit`, `deadcode`) на GitHub Actions ещё не было ни одного запуска.
Поэтому «гейт зелёный» здесь означает **зелёный локально**: шаг `test_audit --strict`
и job `lint` впервые отработают на первом же пуше в `origin`.

## Что осталось

* `docs/development/16-test-strategy.md` называет старое число (782 функции) — цифру стоит
  обновить отдельной правкой документации, здесь она не трогалась, чтобы не смешивать
  правку правила и правку отчёта.
* Смысл правила ослаблен файловым обоснованием (1150 из 1309 тестов до этой доводки —
  сейчас 1190). Вернуть исходный смысл — отдельная карта: `test_audit.py` уже считает
  `своих` и `файлом` раздельно, значит может падать, когда файловое обоснование закрывает
  слишком большую долю тестов.
* Браузерный набор `tests/webapp` в этой карте не гонялся: страницы Mini App и статика
  не менялись, изменения в `tests/webapp/**` — только docstring модулей.
