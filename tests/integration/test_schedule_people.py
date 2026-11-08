"""Ш-03a плана 129: импорт расписания находит человека по никнейму из БД, без словаря в коде.

Проверка идёт на живой тестовой базе: псевдоним кладётся в `person_alias`, а поиск людей
(`scripts/_people.person_index`) — тот же, которым пользуются `import_schedule.py` и
`reconcile.py`. Так ловится именно то, что сломалось после обезличивания: в коде имени нет,
человек находится по данным.
"""

from __future__ import annotations

import import_schedule
import psycopg
from _people import person_index
from psycopg.rows import dict_row

from tests.conftest import _test_dsn


def test_никнейм_из_базы_находит_человека_без_словаря(tmp_path, test_db):
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        person = conn.execute(
            """insert into participant (tg_chat_id, display_name, person_key, role,
                                        status, is_active)
               values (null, 'Соколов Марк', 'соколов-марк', 'troupe', 'active', true)
               returning id""").fetchone()
        conn.execute(
            """insert into person_alias (participant_id, alias, alias_kind, source)
               values (%s, 'маша', 'short', 'manual')""", (person["id"],))
        conn.commit()
        try:
            index = person_index(conn, config=tmp_path / "нет-конфига.yaml")
            assert index.resolve("маша") == person["id"]
            assert index.resolve("Маша") == person["id"]
        finally:
            conn.execute("delete from person_alias where participant_id = %s", (person["id"],))
            conn.execute("delete from participant where id = %s", (person["id"],))
            conn.commit()


def test_импорт_заводит_алиас_и_не_падает_на_повторе(tmp_path, test_db):
    """Вставка алиаса из `main()` исполняется на живой схеме, а не только читается глазами.

    Схема уникальна по `(participant_id, alias)` (023) и `(participant_id, lower(alias))` (030),
    а глобального `unique (alias)` нет с 023. Поэтому проверяются три случая, на которых
    импорт раньше обрывался целиком: новый алиас, повтор того же листа и тот же никнейм
    в другом регистре.
    """
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        person = conn.execute(
            """insert into participant (tg_chat_id, display_name, person_key, role,
                                        status, is_active)
               values (null, 'Соколов Марк', 'соколов-марк', 'troupe', 'active', true)
               returning id""").fetchone()
        conn.execute(
            """insert into person_alias (participant_id, alias, alias_kind, source)
               values (%s, 'Витя', 'short', 'manual')""", (person["id"],))
        conn.commit()
        try:
            index = person_index(conn, config=tmp_path / "нет-конфига.yaml")

            # тот же никнейм в другом регистре: ловит unique (participant_id, lower(alias))
            assert import_schedule.save_aliases(conn, ["витя"], index) == 1
            conn.commit()

            # новый алиас: лист назван полным именем, человек находится по display_name
            assert import_schedule.save_aliases(conn, ["Соколов Марк"], index) == 1
            conn.commit()
            # повтор того же листа: не падает и не дублирует
            assert import_schedule.save_aliases(conn, ["Соколов Марк"], index) == 1
            conn.commit()

            aliases = {row["alias"] for row in conn.execute(
                "select alias from person_alias where participant_id = %s",
                (person["id"],)).fetchall()}
            assert aliases == {"Витя", "соколов марк"}

            # со второго прогона конфиг не нужен: алиас читается из БД
            again = person_index(conn, config=tmp_path / "нет-конфига.yaml")
            assert again.resolve("соколов марк") == person["id"]
        finally:
            conn.execute("delete from person_alias where participant_id = %s", (person["id"],))
            conn.execute("delete from participant where id = %s", (person["id"],))
            conn.commit()
