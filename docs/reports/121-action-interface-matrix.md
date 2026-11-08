# 121. Матрица «действие участника × интерфейс»

**Дата:** 04.11.2026
**Аудит 1/4.** Инвентарь по документации: какие действия человек вообще совершает в
системе и где каждое из них сейчас разрешено — в чате, в Mini App, в обоих или нигде.
Код в этой карте не читался; требования не переписывались, зафиксированы как есть.

**Источники:** `docs/development/19` (требования), `20` (сценарии), `12` (поведение бота),
`21` (каскад разбора), `48` (лист занятости), `82` (навигация и хаб), `90` (разбор Mini App),
`91` (дизайн-система), `92` (модель навигации), `93` (CJM). Для rules «что можно в Mini App,
можно и в чате» дополнительно `23`, `53`, `57` — там это правило записано прямо.

**Машиночитаемая часть:** `docs/reports/121-action-interface-matrix.json`.
Таблицы ниже пересобираются из JSON командой
`env -u PYTHONPATH .venv/bin/python scripts/check_action_matrix.py --write`;
проверка без записи — та же команда без флага, код выхода 1 при расхождении.

**Как читать колонки.**

- **Действие** — то, что человек делает, глаголом; не название экрана и не имя эндпоинта.
- **Кто** — роль в словаре `92` §1 (участник, организатор, админ) плюс владелец и педагог там,
  где документ называет их отдельно.
- **Интерфейс сейчас** — только четыре значения: `чат`, `Mini App`, `оба`, `нигде`.
  «Нигде» не значит «невозможно»: действие может жить в Google Sheets, в скрипте или в API
  без входа — это сказано в примечании к строке.
- **Источник** — файл и строка; для требований это строка таблицы `docs/19`, для сценариев —
  заголовок `docs/20`.
- **Статус требования** — приоритет и статус из `docs/19` дословно (`P0…P2`, `готово`,
  `частично`, `не начато`), в скобках — расхождение с `docs/20`, если оно есть.

## Сводка

<!-- summary:begin -->
| Показатель | Значение |
|---|---:|
| Строк-действий | 56 |
| Требований в docs/19 | 113 |
| Из них с действием участника | 64 |
| Сценариев в docs/20 | 30 |

| Интерфейс сейчас | Действий |
|---|---:|
| чат | 16 |
| Mini App | 10 |
| оба | 14 |
| нигде | 16 |

| Кто | Действий |
|---|---:|
| участник | 17 |
| организатор | 20 |
| админ | 19 |
| владелец | 3 |
| педагог | 1 |
<!-- summary:end -->

Из 113 требований `docs/19` действие участника описано у меньшей части: остальные —
серверные правила, расчёты, миграции и отчёты. Они не выброшены: каждое перечислено в
разделе «Требования без отдельного действия» с причиной, иначе проверка по числу
заголовков была бы неполной.

## Матрица

<!-- matrix:begin -->
| Действие | Кто | Интерфейс сейчас | Источник | Статус требования |
|---|---|---|---|---|
| Войти впервые и получить права владельца | владелец | чат | docs/development/19-functional-requirements.md#L30<br>docs/development/20-scenarios.md#L17 | P0, готово<br><small>Доступ по совпадению chat_id с ADMIN_CHAT_ID, без кода</small> |
| Зарегистрироваться по одноразовому коду и выбрать себя из списка | участник, организатор | чат | docs/development/19-functional-requirements.md#L31<br>docs/development/19-functional-requirements.md#L32<br>docs/development/20-scenarios.md#L31<br>docs/development/20-scenarios.md#L43 | P0, готово<br><small>Повторная привязка того же chat_id невозможна</small> |
| Подать заявку «меня нет в списке» | участник | чат | docs/development/19-functional-requirements.md#L33<br>docs/development/20-scenarios.md#L51 | P0, готово<br><small>Создаёт запись pending и уведомляет администратора</small> |
| Подтвердить или отклонить заявку | админ | оба | docs/development/19-functional-requirements.md#L33<br>docs/development/20-scenarios.md#L51<br>docs/development/92-navigation-model.md#L137 | P0, готово<br><small>В чате — кнопки в уведомлении; в Mini App — раздел «Заявки»</small> |
| Выдать или отозвать роль и возможность | админ | оба | docs/development/19-functional-requirements.md#L35<br>docs/development/20-scenarios.md#L255<br>docs/development/92-navigation-model.md#L140 | P1, готово<br><small>Один путь записи `/admin/grant` (`enabled=false` — отзыв роли, `allowed=false` — отзыв возможности); отзыв роли — пометка строки, а не удаление; входы: карточка человека в чате, экраны «Люди» и «Права» в Mini App</small> |
| Архивировать ушедшего из театра | админ | оба | docs/development/19-functional-requirements.md#L36<br>docs/development/20-scenarios.md#L62<br>docs/development/92-navigation-model.md#L141 | P0, готово<br><small>Один путь записи `/admin/archive` и `…/unarchive` у чата и экрана; входы: карточка человека и раздел «Архив» в чате, раздел «Архив» в Mini App</small> |
| Заблокировать участника | админ | оба | docs/development/19-functional-requirements.md#L38 | P1, готово<br><small>`POST /admin/block` (`blocked=false` — вернуть доступ), одна функция у чата и экрана; статус `blocked` закрывает доступ и в чате, и в Mini App сразу; кнопки в карточке человека и на экране «Люди»</small> |
| Посмотреть аудит действий | админ | оба | docs/development/19-functional-requirements.md#L37<br>docs/development/20-scenarios.md#L264<br>docs/development/92-navigation-model.md#L143 | P0, готово<br><small>Чат: /admin → «Аудит»; Mini App: «Управление → Аудит» (FR-A11)</small> |
| Заполнить профиль (анкета) | участник | Mini App | docs/development/19-functional-requirements.md#L221<br>docs/development/92-navigation-model.md#L168<br>docs/development/82-ux-navigation.md#L85 | P1, готово<br><small>Имя, короткие имена, время в пути, манифест для AI</small> |
| Изменить тариф взноса человеку | организатор | нигде | docs/development/19-functional-requirements.md#L50<br>docs/development/19-functional-requirements.md#L72 | P1, частично<br><small>API есть (participant.dues_tariff), экрана админ-панели нет</small> |
| Посмотреть свои взносы | участник | оба | docs/development/19-functional-requirements.md#L50<br>docs/development/20-scenarios.md#L112<br>docs/development/92-navigation-model.md#L122 | P0, выполнено<br><small>Чат: «Мои взносы» /dues; Mini App: «Деньги» и /webapp/dues</small> |
| Внести оплату взноса | участник | нигде | docs/development/19-functional-requirements.md#L50<br>docs/development/93-cjm.md#L51 | P0, выполнено<br><small>Решения П-9, П-11: заявки и оплаты из чата нет — оплату вносит организатор доходом</small> |
| Записать расход или доход свободным текстом | организатор | чат | docs/development/19-functional-requirements.md#L44<br>docs/development/19-functional-requirements.md#L45<br>docs/development/20-scenarios.md#L73<br>docs/development/20-scenarios.md#L105 | P0, готово<br><small>Разбор правилами и каскадом, черновик с кнопками, запись в БД и лист</small> |
| Подтвердить, изменить или отменить черновик операции | организатор | чат | docs/development/19-functional-requirements.md#L45<br>docs/development/20-scenarios.md#L77<br>docs/development/82-ux-navigation.md#L178 | P0, готово<br><small>«Изменить» по 82 §4.2 ведёт в форму Mini App с предзаполнением</small> |
| Записать операцию формой | организатор | оба | docs/development/12-bot-behavior.md#L94<br>docs/development/92-navigation-model.md#L117<br>docs/development/93-cjm.md#L72 | P0, частично<br><small>Форма в чате — да; в Mini App вход «Записать операцию» описан в 92, но 93 фиксирует «плитки нет»</small> |
| Пополнить справочник категорий и синонимов | админ | чат | docs/development/19-functional-requirements.md#L47<br>docs/development/12-bot-behavior.md#L138 | P0, готово<br><small>12 §5: справочник и личные алиасы настраиваются из Telegram</small> |
| Посмотреть агрегаты по деньгам | организатор, админ | оба | docs/development/19-functional-requirements.md#L57<br>docs/development/20-scenarios.md#L139<br>docs/development/92-navigation-model.md#L118 | P1, готово<br><small>Чат: ответ на «сколько у нас денег»; Mini App: «Деньги → Бюджет» (админ)</small> |
| Сверить листы (файл ↔ база) | админ | оба | docs/development/19-functional-requirements.md#L56<br>docs/development/20-scenarios.md#L248<br>docs/development/92-navigation-model.md#L145 | P0, готово<br><small>Чат: кнопка «Сверить листы»; Mini App: «Управление → Сверка листов»</small> |
| Поправить суммы в листе «Транзакции» | владелец | нигде | docs/development/19-functional-requirements.md#L55<br>docs/development/20-scenarios.md#L248 | P0, частично<br><small>Правка вне бота — в Google Sheets, затем сверка; занятость в базу не применяется</small> |
| Спланировать доходы и расходы (план/факт) | админ | нигде | docs/development/19-functional-requirements.md#L58 | P2, частично<br><small>Только схема, логики и экрана нет</small> |
| Отметить занятость свободным текстом | участник | чат | docs/development/19-functional-requirements.md#L65<br>docs/development/20-scenarios.md#L173 | P0, готово<br><small>«Занят в среду после 18» → подтверждение → запись</small> |
| Отметить занятость формой в чате | участник | чат | docs/development/19-functional-requirements.md#L66<br>docs/development/20-scenarios.md#L181 | P0, готово<br><small>День → время → подтверждение</small> |
| Отметить занятость по часам в Mini App | участник | Mini App | docs/development/19-functional-requirements.md#L74<br>docs/development/48-mini-app-grid.md#L42<br>docs/development/92-navigation-model.md#L92 | P1, частично<br><small>Лист дня и неделя есть; FR-H8 переписан на неделю, статус «не начато» до Ш-03</small> |
| Заполнить неделю по шаблону «обычная неделя» | участник | Mini App | docs/development/19-functional-requirements.md#L73<br>docs/development/92-navigation-model.md#L96 | P1, готово<br><small>Кнопки на /webapp/days; в 92 — строка-действие в листе дня</small> |
| Посмотреть сводку свободных по дням | организатор | оба | docs/development/19-functional-requirements.md#L68<br>docs/development/20-scenarios.md#L193<br>docs/development/20-scenarios.md#L231 | P1, частично<br><small>Чат: счётчики «свободны/известно»; Mini App: сводка недели</small> |
| Посмотреть сводку «люди × дни» | организатор | Mini App | docs/development/19-functional-requirements.md#L154<br>docs/development/48-mini-app-grid.md#L14<br>docs/development/92-navigation-model.md#L104 | P1, готово<br><small>Сетка месяца /webapp/week/month: только чтение, источник — Google-таблица, расхождение с базой показывается; POST /webapp/grid удалён (Ш-04)</small> |
| Найти окна (top-3) для репетиции или тренинга | организатор | нигде | docs/development/19-functional-requirements.md#L69<br>docs/development/19-functional-requirements.md#L83<br>docs/development/19-functional-requirements.md#L85<br>docs/development/20-scenarios.md#L242<br>docs/development/20-scenarios.md#L301<br>docs/development/20-scenarios.md#L312 | P0, готово<br><small>FR-C6 слито в FR-D5; вход из чата и сценариев — Ш-06</small> |
| Запросить занятость рассылкой с кнопками | организатор | нигде | docs/development/19-functional-requirements.md#L71 | P2, не начато<br><small>Механизма нет</small> |
| Посмотреть метрику покрытия занятости | организатор | чат | docs/development/19-functional-requirements.md#L70 | P2, готово<br><small>Строка в «Расписании» gateway</small> |
| Посмотреть расписание и ближайшие события | участник | оба | docs/development/19-functional-requirements.md#L80<br>docs/development/19-functional-requirements.md#L225<br>docs/development/20-scenarios.md#L222<br>docs/development/93-cjm.md#L61 | P0, готово<br><small>Чат: неделя по дням и ссылка на календарь; Mini App: «Сегодня» на Главной (П-5)</small> |
| Заполнить черновой календарь на неделю | владелец | нигде | docs/development/19-functional-requirements.md#L81<br>docs/development/20-scenarios.md#L216 | P0, готово<br><small>Скрипт scripts/calendar_fill_draft.py, интерфейса нет</small> |
| Назначить событие: окно → карточка → календарь | организатор | нигде | docs/development/19-functional-requirements.md#L152<br>docs/development/19-functional-requirements.md#L93<br>docs/development/93-cjm.md#L91 | P0, частично<br><small>Есть в чате и на экране /webapp/assign; сквозной сценарий не проверен</small> |
| Отменить событие | организатор | чат | docs/development/19-functional-requirements.md#L194 | P0, готово<br><small>Кнопка «Отменить» в карточке события в чате, подтверждение до записи (Ш-06)</small> |
| Перенести событие | организатор | чат | docs/development/19-functional-requirements.md#L195 | P0, готово<br><small>Кнопка «Перенести» в карточке события, «было → стало» подтверждается до записи (Ш-06)</small> |
| Изменить состав события после назначения | организатор | нигде | docs/development/19-functional-requirements.md#L196 | P1, не начато |
| Заменить педагога на конкретную дату | организатор | нигде | docs/development/19-functional-requirements.md#L199 | P1, не начато |
| Отметить, что педагог провёл занятие | педагог | нигде | docs/development/19-functional-requirements.md#L201 | P1, не начато |
| Подтвердить тренинг без педагога своей дисциплины | организатор | нигде | docs/development/19-functional-requirements.md#L200 | P1, готово |
| Задать свободный вопрос ассистенту | участник | чат | docs/development/19-functional-requirements.md#L101<br>docs/development/19-functional-requirements.md#L102<br>docs/development/20-scenarios.md#L154 | P0, готово |
| Спросить «кто я» | участник | чат | docs/development/19-functional-requirements.md#L103<br>docs/development/20-scenarios.md#L121 | P0, готово |
| Спросить данные: кто зарегистрирован, деньги, расписание | участник, организатор | чат | docs/development/19-functional-requirements.md#L104<br>docs/development/20-scenarios.md#L136<br>docs/development/20-scenarios.md#L222 | P0, готово |
| Сообщить о проблеме или запросе на доработку текстом | участник | чат | docs/development/19-functional-requirements.md#L108 | P2, готово<br><small>FR-E8 слито в FR-I1: просьба о доработке — вид обращения, отдельного типа нет</small> |
| Настроить квоты AI человеку или роли | админ | чат | docs/development/19-functional-requirements.md#L106<br>docs/development/12-bot-behavior.md#L136 | P1, частично<br><small>12 §5 относит квоты к настройке из Telegram; вторая ветка квоту не проверяет</small> |
| Утвердить новую категорию из очереди | админ | нигде | docs/development/19-functional-requirements.md#L111<br>docs/development/21-intent-cascade.md#L116 | P1, готово<br><small>Только scripts/intent_report.py --approve/--reject</small> |
| Открыть и загрузить файлы студии | админ | Mini App | docs/development/19-functional-requirements.md#L121<br>docs/development/92-navigation-model.md#L393 | P1, частично<br><small>92 §9.1: работает у админа; FR-F5 — частично, права другим участникам позже (П-7)</small> |
| Записать заметку | участник | оба | docs/development/19-functional-requirements.md#L124<br>docs/development/92-navigation-model.md#L170 | P1, готово<br><small>дом — Mini App «Ещё → Заметки»; в чате карточка с подтверждением</small> |
| Настроить тихие часы и типы уведомлений | участник | Mini App | docs/development/19-functional-requirements.md#L132<br>docs/development/92-navigation-model.md#L169<br>docs/development/82-ux-navigation.md#L205 | P1, готово<br><small>Частные — в профиле, общие — в админ-панели; частные важнее (П-8)</small> |
| Отправить рассылку участникам | организатор, админ | Mini App | docs/development/19-functional-requirements.md#L131<br>docs/development/92-navigation-model.md#L144 | P1, частично<br><small>Право notify.send только у админа; организатору не выдаём (П-6)</small> |
| Посмотреть сводку недоставленных уведомлений | админ | нигде | docs/development/19-functional-requirements.md#L207 | P1, не начато |
| Настроить гранулярность записи в человеческий лист | админ | нигде | docs/development/19-functional-requirements.md#L222 | P1, готово<br><small>finance_granularity — настройка в БД, экрана нет</small> |
| Настроить карту источников листа | админ | нигде | docs/development/19-functional-requirements.md#L223 | P1, готово<br><small>finance_source_map — настройка в БД, экрана нет</small> |
| Посмотреть состояние сервисов | админ | Mini App | docs/development/92-navigation-model.md#L146<br>docs/development/93-cjm.md#L155 | P2, готово<br><small>Экран /webapp/services и API есть; требование FR-H16</small> |
| Отправить обратную связь | участник | оба | docs/development/19-functional-requirements.md#L219<br>docs/development/92-navigation-model.md#L171 | —, готово<br><small>Обратная связь принимает и просьбу о доработке (FR-E8 слито в FR-I1)</small> |
| Разобрать обращение и ответить автору | админ | Mini App | docs/development/19-functional-requirements.md#L220<br>docs/development/92-navigation-model.md#L142 | —, готово<br><small>«Управление → Обращения»; счётчик новых — на «Главной»</small> |
| Посмотреть карточку человека | организатор | оба | docs/development/19-functional-requirements.md#L40<br>docs/development/92-navigation-model.md#L157 | P1, частично<br><small>Карточка и API есть; экрана «Люди» с занятостью и долгом нет</small> |
| Настроить общие уведомления студии | админ | Mini App | docs/development/19-functional-requirements.md#L137<br>docs/development/92-navigation-model.md#L148 | P1, готово<br><small>Обещано 92 §2.4; экран /webapp/admin/t/notifications, маршрут /webapp/admin/notifications/settings, право admin.panel (Ш-11)</small> |
<!-- matrix:end -->

## Требования без отдельного действия участника

Это не «лишние» требования: у них нет собственного шага человека, но они влияют на то,
какие действия возможны. Список нужен, чтобы сумма строк сошлась с числом заголовков
`docs/19` и расхождение было объяснено, а не спрятано.

<!-- requirements:begin -->
| Требование | Приоритет | Статус | Почему без строки | Источник |
|---|---|---|---|---|
| FR-A5 | P0 | готово | модель прав: возможность проверяется на сервере, отдельного шага нет | docs/development/19-functional-requirements.md#L35 |
| FR-B5 | P0 | убрано | требование снято 30.10.2026 — механизма подтверждения нет | docs/development/19-functional-requirements.md#L51 |
| FR-B6 | P1 | готово | расчёт долей и сверка арифметики — код и представление | docs/development/19-functional-requirements.md#L52 |
| FR-B8 | P0 | готово | импорт исторических данных — скрипт | docs/development/19-functional-requirements.md#L54 |
| FR-B9 | P0 | готово | месячный отчёт собирает скрипт | docs/development/19-functional-requirements.md#L55 |
| FR-B10 | P0 | готово | проверка инвариантов — скрипт и лист «Проверки» | docs/development/19-functional-requirements.md#L56 |
| FR-B11 | P0 | готово | свойство записи (идемпотентность), а не шаг человека | docs/development/19-functional-requirements.md#L57 |
| FR-C1 | P0 | готово | импорт часовой матрицы — скрипт | docs/development/19-functional-requirements.md#L67 |
| FR-C4 | P1 | частично | правило записи: аудит и защита от затирания при импорте | docs/development/19-functional-requirements.md#L70 |
| FR-D8 | P1 | готово | разбор субъекта — часть запроса, отдельного шага нет | docs/development/19-functional-requirements.md#L88 |
| FR-D9 | P1 | готово | роль как псевдоним — данные, отдельного шага нет | docs/development/19-functional-requirements.md#L89 |
| FR-D10 | P1 | не начато | вычитание интервалов событий — расчёт | docs/development/19-functional-requirements.md#L90 |
| FR-D11 | P1 | готово | правило формирования события — код, не шаг человека | docs/development/19-functional-requirements.md#L91 |
| FR-D12 | P1 | готово | правило формирования события — код, не шаг человека | docs/development/19-functional-requirements.md#L92 |
| FR-D13 | P0 | готово | правило формирования события — код, не шаг человека | docs/development/19-functional-requirements.md#L93 |
| FR-D14 | P1 | частично | правило формирования события — код, не шаг человека | docs/development/19-functional-requirements.md#L94 |
| FR-D16 | P0 | готово | правило формирования события — код, не шаг человека | docs/development/19-functional-requirements.md#L96 |
| FR-D17 | P1 | не начато | учёт дохода от аренды — расчёт | docs/development/19-functional-requirements.md#L97 |
| FR-E5 | P0 | частично | режим деградации — правило поведения | docs/development/19-functional-requirements.md#L107 |
| FR-E7 | P0 | не начато | валидатор ответа — серверная проверка | docs/development/19-functional-requirements.md#L109 |
| FR-E9 | P0 | готово | каскад разбора — механизм, а не шаг | docs/development/19-functional-requirements.md#L110 |
| FR-E10 | P1 | готово | журнал разбора — серверная запись | docs/development/19-functional-requirements.md#L111 |
| FR-F1 | P0 | готово | лист собирает скрипт, человек читает его в Google Sheets | docs/development/19-functional-requirements.md#L118 |
| FR-F2 | P1 | готово | лист собирает скрипт, человек читает его в Google Sheets | docs/development/19-functional-requirements.md#L119 |
| FR-F3 | P1 | готово | лист собирает скрипт, человек читает его в Google Sheets | docs/development/19-functional-requirements.md#L120 |
| FR-F4 | P0 | готово | лист собирает скрипт, человек читает его в Google Sheets | docs/development/19-functional-requirements.md#L121 |
| FR-F6 | P2 | не начато | справочник контактов — данные, действия нет | docs/development/19-functional-requirements.md#L123 |
| FR-G1 | P1 | не начато | уведомление приходит само, отдельного шага человека нет | docs/development/19-functional-requirements.md#L130 |
| FR-G5 | P1 | готово | уведомление приходит само, отдельного шага человека нет | docs/development/19-functional-requirements.md#L131 |
| FR-G6 | P0 | отменено | требование отменено 30.10.2026 — маршруты подтверждения удалены | docs/development/19-functional-requirements.md#L132 |
| FR-G4 | P1 | не начато | дедупликация — серверное правило | docs/development/19-functional-requirements.md#L135 |
| FR-G15 | P2 | не начато | уведомление приходит само, отдельного шага человека нет | docs/development/19-functional-requirements.md#L136 |
| FR-H1 | P0 | готово | эксплуатация: эндпоинт, миграции, тесты или деплой — не действие участника | docs/development/19-functional-requirements.md#L146 |
| FR-H2 | P0 | готово | эксплуатация: эндпоинт, миграции, тесты или деплой — не действие участника | docs/development/19-functional-requirements.md#L147 |
| FR-H3 | P0 | готово | эксплуатация: эндпоинт, миграции, тесты или деплой — не действие участника | docs/development/19-functional-requirements.md#L148 |
| FR-H4 | P0 | частично | эксплуатация: эндпоинт, миграции, тесты или деплой — не действие участника | docs/development/19-functional-requirements.md#L149 |
| FR-H5 | P1 | частично | эксплуатация: эндпоинт, миграции, тесты или деплой — не действие участника | docs/development/19-functional-requirements.md#L150 |
| FR-H6 | P1 | частично | наличие Mini App — платформа, а не действие | docs/development/19-functional-requirements.md#L151 |
| FR-H7 | P2 | не начато | эксплуатация: эндпоинт, миграции, тесты или деплой — не действие участника | docs/development/19-functional-requirements.md#L152 |
| FR-H11 | P0 | не начато | уведомление приходит само, отдельного шага человека нет | docs/development/19-functional-requirements.md#L156 |
| FR-D21 | — | не начато | проверка пересечений — часть действия «Назначить событие» | docs/development/19-functional-requirements.md#L212 |
| FR-D22 | — | частично | правило фиксации присутствия режиссёра, отдельного шага нет | docs/development/19-functional-requirements.md#L213 |
| FR-G16 | — | не начато | ретенция — миграция и архив | docs/development/19-functional-requirements.md#L217 |
| FR-G7 | — | не начато | TTL заявок — серверное правило | docs/development/19-functional-requirements.md#L218 |
| FR-G8 | — | готово | уникальность псевдонимов — ограничение базы | docs/development/19-functional-requirements.md#L219 |
| FR-H12 | — | готово (systemd `Restart=always`, журнал journald) | эксплуатация: эндпоинт, миграции, тесты или деплой — не действие участника | docs/development/19-functional-requirements.md#L220 |
| FR-H13 | — | готово (`/health` с `git_sha`, `scripts/health_agent.py`, очередь) | эксплуатация: эндпоинт, миграции, тесты или деплой — не действие участника | docs/development/19-functional-requirements.md#L221 |
| FR-G11 | — | готово | нет отдельного действия участника: серверное правило или расчёт | docs/development/19-functional-requirements.md#L239 |
| FR-G14 | — | не начато | форматирование листов — скрипт | docs/development/19-functional-requirements.md#L243 |
<!-- requirements:end -->

## Сценарии `docs/20`

Присутствуют все 30: 25 в основной части (`## S-01…S-25`) и 5 в разделе «Новые сценарии
(27.10.2026)» (`### S-26…S-30`). У каждого указан актор, интерфейс и статус из документа;
сценарии без строки-действия (отчёты, скрипты, снятое подтверждение) помечены `нигде` или
`снято` и объяснены в самом документе.

<!-- scenarios:begin -->
| Сценарий | Актор | Действие в матрице | Интерфейс | Статус | Источник |
|---|---|---|---|---|---|
| S-01 | владелец | Первый вход владельца | чат | работает | docs/development/20-scenarios.md#L17 |
| S-02 | организатор | Регистрация организатора по коду | чат | работает | docs/development/20-scenarios.md#L31 |
| S-03 | участник | Регистрация актёра труппы | чат | работает | docs/development/20-scenarios.md#L43 |
| S-04 | участник | Человека нет в списке | чат | работает | docs/development/20-scenarios.md#L51 |
| S-05 | владелец | Уход из театра (архив) | Mini App | частично | docs/development/20-scenarios.md#L62 |
| S-06 | организатор | Расход свободным текстом | чат | работает | docs/development/20-scenarios.md#L73 |
| S-07 | участник | Расход через форму и права | чат | частично | docs/development/20-scenarios.md#L85 |
| S-08 | организатор | Крупная трата без второго подтверждения | чат | снято 30.10.2026 | docs/development/20-scenarios.md#L94 |
| S-09 | организатор | Доход от показа | чат | работает | docs/development/20-scenarios.md#L105 |
| S-10 | участник | Мои взносы | оба | работает | docs/development/20-scenarios.md#L112 |
| S-11 | участник | «Кто я» | чат | работает | docs/development/20-scenarios.md#L121 |
| S-12 | организатор | «Кто зарегистрирован» | чат | работает | docs/development/20-scenarios.md#L136 |
| S-13 | участник | Вопрос ассистенту | чат | работает | docs/development/20-scenarios.md#L151 |
| S-14 | участник | LLM недоступен — формы | чат | работает | docs/development/20-scenarios.md#L160 |
| S-15 | участник | Занятость текстом | чат | работает | docs/development/20-scenarios.md#L173 |
| S-16 | участник | Занятость формой | чат | работает | docs/development/20-scenarios.md#L181 |
| S-17 | организатор | Сводка занятости | оба | частично | docs/development/20-scenarios.md#L190 |
| S-18 | владелец | Отчёт по месяцу | нигде | работает (Google Sheets, не интерфейс) | docs/development/20-scenarios.md#L199 |
| S-19 | владелец | Проверка инвариантов | нигде | работает (терминал и лист) | docs/development/20-scenarios.md#L207 |
| S-20 | владелец | Черновой календарь на неделю | нигде | работает (скрипт) | docs/development/20-scenarios.md#L213 |
| S-21 | участник | Спросить у бота расписание | чат | работает | docs/development/20-scenarios.md#L222 |
| S-22 | организатор | Найти окно для репетиции | нигде | частично | docs/development/20-scenarios.md#L239 |
| S-23 | владелец | Ручная правка таблицы | нигде | частично (Google Sheets) | docs/development/20-scenarios.md#L245 |
| S-24 | админ | Выдать право | чат | работает командой | docs/development/20-scenarios.md#L255 |
| S-25 | админ | Аудит | оба | работает | docs/development/20-scenarios.md#L264 |
| S-26 | организатор | Окна для конкретных людей по ролям | нигде | описан, реализации нет | docs/development/20-scenarios.md#L301 |
| S-27 | организатор | Тренинг для максимального числа людей | нигде | описан, реализации нет | docs/development/20-scenarios.md#L312 |
| S-28 | организатор | Крупная трата пишется сразу | чат | работает | docs/development/20-scenarios.md#L322 |
| S-29 | организатор | Напоминание о незаполненной занятости | чат | описан, автоматической рассылки нет | docs/development/20-scenarios.md#L332 |
| S-30 | участник | Пусто и свободно — разные вещи | чат | описан | docs/development/20-scenarios.md#L340 |
<!-- scenarios:end -->

## Противоречия между документами

**Снимок на 04.11.2026 до решений владельца.** Противоречия ниже — то, что показал аудит;
решения по ним собраны в `docs/reports/124-actions-homes-and-requirements.md` (П-1…П-12),
документы приведены к ним шагом Ш-01. Таблицы выше пересобраны из JSON по новой редакции
`docs/19`; текст противоречий оставлен как основание решений, а не как действующее правило.

Здесь только то, что мешает ответить на вопрос «где действие разрешено»: один документ
говорит «в чате», другой — «только на экране», третий описывает экран, которого уже нет.
Каждый пункт — с обеими цитатами и местом.

### П-1. Правило «что можно в Mini App — можно и в чате» против «Mini App — основное место»

- `docs/development/23` §10, стр. 222: «**любое действие из Mini App доступно кнопками в
  чате**»; то же правило: `53` стр. 80, `57` §7 стр. 87.
- `docs/development/82` §3.1, стр. 101: «**Mini App — основное рабочее место. Чат — быстрые
  действия и подтверждения**»; `19` FR-H8 (стр. 150) прямо требует заполнять свой месяц
  «в Mini App».

Следствие для матрицы: десять действий существуют только на экране (заполнение недели,
лист дня, профиль, уведомления, сводка, файлы). По правилу `23`/`53`/`57` у каждого должен
быть кнопочный путь в чате; по `82` — не должен. Решения нет ни в одном документе.

### П-2. Личный лист занятости: три адреса и три формы

- `docs/development/48` §1, стр. 13: личный лист — `/webapp/my`, сетка «16 часов × 30 дней»;
  там же §2 стр. 24–26: «**Заменено 31.10.2026**… актёр видит неделю строками».
- `docs/development/92` §2.2, стр. 73: «Один экран занятости вместо трёх (`/days`, `/my/sheet`,
  `/schedule`)» — `/webapp/week`; стр. 97: «Личного „часового“ листа на 30 дней больше нет».
- `docs/development/19` FR-H8, стр. 150: «Актёр заполняет **свой** месяц в Mini App:
  16 часов × 30 дней» — и статус «не начато».

Один документ считает сетку отменённой, другой продолжает требовать её как P0. В матрице
это строка «Отметить занятость по часам в Mini App»: интерфейс `Mini App`, статус — противоречие.

### П-3. Сводка занятости: «только для глаз» против «умеет править»

- `docs/development/48` §1, стр. 16: «**правка — только в личном листе, сводка — только для
  глаз**»; §3 стр. 53: «**Без правки**: клетки не реагируют на нажатие».
- `docs/development/19` FR-H9, стр. 151: «Сводная сетка «люди × дни»… `/webapp/schedule`
  **умеет править — правка отключается**» (статус «частично»).

Для строки «Посмотреть сводку «люди × дни»» неясно, есть ли на экране запись. Это же место
порождает разрыв: правка в сводке существует по требованию и запрещена решением `48`.

### П-4. «Сохранить неделю» против автосохранения

- `docs/development/92` §2.2, стр. 90: «**Сохранение — сразу** при закрытии листа, без кнопки
  «Сохранить неделю»».
- `docs/development/48` §2, стр. 43: «сохранение сразу, кнопки «Сохранить» нет».
- `docs/development/93` §1, стр. 35 (колонка «Сейчас»): «кнопка «**Сохранить неделю**» внизу,
  экран прокручен — не видно».

`93` описывает факт (кнопка есть), `48`/`92` — норму (кнопки нет). Пока это не сведено,
непонятно, какое действие в матрице считать завершённым: «отметил час» или «нажал сохранить».

### П-5. Расписание в чате: «оставить» против «перенести в Mini App»

- `docs/development/82` §2, стр. 86: «расписание — в Mini App; в чате только «свободен/занят
  сегодня»».
- `docs/development/19` FR-D26, стр. 225: «Расписание в чате: группировка по дням, тип
  мероприятия, ссылка на календарь» — статус «готово».
- `docs/development/93` §3, стр. 61: «чат: «Расписание» — ближайшие 3 события текстом…
  остаётся».

Строка «Посмотреть расписание» стоит как `оба`; по `82` её чатовая половина подлежит
удалению, по `19` и `93` — работает и остаётся.

### П-6. Рассылка: только админ или организатор с правом

- `docs/development/82` §3.4, стр. 145: «Админ-панель, Рассылка, Архив — **только админ**».
- `docs/development/92` §2.4, стр. 129: строка «Рассылка» доступна по праву `notify.send`
  (у организатора такое право есть по матрице ролей).
- `docs/development/19` FR-G2, стр. 131: «массовое напоминание **только по запросу
  организатора** — экран `/webapp/broadcast`».

Кто в матрице «Отправить рассылку» — организатор или только админ — документы отвечают
по-разному; в строке стоят оба, и это компромисс, а не решение.

### П-7. Файлы: работают у администратора или «не начато»

- `docs/development/92` §9.1, стр. 321: «**Пока только администратор**» — раздел «Файлы
  студии» в «Ещё → Личное», данные отвечают 403 без `admin.panel`.
- `docs/development/19` FR-F5, стр. 121: «Заметки и файлы (личные и общие)» — статус
  «не начато».

Один документ описывает работающий экран, другой считает требование не начатым. Строка
«Открыть и загрузить файлы студии» в матрице имеет интерфейс `Mini App` и статус
«противоречие документов».

### П-8. Тихие часы: два интервала и два места настройки

- `docs/development/82` §5, стр. 213: «Тихие часы: с **21:00 до 09:00**».
- `docs/development/19` FR-G3, стр. 132: «Тихие часы **20:00–08:00** по Самаре» (и решение
  31.10.2026 в подтверждении требования).
- `docs/development/12` §5, стр. 137: «тихие часы, отключение категорий уведомлений» —
  настраиваются «из Telegram»; `92` §2.4, стр. 138: экран «Уведомления» в Mini App.

Строка «Настроить тихие часы» имеет интерфейс `Mini App`, хотя `12` относит настройку к
чату. Интервал в матрицу не входит, но расхождение придётся закрыть до переноса действий.

### П-9. Обязательные формы MVP против статусов требований

`docs/development/12` §3, стр. 91–99 перечисляет обязательные формы: «Взнос оплачен»,
«Репетиция», «Заметка», «Поиск окон». В `docs/19` эти же действия:

- FR-B7 (стр. 50) — «оплата из чата пока не реализована»;
- FR-H10 (стр. 152) — «не начато»;
- FR-F5 (стр. 121) — «не начато»;
- FR-C6 (стр. 69) — «не начато».

Четыре строки матрицы (`Внести оплату взноса`, `Записать заметку`, `Найти окна`,
`Назначить событие`) стоят `нигде` именно из-за этого: `12` требует форму, `19` фиксирует
отсутствие механизма.

### П-10. Поиск окон: «готово» и «не начато» в одном документе

- `docs/development/19` FR-D5, стр. 83: «Поиск общих окон… **готово** — `app/slots.py::find_windows`,
  `POST /tools/schedule/slots`; 17 unit + 13 integration».
- `docs/development/19` FR-C6, стр. 69: «Поиск окон top-3 с учётом занятости и календаря…
  **не начато** — есть только capability `schedule.slots`; алгоритма нет».
- `docs/development/20` S-22, стр. 239: «не реализовано»; `92` §2.2, стр. 96 обещает
  кнопку «Найти окно» → `/webapp/slots`.

Алгоритм и страница описаны как существующие, а сценарий и требование — как отсутствующие.
Поэтому строка «Найти окна (top-3)» имеет интерфейс `нигде` и статус «частично»: формально
вход в Mini App обещан, но пользовательского пути из документов не собирается.

### П-11. Взносы: «вносит организатор» против обязательной формы оплаты

- `docs/development/93` §2, стр. 51: «**Оплату вносит организатор** — отдельно сообщать».
- `docs/development/92` §2.3, стр. 104: у актёра в «Деньгах» «главное действие — **нет**».
- `docs/development/12` §3, стр. 95: форма «Взнос оплачен» — обязательная форма MVP.
- `docs/development/19` FR-B7, стр. 50: «оплата из чата пока не реализована».

Три документа из четырёх говорят, что участник оплату не вносит; `12` требует для этого
форму. Строка «Внести оплату взноса» — `нигде`, и это осознанная фиксация, а не пропуск.

### П-12. Навигация: хаб с плитками, пять разделов чата и четыре вкладки

- `docs/development/82` §3.2, стр. 106–119 — хаб с плитками (в шапке §, стр. 8–10, сам
  документ признаёт §3.2 заменённым); §3.3, стр. 123–126 — клавиатура из четырёх кнопок.
- `docs/development/23` §3, стр. 44–52 — пять разделов чата («Деньги», «Время», «Профиль»,
  «Админ», «Помощь»).
- `docs/development/92` §1, стр. 13–20 — четыре нижние вкладки, четвёртая меняет подпись.

Для матрицы это значит, что «где сейчас» для админских действий нельзя брать из `82` §3.2:
экрана-хаба уже нет, а «Управление» и «Права» из `92` ещё не подтверждены реализацией.
Строки админских действий поэтому опираются на `92` §2.4 и `19`, а не на `82` §3.2.

## Как сошлось число заголовков

- В `docs/19` — **113** строк требований: 89 в основных таблицах (колонки «ID | Требование |
  Приоритет | Статус | Подтверждение») и 24 в дополнениях от 27.10.2026 (колонки «ID |
  Требование | Статус», без приоритета). После решений 04.11.2026: 110 − 2 слитых
  (FR-C6 → FR-D5, FR-E8 → FR-I1) + 5 новых (FR-A10, FR-A11, FR-F7, FR-G17, FR-H16).
- Все 113 присутствуют в JSON: у 24 приоритет `—` означает «в документе приоритет не задан»,
  это не выдуманное значение.
- Требований со строкой-действием — см. сводку; остальные перечислены выше с причиной.
  Снятые требования (`FR-B5`, `FR-G6`) тоже на месте — со статусом «убрано»/«отменено» и
  причиной, потому что документ их не удалил.
- В `docs/20` — **30** сценариев: 25 заголовков `## S-xx` и 5 заголовков `### S-xx.` в
  разделе «Новые сценарии». Поиск по `##` даёт 25, поэтому проверка идёт по `#{2,3}`.

## Чего в матрице нет

- Действий, которых нет в документации: новые требования не придумывались. Если действие
  существует только в коде, его место — в аудите 2/4, а не здесь.
- Оценок «правильно/неправильно»: это инвентарь. Решения «делаем/не делаем» — в своде 5/5.
- API-эндпоинтов как отдельных строк: действие описывает шаг человека, эндпоинт — способ.
  Ссылки на код в `docs/19` сохранены в колонке «Подтверждение», но не перепроверялись.

## Чем проверено

```
env -u PYTHONPATH .venv/bin/python scripts/check_action_matrix.py
env -u PYTHONPATH .venv/bin/python scripts/check_action_matrix.py --json
```

Скрипт падает, если: число или состав требований `docs/19` расходится с JSON; приоритет,
статус или строка-источник требования не совпадают с документом; у действия нет автора,
интерфейса, статуса или источника; источник указывает на несуществующий файл или строку;
сценарий `docs/20` отсутствует; таблицы в этом отчёте разошлись с JSON.

**Номер 121** — свободный после сведения с `main` 04.11.2026 (карта AUD-2 `t_3f1d22a9`):
117 в `main` занят инвентарём кода (`docs/reports/117-actions-inventory.md`), 118 — отчётом
про VK, 119–120 — отчётами 4/4 и свода 5/5. Соседние аудиты 2/4, 3/4 и 4/4 пишут свои отчёты
отдельными картами; их таблицы в этот файл не входят и на эти строки не ссылаются.
