"""Ревизии Alembic поверх db/init/*.sql (docs/reports/132).

Пакет нужен, чтобы ревизии в `versions/` импортировали `migrations.sql` одним
и тем же путём и в образе, и в тестах: `services/tool-api` уже лежит в sys.path.
"""
