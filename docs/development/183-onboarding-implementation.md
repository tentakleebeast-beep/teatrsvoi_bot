# 183. Онбординг E13: реализация — тексты, знакомство, /help

22.11.2026. Карта `t_d1fe8a22`, эпик E13, шаг реализации после спецификации
`docs/development/182`. Ветка `wt/t_d1fe8a22` от `main` (после спеки 182 и
решений владельца 21–22.11.2026).

Документ фиксирует, что именно сделано по §1–§6 спеки 182 и чем это проверено.
Продуктовых решений здесь нет: спорные места решены в 182 и в `docs/reports/74-questions.md`.

## 1. Что сделано

| Область | Файл | Что изменилось |
|---|---|---|
| Тексты | `services/shared/onboarding.py` | `INVITE_INSTRUCTION` (§3.1, 1246 знаков) и `HELP_MEMO` (§3.2, 2443 знака) объявлены один раз |
| Реэкспорт для бота | `services/tg-gateway/app/texts.py` | импорт `HELP_MEMO` и `INVITE_INSTRUCTION`; свой `invite_text` удалён |
| Знакомство | `services/tg-gateway/app/intro.py` | `ENTRY_FIELDS` (имя, короткое имя, телефон), `FILL_FIELDS` (шесть отложенных, только пустые) |
| Сценарий входа | `services/tg-gateway/app/routers/intro.py` | вход по трём вопросам, отдельная кнопка «Знакомство» (`intro_fill`), «Пропустить» нет на шаге короткого имени |
| Чат | `services/tg-gateway/app/routers/menu.py` | `/help` отдаёт `HELP_MEMO`; текст кода по §1.3; чатовый путь кодов и заявок удалён |
| Клавиатуры | `services/tg-gateway/app/keyboards.py` | пунктов «Заявки», «Приглашения», «Коды приглашений» нет; добавлена кнопка «Знакомство» |
| API приглашений | `services/tool-api/app/api/admin.py` | код только на человека (`participant_id`), `POST /webapp/admin/person-new`, `GET /webapp/admin/invite-candidates` |
| API регистрации | `services/tool-api/app/api/people.py` | `check_code` отдаёт имя человека, `attach` без `participant_id`, `register` удалён |
| Экран | `services/tool-api/app/pages/admin_invites.py` | поиск человека, «Новый участник», выпуск на выбранного, лист кода с именем |
| Реестры | `services/tool-api/app/webapp.py`, `app/api/webapp_api.py` | раздел `pending` снят, счётчик «ждут решения» удалён |
| Схема | `db/init/070_personal_invite_code.sql`, `services/tool-api/migrations/versions/070_personal_invite_code.py` | `invite_code.participant_id`; снятие `registration.approve` у всех ролей |

## 2. Тексты и дословность

Оба текста сверяются посимвольно с §3 спеки: `tests/unit/test_gateway_invite_copy.py`
(инструкция и памятка объявлены ровно в одном файле, нет фигурных скобок, эмодзи,
двойных и хвостовых пробелов, длина ≤ 4096 и равна закреплённому числу) и
`tests/unit/test_gateway_help_memo.py` (шаги памятки, отсутствие старых обещаний).

Ответы на ошибки кода — `code_texts` в `routers/menu.py` по §1.3: `code_not_found`,
`code_expired`, `code_exhausted`, `code_not_personal`, `rate_limited`,
`participant_blocked`. Первые три строки цитируются в памятке, поэтому меняются
только вместе с ней — это проверяет `tests/unit/test_onboarding_sequence.py`.

## 3. Схема и миграция 070

`db/init/070_personal_invite_code.sql`:

1. `invite_code.participant_id bigint references participant(id) on delete set null`
   плюс индекс `invite_code_participant_idx`;
2. `delete from rbac_role_capability where capability = 'registration.approve'`
   (строка справочника `rbac_capability` остаётся — на неё смотрят применённые ревизии);
3. идемпотентно: `add column if not exists`, `create index if not exists`, `delete` по паре;
4. откат описан в шапке файла — колонка снимается, гранты возвращаются только вручную.

Данные не переносятся: на 21.11.2026 `participant.status = 'pending'` = 0 (три записи
15, 20 и 23 подтверждены через `/admin/approve`). Если запись всё же останется
`pending`, её подтверждение — выпуск персонального кода и вход им: `attach` ставит
`status='active'` (§4.2 спеки). Скопом переводить `pending` в `active` нельзя — это
выдало бы доступ без проверки.

Номер 070 выбран как максимум+1 по всем веткам (`069_poll_interview_drafts` — голова
`main`); свободность проверена по удалённым ветвям.

## 4. Экран «Приглашения»

`pages/admin_invites.py` переписан по §4.3.2:

- поиск «Фамилия или имя» → `GET /webapp/admin/invite-candidates` с паузой 250 мс;
- список людей без Telegram; выбор строки — `aria-pressed` и подсветка;
- «Выпустить код» заблокирована, пока человек не выбран; тело — `{participant_id}`;
- «Новый участник» открывает шторку: поле «Фамилия Имя», сегменты
  «Актёр · Организатор · Педагог», подсказка «Дисциплину педагог выберет при
  регистрации.» только у педагога; кнопка «Создать и выпустить код» делает
  `person-new`, затем `invite`;
- лист кода печатает имя человека и роль, а не «Роль: …»;
- пустой список: «Людей без Telegram нет — заведите кнопкой «Новый участник».»;
- отказ создания оставляет шторку открытой и печатает слова сервера; отказ выпуска
  после создания закрывает шторку с «Человек создан, код не выпущен — выпустите ещё раз».

Сегменты: кегль 14 px, поля 6 px и системное правило разделителя между невыбранными
сегментами — из решений 22.11.2026 (V-106 б, V-105 а, V-108).

## 5. Что удалено

- чатовый путь кодов: `send_invite`, `panel:invite`, `inv:role`, `panel:codes`,
  `inv:revoke`, `invite_text`, `copy_code_kb`, `chat.copy_code` (V-118 а);
- заявки: `panel:pending`, `send_pending`, `/admin/pending`, `/admin/approve`,
  `/webapp/admin/approve`, раздел и блок `pending`, счётчик «ждут решения»,
  страница `pages/admin_pending.py` (V-117 а, §4.1);
- регистрация «выбери себя»: `POST /tools/participants/register`,
  `POST /tools/participants/search`, состояния `Reg.await_name` / `Reg.choose`.

`invite_code.role` и `invite_code.discipline_code` не удалены: на них смотрят старые
строки и аудит, но новые коды пишут роль из записи и дисциплину не пишут.

## 6. Чем проверено

- `env -u PYTHONPATH .venv/bin/python -m pytest -p no:warnings -o addopts="" -q` — полный набор.
- `ruff check .` — чисто.
- `scripts/check_invariants.py`, `scripts/check_registers.py`, `scripts/test_audit.py --strict`.
- Браузерные экраны: `SVOI_WEBAPP_TESTS=1 ... tests/webapp/test_miniapp_invites_picker.py`,
  `test_miniapp_invites_rights.py`, `tests/webapp` целиком.
- Пересобраны `contracts/openapi.json` (`scripts/openapi_snapshot.py --write`) и
  `docs/reports/117-actions-inventory.md` (`scripts/audit_actions.py --write`).

## 7. Что не проверено и какой риск

- **Живой Telegram.** Ни один тест не отправляет сообщение в чат: юнит-тесты зовут
  обработчики с фейковым `Message`. Как памятка `HELP_MEMO` (2443 знака) выглядит на
  телефоне — не воспроизводил, нужен живой бот.
- **Прод-данные.** `pending = 0` взято из решения владельца 21.11.2026 и спеки 182 §4.2,
  на живой прод-базе в этом проходе не перепроверялось: прод не трогаю.
- **Смоук-стенд.** `tests/smoke` переведён на `person-new` + `invite` + `attach`, но
  без поднятого стенда (`SVOI_SMOKE_TESTS=1`) шаги остаются `skipped`.
- **`check_invariants.py`.** Красный I1 «баланс» на общей dev-базе — данные, а не
  ветка: на чистом `main` вывод тот же (проверялось побайтово).

## 8. Границы

Соседние сценарии не переписывались: профиль, бюджет, расписание, файлы, опросы
не тронуты. Правки — только в том, что названо в спеке 182: тексты, знакомство,
`/help`, персональный код, экран «Приглашения», судьба `pending`.
