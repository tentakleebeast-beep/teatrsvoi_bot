"""Версия схемы при старте: приложение схему не создаёт, а сверяет её с кодом.

Схему накатывает Alembic (`db/init/*.sql` через ревизии). Раньше сервис создавал
её сам, `create table if not exists` по подмножеству описания — из-за этого он
поднимался на отставшей базе и падал 500-й на первом же запросе к новым колонкам
(30.10.2026: не хватало `participant.write_gender` и `budget_tx.is_plan`,
docs/infrastructure/75). Отказ старта с текстом дешевле, чем тихая деградация.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.version import project_root

INI_NAME = "alembic.ini"


def alembic_ini(root: Path | None = None) -> Path:
    """Путь к `alembic.ini`: корень репозитория, в образе — `/app`."""
    return (root or project_root()) / INI_NAME


def head_revision(ini_path: Path | None = None) -> str:
    """Голова цепочки ревизий из файлов, без подключения к базе."""
    script = ScriptDirectory.from_config(Config(str(ini_path or alembic_ini())))
    heads = script.get_heads()
    if len(heads) != 1:
        raise RuntimeError(
            f"цепочка ревизий Alembic разветвлена: head={sorted(heads)}; "
            f"ожидается ровно одна голова")
    return heads[0]


def read_version(conn) -> str | None:
    """Версия из `alembic_version`; None — таблицы или строки нет."""
    exists = conn.execute(
        "select to_regclass('public.alembic_version') as name").fetchone()["name"]
    if exists is None:
        return None
    rows = conn.execute("select version_num from alembic_version").fetchall()
    if len(rows) != 1:
        return ",".join(sorted(row["version_num"] for row in rows)) or None
    return rows[0]["version_num"]


def check_schema_version() -> str:
    """Сверить версию базы с головой цепочки; расхождение — RuntimeError.

    Возвращает версию, чтобы вызывающий мог записать её в лог.
    """
    from app import core  # noqa: PLC0415 — core тянет настройки окружения при импорте

    head = head_revision()
    with core.pool.connection() as conn:
        version = read_version(conn)
    if version is None or version != head:
        raise RuntimeError(
            f"схема не применена (version={version or 'нет'}, head={head}); "
            f"выполните `alembic upgrade head`")
    return version
