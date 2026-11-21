# 179. Инструкция для получателя уходит в чат при выпуске кода из Mini App (E12-1)

21.11.2026. Ветка `wt/t_722379f2` от `827a111` (`main`). Карты: `t_722379f2` — эта
спецификация, `t_a71c8898` — реализация. Эпик E12, шаг 1 из 2. Закрывает вопрос
**V-104** (`docs/reports/74-questions.md`): ответ владельца 20.11.2026 — «код остаётся
на экране, инструкция уходит в чат».

Код продукта в этой карте не правился: решения, точки изменения, замер и границы.
Реализация — по §4, проверка — по §5 и §6.

## 1. Снимок: что проверено и чем

| Что | Чем |
|---|---|
| Бот: что уходит при выпуске кода | `services/tg-gateway/app/routers/admin.py:64-75` (`send_invite`), `services/tg-gateway/app/texts.py:222-240` (`invite_text`) |
| Mini App: что делает экран | `services/tool-api/app/pages/admin_invites.py:25-48` (`issue`, `showCode`) |
| Что отдаёт выпуск в Mini App | `services/tool-api/app/api/admin.py:1109-1115` (`webapp_admin_invite` → `create_invite`, `:39-60`) |
| Как бот зовёт выпуск | `services/tg-gateway/app/routers/admin.py:66` — `POST /admin/invite` того же tool-api |
| Где `kind` превращается в текст | `scripts/send_notifications.py:119-124` (`message_text`) и `:193-201` |
| Обрезка при отправке | `scripts/send_notifications.py:79` — `text[:4000]` |
| Схема очереди | `db/init/001_schema.sql:326-341` — `unique (dedupe_key, recipient_id)` |
| Тихие часы в очереди | `scripts/send_notifications.py:147-180`, `services/tool-api/app/notifications.py` (`resolve_settings`, `in_quiet_hours`, `next_allowed`) |
| Как часто разбирается очередь | `deploy/systemd/svoi-notify-send.timer` — `*:00/5:00`, каждые 5 минут |
| Адресат из Mini App | `services/tool-api/app/core.py:459-483` (`webapp_actor`) — возвращает `participant.id` |
| Сторож «один источник текста» | `tests/unit/test_gateway_invite_copy.py:275-288` |
| Замер текста | `sed -n '199,216p' services/tg-gateway/app/texts.py` + `LC_ALL=en_US.UTF-8 wc -m` (§3.3) |
| Стенд Mini App | `tests/webapp/server.py:1134-1184` — своего `notification` у стенда нет |
| Номера документа и вопроса | `git ls-tree` по всем ветвям и `refs/remotes`: занято до 178 (документ) и до V-112 (вопросы) |

Не проверено в этом проходе: живой Telegram (отправку не дёргал), поведение на телефоне
(экран не меняется — нечего смотреть), живая прод-база.

## 2. Факты, на которых стоит решение

### 2.1. У выпуска два входа, а поведение у них разное

Бот (`send_invite`) отправляет в чат **одно** сообщение: шапка с кодом + `invite_text`,
в конце — `INVITE_LINK` и `INVITE_INSTRUCTION`. Инструкцию пересылают получателю целиком.

Mini App (`pages/admin_invites.py:26`) зовёт `POST /webapp/admin/invite`, получает код и
показывает его в шторке (`showCode`); в чат не уходит ничего. Причина, по которой это так,
записана в `docs/development/174` §2.6 и была вопросом V-104.

### 2.2. Бот и экран приходят в один и тот же обработчик — уведомление нельзя класть в `create_invite`

`services/tg-gateway/app/routers/admin.py:66` зовёт `POST /admin/invite`, а этот адрес и
`POST /webapp/admin/invite` сходятся в одной функции `create_invite`
(`services/tool-api/app/api/admin.py:39-60`). Если поставить уведомление внутри
`create_invite`, админ получит инструкцию дважды при выпуске из бота: один раз — в
сообщении бота, второй — уведомлением из очереди.

Поэтому постановка в очередь живёт **только** в `webapp_admin_invite` (`:1109-1115`),
после успешного `create_invite`.

### 2.3. `send_notifications.py` не знает про `kind`: текст берётся из `payload`

`message_text` (`:119-124`) собирает сообщение из `payload.title` и `payload.body`; `kind`
в отправке не участвует вообще. Значит новый вид уведомления **не требует правки
отправителя**: достаточно положить в `payload` готовые строки. `kind` остаётся меткой
очереди (как `feedback_status` в `api/admin.py:372`, `poll` в `api/polls.py:536`,
`file_access` в `file_access.py:82`).

Отдельно: `deliver` (`:127-219`) не применяет `kinds_muted` — выключение типов действует
только через `should_skip` у планировщика (`scripts/plan_reminders.py:84`, `:111`).
Значит выключенные типы на это сообщение не влияют, и это не наша правка: менять правило
доставки — отдельная карта.

### 2.4. Обрезка при отправке — 4 000 знаков, а не 3 800

В карте написано «`[…] [:3800]` — такой обрезки в боте хватает». В коде обрезка одна:
`scripts/send_notifications.py:79`, `text[:4000]`. Числа 3800 в репозитории нет
(`grep -rn 3800 --include=*.py` — совпадений нет). Ниже считаю по 4 000.

### 2.5. Текст инструкции объявлен в одном месте, и это защищено тестом

`tests/unit/test_gateway_invite_copy.py:286-288`:

```python
declared = [path.name for path in (ROOT / "services").rglob("*.py")
            if "INVITE_INSTRUCTION =" in path.read_text(encoding="utf-8")]
assert declared == ["texts.py"], f"константа объявлена не только в texts.py: {declared}"
```

Копия текста в `tool-api` этот тест уронит — и правильно: две копии разойдутся при первой
же правке. Значит текст должен приехать в оба сервиса **из общего модуля**, а не копией.
Место для такого модуля в проекте уже есть: `services/shared/` копируется в оба образа
(`services/tg-gateway/Dockerfile:19`, `services/tool-api/Dockerfile:22`), и в нём уже
лежат общие тексты (`services/shared/dues.py` — «Мои взносы» для чата и экрана, FR-B7).

### 2.6. «Перевыпуска того же кода» не существует: каждое нажатие выпускает новый код

`create_invite` (`api/admin.py:48`) берёт `secrets.choice(core.CODE_ALPHABET)` восемь раз
на каждый вызов, `invite_code.code` — обычная строка без переиспользования, отзыв только
гасит `expires_at`. Поэтому:

- повторное нажатие «Выпустить код» даёт **второй код** в `invite_code` и второй код на
  экране;
- `dedupe_key`, привязанный к коду, от повторного нажатия **не защищает**: ключи разные,
  сообщений будет два.

Это расхождение карты с кодом, а не моя догадка: `dedupe_key` по коду защищает только от
повторной вставки одного и того же события (ретрай запроса), но не от двойного тапа и не
от «выпустил второй код через минуту».

### 2.7. Тихие часы и таймер — уже есть, обходить не нужно

`deliver` переносит запись на `next_allowed(...)` при попадании в тихие часы
(`:173-180`); действующие часы считает `resolve_settings` — частная настройка важнее общей,
нет и её — значения по умолчанию 20:00–08:00 (`services/tool-api/app/notifications.py`).
Таймер `svoi-notify-send.timer` разбирает очередь каждые 5 минут. То есть критерий «ночью
сообщение приходит после 08:00» выполняется самим путём очереди, если не писать
`scheduled_at` в обход правил.

### 2.8. Адресат — `participant.id` того, кто выпустил

`webapp_actor` (`core.py:459-483`) находит строку `participant` по `tg_chat_id` из токена и
возвращает `{"id": row["id"], ...}` — это и есть `participant.id`; `_webapp_require`
возвращает того же человека с возможностями. `notification.recipient_id` ссылается на
`participant(id)` (`db/init/001_schema.sql:328`), отправитель требует `status='active'` и
непустой `tg_chat_id` (`send_notifications.py:143-144`) — у выпускающего из Mini App оба
условия верны по построению (доступ в приложение даётся активному участнику).

### 2.9. Стенд Mini App не может проверить уход в чат

Стенд (`tests/webapp/server.py`) не имеет таблицы `notification` и своего отправителя:
максимум, что он видит, — тело запроса. Браузерный тест на «сообщение появилось в чате»
поставить нечем, и придумывать ему форму ответа, которой нет у сервера, нельзя. Проверка
ухода в чат — интеграционная, на настоящей БД (§5).

## 3. Решения

### Д1. Где живёт текст: `services/shared/invite.py`, оба сервиса импортируют его

Новый модуль `services/shared/invite.py` с двумя константами, перенесёнными **дословно**:

- `INVITE_INSTRUCTION` — текст из `services/tg-gateway/app/texts.py:199-216` (утверждён,
  эталон — `docs/development/174` §11.3);
- `INVITE_LINK` — строка-связка «Инструкция для получателя — её можно переслать целиком:»
  (`texts.py:219`).

`services/tg-gateway/app/texts.py` вместо литералов делает реэкспорт:

```python
from services.shared.invite import INVITE_INSTRUCTION, INVITE_LINK  # noqa: F401
```

(импорт ставится рядом с `services.shared.dues` / `services.shared.format` — после
`import _bootstrap`). Поведение бота, тексты и длины сообщений не меняются: `invite_text`
читает те же имена.

Почему не копия в `tool-api`: §2.5. Почему не «`tool-api` импортирует `tg-gateway`»:
`services/tg-gateway` — не пакет (дефис в имени каталога), сервисы не должны тянуть друг
друга, а `services/shared` для этого и заведён.

### Д2. `kind` = `invite_instruction`

Имя по образцу существующих (`feedback_status`, `file_access`, `poll`): строчное,
подчёркивания, без роли и кода в имени. Текст по `kind` не собирается — весь текст в
`payload` (§2.3), поэтому правка `scripts/send_notifications.py` не нужна.

### Д3. `payload` = `title` + `body`, кода внутри нет

```json
{"title": "Инструкция для получателя — её можно переслать целиком:",
 "body": "<INVITE_INSTRUCTION дословно>"}
```

Замер (21.11.2026, `texts.py:199-216` и `:219`):

| Что | Значение |
|---|---|
| `INVITE_INSTRUCTION` | 1 074 знака, 18 строк, 1 891 байт UTF-8 |
| `INVITE_LINK` | 55 знаков |
| Сообщение целиком (`title` + `\n\n` + `body`) | 1 131 знак |
| Лимит отправки `text[:4000]` | 4 000 знаков — запас 2 869 |
| `payload` в JSON (`ensure_ascii=False`) | ≈ 2 015 байт (1 891 + 99 на `title` + служебные ключи), лимита у `jsonb` нет |

Кода в тексте нет по построению: `INVITE_INSTRUCTION` его не содержит, и это уже
проверено тестом `test_gateway_invite_copy.py:178` (`assert CODE not in
gw.INVITE_INSTRUCTION` в `test_код_один_раз_и_в_кнопке`). Роль и дисциплина тоже не подставляются: в
утверждённом тексте роль-зависимых фраз нет (`174` Д2).

### Д4. Дедупликация: ключ по коду **плюс** правило «не чаще одной инструкции в сутки»

Ключ: `invite_instruction:<recipient_id>:<code>` — уникален по построению, читается
глазами в `select * from notification`, совпадает с предложением карты.

Правило-спутник (обязательно, иначе критерий «повторное нажатие не даёт второго
сообщения» недостижим, §2.6): запись **не ставится**, если у этого адресата уже есть
запись того же вида, которая ещё `scheduled` или отправлена в последние 24 часа.

| Случай | Что происходит |
|---|---|
| Первое нажатие «Выпустить код» | код в `invite_code` + одна строка `notification` (`scheduled`, `scheduled_at = now()`) |
| Двойной тап (второй запрос через секунды) | второй код в `invite_code` выпускается, **строки в очереди нет** — приходит одно сообщение |
| Второй код через минуту / через час | то же: инструкция одна на сутки |
| Выпуск на следующий день (после отправки вчерашней) | новое сообщение: вчерашняя старше 24 часов |
| Выпуск в 23:50 (тихие часы), повтор в 00:10 | первая строка ждёт 08:00; вторая **не ставится** (первая ещё `scheduled`) — утром одно сообщение, а не два |
| Прошлая инструкция упала (`failed` после 3 попыток) | новое сообщение ставится: `failed` не считается ни `scheduled`, ни `sent` |
| Другой админ выпускает свой код | своя строка: правило считается по `recipient_id` |

Окно 24 часа, а не «календарный день», выбрано из-за случая «23:50 → 00:10»: у
календарного дня там две разные даты и два сообщения на 08:00. Цена правила: админ,
выпустивший два кода за сутки, вторую инструкцию не получает — текст у него уже есть в
чате, он его пересылает. Это решение спецификации, а не требование владельца; меняется
одной строкой SQL, если владелец захочет иначе.

### Д5. Кому: `recipient_id = who["id"]`, не «всем админам»

Тот, кто выпустил (§2.8). Никаких выборок по роли `admin`: выпускать код может и
организатор, а получатель инструкции — всегда автор действия.

### Д6. Экран Mini App не меняется, строку «Инструкция отправлена в чат» не добавляем

Причина в тихих часах: ночью сообщение уходит не сразу, а после 08:00 (`§2.7`), и шторка
со словами «отправлена» в этот момент врала бы — ровно тот класс ошибок, который
`docs/development/69` велит не повторять. Код в шторке по-прежнему единственное, что
показывает выпуск; факт отправки админ видит в чате. Разметка `_BODY_INVITES`, скрипт
`_SCRIPT_INVITES` и стенд `tests/webapp/server.py` не меняются, браузерных тестов по
экрану в карте нет.

### Д7. Сбой постановки в очередь не отменяет выпуск кода

Код уже записан в `invite_code`; если ответ станет 500, админ нажмёт ещё раз и выпустит
второй код. Поэтому вставка в `notification` идёт в отдельном соединении, а её ошибки
(только `DB_ERRORS` — `services/tool-api/app/external.py:29`) пишутся в лог
`core.log.exception(...)` и наружу не выходят. Ответ `POST /webapp/admin/invite` остаётся
прежним: `{"ok": true, "code": ..., "role": ..., "expires_at": ...}` — экран читает
`code`, новых полей не появляется.

### Д8. Бот не меняется

`send_invite` и `invite_text` дают прежний текст; длины из
`test_gateway_invite_copy.py::LENGTHS` (1379 / 1378 / 1455) остаются те же. Меняется только
место объявления константы (Д1) — это плата за то, чтобы текста не было двух копий.

## 4. Точки изменения

| Файл | Что сделать |
|---|---|
| `services/shared/invite.py` | **новый**: `INVITE_INSTRUCTION` (дословно из `texts.py:199-216`) и `INVITE_LINK` (`:219`) с докстрокой «один текст на чат и на уведомление, E12, docs/179» |
| `services/tg-gateway/app/texts.py` | убрать литералы `:199-216` и `:219`, поставить `from services.shared.invite import INVITE_INSTRUCTION, INVITE_LINK  # noqa: F401` рядом с другими импортами `services.shared`; `invite_text` не трогать |
| `services/tool-api/app/api/admin.py` | в `webapp_admin_invite` (`:1109-1115`): сохранить результат `_webapp_require` в переменную, после `create_invite` вызвать постановку в очередь; добавить приватную функцию `_queue_invite_instruction(recipient_id, code)`; в импорты — `from services.shared.invite import INVITE_INSTRUCTION, INVITE_LINK` |
| `tests/unit/test_gateway_invite_copy.py` | сторож `test_оба_входа_зовут_invite_text` (`:286-288`): ожидание `declared == ["invite.py"]` + проверка, что `texts.py` реэкспортирует оба имени (`"from services.shared.invite import"` в исходнике) |
| `tests/integration/test_admin_invites.py` | три теста из §5.1–5.3 |
| `tests/integration/test_invite_instruction_chat.py` | **новый**: ночной перенос (§5.4), по образцу `tests/integration/test_notification_defaults.py:151-178` |
| `docs/development/174-invite-instruction.md` | в §2.6 абзац: «выпуск из Mini App инструкцию в чат отправляет — `docs/development/179`»; вопрос V-104 закрыт |
| `docs/reports/74-questions.md` | V-104: ответ владельца 20.11.2026 и статус `решено` |
| `docs/README.md` | пересобрать: `env -u PYTHONPATH .venv/bin/python scripts/build_docs_index.py`. **Пересобрано на ветке карты**: в строке «номера не использованы» индекс пишет 178, потому что документ 178 живёт на другой ветке; после слияния индекс пересобирается заново, и 178 из этой строки уходит |

Код постановки в очередь (образец, имя и тексты не менять; формат — как у соседних вставок
`api/admin.py:372`, `api/polls.py:536`):

```python
def _queue_invite_instruction(recipient_id: int, code: str) -> bool:
    """Инструкция получателю — тому, кто выпустил (E12, docs/development/179).

    Кода в тексте нет: он на экране. Ключ привязан к коду, но от двойного нажатия
    защищает правило «не чаще одной инструкции в сутки»: каждое нажатие выпускает
    новый код, поэтому по ключу дубль не поймать.
    """
    payload = {"title": INVITE_LINK, "body": INVITE_INSTRUCTION}
    try:
        with core.pool.connection() as conn:
            row = conn.execute(
                "insert into notification (recipient_id, kind, payload, dedupe_key, scheduled_at) "
                "select %s, 'invite_instruction', %s, %s, now() "
                "where not exists (select 1 from notification "
                "                  where recipient_id = %s and kind = 'invite_instruction' "
                "                    and (status = 'scheduled' "
                "                         or (status = 'sent' "
                "                             and sent_at > now() - interval '24 hours'))) "
                "on conflict (dedupe_key, recipient_id) do nothing "
                "returning id",
                (recipient_id, json.dumps(payload, ensure_ascii=False),
                 f"invite_instruction:{recipient_id}:{code}", recipient_id),
            ).fetchone()
        return row is not None
    except DB_ERRORS:
        core.log.exception("инструкция приглашения не поставлена в очередь: %s", code)
        return False
```

`json` и `DB_ERRORS` в `api/admin.py` уже импортированы (`:6`, `:19`); `core.log` есть
(`core.py:327`).

## 5. Тесты

### 5.1. `tests/integration/test_admin_invites.py::test_выпуск_из_mini_app_ставит_инструкцию_в_очередь`

После `POST /webapp/admin/invite` (токен Mini App, участник `admin`, право
`registration.code.issue`) в `notification` **ровно одна** строка:

- `kind = 'invite_instruction'`;
- `recipient_id` = `participant.id` выпустившего (не бот-адрес `123456789`, не «все админы»);
- `payload->>'title'` = `INVITE_LINK`, `payload->>'body'` = `INVITE_INSTRUCTION` из
  `services.shared.invite` (сравнение дословное);
- кода из ответа нет ни в `title`, ни в `body`;
- `status = 'scheduled'`, `scheduled_at` не пусто.

### 5.2. `tests/integration/test_admin_invites.py::test_выпуск_из_бота_инструкцию_в_чат_не_ставит`

`POST /admin/invite` бот-заголовком (`auth` из `tests/conftest.py`) → в `notification`
**нет** строк `kind = 'invite_instruction'`. Смысл: бот уже прислал инструкцию одним
сообщением, второе — дефект (§2.2).

### 5.3. `tests/integration/test_admin_invites.py::test_повторное_нажатие_не_плодит_инструкцию`

Два подряд `POST /webapp/admin/invite` тем же токеном:

- в `notification` всё ещё **одна** строка `invite_instruction`;
- в `/webapp/admin/invites` при этом **два разных кода** — тест фиксирует факт §2.6:
  коды разные, сообщение одно.

### 5.4. `tests/integration/test_invite_instruction_chat.py::test_инструкция_ночью_ждёт_восьми_часов`

Строка ставится через API (или напрямую в `notification` тем же `kind`), затем
`sender.deliver(conn, moment, apply_changes=True, sender=заглушка, out=lambda *_: None)`
на момент 21:00 по Самаре, как в `tests/integration/test_notification_defaults.py:151`:

- `result["sent"] == 0`, `result["postponed"] == 1`;
- `scheduled_at` стал `08:00` следующего дня;
- заглушка отправителя не вызвана.

### 5.5. Сторож источника текста

`tests/unit/test_gateway_invite_copy.py::test_оба_входа_зовут_invite_text` — обновлённое
ожидание (§4): `declared == ["invite.py"]`, `texts.py` реэкспортирует оба имени. Текст
по-прежнему сверяется с эталоном §11.3 (`REFERENCE` в том же файле), длины `LENGTHS`
(1379 / 1378 / 1455) не изменились.

### 5.6. Чем прогонять

```
env -u PYTHONPATH .venv/bin/python -m pytest -q tests/integration/test_admin_invites.py \
    tests/integration/test_invite_instruction_chat.py tests/unit/test_gateway_invite_copy.py
env -u PYTHONPATH .venv/bin/python scripts/build_docs_index.py
env -u PYTHONPATH .venv/bin/python scripts/check_done.py          # полный гейт карты
```

## 6. Критерии приёмки

1. `POST /webapp/admin/invite` из Mini App → в очереди ровно одна строка
   `kind='invite_instruction'`, `recipient_id` = выпустивший, `payload.body` =
   `INVITE_INSTRUCTION` дословно, кода в тексте нет (тест 5.1).
2. Бот-путь `POST /admin/invite` не ставит строк в очередь; текст и длина сообщения бота
   прежние (`LENGTHS` 1379/1378/1455, тест 5.2).
3. Повторное нажатие «Выпустить код» не добавляет второй строки (тест 5.3).
4. Ночью запись переносится на 08:00 и уходит после него (тест 5.4).
5. `INVITE_INSTRUCTION` объявлена ровно в одном месте — `services/shared/invite.py`;
   `texts.py` реэкспортирует, тест-сторож обновлён и зелёный (тест 5.5).
6. Экран «Приглашения» не изменился: `pages/admin_invites.py` и `tests/webapp/server.py`
   в диффе отсутствуют; браузерные тесты по приглашениям зелёные без правок.
7. `scripts/check_done.py` зелёный (ruff, pytest, инварианты, реестры), `docs/README.md`
   пересобран.

## 7. Границы

- Текст `INVITE_INSTRUCTION` не переписывается — переносится дословно; эталон
  `docs/development/174` §11.3, тест сверяет его литералом.
- Формат кода, TTL 7 дней, `max_uses`, отзыв кодов, право `registration.code.issue` — не
  трогаются.
- `scripts/send_notifications.py` и правило тихих часов не правятся: путь очереди уже
  умеет всё, что нужно (§2.3, §2.7).
- Экран Mini App и стенд `tests/webapp/server.py` не правятся (§Д6).
- В боте меняется только место объявления константы (Д1); `send_invite` не меняется.
- Порядок и число сообщений при выпуске из бота не меняются: одно сообщение остаётся.

## 8. Что осталось за картой и что не проверено

- Двойной тап выпускает **два кода** — это поведение `create_invite`, а не этой карты:
  отдельная карта, если владелец захочет защиту кнопки от двойного нажатия.
- Повторная присылка инструкции по кнопке «показать ещё раз» (если админ удалил
  сообщение) — не делаем: экрана с текстом инструкции в Mini App нет.
- `kinds_muted` на это уведомление не действует (§2.3) — общее свойство очереди, менять
  его здесь нельзя.
- Не проверено: живой Telegram (отправка подменяется заглушкой), живая прод-база,
  поведение на телефоне (экран не менялся).

## 9. Числа этой спецификации и команды

| Число | Откуда |
|---|---|
| 1 074 знака / 1 891 байт / 18 строк в `INVITE_INSTRUCTION` | `sed -n '199,216p' services/tg-gateway/app/texts.py \| sed '1s/^INVITE_INSTRUCTION = """//' \| sed '$s/"""$//' > /tmp/ii.txt`; `printf '%s' "$(cat /tmp/ii.txt)" \| LC_ALL=en_US.UTF-8 wc -m` (1074), `wc -c` (1891), `wc -l` (18). Хвостовой перевод строки снимается `printf`: `wc -m` по файлу с ним даёт 1075 — на единицу больше |
| 55 знаков в `INVITE_LINK` | `sed -n '219p' … \| sed 's/^INVITE_LINK = "//; s/"$//' \| printf '%s' "$(cat)" \| wc -m` |
| 1 131 знак в сообщении | 55 + 2 (перевод строки) + 1074 |
| 4 000 знаков лимита | `scripts/send_notifications.py:79` |
| 1379 / 1378 / 1455 — длины сообщения бота | `tests/unit/test_gateway_invite_copy.py::LENGTHS` |
| Документ 179 | `git ls-tree` по всем ветвям и `refs/remotes`: документы заняты до 178 (179 свободен) |
